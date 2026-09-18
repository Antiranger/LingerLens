"""Bounded timing policy for live subtitle translations.

The pipeline has two clocks that constrain a translation request:

* ``provider_timeout_seconds`` is the maximum time we are willing to spend on
  the provider call itself.  It is the whole budget.
* ``playback_delay_seconds`` describes the window the cue's audio still has
  before the delayed player reaches it.  It is measured and reported, but it no
  longer shortens the deadline.

The window used to truncate the deadline as well, which discarded captions the
renderer would still have accepted.  Its estimate comes from how far the pushed
audio has run ahead of the sentence, and it assumes the viewer trails the audio
leg by exactly ``playback_delay_seconds``.  That holds in steady state and does
not hold while the buffer is filling: measured at a session head, the real lag
was 22-24s against a nominal 15s, so captions with 2.6-6.3s of real slack left
were dropped without a provider call.  Whether a caption is too late to be worth
showing is a fact the renderer owns -- it knows the playhead and already drops
cues whose window has passed -- so the backend now only bounds its own work.

``audio_end_wall`` already describes the end of the cue.  The policy therefore
never subtracts the cue's duration again; doing so double-counts long cues and
expires otherwise fast translations while they wait behind an older request.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable


@dataclasses.dataclass(frozen=True)
class TranslationBudget:
    """A cue's absolute deadline and the window it was measured against."""

    deadline_monotonic: float
    provider_timeout_seconds: float
    playback_window_seconds: float | None
    """Estimated window left before the delayed player reaches the cue's audio.

    Reported for diagnosis only.  It no longer shortens ``deadline_monotonic``:
    see the module docstring for the measurement that made it advisory.
    """

    def remaining(self, now_monotonic: float) -> float:
        """Return the non-negative portion of the cue's total budget."""

        return max(0.0, self.deadline_monotonic - now_monotonic)


class TranslationBudgetPolicy:
    """Allocate one total budget per cue at enqueue time.

    The policy is deliberately in-process and side-effect free.  The pipeline
    owns queue/store effects; this module only translates wall-clock age into
    an absolute monotonic deadline, which keeps the timing seam easy to test.
    """

    def __init__(
        self,
        provider_timeout_seconds: float,
        *,
        playback_delay_seconds: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if provider_timeout_seconds <= 0:
            raise ValueError("provider_timeout_seconds must be positive")
        self.provider_timeout_seconds = float(provider_timeout_seconds)
        self.playback_delay_seconds = playback_delay_seconds
        self.wall_clock = wall_clock
        self.monotonic = monotonic

    def allocate(self, audio_end_wall: float | None) -> TranslationBudget:
        """Allocate the provider budget; the cue end stays a reported window.

        ``audio_end_wall`` can be absent for synthetic/offline cues.  Either way
        the provider timeout is the budget, and the playback window is computed
        only so the margin we used to enforce stays visible in telemetry.
        """

        now = self.monotonic()
        deadline = now + self.provider_timeout_seconds
        playback_window: float | None = None
        if self.playback_delay_seconds is not None and audio_end_wall is not None:
            target_delay = max(0.0, float(self.playback_delay_seconds()))
            age = max(0.0, self.wall_clock() - audio_end_wall)
            playback_window = max(0.0, target_delay - age)
        return TranslationBudget(
            deadline_monotonic=deadline,
            provider_timeout_seconds=self.provider_timeout_seconds,
            playback_window_seconds=playback_window,
        )
