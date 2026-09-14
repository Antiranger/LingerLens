"""Small, deterministic policy for detecting and reporting live-source stalls."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveryDecision:
    state: str
    action: str
    reason: str


@dataclass(frozen=True)
class RecoveryPolicy:
    """Thresholds are deliberately below the user-visible 80s failure budget."""

    warning_seconds: float = 3.0
    reconnect_seconds: float = 8.0
    failed_seconds: float = 30.0

    def decide(self, *, stall_seconds: float | None, process_running: bool, source_error: str | None) -> RecoveryDecision:
        if source_error:
            return RecoveryDecision("failed", "report", "source-error")
        if not process_running:
            return RecoveryDecision("failed", "report", "process-stopped")
        stall = max(0.0, float(stall_seconds or 0.0))
        if stall >= self.failed_seconds:
            return RecoveryDecision("failed", "reconnect-and-report", "stall-timeout")
        if stall >= self.reconnect_seconds:
            return RecoveryDecision("reconnecting", "reconnect", "stall-threshold")
        if stall >= self.warning_seconds:
            return RecoveryDecision("warning", "report", "stall-warning")
        return RecoveryDecision("healthy", "none", "none")
