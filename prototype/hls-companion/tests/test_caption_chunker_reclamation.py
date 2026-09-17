"""Regression tests for session-length bounds and reclamation.

These lock in the fix for the defect measured across an 8h simulation before the
fix landed:

    utterance#  us/utterance   vs first
          545         562.4      1.00x
         6540       36101.0     64.20x

Cause: every reclamation path in CaptionChunker was gated on ``state.closed``,
which only ``utterance_final`` sets. Providers such as Deepgram emit
token_snapshot / stable_token_delta / endpoint and never utterance_final
(asr_deepgram_streaming.py:126), so ``_items`` and ``_captions`` gained one
permanent entry per utterance, and ``_spans`` was never trimmed at all while
being sorted on every telemetry() call.

These tests drive the Deepgram event shape exclusively: no utterance_final is
ever sent.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.caption_chunker import (  # noqa: E402
    _ITEM_LEDGER_MAX,
    _SPAN_SAMPLE_MAX,
    CaptionChunker,
)
from companion.providers.base import CaptionObservation, RecognitionToken  # noqa: E402

WORDS = ["今日", "は", "こちらの", "会場", "から", "お伝え", "します", "、",
         "現在", "の", "状況", "を", "ご覧", "ください", "。"]


def utterance(item_id: str, begin: float, speaker: str | None = None):
    """The exact event sequence the Deepgram adapter produces for one utterance.

    token_snapshot (interim) -> stable_token_delta (is_final) -> endpoint
    (speech_final/UtteranceEnd). No utterance_final, ever.
    """
    n = 8
    span = 4.0 / n
    tokens: list[RecognitionToken] = []
    events = []
    for i in range(n):
        t0 = begin + i * span
        tokens.append(RecognitionToken(WORDS[i % len(WORDS)], t0, t0 + span, False, "ja", speaker))
        events.append(
            CaptionObservation("token_snapshot", 1, item_id, revision=i,
                               tokens=tuple(tokens), end_pcm=t0 + span,
                               language="ja", speaker=speaker)
        )
    events.append(
        CaptionObservation("stable_token_delta", 1, item_id, revision=n,
                           tokens=tuple(tokens), end_pcm=begin + 4.0,
                           language="ja", speaker=speaker)
    )
    events.append(
        CaptionObservation("endpoint", 1, item_id, revision=n + 1,
                           end_pcm=begin + 4.0, language="ja", speaker=speaker)
    )
    return events


class ReclamationTests(unittest.TestCase):
    def _drive(self, utterances: int, speaker: str | None = None, pace: float = 0.02):
        """Run the Deepgram shape for N utterances and return (chunker, mono)."""
        chunker = CaptionChunker(realtime=True)
        chunker.reset(1)
        mono = 1000.0
        emitted = 0
        for u in range(utterances):
            begin = u * 4.4
            item_id = str(u)
            speaker_label = f"S{u % 3}" if speaker else None
            chunker.open_item(item_id, begin)
            for observation in utterance(item_id, begin, speaker_label):
                chunker.advance_audio(observation.end_pcm or begin)
                mono += pace
                emitted += len(chunker.observe(observation, now=mono).chunks)
                emitted += len(chunker.expire(mono).chunks)
        # The last utterance's tail is still inside its confirmation window.
        mono += 1.3
        emitted += len(chunker.expire(mono).chunks)
        return chunker, mono, emitted

    def test_item_ledger_stays_bounded_without_utterance_final(self) -> None:
        chunker, _mono, emitted = self._drive(4000)
        self.assertGreater(emitted, 0, "the Deepgram shape must publish cues")
        self.assertLessEqual(len(chunker._items), _ITEM_LEDGER_MAX)
        # Before the fix this was exactly one entry per utterance (4000).
        self.assertLess(len(chunker._items), 4000 // 2)

    def test_item_lanes_are_released_once_empty(self) -> None:
        chunker, _mono, _emitted = self._drive(2000)
        # Keyed by item id, so an empty lane can never receive another unit.
        item_lanes = [k for k in chunker._captions if k[0].startswith("item:")]
        self.assertEqual(item_lanes, [])

    def test_speaker_lanes_are_reused_not_accumulated(self) -> None:
        chunker, _mono, _emitted = self._drive(4000, speaker=True)
        # Three rotating labels must not create thousands of lanes.
        self.assertLessEqual(len(chunker._captions), 16)

    def test_span_samples_are_capped_but_total_is_cumulative(self) -> None:
        chunker, _mono, emitted = self._drive(4000)
        self.assertLessEqual(len(chunker._spans), _SPAN_SAMPLE_MAX)
        self.assertEqual(chunker.telemetry().caption_chunks, emitted)

    def test_reclamation_does_not_change_published_text(self) -> None:
        """Bounding memory must not alter what viewers see."""
        chunker = CaptionChunker(realtime=True)
        chunker.reset(1)
        mono = 1000.0
        texts = []
        for u in range(600):
            begin = u * 4.4
            chunker.open_item(str(u), begin)
            for observation in utterance(str(u), begin):
                chunker.advance_audio(observation.end_pcm or begin)
                mono += 0.02
                texts.extend(c.text for c in chunker.observe(observation, now=mono).chunks)
                texts.extend(c.text for c in chunker.expire(mono).chunks)
        # The last utterance's tail is still inside its confirmation window.
        mono += 1.3
        texts.extend(c.text for c in chunker.expire(mono).chunks)
        self.assertEqual(len(texts), 600)
        self.assertTrue(all(t.strip() for t in texts))
        # Every utterance produced the same joined text, so the segmenter is
        # deterministic across reclamation.
        self.assertEqual(len(set(texts)), 1)

    def test_reclamation_survives_a_stalled_clock(self) -> None:
        """A frozen monotonic clock must still honour the hard ledger bound."""
        chunker = CaptionChunker(realtime=True)
        chunker.reset(1)
        for u in range(_ITEM_LEDGER_MAX * 2):
            begin = u * 4.4
            chunker.open_item(str(u), begin)
            for observation in utterance(str(u), begin):
                chunker.observe(observation, now=1000.0)  # never advances
        self.assertLessEqual(len(chunker._items), _ITEM_LEDGER_MAX)


if __name__ == "__main__":
    unittest.main()
