"""Golden tests for MediaAnchor: the continuously measured offset between the
independent ASR audio leg and the video packaging leg.

Design: docs/subtitle-audio-leg-design-2026-09-09.md
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.media_anchor import MediaAnchor


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


def run_steady(anchor: MediaAnchor, clock: FakeClock, seconds: int, video_base: float, audio_base: float = 0.0) -> None:
    """Advance both legs at exactly 1x for `seconds` samples."""
    for _ in range(seconds):
        clock.advance(1.0)
        t = clock.now
        anchor.add_sample(video_base + (t - 1000.0), audio_base + (t - 1000.0))


class MediaAnchorTests(unittest.TestCase):
    def make_anchor(self, **kwargs):
        clock = FakeClock()
        anchor = MediaAnchor(clock=clock, **kwargs)
        return anchor, clock

    def test_not_ready_until_min_samples(self) -> None:
        anchor, clock = self.make_anchor()
        anchor.add_sample(100.0, 0.0)  # first sample: no rate computable
        clock.advance(1.0)
        anchor.add_sample(101.0, 1.0)
        self.assertFalse(anchor.ready)
        self.assertIsNone(anchor.offset)
        self.assertIsNone(anchor.map_pcm(5.0))

    def test_steady_state_offset_and_mapping(self) -> None:
        anchor, clock = self.make_anchor()
        anchor.add_sample(100.0, 0.0)
        run_steady(anchor, clock, 5, video_base=100.0)
        self.assertTrue(anchor.ready)
        self.assertAlmostEqual(anchor.offset or 0, 100.0, places=6)
        self.assertAlmostEqual(anchor.map_pcm(5.5) or 0, 105.5, places=6)
        spread = anchor.spread
        self.assertIsNotNone(spread)
        self.assertLess(spread if spread is not None else 99, 0.01)

    def test_startup_burst_samples_are_rejected(self) -> None:
        anchor, clock = self.make_anchor()
        anchor.add_sample(100.0, 0.0)
        # Audio leg decodes its startup backlog at 25x while the video leg
        # packages at 1x: these samples must not poison the window.
        audio = 0.0
        for _ in range(5):
            clock.advance(0.2)
            audio += 5.0  # 25x
            anchor.add_sample(100.0 + (clock.now - 1000.0), audio)
        self.assertFalse(anchor.ready)
        self.assertIsNone(anchor.offset)
        # Once both legs settle at 1x, the anchor converges to the true C=100.
        # (audio already raced ahead; C = V - A is whatever it is — the point
        # is burst samples are excluded and the converged value is consistent)
        run_steady(anchor, clock, 5, video_base=100.0, audio_base=audio)
        self.assertTrue(anchor.ready)
        self.assertAlmostEqual(anchor.offset or 0, 100.0 - audio, places=6)

    def test_video_stall_freezes_mapping_at_last_offset(self) -> None:
        anchor, clock = self.make_anchor()
        anchor.add_sample(100.0, 0.0)
        run_steady(anchor, clock, 5, video_base=100.0)
        self.assertTrue(anchor.ready)
        # Video leg stalls for 10s: V frozen (rate 0 → samples rejected),
        # audio leg healthy at 1x.
        frozen_v = 100.0 + (clock.now - 1000.0)
        for _ in range(10):
            clock.advance(1.0)
            anchor.add_sample(frozen_v, clock.now - 1000.0)
        # Offset unchanged: cues during the stall map onto the frozen edge.
        self.assertAlmostEqual(anchor.offset or 0, 100.0, places=6)
        self.assertAlmostEqual(anchor.map_pcm(clock.now - 1000.0) or 0, 100.0 + (clock.now - 1000.0), places=6)

    def test_video_skip_reanchors_to_new_offset(self) -> None:
        anchor, clock = self.make_anchor(window=20, reset_threshold=5.0, reset_samples=3)
        anchor.add_sample(100.0, 0.0)
        run_steady(anchor, clock, 6, video_base=100.0)
        self.assertAlmostEqual(anchor.offset or 0, 100.0, places=6)
        # Video leg recovers from a stall by skipping 40s of content: V jumps
        # +40 within one sample interval (rate spike → that sample rejected),
        # then continues at 1x from the new base.
        skip_at = clock.now
        new_base = 100.0 + (skip_at - 1000.0) + 40.0
        clock.advance(1.0)
        anchor.add_sample(new_base + (clock.now - skip_at) - 1.0 + 1.0, clock.now - 1000.0)
        # Subsequent steady samples all deviate by +40 → fast reset kicks in
        # (window reseeds, then needs min_samples fresh samples to be ready).
        for i in range(6):
            clock.advance(1.0)
            anchor.add_sample(new_base + (clock.now - skip_at), clock.now - 1000.0)
        self.assertTrue(anchor.ready)
        self.assertAlmostEqual(anchor.offset or 0, 140.0, places=6)
        self.assertAlmostEqual(anchor.map_pcm(clock.now - 1000.0) or 0, 140.0 + (clock.now - 1000.0), places=6)

    def test_missing_video_probe_sample_is_ignored(self) -> None:
        anchor, clock = self.make_anchor()
        anchor.add_sample(100.0, 0.0)
        run_steady(anchor, clock, 4, video_base=100.0)
        self.assertTrue(anchor.ready)
        clock.advance(1.0)
        anchor.add_sample(None, clock.now - 1000.0)
        self.assertAlmostEqual(anchor.offset or 0, 100.0, places=6)

    def test_drift_reports_latest_sample_minus_offset(self) -> None:
        anchor, clock = self.make_anchor()
        anchor.add_sample(100.0, 0.0)
        run_steady(anchor, clock, 5, video_base=100.0)
        # One valid-rate sample whose C deviates by +2s (lag difference grew
        # by 2s spread over 4s, so both legs stay inside the rate gate).
        clock.advance(4.0)
        anchor.add_sample(100.0 + (clock.now - 1000.0) + 2.0, clock.now - 1000.0)
        self.assertAlmostEqual(anchor.drift or 0, 2.0, places=6)

    def test_zero_elapsed_clock_does_not_divide_by_zero(self) -> None:
        anchor, clock = self.make_anchor()
        anchor.add_sample(100.0, 0.0)
        anchor.add_sample(100.0, 0.0)  # same tick
        self.assertFalse(anchor.ready)
        clock.advance(1.0)
        anchor.add_sample(101.0, 1.0)
        self.assertIsNone(anchor.offset)

    def test_segment_quantized_video_counter_passes_at_slow_cadence(self) -> None:
        """Live finding 2026-09-09: the video leg's private-media counter
        advances in 1s segment quanta; at a 2.5s sampling cadence dV lands in
        {2,3} (rate 0.8-1.2) and must pass the gate."""
        anchor, clock = self.make_anchor()
        video = 100.0
        audio = 0.0
        anchor.add_sample(video, audio)
        for i in range(8):
            clock.advance(2.5)
            audio += 2.5
            video += 3.0 if i % 5 == 4 else 2.0  # quantized: 2,2,2,2,3 → 11s per 12.5s
            anchor.add_sample(video, audio)
        self.assertTrue(anchor.ready)
        self.assertIsNotNone(anchor.offset)

    def test_batched_video_counter_passes_when_playlist_updates_in_bursts(self) -> None:
        """A live playlist may publish several seconds of media at once."""
        anchor, clock = self.make_anchor()
        video = 100.0
        audio = 0.0
        anchor.add_sample(video, audio)
        # The sampler runs every 2.5s, while the publisher counter advances
        # in 5s batches.  The unchanged samples must be folded into the next
        # interval instead of being treated as a video stall followed by a
        # 2x burst.
        for _ in range(8):
            clock.advance(2.5)
            audio += 2.5
            if _ % 2 == 1:
                video += 5.0
            anchor.add_sample(video, audio)
        self.assertTrue(anchor.ready)
        self.assertAlmostEqual(anchor.offset or 0, 100.0, places=6)


class ExactOffsetTests(unittest.TestCase):
    """The source-clock offset, which the window can only estimate.

    Live measurement 2026-09-16 (600s, TBS NEWS DIG): the window differences
    `privateMediaSeconds - pcm`, and that quantity is a sawtooth -- packaged
    segments land whole while PCM advances smoothly -- whose centre sat a
    stage-frontier gap away from C. Mean was -0.719s against a true C of
    +5.006s, and the offset the window produced swung across a 9.1s range
    (cues up to ~4.5s early, up to ~4s late). The two legs' source origins, by
    contrast, held to a range of 0.000s.
    """

    def make_anchor(self, **kwargs):
        clock = FakeClock()
        anchor = MediaAnchor(clock=clock, **kwargs)
        return anchor, clock

    def test_exact_offset_is_usable_with_an_empty_window(self) -> None:
        anchor, _ = self.make_anchor()
        anchor.set_exact_offset(lambda: 5.006)
        # No samples at all: the window has nothing to say, but the mapping is
        # already exact and must not be held back waiting for convergence.
        self.assertTrue(anchor.ready)
        self.assertAlmostEqual(anchor.exact_offset or 0, 5.006, places=6)
        self.assertAlmostEqual(anchor.offset or 0, 5.006, places=6)
        self.assertAlmostEqual(anchor.map_pcm(10.0) or 0, 15.006, places=6)
        # The window diagnostics must degrade to None, not raise on an empty
        # deque -- `ready` is now true while `_window` is still empty.
        self.assertIsNone(anchor.window_offset)
        self.assertIsNone(anchor.spread)
        self.assertIsNone(anchor.drift)

    def test_exact_offset_overrides_a_converged_window(self) -> None:
        anchor, clock = self.make_anchor()
        anchor.add_sample(100.0, 0.0)
        run_steady(anchor, clock, 5, video_base=100.0)
        self.assertAlmostEqual(anchor.offset or 0, 100.0, places=6)
        anchor.set_exact_offset(lambda: 5.006)
        self.assertAlmostEqual(anchor.offset or 0, 5.006, places=6)
        # The window keeps being sampled, so the disagreement stays visible.
        self.assertAlmostEqual(anchor.window_offset or 0, 100.0, places=6)

    def test_unmeasurable_exact_offset_falls_back_to_the_window(self) -> None:
        anchor, clock = self.make_anchor()
        anchor.add_sample(100.0, 0.0)
        run_steady(anchor, clock, 5, video_base=100.0)
        # A leg with no absolute clock, or one that re-based mid-session, must
        # degrade to the sampled window rather than to a wrong number.
        anchor.set_exact_offset(lambda: None)
        self.assertAlmostEqual(anchor.offset or 0, 100.0, places=6)
        self.assertIsNone(anchor.exact_offset)

    def test_failing_exact_offset_provider_does_not_break_the_mapping(self) -> None:
        anchor, clock = self.make_anchor()
        anchor.add_sample(100.0, 0.0)
        run_steady(anchor, clock, 5, video_base=100.0)

        def explode() -> float:
            raise RuntimeError("probe exploded")

        anchor.set_exact_offset(explode)
        self.assertIsNone(anchor.exact_offset)
        self.assertAlmostEqual(anchor.offset or 0, 100.0, places=6)

    def test_no_provider_at_all_keeps_the_original_behaviour(self) -> None:
        anchor, clock = self.make_anchor()
        anchor.add_sample(100.0, 0.0)
        self.assertIsNone(anchor.exact_offset)
        self.assertFalse(anchor.ready)
        run_steady(anchor, clock, 5, video_base=100.0)
        self.assertTrue(anchor.ready)
        self.assertAlmostEqual(anchor.offset or 0, 100.0, places=6)


if __name__ == "__main__":
    unittest.main()
