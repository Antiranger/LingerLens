"""Fake-WebSocket protocol tests for the assemblyai-streaming ASR adapter.

Fixtures mirror the official AssemblyAI Streaming v3 WebSocket contract
(Begin / SpeechStarted / Turn / Termination / SpeakerRevision):
https://www.assemblyai.com/docs/streaming/api-spec/streaming-websocket
https://www.assemblyai.com/docs/streaming/message-sequence

Key official semantics under test:

* query-string session configuration (misspelled params are silently ignored,
  so Begin.configuration echoes are validated);
* ``Turn`` messages for the same ``turn_order`` COVER each other (the newest
  transcript replaces, never appends);
* a turn is final ONLY when ``end_of_turn == true`` AND
  ``turn_is_formatted == true`` (both-true rule; Universal Streaming can send
  an unformatted terminal turn first);
* word ``start``/``end`` are MILLISECONDS; ``SpeechStarted.timestamp`` is ms;
* ``{"type": "Terminate"}`` ends the session (Termination message follows);
* ``SpeakerRevision`` revises the speaker label of an already-final turn.
"""

from __future__ import annotations

import asyncio
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
from companion.providers.base import SourceLanguagePolicy, validate_source_policy


class FakeAssemblyAIServer:
    def __init__(self) -> None:
        self.handshakes: list[dict[str, Any]] = []
        self.audio = bytearray()
        self.controls: list[dict[str, Any]] = []
        self.outbound: asyncio.Queue[dict[str, Any] | str] = asyncio.Queue()
        self.closed_connections = 0
        self.runner: web.AppRunner | None = None
        self._ws: web.WebSocketResponse | None = None
        self.url = ""
        self.url_401 = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/v3/ws", self._handler)
        app.router.add_get("/v3/ws-401", self._reject)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/v3/ws"
        self.url_401 = f"ws://127.0.0.1:{port}/v3/ws-401"

    async def stop(self) -> None:
        assert self.runner is not None
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        await self.runner.cleanup()

    async def _reject(self, request: web.Request) -> web.Response:
        del request
        return web.Response(status=401, text="invalid token")

    async def _handler(self, request: web.Request) -> web.WebSocketResponse:
        self.handshakes.append({"query": dict(request.query), "auth": request.headers.get("Authorization")})
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
                elif message.type == aiohttp.WSMsgType.TEXT:
                    self.controls.append(json.loads(message.data))
        finally:
            task.cancel()
            self.closed_connections += 1
        return ws

    def controls_of_type(self, message_type: str) -> list[dict[str, Any]]:
        return [message for message in self.controls if message.get("type") == message_type]

    async def send(self, payload: dict[str, Any] | str) -> None:
        await self.outbound.put(payload)

    async def wait_for(self, check, timeout: float = 2.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while not check():
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError("fake server condition not met")
            await asyncio.sleep(0.01)


BEGIN_MESSAGE = {
    "type": "Begin",
    "id": "3207b601-4984-4931-a4dd-4c3b0f56b0dd",
    "expires_at": 1772570132,
    "configuration": {"model": "universal-3-5-pro", "mode": "balanced", "api_version": "2025-05-12"},
}

SPEECH_STARTED = {"type": "SpeechStarted", "timestamp": 1216, "confidence": 0.987654}


def turn(
    *,
    turn_order: int,
    transcript: str,
    end_of_turn: bool,
    turn_is_formatted: bool = True,
    words: list[dict[str, Any]] | None = None,
    speaker_label: str | None = None,
    language_code: str | None = None,
) -> dict[str, Any]:
    if words is None:
        words = [
            {"start": 1216, "end": 1635, "text": "My", "confidence": 0.95, "word_is_final": True},
            {"start": 1640, "end": 2100, "text": "name", "confidence": 0.95, "word_is_final": True},
        ]
    if speaker_label:
        for word in words:
            word["speaker"] = speaker_label
    message: dict[str, Any] = {
        "type": "Turn",
        "turn_order": turn_order,
        "turn_is_formatted": turn_is_formatted,
        "end_of_turn": end_of_turn,
        "transcript": transcript,
        "end_of_turn_confidence": 1,
        "words": words,
        "utterance": transcript,
    }
    if speaker_label:
        message["speaker_label"] = speaker_label
    if language_code:
        message["language_code"] = language_code
        message["language_confidence"] = 0.99
    return message


class AssemblyAIStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeAssemblyAIServer()
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    def provider(self, model: str = "universal-3-5-pro", **options):
        return create_asr({
            "id": "assemblyai-test",
            "kind": "assemblyai-streaming",
            "model": model,
            "baseUrl": self.server.url,
            "apiKey": "fake-key",
            "options": options,
        })

    async def test_handshake_query_auth_and_binary_pcm(self) -> None:
        stream = await self.provider(
            speakerLabels=True,
            maxSpeakers=4,
            mode="balanced",
        ).stream(
            policy=SourceLanguagePolicy.detect(candidates=("ja", "en")),
            sample_rate=16000,
            hotwords=[], context=[],
        )
        await stream.push_pcm(b"\x01\x00" * 800, 0.0)
        await self.server.wait_for(lambda: len(self.server.audio) == 1600)

        handshake = self.server.handshakes[0]
        # Official auth header has NO Bearer prefix.
        self.assertEqual(handshake["auth"], "fake-key")
        self.assertEqual(handshake["query"]["sample_rate"], "16000")
        self.assertEqual(handshake["query"]["encoding"], "pcm_s16le")
        self.assertEqual(handshake["query"]["speech_model"], "universal-3-5-pro")
        self.assertEqual(handshake["query"]["language_codes"], "ja,en")
        self.assertEqual(handshake["query"]["speaker_labels"], "true")
        self.assertEqual(handshake["query"]["max_speakers"], "4")
        await stream.aclose()

    async def test_begin_validation_and_turn_semantics(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("en"), sample_rate=16000, hotwords=[], context=[],
        )
        iterator = stream.__aiter__()
        await self.server.wait_for(lambda: bool(self.server.handshakes))
        # No events are produced by Begin/SpeechStarted alone.
        await self.server.send(BEGIN_MESSAGE)
        await self.server.send(SPEECH_STARTED)
        await self.server.send(turn(turn_order=0, transcript="My name is", end_of_turn=False))
        # The first Turn opens the turn span before the covering interim.
        started = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(started.type, "speech_started")
        self.assertAlmostEqual(started.begin_pcm, 1.216)  # ms -> s
        interim = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(interim.type, "interim")
        self.assertEqual(interim.text, "My name is")
        self.assertEqual(interim.item_id, "0")
        self.assertEqual(interim.caption_observation.kind, "token_snapshot")
        self.assertEqual([token.text for token in interim.caption_observation.tokens], ["My", "name"])
        self.assertAlmostEqual(interim.begin_pcm, 1.216)  # ms -> s
        self.assertAlmostEqual(interim.end_pcm, 2.1)
        self.assertEqual(interim.speaker, None)

        # Same turn_order COVERS the previous interim (replace, not append).
        await self.server.send(turn(turn_order=0, transcript="My name is Sonny.", end_of_turn=False, words=[
            {"start": 1216, "end": 1635, "text": "My", "confidence": 0.95, "word_is_final": True},
            {"start": 1640, "end": 2400, "text": "name", "confidence": 0.95, "word_is_final": True},
            {"start": 2500, "end": 3100, "text": "is Sonny.", "confidence": 0.95, "word_is_final": True},
        ]))
        interim2 = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(interim2.type, "interim")
        self.assertEqual(interim2.text, "My name is Sonny.")
        self.assertEqual(interim2.item_id, "0")
        self.assertAlmostEqual(interim2.end_pcm, 3.1)

        # Terminal but UNFORMATTED turn is NOT final (both-true rule).
        await self.server.send(turn(turn_order=0, transcript="My name is Sonny", end_of_turn=True, turn_is_formatted=False))
        interim3 = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(interim3.type, "interim")

        # end_of_turn && turn_is_formatted -> the single final for the turn.
        await self.server.send(turn(
            turn_order=0, transcript="My name is Sonny.", end_of_turn=True, turn_is_formatted=True,
            speaker_label="A", language_code="en",
            words=[
                {"start": 1216, "end": 1635, "text": "My", "confidence": 0.95, "word_is_final": True},
                {"start": 1640, "end": 2400, "text": "name", "confidence": 0.95, "word_is_final": True},
                {"start": 2500, "end": 3100, "text": "is Sonny.", "confidence": 0.95, "word_is_final": True},
            ],
        ))
        # The turn span is already open, so the terminal adds stop + final.
        events = [await asyncio.wait_for(iterator.__anext__(), 2) for _ in range(2)]
        self.assertEqual(events[0].type, "speech_stopped")
        self.assertEqual(events[0].item_id, "0")
        self.assertAlmostEqual(events[0].end_pcm, 3.1)
        final = events[1]
        self.assertEqual(final.type, "final")
        self.assertEqual(final.text, "My name is Sonny.")
        self.assertEqual(final.item_id, "0")
        self.assertEqual(final.speaker, "A")
        self.assertEqual(final.language, "en")
        self.assertEqual(events[0].caption_observation.kind, "endpoint")
        self.assertEqual(final.caption_observation.kind, "utterance_final")
        self.assertTrue(all(token.provider_stable for token in final.caption_observation.tokens))
        self.assertAlmostEqual(final.begin_pcm, 1.216)
        self.assertAlmostEqual(final.end_pcm, 3.1)
        await stream.aclose()

    async def test_force_endpoint_terminate_and_speaker_revision(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("en"), sample_rate=16000, hotwords=[], context=[],
        )
        await stream.flush()
        await self.server.wait_for(lambda: bool(self.server.controls_of_type("ForceEndpoint")))
        await stream.aclose()
        await self.server.wait_for(lambda: bool(self.server.controls_of_type("Terminate")))
        await self.server.send({
            "type": "Termination",
            "audio_duration_seconds": 45,
            "session_duration_seconds": 47,
        })
        await self.server.send("CLOSE")
        await self.server.wait_for(lambda: self.server.closed_connections >= 1)

    async def test_speaker_revision_event_after_final(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("en"), sample_rate=16000, hotwords=[], context=[],
        )
        iterator = stream.__aiter__()
        await self.server.send(BEGIN_MESSAGE)
        await self.server.send(turn(
            turn_order=3, transcript="My name is Sonny.", end_of_turn=True, turn_is_formatted=True,
            speaker_label="A", words=[
                {"start": 1216, "end": 1635, "text": "My", "confidence": 0.95, "word_is_final": True},
            ],
        ))
        # The terminal turn emits speech_started + speech_stopped + final.
        await asyncio.wait_for(iterator.__anext__(), 2)
        await asyncio.wait_for(iterator.__anext__(), 2)
        final = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(final.type, "final")
        # Official post-Terminate revision: the speaker label of turn 3 changes.
        await self.server.send({"type": "SpeakerRevision", "turn_order": 3, "speaker_label": "B"})
        revision = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(revision.type, "speaker_revision")
        self.assertEqual(revision.item_id, "3")
        self.assertEqual(revision.speaker, "B")
        await stream.aclose()

    async def test_error_message_maps_to_error_event(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("en"), sample_rate=16000, hotwords=[], context=[],
        )
        iterator = stream.__aiter__()
        await self.server.send({"type": "Error", "code": 3007, "detail": "buffer overflow"})
        error = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(error.type, "error")
        self.assertIn("3007", error.message)
        self.assertIn("buffer overflow", error.message)
        await stream.aclose()

    async def test_capabilities_language_detection_and_unknown_model(self) -> None:
        provider = self.provider()
        caps = provider.capabilities
        self.assertTrue(caps.word_timestamps)
        self.assertTrue(caps.manual_commit)
        self.assertFalse(caps.stable_prefix)
        language = caps.language
        self.assertEqual(language.detection, "unrestricted")
        self.assertTrue(language.code_switching)
        self.assertTrue(language.reports_detected_language)
        self.assertEqual(language.tier, "provider_claimed")
        validate_source_policy(SourceLanguagePolicy.specified("ja"), language)
        validate_source_policy(SourceLanguagePolicy.detect(candidates=("ja", "en")), language)
        unknown = self.provider(model="custom-model").capabilities.language
        self.assertEqual(unknown.tier, "experimental")
        self.assertEqual(unknown.detection, "none")

    async def test_failed_handshake_releases_client_session(self) -> None:
        provider = create_asr({
            "id": "aai-badkey", "kind": "assemblyai-streaming",
            "model": "universal-3-5-pro", "baseUrl": self.server.url_401, "apiKey": "bad-key",
        })
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            with self.assertRaises(aiohttp.WSServerHandshakeError):
                await provider.stream(
                    policy=SourceLanguagePolicy.specified("en"), sample_rate=16000, hotwords=[], context=[],
                )
            gc.collect()
        leaked = [item for item in caught if issubclass(item.category, ResourceWarning)]
        self.assertEqual([str(item.message) for item in leaked], [])

    async def test_push_after_close_is_a_noop(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("en"), sample_rate=16000, hotwords=[], context=[],
        )
        await stream.aclose()
        await stream.aclose()  # idempotent
        await stream.push_pcm(b"\x01\x00" * 800, 1.0)


if __name__ == "__main__":
    unittest.main()
