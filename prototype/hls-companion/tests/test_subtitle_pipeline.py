from __future__ import annotations

import asyncio
import time
import unittest
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.providers.base import (
    ASRCapabilities,
    ASREvent,
    ASRProvider,
    CaptionObservation,
    RecognitionToken,
    ASRStream,
    SourceLanguagePolicy,
    StreamMeta,
    TranslationCapabilities,
    TranslationProvider,
    TranslationRequest,
    TranslationResult,
)
import companion.subtitle_pipeline as pipeline_module
from companion.subtitle_pipeline import SubtitlePipeline
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

    def __init__(self, manual_commit: bool = False, price_per_second_cny: float | None = 0.5) -> None:
        self.manual_commit = manual_commit
        self.price_per_second_cny = price_per_second_cny

    @property
    def capabilities(self) -> ASRCapabilities:
        return ASRCapabilities(True, True, True, True, False, False, False, ("ja",), (16000,), self.manual_commit)

    async def stream(self, *, policy: SourceLanguagePolicy, sample_rate: int, hotwords: list[str], context: list[str]) -> ASRStream:
        del policy, sample_rate, hotwords, context
        return FakeStream()


class RecordingTranslation(TranslationProvider):
    def __init__(
        self,
        provider_id: str,
        prefix: str = "译",
        *,
        usage: dict | None = None,
        result_provider_id: str | None = None,
    ) -> None:
        self.id = provider_id
        self.label = provider_id
        self.model = provider_id
        self.prefix = prefix
        self.usage = usage
        self.result_provider_id = result_provider_id or provider_id
        self.requests: list[TranslationRequest] = []

    @property
    def capabilities(self) -> TranslationCapabilities:
        return TranslationCapabilities(True, True, True, False, 1000)

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        self.requests.append(request)
        return TranslationResult(
            self.prefix + request.source_text,
            self.result_provider_id,
            1,
            self.usage,
        )


class TranslationMeteringTests(unittest.IsolatedAsyncioTestCase):
    def test_status_exposes_profile_labels_not_only_internal_ids(self) -> None:
        asr = FakeASR()
        asr.id = "asr-1"
        asr.label = "Sonics"
        translation = RecordingTranslation("translation-1")
        translation.label = "我的翻译模型"
        pipeline = SubtitlePipeline(
            asr_provider=asr,
            translation_provider=translation,
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "ja", "zh-Hans"),
        )

        status = pipeline.status()
        self.assertEqual(status["asrProviderId"], "asr-1")
        self.assertEqual(status["asrProviderLabel"], "Sonics")
        self.assertEqual(status["translationProviderLabel"], "我的翻译模型")

    async def test_cached_prompt_usage_uses_independent_literal_rates(self) -> None:
        provider = RecordingTranslation(
            "primary",
            usage={
                "prompt_tokens": 1000,
                "completion_tokens": 200,
                "total_tokens": 1200,
                "prompt_tokens_details": {"cached_tokens": 400},
                "diagnostic_field": "preserved",
            },
        )
        pipeline = SubtitlePipeline(
            asr_provider=FakeASR(),
            translation_provider=provider,
            translation_pricing_by_provider={
                "primary": {
                    "input": 2.0,
                    "cachedInput": 0.5,
                    "output": 6.0,
                }
            },
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "ja", "zh"),
        )
        cue = pipeline.store.add(
            t_start=0.0,
            t_end=1.0,
            hold=1.2,
            src="meter me",
            lang="ja",
            timing_source="asr",
        )
        pipeline._audio_end_walls[cue.id] = pipeline.wall_clock()
        pipeline._enqueue_translation(cue)
        pipeline._running = True
        worker = asyncio.create_task(pipeline._translation_worker())
        await asyncio.wait_for(pipeline._translation_queue.join(), 1)
        pipeline._running = False
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

        status = pipeline.status()
        self.assertEqual(status["translationUsage"]["calls"], 1)
        self.assertEqual(status["translationUsage"]["nonCachedInputTokens"], 600)
        self.assertEqual(status["translationUsage"]["cachedInputTokens"], 400)
        self.assertEqual(status["translationUsage"]["outputTokens"], 200)
        self.assertEqual(status["translationUsage"]["totalTokens"], 1200)
        self.assertEqual(
            status["translationUsage"]["byProvider"]["primary"]["rawUsage"]["diagnostic_field"],
            "preserved",
        )
        # Worked independently: 600×2/1m + 400×0.5/1m + 200×6/1m = 0.0026 CNY.
        self.assertEqual(status["translationEstimatedCostCny"], 0.0026)
        self.assertIsNone(status["translationEstimateReason"])
        pipeline._pcm_offset = 2.0
        status = pipeline.status()
        # ASR: 2s × 0.5 = 1 CNY; translation: 0.0026 CNY.
        self.assertEqual(status["totalEstimatedCostCny"], 1.0026)
        self.assertIsNone(status["totalEstimateReason"])

    async def test_accumulates_compatible_usage_names_and_explicit_zero_rates(self) -> None:
        provider = RecordingTranslation(
            "primary",
            usage={"input_tokens": 25, "output_tokens": 10, "extra": {"trace": "kept"}},
        )
        pipeline = SubtitlePipeline(
            asr_provider=FakeASR(),
            translation_provider=provider,
            translation_pricing_by_provider={
                "primary": {"input": 0, "cachedInput": 0, "output": 0}
            },
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "ja", "zh"),
        )
        pipeline._record_translation_usage(provider.id, provider.usage)
        pipeline._record_translation_usage(provider.id, provider.usage)
        status = pipeline.status()
        self.assertEqual(status["translationUsage"]["calls"], 2)
        self.assertEqual(status["translationUsage"]["nonCachedInputTokens"], 50)
        self.assertEqual(status["translationUsage"]["cachedInputTokens"], 0)
        self.assertEqual(status["translationUsage"]["outputTokens"], 20)
        self.assertEqual(status["translationUsage"]["totalTokens"], 70)
        self.assertEqual(status["translationEstimatedCostCny"], 0)
        self.assertIsNone(status["translationEstimateReason"])

    async def test_missing_usage_or_required_price_is_unavailable_not_zero(self) -> None:
        missing_usage = SubtitlePipeline(
            asr_provider=FakeASR(),
            translation_pricing_by_provider={
                "primary": {"input": 1, "cachedInput": 1, "output": 1}
            },
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "ja", "zh"),
        )
        missing_usage._record_translation_usage("primary", None)
        status = missing_usage.status()
        self.assertEqual(status["translationUsage"]["calls"], 1)
        self.assertEqual(status["translationUsage"]["unknownUsageCalls"], 1)
        self.assertEqual(status["translationUsage"]["byProvider"]["primary"]["calls"], 1)
        self.assertIsNone(status["translationEstimatedCostCny"])
        self.assertIn("usage unavailable", status["translationEstimateReason"])

        missing_price = SubtitlePipeline(
            asr_provider=FakeASR(),
            translation_pricing_by_provider={
                "primary": {"input": 1, "cachedInput": None, "output": 3}
            },
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "ja", "zh"),
        )
        missing_price._record_translation_usage(
            "primary",
            {"prompt_tokens": 10, "completion_tokens": 5},
        )
        status = missing_price.status()
        self.assertIsNone(status["translationEstimatedCostCny"])
        self.assertIn("pricing incomplete", status["translationEstimateReason"])

    async def test_fallback_chain_usage_is_attributed_to_actual_result_provider(self) -> None:
        provider = RecordingTranslation(
            "fallback-chain",
            usage={"prompt_tokens": 100, "completion_tokens": 20},
            result_provider_id="actual-fallback",
        )
        pipeline = SubtitlePipeline(
            asr_provider=FakeASR(),
            translation_provider=provider,
            translation_pricing_by_provider={
                "fallback-chain": {"input": 99, "cachedInput": 99, "output": 99},
                "actual-fallback": {"input": 2, "cachedInput": 1, "output": 5},
            },
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "ja", "zh"),
        )
        cue = pipeline.store.add(
            t_start=0.0,
            t_end=1.0,
            hold=1.2,
            src="fallback",
            lang="ja",
            timing_source="asr",
        )
        pipeline._audio_end_walls[cue.id] = pipeline.wall_clock()
        pipeline._enqueue_translation(cue)
        pipeline._running = True
        worker = asyncio.create_task(pipeline._translation_worker())
        await asyncio.wait_for(pipeline._translation_queue.join(), 1)
        pipeline._running = False
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        status = pipeline.status()
        self.assertEqual(list(status["translationUsage"]["byProvider"]), ["actual-fallback"])
        # 100×2/1m + 20×5/1m = 0.0003 CNY.
        self.assertEqual(status["translationEstimatedCostCny"], 0.0003)


