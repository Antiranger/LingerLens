import unittest

from companion.recovery_policy import RecoveryPolicy


class RecoveryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = RecoveryPolicy()

    def test_healthy_source(self):
        self.assertEqual(self.policy.decide(stall_seconds=0.4, process_running=True, source_error=None).state, "healthy")

    def test_warns_before_reconnect(self):
        decision = self.policy.decide(stall_seconds=3.0, process_running=True, source_error=None)
        self.assertEqual((decision.state, decision.action), ("warning", "report"))

    def test_reconnects_long_before_eighty_seconds(self):
        decision = self.policy.decide(stall_seconds=8.0, process_running=True, source_error=None)
        self.assertEqual((decision.state, decision.action), ("reconnecting", "reconnect"))

    def test_reports_hard_stall(self):
        decision = self.policy.decide(stall_seconds=30.0, process_running=True, source_error=None)
        self.assertEqual((decision.state, decision.action), ("failed", "reconnect-and-report"))

    def test_process_error_wins(self):
        decision = self.policy.decide(stall_seconds=0.0, process_running=True, source_error="I/O error")
        self.assertEqual((decision.state, decision.action), ("failed", "report"))
