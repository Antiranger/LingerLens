"""Fake-WebSocket protocol tests for the deepgram-streaming ASR adapter.

The fixtures mirror the current official Deepgram low-level streaming contract
(https://developers.deepgram.com/reference/speech-to-text/listen-streaming and
https://developers.deepgram.com/docs/multilingual-code-switching). Everything runs
against a local fake WebSocket server; no cloud key is required."""

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

from companion.languages import LanguageNotSupportedError
from companion.providers import create_asr
from companion.providers.base import SourceLanguagePolicy, StreamMeta, validate_source_policy
from companion.subtitle_pipeline import SubtitlePipeline
from companion.subtitle_store import CueStore


class FakeDeepgramServer:
    """Local fake WebSocket endpoint recording the Deepgram client protocol."""

    def __init__(self) -> None:
        self.handshakes: list[dict[str, Any]] = []
        self.audio = bytearray()
        self.controls: list[dict[str, Any]] = []
        self.outbound: asyncio.Queue[dict[str, Any] | str] = asyncio.Queue()
        self.closed_connections = 0
        self.runner: web.AppRunner | None = None
        self._ws: web.WebSocketResponse | None = None
        self.url = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/listen", self._handler)
        app.router.add_get("/listen-401", self._reject)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/listen"
        self.url_401 = f"ws://127.0.0.1:{port}/listen-401"

    async def stop(self) -> None:
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()  # don't make teardown wait on clients
        await self.runner.cleanup()

    async def _reject(self, request: web.Request) -> web.Response:
        del request
        return web.Response(status=401, text="invalid token")

    async def _handler(self, request: web.Request) -> web.WebSocketResponse:
        self.handshakes.append(
            {
                "path": request.path,
                "query": dict(request.query),
                "auth": request.headers.get("Authorization"),
                "subprotocols": request.headers.getall("Sec-WebSocket-Protocol", []),
            }
        )
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws = ws

        async def writer() -> None:
            while True:
                payload = await self.outbound.get()
                if payload == "CLOSE":
                    await ws.close()
                    return
                if isinstance(payload, str) and payload.startswith("RAW:"):
                    await ws.send_str(payload[4:])
                    continue
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

    async def send(self, payload: dict[str, Any] | str) -> None:
        await self.outbound.put(payload)


# Captured from the current official Deepgram listen-streaming documentation examples.
SPEECH_STARTED = {"type": "SpeechStarted", "channel": [0], "timestamp": 0.5}


def results_message(
    *,
    is_final: bool,
    speech_final: bool = False,
    start: float = 0.48,
    duration: float = 1.6,
    transcript: str = "será el inglés muchos",
    languages: list[str] | None = None,
    words: list[dict[str, Any]] | None = None,
    from_finalize: bool = False,
) -> dict[str, Any]:
    return {
        "type": "Results",
        "channel_index": [0, 1],
        "duration": duration,
        "start": start,
        "is_final": is_final,
        "speech_final": speech_final,
        "from_finalize": from_finalize,
        "channel": {
            "alternatives": [
                {
                    "transcript": transcript,
                    "confidence": 0.9374,
                    "languages": languages or ["es"],
                    "words": words
                    or [
                        {"word": "será", "start": 0.55, "end": 0.91, "confidence": 0.95, "language": "es"},
                        {"word": "el", "start": 0.95, "end": 1.15, "confidence": 0.9, "language": "es"},
                        {"word": "inglés", "start": 1.2, "end": 1.6, "confidence": 0.9, "language": "en"},
                        {"word": "muchos", "start": 1.7, "end": 2.05, "confidence": 0.9, "language": "es"},
                    ],
                }
            ]
        },
        "metadata": {"request_id": "req-1", "model_info": {"name": "nova-3", "version": "1", "arch": "nova-3"}},
    }


class DeepgramStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeDeepgramServer()
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    def provider(self, **options):
        return create_asr({
            "id": "dg-test",
            "kind": "deepgram-streaming",
            "model": "nova-3",
            "baseUrl": self.server.url,
            "apiKey": "fake-key",
            "options": options,
        })

    async def test_handshake_auth_and_query_and_pcm(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=16000,
            hotwords=[],
            context=[],
        )
        await stream.push_pcm(b"\x01\x00" * 1600, 0.0)
        await self.server.wait_for(lambda: len(self.server.audio) >= 3200)

        handshake = self.server.handshakes[0]
        self.assertEqual(handshake["auth"], "Token fake-key")
        self.assertEqual(handshake["query"]["model"], "nova-3")
        self.assertEqual(handshake["query"]["encoding"], "linear16")
        self.assertEqual(handshake["query"]["sample_rate"], "16000")
        self.assertEqual(handshake["query"]["channels"], "1")
        self.assertEqual(handshake["query"]["language"], "ja")
        self.assertEqual(len(self.server.audio), 3200)
        await stream.aclose()

    async def test_official_results_mapping_interim_final_and_vad(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=16000,
            hotwords=[],
            context=[],
        )
        await self.server.send(SPEECH_STARTED)
        await self.server.send(results_message(is_final=False))
        await self.server.send(results_message(is_final=True, speech_final=True))

        iterator = stream.__aiter__()
        events = [await asyncio.wait_for(iterator.__anext__(), 2) for _ in range(4)]

        self.assertEqual(events[0].type, "speech_started")
        self.assertEqual(events[0].begin_pcm, 0.5)
        self.assertEqual(events[1].type, "interim")
        self.assertEqual(events[1].text, "será el inglés muchos")
        self.assertEqual(events[1].caption_observation.kind, "token_snapshot")
        self.assertEqual([token.text for token in events[1].caption_observation.tokens], ["será", "el", "inglés", "muchos"])
        self.assertTrue(all(not token.provider_stable for token in events[1].caption_observation.tokens))
        self.assertEqual(events[2].type, "final")
        self.assertEqual(events[2].text, "será el inglés muchos")
        self.assertEqual(events[2].begin_pcm, 0.55)
        self.assertEqual(events[2].end_pcm, 2.05)
        self.assertEqual(events[2].language, "es")
        self.assertEqual(events[2].caption_observation.kind, "stable_token_delta")
        self.assertTrue(all(token.provider_stable for token in events[2].caption_observation.tokens))
        self.assertEqual(events[3].type, "speech_stopped")
        self.assertEqual(events[3].end_pcm, 2.05)
        self.assertEqual(events[3].caption_observation.kind, "endpoint")
        await stream.aclose()

    async def test_is_final_without_speech_final_keeps_utterance_open_for_later_results(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("en"), sample_rate=16000,
            hotwords=[], context=[],
        )
        await self.server.send(SPEECH_STARTED)
        await self.server.send(results_message(
            is_final=True, speech_final=False, transcript="one two",
            words=[
                {"word": "one", "start": 0.5, "end": 0.9, "confidence": 0.9},
                {"word": "two", "start": 0.9, "end": 1.3, "confidence": 0.9},
            ],
        ))
        await self.server.send(results_message(
            is_final=True, speech_final=True, transcript="three four",
            words=[
                {"word": "three", "start": 1.4, "end": 1.8, "confidence": 0.9},
                {"word": "four", "start": 1.8, "end": 2.2, "confidence": 0.9},
            ],
        ))
        iterator = stream.__aiter__()
        events = [await asyncio.wait_for(iterator.__anext__(), 2) for _ in range(4)]
        self.assertEqual([event.type for event in events], ["speech_started", "final", "final", "speech_stopped"])
        self.assertEqual(events[1].caption_observation.kind, "stable_token_delta")
        self.assertEqual(events[2].caption_observation.kind, "stable_token_delta")
        self.assertEqual(events[3].caption_observation.kind, "endpoint")
        self.assertEqual(events[1].item_id, events[2].item_id)
        await stream.aclose()

    async def test_detect_policy_maps_to_language_multi_and_code_switching(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.detect(candidates=("es", "en")),
            sample_rate=16000,
            hotwords=[],
            context=[],
        )
        self.assertEqual(self.server.handshakes[0]["query"]["language"], "multi")
        await stream.aclose()

    async def test_detect_candidate_outside_multi_set_rejected(self) -> None:
        provider = self.provider()
        caps = provider.capabilities.language
        with self.assertRaises(LanguageNotSupportedError):
            validate_source_policy(SourceLanguagePolicy.detect(candidates=("ar",)), caps)

    async def test_finalize_and_closestream(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=16000,
            hotwords=[],
            context=[],
        )
        await stream.flush()
        await self.server.wait_for(lambda: bool(self.server.controls_of_type("Finalize")))
        await stream.aclose()
        await self.server.wait_for(lambda: bool(self.server.controls_of_type("CloseStream")))

    async def test_keepalive_sent_when_idle(self) -> None:
        stream = await self.provider(keepAliveSeconds=0.05).stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=16000,
            hotwords=[],
            context=[],
        )
        await asyncio.sleep(0.15)
        await self.server.wait_for(lambda: bool(self.server.controls_of_type("KeepAlive")))
        await stream.aclose()

    async def test_missing_key_raises_before_handshake(self) -> None:
        provider = create_asr({
            "id": "dg-nokey",
            "kind": "deepgram-streaming",
            "model": "nova-3",
            "baseUrl": self.server.url,
            "apiKey": "",
        })
        with self.assertRaises(ValueError):
            await provider.stream(
                policy=SourceLanguagePolicy.specified("ja"),
                sample_rate=16000,
                hotwords=[],
                context=[],
            )

    async def test_aclose_is_idempotent_and_releases_resources(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=16000,
            hotwords=[],
            context=[],
        )
        await stream.aclose()
        await stream.aclose()
        await self.server.wait_for(lambda: self.server.closed_connections >= 1)

    async def test_server_close_ends_iteration_and_aclose_still_releases(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=16000,
            hotwords=[],
            context=[],
        )
        await self.server.send("CLOSE")
        events = [event async for event in stream]
        self.assertEqual(events, [])
        await stream.aclose()
        await self.server.wait_for(lambda: self.server.closed_connections >= 1)

    async def test_push_after_close_is_a_noop_not_an_error(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=16000,
            hotwords=[],
            context=[],
        )
        await stream.aclose()
        # Backpressure/late PCM after stop must never raise into the playback path.
        await stream.push_pcm(b"\x01\x00" * 1600, 1.0)

    async def test_diarize_option_sends_query_and_maps_word_speaker(self) -> None:
        stream = await self.provider(diarize=True).stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=16000,
            hotwords=[],
            context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.handshakes))
        self.assertEqual(self.server.handshakes[0]["query"].get("diarize"), "true")
        # Official diarized Results: each word carries an integer "speaker".
        words = [
            {"word": "こんにちは", "start": 0.12, "end": 0.75, "confidence": 0.98, "speaker": 0, "language": "ja"},
            {"word": "、はじめまして", "start": 0.8, "end": 1.4, "confidence": 0.97, "speaker": 0, "language": "ja"},
        ]
        await self.server.send(results_message(is_final=True, speech_final=True, words=words, transcript="こんにちは、はじめまして"))
        iterator = stream.__aiter__()
        events = [await asyncio.wait_for(iterator.__anext__(), 2) for _ in range(2)]
        final = events[0]
        self.assertEqual(final.type, "final")
        self.assertEqual(final.speaker, "0")
        await stream.aclose()

    async def test_utterance_end_ms_query_param_passthrough(self) -> None:
        stream = await self.provider(utteranceEndMs=800).stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=16000,
            hotwords=[],
            context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.handshakes))
        self.assertEqual(self.server.handshakes[0]["query"].get("utterance_end_ms"), "800")
        await stream.aclose()

        # Without the option the parameter is not sent (existing profiles keep
        # today's behavior exactly).
        stream2 = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=16000,
            hotwords=[],
            context=[],
        )
        await self.server.wait_for(lambda: len(self.server.handshakes) >= 2)
        self.assertNotIn("utterance_end_ms", self.server.handshakes[1]["query"])
        await stream2.aclose()

    async def test_diarize_defaults_off(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=16000,
            hotwords=[],
            context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.handshakes))
        self.assertNotIn("diarize", self.server.handshakes[0]["query"])
        self.assertFalse(stream and self.provider().capabilities.speaker_labels)
        await stream.aclose()

    async def test_language_canonicalized_at_adapter_edge(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.detect(candidates=("es", "en")),
            sample_rate=16000,
            hotwords=[],
            context=[],
        )
        await self.server.send(SPEECH_STARTED)
        await self.server.send(results_message(is_final=True, speech_final=True, languages=["zh-TW"]))
        iterator = stream.__aiter__()
        events = [await asyncio.wait_for(iterator.__anext__(), 2) for _ in range(3)]
        # LagLingo alias policy canonicalizes zh-TW onto the zh-Hant identity.
        self.assertEqual(events[1].language, "zh-Hant")
        await stream.aclose()


    async def test_failed_handshake_releases_client_session(self) -> None:
        """A failed ws_connect (e.g. 401) must close the ClientSession it created;
        an unclosed session would surface as a ResourceWarning, never be hidden."""
        provider = create_asr({
            "id": "dg-badkey",
            "kind": "deepgram-streaming",
            "model": "nova-3",
            "baseUrl": self.server.url_401,
            "apiKey": "bad-key",
        })
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            with self.assertRaises(aiohttp.WSServerHandshakeError):
                await provider.stream(
                    policy=SourceLanguagePolicy.specified("ja"),
                    sample_rate=16000,
                    hotwords=[],
                    context=[],
                )
            gc.collect()
        leaked = [item for item in caught if issubclass(item.category, ResourceWarning)]
        self.assertEqual([str(item.message) for item in leaked], [])


