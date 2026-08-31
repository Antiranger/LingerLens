from __future__ import annotations

import asyncio
import threading
import time
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.providers.base import (
    ASRCapabilities,
    ASREvent,
    ASRProvider,
    ASRStream,
    StreamMeta,
    TranslationCapabilities,
    TranslationProvider,
    TranslationRequest,
    TranslationResult,
)
import companion.subtitle_pipeline as pipeline_module
from companion.subtitle_pipeline import SubtitlePipeline, ThreadsafeTeeSink
from companion.subtitle_store import CueStore


class FakeStream(ASRStream):
    def __init__(self) -> None:
        self.commits = 0

    async def push_pcm(self, chunk: bytes, pcm_offset: float) -> None:
        del chunk, pcm_offset

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    def __aiter__(self):
        async def events():
            if False:
                yield None
        return events()

    async def aclose(self) -> None:
        return None


class FakeASR(ASRProvider):
    id = "fake-asr"
    label = "Fake ASR"
    model = "fake"
    price_per_second_cny = 0.5

    def __init__(self, manual_commit: bool = False) -> None:
        self.manual_commit = manual_commit

    @property
    def capabilities(self) -> ASRCapabilities:
        return ASRCapabilities(True, True, True, True, False, False, False, ("ja",), (16000,), self.manual_commit)

    async def stream(self, *, language: str, hotwords: list[str], context: list[str]) -> ASRStream:
        del language, hotwords, context
        return FakeStream()


class RecordingTranslation(TranslationProvider):
    def __init__(self, provider_id: str, prefix: str = "译") -> None:
        self.id = provider_id
        self.label = provider_id
        self.model = provider_id
        self.prefix = prefix
        self.requests: list[TranslationRequest] = []

    @property
    def capabilities(self) -> TranslationCapabilities:
        return TranslationCapabilities(True, True, True, False, 1000)

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        self.requests.append(request)
        return TranslationResult(self.prefix + request.source_text, self.id, 1)


class TranslationLatencyStatsTests(unittest.IsolatedAsyncioTestCase):
    async def test_records_last_and_rolling_average_latency(self) -> None:
        pipeline = SubtitlePipeline(
            asr_provider=FakeASR(),
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "ja", "zh"),
        )
        pipeline._record_translation_latency(2000)
        self.assertEqual(pipeline.stats.last_translation_latency_ms, 2000)
        self.assertEqual(pipeline.stats.avg_translation_latency_ms, 2000.0)
        pipeline._record_translation_latency(4000)
        self.assertEqual(pipeline.stats.last_translation_latency_ms, 4000)
        self.assertEqual(pipeline.stats.avg_translation_latency_ms, 2600.0)
        status = pipeline.status()
        self.assertEqual(status["lastTranslationLatencyMs"], 4000)
        self.assertEqual(status["avgTranslationLatencyMs"], 2600.0)


class TeeSinkTests(unittest.IsolatedAsyncioTestCase):
    async def test_cross_thread_sink_drops_oldest_and_never_blocks_or_raises(self) -> None:
        loop = asyncio.get_running_loop()
        sink = ThreadsafeTeeSink(loop, max_chunks=2)

        def produce() -> None:
            sink(b"one")
            sink(b"two")
            sink(b"three")

        thread = threading.Thread(target=produce)
        thread.start()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        await asyncio.sleep(0)
        self.assertEqual(sink.dropped, 1)
        self.assertEqual(sink.queue.get_nowait(), b"two")
        self.assertEqual(sink.queue.get_nowait(), b"three")
        sink.close()
        sink(b"ignored")
        self.assertEqual(sink.dropped, 2)


