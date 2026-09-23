from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.caption_chunker import (
    CaptionChunker,
    HARD_DEADLINE_SECONDS,
    RELEASE_BUDGET_SECONDS,
)
from companion.providers.base import CaptionObservation, RecognitionToken


def token(text: str, begin: float, end: float, *, language: str = "en", speaker: str | None = None) -> RecognitionToken:
    return RecognitionToken(text, begin, end, True, language=language, speaker=speaker)


def stable(*tokens: RecognitionToken, generation: int = 1, item_id: str = "u1") -> CaptionObservation:
    return CaptionObservation("stable_token_delta", generation, item_id, tokens=tuple(tokens))


class CaptionChunkerContractTests(unittest.TestCase):
    def test_audio_gap_closes_old_lane_before_same_speaker_id_reappears(self) -> None:
        chunker = CaptionChunker()
        chunker.observe(stable(token("unfinished", 0.0, 0.6, speaker="3"), item_id="u1"))
        chunker.observe(CaptionObservation("utterance_final", 1, "u1", stable_text="unfinished", begin_pcm=0, end_pcm=.6))
        chunker.advance_audio(11.5)
        decision = chunker.observe(stable(token("continuation", 10.0, 11.5, speaker="3"), item_id="u2"))
        self.assertEqual([x.text for x in decision.chunks], ["unfinished"])
        self.assertEqual((decision.chunks[0].begin_pcm, decision.chunks[0].end_pcm), (0, .6))
        self.assertEqual([x.text for x in chunker.flush_utterance(None).chunks], ["continuation"])

    def test_duplicate_closed_observation_cannot_consume_another_residual(self):
        c = CaptionChunker()
        done = CaptionObservation("utterance_final", 1, "done", stable_text="Done.", begin_pcm=10, end_pcm=11, language="en")
        c.observe(done)
        c.observe(stable(token("unfinished", 0, .6, speaker="3")))
        self.assertEqual(c.observe(done).chunks, ())
        self.assertEqual([x.text for x in c.flush_utterance(None).chunks], ["unfinished"])

    def test_empty_and_tentative_observations_do_not_close_active_expression(self):
        c = CaptionChunker()
        c.observe(stable(token("unfinished", 0, .6, speaker="3")))
        for kind in ("stable_token_delta", "text_snapshot"):
            self.assertEqual(c.observe(CaptionObservation(kind, 1, "other", begin_pcm=10, end_pcm=11, tentative_text="maybe")).chunks, ())
        self.assertEqual([x.text for x in c.flush_utterance(None).chunks], ["unfinished"])

    def test_final_residual_expires_without_more_audio_or_recognition(self):
        c = CaptionChunker()
        c.observe(CaptionObservation("utterance_final", 1, "old", stable_text="unfinished", begin_pcm=0, end_pcm=.6, language="en", speaker="3"), now=20)
        deadline = c.next_deadline
        self.assertIsNotNone(deadline)
        self.assertEqual(c.expire(deadline - .001).chunks, ())
        out = c.expire(deadline).chunks
        self.assertEqual([(x.text, x.begin_pcm, x.end_pcm) for x in out], [("unfinished", 0, .6)])
        self.assertIsNone(out[0].ends_mid_sentence)
        self.assertIsNone(c.next_deadline)
        self.assertEqual(c.expire(deadline + 100).chunks, ())

    def test_continuous_open_speech_is_not_expired_by_audio(self):
        # This test was named "..._by_audio_or_elapsed_time", and the
        # elapsed-time half was a deliberate decision the user has since
        # reversed: continuous speech held a caption open indefinitely, so a
        # speaker who never paused produced a cue older than the display
        # budget, which was then refused translation and never reached the
        # screen. What is STILL true is that advancing audio never cuts.
        c = CaptionChunker()
        c.observe(stable(token("unfinished", 0, 1, speaker="3")), now=1)
        self.assertEqual(c.advance_audio(100).chunks, ())
        # now=1 armed the hard deadline at 8.0; nothing expires before it.
        self.assertEqual(c.expire(7.9).chunks, ())
        timed_out = c.expire(8.1)
        self.assertEqual([x.text for x in timed_out.chunks], ["unfinished"])
        self.assertEqual(timed_out.chunks[0].cut_reason, "hard_deadline")
        # A continuation after the cut opens a fresh lane rather than
        # resurrecting the one already emitted.
        out = c.observe(stable(token("continuation.", 1, 2, speaker="3")), now=100).chunks
        self.assertEqual([x.text for x in out], ["continuation."])

    def test_nearby_continuation_cancels_residual_deadline(self):
        c = CaptionChunker()
        c.observe(CaptionObservation("utterance_final", 1, "old", stable_text="unfinished", begin_pcm=0, end_pcm=1, language="en", speaker="3"), now=2)
        c.observe(stable(token("continuing", 1.1, 2, speaker="3"), item_id="new"), now=2.01)
        # The residual grace is still cancelled by the nearby continuation, so the
        # two parts merge rather than being flushed apart. The lane also carries
        # the hard deadline now (armed at 2.01 + 7s), so the assertion is on the
        # behaviour it protects: nothing expires before that deadline.
        self.assertEqual(c.expire(8.9).chunks, ())
        self.assertEqual([x.text for x in c.flush_utterance(None).chunks], ["unfinished continuing"])

    def test_late_continuation_does_not_reopen_expired_expression(self):
        c = CaptionChunker()
        c.observe(CaptionObservation("utterance_final", 1, "old", stable_text="unfinished", begin_pcm=0, end_pcm=1, language="en", speaker="3"), now=2)
        out = c.observe(stable(token("continuation.", 1.1, 2, speaker="3"), item_id="new"), now=100).chunks
        self.assertEqual([(x.text, x.begin_pcm) for x in out], [("unfinished", 0), ("continuation.", 1.1)])

    def test_speaker_flip_does_not_extend_old_deadline_or_merge_people(self):
        c = CaptionChunker()
        c.observe(CaptionObservation("utterance_final", 1, "old", stable_text="unfinished", begin_pcm=0, end_pcm=1, language="en", speaker="3"), now=2)
        deadline = c.next_deadline
        c.observe(stable(token("another", .8, 2, speaker="4"), item_id="new"), now=2.01)
        self.assertEqual(c.next_deadline, deadline)
        self.assertEqual([x.speaker for x in c.expire(deadline).chunks], ["3"])
        self.assertEqual([(x.text, x.begin_pcm) for x in c.flush_utterance(None).chunks], [("another", .8)])

    def test_same_label_overlapping_items_keep_separate_audio_ranges(self):
        c = CaptionChunker()
        c.observe(stable(token("first", 0, 2, speaker="3")), now=2)
        out = c.observe(stable(token("second", 1, 3, speaker="3"), item_id="other"), now=2.1).chunks
        out += c.flush_utterance(None).chunks
        self.assertEqual([(x.text, x.begin_pcm, x.end_pcm) for x in out], [("first", 0, 2), ("second", 1, 3)])

    def test_new_token_time_not_accumulated_observation_start_controls_gap(self):
        c = CaptionChunker()
        c.observe(CaptionObservation("utterance_final", 1, "old", stable_text="unfinished", begin_pcm=0, end_pcm=1, language="en", speaker="3"), now=10)
        out = c.observe(CaptionObservation("stable_token_delta", 1, "next", tokens=(token("next", 10, 11, speaker="3"),), begin_pcm=0, end_pcm=11), now=10.01).chunks
        self.assertEqual([x.text for x in out], ["unfinished"])
        self.assertEqual([x.begin_pcm for x in c.flush_utterance(None).chunks], [10])

    def test_hybrid_stable_upgrade_does_not_swallow_a_second_nearby_occurrence(self):
        c = CaptionChunker()
        first = RecognitionToken("ha", 0, .05, False, language="en")
        c.observe(CaptionObservation("token_snapshot", 1, "u", tokens=(first, token("later", 1, 2))))
        c.observe(CaptionObservation("token_snapshot", 1, "u", tokens=(first, token("later", 1, 2), token("tail", 2, 3))))
        c.observe(stable(token("ha", .01, .06), token("ha", .06, .1), item_id="u"))
        self.assertEqual([x.text for x in c.flush_utterance(None).chunks], ["ha ha"])

    def test_reset_and_duplicate_final_do_not_extend_deadline(self):
        c = CaptionChunker()
        final = CaptionObservation("utterance_final", 1, "old", stable_text="unfinished", begin_pcm=0, end_pcm=1, language="en")
        c.observe(final, now=2)
        deadline = c.next_deadline
        c.observe(final, now=2.1)
        self.assertEqual(c.next_deadline, deadline)
        c.reset(2)
        self.assertIsNone(c.next_deadline)
        self.assertEqual(c.observe(final, now=100).chunks, ())
        self.assertEqual(c.expire(100).chunks, ())

    def test_adjacent_repeated_stable_pieces_are_not_timestamp_jitter(self):
        c = CaptionChunker()
        c.observe(stable(token("ま", 7.74, 7.80, language="ja"), token("ま", 7.80, 7.80, language="ja")))
        self.assertEqual([x.text for x in c.flush_utterance(None).chunks], ["まま"])

    def test_final_width_normalization_preserves_original_tokens_and_times(self):
        c = CaptionChunker()
        out = c.observe(CaptionObservation("utterance_final", 1, "u", stable_text="今日は晴れです？明日は雨です。", language="ja", tokens=(token("今日は晴れです？", 0, 1, language="ja"), token("明日は雨です。", 1, 2, language="ja")), begin_pcm=0, end_pcm=2)).chunks
        self.assertEqual([(x.text, x.begin_pcm, x.end_pcm, x.exact_timing) for x in out], [("今日は晴れです？", 0, 1, True), ("明日は雨です。", 1, 2, True)])

    def test_overlapping_speakers_keep_both_active_lanes(self) -> None:
        chunker = CaptionChunker()
        chunker.observe(stable(token("one", 0.0, 1.0, speaker="1"), item_id="u1"))
        decision = chunker.observe(stable(token("two", 0.8, 1.8, speaker="2"), item_id="u2"))
        self.assertEqual(decision.chunks, ())
        self.assertEqual({x.text for x in chunker.flush_utterance(None).chunks}, {"one", "two"})

    def test_final_spacing_changes_do_not_drop_the_unpublished_japanese_tail(self) -> None:
        chunker = CaptionChunker()
        chunks = list(chunker.observe(stable(
            token("今日は晴れです。", 0.0, 3.2, language="ja"),
            token("明日は", 3.2, 4.0, language="ja"),
        )).chunks)
        chunks.extend(chunker.observe(CaptionObservation(
            "utterance_final", 1, "u1",
            stable_text="今日は晴れです。 明日は雨です。",
            begin_pcm=0.0, end_pcm=5.6, language="ja",
        )).chunks)

        self.assertEqual([chunk.text for chunk in chunks], ["今日は晴れです。", "明日は雨です。"])
        self.assertEqual((chunks[-1].begin_pcm, chunks[-1].end_pcm), (3.2, 5.6))
        self.assertEqual(chunker.telemetry().final_reconciliation_conflicts, 0)

    def test_presentation_width_does_not_create_caption_boundaries(self) -> None:
        chunker = CaptionChunker()
        decision = chunker.observe(stable(
            token("じゃないなら、ペ", 0.0, 1.26, language="ja"),
            token("ンダントの秘密は誰から。由美は失踪中。遥と錦。", 1.26, 5.4, language="ja"),
        ))

        self.assertEqual(
            [chunk.text for chunk in decision.chunks],
            ["じゃないなら、ペンダントの秘密は誰から。由美は失踪中。遥と錦。"],
        )
        self.assertEqual(decision.chunks[0].cut_reason, "terminal_punctuation")

    def test_empty_delta_does_not_rewind_emitted_deadline(self) -> None:
        chunker = CaptionChunker()
        first = chunker.observe(stable(token("Done.", 0.0, 1.0)))
        self.assertEqual(len(first.chunks), 1)
        chunker.observe(CaptionObservation("stable_token_delta", 1, "u1", begin_pcm=0.0, end_pcm=6.1))
        self.assertFalse(chunker.advance_audio(6.1).pending_evidence)
        # Advancing audio reports pending evidence at most; it never asks the
        # Provider for a commit (advance_audio cannot make a boundary safe).
        self.assertEqual(chunker.advance_audio(12.1).chunks, ())

    def test_long_unpunctuated_tokens_wait_for_a_boundary_and_preserve_exact_time(self) -> None:
        chunker = CaptionChunker()
        chunks = []
        for index in range(20):
            chunks.extend(chunker.observe(stable(token(f"w{index}", index * 0.6, index * 0.6 + 0.6))).chunks)
        self.assertEqual(chunks, [])
        chunks.extend(chunker.flush_utterance("u1").chunks)
        self.assertEqual(" ".join(chunk.text for chunk in chunks).split(), [f"w{i}" for i in range(20)])
        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0].exact_timing)
        self.assertAlmostEqual(chunks[0].end_pcm, 12)

    def test_hard_deadline_prefers_an_earlier_sentence_boundary_to_a_late_fallback(self) -> None:
        chunker = CaptionChunker()
        decision = chunker.observe(stable(
            token("嘘でしょ。", 0.0, 1.2, language="ja"),
            token("回転", 1.2, 1.5, language="ja"),
            token("寿司シミュレーターでそんなことあるんだ、っていう", 1.5, 6.6, language="ja"),
        ))

        self.assertEqual([chunk.text for chunk in decision.chunks], ["嘘でしょ。"])
        self.assertEqual(decision.chunks[0].cut_reason, "terminal_punctuation")
        residual = chunker.flush_utterance("u1").chunks
        self.assertEqual(
            [chunk.text for chunk in residual],
            ["回転寿司シミュレーターでそんなことあるんだ、っていう"],
        )

    def test_japanese_numeric_enumeration_stays_with_the_following_duration(self) -> None:
        chunker = CaptionChunker()
        decision = chunker.observe(stable(
            token("風邪気味じゃなかったらね、", 0.0, 1.5, language="ja"),
            token("2、", 1.5, 1.8, language="ja"),
            token("3時間やれたかもしれないんですけど、", 1.8, 6.6, language="ja"),
        ))

        self.assertEqual(
            [chunk.text for chunk in decision.chunks],
            ["風邪気味じゃなかったらね、", "2、 3時間やれたかもしれないんですけど、"],
        )
        self.assertTrue(all(chunk.cut_reason == "clause_boundary" for chunk in decision.chunks))
        self.assertEqual(chunker.flush_utterance("u1").chunks, ())

    def test_transport_batching_does_not_change_token_partition(self) -> None:
        evidence = tuple(token(text, index * 0.7, index * 0.7 + 0.5) for index, text in enumerate(
            ("one", "two", "three,", "but", "four", "five", "six.", "seven", "eight")
        ))
        bulk = CaptionChunker()
        bulk_chunks = list(bulk.observe(stable(*evidence)).chunks)
        bulk_chunks.extend(bulk.flush_utterance("u1").chunks)

        incremental = CaptionChunker()
        incremental_chunks = []
        for item in evidence:
            incremental_chunks.extend(incremental.observe(stable(item)).chunks)
        incremental_chunks.extend(incremental.flush_utterance("u1").chunks)
        self.assertEqual([chunk.text for chunk in bulk_chunks], [chunk.text for chunk in incremental_chunks])

    def test_candidate_priority_and_endpoint_residual(self) -> None:
        chunker = CaptionChunker()
        decision = chunker.observe(stable(
            token("We", 0.0, 0.4),
            token("started,", 0.5, 1.4),
            token("and", 1.5, 1.9),
            token("finished.", 2.0, 3.2),
        ))
        self.assertEqual(len(decision.chunks), 1)
        self.assertEqual(decision.chunks[0].text, "We started, and finished.")
        self.assertEqual(decision.chunks[0].cut_reason, "terminal_punctuation")
        self.assertFalse(decision.chunks[0].ends_mid_sentence)

        chunker.observe(stable(token("tail", 3.3, 3.8)))
        residual = chunker.flush_utterance("u1").chunks
        self.assertEqual(len(residual), 1)
        self.assertEqual(residual[0].cut_reason, "utterance_endpoint")
        self.assertFalse(residual[0].starts_mid_sentence)
        self.assertIsNone(residual[0].ends_mid_sentence)

    def test_chunk_position_metadata_propagates_mid_sentence_and_unknown(self) -> None:
        chunker = CaptionChunker()
        first = chunker.observe(stable(
            token("We", 0.0, 0.5),
            token("started,", 0.5, 1.3),
            token("because", 1.4, 2.0),
            token("latency", 2.1, 3.2),
        )).chunks
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].cut_reason, "clause_boundary")
        self.assertFalse(first[0].starts_mid_sentence)
        self.assertTrue(first[0].ends_mid_sentence)

        chunker.observe(stable(token("continues", 3.3, 3.9)))
        residual = chunker.flush_utterance("u1").chunks
        self.assertEqual(len(residual), 1)
        self.assertTrue(residual[0].starts_mid_sentence)
        self.assertIsNone(residual[0].ends_mid_sentence)

    def test_advance_audio_does_not_publish_subminimum_stable_evidence_as_hard_deadline(self) -> None:
        chunker = CaptionChunker()
        chunker.open_item("u1", 0.0)
        chunker.observe(stable(token("fragment", 5.82, 6.0)))
        decision = chunker.advance_audio(12.0)
        self.assertEqual(decision.chunks, ())
        self.assertTrue(decision.pending_evidence)

        endpoint = chunker.observe(CaptionObservation("endpoint", 1, "u1", end_pcm=6.0))
        self.assertEqual(endpoint.chunks, ())
        self.assertEqual([chunk.text for chunk in chunker.flush_utterance(None).chunks], ["fragment"])

    def test_audio_deadline_waits_when_stable_text_is_too_stale_for_a_lexical_fallback(self) -> None:
        chunker = CaptionChunker()
        chunker.open_item("u1", 0.0)
        chunker.observe(stable(
            token("もう、", 0.0, 0.4, language="ja"),
            token("メンバー入ってくれてありがと", 0.4, 2.82, language="ja"),
        ))

        deadline = chunker.advance_audio(6.0)
        self.assertEqual(deadline.chunks, ())
        self.assertTrue(deadline.pending_evidence)

        completed = chunker.observe(stable(token("ね。", 2.82, 3.2, language="ja")))
        self.assertEqual(
            [chunk.text for chunk in completed.chunks],
            ["もう、メンバー入ってくれてありがとね。"],
        )
        self.assertEqual(completed.chunks[0].cut_reason, "terminal_punctuation")

    def test_advance_audio_reports_pending_evidence_without_ever_committing(self) -> None:
        # Advancing audio can only ever report pending evidence: it never
        # publishes a chunk and never asks the Provider for a mid-speech commit,
        # for any Provider capability combination.
        chunker = CaptionChunker()
        chunker.observe(CaptionObservation("text_snapshot", 1, "u1", tentative_text="still changing", begin_pcm=0.0, end_pcm=1.0))
        decision = chunker.advance_audio(6.0)
        self.assertTrue(decision.pending_evidence)
        self.assertEqual(decision.chunks, ())
        self.assertEqual(chunker.telemetry().pending_evidence_over_soft_span, 1)

    def test_hard_deadline_emits_confirmed_text_when_no_boundary_ever_arrives(self) -> None:
        # The backstop. A speaker who never pauses must not hold a caption open
        # forever: once it has been open for the hard deadline its confirmed text is
        # emitted anyway, under the reason name the vocabulary and the translation
        # prompt already carried but which no code path had ever produced.
        chunker = CaptionChunker()
        chunker.open_item("u1", 0.0)
        chunker.observe(stable(token("まだ終わってない", 0.0, 2.0, language="ja")))

        # observe() defaults to now=0.0, so the armed deadline is the constant itself.
        deadline = chunker.next_deadline
        self.assertAlmostEqual(deadline, 7.0, places=3)

        # Waiting is still preferred while there is time left.
        self.assertEqual(chunker.expire(deadline - 0.1).chunks, ())

        timed_out = chunker.expire(deadline + 0.1)
        self.assertEqual([chunk.text for chunk in timed_out.chunks], ["まだ終わってない"])
        self.assertEqual(timed_out.chunks[0].cut_reason, "hard_deadline")
        # A fallback is not a sentence end, and saying otherwise would be a claim the
        # cut cannot support.
        self.assertIsNone(timed_out.chunks[0].ends_mid_sentence)
        self.assertEqual(chunker.telemetry().chunk_cut_reasons, (("hard_deadline", 1),))

    def test_tail_terminal_after_a_noun_is_held_but_a_verb_end_still_cuts(self) -> None:
        # Live at 01:26 the Provider delivered 「…防災。」 then 「大臣。」 then 「も兼任…」
        # and each piece became its own cue. A terminal straight after a noun with
        # nothing after it yet is now held, so later evidence can decide the cut.
        held = CaptionChunker(realtime=True)
        held.open_item("u1", 0.0)
        self.assertEqual(held.observe(stable(token("防災。", 0.0, 1.0, language="ja"))).chunks, ())

        # A genuine sentence end is unaffected: Japanese sentences end in an auxiliary,
        # a verb or an adjective, not a noun, so it still cuts -- once the piece has
        # the three seconds of audio the span floor asks for.
        verb_end = CaptionChunker(realtime=True)
        verb_end.open_item("u1", 0.0)
        decision = verb_end.observe(stable(token("終わりました。", 0.0, 3.0, language="ja")))
        self.assertEqual([chunk.text for chunk in decision.chunks], ["終わりました。"])
        self.assertEqual([chunk.cut_reason for chunk in decision.chunks], ["terminal_punctuation"])

    def test_terminal_after_a_noun_waits_for_its_particle(self) -> None:
        # Observed live: the Provider placed 。 mid-phrase, the cue was cut between the
        # noun and its particle, and the screen showed "…焦点。" followed by "は今後…".
        # The attached-right veto now covers terminals when the word before the mark is
        # a noun, so this stays one cue.
        chunker = CaptionChunker()
        chunker.open_item("u1", 0.0)
        decision = chunker.observe(stable(
            token("焦点。", 0.0, 1.0, language="ja"),
            token("は今後どこまで利上げが進むかです。", 1.0, 3.5, language="ja"),
        ))
        self.assertEqual([chunk.cut_reason for chunk in decision.chunks], ["terminal_punctuation"])
        self.assertEqual(len(decision.chunks), 1)
        self.assertIn("焦点。", decision.chunks[0].text)
        self.assertIn("は今後", decision.chunks[0].text)

    def test_hard_deadline_restarts_when_the_lane_emits_so_no_stub_is_shown(self) -> None:
        # The user saw one- and two-character subtitles on screen. A boundary cut can
        # leave the opening characters of the NEXT clause in the lane, and a deadline
        # armed once per lane fired on that leftover stub. The deadline bounds how long
        # the currently pending text has waited, so emitting anything restarts it.
        chunker = CaptionChunker()
        chunker.open_item("u1", 0.0)
        first = chunker.observe(stable(
            token("終わりました。", 0.0, 1.5, language="ja"),
            token("つぎ", 1.5, 2.0, language="ja"),
        ), now=0.0)
        self.assertEqual([chunk.text for chunk in first.chunks], ["終わりました。"])
        self.assertEqual([chunk.cut_reason for chunk in first.chunks], ["terminal_punctuation"])

        # The lane's original deadline was 7.0. It must not fire on the two-character
        # remainder, because that remainder's own clock had not started yet.
        self.assertEqual(chunker.expire(7.1).chunks, ())

        # New text starts the pending clock, and the deadline follows it.
        chunker.observe(stable(token("つづき", 2.0, 3.0, language="ja")), now=10.0)
        self.assertAlmostEqual(chunker.next_deadline, 17.0, places=3)
        self.assertEqual(chunker.expire(16.9).chunks, ())
        late = chunker.expire(17.1)
        self.assertEqual([chunk.text for chunk in late.chunks], ["つぎつづき"])
        self.assertEqual(late.chunks[0].cut_reason, "hard_deadline")

    def test_hard_deadline_never_turns_an_empty_lane_into_a_cue(self) -> None:
        # Confirmed text only. A lane that accumulated nothing is dropped, not
        # published as an empty subtitle.
        chunker = CaptionChunker()
        chunker.open_item("u1", 0.0)
        chunker.observe(CaptionObservation("endpoint", 1, "u1", end_pcm=1.0))
        decision = chunker.expire(1e9)
        self.assertEqual(decision.chunks, ())

    def test_hard_deadline_does_not_displace_a_boundary_that_arrives_in_time(self) -> None:
        # The backstop must stay a fallback: when the sentence ends before the
        # deadline, the boundary wins and hard_deadline is never used.
        chunker = CaptionChunker()
        chunker.open_item("u1", 0.0)
        decided = chunker.observe(stable(token("終わりました。", 0.0, 1.5, language="ja")))
        self.assertEqual([chunk.cut_reason for chunk in decided.chunks], ["terminal_punctuation"])
        self.assertEqual(chunker.expire(1e9).chunks, ())
        self.assertEqual(chunker.telemetry().chunk_cut_reasons, (("terminal_punctuation", 1),))

    def test_local_agreement_two_commits_policy_text_without_rewriting_it(self) -> None:
        chunker = CaptionChunker()
        first = CaptionObservation("text_snapshot", 1, "u1", tentative_text="hello brave world", begin_pcm=0.0, end_pcm=1.0)
        second = CaptionObservation("text_snapshot", 1, "u1", tentative_text="hello brave world today", begin_pcm=0.0, end_pcm=2.0)
        third = CaptionObservation("text_snapshot", 1, "u1", tentative_text="hello bright world today", begin_pcm=0.0, end_pcm=3.0)
        self.assertEqual(chunker.observe(first).chunks, ())
        self.assertEqual(chunker.observe(second).chunks, ())
        self.assertEqual(chunker.observe(third).chunks, ())
        final = chunker.observe(CaptionObservation("utterance_final", 1, "u1", stable_text="hello bright world today", begin_pcm=0.0, end_pcm=4.0))
        chunks = list(final.chunks) + list(chunker.flush_utterance("u1").chunks)
        self.assertEqual(" ".join(chunk.text for chunk in chunks), "hello brave world today")
        telemetry = chunker.telemetry()
        self.assertGreaterEqual(telemetry.local_agreement_commits, 1)
        self.assertGreaterEqual(telemetry.final_reconciliation_conflicts, 1)

    def test_utterance_final_tokens_add_only_uncommitted_residual_with_exact_timing(self) -> None:
        chunker = CaptionChunker()
        chunker.observe(stable(token("one", 0.0, 0.5)))
        chunks = chunker.observe(CaptionObservation(
            "utterance_final", 1, "u1",
            tokens=(
                RecognitionToken("one", 0.0, 0.5, True, language="en"),
                RecognitionToken(" two", 0.5, 1.0, True, language="en"),
            ),
            stable_text="one two", begin_pcm=0.0, end_pcm=1.0,
        )).chunks
        chunks += chunker.flush_utterance(None).chunks
        self.assertEqual([chunk.text for chunk in chunks], ["one two"])
        self.assertEqual((chunks[0].begin_pcm, chunks[0].end_pcm), (0.0, 1.0))
        self.assertEqual(chunker.telemetry().final_reconciliation_conflicts, 0)

    def test_stable_token_delta_does_not_duplicate_token_already_policy_committed_from_snapshot(self) -> None:
        chunker = CaptionChunker()
        first = (
            RecognitionToken("hello", 0.0, 0.5, False, language="en"),
            RecognitionToken(" world", 0.5, 1.0, False, language="en"),
        )
        second = first + (RecognitionToken(" again", 1.0, 1.5, False, language="en"),)
        chunker.observe(CaptionObservation("token_snapshot", 1, "u1", tokens=first))
        chunker.observe(CaptionObservation("token_snapshot", 1, "u1", tokens=second))
        chunker.observe(CaptionObservation(
            "stable_token_delta", 1, "u1",
            tokens=(RecognitionToken("hello", 0.0, 0.5, True, language="en"),),
        ))
        chunks = chunker.observe(CaptionObservation(
            "utterance_final", 1, "u1", stable_text="hello world again", begin_pcm=0.0, end_pcm=1.5,
        )).chunks
        chunks += chunker.flush_utterance(None).chunks
        self.assertEqual([chunk.text for chunk in chunks], ["hello world again"])

    def test_mutable_token_agreement_tolerates_small_timestamp_jitter_and_holds_back_last_token(self) -> None:
        chunker = CaptionChunker()
        one = RecognitionToken("hello", 0.0, 0.5, False, language="en")
        two = RecognitionToken("world", 0.6, 1.1, False, language="en")
        chunker.observe(CaptionObservation("token_snapshot", 1, "u1", tokens=(one, two)))
        chunker.observe(CaptionObservation("token_snapshot", 1, "u1", tokens=(
            RecognitionToken("hello", 0.01, 0.51, False, language="en"),
            RecognitionToken("world", 0.59, 1.11, False, language="en"),
            RecognitionToken("again", 1.2, 1.7, False, language="en"),
        )))
        chunks = chunker.flush_utterance("u1").chunks
        self.assertEqual([chunk.text for chunk in chunks], ["hello"])
        self.assertEqual(chunker.telemetry().local_agreement_commits, 1)

    def test_endpoint_is_advisory_and_late_final_reconciles_residual(self) -> None:
        chunker = CaptionChunker()
        chunker.observe(stable(token("hello", 0.0, 0.8)))
        self.assertEqual(chunker.observe(CaptionObservation("endpoint",1,"u1",end_pcm=.8)).chunks, ())
        self.assertEqual(chunker.observe(CaptionObservation("endpoint",1,"u1",end_pcm=.8)).chunks, ())
        final = CaptionObservation("utterance_final",1,"u1",stable_text="hello world",begin_pcm=0,end_pcm=1.4)
        self.assertEqual(chunker.observe(final).chunks, ())
        self.assertEqual(chunker.observe(final).chunks, ())
        self.assertEqual([x.text for x in chunker.flush_utterance(None).chunks], ["hello world"])

    def test_single_indivisible_token_overshoot_is_preserved_and_reported(self) -> None:
        chunker = CaptionChunker()
        self.assertEqual(chunker.observe(stable(token("loooooong", 0.0, 7.2))).chunks, ())
        chunks = chunker.flush_utterance(None).chunks
        self.assertEqual([chunk.text for chunk in chunks], ["loooooong"])
        self.assertTrue(chunks[0].exact_timing)
        self.assertEqual(chunker.telemetry().span_over_soft_target, 1)

    def test_text_snapshot_uses_observation_language_for_cjk_safe_prefix(self) -> None:
        chunker = CaptionChunker()
        chunker.observe(CaptionObservation(
            "text_snapshot", 1, "zh", tentative_text="今天继续", begin_pcm=0.0, end_pcm=1.0, language="zh",
        ))
        chunker.observe(CaptionObservation(
            "text_snapshot", 1, "zh", tentative_text="今天继续测试", begin_pcm=0.0, end_pcm=2.0, language="zh",
        ))
        chunks = chunker.observe(CaptionObservation(
            "utterance_final", 1, "zh", stable_text="今天继续测试", begin_pcm=0.0, end_pcm=2.5, language="zh",
        )).chunks
        chunks += chunker.flush_utterance(None).chunks
        self.assertEqual([chunk.text for chunk in chunks], ["今天继续测试"])
        self.assertEqual(chunks[0].language, "zh")

    def test_reset_discards_old_generation_and_unknown_language_still_chunks(self) -> None:
        chunker = CaptionChunker()
        chunker.observe(stable(token("old", 0.0, 1.0), generation=1))
        chunker.reset(2)
        chunks = []
        for index in range(10):
            unknown = RecognitionToken(f"x{index}", index * 0.8, index * 0.8 + 0.5, True)
            chunks.extend(chunker.observe(stable(unknown, generation=2)).chunks)
        chunks.extend(chunker.flush_utterance("u1").chunks)
        self.assertNotIn("old", " ".join(chunk.text for chunk in chunks))
        self.assertEqual(len(chunks), 1)


