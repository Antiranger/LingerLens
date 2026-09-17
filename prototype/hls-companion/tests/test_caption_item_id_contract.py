"""Regression: an observation with item_id=None must not kill the ASR session.

CaptionChunker builds its continuation-lane key as ``"item:" + unit.item_id``
(caption_chunker.py), which raises ``TypeError: can only concatenate str (not
"NoneType") to str`` for a None item id. Through ``_consume_asr_events`` that
reaches ``_asr_manager``'s ``except Exception``, i.e. it is escalated to a full
ASR reconnect -- and would repeat forever if the Provider kept emitting it.

All 11 shipped Adapters normalize with ``str(item_id or "0")``, so this was
latent; the pipeline now enforces the same contract at the evidence boundary
instead of trusting it.

Reproduced before the fix:

    CRASH utterance_final, item_id=None   TypeError: can only concatenate str
    OK    stable_token_delta, item_id=None
    OK    endpoint, item_id=None
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.caption_chunker import CaptionChunker  # noqa: E402
from companion.providers.base import (  # noqa: E402
    ASRCapabilities,
    ASREvent,
    ASRProvider,
    ASRStream,
    CaptionObservation,
    RecognitionToken,
    StreamMeta,
)
from companion.subtitle_pipeline import SubtitlePipeline  # noqa: E402
from companion.subtitle_store import CueStore  # noqa: E402


class _Stream(ASRStream):
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


class _ASR(ASRProvider):
    id = "none-item-id"
    label = "none"
    model = "none"
    price_per_second_cny = 0.0

    @property
    def capabilities(self) -> ASRCapabilities:
        return ASRCapabilities(True, True, False, True, True, False, False,
                               ("ja",), (16000,), False)

    async def stream(self, *, policy, sample_rate, hotwords, context):
        del policy, sample_rate, hotwords, context
        return _Stream()


def observation(kind: str, **kwargs) -> CaptionObservation:
    return CaptionObservation(kind, 1, None, **kwargs)  # type: ignore[arg-type]


class NoneItemIdIsToleratedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pipeline = SubtitlePipeline(
            asr_provider=_ASR(),
            translation_provider=None,
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "ja", "zh-Hans"),
        )
        self.pipeline._push_breadcrumbs.append((0.0, 0.0))
        self.pipeline.media_epoch = 1_000.0

    async def test_pipeline_maps_a_none_item_id_to_a_stable_placeholder(self) -> None:
        mapped = self.pipeline._map_caption_observation(
            observation("utterance_final", stable_text="こんにちは。",
                        begin_pcm=0.0, end_pcm=1.0, language="ja")
        )
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped.item_id, "0")

    async def test_utterance_final_with_none_item_id_is_handled_by_the_chunker(self) -> None:
        chunker = CaptionChunker(realtime=True)
        chunker.reset(1)
        # The chunker itself is only reachable with a normalized id, so assert
        # the normalized form works and produces the expected single cue.
        decision = chunker.observe(
            CaptionObservation("utterance_final", 1, "0", stable_text="こんにちは。",
                               begin_pcm=0.0, end_pcm=1.0, language="ja"),
            now=1_000.0,
        )
        # The item is closed and its text ends in a terminal, so it is held for the
        # confirmation window rather than published at the endpoint; the window then
        # releases it under its own cut reason.
        self.assertEqual([c.text for c in decision.chunks], [])
        released = chunker.expire(1_000.0 + 1.3)
        self.assertEqual([c.text for c in released.chunks], ["こんにちは。"])
        self.assertEqual(released.chunks[0].cut_reason, "terminal_punctuation")

    async def test_none_item_id_events_never_raise_out_of_the_pipeline(self) -> None:
        """The regression: this used to raise and trigger an ASR reconnect."""
        events = [
            ASREvent("speech_started", begin_pcm=0.0, item_id=None),
            ASREvent("interim", text="こんにちは。", begin_pcm=0.0, end_pcm=1.0,
                     language="ja", item_id=None,
                     caption_observation=observation(
                         "token_snapshot", begin_pcm=0.0, end_pcm=1.0, language="ja",
                         tokens=(RecognitionToken("こんにちは。", 0.0, 1.0, False, "ja"),))),
            ASREvent("final", text="こんにちは。", begin_pcm=0.0, end_pcm=1.0,
                     language="ja", item_id=None,
                     caption_observation=observation(
                         "stable_token_delta", begin_pcm=0.0, end_pcm=1.0, language="ja",
                         tokens=(RecognitionToken("こんにちは。", 0.0, 1.0, True, "ja"),))),
            ASREvent("final", text="", begin_pcm=0.0, end_pcm=1.0,
                     language="ja", item_id=None,
                     caption_observation=observation("endpoint", end_pcm=1.0, language="ja")),
        ]
        for event in events:
            await self.pipeline._handle_asr_event(event)
        # Nothing was dropped as unmappable, so no evidence was lost.
        self.assertEqual(self.pipeline.stats.unmapped_observations, 0)
        self.assertIsNone(self.pipeline.stats.last_error)


if __name__ == "__main__":
    unittest.main()
