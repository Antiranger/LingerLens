from __future__ import annotations

import sys
import dataclasses
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from companion.caption_chunker import CaptionChunker
from companion.providers.base import CaptionObservation, RecognitionToken


def evidence(text, start, end, *, item="u1", speaker="a", final=False, language="ja"):
    return CaptionObservation(
        "utterance_final" if final else "stable_token_delta", 1, item,
        tokens=(RecognitionToken(text, start, end, True, language=language, speaker=speaker),),
        stable_text=text if final else "", begin_pcm=start, end_pcm=end, language=language, speaker=speaker,
    )


class ClauseCaptionTests(unittest.TestCase):
    def test_colloquial_final_no_question_is_complete(self):
        for text in ("え、もう、もう食べないの？もう飲まないの？", "エキーちゃん行かないの？"):
            with self.subTest(text=text):
                c = CaptionChunker()
                out = c.observe(evidence(text, 0, 2, final=True)).chunks
                self.assertEqual([x.text for x in out], [text])
                self.assertEqual(out[0].cut_reason, "terminal_punctuation")

    def test_question_rule_does_not_split_nominalizer_or_auxiliary(self):
        for left, right in (("食べないの？", "が不思議です。"), ("検索して？", "も見つかりません。")):
            with self.subTest(left=left):
                c = CaptionChunker()
                out = c.observe(CaptionObservation("stable_token_delta", 1, "u", tokens=(
                    RecognitionToken(left, 0, 1, True, language="ja"),
                    RecognitionToken(right, 1, 2, True, language="ja"),
                ))).chunks
                self.assertEqual([x.text for x in out], [left + right])

    def test_english_sentence_ending_in_number_does_not_wait(self):
        for text in ("The biggest one that was the pivotal turning point was round 19.", "We finished in 2026."):
            with self.subTest(text=text):
                c = CaptionChunker()
                self.assertEqual([x.text for x in c.observe(evidence(text, 0, 2, language="en", final=True)).chunks], [text])

    def test_numeric_period_protection_keeps_decimal_enumeration_and_abbreviation(self):
        for left, right in (("The value is 3.", "14 today."), ("19.", "Next item."), ("U.S.", "policy changed.")):
            with self.subTest(left=left):
                c = CaptionChunker()
                out = c.observe(CaptionObservation("stable_token_delta", 1, "u", tokens=(
                    RecognitionToken(left, 0, 1, True, language="en"),
                    RecognitionToken(right, 1, 2, True, language="en"),
                ))).chunks
                self.assertEqual(len(out), 1)
                self.assertEqual((out[0].begin_pcm, out[0].end_pcm), (0, 2))

    def test_colloquial_causal_clause_and_independent_reactions_do_not_wait(self):
        for text in ("楽しそうにやっておりましたんで、", "全然ギュウギュウギュウ。", "えー。", "ほら。", "美味しくなれとかね。"):
            with self.subTest(text=text):
                c = CaptionChunker()
                self.assertEqual([x.text for x in c.observe(evidence(text, 0, 2)).chunks], [text])
        c = CaptionChunker()
        self.assertEqual(c.observe(evidence("配信の予定はね。", 0, 2)).chunks, ())

    def test_finite_sentence_before_demo_is_separate_but_te_form_is_not(self):
        c = CaptionChunker()
        obs = CaptionObservation("stable_token_delta", 1, "u", tokens=(
            RecognitionToken("今日は続けます。", 0, 1, True, language="ja", speaker="a"),
            RecognitionToken("でも遅延には注意します。", 1, 2, True, language="ja", speaker="a"),
        ))
        self.assertEqual([x.text for x in c.observe(obs).chunks],
                         ["今日は続けます。", "でも遅延には注意します。"])
        c = CaptionChunker()
        obs = dataclasses.replace(obs, tokens=(
            RecognitionToken("検索して。", 0, 1, True, language="ja", speaker="a"),
            RecognitionToken("も見つかりません。", 1, 2, True, language="ja", speaker="a"),
        ))
        self.assertEqual([x.text for x in c.observe(obs).chunks], ["検索して。も見つかりません。"])

    def test_identical_simultaneous_words_keep_both_speakers(self):
        c = CaptionChunker()
        obs = CaptionObservation("stable_token_delta", 1, "u", tokens=tuple(
            RecognitionToken("はい。", 0, 1, True, language="ja", speaker=speaker)
            for speaker in ("a", "b")
        ))
        self.assertEqual([(x.text, x.speaker) for x in c.observe(obs).chunks],
                         [("はい。", "a"), ("はい。", "b")])

    def test_generation_reset_discards_pending_old_speaker_text(self):
        c = CaptionChunker()
        c.observe(evidence("私は", 0, 1, final=True))
        c.reset(2)
        self.assertEqual(c.observe(evidence("古いです。", 1, 2, item="old")).chunks, ())
        out = c.observe(dataclasses.replace(evidence("始めます。", 0, 1, item="new"), generation=2)).chunks
        self.assertEqual([x.text for x in out], ["始めます。"])

    def test_closed_item_ledger_does_not_grow_with_completed_sentences(self):
        c = CaptionChunker()
        for i in range(2000):
            out = c.observe(evidence("できました。", i, i+1, item=str(i), final=True)).chunks
            self.assertEqual(len(out), 1)
        self.assertLessEqual(len(c._items), 512)
        self.assertEqual(len(c._captions), 1)
        self.assertEqual(sum(len(s.units) for s in c._captions.values()), 0)

    def test_recognition_final_does_not_cut_a_topic_fragment(self):
        c = CaptionChunker()
        self.assertEqual(c.observe(evidence("で、頑張り度はね。", 0, 1.2, final=True)).chunks, ())
        chunks = c.observe(evidence("めちゃくちゃ頑張ってた。", 1.3, 3.2, item="u2", final=True)).chunks
        self.assertEqual([x.text for x in chunks], ["で、頑張り度はね。めちゃくちゃ頑張ってた。"])
        self.assertEqual((chunks[0].begin_pcm, chunks[0].end_pcm), (0, 3.2))

    def test_six_seconds_cannot_cut_an_incomplete_clause(self):
        c = CaptionChunker()
        self.assertEqual(c.observe(evidence("昨日からずっと楽しみにしていた新しいゲームを", 0, 6)).chunks, ())
        decision = c.advance_audio(10)
        # Audio advancement reports pending evidence; it never force-cuts.
        self.assertTrue(decision.pending_evidence)
        self.assertEqual(decision.chunks, ())
        chunks = c.observe(evidence("ようやく始められたので、", 6, 8.4)).chunks
        self.assertEqual([x.text for x in chunks], ["昨日からずっと楽しみにしていた新しいゲームをようやく始められたので、"])

    def test_short_complete_sentence_is_not_held_until_three_seconds(self):
        c = CaptionChunker()
        out = c.observe(evidence("嘘でしょ。", 0, 1.2)).chunks
        self.assertEqual([x.text for x in out], ["嘘でしょ。"])

    def test_overlapping_speakers_do_not_consume_each_others_words(self):
        c = CaptionChunker()
        c.observe(evidence("私は", 0, 1, speaker="a"))
        b = c.observe(evidence("先に行きます。", .5, 2, item="b", speaker="b", final=True)).chunks
        a = c.observe(evidence("残ります。", 1, 2.5, item="a2", speaker="a", final=True)).chunks
        self.assertEqual([x.text for x in b+a], ["先に行きます。", "私は残ります。"])
        self.assertEqual([x.speaker for x in b+a], ["b", "a"])
        self.assertEqual([(x.begin_pcm,x.end_pcm) for x in b+a], [(.5,2),(0,2.5)])

    def test_unknown_speakers_are_not_merged_across_items(self):
        c = CaptionChunker()
        self.assertEqual(c.observe(evidence("私は", 0, 1, speaker=None, final=True)).chunks, ())
        out = c.observe(evidence("先に行きます。", .5, 2, item="other", speaker=None, final=True)).chunks
        self.assertEqual([x.text for x in out], ["先に行きます。"])
        self.assertEqual([x.text for x in c.flush_utterance(None).chunks], ["私は"])

    def test_final_tokens_keep_internal_boundary_times(self):
        c = CaptionChunker()
        obs = CaptionObservation("utterance_final",1,"u1",stable_text="できました。次もできました。",language="ja",speaker="a",
            tokens=(RecognitionToken("できました。",0,1,True,language="ja",speaker="a"),
                    RecognitionToken("次もできました。",1,2,True,language="ja",speaker="a")),begin_pcm=0,end_pcm=2)
        out = c.observe(obs).chunks
        self.assertEqual([x.text for x in out], ["できました。", "次もできました。"])
        self.assertEqual([(x.begin_pcm,x.end_pcm) for x in out], [(0,1),(1,2)])

    def test_endpoint_is_advisory_and_stop_preserves_the_residual_once(self):
        c = CaptionChunker()
        c.observe(evidence("配信の予定は", 0, 2))
        self.assertEqual(c.observe(CaptionObservation("endpoint",1,"u1",end_pcm=2)).chunks, ())
        self.assertEqual([x.text for x in c.flush_utterance(None).chunks], ["配信の予定は"])
        self.assertEqual(c.flush_utterance(None).chunks, ())


if __name__ == "__main__":
    unittest.main()