class PipelineFinalTests(unittest.IsolatedAsyncioTestCase):
    def make_pipeline(self, **changes):
        arguments = dict(
            asr_provider=FakeASR(),
            cue_store=CueStore(),
            meta=StreamMeta("title", "channel", "gaming", "ja", "zh"),
            pdt_epoch=lambda: 950.0,
            media_clock=lambda: None,
            wall_clock=lambda: 1000.0,
            monotonic=lambda: 50.0,
            silence_duration_ms=400,
        )
        arguments.update(changes)
        pipeline = SubtitlePipeline(**arguments)
        # Identity mapping between the ASR session clock and our pcm clock,
        # which is what a session with no dropped chunks looks like.
        pipeline._push_breadcrumbs.append((0.0, 0.0))
        return pipeline

    @staticmethod
    def prime_anchor(pipeline: SubtitlePipeline, c: float = 0.0) -> None:
        pipeline.anchor._pdt0 = 950.0
        pipeline.anchor.samples[:] = [c]

    async def test_final_takes_boundaries_from_its_own_vad_events(self) -> None:
        pipeline = self.make_pipeline()
        self.prime_anchor(pipeline)
        # The provider reports true offsets; they are NOT derived from how much
        # audio we happen to have sent, and no lag/silence compensation applies.
        pipeline._last_sent_pcm_offset = 6.0
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=5.7, item_id="a"))
        pipeline._last_sent_pcm_offset = 8.0
        await pipeline._handle_asr_event(ASREvent("speech_stopped", end_pcm=7.6, item_id="a"))
        cues = await pipeline._handle_final(ASREvent("final", text=" ＡＢ。ＣＤＥ ", language="ja", item_id="a"))
        self.assertEqual([cue.src for cue in cues], ["AB。CDE"])
        self.assertEqual(cues[0].timing_source, "asr")
        self.assertAlmostEqual(cues[0].t_start, 955.7)
        self.assertAlmostEqual(cues[0].t_end, 957.6)

    async def test_interleaved_utterances_do_not_steal_each_others_boundaries(self) -> None:
        """The next utterance opens before the previous final is handled."""
        pipeline = self.make_pipeline()
        self.prime_anchor(pipeline)
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=1.0, item_id="a"))
        await pipeline._handle_asr_event(ASREvent("speech_stopped", end_pcm=4.0, item_id="a"))
        # Same-millisecond arrival, as observed against the live provider.
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=4.0, item_id="b"))
        first = await pipeline._handle_final(ASREvent("final", text="さいしょ", item_id="a"))
        await pipeline._handle_asr_event(ASREvent("speech_stopped", end_pcm=9.0, item_id="b"))
        second = await pipeline._handle_final(ASREvent("final", text="つぎです", item_id="b"))
        self.assertEqual((first[0].t_start, first[0].t_end), (951.0, 954.0))
        self.assertEqual((second[0].t_start, second[0].t_end), (954.0, 959.0))
        self.assertEqual(pipeline.status()["timingSourceCounts"], {"asr": 2, "vad": 0, "approx": 0})

    async def test_repeated_short_final_is_only_deduped_when_adjacent(self) -> None:
        pipeline = self.make_pipeline()
        self.prime_anchor(pipeline)
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=1.0, item_id="a"))
        await pipeline._handle_asr_event(ASREvent("speech_stopped", end_pcm=2.0, item_id="a"))
        pipeline._last_sent_pcm_offset = 2.0
        await pipeline._handle_final(ASREvent("final", text="うん。", item_id="a"))

        # An immediate resend of the same text is a duplicate.
        pipeline._last_sent_pcm_offset = 2.5
        self.assertEqual(await pipeline._handle_final(ASREvent("final", text="うん。", item_id="b")), [])
        self.assertEqual(pipeline.stats.final_deduplicated, 1)

        # The speaker genuinely saying it again later is not.
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=20.0, item_id="c"))
        await pipeline._handle_asr_event(ASREvent("speech_stopped", end_pcm=21.0, item_id="c"))
        pipeline._last_sent_pcm_offset = 21.0
        again = await pipeline._handle_final(ASREvent("final", text="うん。", item_id="c"))
        self.assertEqual([cue.src for cue in again], ["うん。"])

    async def test_final_without_vad_span_falls_back_to_approx(self) -> None:
        pipeline = self.make_pipeline()
        self.prime_anchor(pipeline)
        pipeline._last_sent_pcm_offset = 3.0
        cues = await pipeline._handle_final(ASREvent("final", text="孤立", item_id="zzz"))
        self.assertEqual(cues[0].timing_source, "approx")
        self.assertIsNone(cues[0].t_start)
        self.assertEqual(pipeline.stats.unjoined_finals, 1)

    async def test_server_offsets_survive_dropped_pcm(self) -> None:
        """A dropped chunk desynchronises the two clocks; breadcrumbs fix it."""
        pipeline = self.make_pipeline()
        pipeline._push_breadcrumbs.clear()
        # Pushed 2s of audio that came from pcm offsets 0..2, then a 5s gap in
        # our own timeline was dropped before pushing resumed.
        pipeline._push_breadcrumbs.append((0.0, 0.0))
        pipeline._push_breadcrumbs.append((2.0, 7.0))
        self.assertAlmostEqual(pipeline._server_to_pipeline(1.0), 1.0)
        self.assertAlmostEqual(pipeline._server_to_pipeline(2.5), 7.5)
        self.assertIsNone(pipeline._server_to_pipeline(None))

    async def test_parked_final_flushes_when_anchor_becomes_ready(self) -> None:
        pipeline = self.make_pipeline(pdt_epoch=lambda: None)
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=1.0, item_id="a"))
        await pipeline._handle_asr_event(ASREvent("speech_stopped", end_pcm=2.0, item_id="a"))
        pipeline._last_sent_pcm_offset = 2.0
        cues = await pipeline._handle_final(ASREvent("final", text="こんにちは", item_id="a"))
        self.assertEqual(cues, [])
        self.assertEqual(len(pipeline._pending_finals), 1)
        self.assertEqual(len(pipeline.store), 0)

        pipeline.anchor._pdt0 = 950.0
        pipeline.anchor.samples[:] = [-0.25]
        flushed = pipeline._flush_pending_finals()
        self.assertEqual([cue.src for cue in flushed], ["こんにちは"])
        self.assertAlmostEqual(flushed[0].t_end, 951.75)
        self.assertEqual(len(pipeline._pending_finals), 0)

    async def test_ingest_error_suppresses_final(self) -> None:
        pipeline = self.make_pipeline(ingest_status=lambda: {"sourceError": "expired", "log_tail": []})
        self.prime_anchor(pipeline)
        pipeline._last_sent_pcm_offset = 2.0
        cues = await pipeline._handle_final(ASREvent("final", text="こんにちは"))
        self.assertEqual(cues, [])
        self.assertEqual(len(pipeline.store), 0)
        self.assertEqual(pipeline.stats.suppressed_by_ingest_error, 1)

    async def test_backlog_drops_oldest_as_terminal_source_only_and_uses_fallback(self) -> None:
        primary = RecordingTranslation("primary", "主")
        fallback = RecordingTranslation("fallback", "备")
        pipeline = self.make_pipeline(translation_provider=primary, fallback_translation_provider=fallback)
        for index in range(3):
            pipeline.context.add(f"old{index}", f"译{index}", 1000.0)
        limit = pipeline._queue_limit
        overflow = 2
        cues = []
        for index in range(limit + overflow):
            cue = pipeline.store.add(
                t_start=None,
                t_end=1000 + index,
                hold=1.2,
                src=f"句子{index}",
                lang="ja",
                timing_source="approx",
            )
            cues.append(cue)
            pipeline._enqueue_translation(cue)
        self.assertEqual(pipeline._translation_queue.qsize(), limit)
        self.assertEqual(pipeline.stats.translation_dropped, overflow)
        self.assertEqual(pipeline.store.get(cues[0].id).state, "failed")
        self.assertEqual(pipeline.store.get(cues[1].id).state, "failed")
        self.assertEqual(pipeline._degrade_level, 2)
        self.assertEqual(
            [pipeline._translation_queue.get_nowait().src for _ in range(limit)],
            [f"句子{i}" for i in range(overflow, limit + overflow)],
        )
        # Dropped items must still settle the queue's unfinished-task counter.
        for _ in range(limit):
            pipeline._translation_queue.task_done()
        await asyncio.wait_for(pipeline._translation_queue.join(), 1)

        pipeline._translation_queue = asyncio.Queue()
        pipeline._enqueue_translation(cues[-1])
        pipeline._audio_end_walls[cues[-1].id] = 999.0
        pipeline._degrade_level = 2
        pipeline._running = True
        worker = asyncio.create_task(pipeline._translation_worker())
        await asyncio.wait_for(pipeline._translation_queue.join(), 1)
        pipeline._running = False
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        self.assertEqual(len(primary.requests), 0)
        self.assertEqual(len(fallback.requests), 1)
        self.assertEqual(len(fallback.requests[0].history), 2)
        self.assertEqual(pipeline.store.get(cues[-1].id).state, "done")
        self.assertTrue(pipeline.store.get(cues[-1].id).zh.startswith("备"))
        self.assertEqual(pipeline.status()["readyLagP95"], 1.0)

    async def test_manual_commit_hard_caps_active_utterance_when_enabled(self) -> None:
        pipeline = self.make_pipeline(asr_provider=FakeASR(manual_commit=True), max_utterance_seconds=6.0)
        stream = FakeStream()
        pipeline._span_for("a")["start"] = 1.0
        pipeline._last_sent_pcm_offset = 7.1
        await pipeline._maybe_force_commit(stream)
        self.assertEqual(stream.commits, 1)
        self.assertEqual(pipeline.stats.forced_commits, 1)

    async def test_manual_commit_is_off_by_default(self) -> None:
        pipeline = self.make_pipeline(asr_provider=FakeASR(manual_commit=True))
        stream = FakeStream()
        pipeline._span_for("a")["start"] = 1.0
        pipeline._last_sent_pcm_offset = 60.0
        await pipeline._maybe_force_commit(stream)
        self.assertEqual(stream.commits, 0)

    async def test_manual_commit_ignores_already_closed_utterances(self) -> None:
        pipeline = self.make_pipeline(asr_provider=FakeASR(manual_commit=True), max_utterance_seconds=6.0)
        stream = FakeStream()
        span = pipeline._span_for("a")
        span["start"], span["end"] = 1.0, 2.0
        pipeline._last_sent_pcm_offset = 60.0
        await pipeline._maybe_force_commit(stream)
        self.assertEqual(stream.commits, 0)

    async def test_context_degradation_recovers_after_empty_stability(self) -> None:
        now = [10.0]
        pipeline = self.make_pipeline(monotonic=lambda: now[0], recovery_seconds=30)
        pipeline._degrade_level = 2
        pipeline._update_recovery(0)
        now[0] = 39.9
        pipeline._update_recovery(0)
        self.assertEqual(pipeline._degrade_level, 2)
        now[0] = 40.0
        pipeline._update_recovery(0)
        self.assertEqual(pipeline._degrade_level, 0)


