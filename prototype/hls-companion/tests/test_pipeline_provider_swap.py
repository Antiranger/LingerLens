"""Regression test: a runtime provider hot-swap must resolve cues terminally.

``server.py:551-553`` sets both ``translation_provider`` and
``fallback_translation_provider`` to None while translation workers are running.
A cue enqueued before the swap but dequeued after it hits:

    subtitle_pipeline.py:1340-1341
        if provider is None:
            continue        # finally runs task_done(), but no terminal outcome

``_record_ready_lag`` is the only place that pops ``_cue_latencies`` and
``_audio_end_walls``, and it was skipped, so the cue stayed in state "src"
forever (the viewer sees source text with no translation) while both dicts grew.

Measured before the fix, with a 60-cue backlog and 4 busy workers:

    store=60  states={'done': 4, 'failed': 40, 'src': 16}
    _cue_latencies=16  _audio_end_walls=16
    translation queue unfinished tasks = 0   <- task_done() did run
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
import time
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.providers.base import (  # noqa: E402
    ASRCapabilities,
    ASREvent,
    ASRProvider,
    ASRStream,
    CaptionObservation,
    RecognitionToken,
    StreamMeta,
    TranslationCapabilities,
    TranslationProvider,
    TranslationRequest,
    TranslationResult,
)
from companion.subtitle_pipeline import SubtitlePipeline  # noqa: E402
from companion.subtitle_store import CueStore  # noqa: E402

WORDS = ["今日", "は", "こちらの", "会場", "から", "お伝え", "します", "、",
         "現在", "の", "状況", "を", "ご覧", "ください", "。"]


class FakeStream(ASRStream):
    async def push_pcm(self, chunk, pcm_offset):
        del chunk, pcm_offset

    async def flush(self):
        return None

    async def commit(self):
        return None

    def __aiter__(self):
        async def events():
            if False:
                yield None
        return events()

    async def aclose(self):
        return None


class FakeASR(ASRProvider):
    id = "fake-asr"
    label = "fake"
    model = "fake"
    price_per_second_cny = 0.0

    @property
    def capabilities(self) -> ASRCapabilities:
        return ASRCapabilities(True, True, False, True, True, False, False,
                               ("ja",), (16000,), False)

    async def stream(self, *, policy, sample_rate, hotwords, context):
        del policy, sample_rate, hotwords, context
        return FakeStream()


class SlowTranslation(TranslationProvider):
    """Slow enough that a backlog exists when the swap happens."""

    def __init__(self, latency: float = 0.5) -> None:
        self.id = "mt"
        self.label = "mt"
        self.model = "mt"
        self.latency = latency

    @property
    def capabilities(self) -> TranslationCapabilities:
        return TranslationCapabilities(True, True, True, False, 1000)

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        await asyncio.sleep(self.latency)
        return TranslationResult("译:" + request.source_text, "mt", 500, None)


def observations_for(item_id: str, begin: float, generation: int):
    n = 8
    span = 4.0 / n
    tokens: list[RecognitionToken] = []
    events = []
    for i in range(n):
        t0 = begin + i * span
        tokens.append(RecognitionToken(WORDS[i % len(WORDS)], t0, t0 + span, False, "ja", None))
        events.append(dataclasses.replace(
            ASREvent("interim", text="".join(t.text for t in tokens), begin_pcm=begin,
                     end_pcm=t0 + span, language="ja", item_id=item_id),
            caption_observation=CaptionObservation(
                "token_snapshot", generation, item_id, revision=i, tokens=tuple(tokens),
                begin_pcm=begin, end_pcm=t0 + span, language="ja")))
    events.append(dataclasses.replace(
        ASREvent("final", text="".join(t.text for t in tokens), begin_pcm=begin,
                 end_pcm=begin + 4.0, language="ja", item_id=item_id),
        caption_observation=CaptionObservation(
            "stable_token_delta", generation, item_id, revision=n, tokens=tuple(tokens),
            begin_pcm=begin, end_pcm=begin + 4.0, language="ja")))
    events.append(dataclasses.replace(
        ASREvent("final", text="", begin_pcm=begin, end_pcm=begin + 4.0,
                 language="ja", item_id=item_id),
        caption_observation=CaptionObservation(
            "endpoint", generation, item_id, revision=n + 1,
            begin_pcm=begin, end_pcm=begin + 4.0, language="ja")))
    return events


class ProviderHotSwapTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pipeline = SubtitlePipeline(
            asr_provider=FakeASR(),
            translation_provider=SlowTranslation(),
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "ja", "zh-Hans"),
            translation_workers=4,
            translation_timeout_seconds=6.0,
            playback_delay_seconds=lambda: 5.0,
        )
        # Identity ASR-clock -> pipeline-clock mapping, as a drop-free session has.
        self.pipeline._push_breadcrumbs.append((0.0, 0.0))
        self.pipeline.media_epoch = time.time() - 5.0
        self.pipeline._running = True
        self.pipeline._stream = FakeStream()
        self.workers = [
            asyncio.create_task(self.pipeline._translation_worker(), name=f"mt-{i}")
            for i in range(4)
        ]

    async def asyncTearDown(self) -> None:
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.pipeline._running = False

    async def _queue(self, count: int) -> None:
        for index in range(count):
            begin = index * 4.4
            self.pipeline._pcm_offset = max(self.pipeline._pcm_offset, begin + 4.0)
            for event in observations_for(f"u{index}", begin, self.pipeline._generation):
                await self.pipeline._handle_asr_event(event)
            await asyncio.sleep(0)

    async def test_hot_swap_leaves_no_cue_without_a_terminal_state(self) -> None:
        await self._queue(60)
        # What server.py:551-553 does while workers are mid-flight.
        self.pipeline.translation_provider = None
        self.pipeline.fallback_translation_provider = None
        await asyncio.sleep(3.0)

        states = Counter(cue.state for cue in self.pipeline.store._cues)
        stuck = states.get("src", 0) + states.get("translating", 0)
        self.assertEqual(stuck, 0, f"cues left non-terminal: {dict(states)}")

    async def test_hot_swap_retains_no_latency_or_deadline_state(self) -> None:
        await self._queue(60)
        self.pipeline.translation_provider = None
        self.pipeline.fallback_translation_provider = None
        await asyncio.sleep(3.0)

        self.assertEqual(len(self.pipeline._cue_latencies), 0)
        self.assertEqual(len(self.pipeline._audio_end_walls), 0)
        self.assertEqual(len(self.pipeline._translation_budgets), 0)
        self.assertEqual(self.pipeline._translation_queue.qsize(), 0)
        # task_done() balanced every get().
        self.assertEqual(self.pipeline._translation_queue._unfinished_tasks, 0)

    async def test_cues_are_marked_failed_not_silently_dropped(self) -> None:
        await self._queue(20)
        self.pipeline.translation_provider = None
        await asyncio.sleep(2.0)
        states = Counter(cue.state for cue in self.pipeline.store._cues)
        self.assertGreater(
            states.get("failed", 0),
            0,
            "an unservable cue must be resolved as failed, not left in src",
        )


if __name__ == "__main__":
    unittest.main()
