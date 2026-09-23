"""Session-backed translation: the Provider translates, LingerLens only resolves.

Soniox ``stt-rt-v5`` with a ``translation`` block and Qwen3-LiveTranslate both
emit source *and* translation on one WebSocket. These tests pin the contract
that makes that usable without a second translation model:

* the Adapter's segment id keeps native translation tied to its source, and the
  chunker keeps that segment as the caption unit rather than re-cutting it,
* a cue is resolved by the Provider utterance id it was built from, and only at
  a point the Provider itself aligned,
* a cue whose Provider never translated anything fails as source-only rather
  than hanging, and
* the LLM translation profile is not consulted at all.
"""
from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from test_subtitle_pipeline import FakeASR, RecordingTranslation

from companion.caption_chunker import CaptionChunker
from companion.media_anchor import MediaAnchor
from companion.providers.base import (
    ASREvent,
    CaptionObservation,
    ProviderRefusalError,
    RecognitionToken,
    StreamMeta,
    TranslationRequest,
)
from companion.providers.native_session import (
    NativeSessionTranslation,
    NativeTranslationBus,
)
from companion.subtitle_pipeline import SubtitlePipeline
from companion.subtitle_store import CueStore


def translation_provider(bus: NativeTranslationBus, **kwargs) -> NativeSessionTranslation:
    return NativeSessionTranslation(
        bus,
        provider_id=kwargs.pop("provider_id", "soniox-test:native"),
        label=kwargs.pop("label", "Soniox（Provider 内置翻译）"),
        model=kwargs.pop("model", "stt-rt-v5"),
        **kwargs,
    )


class BusTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_recorded_translation_resolves_immediately(self) -> None:
        bus = NativeTranslationBus()
        bus.record(item_id="1", source_text="こんにちは", translation="你好")
        bus.close_item("1")
        resolved = await bus.resolve(item_id="1", source_text="こんにちは", deadline_monotonic=None)
        self.assertEqual(resolved, "你好")

    async def test_an_open_segment_without_translation_yet_waits_then_resolves(self) -> None:
        """The Provider translates after it recognises, so the wait is normal."""
        bus = NativeTranslationBus()
        bus.record(item_id="1", source_text="hello", translation="")

        async def late_translation() -> None:
            await asyncio.sleep(0.05)
            bus.record(item_id="1", source_text="hello", translation="你好")
            bus.close_item("1")

        task = asyncio.create_task(late_translation())
        resolved = await bus.resolve(
            item_id="1", source_text="hello", deadline_monotonic=time.monotonic() + 2
        )
        await task
        self.assertEqual(resolved, "你好")

    async def test_a_closed_segment_without_translation_is_terminal(self) -> None:
        """A Provider that never translated must not hold a worker to the deadline."""
        bus = NativeTranslationBus()
        bus.record(item_id="1", source_text="hello", translation="")
        bus.close_item("1")
        started = time.monotonic()
        resolved = await bus.resolve(
            item_id="1", source_text="hello", deadline_monotonic=time.monotonic() + 5
        )
        self.assertIsNone(resolved)
        self.assertLess(time.monotonic() - started, 1.0)

    async def test_an_expired_deadline_stops_an_open_segment_waiting(self) -> None:
        bus = NativeTranslationBus()
        bus.record(item_id="1", source_text="hello", translation="")
        resolved = await bus.resolve(
            item_id="1", source_text="hello", deadline_monotonic=time.monotonic() - 1
        )
        self.assertIsNone(resolved)

    async def test_text_is_never_returned_twice_for_one_segment(self) -> None:
        """A second cue from the same segment must not repeat the first's text."""
        bus = NativeTranslationBus()
        bus.record(item_id="1", source_text="one two three four", translation="一二三四")
        bus.close_item("1")
        first = await bus.resolve(item_id="1", source_text="one two three four", deadline_monotonic=None)
        second = await bus.resolve(item_id="1", source_text="one two three four", deadline_monotonic=None)
        self.assertEqual(first, "一二三四")
        self.assertIsNone(second, "the segment's translation was already consumed")

    async def test_an_open_segment_does_not_guess_a_mid_cut_translation(self) -> None:
        bus = NativeTranslationBus()
        bus.record(item_id="1", source_text="one two three four", translation="一二三四五六七八")
        cut = await bus.resolve(item_id="1", source_text="one two", deadline_monotonic=time.monotonic() + 0.01)
        self.assertIsNone(cut)
        bus.close_item("1")
        rest = await bus.resolve(item_id="1", source_text="three four", deadline_monotonic=None)
        self.assertIsNone(rest)
        whole = await bus.resolve(item_id="1", source_text="one two three four", deadline_monotonic=None)
        self.assertEqual(whole, "一二三四五六七八")

    async def test_a_new_session_cannot_resolve_against_the_old_ones_items(self) -> None:
        bus = NativeTranslationBus()
        bus.record(item_id="1", source_text="old", translation="旧")
        bus.reset()
        bus.record(item_id="1", source_text="new", translation="新")
        bus.close_item("1")
        self.assertEqual(
            await bus.resolve(item_id="1", source_text="new", deadline_monotonic=None), "新"
        )

    async def test_a_cleaned_full_width_mark_does_not_cost_a_cue_its_translation(self) -> None:
        """The cue's text has been through ``clean_subtitle_text``; compare it there.

        Soniox writes Japanese with full-width ``！`` and repeats a ``。`` when it
        stutters. NFKC and the repeated-punctuation fold leave a cue whose text
        the raw Provider string no longer equals, which used to discard the whole
        segment's translation rather than mis-join it.
        """
        bus = NativeTranslationBus()
        bus.record(item_id="1", source_text="けど、買った！危ない！", translation="但是买了，好险")
        bus.close_item("1")
        self.assertEqual(
            await bus.resolve(item_id="1", source_text="けど、買った!危ない!", deadline_monotonic=None),
            "但是买了，好险",
        )

    async def test_a_provider_anchor_gives_each_half_of_a_segment_its_own_slice(self) -> None:
        """Where the Provider aligned it is the only place a cut may be answered.

        The anchor also settles early: a cue the Provider has already translated
        does not queue behind the silence marker that closes its segment.
        """
        bus = NativeTranslationBus()
        bus.record(
            item_id="1",
            source_text="one two three four",
            translation="一二三四",
            anchors=(("one two", "一二"),),
        )
        self.assertEqual(
            await bus.resolve(item_id="1", source_text="one two", deadline_monotonic=None), "一二"
        )
        bus.close_item("1")
        self.assertEqual(
            await bus.resolve(item_id="1", source_text="three four", deadline_monotonic=None), "三四"
        )

    async def test_a_cut_the_provider_never_aligned_fails_fast_once_closed(self) -> None:
        """No anchor and not the whole segment: nothing can arrive later either."""
        bus = NativeTranslationBus()
        bus.record(item_id="1", source_text="one two", translation="一二")
        bus.close_item("1")
        started = time.monotonic()
        self.assertIsNone(
            await bus.resolve(item_id="1", source_text="one", deadline_monotonic=time.monotonic() + 5)
        )
        self.assertLess(time.monotonic() - started, 1.0, "a doomed cue must not park a worker")


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_missing_provider_translation_is_a_failure_not_an_empty_cue(self) -> None:
        provider = translation_provider(NativeTranslationBus())
        request = TranslationRequest(
            "hello",
            StreamMeta(None, None, None, "en", "zh-Hans"),
            [],
            [],
            # The pipeline always allocates a budget; without one, "the Provider
            # has not translated this yet" and "it never will" are the same
            # observation and waiting is the only honest answer.
            deadline_monotonic=time.monotonic() + 0.2,
        )
        started = time.monotonic()
        with self.assertRaises(ProviderRefusalError):
            await provider.translate(request)
        self.assertLess(time.monotonic() - started, 2.0)

    async def test_the_target_contract_is_the_asr_profiles_own_target_set(self) -> None:
        provider = translation_provider(NativeTranslationBus(), target_tags=("zh", "en"))
        self.assertEqual(provider.capabilities.language.target_tags, ("zh", "en"))
        self.assertFalse(provider.capabilities.rolling_context)