class ReleaseBudgetTests(unittest.TestCase):
    """The rule the deadline constants exist to satisfy.

    A bilingual lane waits for the Provider's own boundary, so the Provider is
    what would have to bound it -- and neither one does. Measured on real Japanese
    against Qwen3.8 LiveTranslate, one server-VAD utterance stayed open for 15.7 s
    and another for 15.8 s at a 500 ms silence threshold, and 36.4 s at 800 ms
    (.scratch/qwen-livetranslate-live/events_*sil*.json, 2026-09-21). The release
    budget is therefore ours to enforce, and this is the test that keeps it honest.
    """

    def test_the_budget_is_the_tightest_holdback_the_player_allows(self) -> None:
        # server.py owns the floor the viewer can dial down to. If it moves, the
        # budget written into the chunker is a lie and must move with it.
        from companion.server import MIN_TARGET_DELAY_SECONDS

        self.assertEqual(RELEASE_BUDGET_SECONDS, MIN_TARGET_DELAY_SECONDS)

    def test_a_lane_fed_without_a_provider_boundary_still_releases_in_time(self) -> None:
        chunker = CaptionChunker(realtime=True, segments_only=True)
        fired_at = None
        held: tuple = ()
        for index in range(12):
            now = index + 0.9
            decision = chunker.observe(
                stable(token(f"word{index} ", float(index), now), item_id="one-long-turn"),
                now=now,
            )
            held = decision.chunks or chunker.expire(now).chunks
            if not held:
                self.assertLess(
                    now, HARD_DEADLINE_SECONDS,
                    "the deadline passed and the lane is still held: under "
                    "segments_only nothing releases it but the Provider's own "
                    "segment end, and it never sent one",
                )
                continue
            fired_at = now
            break
        self.assertIsNotNone(fired_at, "12 s of continuous speech must not hold a lane open")
        self.assertLessEqual(fired_at, RELEASE_BUDGET_SECONDS,
                             "released while the viewer can still be shown the line")
        self.assertLess(HARD_DEADLINE_SECONDS, RELEASE_BUDGET_SECONDS,
                        "the deadline has to leave room for the rest of the path")
        self.assertEqual(held[0].cut_reason, "hard_deadline")
        self.assertLessEqual(held[0].end_pcm, HARD_DEADLINE_SECONDS + 1.0,
                             "the released line is bounded by the deadline, not by the run-on")

    def test_a_backlog_releases_in_pieces_bounded_by_speech(self) -> None:
        """Sixteen seconds arriving at once is not one subtitle.

        This is what a stall costs the viewer: the deadline counts wall-clock
        waiting, and a backlog that lands in one poll has waited only that long
        while carrying everything said since. Measured on four real captures
        replayed 2026-09-22, where the whole lane came out as one 11-12 s caption.
        """
        chunker = CaptionChunker(realtime=True, segments_only=True)
        burst = stable(
            *[token(f"w{index}", float(index), float(index) + 0.9, language="ja")
              for index in range(16)],
            item_id="backlog",
        )
        self.assertEqual(chunker.observe(burst, now=0.5).chunks, ())

        cues = []
        for tick in range(10):
            cues.extend(chunker.expire(7.5 + float(tick)).chunks)

        self.assertGreater(len(cues), 1, "the backlog has to drain, not pile up")
        for cue in cues:
            self.assertEqual(cue.cut_reason, "hard_deadline")
            self.assertLessEqual(
                cue.end_pcm - cue.begin_pcm, HARD_DEADLINE_SECONDS + 1e-6,
                f"a cue covering {cue.end_pcm - cue.begin_pcm:.1f} s of speech is "
                "not readable at 1x no matter how long we waited for it",
            )
        self.assertEqual(
            "".join(cue.text.replace(" ", "") for cue in cues),
            "".join(f"w{index}" for index in range(16)),
            "slicing may rearrange when text appears, never what appears",
        )

    def test_a_budget_that_breaks_on_the_first_word_releases_the_lane_whole(self) -> None:
        """One word off a sentence is not a point the Provider ever stood on.

        Cutting where the lane's first unit already overshoots strands both halves:
        the stub matches no boundary the model stated, and the remainder is then
        measured against a cursor nothing advanced -- so the lane loses its Chinese
        on two captions where it had lost it on one (default capture 2026-09-22).
        """
        chunker = CaptionChunker(realtime=True, segments_only=True)
        chunker.observe(stable(
            token("長いまま来た", 0.0, 9.0, language="ja"),
            token("残り", 9.0, 10.5, language="ja"),
            item_id="long-first-unit",
        ), now=0.5)
        cues = chunker.expire(7.5).chunks
        self.assertEqual([cue.text for cue in cues], ["長いまま来た残り"])
        self.assertEqual(cues[0].cut_reason, "hard_deadline")