class AsrMeteringTests(unittest.TestCase):
    def test_asr_and_total_costs_preserve_zero_and_report_unavailable_reasons(self) -> None:
        free = SubtitlePipeline(
            asr_provider=FakeASR(price_per_second_cny=0),
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "ja", "zh"),
        )
        free._pcm_offset = 12.5
        status = free.status()
        self.assertEqual(status["asrUsage"], {"seconds": 12.5})
        self.assertEqual(status["asrEstimatedCostCny"], 0)
        self.assertIsNone(status["asrEstimateReason"])
        self.assertIsNone(status["totalEstimatedCostCny"])
        self.assertIn("translation usage unavailable", status["totalEstimateReason"])

        unknown = SubtitlePipeline(
            asr_provider=FakeASR(price_per_second_cny=None),
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "ja", "zh"),
        )
        unknown._pcm_offset = 12.5
        status = unknown.status()
        self.assertIsNone(status["asrEstimatedCostCny"])
        self.assertIn("ASR pricing unavailable", status["asrEstimateReason"])


class MinimalLatencyBreakdownTests(unittest.TestCase):
    def test_evicted_frontier_is_not_replaced_by_newer_timestamp(self) -> None:
        from collections import deque
        samples = deque(((i / 10, i / 10) for i in range(1, 1202)), maxlen=1200)
        self.assertIsNone(SubtitlePipeline._frontier_time(samples, .1))
        self.assertEqual(SubtitlePipeline._frontier_time(samples, .3), .3)

    def test_materialized_cue_pins_audio_time_across_translation_delay(self) -> None:
        now = [3.0]
        pipeline = SubtitlePipeline(asr_provider=FakeASR(), translation_provider=RecordingTranslation("mt"),
            cue_store=CueStore(), meta=StreamMeta(None, None, None, "en", "zh"), monotonic=lambda: now[0])
        pipeline._audio_push_times.append((2.0, 1.0))
        cue = pipeline._materialize_cue(pipeline_module._PendingFinal(
            text="fixture", begin_pcm=1.0, end_pcm=2.0, timing_source="asr", lang="en",
            audio_end_wall=0.0, evidence_available_mono=2.0, chunk_emitted_mono=3.0), 100.0)
        latency = pipeline._cue_latencies[cue.id]
        latency.translation_started = 4.0
        latency.provider_finished = 140.0
        now[0] = 141.0
        pipeline._audio_push_times.clear()
        pipeline._audio_push_times.extend((float(i), float(i)) for i in range(10, 1210))
        pipeline._record_stage_lags(cue.id)
        self.assertEqual(pipeline.status()["totalReadyDelayP95"], 140.0)
        self.assertEqual(pipeline.status()["latencySamples"], 1)

    def test_nonmonotonic_stage_is_unknown_not_zero_clamped(self) -> None:
        pipeline = SubtitlePipeline(asr_provider=FakeASR(), translation_provider=RecordingTranslation("mt"),
            cue_store=CueStore(), meta=StreamMeta(None, None, None, "en", "zh"), monotonic=lambda: 10.0)
        pipeline._audio_push_times.append((2.0, 4.0))
        cue = pipeline._materialize_cue(pipeline_module._PendingFinal(
            text="fixture", begin_pcm=1.0, end_pcm=2.0, timing_source="asr", lang="en",
            audio_end_wall=0.0, evidence_available_mono=2.0, chunk_emitted_mono=3.0), 100.0)
        latency = pipeline._cue_latencies[cue.id]
        latency.translation_started = 5.0
        latency.provider_finished = 9.0
        pipeline._record_stage_lags(cue.id)
        self.assertEqual(pipeline.status()["latencySamples"], 0)
        self.assertEqual(pipeline.status()["latencyUnknown"], 1)
        self.assertEqual(pipeline.status()["latencyUnknownReasons"], {"nonMonotonic": 1})

    def test_records_complete_stage_breakdown_without_text(self) -> None:
        now = [10.0]
        pipeline = SubtitlePipeline(
            asr_provider=FakeASR(),
            translation_provider=RecordingTranslation("mt"),
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "en", "zh"),
            monotonic=lambda: now[0],
        )
        pipeline._audio_push_times.append((2.0, 1.0))
        pending = pipeline_module._PendingFinal(
            text="private text",
            begin_pcm=1.0,
            end_pcm=2.0,
            timing_source="asr",
            lang="en",
            audio_end_wall=9.0,
            evidence_available_mono=3.0,
            chunk_emitted_mono=4.0,
        )
        cue = pipeline._materialize_cue(pending, 100.0)
        latency = pipeline._cue_latencies[cue.id]
        latency.translation_started = 6.0
        latency.provider_finished = 9.0
        pipeline._record_stage_lags(cue.id)

        status = pipeline.status()
        self.assertEqual(status["asrAdapterDelayP95"], 2.0)
        self.assertEqual(status["chunkerPolicyDelayP95"], 1.0)
        self.assertEqual(status["translationQueueDelayP95"], 2.0)
        self.assertEqual(status["translationProviderDelayP95"], 3.0)
        self.assertEqual(status["storeUpdateDelayP95"], 1.0)
        self.assertEqual(status["totalReadyDelayP95"], 9.0)
        self.assertEqual(status["latencySamples"], 1)
        self.assertNotIn("private text", repr(status))

    def test_incomplete_sample_is_counted_unknown(self) -> None:
        pipeline = SubtitlePipeline(
            asr_provider=FakeASR(),
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "en", "zh"),
            monotonic=lambda: 10.0,
        )
        pipeline._cue_latencies[7] = pipeline_module._CueLatency(2.0, None, 4.0, 5.0)
        pipeline._record_stage_lags(7)
        status = pipeline.status()
        self.assertEqual(status["latencySamples"], 0)
        self.assertEqual(status["latencyUnknown"], 1)

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


class PipelineFinalTests(unittest.IsolatedAsyncioTestCase):
    def make_pipeline(self, **changes):
        arguments: dict[str, Any] = dict(
            asr_provider=FakeASR(),
            cue_store=CueStore(),
            meta=StreamMeta("title", "channel", "gaming", "ja", "zh"),
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
    def prime_timeline(pipeline: SubtitlePipeline, c: float = 0.0) -> None:
        pipeline.media_epoch = 950.0 + c

    async def test_final_takes_boundaries_from_its_own_vad_events(self) -> None:
        pipeline = self.make_pipeline()
        self.prime_timeline(pipeline)
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
        self.prime_timeline(pipeline)
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
        self.prime_timeline(pipeline)
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

    async def test_same_short_text_from_different_speakers_is_not_deduplicated(self) -> None:
        pipeline = self.make_pipeline()
        self.prime_timeline(pipeline)
        pipeline._last_sent_pcm_offset = 2.0
        first = await pipeline._handle_final(ASREvent(
            "final", text="はい。", item_id="a", speaker="speaker-1",
        ))
        pipeline._last_sent_pcm_offset = 2.4
        second = await pipeline._handle_final(ASREvent(
            "final", text="はい。", item_id="b", speaker="speaker-2",
        ))
        self.assertEqual([cue.speaker for cue in first + second], ["speaker-1", "speaker-2"])
        self.assertEqual(pipeline.stats.final_deduplicated, 0)

    async def test_final_without_vad_span_falls_back_to_approx(self) -> None:
        pipeline = self.make_pipeline()
        self.prime_timeline(pipeline)
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

    async def test_final_waits_for_private_hls_epoch(self) -> None:
        pipeline = self.make_pipeline()
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=1.0, item_id="a"))
        await pipeline._handle_asr_event(ASREvent("speech_stopped", end_pcm=2.0, item_id="a"))
        pipeline._last_sent_pcm_offset = 2.0
        cues = await pipeline._handle_final(ASREvent("final", text="こんにちは", item_id="a"))
        self.assertEqual(cues, [])
        self.assertEqual(len(pipeline._pending_finals), 1)
        self.assertEqual(len(pipeline.store), 0)

        pipeline.media_epoch = 949.75
        flushed = pipeline._flush_pending_finals()
        self.assertEqual([cue.src for cue in flushed], ["こんにちは"])
        self.assertAlmostEqual(flushed[0].t_end, 951.75)
        self.assertEqual(len(pipeline._pending_finals), 0)

    async def test_ingest_error_suppresses_final(self) -> None:
        pipeline = self.make_pipeline(ingest_status=lambda: {"sourceError": "expired", "log_tail": []})
        self.prime_timeline(pipeline)
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
            pipeline.context.add(
                f"old{index}", f"译{index}",
                generation=pipeline._generation,
                chunk_order=index + 1,
                media_t_end=1000.0,
            )
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
                generation=pipeline._generation,
                chunk_order=index + 4,
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
        status = pipeline.status()
        self.assertEqual(status["readyLagP95"], 1.0)
        self.assertEqual(status["translationSuccessReadyLagP95"], 1.0)
        self.assertEqual(status["terminalOutcomeLagP95"], 1.0)

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