class PrefixSplitTests(unittest.IsolatedAsyncioTestCase):
    """Long-utterance prefix splitting (redesign Fix F P2 / RC-4).

    Event sequences mirror the captured real-time stream: speech_started,
    stable-prefix interims growing monotonically, speech_stopped, final.
    """

    def make_pipeline(self, **changes):
        arguments = dict(
            asr_provider=FakeASR(),
            cue_store=CueStore(),
            meta=StreamMeta("title", "channel", "gaming", "ja", "zh"),
            pdt_epoch=lambda: 950.0,
            media_clock=lambda: None,
            wall_clock=lambda: 1000.0,
            monotonic=lambda: 50.0,
            silence_duration_ms=400,
        )
        arguments.update(changes)
        pipeline = SubtitlePipeline(**arguments)
        pipeline._push_breadcrumbs.append((0.0, 0.0))
        pipeline.anchor._pdt0 = 950.0
        pipeline.anchor.samples[:] = [0.0]
        return pipeline

    @staticmethod
    def interim(pipeline, item, text, at):
        pipeline._last_sent_pcm_offset = at
        pipeline._pcm_offset = at
        return pipeline._handle_asr_event(ASREvent("interim", text=text, item_id=item))

    async def test_long_utterance_emits_chained_prefix_cues_then_tail(self) -> None:
        pipeline = self.make_pipeline()
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=40.0, item_id="a"))
        # First confirmation arrives 6.2s into the utterance (past the 3s cap).
        await self.interim(pipeline, "a", "待って、何よ。マジは", 46.2)
        await self.interim(pipeline, "a", "待って、何よ。マジは当たり前のようにね、完璧。", 52.8)
        await pipeline._handle_asr_event(ASREvent("speech_stopped", end_pcm=58.5, item_id="a"))
        pipeline._last_sent_pcm_offset = 59.0
        await pipeline._handle_final(ASREvent(
            "final", text="待って、何よ。マジは当たり前のようにね、完璧。一旦完璧。", item_id="a"))

        cues = pipeline.store.query(after_seq=0)
        self.assertEqual([c.src for c in cues], ["待って、何よ。", "マジは当たり前のようにね、完璧。", "一旦完璧。"])
        # Cue 1 anchors at the exact VAD onset; ends chain forward; the tail
        # closes at the exact speech_stopped endpoint.
        self.assertAlmostEqual(cues[0].t_start, 950.0 + 40.0)
        self.assertAlmostEqual(cues[0].t_end, 950.0 + 46.2)
        self.assertAlmostEqual(cues[1].t_start, 950.0 + 46.2)
        self.assertAlmostEqual(cues[1].t_end, 950.0 + 52.8)
        self.assertEqual(cues[0].timing_source, "vad")
        self.assertAlmostEqual(cues[2].t_start, 950.0 + 52.8)
        self.assertAlmostEqual(cues[2].t_end, 950.0 + 58.5)
        self.assertEqual(cues[2].timing_source, "asr")
        self.assertEqual(pipeline.stats.prefix_cues, 2)
        self.assertEqual(pipeline.stats.final_tails, 1)
        self.assertEqual(pipeline.status()["prefixCues"], 2)

    async def test_short_utterance_keeps_final_only_behaviour(self) -> None:
        pipeline = self.make_pipeline()
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=1.0, item_id="a"))
        # Confirmation 1.0s in: below the 3s cap, so nothing is emitted early.
        await self.interim(pipeline, "a", "はい。", 2.0)
        self.assertEqual(pipeline.store.query(after_seq=0), [])
        await pipeline._handle_asr_event(ASREvent("speech_stopped", end_pcm=2.0, item_id="a"))
        pipeline._last_sent_pcm_offset = 2.3
        cues = await pipeline._handle_final(ASREvent("final", text="はい。", item_id="a"))
        self.assertEqual([c.src for c in cues], ["はい。"])
        self.assertEqual(pipeline.stats.prefix_cues, 0)

    async def test_gated_sentences_flush_together_once_cap_is_passed(self) -> None:
        pipeline = self.make_pipeline()
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=10.0, item_id="a"))
        await self.interim(pipeline, "a", "一つ目。二つ目。", 12.0)   # gated: age 2.0
        self.assertEqual(pipeline.store.query(after_seq=0), [])
        await self.interim(pipeline, "a", "一つ目。二つ目。三つ目の途中", 14.5)  # age 4.5
        cues = pipeline.store.query(after_seq=0)
        self.assertEqual([c.src for c in cues], ["一つ目。二つ目。"])
        self.assertAlmostEqual(cues[0].t_start, 950.0 + 10.0)
        self.assertAlmostEqual(cues[0].t_end, 950.0 + 14.5)

    async def test_final_matching_emitted_prefix_is_absorbed(self) -> None:
        pipeline = self.make_pipeline()
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=0.0, item_id="a"))
        await self.interim(pipeline, "a", "はい。そうです。", 5.0)
        await pipeline._handle_asr_event(ASREvent("speech_stopped", end_pcm=6.0, item_id="a"))
        pipeline._last_sent_pcm_offset = 6.4
        cues = await pipeline._handle_final(ASREvent("final", text="はい。そうです。", item_id="a"))
        self.assertEqual(cues, [])
        self.assertEqual([c.src for c in pipeline.store.query(after_seq=0)], ["はい。そうです。"])
        self.assertEqual(pipeline.stats.final_absorbed, 1)

    async def test_conflicting_final_emits_whole_transcript(self) -> None:
        pipeline = self.make_pipeline()
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=0.0, item_id="a"))
        await self.interim(pipeline, "a", "前半。", 5.0)
        await pipeline._handle_asr_event(ASREvent("speech_stopped", end_pcm=6.0, item_id="a"))
        pipeline._last_sent_pcm_offset = 6.4
        cues = await pipeline._handle_final(ASREvent("final", text="全然違う文になりました。", item_id="a"))
        self.assertEqual([c.src for c in cues], ["全然違う文になりました。"])
        self.assertEqual(pipeline.stats.split_conflicts, 1)

    async def test_interim_without_speech_start_is_ignored(self) -> None:
        pipeline = self.make_pipeline()
        await self.interim(pipeline, "orphan", "対応関係のない interim。", 5.0)
        self.assertEqual(pipeline.store.query(after_seq=0), [])
        pipeline._last_sent_pcm_offset = 5.5
        cues = await pipeline._handle_final(ASREvent("final", text="孤立", item_id="orphan"))
        self.assertEqual(cues[0].timing_source, "approx")

    async def test_prefix_rewrite_stops_splitting_that_utterance(self) -> None:
        pipeline = self.make_pipeline()
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=0.0, item_id="a"))
        await self.interim(pipeline, "a", "書き換え前。", 5.0)
        # The "stable" prefix no longer contains what we already emitted.
        await self.interim(pipeline, "a", "まるで別の話", 6.0)
        self.assertEqual(pipeline.stats.prefix_rewrites, 1)
        self.assertEqual(len(pipeline.store.query(after_seq=0)), 1)
        await pipeline._handle_asr_event(ASREvent("speech_stopped", end_pcm=7.0, item_id="a"))
        pipeline._last_sent_pcm_offset = 7.4
        cues = await pipeline._handle_final(ASREvent("final", text="まるで別の話になりました。", item_id="a"))
        self.assertEqual([c.src for c in cues], ["まるで別の話になりました。"])
        self.assertEqual(pipeline.stats.split_conflicts, 1)

    async def test_disabled_prefix_split_keeps_old_behaviour(self) -> None:
        pipeline = self.make_pipeline(prefix_split_enabled=False)
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=0.0, item_id="a"))
        await self.interim(pipeline, "a", "長い話の途中。まだ続く。", 8.0)
        self.assertEqual(pipeline.store.query(after_seq=0), [])



if __name__ == "__main__":
    unittest.main()