class DeepgramPipelineIsolationTests(unittest.IsolatedAsyncioTestCase):
    """SubtitlePipeline stays the only generic reconnect owner; an STT failure
    must never block or stop the playback/PCM path."""

    async def asyncSetUp(self) -> None:
        self.server = FakeDeepgramServer()
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    async def test_stt_error_reconnects_via_pipeline_and_never_stalls_pcm(self) -> None:
        provider = create_asr({
            "id": "dg-pipeline",
            "kind": "deepgram-streaming",
            "model": "nova-3",
            "baseUrl": self.server.url,
            "apiKey": "fake-key",
        })
        pipeline = SubtitlePipeline(
            asr_provider=provider,
            translation_provider=None,
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "ja", "zh-Hans"),
            source_policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=16000,
        )
        pipeline._running = True
        manager = asyncio.create_task(pipeline._asr_manager())
        try:
            await self.server.wait_for(lambda: bool(self.server.handshakes))
            # A protocol error inside the session yields an error event; the
            # pipeline records it and owns the retry, the adapter does not.
            await self.server.outbound.put("RAW:not-json")
            await self.server.wait_for(lambda: len(self.server.handshakes) >= 2, timeout=4.0)
            self.assertGreaterEqual(pipeline.stats.asr_reconnects, 1)

            # Playback path keeps advancing regardless of ASR health.
            # _enqueue_pcm_chunk is the production variant (start() creates the
            # queue); the old synchronous _ingest_pcm_chunk was a test-only
            # duplicate that silently dropped on overflow.
            pipeline._pcm_queue = asyncio.Queue(maxsize=pipeline.pcm_queue_chunks)
            await pipeline._enqueue_pcm_chunk(b"\x00\x00" * 3200, 0.0)
            await pipeline._enqueue_pcm_chunk(b"\x00\x00" * 3200, 0.2)
            self.assertAlmostEqual(pipeline._pcm_offset, 0.4)
            self.assertEqual(pipeline._pcm_queue.qsize(), 2)
        finally:
            pipeline._running = False
            manager.cancel()
            await asyncio.gather(manager, return_exceptions=True)
        await pipeline.stop()


if __name__ == "__main__":
    unittest.main()