class AudioLegAnchorTests(unittest.IsolatedAsyncioTestCase):
    """Golden tests for P3-B: cues from the independent audio leg map through
    the continuously measured MediaAnchor into video-leg media space.
    Design: docs/subtitle-audio-leg-design-2026-09-09.md"""

    def make_pipeline(self, anchor, probe=None, **changes):
        from companion.media_anchor import MediaAnchor  # noqa: F401
        arguments: dict[str, Any] = dict(
            asr_provider=FakeASR(),
            cue_store=CueStore(),
            meta=StreamMeta("title", "channel", "gaming", "ja", "zh"),
            wall_clock=lambda: 1000.0,
            monotonic=lambda: 50.0,
            silence_duration_ms=400,
            media_anchor=anchor,
            anchor_probe=probe,
        )
        arguments.update(changes)
        pipeline = SubtitlePipeline(**arguments)
        pipeline._push_breadcrumbs.append((0.0, 0.0))
        pipeline.media_epoch = 950.0
        return pipeline

    @staticmethod
    def feed_steady(anchor, now, samples, video_base, audio_base=0.0):
        """Both legs at exactly 1x: C = video_base - audio_base."""
        anchor.add_sample(video_base, audio_base)
        for i in range(1, samples + 1):
            now[0] += 1.0
            anchor.add_sample(video_base + i, audio_base + i)

    async def emit_final(self, pipeline, begin=1.0, end=2.0, text="こんにちは"):
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=begin, item_id="a"))
        await pipeline._handle_asr_event(ASREvent("speech_stopped", end_pcm=end, item_id="a"))
        pipeline._last_sent_pcm_offset = end
        return await pipeline._handle_final(ASREvent("final", text=text, item_id="a"))

    async def test_cues_hold_until_anchor_ready_then_map_through_offset(self) -> None:
        from companion.media_anchor import MediaAnchor
        now = [1000.0]
        anchor = MediaAnchor(clock=lambda: now[0])
        pipeline = self.make_pipeline(anchor)
        # Anchor not ready: the final is held, not materialized on a
        # meaningless timeline.
        cues = await self.emit_final(pipeline)
        self.assertEqual(cues, [])
        self.assertEqual(len(pipeline.store), 0)
        self.assertEqual(len(pipeline._pending_finals), 1)
        # Anchor converges at C=40 (audio leg started 40 media-seconds later
        # than the video leg's packaged origin).
        self.feed_steady(anchor, now, 4, video_base=40.0)
        self.assertTrue(anchor.ready)
        flushed = pipeline._flush_pending_finals()
        self.assertEqual(len(flushed), 1)
        self.assertAlmostEqual(flushed[0].t_end, 950.0 + 2.0 + 40.0)
        self.assertAlmostEqual(flushed[0].t_start, 950.0 + 1.0 + 40.0)

    async def test_video_skip_reanchors_subsequent_cues(self) -> None:
        from companion.media_anchor import MediaAnchor
        now = [1000.0]
        anchor = MediaAnchor(clock=lambda: now[0], window=20, reset_threshold=5.0, reset_samples=3)
        pipeline = self.make_pipeline(anchor)
        self.feed_steady(anchor, now, 5, video_base=140.0)  # C = 140
        first = await self.emit_final(pipeline)
        self.assertAlmostEqual(first[0].t_end, 950.0 + 2.0 + 140.0)
        # Video leg stalls then skips 30s of content: V jumps, audio leg
        # continues at 1x. The jump sample is rate-rejected; the following
        # steady samples trigger the fast reset.
        video_now = 140.0 + 5 + 30.0  # jumped
        audio_now = 5.0
        now[0] += 1.0
        video_now += 1.0
        audio_now += 1.0
        anchor.add_sample(video_now, audio_now)  # spike sample, rejected
        for _ in range(6):
            now[0] += 1.0
            video_now += 1.0
            audio_now += 1.0
            anchor.add_sample(video_now, audio_now)
        self.assertTrue(anchor.ready)
        self.assertAlmostEqual(anchor.offset or 0, 170.0, places=6)
        second = await self.emit_final(pipeline, begin=10.0, end=11.0, text="つぎ")
        self.assertAlmostEqual(second[0].t_end, 950.0 + 11.0 + 170.0)

    async def test_status_reports_audio_leg_source_and_anchor_telemetry(self) -> None:
        from companion.media_anchor import MediaAnchor
        now = [1000.0]
        anchor = MediaAnchor(clock=lambda: now[0])
        pipeline = self.make_pipeline(anchor)
        self.feed_steady(anchor, now, 4, video_base=40.0)
        status = pipeline.status()
        self.assertEqual(status["captionSource"], "audio-leg")
        self.assertTrue(status["mediaAnchor"]["ready"])
        self.assertAlmostEqual(status["mediaAnchor"]["offset"], 40.0)
        # Legacy private-HLS mode is unchanged.
        legacy = PipelineFinalTests().make_pipeline()
        legacy_status = legacy.status()
        self.assertEqual(legacy_status["captionSource"], "private-hls")
        self.assertIsNone(legacy_status["mediaAnchor"])

    async def test_epoch_late_binding_releases_held_finals_with_anchor_mapping(self) -> None:
        """Parallel startup contract: pipeline started with epoch=None holds
        finals; set_media_epoch releases them through the anchor mapping."""
        from companion.media_anchor import MediaAnchor
        now = [1000.0]
        anchor = MediaAnchor(clock=lambda: now[0])
        pipeline = self.make_pipeline(anchor)
        pipeline.media_epoch = None  # override prime: epoch not yet known
        self.feed_steady(anchor, now, 4, video_base=40.0)  # C=40, ready
        cues = await self.emit_final(pipeline)
        self.assertEqual(cues, [])
        self.assertEqual(len(pipeline._pending_finals), 1)
        pipeline.set_media_epoch(950.0)
        self.assertEqual(len(pipeline._pending_finals), 0)
        self.assertEqual(len(pipeline.store), 1)
        # A subsequent final materializes immediately through the anchor.
        second = await self.emit_final(pipeline, begin=3.0, end=4.0, text="つぎ")
        self.assertAlmostEqual(second[0].t_end, 950.0 + 4.0 + 40.0)

    async def test_audio_leg_input_uses_explicit_format_without_hls_flags(self) -> None:
        captured: dict[str, Any] = {}

        async def factory(*args, **kwargs):
            captured["args"] = args
            raise RuntimeError("no ffmpeg in unit test")

        from companion.media_anchor import MediaAnchor
        pipeline = self.make_pipeline(MediaAnchor(), subprocess_factory=factory)
        with self.assertRaises(RuntimeError):
            await pipeline.start("tcp://127.0.0.1:9999", 950.0, input_format="mpegts")
        args = list(captured["args"])
        self.assertIn("-f", args)
        self.assertEqual(args[args.index("-f") + 1], "mpegts")
        self.assertIn("tcp://127.0.0.1:9999", args)
        self.assertNotIn("-live_start_index", args)

    async def test_default_input_keeps_private_hls_flags(self) -> None:
        captured: dict[str, Any] = {}

        async def factory(*args, **kwargs):
            captured["args"] = args
            raise RuntimeError("no ffmpeg in unit test")

        pipeline = self.make_pipeline(None, subprocess_factory=factory)
        with self.assertRaises(RuntimeError):
            await pipeline.start("http://127.0.0.1/private/live.m3u8", 950.0)
        args = list(captured["args"])
        self.assertIn("-live_start_index", args)


