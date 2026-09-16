"""Small, deterministic policy for detecting and reporting live-source stalls."""

from __future__ import annotations

import math
from dataclasses import dataclass

# The same ruler the stall banner uses (web-player/playback-recovery.js):
# one segment plus a margin, with a floor for a stream that reports no segment
# length at all.
QUIET_FLOOR_SECONDS = 5.0
STALL_MARGIN_SECONDS = 2.0
# "Not coming back on its own", and the budget the old constants were kept under.
FAILED_SECONDS = 30.0


def quiet_threshold(target_duration: float | None) -> float:
    """Seconds of silence before the source counts as quiet, not breathing.

    A download leg receives one segment's worth of bytes per segment cadence and
    is otherwise silent, so "seconds since the last byte" normally ramps from 0
    to about one segment and resets -- its normal peak IS the segment length.
    Measured on a healthy 1080p60 stream with 5.005s segments: the media leg was
    idle p50 3.5s with a maximum of 5.4-6.0s over 261 samples, and none crossed
    this threshold.
    """
    try:
        longest = math.ceil(float(target_duration or 0.0))
    except (TypeError, ValueError):
        longest = 0
    if longest <= 0:
        return QUIET_FLOOR_SECONDS
    return max(QUIET_FLOOR_SECONDS, float(longest) + STALL_MARGIN_SECONDS)


@dataclass(frozen=True)
class RecoveryDecision:
    state: str
    action: str
    reason: str


@dataclass(frozen=True)
class RecoveryPolicy:
    """Classify source silence on the stream's own clock.

    The previous constants (3s warning / 8s reconnect / 30s failed) were chosen
    when the number they consume meant "seconds since the last yt-dlp log line",
    whose normal value was 0.2s. It now means "seconds since the last MEDIA
    BYTE", measured at the download legs, so its normal range is 0 to about one
    segment. The 3s line therefore sat inside the normal range and reported
    ``warning`` on 66-73.5% of the samples of a stream that was provably fine.

    Nothing in the repo consumes ``action``, and the stall banner classifies the
    same payload for itself with the same scaled threshold, so this is a report
    rather than a control -- but a report that a person or a diagnostic reads,
    which is exactly why it has to be true.
    """

    failed_seconds: float = FAILED_SECONDS

    def decide(
        self,
        *,
        stall_seconds: float | None,
        process_running: bool,
        source_error: str | None,
        target_duration: float | None = None,
    ) -> RecoveryDecision:
        if source_error:
            return RecoveryDecision("failed", "report", "source-error")
        if not process_running:
            return RecoveryDecision("failed", "report", "process-stopped")
        stall = max(0.0, float(stall_seconds or 0.0))
        quiet = quiet_threshold(target_duration)
        if stall >= max(self.failed_seconds, quiet * 3):
            return RecoveryDecision("failed", "reconnect-and-report", "stall-timeout")
        if stall >= quiet * 2:
            return RecoveryDecision("reconnecting", "reconnect", "stall-threshold")
        if stall >= quiet:
            return RecoveryDecision("warning", "report", "stall-warning")
        return RecoveryDecision("healthy", "none", "none")
