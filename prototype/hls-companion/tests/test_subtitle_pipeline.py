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
from companion.media_anchor import MediaAnchor
from companion.subtitle_pipeline import (
    VIEWER_POSITION_TTL_SECONDS,
    SubtitlePipeline,
)
from companion.subtitle_store import Cue, CueStore


class FakeStream(ASRStream):
    def __init__(self) -> None:
        self.commits = 0
        self.pcm: list[float] = []

    async def push_pcm(self, chunk: bytes, pcm_offset: float) -> None:
        del chunk
        self.pcm.append(pcm_offset)

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


async def deliver_final(
    pipeline: SubtitlePipeline,
    text: str,
    item_id: str,
    *,
    begin: float | None = None,
    end: float | None = None,
    language: str | None = "ja",
    speaker: str | None = None,
    kind: str = "utterance_final",
    stable: bool = True,
) -> list[Cue]:
    """Deliver a Provider final through the normalized CaptionObservation path.

    Mirrors what every shipped Adapter does, so the test exercises the live
    segmentation path (``_handle_asr_event`` -> ``_handle_caption_observation``
    -> ``CaptionChunker``) rather than the deleted ``_handle_final`` fossil.

    ``begin``/``end`` are the timestamps the Provider itself reports for the
    utterance; when both are given the event also carries one RecognitionToken
    spanning them, which is the shape token-level Adapters (Soniox, Deepgram)
    emit and what makes the chunk count as exact ``asr`` timing. Without them
    the observation is text-only and the pipeline maps it onto the utterance's
    own VAD span / the audio frontier.
    """
    tokens: tuple[RecognitionToken, ...] = ()
    if begin is not None and end is not None:
        tokens = (RecognitionToken(text, begin, end, stable, language, speaker),)
    observation = CaptionObservation(
        kind,
        pipeline._generation,
        item_id,
        tokens=tokens,
        stable_text=text,
        begin_pcm=begin,
        end_pcm=end,
        language=language,
        speaker=speaker,
    )
    event = ASREvent(
        "final",
        text=text,
        begin_pcm=begin,
        end_pcm=end,
        language=language,
        item_id=item_id,
        speaker=speaker,
        caption_observation=observation,
    )
    before = len(pipeline.store)
    await pipeline._handle_asr_event(event)
    return list(pipeline.store._cues)[before:]


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
        cues = await deliver_final(pipeline, " ＡＢ。ＣＤＥ ", "a")
        self.assertEqual([cue.src for cue in cues], ["AB。CDE"])
        # The final carries no token timestamps, so its boundaries come from its
        # own item's VAD span and the live path labels that provenance "vad":
        # ``timing_source`` is "asr" only for chunks whose every unit has exact
        # token timing (subtitle_pipeline.py ``_queue_caption_chunk``, which
        # replaced the deleted _handle_final's span-junction labelling). The
        # boundaries themselves are unchanged and still distinguish the VAD
        # span from the 8.0s sent-audio frontier.
        self.assertEqual(cues[0].timing_source, "vad")
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
        first = await deliver_final(pipeline, "さいしょ", "a")
        await pipeline._handle_asr_event(ASREvent("speech_stopped", end_pcm=9.0, item_id="b"))
        second = await deliver_final(pipeline, "つぎです", "b")
        self.assertEqual((first[0].t_start, first[0].t_end), (951.0, 954.0))
        self.assertEqual((second[0].t_start, second[0].t_end), (954.0, 959.0))
        # Both finals are text-only, so each cue is timed by its own item's VAD
        # span and the live path records that provenance as "vad"
        # (subtitle_pipeline.py ``_queue_caption_chunk``; the deleted
        # _handle_final labelled the same boundaries "asr").
        # Only two provenances exist: CaptionChunk.begin_pcm is a non-optional
        # float, so the former third state "approx" was unreachable and is gone.
        self.assertEqual(pipeline.status()["timingSourceCounts"], {"asr": 0, "vad": 2})

    async def test_duplicate_final_for_the_same_item_publishes_only_one_cue(self) -> None:
        pipeline = self.make_pipeline()
        self.prime_timeline(pipeline)
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=1.0, item_id="a"))
        await pipeline._handle_asr_event(ASREvent("speech_stopped", end_pcm=2.0, item_id="a"))
        pipeline._last_sent_pcm_offset = 2.0
        first = await deliver_final(pipeline, "うん。", "a")
        self.assertEqual([cue.src for cue in first], ["うん。"])

        # The live guarantee that replaced the deleted text+recency dedup
        # (`stats.final_deduplicated`, `_previous_final*`): a Provider resend of
        # the same final for the SAME item id cannot publish a second cue,
        # because CaptionChunker closes the item on its `utterance_final`
        # observation and returns an empty decision for further evidence
        # (caption_chunker.py ``observe``, the `state.closed` guard).
        pipeline._last_sent_pcm_offset = 2.5
        self.assertEqual(await deliver_final(pipeline, "うん。", "a"), [])
        self.assertEqual(len(pipeline.store), 1)

        # A genuinely new utterance that happens to repeat the same words is
        # not suppressed: the dedup key is the item id, never "same text
        # recently", so a real second "うん。" still reaches the viewer.
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=20.0, item_id="b"))
        await pipeline._handle_asr_event(ASREvent("speech_stopped", end_pcm=21.0, item_id="b"))
        pipeline._last_sent_pcm_offset = 21.0
        again = await deliver_final(pipeline, "うん。", "b")
        self.assertEqual([cue.src for cue in again], ["うん。"])
        self.assertEqual(len(pipeline.store), 2)

    async def test_same_short_text_from_different_speakers_is_not_deduplicated(self) -> None:
        pipeline = self.make_pipeline()
        self.prime_timeline(pipeline)
        pipeline._last_sent_pcm_offset = 2.0
        first = await deliver_final(pipeline, "はい。", "a", speaker="speaker-1")
        pipeline._last_sent_pcm_offset = 2.4
        second = await deliver_final(pipeline, "はい。", "b", speaker="speaker-2")
        self.assertEqual([cue.speaker for cue in first + second], ["speaker-1", "speaker-2"])
        # `stats.final_deduplicated` was deleted with the legacy text dedup it
        # counted; the live equivalent guarantee is that both cues reach the
        # store, since dedup is keyed by item id under CaptionChunker
        # (caption_chunker.py ``observe``) and never by text or speaker.
        self.assertEqual(len(pipeline.store), 2)

    async def test_final_without_vad_span_falls_back_to_approx(self) -> None:
        pipeline = self.make_pipeline()
        self.prime_timeline(pipeline)
        pipeline._last_sent_pcm_offset = 3.0
        cues = await deliver_final(pipeline, "孤立", "zzz")
        # The observation was still mapped and published, not dropped as
        # unmapped evidence (the counter that replaced `stats.unjoined_finals`).
        self.assertEqual(pipeline.stats.unmapped_observations, 0)
        self.assertEqual(len(cues), 1)
        # A final with no VAD span is no longer labelled "approx" (the name is
        # kept so the deleted mechanism's coverage stays traceable): the live
        # chunker substitutes the lane's 0.0 origin for a missing unit begin
        # (caption_chunker.py ``_chunk_times``), so the start is the pipeline
        # origin and the end is the audio frontier that
        # ``_map_caption_observation`` used as the fallback end. The deleted
        # _handle_final left t_start None for this case instead.
        self.assertEqual(cues[0].timing_source, "vad")
        self.assertEqual(cues[0].t_start, 950.0)
        self.assertAlmostEqual(cues[0].t_end, 953.0)

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
        cues = await deliver_final(pipeline, "こんにちは", "a")
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
        # An item id is mandatory on the live path: CaptionChunker keys its
        # per-utterance lanes by it (caption_chunker.py ``observe``), and every
        # shipped Adapter supplies one. The deleted _handle_final tolerated a
        # missing id because it keyed nothing.
        cues = await deliver_final(pipeline, "こんにちは", "a")
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

    async def emit_final(self, pipeline, begin=1.0, end=2.0, text="こんにちは", item_id="a"):
        """Deliver one utterance as its VAD span plus its normalized final.

        A second utterance in the same test needs its own ``item_id``: the live
        CaptionChunker closes an item on its ``utterance_final`` and ignores
        later evidence for it (caption_chunker.py ``observe``), which is how a
        Provider's real per-utterance ids behave. The deleted ``_handle_final``
        accepted a repeated id because it kept no per-item ledger.
        """
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=begin, item_id=item_id))
        await pipeline._handle_asr_event(ASREvent("speech_stopped", end_pcm=end, item_id=item_id))
        pipeline._last_sent_pcm_offset = end
        return await deliver_final(pipeline, text, item_id)

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
        second = await self.emit_final(pipeline, begin=10.0, end=11.0, text="つぎ", item_id="b")
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
        second = await self.emit_final(pipeline, begin=3.0, end=4.0, text="つぎ", item_id="b")
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
        # Production path: the async enqueue owns the PCM clock (the synchronous
        # _ingest_pcm_chunk, which could accept a chunk with no queue, is gone).
        pipeline._pcm_queue = asyncio.Queue(maxsize=pipeline.pcm_queue_chunks)
        # 4800 bytes at 24 kHz 16-bit mono is exactly 0.1s of audio.
        await pipeline._enqueue_pcm_chunk(b"\x00" * 4800, 0.0)
        self.assertAlmostEqual(pipeline._pcm_offset, 0.1)
        await pipeline._enqueue_pcm_chunk(b"\x00" * 48000, pipeline._pcm_offset)
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
        pipeline._pcm_queue = asyncio.Queue(maxsize=pipeline.pcm_queue_chunks)
        await pipeline._enqueue_pcm_chunk(b"\x00" * 3200, 0.0)
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
        cues = await deliver_final(pipeline, "Bonjour le monde", "a", language="fr")
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
        cues = await deliver_final(pipeline, "你好", "a", language="zh-TW")
        self.assertEqual(cues[0].lang, "zh-Hant")

    async def test_unreported_language_falls_back_to_specified_or_preferred(self) -> None:
        specified = self.make_pipeline(source_policy=SourceLanguagePolicy.specified("en"))
        specified._last_sent_pcm_offset = 2.0
        cues = await deliver_final(specified, "no report", "a", language=None)
        self.assertEqual(cues[0].lang, "en")

        detected = self.make_pipeline(
            source_policy=SourceLanguagePolicy.detect(candidates=("fr", "en"), preferred="fr")
        )
        detected._last_sent_pcm_offset = 2.0
        cues = await deliver_final(detected, "no report", "a", language=None)
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

    async def test_a_silent_provider_never_stalls_the_audio_feed(self) -> None:
        """Evidence is speech-gated, so it cannot be a flow-control signal.

        Both flow-control attempts on 2026-09-18 were measured wrong. Pacing to the
        renderer's playhead drained the ASR queue but ate the caption's own margin
        (median 0.66s late against 0.23s). Bounding the queue by the provider's
        confirmation was worse: during music or game audio nothing is confirmed,
        the feed was held back, and the next sentence reached a starved provider.
        Same Bilibili room, same 125s: 34 captions with the bound off against 3
        with it on, asrAdapterDelay P50 1.3s against 6.4s.

        So the sender hands over everything the queue gives it, and reports the
        queue instead of acting on it.
        """
        pipeline = SubtitlePipeline(
            asr_provider=FakeASR(),
            translation_provider=self.ControlledTranslation(),
            cue_store=CueStore(),
            meta=StreamMeta("title", "channel", "gaming", "en", "es"),
            translation_workers=1,
        )
        stream = FakeStream()
        pipeline._stream = stream
        pipeline._pcm_queue = asyncio.Queue()
        pipeline._running = True
        # The provider confirmed a position long ago and has said nothing since --
        # the case that used to hold the feed back.
        pipeline._asr_evidence_pcm = -30.0
        data = b"\x00\x01" * (pipeline_module.PCM_CHUNK_BYTES // 2)
        for index in range(6):
            pipeline._pcm_queue.put_nowait((data, index * pipeline_module.PCM_CHUNK_SECONDS))

        sender = asyncio.ensure_future(pipeline._pcm_sender())
        try:
            for _ in range(200):
                if len(stream.pcm) == 6:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(len(stream.pcm), 6, "a silent provider must not stall the feed")
            self.assertAlmostEqual(pipeline._last_sent_pcm_offset, 6 * pipeline_module.PCM_CHUNK_SECONDS, places=6)
            # The queue is still reported, so the measurement survives the removal.
            self.assertEqual(
                round(pipeline._last_sent_pcm_offset - pipeline._asr_evidence_pcm, 3),
                round(6 * pipeline_module.PCM_CHUNK_SECONDS + 30.0, 3),
            )
        finally:
            pipeline._running = False
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)

    async def test_the_playhead_report_is_measurement_only(self) -> None:
        """The stated playhead is what the caption's margin can be read from.

        It arrives with the status poll the page already sends once a second, and
        it throttles nothing: the feed's bound is the ASR's own queue.
        """
        clock = [100.0]
        pipeline = SubtitlePipeline(
            asr_provider=FakeASR(),
            translation_provider=self.ControlledTranslation(),
            cue_store=CueStore(),
            meta=StreamMeta("title", "channel", "gaming", "en", "es"),
            translation_workers=1,
            wall_clock=lambda: 1_000.0 + (clock[0] - 100.0),
            monotonic=lambda: clock[0],
        )
        pipeline.media_epoch = 1_000.0
        pipeline.media_anchor = MediaAnchor()
        pipeline.media_anchor.set_exact_offset(lambda: 5.0)
        # Audio handed over to 300s of the PCM timeline == 1305s of wall clock.
        pipeline._last_sent_pcm_offset = 300.0

        # Playhead wall 1180 -> PCM 175: the feed leads the viewer by 125s.
        pipeline.set_viewer_wall_time(1_180.0)
        self.assertEqual(pipeline.viewer_lead_seconds(), 125.0)

        # A stale statement is not a reference: reporting reverts to "unknown"
        # rather than describing a playhead from eight seconds ago.
        clock[0] += VIEWER_POSITION_TTL_SECONDS + 0.1
        self.assertIsNone(pipeline.viewer_lead_seconds())

        # Garbage does not replace a trustworthy statement ...
        clock[0] += 1.0
        pipeline.set_viewer_wall_time(1_180.0)
        self.assertEqual(pipeline.viewer_lead_seconds(), 125.0)
        pipeline.set_viewer_wall_time("not a number")
        pipeline.set_viewer_wall_time(float("nan"))
        self.assertEqual(pipeline.viewer_lead_seconds(), 125.0)

        # ... and with nothing trustworthy on record, a wildly wrong clock is
        # refused instead of becoming the reference (a reloaded page, a stale tab).
        clock[0] += VIEWER_POSITION_TTL_SECONDS + 1.0
        pipeline.set_viewer_wall_time(1_000.0 + 5_000.0)
        self.assertIsNone(pipeline.viewer_lead_seconds())

    async def test_a_cue_past_its_estimated_window_still_reaches_the_provider(self) -> None:
        """The backend estimates lateness; only the renderer can know it.

        ``audio_end_wall`` 20s behind a 15s delay is the session-head case that
        used to drop the caption here, with no provider call, while the renderer
        still had seconds of window left for it.
        """
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
            monotonic=time.monotonic,
        )
        cue = pipeline.store.add(
            t_start=0.0, t_end=1.0, hold=1.2, src="late", lang="en", timing_source="asr",
            generation=1, chunk_order=1,
        )
        pipeline._audio_end_walls[cue.id] = 1_000.0
        pipeline._running = True
        worker = asyncio.create_task(pipeline._translation_worker())
        try:
            pipeline._enqueue_translation(cue)

            await asyncio.wait_for(provider.started.setdefault(1, asyncio.Event()).wait(), 1)
            self.assertEqual(pipeline.stats.translation_dropped, 0)
            self.assertEqual(pipeline.stats.translation_deadline_expired, 0)
            self.assertEqual(pipeline.stats.translation_failures, 0)

            provider.releases.setdefault(1, asyncio.Event()).set()
            await asyncio.wait_for(pipeline._translation_queue.join(), 1)
            self.assertEqual(pipeline.store.get(cue.id).state, "done")
            self.assertEqual(pipeline.store.get(cue.id).zh, "z1")
        finally:
            pipeline._running = False
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

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

    def make_pipeline(self) -> SubtitlePipeline:
        pipeline = SubtitlePipeline(
            asr_provider=FakeASR(),
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
        pipeline = self.make_pipeline()
        await pipeline._handle_asr_event(ASREvent("speech_started", begin_pcm=0.0, item_id="silent-evidence"))
        stream = FakeStream()
        pipeline._stream = stream
        pipeline._pcm_queue = asyncio.Queue(maxsize=pipeline.pcm_queue_chunks)
        pipeline._running = True
        pipeline._pcm_queue.put_nowait((b"\x00" * (6 * pipeline.pcm_bytes_per_second), 0.0))
        sender = asyncio.create_task(pipeline._pcm_sender())
        # Wait for the sender to drain the chunk and report the frontier. The
        # bound keeps a sender that stops advancing the frontier from hanging
        # the test; the assertions below then fail instead.
        for _ in range(200):
            if pipeline.status()["pendingEvidenceOverSoftSpan"]:
                break
            await asyncio.sleep(0)
        pipeline._running = False
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
        # Pushing 6s of audio past an open utterance with no lexical evidence
        # must not ask the Provider for a hard commit, and must not publish a
        # caption either: the frontier path only reports pending evidence
        # (SubtitlePipeline._advance_caption_frontier ->
        # CaptionChunker.advance_audio). The deleted `manualHardCommits` counter
        # has no live equivalent; the surviving observable is the informational
        # pending-evidence telemetry that replaced `hardCapPendingEvidence`.
        self.assertEqual(stream.commits, 0)
        self.assertEqual(pipeline.store.query(after_seq=0), [])
        self.assertEqual(pipeline.status()["pendingEvidenceOverSoftSpan"], 1)

        class CommitRejectingStream(FakeStream):
            async def commit(self) -> None:
                raise RuntimeError("commit rejected")

        # A Provider that would reject a commit is never asked either, and an
        # item already counted is not counted twice: advancing the frontier
        # again changes neither the store nor the commit count.
        rejecting = CommitRejectingStream()
        await pipeline._advance_caption_frontier(12.0, rejecting)
        self.assertEqual(rejecting.commits, 0)
        self.assertEqual(pipeline.status()["pendingEvidenceOverSoftSpan"], 1)
        self.assertEqual(pipeline.store.query(after_seq=0), [])

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

    async def test_normalized_stable_prefix_uses_vad_timing(self) -> None:
        pipeline = self.make_pipeline()
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
        # The observation carries no token timestamps, so the chunk is timed by
        # its VAD onset and by the audio frontier that was current when the
        # stable prefix arrived (SubtitlePipeline._map_caption_observation),
        # and it publishes without waiting for an utterance final.
        self.assertAlmostEqual(cues[0].t_start, 951.0)
        self.assertAlmostEqual(cues[0].t_end, 955.0)
        self.assertEqual(pipeline.stats.unmapped_observations, 0)
        # `stats.prefix_cues` and the `manualHardCommits` status key were deleted
        # with the legacy prefix-split and manual-commit mechanisms they
        # counted, so the only assertions that no longer described live
        # behaviour are those two "this old path stayed at zero" checks; every
        # other assertion above is unchanged.

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