class SampleRateTests(unittest.IsolatedAsyncioTestCase):
    def make_pipeline(self, **changes):
        arguments: dict[str, Any] = dict(
            asr_provider=FakeASR(),
            cue_store=CueStore(),
            meta=StreamMeta("title", "channel", "gaming", "ja", "zh-Hans"),
            wall_clock=lambda: 1000.0,
            monotonic=lambda: 50.0,
        )
        arguments.update(changes)
        return SubtitlePipeline(**arguments)

    def test_rejects_unsupported_sample_rates(self) -> None:
        for rate in (8000, 44100, 48000):
            with self.assertRaises(ValueError, msg=str(rate)):
                self.make_pipeline(sample_rate=rate)

    async def test_24khz_pcm_clock_and_cost_seconds(self) -> None:
        pipeline = self.make_pipeline(sample_rate=24000)
        self.assertEqual(pipeline.sample_rate, 24000)
        self.assertEqual(pipeline.pcm_bytes_per_second, 48000)
        # 4800 bytes at 24 kHz 16-bit mono is exactly 0.1s of audio.
        pipeline._ingest_pcm_chunk(b"\x00" * 4800, 0.0)
        self.assertAlmostEqual(pipeline._pcm_offset, 0.1)
        pipeline._ingest_pcm_chunk(b"\x00" * 48000, pipeline._pcm_offset)
        self.assertAlmostEqual(pipeline._pcm_offset, 1.1)
        status = pipeline.status()
        self.assertEqual(status["sampleRate"], 24000)
        # Cost seconds track the negotiated rate, not a hardcoded 16 kHz:
        # 1.1s x 0.5 CNY/s.
        self.assertAlmostEqual(status["asrEstimatedCostCny"], 0.55)

    async def test_ffmpeg_is_started_with_the_negotiated_rate(self) -> None:
        captured: dict[str, Any] = {}

        async def factory(*args, **kwargs):
            captured["args"] = args
            raise RuntimeError("no ffmpeg in unit test")

        pipeline = self.make_pipeline(sample_rate=24000, subprocess_factory=factory)
        with self.assertRaises(RuntimeError):
            await pipeline.start("http://127.0.0.1/private/live.m3u8", 950.0)
        self.assertIn("-ar", captured["args"])
        index = list(captured["args"]).index("-ar")
        self.assertEqual(captured["args"][index + 1], "24000")
        self.assertIn("aresample=async=1:first_pts=0", captured["args"])
        self.assertIn("-live_start_index", captured["args"])

    async def test_16khz_default_is_unchanged(self) -> None:
        pipeline = self.make_pipeline()
        self.assertEqual(pipeline.pcm_bytes_per_second, 32000)
        pipeline._ingest_pcm_chunk(b"\x00" * 3200, 0.0)
        self.assertAlmostEqual(pipeline._pcm_offset, 0.1)
        self.assertEqual(pipeline.status()["sampleRate"], 16000)

class CueLanguageTests(unittest.IsolatedAsyncioTestCase):
    def make_pipeline(self, **changes):
        arguments: dict[str, Any] = dict(
            asr_provider=FakeASR(),
            cue_store=CueStore(),
            meta=StreamMeta("title", "channel", "gaming", "en", "zh-Hans"),
            wall_clock=lambda: 1000.0,
            monotonic=lambda: 50.0,
        )
        arguments.update(changes)
        pipeline = SubtitlePipeline(**arguments)
        pipeline._push_breadcrumbs.append((0.0, 0.0))
        pipeline.media_epoch = 950.0
        return pipeline

    async def test_reported_language_wins_and_translation_uses_cue_language(self) -> None:
        translation = RecordingTranslation("primary")
        pipeline = self.make_pipeline(
            source_policy=SourceLanguagePolicy.specified("en"),
            translation_provider=translation,
        )
        pipeline._last_sent_pcm_offset = 2.0
        cues = await pipeline._handle_final(ASREvent("final", text="Bonjour le monde", language="fr", item_id="a"))
        self.assertEqual(cues[0].lang, "fr")

        pipeline._running = True
        worker = asyncio.create_task(pipeline._translation_worker())
        await asyncio.wait_for(pipeline._translation_queue.join(), 1)
        pipeline._running = False
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        # The translation request source comes from the cue, not the
        # stream-level specified language.
        self.assertEqual(translation.requests[0].meta.source_lang, "fr")
        self.assertEqual(translation.requests[0].meta.target_lang, "zh-Hans")

    def test_target_language_hot_switch_clears_old_translation_context(self) -> None:
        pipeline = self.make_pipeline(translation_provider=RecordingTranslation("primary"))
        pipeline.context.add(
            "old source", "旧译文", generation=1, chunk_order=1, media_t_end=1.0,
        )

        self.assertTrue(pipeline.update_target_language("en-US"))
        self.assertEqual(pipeline.meta.target_lang, "en-US")
        self.assertEqual(pipeline.context.orders(1), ())
        self.assertEqual(pipeline.status()["targetLanguage"], "en-US")
        self.assertFalse(pipeline.update_target_language("en-US"))

    async def test_reported_language_is_canonicalized_at_the_edge(self) -> None:
        pipeline = self.make_pipeline(source_policy=SourceLanguagePolicy.specified("en"))
        pipeline._last_sent_pcm_offset = 2.0
        cues = await pipeline._handle_final(ASREvent("final", text="你好", language="zh-TW", item_id="a"))
        self.assertEqual(cues[0].lang, "zh-Hant")

    async def test_unreported_language_falls_back_to_specified_or_preferred(self) -> None:
        specified = self.make_pipeline(source_policy=SourceLanguagePolicy.specified("en"))
        specified._last_sent_pcm_offset = 2.0
        cues = await specified._handle_final(ASREvent("final", text="no report", item_id="a"))
        self.assertEqual(cues[0].lang, "en")

        detected = self.make_pipeline(
            source_policy=SourceLanguagePolicy.detect(candidates=("fr", "en"), preferred="fr")
        )
        detected._last_sent_pcm_offset = 2.0
        cues = await detected._handle_final(ASREvent("final", text="no report", item_id="a"))
        self.assertEqual(cues[0].lang, "fr")

    async def test_detect_policy_is_passed_to_the_asr_stream(self) -> None:
        class PolicyRecordingASR(FakeASR):
            def __init__(self) -> None:
                super().__init__()
                self.received: dict[str, Any] = {}

            async def stream(self, *, policy, sample_rate, hotwords, context):
                self.received = {
                    "policy": policy,
                    "sample_rate": sample_rate,
                    "hotwords": hotwords,
                    "context": context,
                }
                return FakeStream()

        asr = PolicyRecordingASR()
        policy = SourceLanguagePolicy.detect(candidates=("ja", "en"), preferred="ja")
        pipeline = self.make_pipeline(asr_provider=asr, source_policy=policy, sample_rate=24000)
        pipeline._running = True
        task = asyncio.create_task(pipeline._asr_manager())
        await asyncio.sleep(0.05)
        pipeline._running = False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertIs(asr.received["policy"], policy)
        self.assertEqual(asr.received["sample_rate"], 24000)


class PrefixSplitTests(unittest.IsolatedAsyncioTestCase):
    """Long-utterance prefix splitting (redesign Fix F P2 / RC-4).

    Event sequences mirror the captured real-time stream: speech_started,
    stable-prefix interims growing monotonically, speech_stopped, final.
    """

    def make_pipeline(self, **changes):
        arguments: dict[str, Any] = dict(
            asr_provider=FakeASR(),
            cue_store=CueStore(),
            meta=StreamMeta("title", "channel", "gaming", "ja", "zh"),
            wall_clock=lambda: 1000.0,
            monotonic=lambda: 50.0,
            silence_duration_ms=400,
        )
        arguments.update(changes)
        pipeline = SubtitlePipeline(**arguments)
        pipeline._push_breadcrumbs.append((0.0, 0.0))
        pipeline.media_epoch = 950.0
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

    async def test_stable_interim_begin_time_can_open_utterance_before_delayed_endpoint(self) -> None:
        pipeline = self.make_pipeline()
        pipeline._last_sent_pcm_offset = 5.0
        pipeline._pcm_offset = 5.0
        await pipeline._handle_asr_event(ASREvent(
            "interim",
            text="対応関係のある interim。",
            begin_pcm=1.0,
            end_pcm=4.8,
            item_id="soniox-1",
        ))
        cues = pipeline.store.query(after_seq=0)
        self.assertEqual([cue.src for cue in cues], ["対応関係のある interim。"])
        self.assertAlmostEqual(cues[0].t_start, 951.0)
        self.assertAlmostEqual(cues[0].t_end, 955.0)

    async def test_soniox_speaker_label_is_preserved_across_prefix_and_final_tail(self) -> None:
        pipeline = self.make_pipeline()
        pipeline._last_sent_pcm_offset = 5.0
        pipeline._pcm_offset = 5.0
        await pipeline._handle_asr_event(ASREvent(
            "interim",
            text="一人目の発言。",
            begin_pcm=1.0,
            end_pcm=4.8,
            item_id="soniox-1",
            speaker="speaker-7",
        ))
        await pipeline._handle_asr_event(ASREvent("speech_stopped", end_pcm=6.0, item_id="soniox-1"))
        pipeline._last_sent_pcm_offset = 6.2
        await pipeline._handle_final(ASREvent(
            "final",
            text="一人目の発言。続きです。",
            item_id="soniox-1",
        ))
        cues = pipeline.store.query(after_seq=0)
        self.assertEqual([cue.speaker for cue in cues], ["speaker-7", "speaker-7"])

    async def test_interim_without_any_timing_anchor_is_ignored(self) -> None:
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



