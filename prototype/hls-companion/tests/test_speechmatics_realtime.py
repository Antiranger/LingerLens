"""Fake-WebSocket protocol tests for the speechmatics-realtime adapter.

Fixtures mirror the official Speechmatics Realtime WebSocket v2 API:
https://docs.speechmatics.com/api-ref/realtime-transcription-websocket

Key official semantics under test:

* ``StartRecognition`` first message (``model`` field -- the deprecated
  ``operating_point`` is NOT sent), ``Authorization: Bearer`` handshake;
* binary ``AddAudio`` frames each acknowledged by ``AudioAdded {seq_no}``;
* ``AddPartialTranscript`` -> interim (partials carry no usable confidence),
  ``AddTranscript`` -> final; ``start_time``/``end_time`` are SECONDS;
* ``EndOfStream {last_seq_no}`` -> ``EndOfTranscript`` ends the session;
* ``ForceEndOfUtterance`` manual flush; diarization via
  ``transcription_config.diarization = "speaker"``;
* single-language policy: ``language`` is required, no detection contract.
"""

from __future__ import annotations

import asyncio
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


class FakeSpeechmaticsServer:
    def __init__(self) -> None:
        self.handshakes: list[dict[str, Any]] = []
        self.start_requests: list[dict[str, Any]] = []
        self.controls: list[dict[str, Any]] = []
        self.audio_frames = 0
        self.outbound: asyncio.Queue[dict[str, Any] | str] = asyncio.Queue()
        self.closed_connections = 0
        self.runner: web.AppRunner | None = None
        self._ws: web.WebSocketResponse | None = None
        self.url = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/v2/", self._handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/v2/"

    async def stop(self) -> None:
        assert self.runner is not None
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        await self.runner.cleanup()

    async def _handler(self, request: web.Request) -> web.WebSocketResponse:
        self.handshakes.append({"auth": request.headers.get("Authorization")})
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws = ws
        seq = 0

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
                    self.audio_frames += 1
                    seq += 1
                    await ws.send_str(json.dumps({"message": "AudioAdded", "seq_no": seq}))
                elif message.type == aiohttp.WSMsgType.TEXT:
                    raw = json.loads(message.data)
                    self.controls.append(raw)
                    if raw.get("message") == "StartRecognition":
                        self.start_requests.append(raw)
                        await ws.send_str(json.dumps({
                            "message": "RecognitionStarted",
                            "id": "9c8c7f70-ead1-4e08-99d5-6a544d0d8645",
                            "language_pack_info": {"language_description": "Japanese", "writing_direction": "ltr", "itn": True},
                        }))
                    elif raw.get("message") == "EndOfStream":
                        await ws.send_str(json.dumps({"message": "EndOfTranscript"}))
        finally:
            task.cancel()
            self.closed_connections += 1
        return ws

    def controls_of(self, message: str) -> list[dict[str, Any]]:
        return [item for item in self.controls if item.get("message") == message]

    async def send(self, payload: dict[str, Any] | str) -> None:
        await self.outbound.put(payload)

    async def wait_for(self, check, timeout: float = 2.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while not check():
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError("fake server condition not met")
            await asyncio.sleep(0.01)


def transcript_message(message: str, *, start: float, end: float, transcript: str, words: list[dict[str, Any]], metadata_only: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "message": message,
        "format": "2.4",
        "results": [
            {
                "type": word["type"] if word.get("type") else "word",
                "start_time": word["start_time"],
                "end_time": word["end_time"],
                "attaches_to": "none",
                "is_eos": bool(word.get("is_eos")),
                "alternatives": [{
                    "content": word["content"],
                    "confidence": word.get("confidence", 0.98),
                    "language": word.get("language", "ja"),
                    **({"speaker": word["speaker"]} if "speaker" in word else {}),
                }],
            }
            for word in words
        ],
    }
    if metadata_only:
        payload["tags"] = []
    payload["metadata"] = {"start_time": start, "end_time": end, "transcript": transcript}
    return payload


PARTIAL = transcript_message(
    "AddPartialTranscript", start=0.12, end=0.6, transcript="こんにちは",
    words=[{"start_time": 0.12, "end_time": 0.6, "content": "こんにちは", "confidence": 0.4}],
)
FINAL = transcript_message(
    "AddTranscript", start=0.12, end=1.45, transcript="こんにちは、みなさん",
    words=[
        {"start_time": 0.12, "end_time": 0.6, "content": "こんにちは", "is_eos": False, "speaker": "S1"},
        {"start_time": 0.65, "end_time": 1.45, "content": "、みなさん", "is_eos": True, "speaker": "S1"},
    ],
)


class SpeechmaticsRealtimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeSpeechmaticsServer()
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    def provider(self, **options):
        return create_asr({
            "id": "speechmatics-test",
            "kind": "speechmatics-realtime",
            "model": "enhanced",
            "baseUrl": self.server.url,
            "apiKey": "fake-key",
            "options": options,
        })

    async def test_start_recognition_contract_and_audio_frames(self) -> None:
        stream = await self.provider(
            diarization=True, maxSpeakers=6, maxDelaySeconds=3,
        ).stream(
            policy=SourceLanguagePolicy.specified("ja"), sample_rate=16000, hotwords=[], context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.start_requests))
        handshake = self.server.handshakes[0]
        self.assertEqual(handshake["auth"], "Bearer fake-key")
        request = self.server.start_requests[0]
        self.assertEqual(request["audio_format"], {"type": "raw", "encoding": "pcm_s16le", "sample_rate": 16000})
        config = request["transcription_config"]
        self.assertEqual(config["language"], "ja")
        self.assertEqual(config["model"], "enhanced")  # NOT operating_point
        self.assertTrue(config["enable_partials"])
        self.assertEqual(config["diarization"], "speaker")
        self.assertEqual(config["speaker_diarization_config"]["max_speakers"], 6)
        self.assertEqual(config["max_delay"], 3)

        await stream.push_pcm(b"\x01\x00" * 3200, 0.0)
        await self.server.wait_for(lambda: self.server.audio_frames >= 1)
        await stream.aclose()

    async def test_partial_final_and_end_of_stream(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"), sample_rate=16000, hotwords=[], context=[],
        )
        iterator = stream.__aiter__()
        await self.server.wait_for(lambda: bool(self.server.start_requests))
        await stream.push_pcm(b"\x01\x00" * 3200, 0.0)
        await self.server.wait_for(lambda: self.server.audio_frames >= 1)

        await self.server.send(PARTIAL)
        started = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(started.type, "speech_started")
        self.assertEqual(started.item_id, "1")
        interim = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(interim.type, "interim")
        self.assertEqual(interim.text, "こんにちは")
        self.assertAlmostEqual(interim.begin_pcm, 0.12)  # seconds, not ms
        self.assertAlmostEqual(interim.end_pcm, 0.6)
        # Partial confidence is meaningless upstream: not propagated.

        await self.server.send(FINAL)
        # The final CLOSES the utterance the partials opened: same item id,
        # no duplicate speech_started.
        events = [await asyncio.wait_for(iterator.__anext__(), 2) for _ in range(2)]
        self.assertEqual(events[0].type, "speech_stopped")
        self.assertEqual(events[0].item_id, "1")
        self.assertAlmostEqual(events[0].end_pcm, 1.45)
        final = events[1]
        self.assertEqual(final.type, "final")
        self.assertEqual(final.text, "こんにちは、みなさん")
        self.assertEqual(final.speaker, "S1")
        self.assertEqual(final.item_id, "1")

        await stream.aclose()
        await self.server.wait_for(lambda: bool(self.server.controls_of("EndOfStream")))
        end_of_stream = self.server.controls_of("EndOfStream")[0]
        self.assertEqual(end_of_stream["last_seq_no"], self.server.audio_frames)
        # The fake server answers EndOfStream with EndOfTranscript on the
        # socket; the adapter consumes it and closes. Wait for the teardown.
        await self.server.wait_for(lambda: self.server.closed_connections >= 1)

    async def test_force_end_of_utterance_flush(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"), sample_rate=16000, hotwords=[], context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.start_requests))
        await stream.flush()
        await self.server.wait_for(lambda: bool(self.server.controls_of("ForceEndOfUtterance")))
        await stream.aclose()

    async def test_error_message_maps_to_error_event(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"), sample_rate=16000, hotwords=[], context=[],
        )
        iterator = stream.__aiter__()
        await self.server.send({"message": "Error", "code": "4005", "reason": "quota_exceeded"})
        error = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(error.type, "error")
        self.assertIn("4005", error.message)
        self.assertIn("quota_exceeded", error.message)
        await stream.aclose()

    async def test_detect_policy_is_rejected_single_language_contract(self) -> None:
        provider = self.provider()
        language = provider.capabilities.language
        self.assertEqual(language.detection, "none")
        with self.assertRaises(LanguageNotSupportedError):
            await provider.stream(
                policy=SourceLanguagePolicy.detect(candidates=("ja", "en")),
                sample_rate=16000, hotwords=[], context=[],
            )
        caps = provider.capabilities
        self.assertTrue(caps.word_timestamps)
        self.assertEqual(caps.language.tier, "provider_claimed")

    async def test_missing_key_raises_before_handshake(self) -> None:
        provider = create_asr({
            "id": "sm-nokey", "kind": "speechmatics-realtime", "model": "enhanced",
            "baseUrl": self.server.url, "apiKey": "", "options": {},
        })
        with self.assertRaises(ValueError):
            await provider.stream(
                policy=SourceLanguagePolicy.specified("ja"), sample_rate=16000, hotwords=[], context=[],
            )

    async def test_close_is_idempotent(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"), sample_rate=16000, hotwords=[], context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.start_requests))
        await stream.aclose()
        await stream.aclose()
        await self.server.wait_for(lambda: self.server.closed_connections >= 1)


if __name__ == "__main__":
    unittest.main()
