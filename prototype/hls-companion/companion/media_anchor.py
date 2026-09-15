"""Continuously measured media anchor between two independent download legs.

Design: docs/subtitle-audio-leg-design-2026-09-09.md

With a dedicated ASR audio leg (P3-B), the subtitle pipeline's PCM clock and
the video packaging leg no longer share a byte origin. The anchor measures the
offset C = V(t) - A(t) once per sample tick and maps PCM positions into the
video leg's private media space:

    cue_media_position = pcm_seconds + anchor.offset

The window median differences two *stage-output* counters, so it is short by
whatever already-received media each stage is still holding:

    C_measured = C_true + (audio stage backlog - video stage backlog)

A stage cannot count media until it has emitted it, and the video packaging
stage structurally holds a segment's worth (measured 0.7-6.0s live) while the
audio decode stage holds ~0. Callers close that gap by supplying `correction`,
which is added to `offset`; it is deliberately kept out of the sampled window so
the sampling stays on the smooth counters the rate gate was tuned for.

Sampling is rate-gated: only intervals where BOTH legs advanced at ~1x
(relative to wall clock) count. Startup backlogs decoded at CPU speed (25x)
and stall intervals (0x) are rejected, so bursts cannot poison the estimate.
Rejected samples still refresh the raw position baseline — otherwise a
post-skip leg would be locked out of the gate forever.

A jump reset handles video-leg segment skips: when several consecutive valid
samples all deviate from the current median by more than `reset_threshold`,
the window is reseeded so the anchor reconverges in seconds instead of waiting
for half the window to flush.
"""
from __future__ import annotations

import statistics
import time
from collections import deque
from typing import Callable


class MediaAnchor:
    def __init__(
        self,
        *,
        window: int = 30,
        min_samples: int = 3,
        rate_tolerance: float = 0.6,
        min_interval: float = 0.2,
        reset_threshold: float = 5.0,
        reset_samples: int = 3,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window: deque[float] = deque(maxlen=window)
        self._min_samples = min_samples
        self._rate_tolerance = rate_tolerance
        self._min_interval = min_interval
        self._reset_threshold = reset_threshold
        self._reset_samples = reset_samples
        self._clock = clock
        self._last_raw: tuple[float, float, float] | None = None  # (t, video, pcm)
        self._latest_c: float | None = None
        self._deviating_run = 0
        self._deviating_sign = 0
        self._correction: Callable[[], float] | None = None

    def set_correction(self, correction: Callable[[], float] | None) -> None:
        """Supply the stage-backlog gap that the window median cannot see.

        Kept out of `add_sample` on purpose: the correction is quantized by
        segment finalization, and feeding it into the sampled window would
        replace the smooth counters the rate gate depends on with a sawtooth.
        """
        self._correction = correction

    def add_sample(self, video_seconds: float | None, pcm_seconds: float) -> None:
        """Ingest one (video-leg, audio-leg) position pair at the current tick."""
        if video_seconds is None:
            # Probe failed: do not advance the baseline, so the next pair with
            # a video reading cannot compute a bogus cross-gap rate.
            self._last_raw = None
            return
        now = self._clock()
        previous = self._last_raw
        # The private HLS counter can arrive in batches even when the media
        # itself is advancing continuously (for example, FFmpeg rewrites a
        # five-second playlist window at once).  Keep the previous baseline
        # while the video counter is unchanged so the next advancing sample
        # measures the whole elapsed interval instead of producing a false
        # 0x/2x rate pair that the gate must reject.  A real video stall still
        # contributes no accepted sample, so the last trusted mapping remains
        # in force until the leg resumes.
        if previous is not None and float(video_seconds) == previous[1]:
            return
        self._last_raw = (now, float(video_seconds), float(pcm_seconds))
        if previous is None:
            return
        dt = now - previous[0]
        if dt < self._min_interval:
            return
        video_rate = (float(video_seconds) - previous[1]) / dt
        pcm_rate = (float(pcm_seconds) - previous[2]) / dt
        if not (1.0 - self._rate_tolerance <= video_rate <= 1.0 + self._rate_tolerance):
            return
        if not (1.0 - self._rate_tolerance <= pcm_rate <= 1.0 + self._rate_tolerance):
            return
        c_value = float(video_seconds) - float(pcm_seconds)
        self._latest_c = c_value
        # Compare against the window's own centre, not the corrected offset: a
        # skip is a movement of the window, and the caller's correction is a
        # separate, slowly varying quantity.
        median = self.window_offset
        if median is not None and abs(c_value - median) > self._reset_threshold:
            sign = 1 if c_value > median else -1
            self._deviating_run = self._deviating_run + 1 if sign == self._deviating_sign else 1
            self._deviating_sign = sign
            if self._deviating_run >= self._reset_samples:
                # A video-leg skip (or equivalent jump) moved C for good.
                self._window.clear()
                self._deviating_run = 0
        else:
            self._deviating_run = 0
            self._deviating_sign = 0
        self._window.append(c_value)

    @property
    def ready(self) -> bool:
        return len(self._window) >= self._min_samples

    @property
    def samples(self) -> int:
        return len(self._window)

    @property
    def window_offset(self) -> float | None:
        """The raw window median, without the caller's stage-backlog correction."""
        if not self.ready:
            return None
        return statistics.median(self._window)

    @property
    def offset(self) -> float | None:
        median = self.window_offset
        if median is None:
            return None
        if self._correction is None:
            return median
        return median + self._correction()

    @property
    def spread(self) -> float | None:
        """Interquartile range of the window; a healthy anchor stays < 0.5s."""
        if not self.ready:
            return None
        ordered = sorted(self._window)
        mid = len(ordered) // 2
        lower = ordered[:mid] or ordered[:1]
        upper = ordered[(len(ordered) - mid):] or ordered[-1:]
        return statistics.median(upper) - statistics.median(lower)

    @property
    def drift(self) -> float | None:
        median = self.window_offset
        if median is None or self._latest_c is None:
            return None
        return self._latest_c - median

    def map_pcm(self, pcm_seconds: float) -> float | None:
        """Map an audio-leg PCM position into video-leg media seconds."""
        median = self.offset
        if median is None:
            return None
        return pcm_seconds + median
