"""Fake-WebSocket tests for the Qwen3-ASR-Flash-Realtime adapter.

Before this file the profile appeared in the catalog and in the settings dialog,
and the only thing that had ever been asserted about its wire format was that a
failed handshake does not leak a session (test_asr_handshake_cleanup.py). That
leaves every claim a viewer depends on unproven: what the session opens with,
which frame carries the timing, and which text may still be rewritten.

The traps this suite pins come from the shape of the live event stream, recorded
once against the real service:

* ``speech_started``/``speech_stopped`` carry ``audio_start_ms``/``audio_end_ms``
  and ``completed`` carries **no** timing at all, so a client that reads the
  caption window off ``completed`` silently falls back to an estimate,
* ``input_audio_transcription.text`` is a stable prefix plus a ``stash`` tail
  that the provider rewrites, so the tail must never be claimed as stable,
* the source language is mandatory: this adapter has no detection, so a policy
  that leaves it to the provider must be refused at connect time rather than
  connected and left mute,
* ``session.finish`` ends the session; without it the last utterance is not
  flushed.
"""
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

from companion.languages import LanguageNotSupportedError
from companion.providers import create_asr
from companion.providers.base import SourceLanguagePolicy

MODEL = "qwen3-asr-flash-realtime"


class FakeQwenRealtimeServer:
    def __init__(self) -> None:
        self.headers: list[dict[str, str]] = []
        self.paths: list[str] = []
        self.session_updates: list[dict[str, Any]] = []
        self.audio: list[str] = []
        self.commits = 0
        self.finish_requests = 0
        self._sockets: list[web.WebSocketResponse] = []
        self.outbound: asyncio.Queue[dict[str, Any] | str] = asyncio.Queue()
        self.runner: web.AppRunner | None = None
        self.url = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/api-ws/v1/realtime", self._handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        server = site._server
        assert server is not None
        port = server.sockets[0].getsockname()[1]  # type: ignore[attr-defined]
        self.url = f"ws://127.0.0.1:{port}/api-ws/v1/realtime"

    async def stop(self) -> None:
        assert self.runner is not None
        for ws in self._sockets:
            if not ws.closed:
                await ws.close()
        await self.runner.cleanup()

    async def _handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._sockets.append(ws)
        self.headers.append(dict(request.headers))
        self.paths.append(request.path_qs)

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
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                frame = json.loads(message.data)
                kind = frame.get("type")
                if kind == "session.update":
                    self.session_updates.append(frame)
                    await ws.send_str(json.dumps({"type": "session.updated"}))
                elif kind == "input_audio_buffer.append":
                    self.audio.append(frame.get("audio") or "")
                elif kind == "input_audio_buffer.commit":
                    self.commits += 1
                elif kind == "session.finish":
                    self.finish_requests += 1
                    await ws.send_str(json.dumps({"type": "session.finished"}))
        finally:
            task.cancel()
        return ws

    async def send(self, payload: dict[str, Any]) -> None:
        await self.outbound.put(payload)

    async def wait_for(self, check, timeout: float = 2.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while not check():
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("fake server condition not met")
            await asyncio.sleep(0.01)


class QwenRealtimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeQwenRealtimeServer()
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    def provider(self, **options):
        options.setdefault("turnDetection", {"type": "server_vad", "threshold": 0.2, "silenceDurationMs": 400})
        return create_asr({
            "id": "qwen-realtime-test",
            "kind": "dashscope-qwen-realtime",
            "model": MODEL,
            "baseUrl": self.server.url,
            "apiKey": "fake-key",
            "options": options,
        })

    async def connected(self, provider, policy: SourceLanguagePolicy | None = None):
        stream = await provider.stream(
            policy=policy or SourceLanguagePolicy.specified("ja"),
            sample_rate=16000, hotwords=[], context=[],
        )
        self.addAsyncCleanup(stream.aclose)
        await self.server.wait_for(lambda: len(self.server.session_updates) >= 1)
        return stream

    async def next_event(self, iterator, kind: str):
        for _ in range(20):
            event = await asyncio.wait_for(iterator.__anext__(), 2)
            if event.type == kind:
                return event
        raise AssertionError(f"no {kind} event arrived")

    async def test_translating_is_not_this_profiles_job(self) -> None:
        capabilities = self.provider().capabilities
        self.assertFalse(capabilities.native_translation.enabled)
        self.assertEqual(
            capabilities.language.detection, "none",
            "the settings dialog blocks 源语言=自动 on this claim; a wrong value "
            "would promise a mode the adapter then refuses",
        )
        self.assertTrue(capabilities.language.reports_detected_language)
        self.assertIn("ja", capabilities.language.supported_tags)

    async def test_the_handshake_uses_the_bearer_header_and_the_model_query(self) -> None:
        await self.connected(self.provider())
        self.assertEqual(self.server.headers[0].get("Authorization"), "Bearer fake-key")
        self.assertEqual(self.server.paths[0], f"/api-ws/v1/realtime?model={MODEL}")

    async def test_the_session_declares_pcm_the_language_and_a_short_vad_window(self) -> None:
        await self.connected(self.provider())
        session = self.server.session_updates[0]["session"]
        self.assertEqual(session["input_audio_format"], "pcm")
        self.assertEqual(session["sample_rate"], 16000)
        self.assertEqual(session["input_audio_transcription"], {"language": "ja"})
        self.assertEqual(session["turn_detection"], {
            "type": "server_vad", "threshold": 0.2, "silence_duration_ms": 400,
        }, "a wide silence window merges continuous speech into subtitle-sized "
           "sentences nobody can read; 400ms is the documented fast setting")

    async def test_a_source_language_the_profile_must_choose_is_refused(self) -> None:
        with self.assertRaises(LanguageNotSupportedError):
            await self.provider().stream(
                policy=SourceLanguagePolicy.detect(), sample_rate=16000,
                hotwords=[], context=[],
            )

    async def test_audio_is_appended_as_base64_pcm(self) -> None:
        stream = await self.connected(self.provider())
        await stream.push_pcm(b"\x01\x02\x03\x04", 0.0)
        await self.server.wait_for(lambda: len(self.server.audio) >= 1)
        self.assertEqual(base64.b64decode(self.server.audio[0]), b"\x01\x02\x03\x04")

    async def test_the_stable_prefix_and_the_rewritten_tail_are_kept_apart(self) -> None:
        stream = await self.connected(self.provider())
        iterator = stream.__aiter__()
        await self.server.send({
            "type": "conversation.item.input_audio_transcription.text",
            "item_id": "i1", "text": "こんにちは", "stash": "、", "language": "ja",
        })
        interim = await self.next_event(iterator, "interim")
        self.assertEqual(interim.text, "こんにちは")
        self.assertEqual(interim.stash, "、")
        observation = interim.caption_observation
        assert observation is not None
        self.assertEqual(observation.kind, "stable_prefix_snapshot")
        self.assertEqual(observation.stable_text, "こんにちは")
        self.assertEqual(observation.tentative_text, "、", "the stash is rewritten, "
                         "so claiming it as stable would let the chunker publish a "
                         "prefix the provider was about to change")

    async def test_the_turn_marker_carries_the_window_and_closes_the_caption(self) -> None:
        stream = await self.connected(self.provider())
        iterator = stream.__aiter__()
        await self.server.send({"type": "input_audio_buffer.speech_started", "item_id": "i1", "audio_start_ms": 120})
        started = await self.next_event(iterator, "speech_started")
        self.assertEqual(started.begin_pcm, 0.12)
        await self.server.send({"type": "input_audio_buffer.speech_stopped", "item_id": "i1", "audio_end_ms": 1400})
        stopped = await self.next_event(iterator, "speech_stopped")
        assert stopped.caption_observation is not None
        self.assertEqual((stopped.end_pcm, stopped.caption_observation.kind), (1.4, "endpoint"))

    async def test_the_final_carries_no_timing_of_its_own(self) -> None:
        """Verified against a captured live stream: ``completed`` has no ``audio_*_ms``.

        Reading the window off it used to fall through to an estimator, which put
        every caption a few hundred milliseconds in the wrong place.
        """
        stream = await self.connected(self.provider())
        iterator = stream.__aiter__()
        await self.server.send({
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "i1", "transcript": "こんにちは、", "language": "ja",
            "content_index": 0, "emotion": "neutral",
        })
        final = await self.next_event(iterator, "final")
        self.assertEqual(final.text, "こんにちは、")
        self.assertEqual(final.language, "ja")
        self.assertIsNone(final.begin_pcm)
        self.assertIsNone(final.end_pcm)
        assert final.caption_observation is not None
        self.assertEqual(final.caption_observation.kind, "utterance_final")

    async def test_a_provider_error_becomes_an_error_event(self) -> None:
        stream = await self.connected(self.provider())
        iterator = stream.__aiter__()
        await self.server.send({"type": "error", "error": {"code": "InvalidParameter", "message": "bad language"}})
        event = await self.next_event(iterator, "error")
        self.assertIn("bad language", event.message or "")

    async def test_a_hard_cap_can_break_an_utterance_the_provider_is_still_streaming(self) -> None:
        stream = await self.connected(self.provider())
        await stream.commit()
        await self.server.wait_for(lambda: self.server.commits >= 1)

    async def test_closing_finishes_the_session(self) -> None:
        stream = await self.connected(self.provider())
        await stream.aclose()
        await self.server.wait_for(lambda: self.server.finish_requests >= 1)


if __name__ == "__main__":
    unittest.main()
