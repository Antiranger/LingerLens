"""Fake-WebSocket protocol tests for the openai-realtime-transcription ASR adapter.

The fixtures mirror the current official OpenAI Realtime transcription-session
schema (https://platform.openai.com/docs/guides/realtime-transcription). Everything
runs against a local fake WebSocket server; no cloud key is required."""

from __future__ import annotations

import asyncio
import base64
import gc
import json
import sys
import unittest
import warnings
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.providers import create_asr
from companion.providers.base import SourceLanguagePolicy


class FakeOpenAIServer:
    """Local fake WebSocket endpoint recording the OpenAI realtime client protocol."""

    def __init__(self) -> None:
        self.handshakes: list[dict[str, Any]] = []
        self.audio = bytearray()
        self.controls: list[dict[str, Any]] = []
        self.outbound: asyncio.Queue[dict[str, Any] | str] = asyncio.Queue()
        self.closed_connections = 0
        self.runner: web.AppRunner | None = None
        self.url = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/realtime", self._handler)
        app.router.add_get("/realtime-401", self._reject)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/realtime"
        self.url_401 = f"ws://127.0.0.1:{port}/realtime-401"

    async def stop(self) -> None:
        await self.runner.cleanup()

    async def _reject(self, request: web.Request) -> web.Response:
        del request
        return web.Response(status=401, text="invalid api key")

    async def _handler(self, request: web.Request) -> web.WebSocketResponse:
        self.handshakes.append({
            "path": request.path,
            "query": dict(request.query),
            "auth": request.headers.get("Authorization"),
        })
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        async def writer() -> None:
            while True:
                payload = await self.outbound.get()
                if payload == "CLOSE":
                    await ws.close()
                    return
                await ws.send_str(json.dumps(payload))

        writer_task = asyncio.create_task(writer())
        try:
            async for message in ws:
                if message.type == aiohttp.WSMsgType.BINARY:
                    self.audio.extend(message.data)
                elif message.type == aiohttp.WSMsgType.TEXT:
                    self.controls.append(json.loads(message.data))
        finally:
            writer_task.cancel()
            self.closed_connections += 1
        return ws

    def controls_of_type(self, message_type: str) -> list[dict[str, Any]]:
        return [message for message in self.controls if message.get("type") == message_type]

    async def wait_for(self, check, timeout: float = 2.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while not check():
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError("fake server condition not met")
            await asyncio.sleep(0.01)

    async def send(self, payload: dict[str, Any]) -> None:
        await self.outbound.put(payload)


def session_update_fixture(model: str, languages: list[str] | None = None) -> dict[str, Any]:
    transcription: dict[str, Any] = {"model": model}
    if languages is not None:
        transcription["languages"] = languages
    return {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "transcription": transcription,
                    "turn_detection": None,
                }
            },
        },
    }


class OpenAIRealtimeTranscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeOpenAIServer()
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    def provider(self, **options):
        return create_asr({
            "id": "openai-test",
            "kind": "openai-realtime-transcription",
            "model": "gpt-live-transcribe",
            "baseUrl": self.server.url,
            "apiKey": "fake-key",
            "options": options,
        })

    async def test_session_update_schema_and_pcm_append(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=24000,
            hotwords=[],
            context=[],
        )
        await stream.push_pcm(b"\x02\x00" * 2400, 0.0)
        await self.server.wait_for(lambda: len(self.server.controls_of_type("input_audio_buffer.append")) >= 1)

        session_updates = self.server.controls_of_type("session.update")
        self.assertEqual(len(session_updates), 1)
        session = session_updates[0]["session"]
        self.assertEqual(session["type"], "transcription")
        audio_input = session["audio"]["input"]
        self.assertEqual(audio_input["format"], {"type": "audio/pcm", "rate": 24000})
        self.assertEqual(audio_input["transcription"]["model"], "gpt-live-transcribe")
        self.assertEqual(audio_input["transcription"]["languages"], ["ja"])

        appends = self.server.controls_of_type("input_audio_buffer.append")
        self.assertEqual(len(appends), 1)
        decoded = base64.b64decode(appends[0]["audio"])
        self.assertEqual(decoded, b"\x02\x00" * 2400)

        await stream.aclose()

    async def test_official_delta_and_completed_events(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=24000,
            hotwords=[],
            context=[],
        )
        await self.server.send({
            "type": "input_audio_buffer.speech_started",
            "item_id": "item_001",
            "audio_start_ms": 1500,
        })
        await self.server.send({
            "type": "input_audio_buffer.speech_stopped",
            "item_id": "item_001",
            "audio_end_ms": 3200,
        })
        await self.server.send({
            "type": "conversation.item.input_audio_transcription.delta",
            "item_id": "item_001",
            "content_index": 0,
            "delta": "Bonjour,",
        })
        await self.server.send({
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item_001",
            "content_index": 0,
            "transcript": "Bonjour, pouvez-vous m'entendre ?",
            "languages": [{"code": "fr"}],
        })

        iterator = stream.__aiter__()
        events = [await asyncio.wait_for(iterator.__anext__(), 2) for _ in range(4)]

        self.assertEqual(events[0].type, "speech_started")
        self.assertEqual(events[0].begin_pcm, 1.5)
        self.assertEqual(events[1].type, "speech_stopped")
        self.assertEqual(events[1].end_pcm, 3.2)
        self.assertEqual(events[2].type, "interim")
        self.assertEqual(events[3].type, "final")
        self.assertEqual(events[3].text, "Bonjour, pouvez-vous m'entendre ?")
        self.assertEqual(events[3].language, "fr")
        await stream.aclose()

    async def test_session_update_never_sends_singular_language_field(self) -> None:
        """Audit regression guard: the current schema uses plural ``languages``
        candidate hints; the singular ``language`` field must never appear
        (sending both is rejected by the API)."""
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=24000,
            hotwords=[],
            context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.controls_of_type("session.update")))
        payload = json.dumps(self.server.controls_of_type("session.update")[0])
        self.assertNotIn('"language"', payload)
        await stream.aclose()

    async def test_out_of_order_completed_events_pair_by_item_id(self) -> None:
        """Official contract: completed-event ordering across speech turns is
        NOT guaranteed; deltas and finals must be correlated by item_id."""
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=24000,
            hotwords=[],
            context=[],
        )
        await self.server.send({"type": "conversation.item.input_audio_transcription.delta", "item_id": "item_001", "delta": "一"})
        await self.server.send({"type": "conversation.item.input_audio_transcription.delta", "item_id": "item_002", "delta": "二"})
        await self.server.send({"type": "conversation.item.input_audio_transcription.completed", "item_id": "item_002", "transcript": "二番目"})
        await self.server.send({"type": "conversation.item.input_audio_transcription.completed", "item_id": "item_001", "transcript": "一番目"})
        iterator = stream.__aiter__()
        events = [await asyncio.wait_for(iterator.__anext__(), 2) for _ in range(4)]
        self.assertEqual((events[0].type, events[0].text, events[0].item_id), ("interim", "一", "item_001"))
        self.assertEqual((events[1].type, events[1].text, events[1].item_id), ("interim", "二", "item_002"))
        self.assertEqual((events[2].type, events[2].text, events[2].item_id), ("final", "二番目", "item_002"))
        self.assertEqual((events[3].type, events[3].text, events[3].item_id), ("final", "一番目", "item_001"))
        await stream.aclose()

    async def test_legacy_preview_model_is_not_promoted(self) -> None:
        """The legacy realtime-preview generation stays out of the current
        schema: unknown model ids remain experimental and claim nothing."""
        provider = create_asr({
            "id": "legacy", "kind": "openai-realtime-transcription",
            "model": "gpt-4o-realtime-preview", "baseUrl": self.server.url, "apiKey": "k",
        })
        language = provider.capabilities.language
        self.assertEqual(language.tier, "experimental")
        self.assertEqual(language.detection, "none")
        self.assertFalse(language.reports_detected_language)

    async def test_detect_policy_sends_candidates_as_language_hints(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.detect(candidates=("fr", "es")),
            sample_rate=24000,
            hotwords=[],
            context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.controls_of_type("session.update")))
        session = self.server.controls_of_type("session.update")[0]["session"]
        self.assertEqual(session["audio"]["input"]["transcription"]["languages"], ["fr", "es"])
        await stream.aclose()

    async def test_detect_without_candidates_omits_languages(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.detect(),
            sample_rate=24000,
            hotwords=[],
            context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.controls_of_type("session.update")))
        session = self.server.controls_of_type("session.update")[0]["session"]
        self.assertNotIn("languages", session["audio"]["input"]["transcription"])
        await stream.aclose()

    async def test_manual_commit_and_close(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=24000,
            hotwords=[],
            context=[],
        )
        await stream.commit()
        await self.server.wait_for(lambda: bool(self.server.controls_of_type("input_audio_buffer.commit")))
        await stream.aclose()

    async def test_missing_key_raises_before_handshake(self) -> None:
        provider = create_asr({
            "id": "openai-nokey",
            "kind": "openai-realtime-transcription",
            "model": "gpt-live-transcribe",
            "baseUrl": self.server.url,
            "apiKey": "",
        })
        with self.assertRaises(ValueError):
            await provider.stream(
                policy=SourceLanguagePolicy.specified("ja"),
                sample_rate=24000,
                hotwords=[],
                context=[],
            )

    async def test_gpt_transcribe_omits_languages_hint(self) -> None:
        provider = create_asr({
            "id": "openai-gpt-transcribe-test",
            "kind": "openai-realtime-transcription",
            "model": "gpt-transcribe",
            "baseUrl": self.server.url,
            "apiKey": "fake-key",
        })
        stream = await provider.stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=24000,
            hotwords=[],
            context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.controls_of_type("session.update")))
        session = self.server.controls_of_type("session.update")[0]["session"]
        self.assertNotIn("languages", session["audio"]["input"]["transcription"])
        await stream.aclose()

    async def test_aclose_is_idempotent(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=24000,
            hotwords=[],
            context=[],
        )
        await stream.aclose()
        await stream.aclose()
        await self.server.wait_for(lambda: self.server.closed_connections >= 1)

    async def test_sample_rate_mismatch_raises(self) -> None:
        provider = self.provider()
        with self.assertRaises(ValueError):
            await provider.stream(
                policy=SourceLanguagePolicy.specified("ja"),
                sample_rate=16000,
                hotwords=[],
                context=[],
            )

    async def test_error_event_and_usage_parsing(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=24000,
            hotwords=[],
            context=[],
        )
        await self.server.send({
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item_009",
            "content_index": 0,
            "transcript": "",
            "languages": [],
            "usage": {"type": "tokens", "total_tokens": 16, "input_tokens": 11, "output_tokens": 5},
        })
        await self.server.send({"type": "error", "error": {"type": "server_error", "message": "transcription failed"}})
        iterator = stream.__aiter__()
        final = await asyncio.wait_for(iterator.__anext__(), 2)
        error = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(final.type, "final")
        self.assertIsNone(final.language)
        self.assertIsNotNone(final.raw)
        assert final.raw is not None
        self.assertEqual(final.raw["usage"]["total_tokens"], 16)
        self.assertEqual(error.type, "error")
        self.assertEqual(error.message, "transcription failed")
        await stream.aclose()

    async def test_push_after_close_is_a_noop_not_an_error(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=24000,
            hotwords=[],
            context=[],
        )
        await stream.aclose()
        await stream.push_pcm(b"\x02\x00" * 2400, 1.0)


    async def test_failed_handshake_releases_client_session(self) -> None:
        """A failed ws_connect (e.g. 401) must close the ClientSession it created;
        an unclosed session would surface as a ResourceWarning, never be hidden."""
        provider = create_asr({
            "id": "openai-badkey",
            "kind": "openai-realtime-transcription",
            "model": "gpt-live-transcribe",
            "baseUrl": self.server.url_401,
            "apiKey": "bad-key",
        })
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            with self.assertRaises(aiohttp.WSServerHandshakeError):
                await provider.stream(
                    policy=SourceLanguagePolicy.specified("ja"),
                    sample_rate=24000,
                    hotwords=[],
                    context=[],
                )
            gc.collect()
        leaked = [item for item in caught if issubclass(item.category, ResourceWarning)]
        self.assertEqual([str(item.message) for item in leaked], [])


if __name__ == "__main__":
    unittest.main()