class ChunkerTests(unittest.TestCase):
    def test_segments_only_does_not_cut_inside_a_provider_segment(self) -> None:
        """Punctuation inside one Provider utterance stays one caption.

        The Provider's translation belongs to the whole utterance, so a cut
        inside it would leave translation text attributable to neither half.
        """
        tokens = (
            RecognitionToken("Hello there. ", 0.0, 3.2, True, "en"),
            RecognitionToken("How are you today?", 3.2, 6.0, True, "en"),
        )

        def observation(kind: str) -> CaptionObservation:
            return CaptionObservation(
                kind, 1, "1", tokens=tokens,
                stable_text="Hello there. How are you today?",
                begin_pcm=0.0, end_pcm=6.0, language="en",
            )

        split = CaptionChunker(realtime=True).observe(observation("utterance_final")).chunks
        self.assertEqual([chunk.text for chunk in split], ["Hello there.", "How are you today?"])

        chunker = CaptionChunker(realtime=True, segments_only=True)
        self.assertEqual(chunker.observe(observation("stable_token_delta")).chunks, ())
        whole = chunker.observe(observation("endpoint")).chunks
        self.assertEqual(
            [chunk.text for chunk in whole],
            ["Hello there. How are you today?"],
            "the Provider's own segment is the caption unit",
        )

    def test_segments_only_never_hands_two_provider_segments_to_one_cue(self) -> None:
        """A speaker lane that spans two segments matches neither translation.

        The recommended Soniox bilingual profile ships ``enableSpeakerDiarization``
        on, which keys the lane by speaker label instead of by utterance, so two
        segments spoken by one person arrive as one cue.
        """
        chunker = CaptionChunker(realtime=True, segments_only=True)

        def interim(item_id: str, text: str, begin: float, end: float) -> CaptionObservation:
            return CaptionObservation(
                "stable_token_delta", 1, item_id,
                tokens=(RecognitionToken(text, begin, end, True, "ja", "1"),),
                begin_pcm=begin, end_pcm=end, language="ja", speaker="1",
            )

        chunker.observe(interim("1", "こんにちは。", 0.0, 1.5))
        emitted = chunker.observe(interim("2", "さようなら。", 1.6, 3.0)).chunks
        self.assertEqual([chunk.text for chunk in emitted], ["こんにちは。"])
        self.assertEqual([chunk.item_id for chunk in emitted], ["1"])


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def pipeline(self, bus: NativeTranslationBus):
        provider = FakeASR()
        provider.capabilities_override = None
        pipeline = SubtitlePipeline(
            asr_provider=provider,
            translation_provider=translation_provider(bus),
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "ja", "zh-Hans"),
            native_translation_bus=bus,
        )
        pipeline.media_epoch = 0.0
        pipeline._running = True
        worker = asyncio.create_task(pipeline._translation_worker())
        return pipeline, worker

    async def stop(self, pipeline, worker) -> None:
        pipeline._running = False
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    async def test_a_provider_translation_reaches_the_cue_without_a_translation_model(self) -> None:
        bus = NativeTranslationBus()
        pipeline, worker = await self.pipeline(bus)
        try:
            await pipeline._handle_asr_event(ASREvent(
                "interim",
                text="こんにちは",
                item_id="1",
                translation="你好",
                caption_observation=CaptionObservation(
                    "stable_prefix_snapshot", pipeline._generation, "1",
                    stable_text="こんにちは", begin_pcm=0.0, end_pcm=2.0, language="ja",
                ),
            ))
            await pipeline._handle_asr_event(ASREvent(
                "final", text="こんにちは", item_id="1", translation="你好",
                begin_pcm=0.0, end_pcm=2.0, language="ja",
                caption_observation=CaptionObservation(
                    "utterance_final", pipeline._generation, "1",
                    stable_text="こんにちは", begin_pcm=0.0, end_pcm=2.0, language="ja",
                ),
            ))
            await asyncio.wait_for(pipeline._translation_queue.join(), 2)
        finally:
            await self.stop(pipeline, worker)
        cues = list(pipeline.store._cues)
        self.assertEqual(len(cues), 1)
        self.assertEqual((cues[0].src, cues[0].zh, cues[0].state), ("こんにちは", "你好", "done"))
        self.assertEqual(pipeline.stats.translation_provider_failures, 0)

    async def test_a_native_segment_is_not_cut_into_cues_the_provider_cannot_answer_for(self) -> None:
        """Publishing a sentence early looked like latency and was not.

        The first sentence of a still-open segment became a cue the bus could
        only answer with the whole segment's translation, so it kept none -- and
        its remainder kept none either. The Provider's segment is the caption
        unit for these Profiles; the anchor path is what reaches the viewer
        before the silence marker instead.
        """
        bus = NativeTranslationBus()
        pipeline, worker = await self.pipeline(bus)
        pipeline._push_breadcrumbs.append((0.0, 0.0))
        pipeline._last_sent_pcm_offset = 6.4
        try:
            self.assertTrue(
                pipeline.caption_chunker.segments_only,
                "a native-translation pipeline must not re-cut the Provider's segments",
            )
            await pipeline._handle_asr_event(ASREvent(
                "interim",
                text="This sentence is complete. So is this one.",
                item_id="early",
                translation="这句话已经完整了。这一句也是。",
                caption_observation=CaptionObservation(
                    "stable_prefix_snapshot", pipeline._generation, "early",
                    stable_text="This sentence is complete. So is this one.",
                    begin_pcm=0.0, end_pcm=6.4, language="en",
                ),
            ))
            self.assertEqual(
                list(pipeline.store._cues), [], "no cue is orphaned inside an open segment",
            )
            await pipeline._handle_asr_event(ASREvent(
                "final", text="This sentence is complete. So is this one.", item_id="early",
                translation="这句话已经完整了。这一句也是。",
                caption_observation=CaptionObservation(
                    "utterance_final", pipeline._generation, "early",
                    stable_text="This sentence is complete. So is this one.",
                    begin_pcm=0.0, end_pcm=6.4, language="en",
                ),
            ))
            await asyncio.wait_for(pipeline._translation_queue.join(), 2)
            cues = list(pipeline.store._cues)
            self.assertEqual(len(cues), 1)
            self.assertEqual(
                (cues[0].state, cues[0].zh), ("done", "这句话已经完整了。这一句也是。")
            )
        finally:
            await self.stop(pipeline, worker)

    async def test_an_utterance_the_provider_never_translated_fails_source_only(self) -> None:
        bus = NativeTranslationBus()
        pipeline, worker = await self.pipeline(bus)
        try:
            await pipeline._handle_asr_event(ASREvent(
                "final", text="こんにちは", item_id="7", translation="",
                begin_pcm=0.0, end_pcm=2.0, language="ja",
                caption_observation=CaptionObservation(
                    "utterance_final", pipeline._generation, "7",
                    stable_text="こんにちは", begin_pcm=0.0, end_pcm=2.0, language="ja",
                ),
            ))
            await asyncio.wait_for(pipeline._translation_queue.join(), 2)
        finally:
            await self.stop(pipeline, worker)
        cues = list(pipeline.store._cues)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].state, "failed")
        self.assertIsNone(cues[0].zh)

    async def test_the_session_backed_provider_replaces_the_configured_llm(self) -> None:
        """The point of the feature: no second model is called."""
        bus = NativeTranslationBus()
        llm = RecordingTranslation("qwen-translate")
        pipeline = SubtitlePipeline(
            asr_provider=FakeASR(),
            translation_provider=translation_provider(bus),
            fallback_translation_provider=llm,
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "ja", "zh-Hans"),
            native_translation_bus=bus,
        )
        pipeline.media_epoch = 0.0
        pipeline._running = True
        worker = asyncio.create_task(pipeline._translation_worker())
        try:
            bus.record(item_id="3", source_text="ありがとう", translation="谢谢")
            bus.close_item("3")
            await pipeline._handle_asr_event(ASREvent(
                "final", text="ありがとう", item_id="3", translation="谢谢",
                begin_pcm=0.0, end_pcm=1.5, language="ja",
                caption_observation=CaptionObservation(
                    "utterance_final", pipeline._generation, "3",
                    stable_text="ありがとう", begin_pcm=0.0, end_pcm=1.5, language="ja",
                ),
            ))
            await asyncio.wait_for(pipeline._translation_queue.join(), 2)
        finally:
            await self.stop(pipeline, worker)
        self.assertEqual(llm.requests, [], "the LLM profile must not be consulted")
        self.assertEqual(list(pipeline.store._cues)[0].zh, "谢谢")

    async def test_the_pipeline_hands_the_target_language_to_the_asr_profile(self) -> None:
        """A translating Provider needs the target when its session opens."""
        bus = NativeTranslationBus()
        provider = FakeASR()
        pipeline = SubtitlePipeline(
            asr_provider=provider,
            translation_provider=translation_provider(bus),
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "ja", "zh-Hans"),
            native_translation_bus=bus,
        )
        pipeline.media_epoch = 0.0
        pipeline._running = True
        manager = asyncio.create_task(pipeline._asr_manager())
        try:
            for _ in range(200):
                if provider.translation_target:
                    break
                await asyncio.sleep(0.01)
        finally:
            pipeline._running = False
            pipeline._stream = None
            manager.cancel()
            await asyncio.gather(manager, return_exceptions=True)
        self.assertEqual(provider.translation_target, "zh-Hans")

    async def test_caption_draft_projects_the_held_segment_until_the_provider_closes_it(self) -> None:
        """The viewer reads what is recognized while the turn is still open.

        Nothing may reach the store from this text: a projection is not a cue, so
        it cannot be exported, listed, or answered for by the session translation.
        """
        bus = NativeTranslationBus()
        pipeline, worker = await self.pipeline(bus)
        pipeline._push_breadcrumbs.append((0.0, 0.0))
        try:
            self.assertIsNone(pipeline.caption_draft())
            await pipeline._handle_asr_event(self.held("こんにちは", end=1.5))
            draft = pipeline.caption_draft()
            self.assertIsNotNone(draft)
            self.assertEqual((draft["text"], draft["lang"], draft["itemId"]), ("こんにちは", "ja", "1"))
            self.assertEqual((draft["tStart"], draft["tEnd"]), (0.0, 1.5))
            await pipeline._handle_asr_event(self.held("こんにちは世界", end=3.0))
            self.assertEqual(pipeline.caption_draft()["text"], "こんにちは世界")
            self.assertEqual(list(pipeline.store._cues), [])

            await pipeline._handle_asr_event(ASREvent(
                "final", text="こんにちは世界", item_id="1", translation="你好世界",
                begin_pcm=0.0, end_pcm=3.0, language="ja",
                caption_observation=CaptionObservation(
                    "utterance_final", pipeline._generation, "1",
                    stable_text="こんにちは世界", begin_pcm=0.0, end_pcm=3.0, language="ja",
                ),
            ))
            await asyncio.wait_for(pipeline._translation_queue.join(), 2)
        finally:
            await self.stop(pipeline, worker)
        self.assertEqual(len(list(pipeline.store._cues)), 1)
        self.assertIsNone(
            pipeline.caption_draft(),
            "the cue replaced the projection, so drawing both would double the line",
        )

    async def test_caption_draft_stops_where_the_viewer_has_heard(self) -> None:
        """The audio leg runs ahead of the playhead, so the held text does too."""
        bus = NativeTranslationBus()
        pipeline, worker = await self.pipeline(bus)
        pipeline._push_breadcrumbs.append((0.0, 0.0))
        anchor = MediaAnchor()
        anchor.set_exact_offset(lambda: 10.0)
        pipeline.media_anchor = anchor
        pipeline.media_epoch = pipeline.wall_clock()
        epoch, offset = pipeline.media_epoch, 10.0
        try:
            await pipeline._handle_asr_event(self.held("こんにちは", end=1.5))
            await pipeline._handle_asr_event(self.held("こんにちは世界", end=4.0))
            await pipeline._handle_asr_event(self.held("こんにちは世界、さようなら", end=8.0))
            pipeline.set_viewer_wall_time(epoch + offset + 2.0)
            draft = pipeline.caption_draft()
            self.assertEqual(
                draft["text"], "こんにちは世界",
                "the last unit begins past the playhead, so it has not been heard yet",
            )
            self.assertEqual((draft["tStart"], draft["tEnd"]), (epoch + offset, epoch + offset + 4.0))
            pipeline.set_viewer_wall_time(epoch + offset + 6.0)
            self.assertEqual(pipeline.caption_draft()["text"], "こんにちは世界、さようなら")
        finally:
            await self.stop(pipeline, worker)

    async def test_caption_draft_says_nothing_while_the_playhead_is_unknown(self) -> None:
        """No playhead, no proof of what has been heard -- so nothing is shown."""
        bus = NativeTranslationBus()
        pipeline, worker = await self.pipeline(bus)
        pipeline._push_breadcrumbs.append((0.0, 0.0))
        pipeline.media_anchor = MediaAnchor()
        try:
            await pipeline._handle_asr_event(self.held("こんにちは", end=1.5))
            self.assertIsNotNone(pipeline.caption_chunker.pending_caption())
            self.assertIsNone(pipeline.caption_draft())
        finally:
            await self.stop(pipeline, worker)

    @staticmethod
    def held(text: str, *, end: float) -> ASREvent:
        """One running transcript of an open Provider turn."""
        return ASREvent(
            "interim", text=text, item_id="1",
            caption_observation=CaptionObservation(
                "stable_prefix_snapshot", 0, "1",
                stable_text=text, begin_pcm=0.0, end_pcm=end, language="ja",
            ),
        )


if __name__ == "__main__":
    unittest.main()
