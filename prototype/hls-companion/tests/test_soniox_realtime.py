"""Fake-WebSocket tests for the Soniox realtime STT adapter."""
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

from companion.providers import create_asr
from companion.providers import asr_soniox_realtime as soniox_module
from companion.providers.base import SourceLanguagePolicy, validate_source_policy


class FakeSonioxServer:
    def __init__(self) -> None:
        self.start_requests: list[dict[str, Any]] = []
        self.audio = bytearray()
        self.controls: list[dict[str, Any]] = []
        self.empty_audio_frames = 0
        self.closed_connections = 0
        self._ws = None
        self.outbound: asyncio.Queue[dict[str, Any] | str] = asyncio.Queue()
        self.runner: web.AppRunner | None = None
        self.url = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/transcribe-websocket", self._handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        server = site._server
        assert server is not None
        sockets = server.sockets  # type: ignore[attr-defined]
        port = sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/transcribe-websocket"

    async def stop(self) -> None:
        assert self.runner is not None
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()  # don't make teardown wait on clients
        await self.runner.cleanup()

    async def _handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws = ws
        first = await ws.receive()
        self.start_requests.append(json.loads(first.data))

        async def writer() -> None:
            while True:
                payload = await self.outbound.get()
                if payload == "CLOSE":
                    await ws.close()
                    return
                if isinstance(payload, str) and payload.startswith("RAW:"):
                    await ws.send_str(payload[4:])
                else:
                    await ws.send_str(json.dumps(payload))

        task = asyncio.create_task(writer())
        try:
            async for message in ws:
                if message.type == aiohttp.WSMsgType.BINARY:
                    if message.data:
                        self.audio.extend(message.data)
                    else:
                        self.empty_audio_frames += 1
                elif message.type == aiohttp.WSMsgType.TEXT:
                    self.controls.append(json.loads(message.data))
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


class SonioxRealtimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeSonioxServer()
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    def provider(self, **options):
        options.setdefault("closeDrainTimeoutSeconds", 0.1)  # keep tests fast
        return create_asr({
            "id": "soniox-test",
            "kind": "soniox-realtime",
            "model": "stt-rt-v5",
            "baseUrl": self.server.url,
            "apiKey": "fake-key",
            "options": options,
        })

    async def test_numeric_tokens_control_message_is_ignored(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.detect(), sample_rate=16000,
            hotwords=[], context=[],
        )
        self.assertEqual(stream._map_event({"tokens": 0, "finished": False}), [])
        await stream.aclose()

    async def test_start_request_language_context_and_pcm(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=16000,
            hotwords=[],
            context=["LagLingo", "配信者"],
        )
        await stream.push_pcm(b"\x01\x00" * 1600, 0.0)
        await self.server.wait_for(lambda: len(self.server.audio) == 3200)
        start = self.server.start_requests[0]
        self.assertEqual(start["api_key"], "fake-key")
        self.assertEqual(start["model"], "stt-rt-v5")
        self.assertEqual(start["audio_format"], "pcm_s16le")
        self.assertEqual(start["sample_rate"], 16000)
        self.assertEqual(start["num_channels"], 1)
        self.assertEqual(start["language_hints"], ["ja"])
        self.assertTrue(start["language_hints_strict"])
        self.assertEqual(start["context"], {"terms": ["LagLingo", "配信者"]})
        await stream.aclose()

    async def test_endpoint_tuning_options_map_to_official_wire_names(self) -> None:
        stream = await self.provider(
            endpointSensitivity=0.5,
            endpointLatencyAdjustmentLevel=1,
            maxEndpointDelayMs=1000,
            maxNonFinalTokensDurationMs=5000,
        ).stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=16000,
            hotwords=[],
            context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.start_requests))
        start = self.server.start_requests[0]
        self.assertEqual(start["endpoint_sensitivity"], 0.5)
        self.assertEqual(start["endpoint_latency_adjustment_level"], 1)
        self.assertEqual(start["max_endpoint_delay_ms"], 1000)
        self.assertEqual(start["max_non_final_tokens_duration_ms"], 5000)
        await stream.aclose()

    async def test_token_stream_maps_stable_interim_and_endpoint_final(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.detect(candidates=("ja", "en"), allow_code_switching=True),
            sample_rate=16000,
            hotwords=[], context=[],
        )
        await self.server.send({"tokens": [
            {"text": "Hello ", "start_ms": 100, "end_ms": 400, "is_final": True, "language": "en"},
            {"text": "世", "start_ms": 410, "end_ms": 520, "is_final": False, "language": "ja"},
        ]})
        await self.server.send({"tokens": [
            {"text": "世界", "start_ms": 410, "end_ms": 700, "is_final": True, "language": "ja"},
            # Soniox boundary/control tokens do not represent spoken audio and
            # may carry zero timestamps. The lexical token remains authoritative.
            {"text": "<end>", "start_ms": 0, "end_ms": 0, "is_final": True},
        ]})
        iterator = stream.__aiter__()
        events = [await asyncio.wait_for(iterator.__anext__(), 2) for _ in range(4)]
        self.assertEqual((events[0].type, events[0].text), ("interim", "Hello 世"))
        observation = events[0].caption_observation
        self.assertIsNotNone(observation)
        self.assertEqual(observation.kind, "stable_token_delta")
        self.assertEqual(observation.tokens, ())
        self.assertTrue(all(item.provider_stable for item in observation.tokens))
        self.assertEqual((events[1].type, events[1].begin_pcm), ("speech_started", 0.1))
        self.assertEqual((events[2].type, events[2].end_pcm), ("speech_stopped", 0.7))
        self.assertEqual((events[3].type, events[3].text), ("final", "Hello 世界"))
        # One final token in each language is a tie; the adapter deterministically
        # keeps the first dominant token language rather than inventing a cue tag.
        self.assertEqual(events[3].language, "en")
        self.assertIsNone(events[2].caption_observation)
        self.assertEqual(events[3].caption_observation.kind, "utterance_final")
        self.assertEqual([item.text for item in events[3].caption_observation.tokens], ["Hello", "世界"])
        await stream.aclose()

    async def test_provider_clock_cannot_run_ahead_of_sent_pcm(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"), sample_rate=16000,
            hotwords=[], context=[],
        )
        # One second of authoritative PCM has been sent, while the Provider's
        # response clock claims it processed 1.4s. All token timestamps in this
        # response are therefore projected back by 400ms at the Adapter boundary.
        await stream.push_pcm(b"\x00" * 32000, 0.0)
        await self.server.send({
            "total_audio_proc_ms": 1400,
            "tokens": [
                {"text": "字幕", "start_ms": 900, "end_ms": 1300, "is_final": True, "language": "ja"},
                {"text": "<end>", "start_ms": 1400, "end_ms": 1400, "is_final": True},
            ],
        })
        iterator = stream.__aiter__()
        events = [await asyncio.wait_for(iterator.__anext__(), 2) for _ in range(3)]
        self.assertAlmostEqual(events[0].begin_pcm, 0.5)
        self.assertAlmostEqual(events[1].end_pcm, 0.9)
        self.assertAlmostEqual(events[2].end_pcm, 0.9)
        self.assertEqual(events[2].raw["total_audio_proc_ms"], 1400)
        await stream.aclose()

    def test_finalize_is_not_advertised_as_a_safe_caption_boundary(self) -> None:
        provider = self.provider()
        self.assertFalse(provider.capabilities.manual_commit)

        # Crossing the display-duration target must wait for stable lexical
        # evidence instead of forcing a protocol finalize in mid-speech.
        from companion.caption_chunker import CaptionChunker

        chunker = CaptionChunker(realtime=True)
        chunker.reset(1)
        chunker.open_item("japanese", 0.0)
        decision = chunker.advance_audio(6.1)
        self.assertTrue(decision.pending_evidence)
        self.assertEqual(decision.chunks, ())

    async def test_subword_tokens_are_normalized_to_lexical_tokens_before_chunking(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("en"), sample_rate=16000,
            hotwords=[], context=[],
        )
        await self.server.send({"tokens": [
            {"text": "Ch", "start_ms": 0, "end_ms": 120, "is_final": True, "language": "en"},
            {"text": "at", "start_ms": 120, "end_ms": 240, "is_final": True, "language": "en"},
            {"text": ",", "start_ms": 240, "end_ms": 270, "is_final": True, "language": "en"},
            {"text": " list", "start_ms": 300, "end_ms": 500, "is_final": True, "language": "en"},
            {"text": "en", "start_ms": 500, "end_ms": 650, "is_final": True, "language": "en"},
        ]})
        await self.server.send({"tokens": [
            {"text": " to", "start_ms": 680, "end_ms": 800, "is_final": True, "language": "en"},
            {"text": " m", "start_ms": 820, "end_ms": 900, "is_final": True, "language": "en"},
            {"text": "e", "start_ms": 900, "end_ms": 990, "is_final": True, "language": "en"},
            {"text": ".", "start_ms": 990, "end_ms": 1020, "is_final": True, "language": "en"},
            {"text": "<end>", "is_final": True},
        ]})
        iterator = stream.__aiter__()
        try:
            events = [await asyncio.wait_for(iterator.__anext__(), 2) for _ in range(4)]
            lexical = [
                token.text for event in events
                if event.caption_observation is not None
                for token in event.caption_observation.tokens
            ]
            self.assertEqual(lexical, ["Chat,", "listen", "to", "me."])
            final = next(event for event in events if event.type == "final")
            self.assertEqual(final.text, "Chat, listen to me.")
            stopped = next(event for event in events if event.type == "speech_stopped")
            self.assertIsNone(stopped.caption_observation)
            self.assertEqual(final.caption_observation.kind, "utterance_final")
        finally:
            await stream.aclose()

    async def test_trailing_whitespace_closes_lexical_units(self) -> None:
        tokens = [
            {"text": "Hello ", "start_ms": 0, "end_ms": 200, "language": "en"},
            {"text": "world", "start_ms": 220, "end_ms": 400, "language": "en"},
        ]
        lexical = soniox_module._lexical_tokens(tokens, True)
        self.assertEqual([token.text for token in lexical], ["Hello", "world"])
        self.assertEqual([token.text for token in soniox_module._closed_lexical_prefix(tokens)], ["Hello"])

    async def test_multiple_trailing_whitespace_boundaries_are_preserved(self) -> None:
        tokens = [
            {"text": "you ", "start_ms": 0, "end_ms": 100, "language": "en"},
            {"text": "and ", "start_ms": 120, "end_ms": 220, "language": "en"},
            {"text": "I", "start_ms": 240, "end_ms": 300, "language": "en"},
        ]
        self.assertEqual(
            [token.text for token in soniox_module._lexical_tokens(tokens, True)],
            ["you", "and", "I"],
        )
        self.assertEqual(
            [token.text for token in soniox_module._closed_lexical_prefix(tokens)],
            ["you", "and"],
        )

    async def test_subword_split_across_messages_is_held_until_lexical_boundary(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("en"), sample_rate=16000,
            hotwords=[], context=[],
        )
        await self.server.send({"tokens": [
            {"text": "Ch", "start_ms": 0, "end_ms": 120, "is_final": True, "language": "en"},
        ]})
        await self.server.send({"tokens": [
            {"text": "at", "start_ms": 120, "end_ms": 240, "is_final": True, "language": "en"},
            {"text": " next", "start_ms": 300, "end_ms": 500, "is_final": True, "language": "en"},
        ]})
        await self.server.send({"tokens": [
            {"text": "<end>", "is_final": True},
        ]})
        iterator = stream.__aiter__()
        try:
            events = [await asyncio.wait_for(iterator.__anext__(), 2) for _ in range(5)]
            interim_observations = [
                event.caption_observation for event in events
                if event.type == "interim" and event.caption_observation is not None
            ]
            self.assertEqual(interim_observations[0].tokens, ())
            self.assertEqual([token.text for token in interim_observations[1].tokens], ["Chat"])
            final = next(event for event in events if event.type == "final")
            self.assertEqual([token.text for token in final.caption_observation.tokens], ["next"])
        finally:
            await stream.aclose()

    async def test_japanese_stable_pieces_are_exposed_immediately_with_exact_times(self) -> None:
        katakana_tokens = [
            {"text": "じゃないなら、ペ", "start_ms": 0, "end_ms": 1260, "language": "ja"},
            {"text": "ンダントの秘密は誰から。", "start_ms": 1260, "end_ms": 3200, "language": "ja"},
        ]
        hiragana_tokens = [
            {"text": "ね、この辺りで終わってお", "start_ms": 0, "end_ms": 1320, "language": "ja"},
            {"text": "きましょうかねと。", "start_ms": 1320, "end_ms": 2600, "language": "ja"},
        ]

        for tokens, expected, end in (
            (katakana_tokens, "じゃないなら、ペンダントの秘密は誰から。", 3.2),
            (hiragana_tokens, "ね、この辺りで終わっておきましょうかねと。", 2.6),
        ):
            with self.subTest(expected=expected):
                lexical = soniox_module._lexical_tokens(tokens, True)
                self.assertEqual("".join(token.text for token in lexical), expected)
                self.assertTrue(all(token.is_piece for token in lexical))
                self.assertEqual(len(lexical), len(tokens))
                self.assertEqual((lexical[0].begin_pcm, lexical[-1].end_pcm), (0.0, end))
                self.assertEqual([t.text for t in soniox_module._closed_lexical_prefix(tokens[:1])], [tokens[0]["text"]])
                self.assertEqual(
                    [token.text for token in soniox_module._closed_lexical_prefix(tokens)],
                    [token["text"] for token in tokens],
                )

        for boundary_tokens in (
            [
                {"text": "このま", "start_ms": 0, "end_ms": 300, "language": "ja", "speaker": "1"},
                {"text": "行ってたら。", "start_ms": 500, "end_ms": 900, "language": "ja", "speaker": "1"},
            ],
            [
                {"text": "このま", "start_ms": 0, "end_ms": 300, "language": "ja", "speaker": "1"},
                {"text": "行ってたら。", "start_ms": 300, "end_ms": 900, "language": "ja", "speaker": "2"},
            ],
            [
                {"text": "終わり。", "start_ms": 0, "end_ms": 300, "language": "ja", "speaker": "1"},
                {"text": "次です。", "start_ms": 300, "end_ms": 900, "language": "ja", "speaker": "1"},
            ],
        ):
            with self.subTest(boundary_tokens=boundary_tokens):
                self.assertEqual(len(soniox_module._lexical_tokens(boundary_tokens, True)), 2)

        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"), sample_rate=16000,
            hotwords=[], context=[],
        )
        await self.server.send({"tokens": [{**hiragana_tokens[0], "is_final": True}]})
        await self.server.send({"tokens": [{**hiragana_tokens[1], "is_final": True}]})
        await self.server.send({"tokens": [{"text": "<end>", "is_final": True}]})
        iterator = stream.__aiter__()
        try:
            events = [await asyncio.wait_for(iterator.__anext__(), 2) for _ in range(5)]
            interim = [event for event in events if event.type == "interim"]
            self.assertEqual([t.text for t in interim[0].caption_observation.tokens], [hiragana_tokens[0]["text"]])
            self.assertEqual(
                [token.text for token in interim[1].caption_observation.tokens],
                [hiragana_tokens[1]["text"]],
            )
            final = next(event for event in events if event.type == "final")
            self.assertEqual(final.caption_observation.tokens, ())
        finally:
            await stream.aclose()

    async def test_finalize_keepalive_error_and_stop_frames(self) -> None:
        stream = await self.provider(keepAliveSeconds=0.05, closeDrainTimeoutSeconds=0.2).stream(
            policy=SourceLanguagePolicy.specified("en"), sample_rate=16000,
            hotwords=[], context=[],
        )
        await stream.flush()
        await self.server.wait_for(lambda: {"type": "finalize"} in self.server.controls)
        await self.server.wait_for(lambda: {"type": "keepalive"} in self.server.controls)
        await self.server.send({"error_code": 401, "error_type": "auth_error", "error_message": "bad key"})
        event = await asyncio.wait_for(stream.__aiter__().__anext__(), 2)
        self.assertEqual((event.type, event.message), ("error", "bad key"))
        await stream.aclose()
        await self.server.wait_for(lambda: self.server.empty_audio_frames == 1)

    async def test_speaker_diarization_config_and_token_speaker_confidence(self) -> None:
        stream = await self.provider(enableSpeakerDiarization=True).stream(
            policy=SourceLanguagePolicy.detect(candidates=("ja", "en")),
            sample_rate=16000,
            hotwords=[],
            context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.start_requests))
        self.assertTrue(self.server.start_requests[0].get("enable_speaker_diarization"))

        # Official token fields: "speaker" is a STRING label, confidence is
        # per-token 0..1 (https://github.com/soniox/soniox-python types).
        # All lexical tokens are already final in this batch, so the boundary
        # token closes the utterance without a preceding interim.
        await self.server.send({"tokens": [
            {"text": "Hello", "start_ms": 100, "end_ms": 300, "is_final": True, "speaker": "1", "confidence": 0.9, "language": "en"},
            {"text": " there", "start_ms": 310, "end_ms": 600, "is_final": True, "speaker": "1", "confidence": 0.8, "language": "en"},
            {"text": "<end>", "is_final": True},
        ]})
        iterator = stream.__aiter__()
        events = [await asyncio.wait_for(iterator.__anext__(), 2) for _ in range(3)]
        final = events[-1]
        self.assertEqual(final.type, "final")
        self.assertEqual(final.text, "Hello there")
        self.assertEqual(final.speaker, "1")
        self.assertAlmostEqual(final.confidence, 0.85)
        # Control tokens carry no confidence and must not skew the average.
        await stream.aclose()

    async def test_without_diarization_no_speaker_is_reported(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("en"), sample_rate=16000, hotwords=[], context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.start_requests))
        self.assertNotIn("enable_speaker_diarization", self.server.start_requests[0])
        await self.server.send({"tokens": [
            {"text": "Hi", "start_ms": 0, "end_ms": 200, "is_final": True, "language": "en"},
            {"text": "<end>", "is_final": True},
        ]})
        iterator = stream.__aiter__()
        events = [await asyncio.wait_for(iterator.__anext__(), 2) for _ in range(3)]
        self.assertIsNone(events[-1].speaker)
        self.assertIsNone(events[-1].confidence)
        await stream.aclose()

    async def test_graceful_eof_drains_tail_finals_before_close(self) -> None:
        """Regression: aclose() used to send the empty stop frame and close the
        WebSocket immediately, dropping the server's tail final tokens. The
        official stop sequence is: empty frame -> server flushes remaining
        finals -> server sends finished=true -> server closes."""
        stream = await self.provider(closeDrainTimeoutSeconds=2.0).stream(
            policy=SourceLanguagePolicy.specified("en"), sample_rate=16000, hotwords=[], context=[],
        )
        iterator = stream.__aiter__()
        collected: list[Any] = []

        async def consume() -> None:
            async for event in iterator:
                collected.append(event)

        consumer = asyncio.create_task(consume())
        await stream.push_pcm(b"\x01\x00" * 1600, 0.0)
        await self.server.wait_for(lambda: len(self.server.audio) == 3200)

        # Stop the stream WHILE the consumer is still iterating (pipeline stop
        # order: flush -> aclose with the event loop still consuming). Model the
        # server's tail response concurrently: aclose deliberately waits for it.
        close_task = asyncio.create_task(stream.aclose())
        await self.server.wait_for(lambda: self.server.empty_audio_frames == 1)

        # Server drains the tail: one last final token batch, then finished.
        await self.server.send({"tokens": [
            {"text": " tail", "start_ms": 700, "end_ms": 900, "is_final": True, "language": "en"},
            {"text": "<fin>", "start_ms": 0, "end_ms": 0, "is_final": True},
        ]})
        await self.server.send({"tokens": [], "finished": True})
        await asyncio.wait_for(close_task, 3)
        await asyncio.wait_for(consumer, 3)
        finals = [event for event in collected if event.type == "final"]
        self.assertEqual([event.text for event in finals], ["tail"])
        self.assertEqual(finals[-1].end_pcm, 0.9)
        # The iterator ended cleanly after finished=true.
        await self.server.wait_for(lambda: self.server.closed_connections >= 1)

    async def test_aclose_before_iteration_closes_promptly(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("en"), sample_rate=16000, hotwords=[], context=[],
        )
        await stream.aclose()
        await stream.aclose()  # idempotent
        await self.server.wait_for(lambda: self.server.closed_connections >= 1)

    async def test_aclose_is_bounded_when_server_never_finishes(self) -> None:
        stream = await self.provider(closeDrainTimeoutSeconds=0.15).stream(
            policy=SourceLanguagePolicy.specified("en"), sample_rate=16000, hotwords=[], context=[],
        )
        iterator = stream.__aiter__()
        await stream.aclose()
        await self.server.wait_for(lambda: self.server.empty_audio_frames == 1)
        # No finished=true ever arrives: the drain window must expire and the
        # client must close the connection itself.
        await self.server.wait_for(lambda: self.server.closed_connections >= 1, timeout=3.0)
        with self.assertRaises(StopAsyncIteration):
            await asyncio.wait_for(iterator.__anext__(), 2)

    async def test_capabilities_and_unknown_model(self) -> None:
        provider = self.provider()
        caps = provider.capabilities
        self.assertTrue(caps.stable_prefix)
        self.assertTrue(caps.word_timestamps)
        self.assertTrue(caps.context)
        self.assertFalse(caps.manual_commit)
        self.assertTrue(caps.language.code_switching)
        validate_source_policy(SourceLanguagePolicy.specified("zh-Hant"), caps.language)
        validate_source_policy(SourceLanguagePolicy.detect(candidates=("ja", "en"), allow_code_switching=True), caps.language)
        unknown = create_asr({
            "id": "unknown", "kind": "soniox-realtime", "model": "custom",
            "baseUrl": self.server.url, "apiKey": "k",
        })
        self.assertEqual(unknown.capabilities.language.tier, "experimental")
        self.assertEqual(unknown.capabilities.language.detection, "none")


if __name__ == "__main__":
    unittest.main()
