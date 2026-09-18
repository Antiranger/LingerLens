from __future__ import annotations

import unittest

from companion.translation_budget import TranslationBudgetPolicy


class TranslationBudgetPolicyTests(unittest.TestCase):
    def test_uses_audio_end_age_without_subtracting_cue_duration_again(self) -> None:
        now = [100.0]
        policy = TranslationBudgetPolicy(
            6.0,
            playback_delay_seconds=lambda: 11.0,
            wall_clock=lambda: 1_005.0,
            monotonic=lambda: now[0],
        )

        budget = policy.allocate(audio_end_wall=1_000.0)

        self.assertEqual(budget.playback_window_seconds, 6.0)
        self.assertEqual(budget.deadline_monotonic, 106.0)
        self.assertEqual(budget.remaining(103.0), 3.0)

    def test_a_window_that_has_passed_is_reported_without_cancelling_the_call(self) -> None:
        policy = TranslationBudgetPolicy(
            6.0,
            playback_delay_seconds=lambda: 15.0,
            wall_clock=lambda: 1_020.0,
            monotonic=lambda: 100.0,
        )

        budget = policy.allocate(audio_end_wall=1_000.0)

        # The window is still measured and reported: 20 seconds of age against a
        # 15 second delay leaves none. It no longer truncates the deadline,
        # because the age is an estimate and the renderer owns the real answer.
        self.assertEqual(budget.playback_window_seconds, 0.0)
        self.assertEqual(budget.deadline_monotonic, 106.0)
        self.assertEqual(budget.remaining(100.0), 6.0)

    def test_without_media_timing_it_falls_back_to_provider_timeout(self) -> None:
        policy = TranslationBudgetPolicy(
            6.0,
            monotonic=lambda: 100.0,
        )

        budget = policy.allocate(audio_end_wall=None)

        self.assertIsNone(budget.playback_window_seconds)
        self.assertEqual(budget.deadline_monotonic, 106.0)


if __name__ == "__main__":
    unittest.main()
