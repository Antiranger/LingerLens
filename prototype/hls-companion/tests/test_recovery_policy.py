import unittest

from companion.recovery_policy import RecoveryPolicy, quiet_threshold


class QuietThresholdTests(unittest.TestCase):
    def test_scales_to_the_segment_length(self):
        # One segment plus the 2s margin -- the same ruler the stall banner uses.
        self.assertEqual(quiet_threshold(5), 7.0)
        self.assertEqual(quiet_threshold(6), 8.0)

    def test_one_second_segments_keep_the_floor(self):
        self.assertEqual(quiet_threshold(1), 5.0)

    def test_unknown_segment_length_keeps_the_floor(self):
        for value in (None, 0, 0.0, "", "not a number"):
            self.assertEqual(quiet_threshold(value), 5.0)


class RecoveryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = RecoveryPolicy()

    def decide(self, stall, **kwargs):
        return self.policy.decide(
            stall_seconds=stall, process_running=True, source_error=None, **kwargs
        )

    def test_a_healthy_stream_never_warns(self):
        # Measured on a healthy 1080p60 stream with 5.005s segments (261 samples):
        # the media leg was idle p50 3.5s, max 5.4-6.0s. The old 3s warning line
        # called 66-73.5% of that healthy stream a warning.
        for stall in (0.4, 3.0, 3.5, 5.4, 6.0):
            self.assertEqual(self.decide(stall, target_duration=6).state, "healthy")

    def test_warns_once_the_normal_peak_is_cleared(self):
        decision = self.decide(8.0, target_duration=6)
        self.assertEqual((decision.state, decision.action), ("warning", "report"))

    def test_reconnects_at_twice_the_quiet_threshold(self):
        decision = self.decide(16.0, target_duration=6)
        self.assertEqual((decision.state, decision.action), ("reconnecting", "reconnect"))

    def test_reports_hard_stall(self):
        decision = self.decide(30.0, target_duration=6)
        self.assertEqual((decision.state, decision.action), ("failed", "reconnect-and-report"))

    def test_the_failed_budget_grows_with_a_slow_stream(self):
        # 20s segments get a 22s quiet threshold, so 30s is only a warning and the
        # 30s failure floor yields to three segments (66s).
        self.assertEqual(self.decide(31.0, target_duration=20).state, "warning")
        self.assertEqual(self.decide(44.0, target_duration=20).state, "reconnecting")
        self.assertEqual(self.decide(66.0, target_duration=20).state, "failed")

    def test_the_well_known_floor_still_governs_short_segments(self):
        # 1s segments: quiet 5s, reconnect 10s, and the 30s failure floor stands.
        self.assertEqual(self.decide(9.9, target_duration=1).state, "warning")
        self.assertEqual(self.decide(10.0, target_duration=1).state, "reconnecting")
        self.assertEqual(self.decide(30.0, target_duration=1).state, "failed")

    def test_process_error_wins(self):
        decision = self.policy.decide(stall_seconds=0.0, process_running=True, source_error="I/O error")
        self.assertEqual((decision.state, decision.action), ("failed", "report"))

    def test_stopped_process_wins(self):
        decision = self.policy.decide(stall_seconds=0.0, process_running=False, source_error=None)
        self.assertEqual((decision.state, decision.action), ("failed", "report"))

    def test_a_missing_stall_reading_is_not_a_stall(self):
        decision = self.policy.decide(stall_seconds=None, process_running=True, source_error=None)
        self.assertEqual(decision.state, "healthy")


if __name__ == "__main__":
    unittest.main()
