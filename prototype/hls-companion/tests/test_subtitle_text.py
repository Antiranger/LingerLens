from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("lingerlens_subtitle_text", ROOT / "companion" / "subtitle_text.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class SubtitleTextTests(unittest.TestCase):
    def test_normalizes_whitespace_and_width(self) -> None:
        self.assertEqual(MODULE.clean_subtitle_text("　ＡＢＣ　　です　"), "ABC です")

    def test_removes_asr_markers_and_noise_but_keeps_semantic_tags(self) -> None:
        self.assertEqual(MODULE.clean_subtitle_text("<|ja|> [BGM] こんにちは (笑) (音楽)"), "こんにちは (笑)")
        self.assertEqual(MODULE.clean_subtitle_text("[Music] hello"), "hello")

    def test_folds_repeated_punctuation(self) -> None:
        self.assertEqual(MODULE.clean_subtitle_text("本当。。。 ね、、 wow!!!"), "本当… ね、 wow!")

    def test_discards_short_symbol_only_and_single_filler(self) -> None:
        for text in ("a", "！？…", "えー", "あの", "うーん", "はい", "[BGM]"):
            with self.subTest(text=text):
                self.assertIsNone(MODULE.clean_subtitle_text(text))
        self.assertEqual(MODULE.clean_subtitle_text("はい、行きます"), "はい、行きます")

    def test_deduplicates_exact_and_near_prefix_resends(self) -> None:
        self.assertTrue(MODULE.is_duplicate_final("こんにちは", "こんにちは"))
        self.assertTrue(MODULE.is_duplicate_final("こんにち", "こんにちは"))
        self.assertFalse(MODULE.is_duplicate_final("こん", "こんにちは"))
        self.assertFalse(MODULE.is_duplicate_final("こんにちはね", "こんにちは"))

    def test_newly_confirmed_sentences_cut_at_last_boundary(self) -> None:
        prefix = "待って、何よ。マジは当たり前のようにね、"
        # Everything through the LAST strong boundary is complete; the open
        # tail after it is not returned.
        self.assertEqual(MODULE.newly_confirmed_sentences(prefix), "待って、何よ。")
        # Same-instant confirmations group into one slice.
        burst = "そうそうそう。一応神だったよねってことで。じゃ惜しかった。"
        self.assertEqual(MODULE.newly_confirmed_sentences(burst), burst)
        self.assertIsNone(MODULE.newly_confirmed_sentences("まだ終わってない話の続き、"))
        self.assertIsNone(MODULE.newly_confirmed_sentences(""))

    def test_newly_confirmed_sentences_returns_only_text_beyond_emitted(self) -> None:
        prefix = "待って、何よ。マジは当たり前のようにね、完璧。"
        # Both sentences are complete in the same confirmation: one grouped
        # slice through the LAST boundary (matching the real event stream,
        # where a batch confirms several sentences at once).
        first = MODULE.newly_confirmed_sentences(prefix)
        self.assertEqual(first, prefix)
        # Nothing new completed yet.
        self.assertIsNone(MODULE.newly_confirmed_sentences(prefix + "その次の文", already_emitted=first))
        # A rewritten prefix that no longer contains the emitted text is not
        # new material; callers treat it as a rewrite.
        self.assertIsNone(MODULE.newly_confirmed_sentences("別の文に変わった。", already_emitted=first))

    def test_splits_at_strong_then_weak_boundaries_and_hard_limit(self) -> None:
        strong = "あ" * 40 + "。" + "い" * 50
        self.assertEqual(MODULE.split_subtitle_text(strong), ["あ" * 40 + "。", "い" * 50])
        weak = "あ" * 50 + "、" + "い" * 40
        self.assertEqual(MODULE.split_subtitle_text(weak), ["あ" * 50 + "、", "い" * 40])
        hard = "あ" * 161
        self.assertEqual([len(part) for part in MODULE.split_subtitle_text(hard)], [80, 80, 1])

    def test_interpolates_split_timing_by_character_ratio(self) -> None:
        parts = MODULE.split_with_timing("a" * 80 + "b" * 20, 10.0, 20.0)
        self.assertEqual(len(parts), 2)
        self.assertAlmostEqual(parts[0].begin_pcm, 10.0)
        self.assertAlmostEqual(parts[0].end_pcm, 18.0)
        self.assertAlmostEqual(parts[1].begin_pcm, 18.0)
        self.assertAlmostEqual(parts[1].end_pcm, 20.0)

    def test_calculates_clamped_hold_using_translation_then_source(self) -> None:
        self.assertEqual(MODULE.calculate_hold("短句"), 1.2)
        self.assertEqual(MODULE.calculate_hold("x" * 100), 6.0)
        self.assertEqual(MODULE.calculate_hold("x" * 200), 8.0)
        self.assertEqual(MODULE.calculate_hold("x" * 100, "中文"), 1.2)


if __name__ == "__main__":
    unittest.main()
