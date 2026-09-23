"""Fake-WebSocket tests for the Qwen3-LiveTranslate realtime adapter.

The protocol has traps this suite exists to pin:

* the model answers on the global ``dashscope.aliyuncs.com`` realtime host with
  nothing but an API key; the ``{WorkspaceId}.<region>.maas.aliyuncs.com`` host
  the docs print is an alternative that has to have its placeholder filled in,
  and leaving it literal looks exactly like a Provider that returns nothing,
* a session must name a voice it accepts. Measured live on 2026-09-21: the
  session 3.8 hands back carries voice ``Chelsie``, which the model rejects on
  its first generated turn, killing the connection before any transcript or
  translation exists -- 3.5's own default is already valid, so the same field is
  simply honoured there,
* the session 3.8 reports for turn detection waits 800 ms of silence before a
  caption can exist at all, and whether a partial ``turn_detection`` object
  merges or replaces is undocumented -- so the request restates the whole object
  with only the silence shortened,
* the 3.8 and 3.5 generations use different session field names AND different
  event names, and Alibaba documents them as not interchangeable,
* the caption closes on the transcript's ``.completed``, not on
  ``input_audio_buffer.speech_stopped``: the turn marker arrives while the
  transcript is still growing, and a cue cut there matches nothing the Provider
  aligned its translation to,
* closing without ``session.finish`` loses the final utterance's transcript and
  translation, so shutdown must send it and wait for ``session.finished``.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import aiohttp
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.languages import LanguageNotSupportedError
from companion.providers import create_asr
from companion.providers.asr_dashscope_livetranslate import _silence_duration_ms
from companion.providers.base import SourceLanguagePolicy, StreamMeta
from companion.providers.native_session import (
    NativeSessionTranslation,
    NativeTranslationBus,
)
from companion.subtitle_pipeline import SubtitlePipeline
from companion.subtitle_store import CueStore

MODEL_38 = "qwen3.8-livetranslate-flash-realtime"
MODEL_35 = "qwen3.5-livetranslate-flash-realtime"


class FakeLiveTranslateServer:
    def __init__(self) -> None:
        self.headers: list[dict[str, str]] = []
        self.paths: list[str] = []
        self.session_updates: list[dict[str, Any]] = []
        self.audio_frames = 0
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
                if message.type == aiohttp.WSMsgType.TEXT:
                    frame = json.loads(message.data)
                    if frame.get("type") == "session.update":
                        self.session_updates.append(frame)
                        await ws.send_str(json.dumps({"type": "session.updated"}))
                    elif frame.get("type") == "input_audio_buffer.append":
                        self.audio_frames += 1
                    elif frame.get("type") == "session.finish":
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


class LiveTranslateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeLiveTranslateServer()
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    def provider(self, **options):
        options.setdefault("closeDrainTimeoutSeconds", 1.0)
        return create_asr({
            "id": "livetranslate-test",
            "kind": "dashscope-livetranslate-realtime",
            "model": MODEL_38,
            "baseUrl": self.server.url,
            "apiKey": "fake-key",
            "options": options,
        })

    async def connected(
        self, provider, policy: SourceLanguagePolicy | None = None,
        *, expect_session_updates: int = 1,
    ):
        stream = await provider.stream(
            policy=policy or SourceLanguagePolicy.specified("ja"), sample_rate=16000,
            hotwords=[], context=[],
        )
        self.addAsyncCleanup(stream.aclose)
        await self.server.wait_for(
            lambda: len(self.server.session_updates) >= expect_session_updates
        )
        return stream

    async def next_event(self, iterator, kind: str):
        for _ in range(20):
            event = await asyncio.wait_for(iterator.__anext__(), 2)
            if event.type == kind:
                return event
        raise AssertionError(f"no {kind} event arrived")

    async def test_translating_is_what_this_profile_is_for(self) -> None:
        provider = self.provider()
        capabilities = provider.capabilities.native_translation
        self.assertTrue(capabilities.enabled)
        self.assertFalse(capabilities.two_way, "LiveTranslate is documented one-way only")
        self.assertIn("ja", provider.capabilities.language.supported_tags)

    async def test_an_unreplaced_workspace_placeholder_fails_before_connecting(self) -> None:
        provider = self.provider()
        provider.base_url = "wss://<workspace-id>.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "workspace"):
                await provider.stream(
                    policy=SourceLanguagePolicy.specified("ja"), sample_rate=16000,
                    hotwords=[], context=[],
                )

    async def test_the_handshake_uses_the_bearer_header_and_the_model_query(self) -> None:
        await self.connected(self.provider())
        self.assertEqual(self.server.headers[0].get("Authorization"), "Bearer fake-key")
        self.assertEqual(self.server.paths[0], f"/api-ws/v1/realtime?model={MODEL_38}")

    async def test_the_38_session_uses_the_38_field_names(self) -> None:
        provider = self.provider()
        provider.set_translation_target("zh-Hans")
        await self.connected(provider)
        session = self.server.session_updates[0]["session"]
        self.assertEqual(session["output_modalities"], ["text"])
        self.assertEqual(session["translation"]["language"], "zh")
        self.assertNotIn(
            "input_audio_transcription", session,
            "3.8 auto-detects; pinning is opt-in",
        )

    async def test_text_only_is_written_in_both_field_names_because_the_server_reads_both(self) -> None:
        """Docs call ``modalities`` a 3.5 field; the live 3.8 server disagrees.

        Sending only ``output_modalities: ["text"]`` -- the shape the 3.8 page
        prints -- leaves the echoed session at ``["text","audio"]``. The adapter
        sends both so the session is text-only whichever name a given deployment
        happens to read, and an audio profile flips both together.
        """
        provider = self.provider()
        provider.set_translation_target("zh-Hans")
        await self.connected(provider)
        text = self.server.session_updates[-1]["session"]
        self.assertEqual(text["modalities"], ["text"])
        self.assertEqual(text["output_modalities"], ["text"])
        audio = self.provider(audioOutput=True)
        audio.set_translation_target("zh-Hans")
        await self.connected(audio, expect_session_updates=2)
        self.assertEqual(
            self.server.session_updates[-1]["session"]["modalities"], ["text", "audio"],
        )

    async def test_the_session_names_a_voice_the_model_accepts(self) -> None:
        """Without this the live service closes the socket on the first turn.

        ``Voice 'Chelsie' is not supported`` arrives as an error frame about 0.5s
        into the audio and the connection is dropped -- with text-only subtitles
        nobody would expect a voice to matter, which is why the default is pinned
        rather than left to the server.
        """
        provider = self.provider()
        await self.connected(provider)
        self.assertEqual(self.server.session_updates[-1]["session"]["voice"], "Tina")
        named = self.provider(voice="Vincent")
        await self.connected(named, expect_session_updates=2)
        self.assertEqual(self.server.session_updates[-1]["session"]["voice"], "Vincent")

    async def test_turn_detection_restates_the_whole_object_the_server_echoes(self) -> None:
        """Only ``silence_duration_ms`` is ours; the rest is what the service sent.

        The live 3.8 session answers ``server_vad`` with a 6-field object whose
        ``silence_duration_ms`` is 800. Whether a partial object merges or
        replaces is undocumented, and a session that lost ``create_response``
        would stop translating, so the request carries every field back.
        """
        provider = self.provider()
        await self.connected(provider)
        self.assertEqual(
            self.server.session_updates[-1]["session"]["turn_detection"],
            {
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 300,
                "create_response": True,
                "interrupt_response": True,
            },
        )
        tuned = self.provider(silenceDurationMs=250)
        await self.connected(tuned, expect_session_updates=2)
        self.assertEqual(
            self.server.session_updates[-1]["session"]["turn_detection"]["silence_duration_ms"], 250,
        )
        # A missing or unusable value keeps the default; a number outside the
        # 200..6000 this helper allows is pulled back inside rather than sent.
        self.assertEqual(_silence_duration_ms({}), 300)
        self.assertEqual(_silence_duration_ms({"silenceDurationMs": "soon"}), 300)
        self.assertEqual(_silence_duration_ms({"silenceDurationMs": 40}), 200)
        self.assertEqual(_silence_duration_ms({"silenceDurationMs": 99999}), 6000)

    async def test_the_35_session_uses_the_35_field_names(self) -> None:
        provider = self.provider()
        provider.model = MODEL_35
        provider.set_translation_target("zh-Hans")
        await self.connected(provider)
        session = self.server.session_updates[0]["session"]
        self.assertEqual(session["modalities"], ["text"])
        self.assertEqual(session["input_audio_format"], "pcm")
        self.assertEqual(session["translation"]["language"], "zh")
        self.assertNotIn("output_modalities", session)

    async def test_the_workspace_id_is_filled_in_instead_of_demanded(self) -> None:
        """Asking the user to hand-edit a URL was why this Profile never connected.

        The host is per-workspace, so the id has to come from somewhere; the
        official sample reads DASHSCOPE_WORKSPACE_ID, and the advanced-settings
        field is the same value for people without a shell environment.
        """
        provider = self.provider(workspaceId="llm-abcdef123456")
        provider.base_url = (
            "wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"
        )
        self.assertEqual(
            provider.endpoint_url(),
            "wss://llm-abcdef123456.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime",
        )
        provider.options = {}
        with mock.patch.dict(os.environ, {"DASHSCOPE_WORKSPACE_ID": "llm-env"}):
            self.assertTrue(provider.endpoint_url().startswith("wss://llm-env."))

    async def test_a_pinned_source_language_reaches_only_the_generation_that_has_the_field(self) -> None:
        """3.5 documents ``input_audio_transcription.language``; 3.8 documents no pin."""
        provider = self.provider(sourceLanguage="ko")
        await self.connected(provider)
        session = self.server.session_updates[0]["session"]
        self.assertNotIn(
            "input_audio_transcription", session,
            "3.8 recognises the source language on its own and has no field for it",
        )
        provider.model = MODEL_35
        await self.connected(provider, expect_session_updates=2)
        self.assertEqual(
            self.server.session_updates[1]["session"]["input_audio_transcription"],
            {"model": "qwen3-asr-flash-realtime", "language": "ko"},
        )

    async def test_glossary_phrases_reach_the_documented_corpus_field(self) -> None:
        provider = self.provider(phrases={"人工智能": "Artificial Intelligence"})
        await self.connected(provider)
        self.assertEqual(
            self.server.session_updates[0]["session"]["translation"]["corpus"]["phrases"],
            {"人工智能": "Artificial Intelligence"},
        )

    async def test_transcript_deltas_are_concatenated_not_replaced(self) -> None:
        stream = await self.connected(self.provider())
        iterator = stream.__aiter__()
        await self.server.send({"type": "input_audio_buffer.speech_started", "item_id": "i1", "audio_start_ms": 0})
        await self.server.send({"type": "conversation.item.input_audio_transcription.delta", "item_id": "i1", "delta": "こんに"})
        await self.server.send({"type": "conversation.item.input_audio_transcription.delta", "item_id": "i1", "delta": "ちは"})
        await self.server.send({"type": "input_audio_buffer.speech_stopped", "item_id": "i1", "audio_end_ms": 900})
        first = await self.next_event(iterator, "interim")
        second = await self.next_event(iterator, "interim")
        self.assertEqual(first.text, "こんに")
        self.assertEqual(
            second.text, "こんにちは", "each delta extends the running text"
        )
        self.assertEqual(second.item_id, "i1")
        self.assertEqual(second.caption_observation.kind, "stable_prefix_snapshot")

    async def test_the_transcript_completion_closes_the_caption_not_the_vad_stop(self) -> None:
        """Closing at the turn marker cost every Qwen cue its translation.

        ``speech_stopped`` arrives while the transcript is still being written, so
        a caption cut there carries a prefix the Provider never aligned anything
        to, and the ledger's pair for the item is the completed transcript. The
        two strings are never equal, so the cue kept its source and lost its
        translation. The transcript's own end is the only honest boundary here.
        """
        stream = await self.connected(self.provider())
        iterator = stream.__aiter__()
        await self.server.send({"type": "input_audio_buffer.speech_started", "item_id": "i1", "audio_start_ms": 0})
        await self.server.send({"type": "conversation.item.input_audio_transcription.delta", "item_id": "i1", "delta": "こんに"})
        await self.next_event(iterator, "interim")
        await self.server.send({"type": "input_audio_buffer.speech_stopped", "item_id": "i1", "audio_end_ms": 900})
        stopped = await self.next_event(iterator, "speech_stopped")
        self.assertIsNone(
            stopped.caption_observation, "the turn marker is timing evidence only"
        )
        self.assertEqual(stopped.end_pcm, 0.9)

        await self.server.send({
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "i1", "transcript": "こんにちは",
        })
        closed = await self.next_event(iterator, "interim")
        self.assertEqual(closed.caption_observation.kind, "utterance_final")
        self.assertEqual(closed.text, "こんにちは")

        await self.server.send({"type": "response.created", "response": {"id": "r1"}})
        await self.server.send({"type": "response.text.delta", "delta": "你好"})
        update = await self.next_event(iterator, "translation")
        self.assertEqual((update.translation, update.item_id), ("你好", "i1"))

    async def test_each_growth_freezes_the_pair_the_two_streams_stood_on(self) -> None:
        """A caption cut mid-turn can only resolve against a boundary we froze.

        This protocol states no alignment points, so before there were any, the one
        caption the ledger could match was the whole closed segment: a cue the
        chunker cut at its hard deadline kept its Japanese and lost its Chinese
        (measured 0 of 5 and 1 of 6 on real captures). Every pair below is text the
        model itself sent -- the frozen set is the transcript and the translation as
        each of them stood, never a string interpolated here.
        """
        stream = await self.connected(self.provider())
        iterator = stream.__aiter__()
        await self.server.send({"type": "input_audio_buffer.speech_started", "item_id": "i1", "audio_start_ms": 0})
        await self.server.send({"type": "conversation.item.input_audio_transcription.delta", "item_id": "i1", "delta": "こんにちは"})
        before = await self.next_event(iterator, "interim")
        self.assertEqual(
            before.translation_anchors, (), "nothing has been translated yet"
        )

        await self.server.send({"type": "response.created", "response": {"id": "r1"}})
        await self.server.send({"type": "response.text.delta", "delta": "你好"})
        grown = await self.next_event(iterator, "translation")
        self.assertEqual(grown.translation_anchors, (("こんにちは", "你好"),))

        # The speaker runs on and the transcript outruns the translation. The first
        # pair survives, and the new one pairs the longer text with the shorter
        # translation: whichever text a cue is cut on, the Chinese it gets is the
        # text the model had actually reached, which can trail but never outrun.
        await self.server.send({"type": "conversation.item.input_audio_transcription.delta", "item_id": "i1", "delta": "、"})
        await self.server.send({"type": "conversation.item.input_audio_transcription.delta", "item_id": "i1", "delta": "元気"})
        later = None
        for _ in range(2):
            later = await self.next_event(iterator, "interim")
        self.assertEqual(later.text, "こんにちは、元気")
        self.assertEqual(
            later.translation_anchors,
            (("こんにちは", "你好"), ("こんにちは、", "你好"), ("こんにちは、元気", "你好")),
            "every text the transcript has stood on stays resolvable",
        )
        self.assertEqual(later.translation, "你好", "the ledger replaces, so carry the total")

        await self.server.send({"type": "response.text.delta", "delta": "，很好"})
        second = await self.next_event(iterator, "translation")
        self.assertEqual(
            second.translation_anchors,
            (
                ("こんにちは", "你好"),
                ("こんにちは、", "你好"),
                ("こんにちは、元気", "你好"),
                ("こんにちは、元気", "你好，很好"),
            ),
        )

        await self.server.send({"type": "response.text.done", "text": "你好，很好"})
        final = await self.next_event(iterator, "final")
        self.assertEqual((final.item_id, final.translation), ("i1", "你好，很好"))
        self.assertEqual(final.text, "こんにちは、元気")
        self.assertEqual(
            final.translation_anchors[-1], ("こんにちは、元気", "你好，很好"),
            "the cue the ledger closes on and the string it closes with are the same pair",
        )

    async def test_the_35_translation_event_name_is_understood(self) -> None:
        """3.5 streams its translation as ``response.text.text``, which is a running
        snapshot, not a delta -- appending it would double the sentence."""
        provider = self.provider()
        provider.model = MODEL_35
        stream = await self.connected(provider)
        iterator = stream.__aiter__()
        await self.server.send({"type": "input_audio_buffer.speech_started", "item_id": "i1", "audio_start_ms": 0})
        await self.server.send({
            "type": "conversation.item.input_audio_transcription.text",
            "item_id": "i1", "text": "こんにちは",
        })
        await self.next_event(iterator, "interim")
        await self.server.send({"type": "response.created", "response": {"id": "r1"}})
        await self.server.send({"type": "response.text.text", "text": "你"})
        await self.server.send({"type": "response.text.text", "text": "你好"})
        first = await self.next_event(iterator, "translation")
        second = await self.next_event(iterator, "translation")
        self.assertEqual((first.translation, second.translation), ("你", "你好"))

    async def test_a_turn_with_no_transcript_does_not_steal_the_next_translation(self) -> None:
        """A breath between sentences used to shift every later caption's text.

        The live stream shows why: the server opens a response for every VAD turn,
        including the turn it heard nothing in, and it links a response to its
        utterance by nothing but order. Pairing on transcript arrivals left that
        empty turn out of the queue and then queued it from its own empty
        completion, so the next real sentence's Chinese landed on the line before
        it -- source and translation one row apart, which no amount of cue-side
        matching can repair.
        """
        stream = await self.connected(self.provider())
        iterator = stream.__aiter__()
        for frame in (
            {"type": "input_audio_buffer.speech_started", "item_id": "breath", "audio_start_ms": 0},
            {"type": "response.created", "response": {"id": "r1"}},
            {"type": "input_audio_buffer.speech_stopped", "item_id": "breath", "audio_end_ms": 300},
            {"type": "conversation.item.input_audio_transcription.completed", "item_id": "breath", "transcript": ""},
            {"type": "response.text.done", "text": ""},
            {"type": "response.done", "response": {"id": "r1"}},
            {"type": "input_audio_buffer.speech_started", "item_id": "said", "audio_start_ms": 900},
            {"type": "conversation.item.input_audio_transcription.delta", "item_id": "said", "delta": "同じ動き"},
            {"type": "response.created", "response": {"id": "r2"}},
            {"type": "response.text.delta", "delta": "原来动作是一样的"},
            {"type": "conversation.item.input_audio_transcription.completed", "item_id": "said", "transcript": "同じ動き。"},
            {"type": "response.text.done", "text": "原来动作是一样的。"},
        ):
            await self.server.send(frame)
        final = await self.next_event(iterator, "final")
        self.assertEqual((final.item_id, final.text, final.translation), ("said", "同じ動き。", "原来动作是一样的。"))

    async def test_one_real_session_lands_one_bilingual_subtitle(self) -> None:
        """The claim this Profile exists for, end to end: a source line and its Chinese.

        Real Adapter, real caption chunker, real session-backed translation
        Provider; only the server is a fixture. This is what used to produce no
        subtitle at all, and it is the behaviour the docs describe but no unit
        test of the mapping alone can show.
        """
        bus = NativeTranslationBus()
        provider = self.provider(workspaceId="llm-test-workspace")
        provider.set_translation_target("zh-Hans")
        stream = await self.connected(provider)
        pipeline = SubtitlePipeline(
            asr_provider=provider,
            translation_provider=NativeSessionTranslation(
                bus,
                provider_id="livetranslate-test:native",
                label="Qwen LiveTranslate（Provider 内置翻译）",
                model=provider.model,
                target_tags=provider.native_translation.target_tags,
            ),
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "ja", "zh-Hans"),
            native_translation_bus=bus,
        )
        pipeline.media_epoch = 0.0
        pipeline._running = True
        worker = asyncio.create_task(pipeline._translation_worker())

        async def pump() -> None:
            async for event in stream:
                await pipeline._handle_asr_event(event)

        consumer = asyncio.create_task(pump())
        try:
            for frame in (
                {"type": "input_audio_buffer.speech_started", "item_id": "i1", "audio_start_ms": 0},
                {"type": "conversation.item.input_audio_transcription.delta",
                 "item_id": "i1", "delta": "こんにちは"},
                {"type": "input_audio_buffer.speech_stopped", "item_id": "i1", "audio_end_ms": 900},
                {"type": "conversation.item.input_audio_transcription.completed",
                 "item_id": "i1", "transcript": "こんにちは。"},
                {"type": "response.created", "response": {"id": "r1"}},
                {"type": "response.text.delta", "delta": "你好。"},
                {"type": "response.text.done", "text": "你好。"},
            ):
                await self.server.send(frame)
            async def settled() -> list:
                # A queue join would pass straight through here: the last frame is
                # still in flight, so there is nothing on the queue yet.
                for _ in range(250):
                    cues = [cue for cue in pipeline.store._cues if cue.state in {"done", "failed"}]
                    if cues:
                        return cues
                    await asyncio.sleep(0.02)
                return list(pipeline.store._cues)

            cues = await asyncio.wait_for(settled(), 5)
            self.assertEqual(len(cues), 1, "one Provider turn is one subtitle")
            self.assertEqual(
                (cues[0].src, cues[0].zh, cues[0].state),
                ("こんにちは。", "你好。", "done"),
            )
            self.assertEqual(
                pipeline.stats.translation_provider_failures, 0,
                "the session's own translation resolved the cue; no model was called",
            )
        finally:
            pipeline._running = False
            consumer.cancel()
            worker.cancel()
            await asyncio.gather(consumer, worker, return_exceptions=True)

    async def test_response_done_closes_the_utterance_without_a_text_done_event(self) -> None:
        """3.8's completion may arrive only as ``response.done``."""
        stream = await self.connected(self.provider())
        iterator = stream.__aiter__()
        await self.server.send({"type": "input_audio_buffer.speech_started", "item_id": "i1", "audio_start_ms": 0})
        await self.server.send({"type": "conversation.item.input_audio_transcription.delta", "item_id": "i1", "delta": "ありがとう"})
        await self.next_event(iterator, "interim")
        await self.server.send({"type": "input_audio_buffer.speech_stopped", "item_id": "i1", "audio_end_ms": 500})
        await self.next_event(iterator, "speech_stopped")
        await self.server.send({"type": "response.created", "response": {"id": "r1"}})
        await self.server.send({"type": "response.text.delta", "delta": "谢谢"})
        await self.next_event(iterator, "translation")
        await self.server.send({
            "type": "response.done",
            "response": {
                "id": "r1", "status": "completed",
                "output": [{"content": [{"type": "text", "text": "谢谢"}]}],
            },
        })
        final = await self.next_event(iterator, "final")
        self.assertEqual(final.translation, "谢谢")

    async def test_the_35_text_and_stash_shape_is_still_understood(self) -> None:
        """A profile that saved the older model id must not go silently blank."""
        stream = await self.connected(self.provider())
        iterator = stream.__aiter__()
        await self.server.send({"type": "input_audio_buffer.speech_started", "item_id": "i1", "audio_start_ms": 0})
        await self.server.send({
            "type": "conversation.item.input_audio_transcription.text",
            "item_id": "i1", "text": "こんにちは", "stash": "、", "language": "ja",
        })
        interim = await self.next_event(iterator, "interim")
        self.assertEqual(interim.text, "こんにちは")
        self.assertEqual(interim.language, "ja")

    async def test_audio_is_appended_as_base64_pcm(self) -> None:
        stream = await self.connected(self.provider())
        await stream.push_pcm(b"\x00\x01" * 160, 0.0)
        await self.server.wait_for(lambda: self.server.audio_frames == 1)
        self.assertEqual(self.server.audio_frames, 1)

    async def test_shutdown_sends_session_finish_and_waits_for_the_ack(self) -> None:
        stream = await self.connected(self.provider())
        iterator = stream.__aiter__()
        drain = asyncio.create_task(iterator.__anext__())
        await asyncio.sleep(0.05)
        await stream.aclose()
        drain.cancel()
        await asyncio.gather(drain, return_exceptions=True)
        self.assertEqual(self.server.finish_requests, 1)

    async def test_a_provider_error_becomes_an_error_event(self) -> None:
        stream = await self.connected(self.provider())
        iterator = stream.__aiter__()
        await self.server.send({
            "type": "error",
            "error": {"type": "invalid_value", "code": "invalid_value", "message": "bad session.modalities"},
        })
        error = await self.next_event(iterator, "error")
        self.assertIn("bad session.modalities", error.message)

    async def test_the_target_language_must_be_one_the_model_speaks(self) -> None:
        provider = self.provider()
        with self.assertRaises(LanguageNotSupportedError):
            provider.set_translation_target("xx")


if __name__ == "__main__":
    unittest.main()
