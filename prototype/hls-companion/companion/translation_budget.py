"""Bounded timing policy for live subtitle translations.

The pipeline has two clocks that constrain a translation request:

* ``provider_timeout_seconds`` is the maximum time we are willing to spend on
  the provider call itself.
* ``playback_delay_seconds`` is the remaining wall-clock window before the
  cue's audio end reaches the delayed player.

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
    """A single cue's absolute deadline and its two useful constraints."""

    deadline_monotonic: float
    provider_timeout_seconds: float
    playback_window_seconds: float | None

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
        """Allocate a deadline from the cue end, never from its start.

        ``audio_end_wall`` can be absent for synthetic/offline cues.  In that
        case the provider timeout remains the only constraint.
        """

        now = self.monotonic()
        deadline = now + self.provider_timeout_seconds
        playback_window: float | None = None
        if self.playback_delay_seconds is not None and audio_end_wall is not None:
            target_delay = max(0.0, float(self.playback_delay_seconds()))
            age = max(0.0, self.wall_clock() - audio_end_wall)
            playback_window = max(0.0, target_delay - age)
            deadline = min(deadline, now + playback_window)
        return TranslationBudget(
            deadline_monotonic=deadline,
            provider_timeout_seconds=self.provider_timeout_seconds,
            playback_window_seconds=playback_window,
        )