class TranslationTerminalOutcomeTests(unittest.TestCase):
    def test_pipeline_without_translation_provider_publishes_terminal_source_only(self) -> None:
        pipeline = SubtitlePipeline(
            asr_provider=FakeASR(),
            cue_store=CueStore(),
            meta=StreamMeta("title", "channel", "gaming", "en", "es"),
            wall_clock=lambda: 1000.0,
        )
        pipeline.media_epoch = 950.0
        cue = pipeline._materialize_cue(
            pipeline_module._PendingFinal(
                text="source only",
                begin_pcm=0.0,
                end_pcm=1.0,
                timing_source="asr",
                lang="en",
                audio_end_wall=999.0,
                generation=1,
                chunk_order=1,
            ),
            951.0,
        )
        self.assertEqual(pipeline.store.get(cue.id).state, "failed")
        status = pipeline.status()
        self.assertEqual(status["sourceReadyLagP95"], 1.0)
        self.assertIsNone(status["translationSuccessReadyLagP95"])
        self.assertEqual(status["terminalOutcomeLagP95"], 1.0)
        self.assertEqual(status["readyLagP95"], 1.0)


class TranslationContinuityPipelineTests(unittest.IsolatedAsyncioTestCase):
    class ControlledTranslation(TranslationProvider):
        id = "controlled"
        label = "Controlled"
        model = "controlled"

        def __init__(self, *, failures: set[int] | None = None) -> None:
            self.requests: dict[int, TranslationRequest] = {}
            self.started: dict[int, asyncio.Event] = {}
            self.releases: dict[int, asyncio.Event] = {}
            self.failures = failures or set()

        @property
        def capabilities(self) -> TranslationCapabilities:
            return TranslationCapabilities(True, True, True, False, 1000)

        async def translate(self, request: TranslationRequest) -> TranslationResult:
            assert request.chunk_order is not None
            order = request.chunk_order
            self.requests[order] = request
            self.started.setdefault(order, asyncio.Event()).set()
            await self.releases.setdefault(order, asyncio.Event()).wait()
            if order in self.failures:
                raise RuntimeError(f"translation {order} failed")
            return TranslationResult(f"z{order}", self.id, order)

    def make_pipeline(self, provider: TranslationProvider, *, workers: int = 4) -> SubtitlePipeline:
        return SubtitlePipeline(
            asr_provider=FakeASR(),
            translation_provider=provider,
            cue_store=CueStore(),
            meta=StreamMeta("title", "channel", "gaming", "en", "es"),
            translation_workers=workers,
            wall_clock=lambda: 1000.0,
            monotonic=time.monotonic,
        )

    @staticmethod
    def add_cue(pipeline: SubtitlePipeline, order: int, *, generation: int = 1):
        cue = pipeline.store.add(
            t_start=float(order - 1),
            t_end=float(order),
            hold=1.2,
            src=f"s{order}",
            lang="en",
            timing_source="asr",
            generation=generation,
            chunk_order=order,
        )
        pipeline._audio_end_walls[cue.id] = pipeline.wall_clock()
        pipeline._enqueue_translation(cue)
        return cue

    async def test_slow_first_translation_does_not_block_later_sentences(self) -> None:
        provider = self.ControlledTranslation()
        pipeline = self.make_pipeline(provider, workers=4)
        pipeline._running = True
        workers = [asyncio.create_task(pipeline._translation_worker()) for _ in range(4)]
        try:
            cues = [self.add_cue(pipeline, order) for order in (1, 2, 3)]
            for order in (1, 2, 3):
                await asyncio.wait_for(provider.started.setdefault(order, asyncio.Event()).wait(), 1)
            self.assertEqual(provider.requests[2].history, [("s1", None)])
            for order in (3, 2):
                provider.releases[order].set()
            for _ in range(100):
                if all(pipeline.store.get(c.id).state == "done" for c in cues[1:]):
                    break
                await asyncio.sleep(0)
            self.assertEqual([pipeline.store.get(c.id).state for c in cues], ["translating", "done", "done"])
            # A fourth caption can start with completed earlier context while
            # caption 1 remains stuck. Its original media window never moves.
            fourth = self.add_cue(pipeline, 4)
            await asyncio.wait_for(provider.started.setdefault(4, asyncio.Event()).wait(), 1)
            self.assertEqual(provider.requests[4].history, [("s1", None), ("s2", "z2"), ("s3", "z3")])
            self.assertEqual((fourth.t_start, fourth.t_end), (3, 4))
            provider.releases[4].set()
            provider.releases[1].set()
            await asyncio.wait_for(pipeline._translation_queue.join(), 1)
            self.assertEqual(pipeline.context.orders(1), (1, 2, 3, 4))
        finally:
            pipeline._running = False
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    async def test_fast_provider_is_not_expired_by_an_older_long_cue(self) -> None:
        """A fast second call must survive queue wait caused by an older cue.

        The live failure was reproducible when a long cue reserved its whole
        audio span inside the 15-second playback delay.  The first provider
        call below takes 400ms; the second takes only 50ms, but the current
        deadline formula expires it before the worker can call the provider.
        """
        class DelayedTranslation(TranslationProvider):
            id = "delayed"
            label = "Delayed"
            model = "delayed"

            @property
            def capabilities(self) -> TranslationCapabilities:
                return TranslationCapabilities(True, True, True, False, 1000)

            async def translate(self, request: TranslationRequest) -> TranslationResult:
                await asyncio.sleep(0.55 if request.chunk_order == 1 else 0.05)
                return TranslationResult(f"z{request.chunk_order}", self.id, 50)

        provider = DelayedTranslation()
        pipeline = SubtitlePipeline(
            asr_provider=FakeASR(),
            translation_provider=provider,
            cue_store=CueStore(),
            meta=StreamMeta("title", "channel", "gaming", "en", "es"),
            translation_workers=1,
            translation_timeout_seconds=0.8,
            playback_delay_seconds=lambda: 1.5,
        )
        first = pipeline.store.add(
            t_start=0.0, t_end=0.1, hold=1.2, src="old", lang="en", timing_source="asr",
            generation=1, chunk_order=1,
        )
        second = pipeline.store.add(
            t_start=0.0, t_end=1.0, hold=1.2, src="fresh", lang="en", timing_source="asr",
            generation=1, chunk_order=2,
        )
        pipeline._audio_end_walls[first.id] = pipeline.wall_clock()
        pipeline._audio_end_walls[second.id] = pipeline.wall_clock()
        pipeline._enqueue_translation(first)
        pipeline._enqueue_translation(second)
        pipeline._running = True
        worker = asyncio.create_task(pipeline._translation_worker())
        try:
            await asyncio.wait_for(pipeline._translation_queue.join(), 2)
            self.assertEqual(pipeline.store.get(first.id).state, "done")
            self.assertEqual(pipeline.store.get(second.id).state, "done")
        finally:
            pipeline._running = False
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_already_late_cue_is_dropped_at_queue_seam(self) -> None:
        provider = self.ControlledTranslation()
        pipeline = SubtitlePipeline(
            asr_provider=FakeASR(),
            translation_provider=provider,
            cue_store=CueStore(),
            meta=StreamMeta("title", "channel", "gaming", "en", "es"),
            translation_workers=1,
            translation_timeout_seconds=6.0,
            playback_delay_seconds=lambda: 15.0,
            wall_clock=lambda: 1_020.0,
            monotonic=lambda: 100.0,
        )
        cue = pipeline.store.add(
            t_start=0.0, t_end=1.0, hold=1.2, src="stale", lang="en", timing_source="asr",
            generation=1, chunk_order=1,
        )
        pipeline._audio_end_walls[cue.id] = 1_000.0

        pipeline._enqueue_translation(cue)

        self.assertEqual(pipeline._translation_queue.qsize(), 0)
        self.assertEqual(provider.requests, {})
        self.assertEqual(pipeline.store.get(cue.id).state, "failed")
        self.assertEqual(pipeline.stats.translation_dropped, 1)
        self.assertEqual(pipeline.stats.translation_deadline_expired, 1)
        self.assertEqual(pipeline.stats.translation_failures, 0)

    async def test_target_hot_switch_discards_inflight_old_language_and_updates_next_cue(self) -> None:
        provider = self.ControlledTranslation()
        pipeline = self.make_pipeline(provider, workers=1)
        pipeline._running = True
        worker = asyncio.create_task(pipeline._translation_worker())
        try:
            first = self.add_cue(pipeline, 1)
            await asyncio.wait_for(provider.started.setdefault(1, asyncio.Event()).wait(), 1)
            self.assertEqual(provider.requests[1].meta.target_lang, "es")

            pipeline.update_target_language("fr")
            provider.releases.setdefault(1, asyncio.Event()).set()
            await asyncio.wait_for(pipeline._translation_queue.join(), 1)
            self.assertEqual(pipeline.store.get(first.id).state, "failed")
            self.assertIsNone(pipeline.store.get(first.id).zh)

            second = self.add_cue(pipeline, 2)
            await asyncio.wait_for(provider.started.setdefault(2, asyncio.Event()).wait(), 1)
            self.assertEqual(provider.requests[2].meta.target_lang, "fr")
            self.assertEqual(provider.requests[2].history, [])
            provider.releases.setdefault(2, asyncio.Event()).set()
            await asyncio.wait_for(pipeline._translation_queue.join(), 1)
            self.assertEqual(pipeline.store.get(second.id).state, "done")
        finally:
            pipeline._running = False
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_parallel_translation_keeps_the_worker_limit_and_reuses_freed_slots(self) -> None:
        provider = self.ControlledTranslation()
        pipeline = self.make_pipeline(provider, workers=4)
        pipeline._running = True
        workers = [asyncio.create_task(pipeline._translation_worker()) for _ in range(4)]
        try:
            for order in range(1, 6):
                self.add_cue(pipeline, order)
            for order in range(1, 5):
                await asyncio.wait_for(provider.started.setdefault(order, asyncio.Event()).wait(), 1)
            self.assertNotIn(5, provider.requests)
            provider.releases[3].set()
            await asyncio.wait_for(provider.started.setdefault(5, asyncio.Event()).wait(), 1)
            self.assertEqual(provider.requests[5].history,
                             [("s3", "z3"), ("s4", None)])  # Existing backlog policy keeps the latest two.
            for order in (1, 2, 4, 5):
                provider.releases[order].set()
            await asyncio.wait_for(pipeline._translation_queue.join(), 1)
        finally:
            pipeline._running = False
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    async def test_chunk4_receives_the_ordered_history_of_chunks_1_2_3(self) -> None:
        provider = self.ControlledTranslation()
        pipeline = self.make_pipeline(provider, workers=3)
        pipeline._running = True
        workers = [asyncio.create_task(pipeline._translation_worker()) for _ in range(3)]
        try:
            for order in (1, 2, 3):
                self.add_cue(pipeline, order)
            for order in (3, 1, 2):
                await asyncio.wait_for(provider.started.setdefault(order, asyncio.Event()).wait(), 1)
                provider.releases[order].set()
                for _ in range(100):
                    stored = pipeline.store.query(after_seq=0)
                    if any(cue.chunk_order == order and cue.state == "done" for cue in stored):
                        break
                    await asyncio.sleep(0)
            self.add_cue(pipeline, 4)
            await asyncio.wait_for(provider.started.setdefault(4, asyncio.Event()).wait(), 1)
            self.assertEqual(provider.requests[4].history, [("s1", "z1"), ("s2", "z2"), ("s3", "z3")])
            provider.releases[4].set()
            await asyncio.wait_for(pipeline._translation_queue.join(), 1)
        finally:
            pipeline._running = False
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    async def test_failed_predecessor_releases_successor_without_deadlock(self) -> None:
        provider = self.ControlledTranslation(failures={1})
        pipeline = self.make_pipeline(provider, workers=4)
        pipeline._running = True
        workers = [asyncio.create_task(pipeline._translation_worker()) for _ in range(4)]
        try:
            first = self.add_cue(pipeline, 1)
            second = self.add_cue(pipeline, 2)
            await asyncio.wait_for(provider.started.setdefault(1, asyncio.Event()).wait(), 1)
            provider.releases.setdefault(1, asyncio.Event()).set()
            await asyncio.wait_for(provider.started.setdefault(2, asyncio.Event()).wait(), 1)
            # Successor starts without waiting for predecessor retries.
            self.assertIn(pipeline.store.get(first.id).state, {"translating", "failed"})
            self.assertEqual(provider.requests[2].history, [("s1", None)])
            provider.releases.setdefault(2, asyncio.Event()).set()
            await asyncio.wait_for(pipeline._translation_queue.join(), 1)
            self.assertEqual(pipeline.store.get(second.id).state, "done")
        finally:
            pipeline._running = False
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    async def test_different_generations_can_translate_concurrently(self) -> None:
        class GenerationControlled(TranslationProvider):
            id = "generation-controlled"
            label = "Generation controlled"
            model = "controlled"

            def __init__(self) -> None:
                self.started: dict[tuple[int, int], asyncio.Event] = {}
                self.releases: dict[tuple[int, int], asyncio.Event] = {}

            @property
            def capabilities(self) -> TranslationCapabilities:
                return TranslationCapabilities(True, True, True, False, 1000)

            async def translate(self, request: TranslationRequest) -> TranslationResult:
                assert request.generation is not None and request.chunk_order is not None
                key = (request.generation, request.chunk_order)
                self.started.setdefault(key, asyncio.Event()).set()
                await self.releases.setdefault(key, asyncio.Event()).wait()
                return TranslationResult(f"z{key}", self.id, 1)

        provider = GenerationControlled()
        pipeline = self.make_pipeline(provider, workers=4)
        pipeline._running = True
        workers = [asyncio.create_task(pipeline._translation_worker()) for _ in range(4)]
        try:
            self.add_cue(pipeline, 1, generation=1)
            self.add_cue(pipeline, 2, generation=1)
            self.add_cue(pipeline, 1, generation=2)
            await asyncio.wait_for(provider.started.setdefault((1, 1), asyncio.Event()).wait(), 1)
            await asyncio.wait_for(provider.started.setdefault((2, 1), asyncio.Event()).wait(), 1)
            await asyncio.wait_for(provider.started.setdefault((1, 2), asyncio.Event()).wait(), 1)
            provider.releases.setdefault((1, 1), asyncio.Event()).set()
            provider.releases.setdefault((2, 1), asyncio.Event()).set()
            await asyncio.wait_for(provider.started[(1, 2)].wait(), 1)
            provider.releases.setdefault((1, 2), asyncio.Event()).set()
            await asyncio.wait_for(pipeline._translation_queue.join(), 1)
        finally:
            pipeline._running = False
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    async def test_saturated_workers_skip_expired_work_and_recover_for_fresh_sentences(self) -> None:
        provider = self.ControlledTranslation()
        now = [0.0]
        pipeline = SubtitlePipeline(
            asr_provider=FakeASR(),
            translation_provider=provider,
            cue_store=CueStore(),
            meta=StreamMeta("title", "channel", "gaming", "en", "es"),
            translation_workers=1,
            translation_timeout_seconds=6.0,
            wall_clock=lambda: 1000.0,
            monotonic=lambda: now[0],
        )
        pipeline._running = True
        workers = [asyncio.create_task(pipeline._translation_worker()) for _ in range(1)]
        try:
            self.add_cue(pipeline, 1)
            second = self.add_cue(pipeline, 2)
            await asyncio.wait_for(provider.started.setdefault(1, asyncio.Event()).wait(), 1)
            now[0] = 7.0
            provider.releases.setdefault(1, asyncio.Event()).set()
            await asyncio.wait_for(pipeline._translation_queue.join(), 1)
            self.assertNotIn(2, provider.requests)
            self.assertEqual(pipeline.store.get(second.id).state, "failed")
            fresh = self.add_cue(pipeline, 3)
            await asyncio.wait_for(provider.started.setdefault(3, asyncio.Event()).wait(), 1)
            self.assertEqual(provider.requests[3].deadline_monotonic, 13.0)
            self.assertEqual((fresh.t_start, fresh.t_end), (2, 3))
            provider.releases[3].set()
            await asyncio.wait_for(pipeline._translation_queue.join(), 1)
            self.assertEqual(pipeline.store.get(fresh.id).state, "done")
        finally:
            pipeline._running = False
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    async def test_provider_deadline_uses_cue_end_window_without_double_counting_span(self) -> None:
        provider = self.ControlledTranslation()
        pipeline = SubtitlePipeline(
            asr_provider=FakeASR(),
            translation_provider=provider,
            cue_store=CueStore(),
            meta=StreamMeta("title", "channel", "gaming", "en", "es"),
            translation_workers=1,
            translation_timeout_seconds=6.0,
            playback_delay_seconds=lambda: 11.0,
            wall_clock=lambda: 1000.0,
            monotonic=lambda: 100.0,
        )
        cue_item = pipeline.store.add(
            t_start=0.0, t_end=6.0, hold=1.2, src="long cue", lang="en", timing_source="asr",
            generation=1, chunk_order=1,
        )
        pipeline._audio_end_walls[cue_item.id] = 1000.0
        pipeline._enqueue_translation(cue_item)
        pipeline._running = True
        worker = asyncio.create_task(pipeline._translation_worker())
        try:
            await asyncio.wait_for(provider.started.setdefault(1, asyncio.Event()).wait(), 1)
            # audio_end_wall already marks the cue's end.  Subtracting the
            # six-second cue span here would reserve the same time twice.
            self.assertEqual(provider.requests[1].deadline_monotonic, 106.0)
            provider.releases.setdefault(1, asyncio.Event()).set()
            await asyncio.wait_for(pipeline._translation_queue.join(), 1)
        finally:
            pipeline._running = False
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_chunk2_excludes_current_future_and_counts_missing_predecessor(self) -> None:
        provider = RecordingTranslation("translation")
        pipeline = self.make_pipeline(provider, workers=1)
        # A future chunk may finish before chunk 2 starts, but must not enter its history.
        pipeline.context.add("s3", "z3", generation=1, chunk_order=3, media_t_end=3.0)
        pipeline._running = True
        worker = asyncio.create_task(pipeline._translation_worker())
        try:
            self.add_cue(pipeline, 2)
            await asyncio.wait_for(pipeline._translation_queue.join(), 1)
        finally:
            pipeline._running = False
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        self.assertEqual(provider.requests[0].history, [])
        self.assertEqual(pipeline.status()["translationContextMissingImmediatePredecessor"], 1)

    async def test_context_seconds_is_evaluated_at_cue_media_time(self) -> None:
        provider = RecordingTranslation("translation")
        pipeline = SubtitlePipeline(
            asr_provider=FakeASR(),
            translation_provider=provider,
            cue_store=CueStore(),
            meta=StreamMeta("title", "channel", "gaming", "en", "es"),
            context_seconds=2.0,
            translation_workers=1,
            wall_clock=lambda: 10_000.0,
        )
        pipeline.context.add("too old", "old", generation=1, chunk_order=1, media_t_end=1.9)
        pipeline.context.add("recent", "new", generation=1, chunk_order=2, media_t_end=2.0)
        cue = pipeline.store.add(
            t_start=3.0, t_end=4.0, hold=1.2, src="current", lang="en", timing_source="asr",
            generation=1, chunk_order=3,
        )
        pipeline._audio_end_walls[cue.id] = pipeline.wall_clock()
        pipeline._enqueue_translation(cue)
        pipeline._running = True
        worker = asyncio.create_task(pipeline._translation_worker())
        try:
            await asyncio.wait_for(pipeline._translation_queue.join(), 1)
        finally:
            pipeline._running = False
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        self.assertEqual(provider.requests[0].history, [("recent", "new")])


class CaptionChunkerPipelineIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_soniox_raw_pieces_flow_through_to_overlapping_exact_cues(self) -> None:
        from companion.providers.asr_soniox_realtime import SonioxRealtimeASRProvider, _SonioxStream
        provider = SonioxRealtimeASRProvider({"id": "fixture", "model": "stt-rt-v5",
            "baseUrl": "ws://unused", "options": {"enableSpeakerDiarization": True}})
        stream = _SonioxStream(provider, SourceLanguagePolicy.specified("ja"), 16000, [])
        stream._audio_bytes_sent = 5 * 32000
        pipeline = self.make_pipeline()
        frames = [
            [("私", 0, 200, "1"), ("は", 200, 400, "1"), ("。", 400, 400, "1")],
            [("<end>", None, None, None)],
            [("はい", 500, 800, "2"), ("。", 800, 800, "2")],
            [("<end>", None, None, None)],
            [("1", 900, 1000, "1"), ("2", 1000, 1100, "1"),
             ("個", 1100, 1300, "1"), ("買いました", 1300, 2000, "1"), ("。", 2000, 2000, "1")],
            [("<end>", None, None, None)],
        ]
        for frame in frames:
            raw = {"tokens": [dict(text=text, start_ms=begin, end_ms=end, speaker=speaker,
                                   language="ja", is_final=True) for text, begin, end, speaker in frame]}
            for event in stream._map_event(raw):
                await pipeline._handle_asr_event(event)
        cues = pipeline.store.query(after_seq=0)
        self.assertEqual([c.src for c in cues], ["私は。", "はい。", "12個買いました。"])
        self.assertEqual([c.speaker for c in cues], ["1", "2", "1"])
        self.assertEqual([(c.t_start, c.t_end) for c in cues], [(950.0, 950.4), (950.5, 950.8), (950.9, 952.0)])
        self.assertEqual([c.timing_source for c in cues], ["asr"] * len(cues))
        self.assertEqual(pipeline._flush_caption_session(), [])

    def make_pipeline(self, *, manual_commit: bool = False) -> SubtitlePipeline:
        pipeline = SubtitlePipeline(
            asr_provider=FakeASR(manual_commit=manual_commit),
            cue_store=CueStore(),
            meta=StreamMeta("title", "channel", "gaming", "en", "zh"),
            wall_clock=lambda: 1000.0,
            monotonic=lambda: 50.0,
        )
        pipeline._push_breadcrumbs.append((0.0, 0.0))
        pipeline.media_epoch = 950.0
        return pipeline

    async def test_residual_timer_publishes_without_audio_and_stop_cancels_it(self):
        pipeline = self.make_pipeline()
        pipeline.monotonic = time.monotonic
        pipeline._running = True
        worker = asyncio.create_task(pipeline._caption_deadline_worker())
        pipeline._tasks.append(worker)
        try:
            await pipeline._handle_asr_event(ASREvent(
                "final", text="unfinished", item_id="residual", language="en",
                caption_observation=CaptionObservation(
                    "utterance_final", 0, "residual", stable_text="unfinished",
                    tokens=(RecognitionToken("unfinished", 0, .6, True, language="en", speaker="3"),),
                    begin_pcm=0, end_pcm=.6, language="en", speaker="3",
                ),
            ))
            async def published():
                while not pipeline.store.query(after_seq=0):
                    await asyncio.sleep(.01)
            await asyncio.wait_for(published(), 2)
            cues = pipeline.store.query(after_seq=0)
            self.assertEqual([(c.src, c.t_start, c.t_end, c.timing_source) for c in cues], [("unfinished", 950, 950.6, "asr")])
            self.assertEqual(pipeline._flush_caption_session(), [])
        finally:
            await pipeline.stop()
        self.assertTrue(worker.done())
        self.assertEqual(pipeline._tasks, [])

    async def test_long_unpunctuated_utterance_releases_at_endpoint_without_duplicate(self) -> None:
        pipeline = self.make_pipeline()
        tokens = tuple(
            RecognitionToken(f"w{index}", index * 0.75, index * 0.75 + 0.6, True, language="en")
            for index in range(27)
        )
        await pipeline._handle_asr_event(ASREvent(
            "interim",
            text=" ".join(token.text for token in tokens),
            item_id="long",
            caption_observation=CaptionObservation(
                "stable_token_delta", 0, "long", tokens=tokens,
                begin_pcm=0.0, end_pcm=20.1,
            ),
        ))
        await pipeline._handle_asr_event(ASREvent(
            "speech_stopped", end_pcm=20.1, item_id="long",
            caption_observation=CaptionObservation("endpoint", 0, "long", end_pcm=20.1),
        ))
        await pipeline._handle_asr_event(ASREvent(
            "final",
            text=" ".join(token.text for token in tokens),
            item_id="long",
            caption_observation=CaptionObservation(
                "utterance_final", 0, "long", tokens=tokens,
                stable_text=" ".join(token.text for token in tokens),
                begin_pcm=0.0, end_pcm=20.1,
            ),
        ))

        self.assertEqual(len(pipeline.store.query(after_seq=0)), 1)
        self.assertEqual(pipeline._flush_caption_session(), [])
        cues = pipeline.store.query(after_seq=0)
        self.assertEqual(len(cues), 1)
        self.assertEqual(" ".join(cue.src for cue in cues).split(), [token.text for token in tokens])
        self.assertAlmostEqual(cues[0].t_end - cues[0].t_start, 20.1)
        self.assertTrue(all(cue.timing_source == "asr" for cue in cues))
        self.assertEqual([cue.chunk_order for cue in cues], list(range(1, len(cues) + 1)))
        self.assertEqual(pipeline.status()["captionChunks"], len(cues))

    async def test_complete_stable_sentence_does_not_wait_for_utterance_final(self) -> None:
        pipeline = self.make_pipeline()
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=0.0, item_id="soniox"))
        await pipeline._handle_asr_event(ASREvent(
            "interim", text="Chat, listen to me.", item_id="soniox", language="en",
            caption_observation=CaptionObservation(
                "stable_token_delta", 0, "soniox",
                tokens=(
                    RecognitionToken("Chat,", 0.0, 0.4, True, language="en"),
                    RecognitionToken("listen", 0.5, 0.9, True, language="en"),
                    RecognitionToken("to", 1.0, 1.2, True, language="en"),
                    RecognitionToken("me.", 1.3, 1.6, True, language="en"),
                ),
                begin_pcm=0.0, end_pcm=1.6, language="en",
            ),
        ))
        await pipeline._handle_asr_event(ASREvent(
            "speech_stopped", end_pcm=1.6, item_id="soniox",
        ))
        self.assertEqual([c.src for c in pipeline.store.query(after_seq=0)], ["Chat, listen to me."])
        await pipeline._handle_asr_event(ASREvent(
            "final", text="Chat, listen to me.", item_id="soniox", language="en",
            caption_observation=CaptionObservation(
                "utterance_final", 0, "soniox", stable_text="Chat, listen to me.",
                begin_pcm=0.0, end_pcm=1.6, language="en",
            ),
        ))
        cues = pipeline.store.query(after_seq=0)
        self.assertEqual([cue.src for cue in cues], ["Chat, listen to me."])
        self.assertEqual(cues[0].cut_reason, "terminal_punctuation")

    async def test_pcm_sender_frontier_never_forces_a_caption_commit(self) -> None:
        pipeline = self.make_pipeline(manual_commit=True)
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=0.0, item_id="silent-evidence"))
        stream = FakeStream()
        pipeline._stream = stream
        pipeline._pcm_queue = asyncio.Queue()
        pipeline._running = True
        pipeline._pcm_queue.put_nowait((b"\x00" * (6 * pipeline.pcm_bytes_per_second), 0.0))
        sender = asyncio.create_task(pipeline._pcm_sender())
        for _ in range(20):
            if stream.commits:
                break
            await asyncio.sleep(0)
        pipeline._running = False
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
        self.assertEqual(stream.commits, 0)
        self.assertEqual(pipeline.status()["manualHardCommits"], 0)

        class FailingCommitStream(FakeStream):
            async def commit(self) -> None:
                raise RuntimeError("commit rejected")

        failing_pipeline = self.make_pipeline(manual_commit=True)
        await failing_pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=0.0, item_id="u1"))
        failing_stream = FailingCommitStream()
        await failing_pipeline._advance_caption_frontier(6.0, failing_stream)
        self.assertEqual(failing_pipeline.status()["commitFailures"], 0)
        self.assertTrue(failing_pipeline._manual_commit_ok)
        await failing_pipeline._advance_caption_frontier(12.0, failing_stream)
        self.assertEqual(failing_pipeline.status()["commitFailures"], 0)
        self.assertGreaterEqual(failing_pipeline.status()["hardCapPendingEvidence"], 1)

    async def test_production_pcm_queue_applies_backpressure_without_dropping(self) -> None:
        pipeline = self.make_pipeline()
        pipeline._pcm_queue = asyncio.Queue(maxsize=1)
        pipeline._pcm_queue.put_nowait((b"old", 0.0))
        blocked = asyncio.create_task(pipeline._enqueue_pcm_chunk(b"\x00" * 3200, 0.1))
        await asyncio.sleep(0)
        self.assertFalse(blocked.done())
        pipeline._pcm_queue.get_nowait()
        await asyncio.wait_for(blocked, 1)
        self.assertEqual(pipeline.stats.pcm_dropped, 0)
        self.assertAlmostEqual(pipeline._pcm_offset, 0.2)

    async def test_normalized_stable_prefix_uses_vad_timing_and_suppresses_legacy_prefix_path(self) -> None:
        pipeline = self.make_pipeline(manual_commit=True)
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=1.0, item_id="prefix"))
        pipeline._last_sent_pcm_offset = 5.0
        observation = CaptionObservation(
            "stable_prefix_snapshot", 0, "prefix",
            stable_text="This is confirmed.", tentative_text=" more",
        )
        await pipeline._handle_asr_event(ASREvent(
            "interim", text="This is confirmed.", stash=" more", item_id="prefix",
            caption_observation=observation,
        ))
        await pipeline._handle_asr_event(ASREvent(
            "speech_stopped", end_pcm=5.2, item_id="prefix",
            caption_observation=CaptionObservation("endpoint", 0, "prefix", end_pcm=5.2),
        ))
        cues = pipeline.store.query(after_seq=0)
        self.assertEqual([cue.src for cue in cues], ["This is confirmed."])
        self.assertEqual(cues[0].timing_source, "vad")
        self.assertEqual(pipeline.stats.prefix_cues, 0)
        self.assertEqual(pipeline.status()["manualHardCommits"], 0)

    async def test_stop_flush_is_idempotent_and_restart_resets_generation_order(self) -> None:
        pipeline = self.make_pipeline()
        await pipeline._handle_asr_event(ASREvent(
            "interim", text="stable", item_id="u1",
            caption_observation=CaptionObservation(
                "stable_token_delta", 0, "u1",
                tokens=(RecognitionToken("stable", 0.0, 0.8, True, language="en"),),
            ),
        ))
        first = pipeline._flush_caption_session()
        second = pipeline._flush_caption_session()
        self.assertEqual([cue.src for cue in first], ["stable"])
        self.assertEqual(second, [])
        self.assertEqual(pipeline.status()["residualFlushes"], 1)

        previous_generation = pipeline._generation
        pipeline._begin_generation()
        self.assertEqual(pipeline._generation, previous_generation + 1)
        await pipeline._handle_asr_event(ASREvent(
            "final", text="new", item_id="u1",
            caption_observation=CaptionObservation(
                "utterance_final", 0, "u1", stable_text="new", begin_pcm=2.0, end_pcm=2.8,
            ),
        ))
        pipeline._flush_caption_session()
        newest = pipeline.store.query(after_seq=0)[-1]
        self.assertEqual(newest.generation, pipeline._generation)
        self.assertEqual(newest.chunk_order, 1)

    async def test_normalized_evidence_disables_legacy_max_utterance_commit_timer(self) -> None:
        pipeline = self.make_pipeline(manual_commit=True)
        pipeline.max_utterance_seconds = 6.0
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=0.0, item_id="local"))
        await pipeline._handle_asr_event(ASREvent(
            "interim", text="local stable evidence", item_id="local",
            caption_observation=CaptionObservation(
                "stable_token_delta", 0, "local",
                tokens=(
                    RecognitionToken("local ", 0.0, 2.0, True, language="en"),
                    RecognitionToken("stable ", 2.1, 4.0, True, language="en"),
                    RecognitionToken("evidence", 4.1, 5.9, True, language="en"),
                ),
            ),
        ))
        stream = FakeStream()
        pipeline._last_sent_pcm_offset = 6.1
        await pipeline._advance_caption_frontier(6.1, stream)
        if not pipeline._caption_evidence_seen:
            await pipeline._maybe_force_commit(stream)
        self.assertEqual(stream.commits, 0)
        self.assertEqual(pipeline.status()["manualHardCommits"], 0)

    async def test_chunk_metadata_reaches_existing_translation_request_path(self) -> None:
        provider = RecordingTranslation("translation")
        pipeline = SubtitlePipeline(
            asr_provider=FakeASR(),
            translation_provider=provider,
            cue_store=CueStore(),
            meta=StreamMeta("title", "channel", "gaming", "en", "zh"),
            wall_clock=lambda: 1000.0,
            monotonic=lambda: 50.0,
        )
        pipeline._push_breadcrumbs.append((0.0, 0.0))
        pipeline.media_epoch = 950.0
        await pipeline._handle_asr_event(ASREvent(
            "final", text="unfinished phrase", item_id="meta",
            caption_observation=CaptionObservation(
                "utterance_final", 0, "meta", stable_text="unfinished phrase",
                begin_pcm=0.0, end_pcm=2.0,
            ),
        ))
        pipeline._flush_caption_session()
        pipeline._running = True
        worker = asyncio.create_task(pipeline._translation_worker())
        await asyncio.wait_for(pipeline._translation_queue.join(), 1)
        pipeline._running = False
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        request = provider.requests[0]
        self.assertEqual(request.generation, pipeline._generation)
        self.assertEqual(request.chunk_order, 1)
        self.assertEqual(request.cut_reason, "utterance_endpoint")
        self.assertIsNone(request.ends_mid_sentence)

    async def test_malformed_token_timestamps_fall_back_without_claiming_word_timing(self) -> None:
        pipeline = self.make_pipeline()
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=2.0, item_id="bad"))
        await pipeline._handle_asr_event(ASREvent(
            "final", text="fallback final", item_id="bad",
            caption_observation=CaptionObservation(
                "utterance_final", 0, "bad", stable_text="fallback final",
                tokens=(RecognitionToken("fallback", 4.0, 3.0, True, language="en"),),
                begin_pcm=2.0, end_pcm=4.0,
            ),
        ))
        pipeline._flush_caption_session()
        cue = pipeline.store.query(after_seq=0)[0]
        self.assertNotEqual(cue.timing_source, "asr")
        self.assertEqual(cue.src, "fallback final")


if __name__ == "__main__":
    unittest.main()