class PendingCaptionTests(unittest.TestCase):
    """The projection the player draws before the Provider closes its turn."""

    def test_a_held_lane_is_projected_and_grows_until_the_turn_closes(self) -> None:
        chunker = CaptionChunker(realtime=True, segments_only=True)
        self.assertIsNone(chunker.pending_caption())

        chunker.observe(stable(token("こんにちは", 0.0, 1.5, language="ja"), item_id="u1"), now=1.0)
        self.assertEqual(chunker.observe(stable(token("世界", 1.6, 2.4, language="ja"), item_id="u1"), now=2.0).chunks, ())
        held = chunker.pending_caption()
        self.assertIsNotNone(held)
        self.assertEqual(held.text, "こんにちは世界")
        self.assertEqual(held.item_id, "u1")
        self.assertEqual((held.begin_pcm, held.end_pcm), (0.0, 2.4))
        self.assertEqual(held.language, "ja")

        released = chunker.observe(CaptionObservation(
            "utterance_final", 1, "u1", stable_text="こんにちは世界", begin_pcm=0.0, end_pcm=2.4, language="ja",
        ), now=3.0)
        self.assertEqual([chunk.text for chunk in released.chunks], ["こんにちは世界"])
        self.assertIsNone(
            chunker.pending_caption(),
            "the cue replaced the projection, so drawing both would duplicate the line",
        )

    def test_projection_stops_at_the_position_the_viewer_has_heard(self) -> None:
        """A lane runs to the pushed audio, which leads the playhead by the app delay."""
        chunker = CaptionChunker(realtime=True, segments_only=True)
        chunker.observe(stable(token("すでに聞いた", 0.0, 1.5, language="ja"), item_id="u1"), now=1.0)
        chunker.observe(stable(token("まだ先の言葉", 5.0, 6.0, language="ja"), item_id="u1"), now=6.0)
        self.assertEqual(chunker.pending_caption(heard_pcm=2.0).text, "すでに聞いた")
        self.assertEqual(chunker.pending_caption().text, "すでに聞いたまだ先の言葉")
        # A unit already under the playhead is kept even if it is not finished,
        # which is how a cue displays from the moment its window opens.
        self.assertEqual(chunker.pending_caption(heard_pcm=0.5).text, "すでに聞いた")
        self.assertIsNone(chunker.pending_caption(heard_pcm=-0.5))

    def test_projection_reports_the_newest_lane_only(self) -> None:
        """One line on screen: the sentence being heard now, not an old stub."""
        chunker = CaptionChunker(realtime=True, segments_only=True)
        chunker.observe(stable(token("first", 0.0, 1.0, speaker="1"), item_id="u1"), now=1.0)
        chunker.observe(stable(token("second", 2.0, 3.0, speaker="2"), item_id="u2"), now=3.0)
        held = chunker.pending_caption()
        self.assertEqual(held.text, "second")
        self.assertEqual(held.speaker, "2")


if __name__ == "__main__":
    unittest.main()
