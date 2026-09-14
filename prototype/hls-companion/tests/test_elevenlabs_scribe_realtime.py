"""Fake-WebSocket protocol tests for the elevenlabs-scribe-realtime adapter.

Fixtures mirror the official Scribe v2 Realtime reference
(https://elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime):
query-parameter session config, ``xi-api-key`` header, base64
``input_audio_chunk`` JSON frames, partial/committed transcripts, typed error
events, and a plain WebSocket close with no invented control frames."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import unittest
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.providers import create_asr
from companion.providers.base import SourceLanguagePolicy


class FakeScribeServer:
    def __init__(self) -> None:
        self.handshakes: list[dict[str, Any]] = []
        self.chunks: list[dict[str, Any]] = []
        self.outbound: asyncio.Queue[dict[str, Any] | str] = asyncio.Queue()
        self.closed_connections = 0
        self.runner: web.AppRunner | None = None
        self._ws: web.WebSocketResponse | None = None
        self.url = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/v1/speech-to-text/realtime", self._handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/v1/speech-to-text/realtime"

    async def stop(self) -> None:
        assert self.runner is not None
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()  # don't make teardown wait on clients
        await self.runner.cleanup()

    async def _handler(self, request: web.Request) -> web.WebSocketResponse:
        self.handshakes.append({
            "query": dict(request.query),
            "api_key": request.headers.get("xi-api-key"),
        })
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws = ws
        await ws.send_str(json.dumps({
            "message_type": "session_started",
            "session_id": "sess_123456789",
            "config": {"audio_format": "pcm_16000", "model_id": "scribe_v2_realtime", "sample_rate": 16000},
        }))

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
                if message.type == aiohttp.WSMsgType.TEXT:
                    self.chunks.append(json.loads(message.data))
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


class ElevenLabsScribeRealtimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeScribeServer()
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    def provider(self, **options):
        return create_asr({
            "id": "scribe-test",
            "kind": "elevenlabs-scribe-realtime",
            "model": "scribe_v2_realtime",
            "baseUrl": self.server.url,
            "apiKey": "fake-key",
            "options": options,
        })

    async def test_handshake_query_config_and_base64_audio_frames(self) -> None:
        stream = await self.provider(commitStrategy="manual", includeLanguageDetection=True).stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=16000,
            hotwords=[],
            context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.handshakes))
        handshake = self.server.handshakes[0]
        self.assertEqual(handshake["api_key"], "fake-key")
        self.assertEqual(handshake["query"]["model_id"], "scribe_v2_realtime")
        self.assertEqual(handshake["query"]["audio_format"], "pcm_16000")
        self.assertEqual(handshake["query"]["commit_strategy"], "manual")
        self.assertEqual(handshake["query"]["language_code"], "ja")
        self.assertEqual(handshake["query"]["include_language_detection"], "true")

        audio = bytes(range(256)) * 4
        await stream.push_pcm(audio, 0.0)
        await self.server.wait_for(lambda: len(self.server.chunks) >= 1)
        chunk = self.server.chunks[0]
        self.assertEqual(chunk["message_type"], "input_audio_chunk")
        self.assertEqual(base64.b64decode(chunk["audio_base_64"]), audio)
        self.assertNotIn("commit", chunk)

        # Manual flush: an empty chunk carrying commit=true.
        await stream.flush()
        await self.server.wait_for(lambda: any(item.get("commit") for item in self.server.chunks))
        commit_chunk = next(item for item in self.server.chunks if item.get("commit"))
        self.assertEqual(base64.b64decode(commit_chunk["audio_base_64"]), b"")
        await stream.aclose()

    async def test_detect_policy_omits_language_code(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.detect(candidates=("ja", "en")),
            sample_rate=16000, hotwords=[], context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.handshakes))
        self.assertNotIn("language_code", self.server.handshakes[0]["query"])
        await stream.aclose()

    async def test_partial_and_committed_transcripts(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"), sample_rate=16000, hotwords=[], context=[],
        )
        await self.server.send({"message_type": "partial_transcript", "text": "こんにちはみな"})
        await self.server.send({"message_type": "committed_transcript", "text": "こんにちはみなさん"})
        iterator = stream.__aiter__()
        interim = await asyncio.wait_for(iterator.__anext__(), 2)
        final = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual((interim.type, interim.text), ("interim", "こんにちはみな"))
        self.assertEqual((final.type, final.text), ("final", "こんにちはみなさん"))
        self.assertIsNone(final.begin_pcm)  # no timestamps in this protocol
        await stream.aclose()

    async def test_committed_with_timestamps_carries_detected_language(self) -> None:
        stream = await self.provider(includeLanguageDetection=True).stream(
            policy=SourceLanguagePolicy.specified("ja"), sample_rate=16000, hotwords=[], context=[],
        )
        await self.server.send({
            "message_type": "committed_transcript_with_timestamps",
            "text": " bonjour tout le monde",
            "language_code": "fr",
        })
        iterator = stream.__aiter__()
        final = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(final.type, "final")
        self.assertEqual(final.language, "fr")
        self.assertIsNotNone(final.raw)
        await stream.aclose()

    async def test_timestamped_copy_does_not_duplicate_committed_cue(self) -> None:
        stream = await self.provider(includeTimestamps=True).stream(
            policy=SourceLanguagePolicy.specified("ja"), sample_rate=16000, hotwords=[], context=[],
        )
        await self.server.send({"message_type": "committed_transcript", "text": "こんにちは"})
        await self.server.send({
            "message_type": "committed_transcript_with_timestamps",
            "text": "こんにちは",
            "words": [{"text": "こんにちは", "start": 0.1, "end": 0.8}],
        })
        iterator = stream.__aiter__()
        final = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual((final.type, final.text), ("final", "こんにちは"))
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(iterator.__anext__(), 0.05)
        await stream.aclose()

    async def test_typed_error_events_map_to_error(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"), sample_rate=16000, hotwords=[], context=[],
        )
        await self.server.send({"message_type": "rate_limited", "message": "too many concurrent requests"})
        iterator = stream.__aiter__()
        error = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(error.type, "error")
        self.assertIn("too many concurrent requests", error.message)
        await stream.aclose()

    async def test_close_is_direct_idempotent_and_releases(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"), sample_rate=16000, hotwords=[], context=[],
        )
        # No commit/close control frame may be sent on stop.
        await stream.aclose()
        await stream.aclose()
        await self.server.wait_for(lambda: self.server.closed_connections >= 1)
        self.assertEqual([item for item in self.server.chunks if item.get("message_type") not in {"input_audio_chunk"}], [])

    async def test_push_after_close_is_a_noop_not_an_error(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"), sample_rate=16000, hotwords=[], context=[],
        )
        await stream.aclose()
        await stream.push_pcm(b"\x01\x00" * 1600, 1.0)

    async def test_missing_key_raises_before_handshake(self) -> None:
        provider = create_asr({
            "id": "scribe-nokey", "kind": "elevenlabs-scribe-realtime",
            "model": "scribe_v2_realtime", "baseUrl": self.server.url, "apiKey": "",
        })
        with self.assertRaises(ValueError):
            await provider.stream(
                policy=SourceLanguagePolicy.specified("ja"),
                sample_rate=16000, hotwords=[], context=[],
            )

    async def test_capabilities_commit_strategies_and_unknown_model(self) -> None:
        manual = self.provider(commitStrategy="manual")
        self.assertTrue(manual.capabilities.manual_commit)
        self.assertFalse(manual.capabilities.server_vad)
        vad = self.provider(commitStrategy="vad")
        self.assertFalse(vad.capabilities.manual_commit)
        self.assertTrue(vad.capabilities.server_vad)
        self.assertFalse(manual.capabilities.word_timestamps)
        self.assertFalse(manual.capabilities.speaker_labels)  # undocumented: never claimed
        self.assertEqual(manual.capabilities.language.tier, "provider_claimed")
        unknown = create_asr({
            "id": "scribe-x", "kind": "elevenlabs-scribe-realtime",
            "model": "scribe_v1_realtime", "baseUrl": self.server.url, "apiKey": "k",
        })
        self.assertEqual(unknown.capabilities.language.tier, "experimental")


if __name__ == "__main__":
    unittest.main()
