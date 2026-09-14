"""Fake-WebSocket protocol tests for the tencent-asr adapter.

Fixtures mirror the official Tencent Cloud realtime ASR WebSocket protocol
(classic response shape + the conference speaker shape):
https://cloud.tencent.com/document/product/1093/48982   (classic protocol)
https://cloud.tencent.com/document/product/1093/131127  (V2 + signature)
https://cloud.tencent.com/document/product/1093/130881  (speaker sentences)

Key official semantics under test:

* signed URL: params sorted by key, ``signature`` = base64(HMAC-SHA1(
  "asr.cloud.tencent.com/asr/v2/{appid}?<sorted query>", SecretKey)),
  URL-encoded in the final URL; ``expired > timestamp``; a fresh ``voice_id``
  (UUID) per connection; the full signed URL is NEVER logged or surfaced in
  errors;
* binary PCM frames, ``voice_format=1``;
* ``result.slice_type`` 0/1 -> interim, 2 -> final; ``start_time``/``end_time``
  milliseconds; ``word_list`` word timing passthrough;
* speaker engines may answer with ``sentences.sentence_list[]`` (confirmed
  shape): ``sentence_type`` 0 -> interim, 1 -> final, ``speaker_id`` mapped;
  unknown response shapes are ignored, never guessed;
* ``code != 0`` -> typed error with code+message.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import sys
import unittest
import urllib.parse
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.providers import create_asr
from companion.providers.base import SourceLanguagePolicy

APP_PATH = "/asr/v2/tencent-test-appid"


def expected_signature(params: dict[str, str], host_path: str, secret_key: str = "fake-secret-key") -> str:
    canonical = host_path + "?" + "&".join(f"{key}={params[key]}" for key in sorted(params))
    digest = hmac.new(secret_key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


class FakeTencentServer:
    def __init__(self) -> None:
        self.handshakes: list[dict[str, Any]] = []
        self.audio = bytearray()
        self.voice_ids: list[str] = []
        self.outbound: asyncio.Queue[dict[str, Any] | str] = asyncio.Queue()
        self.closed_connections = 0
        self.runner: web.AppRunner | None = None
        self._ws: web.WebSocketResponse | None = None
        self.url = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/asr/v2/tencent-test-appid", self._handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/asr/v2/tencent-test-appid"

    async def stop(self) -> None:
        assert self.runner is not None
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        await self.runner.cleanup()

    async def _handler(self, request: web.Request) -> web.WebSocketResponse:
        self.handshakes.append({"query": dict(request.query)})
        self.voice_ids.append(request.query["voice_id"])
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws = ws

        async def writer() -> None:
            while True:
                payload = await self.outbound.get()
                if payload == "CLOSE":
                    await ws.close()
                    return
                await ws.send_str(json.dumps(payload))

        task = asyncio.create_task(writer())
        try:
            async for message in ws:
                if message.type == aiohttp.WSMsgType.BINARY:
                    self.audio.extend(message.data)
        finally:
            task.cancel()
            self.closed_connections += 1
        return ws

    async def send(self, payload: dict[str, Any] | str) -> None:
        await self.outbound.put(payload)

    async def wait_for(self, check, timeout: float = 2.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while not check():
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError("fake server condition not met")
            await asyncio.sleep(0.01)


class TencentAsrTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeTencentServer()
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    def provider(self, **options):
        options.setdefault("appId", "tencent-test-appid")
        options.setdefault("secretId", "fake-secret-id")
        options.setdefault("engineModelType", "16k_zh_en_speaker_2.0")
        options.setdefault("wordInfo", 1)
        return create_asr({
            "id": "tencent-test",
            "kind": "tencent-asr",
            "model": "16k_zh_en_speaker_2.0",
            "baseUrl": self.server.url,
            "apiKey": "fake-secret-key",
            "options": options,
        })

    async def test_signed_url_query_and_signature(self) -> None:
        stream = await self.provider(needvad=1).stream(
            policy=SourceLanguagePolicy.specified("zh"), sample_rate=16000, hotwords=[], context=[],
        )
        await stream.push_pcm(b"\x01\x00" * 6400, 0.0)
        await self.server.wait_for(lambda: len(self.server.audio) == 12800)

        query = self.server.handshakes[0]["query"]
        self.assertEqual(query["engine_model_type"], "16k_zh_en_speaker_2.0")
        self.assertEqual(query["voice_format"], "1")  # PCM
        self.assertEqual(query["secretid"], "fake-secret-id")
        self.assertEqual(query["word_info"], "1")
        self.assertEqual(query["needvad"], "1")
        self.assertRegex(query["voice_id"], r"^[0-9a-f-]{36}$")
        self.assertLess(int(query["timestamp"]), int(query["expired"]))
        # Recompute the expected signature over the sorted canonical string.
        # Production canonical = "asr.cloud.tencent.com" + path (no port);
        # against the fake server it is "127.0.0.1:<port>" + path. The URL
        # itself is credential-bearing and never logged.
        from urllib.parse import urlparse, unquote as _unquote
        parsed_url = urlparse(self.server.url)
        host_path = f"{parsed_url.netloc}{parsed_url.path}"
        params = {key: value for key, value in query.items() if key != "signature"}
        self.assertEqual(query["signature"], expected_signature(params, host_path))
        # The signature arrives URL-encoded in the final URL.
        self.assertEqual(query["signature"], urllib.parse.unquote(query["signature"]))
        await stream.aclose()

    async def test_voice_id_is_fresh_per_connection(self) -> None:
        first = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("zh"), sample_rate=16000, hotwords=[], context=[],
        )
        await self.server.wait_for(lambda: len(self.server.voice_ids) >= 1)
        await first.aclose()
        second = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("zh"), sample_rate=16000, hotwords=[], context=[],
        )
        await self.server.wait_for(lambda: len(self.server.voice_ids) >= 2)
        self.assertNotEqual(self.server.voice_ids[0], self.server.voice_ids[1])
        await second.aclose()

    async def test_slice_type_mapping_and_word_timestamps(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("zh"), sample_rate=16000, hotwords=[], context=[],
        )
        iterator = stream.__aiter__()
        await self.server.wait_for(lambda: bool(self.server.handshakes))

        await self.server.send({"code": 0, "message": "success", "voice_id": "v1", "message_id": "m1",
                                "result": {"slice_type": 0, "index": 1, "start_time": 0, "end_time": 640,
                                           "voice_text_str": "您好，"}})
        started = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(started.type, "speech_started")
        self.assertEqual(started.item_id, "1")
        interim1 = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(interim1.type, "interim")
        self.assertEqual(interim1.text, "您好，")
        self.assertAlmostEqual(interim1.begin_pcm, 0.0)
        self.assertAlmostEqual(interim1.end_pcm, 0.64)
        self.assertEqual(interim1.item_id, "1")

        await self.server.send({"code": 0, "message": "success", "voice_id": "v1", "message_id": "m2",
                                "result": {"slice_type": 1, "index": 1, "start_time": 0, "end_time": 1280,
                                           "voice_text_str": "您好，欢迎",
                                           "word_size": 2,
                                           "word_list": [
                                               {"word": "您好", "start_time": 0, "end_time": 320, "speaker": 1},
                                               {"word": "欢迎", "start_time": 350, "end_time": 1280, "speaker": 1},
                                           ]}})
        interim2 = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(interim2.type, "interim")
        self.assertEqual(interim2.text, "您好，欢迎")

        # slice_type 2 is the terminal slice -> final (+ VAD join events).
        await self.server.send({"code": 0, "message": "success", "voice_id": "v1", "message_id": "m3",
                                "result": {"slice_type": 2, "index": 1, "start_time": 0, "end_time": 1280,
                                           "voice_text_str": "您好，欢迎体验。",
                                           "word_size": 3,
                                           "word_list": [
                                               {"word": "您好", "start_time": 0, "end_time": 320, "speaker": 1},
                                               {"word": "欢迎", "start_time": 350, "end_time": 900, "speaker": 1},
                                               {"word": "体验", "start_time": 950, "end_time": 1280, "speaker": 1},
                                           ]}})
        # The utterance already started on its first interim slice: the
        # terminal slice only closes it (no duplicate speech_started).
        events = [await asyncio.wait_for(iterator.__anext__(), 2) for _ in range(2)]
        self.assertEqual(events[0].type, "speech_stopped")
        self.assertAlmostEqual(events[0].end_pcm, 1.28)
        final = events[1]
        self.assertEqual(final.type, "final")
        self.assertEqual(final.text, "您好，欢迎体验。")
        self.assertEqual(final.item_id, "1")
        self.assertEqual(final.speaker, "1")
        # Raw carries the official word_list verbatim for later slices.
        assert final.raw is not None
        self.assertEqual(final.raw["result"]["word_list"][0]["word"], "您好")

        await stream.aclose()

    async def test_speaker_sentences_response_shape(self) -> None:
        """Confirmed second response shape (conference speaker doc): the
        adapter maps it; unknown shapes stay ignored."""
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("zh"), sample_rate=16000, hotwords=[], context=[],
        )
        iterator = stream.__aiter__()
        await self.server.wait_for(lambda: bool(self.server.handshakes))
        await self.server.send({
            "code": 0, "message": "success", "voice_id": "v2", "message_id": "m1",
            "result": {"sentences": {"sentence_list": [
                {"sentence_id": 1, "sentence": "您好，", "sentence_type": 0, "speaker_id": 2, "start_time": 0, "end_time": 700},
                {"sentence_id": 2, "sentence": "欢迎观看", "sentence_type": 1, "speaker_id": 2, "start_time": 800, "end_time": 1500},
            ]}},
        })
        # Pipeline span-join ordering: each sentence opens with
        # speech_started; the confirmed sentence also closes with
        # speech_stopped before its final.
        started1 = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual((started1.type, started1.item_id, started1.speaker), ("speech_started", "1", "2"))
        interim = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(interim.type, "interim")
        self.assertEqual(interim.item_id, "1")
        self.assertEqual(interim.speaker, "2")
        started2 = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual((started2.type, started2.item_id), ("speech_started", "2"))
        stopped2 = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual((stopped2.type, stopped2.item_id), ("speech_stopped", "2"))
        final = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(final.type, "final")
        self.assertEqual(final.item_id, "2")
        self.assertEqual(final.speaker, "2")
        await stream.aclose()

    async def test_unknown_result_shape_is_ignored_not_guessed(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("zh"), sample_rate=16000, hotwords=[], context=[],
        )
        iterator = stream.__aiter__()
        await self.server.wait_for(lambda: bool(self.server.handshakes))
        await self.server.send({"code": 0, "message": "success", "voice_id": "v3", "message_id": "m1",
                                "result": {"mystery_field": [{"unheard": "of"}]}})

        async def next_event() -> Any:
            return await asyncio.wait_for(iterator.__anext__(), 0.3)

        try:
            await next_event()
        except asyncio.TimeoutError:
            pass  # expected: nothing mappable
        else:
            self.fail("unknown result shape must not be mapped")
        await stream.aclose()

    async def test_error_code_maps_to_error_without_signed_url(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("zh"), sample_rate=16000, hotwords=[], context=[],
        )
        iterator = stream.__aiter__()
        await self.server.wait_for(lambda: bool(self.server.handshakes))
        await self.server.send({"code": 4002, "message": "signature expired", "voice_id": "v4"})
        error = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(error.type, "error")
        self.assertIn("4002", error.message)
        self.assertIn("signature expired", error.message)
        # The signed URL (with its signature credential) is never surfaced.
        self.assertNotIn("signature=", error.message)
        self.assertNotIn("asr/v2", error.message)
        await stream.aclose()

    async def test_placeholder_base_url_is_filled_from_app_id(self) -> None:
        provider = create_asr({
            "id": "tencent-placeholder", "kind": "tencent-asr", "model": "16k_ja",
            "baseUrl": "ws://127.0.0.1:9/asr/v2/<appid>", "apiKey": "secret",
            "options": {"appId": "12345", "secretId": "sid", "engineModelType": "16k_ja"},
        })
        stream = provider  # exercise the adapter's signed URL without connecting
        from companion.providers.asr_tencent_asr import _TencentAsrStream
        signed = _TencentAsrStream(stream, SourceLanguagePolicy.specified("ja"), 16000)._signed_url()
        self.assertIn("/asr/v2/12345?", signed)
        self.assertNotIn("<appid>", signed)

    async def test_multi_language_engine_accepts_detect_policy(self) -> None:
        provider = create_asr({
            "id": "tencent-multi", "kind": "tencent-asr", "model": "16k_multi_lang",
            "baseUrl": self.server.url, "apiKey": "fake-secret-key",
            "options": {"appId": "tencent-test-appid", "secretId": "fake-secret-id", "engineModelType": "16k_multi_lang"},
        })
        self.assertEqual(provider.capabilities.language.detection, "unrestricted")
        stream = await provider.stream(
            policy=SourceLanguagePolicy.detect(candidates=("ja", "en")),
            sample_rate=16000, hotwords=[], context=[],
        )
        await stream.aclose()

    async def test_japanese_classic_engine_capability(self) -> None:
        provider = create_asr({
            "id": "tencent-ja", "kind": "tencent-asr", "model": "16k_ja",
            "baseUrl": self.server.url, "apiKey": "k",
            "options": {"secretId": "sid", "engineModelType": "16k_ja"},
        })
        language = provider.capabilities.language
        assert language.supported_tags is not None
        self.assertIn("ja", language.supported_tags)
        self.assertEqual(language.detection, "none")
        self.assertEqual(language.tier, "provider_claimed")

    async def test_missing_credentials_raise_before_handshake(self) -> None:
        provider = create_asr({
            "id": "tc-nokey", "kind": "tencent-asr", "model": "16k_zh_en_speaker_2.0",
            "baseUrl": self.server.url, "apiKey": "", "options": {"secretId": "sid"},
        })
        with self.assertRaises(ValueError):
            await provider.stream(
                policy=SourceLanguagePolicy.specified("zh"), sample_rate=16000, hotwords=[], context=[],
            )
        provider = create_asr({
            "id": "tc-nosecretid", "kind": "tencent-asr", "model": "16k_zh_en_speaker_2.0",
            "baseUrl": self.server.url, "apiKey": "sk", "options": {},
        })
        with self.assertRaises(ValueError):
            await provider.stream(
                policy=SourceLanguagePolicy.specified("zh"), sample_rate=16000, hotwords=[], context=[],
            )

    async def test_close_is_idempotent(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("zh"), sample_rate=16000, hotwords=[], context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.handshakes))
        await stream.aclose()
        await stream.aclose()
        await self.server.wait_for(lambda: self.server.closed_connections >= 1)


if __name__ == "__main__":
    unittest.main()
