"""Atomic HLS capture-media cursor.

The public contract is epoch seconds on the playlist PROGRAM-DATE-TIME timeline.
It never falls back to the machine wall clock when PDT is unavailable.
"""

from __future__ import annotations

import threading
import time
from typing import Any


class CaptureClock:
    """Thread-safe snapshot of the packaging leg's media progress."""

    def __init__(self, *, stall_after_seconds: float | None = None) -> None:
        self._lock = threading.Lock()
        self._pdt_epoch: float | None = None
        self._completed_private_media_seconds = 0.0
        self._target_duration = 0.0
        self._last_media_advance_monotonic: float | None = None
        self._last_capture_wall_time: float | None = None
        self._stall_after_seconds = stall_after_seconds

    @property
    def pdt_epoch(self) -> float | None:
        with self._lock:
            return self._pdt_epoch

    def update(
        self,
        *,
        pdt_epoch: float | None,
        completed_private_media_seconds: float,
        target_duration: float,
        monotonic_time: float | None = None,
    ) -> None:
        """Atomically publish a packaging-progress sample.

        Media progress may arrive in bursts. The externally visible cursor is
        monotonic even if a malformed/stale playlist sample moves backwards.
        """
        now = time.monotonic() if monotonic_time is None else float(monotonic_time)
        completed = max(0.0, float(completed_private_media_seconds))
        target = max(0.0, float(target_duration))
        with self._lock:
            if pdt_epoch is not None and self._pdt_epoch is None:
                self._pdt_epoch = float(pdt_epoch)
            elif pdt_epoch is not None and self._pdt_epoch is not None:
                # The first PDT plus accumulated duration defines the session
                # clock. Ignore later fragment PDT jitter/discontinuity here.
                self._pdt_epoch = min(self._pdt_epoch, float(pdt_epoch))
            if completed > self._completed_private_media_seconds:
                self._completed_private_media_seconds = completed
                self._last_media_advance_monotonic = now
            self._target_duration = target
            base = self._base_wall_time_locked()
            if base is not None:
                self._last_capture_wall_time = max(self._last_capture_wall_time or base, base)

    # Compatibility for callers/tests while keeping one algorithm.
    def update_sample(
        self,
        pts: float | None = None,
        monotonic_time: float | None = None,
        pdt_epoch: float | None = None,
        **kwargs: Any,
    ) -> None:
        completed = pts if pts is not None else kwargs.get("pts_sec", kwargs.get("pts", 0.0))
        mono = monotonic_time if monotonic_time is not None else kwargs.get("mono_sec")
        pdt = pdt_epoch if pdt_epoch is not None else kwargs.get("pdt_sec")
        self.update(
            pdt_epoch=float(pdt) if pdt is not None else None,
            completed_private_media_seconds=float(completed or 0.0),
            target_duration=float(kwargs.get("target_duration", kwargs.get("targetDuration", self._target_duration or 1.0))),
            monotonic_time=float(mono) if mono is not None else None,
        )

    def _base_wall_time_locked(self) -> float | None:
        if self._pdt_epoch is None:
            return None
        return self._pdt_epoch + self._completed_private_media_seconds

    def capture_wall_time(self, monotonic_time: float | None = None) -> float | None:
        now = time.monotonic() if monotonic_time is None else float(monotonic_time)
        with self._lock:
            base = self._base_wall_time_locked()
            if base is None:
                return None
            last_advance = self._last_media_advance_monotonic
            interpolation = 0.0
            if last_advance is not None and self._target_duration > 0:
                elapsed = max(0.0, now - last_advance)
                stall_limit = self._stall_after_seconds
                if stall_limit is None:
                    stall_limit = self._target_duration * 2.0
                if elapsed <= max(0.0, stall_limit):
                    interpolation = min(elapsed, self._target_duration)
                elif self._last_capture_wall_time is not None:
                    interpolation = min(
                        self._target_duration,
                        max(0.0, self._last_capture_wall_time - base),
                    )
            value = base + interpolation
            if self._last_capture_wall_time is not None:
                value = max(value, self._last_capture_wall_time)
            self._last_capture_wall_time = value
            return value

    def monotonic_to_media_time(self, received_monotonic: float) -> float | None:
        """Map a receive-monotonic timestamp to the capture PDT timeline."""
        with self._lock:
            base = self._base_wall_time_locked()
            last_advance = self._last_media_advance_monotonic
            target = self._target_duration
        if base is None or last_advance is None:
            return None
        delta = float(received_monotonic) - last_advance
        if delta >= 0:
            delta = min(delta, target)
        else:
            # Messages received while PDT was unavailable are mapped into the
            # newly available cursor interval without fabricating wall time.
            delta = max(delta, 0.0)
        return base + delta

    # Legacy relative helpers retained only for internal compatibility.
    def monotonic_to_pts(self, monotonic_time: float) -> float | None:
        media_time = self.monotonic_to_media_time(monotonic_time)
        pdt = self.pdt_epoch
        return None if media_time is None or pdt is None else media_time - pdt

    def wall_clock_to_pts(self, wall_clock_time: float) -> float | None:
        pdt = self.pdt_epoch
        return None if pdt is None else float(wall_clock_time) - pdt

    def pts_to_wall_clock(self, pts: float) -> float | None:
        pdt = self.pdt_epoch
        return None if pdt is None else pdt + float(pts)

    def reset(self) -> None:
        with self._lock:
            self._pdt_epoch = None
            self._completed_private_media_seconds = 0.0
            self._target_duration = 0.0
            self._last_media_advance_monotonic = None
            self._last_capture_wall_time = None

    def snapshot(self, monotonic_time: float | None = None) -> dict[str, Any]:
        capture = self.capture_wall_time(monotonic_time)
        with self._lock:
            return {
                "available": capture is not None,
                "captureWallTime": capture,
                "pdtEpoch": self._pdt_epoch,
                "completedPrivateMediaSeconds": self._completed_private_media_seconds,
                "targetDuration": self._target_duration or None,
                "lastMediaAdvanceMonotonic": self._last_media_advance_monotonic,
            }
