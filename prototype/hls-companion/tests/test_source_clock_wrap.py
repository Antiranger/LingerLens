"""R1: unwrap the 33-bit source clock, and refuse to publish what cannot be proven.

The PES PTS field is 33 bits at 90kHz, so the source's own clock wraps every
2**33 / 90000 = 95443.717688... seconds, about 26.512144 hours. A stream that runs
that long crosses the boundary, and a download leg that starts on the other side
of it from its partner reports an origin 26.5 hours away from its partner's. Before
R1 the raw subtraction produced that figure and the skew guard refused it, which
was safe but permanent: the stream stayed on the sampled window, the mapping
measured 4.44 seconds off.

These tests replay REAL transport-stream packets -- sync byte, PID, adaptation
field with its discontinuity_indicator, PES start code, PES header flags and the
five PTS bytes -- through the REAL probe, and drive the REAL anchor and the REAL
status wiring. Nothing here re-implements the unwrapping arithmetic to check the
unwrapping arithmetic; the only numbers written by hand are the ones the container
format defines.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import companion.server as server_module
from companion.media_anchor import MediaAnchor
from companion.server import CompanionApplication
from companion.source_timeline import PTS_HZ, PTS_MODULUS, MpegTsPtsProbe, signed_pts_delta
from companion.ytdlp_ingest import YtDlpLiveIngest

VIDEO_PID = 0x0100
AUDIO_PID = 0x0101
OTHER_PID = 0x0200
VIDEO_STREAM_ID = 0xE0
AUDIO_STREAM_ID = 0xC0

# One whole turn of the clock, and the size of the guard windows the probe reuses.
WRAP_TICKS = PTS_MODULUS
WRAP_SECONDS = PTS_MODULUS / PTS_HZ
EDGE_TICKS = 600 * PTS_HZ
REORDER_TICKS = 100 * PTS_HZ


def encode_pts(ticks: int) -> bytes:
    """The five PTS bytes exactly as the container defines them, markers included."""
    return bytes(
        [
            0x20 | ((ticks >> 29) & 0x0E) | 0x01,
            (ticks >> 22) & 0xFF,
            ((ticks >> 14) & 0xFE) | 0x01,
            (ticks >> 7) & 0xFF,
            ((ticks << 1) & 0xFE) | 0x01,
        ]
    )


def pes_payload(stream_id: int, ticks: int) -> bytes:
    header = bytes([0x00, 0x00, 0x01, stream_id, 0x00, 0x00, 0x80, 0x80, 0x05])
    return header + encode_pts(ticks) + b"\xaa" * 24


def ts_packet(pid: int, payload: bytes, *, discontinuity: bool = False) -> bytes:
    """One 188-byte transport packet carrying a PES header, optionally flagged."""
    prefix = bytes([0x47, 0x40 | ((pid >> 8) & 0x1F), pid & 0xFF])
    if discontinuity:
        # adaptation_field_control = 3 (adaptation + payload), then the field:
        # length 1, and the flag byte whose bit 7 is the discontinuity_indicator.
        body = prefix + bytes([0x30, 0x01, 0x80]) + payload
    else:
        body = prefix + bytes([0x10]) + payload
    assert len(body) <= 188, "the payload does not fit in one transport packet"
    return body + b"\xff" * (188 - len(body))


def video_packet(ticks: int, *, discontinuity: bool = False) -> bytes:
    return ts_packet(VIDEO_PID, pes_payload(VIDEO_STREAM_ID, ticks), discontinuity=discontinuity)


def audio_packet(ticks: int) -> bytes:
    return ts_packet(AUDIO_PID, pes_payload(AUDIO_STREAM_ID, ticks))


class PtsEncodingTests(unittest.TestCase):
    def test_the_test_encodes_pts_the_way_the_probe_decodes_it(self) -> None:
        # The whole file depends on this, so it is asserted rather than assumed:
        # a wrong encoder would make every replay below meaningless.
        for ticks in (0, 1, 45_000, PTS_MODULUS - 1, PTS_MODULUS - 45_000):
            probe = MpegTsPtsProbe(want_audio=False)
            values = probe.feed(video_packet(ticks))
            self.assertEqual(values, [ticks / PTS_HZ])

    def test_signed_delta_wraps_instead_of_subtracting(self) -> None:
        self.assertEqual(signed_pts_delta(3 * PTS_HZ, WRAP_TICKS - 2 * PTS_HZ), 5 * PTS_HZ)
        self.assertEqual(signed_pts_delta(WRAP_TICKS - 2 * PTS_HZ, 3 * PTS_HZ), -5 * PTS_HZ)
        # Without the modulo the same pair looks 26.5 hours apart.
        self.assertGreater(abs(3 * PTS_HZ - (WRAP_TICKS - 2 * PTS_HZ)), 26 * 3600 * PTS_HZ)


class WrapReplayTests(unittest.TestCase):
    """D: a wrap inside a session must not break monotonicity or the extent."""

    def test_the_five_packet_crossing_is_continuous(self) -> None:
        probe = MpegTsPtsProbe(want_audio=False)
        ticks = [WRAP_TICKS - 90_000, WRAP_TICKS - 45_000, 0, 45_000, 90_000]
        values: list[float] = []
        for tick in ticks:
            values.extend(probe.feed(video_packet(tick)))

        self.assertEqual(len(values), 5)
        for earlier, later in zip(values, values[1:]):
            self.assertAlmostEqual(later - earlier, 0.5, places=9)
        # The first value is the raw one: nothing before it, so nothing to unwrap.
        self.assertAlmostEqual(values[0], (WRAP_TICKS - 90_000) / PTS_HZ, places=9)
        # And the clock stayed usable throughout.
        self.assertTrue(probe.clock_valid)
        self.assertIsNone(probe.invalid_reason)

    def test_two_full_periods_accumulate_without_integer_drift(self) -> None:
        # G: only the arithmetic is synthetic here -- every packet is really
        # encoded and really parsed. 400 steps of 500s is 2.09 periods, so the
        # boundary is crossed twice; a counting unwrap must not drift by a tick.
        step = 500 * PTS_HZ
        count = 400
        first = WRAP_TICKS - 900_000
        probe = MpegTsPtsProbe(want_audio=False)

        values: list[float] = []
        for index in range(count):
            raw = (first + index * step) % WRAP_TICKS
            values.extend(probe.feed(video_packet(raw)))

        self.assertTrue(probe.clock_valid, probe.invalid_reason)
        self.assertEqual(len(values), count)
        self.assertAlmostEqual(values[0], first / PTS_HZ, places=9)
        for index, value in enumerate(values):
            # Exact: the unwrapped result is an integer tick count, so the drift
            # after two periods must be zero, not "small".
            self.assertEqual(
                round(value * PTS_HZ),
                round(first + index * step),
                msg=f"step {index} drifted",
            )
        # The unwrapping is per-session state, not a global epoch. The number of
        # crossings is derived from the inputs rather than written down: 400 steps
        # of 500s from 10s before the boundary is 3 crossings, not 2.
        self.assertEqual(probe._wrap_ticks, ((first + (count - 1) * step) // WRAP_TICKS) * WRAP_TICKS)

    def test_a_late_packet_from_the_previous_epoch_is_ignored(self) -> None:
        # G: the old probe tolerated a small backwards PTS because packets can be
        # reordered. Just after a wrap, a packet from before it is the same shape,
        # and counting another period for it would add 26.5 hours.
        probe = MpegTsPtsProbe(want_audio=False)
        probe.feed(video_packet(WRAP_TICKS - 45_000))
        after_wrap = probe.feed(video_packet(0))
        late = probe.feed(video_packet(WRAP_TICKS - 90_000))
        resumed = probe.feed(video_packet(45_000))

        self.assertEqual(len(after_wrap), 1)
        self.assertEqual(late, [], "a 1s-early stray packet must be ignored, not counted")
        self.assertTrue(probe.clock_valid, "the clock must not be permanently broken by it")
        self.assertEqual(probe._wrap_ticks, WRAP_TICKS, "exactly one period was crossed")
        self.assertEqual(len(resumed), 1)
        self.assertAlmostEqual(resumed[0] - after_wrap[0], 0.5, places=9)

    def test_a_small_backwards_reorder_is_still_ignored(self) -> None:
        # G: the pre-existing rule, unchanged.
        probe = MpegTsPtsProbe(want_audio=False)
        probe.feed(video_packet(500 * PTS_HZ))
        ignored = probe.feed(video_packet(500 * PTS_HZ - REORDER_TICKS // 2))
        self.assertEqual(ignored, [])
        self.assertTrue(probe.clock_valid)
        self.assertEqual(probe._wrap_ticks, 0)


class ClockInvalidationTests(unittest.TestCase):
    """D: a clock that cannot be trusted is said so, once, and stays that way."""

    def test_a_transport_discontinuity_invalidates_the_clock(self) -> None:
        probe = MpegTsPtsProbe(want_audio=False)
        probe.feed(video_packet(500 * PTS_HZ))
        values = probe.feed(video_packet(600 * PTS_HZ, discontinuity=True))

        self.assertEqual(values, [])
        self.assertFalse(probe.clock_valid)
        self.assertEqual(probe.invalid_reason, "transport-discontinuity")

    def test_another_pids_discontinuity_does_not_touch_this_clock(self) -> None:
        # G: only the selected media PID's flag describes this stream.
        probe = MpegTsPtsProbe(want_audio=False)
        probe.feed(video_packet(500 * PTS_HZ))
        probe.feed(ts_packet(OTHER_PID, pes_payload(VIDEO_STREAM_ID, 0), discontinuity=True))
        values = probe.feed(video_packet(501 * PTS_HZ))

        self.assertTrue(probe.clock_valid)
        self.assertEqual(len(values), 1, "the video clock must keep working")

    def test_a_large_backwards_jump_that_is_not_a_wrap_invalidates(self) -> None:
        probe = MpegTsPtsProbe(want_audio=False)
        probe.feed(video_packet(50_000 * PTS_HZ))
        # 700s backwards and nowhere near the boundary: not a wrap, not reorder.
        values = probe.feed(video_packet(50_000 * PTS_HZ - 700 * PTS_HZ))

        self.assertEqual(values, [])
        self.assertFalse(probe.clock_valid)
        self.assertEqual(probe.invalid_reason, "non-wrap-clock-reset")

    def test_an_unattributable_reverse_jump_invalidates(self) -> None:
        probe = MpegTsPtsProbe(want_audio=False)
        probe.feed(video_packet(1_000 * PTS_HZ))
        # A forward jump of more than half a period. It is not adjacent to the
        # boundary, so it is not a wrap, and it is far too large to be reordering:
        # the two readings are genuinely indistinguishable, and the probe says so
        # instead of picking one.
        values = probe.feed(video_packet(60_000 * PTS_HZ))

        self.assertEqual(values, [])
        self.assertFalse(probe.clock_valid)
        self.assertEqual(probe.invalid_reason, "ambiguous-reverse-wrap-or-reset")

    def test_a_later_good_packet_cannot_restore_trust(self) -> None:
        # D: the packets that looked fine before a reset still look fine after one,
        # so nothing in this session may quietly re-establish the clock. Only a new
        # session, with a new probe, owns a new clock.
        probe = MpegTsPtsProbe(want_audio=False)
        probe.feed(video_packet(500 * PTS_HZ, discontinuity=True))
        self.assertFalse(probe.clock_valid)
        for tick in (501, 502, 503):
            self.assertEqual(probe.feed(video_packet(tick * PTS_HZ)), [])
        self.assertFalse(probe.clock_valid)
        self.assertEqual(probe.invalid_reason, "transport-discontinuity")

    def test_a_new_session_gets_a_new_clock(self) -> None:
        # G: "unusable for this session" is not "unusable forever".
        broken = MpegTsPtsProbe(want_audio=False)
        broken.feed(video_packet(500 * PTS_HZ, discontinuity=True))
        self.assertFalse(broken.clock_valid)

        fresh = MpegTsPtsProbe(want_audio=False)
        self.assertTrue(fresh.clock_valid)
        self.assertEqual(fresh.feed(video_packet(500 * PTS_HZ)), [500.0])

    def test_the_first_reason_is_the_one_reported(self) -> None:
        probe = MpegTsPtsProbe(want_audio=False)
        probe.feed(video_packet(500 * PTS_HZ, discontinuity=True))
        probe.feed(video_packet(50_000 * PTS_HZ - 700 * PTS_HZ))
        self.assertEqual(probe.invalid_reason, "transport-discontinuity")


class IngestAggregationTests(unittest.TestCase):
    """D/G: the consumers see one clock per leg, and the aggregate needs both."""

    def ingest(self, pumps, *, selectors=1, leg_role="media"):
        """A real ingest object with only the state snapshot() reads."""
        target = object.__new__(YtDlpLiveIngest)
        target.pumps = pumps
        target.selectors = [object()] * selectors
        target.leg_role = leg_role
        target.processes = []          # not running
        target.error = None
        target.log_tail = []
        target.source_pts = [[] for _ in pumps]
        target.source_pts_first = [None for _ in pumps]
        target._last_leg_marks = None
        target.format_selector = "test"
        target._version_label = lambda: "test"
        return target

    def pump(self, label, clock=(True, None), first=None, points=()):
        return SimpleNamespace(
            label=label,
            forwarded_bytes=0,
            last_byte_at=None,
            pts_clock=clock,
            _first=first,
            _points=list(points),
        )

    def test_a_real_probe_drives_the_legs_reported_validity(self) -> None:
        probe = MpegTsPtsProbe(want_audio=False)
        holder = SimpleNamespace(
            label="video", forwarded_bytes=0, last_byte_at=None,
            pts_clock=(probe.clock_valid, probe.invalid_reason),
        )
        ingest = self.ingest([holder])

        def refresh():
            holder.pts_clock = (probe.clock_valid, probe.invalid_reason)

        refresh()
        self.assertTrue(ingest.snapshot()["sourceClockValid"])
        probe.feed(video_packet(500 * PTS_HZ, discontinuity=True))
        refresh()
        snapshot = ingest.snapshot()
        self.assertFalse(snapshot["sourceClockValid"])
        self.assertEqual(snapshot["sourceClockReason"], "transport-discontinuity")
        self.assertFalse(snapshot["legThroughput"][0]["clockValid"])
        self.assertEqual(snapshot["legThroughput"][0]["clockReason"], "transport-discontinuity")

    def test_one_bad_pump_makes_the_leg_untrustworthy(self) -> None:
        # D: a leg's pumps are both needed by the packaging mux, so the leg is as
        # good as its worst clock -- and the exact offset needs both legs.
        ingest = self.ingest([self.pump("video"), self.pump("audio", clock=(False, "non-wrap-clock-reset"))])
        snapshot = ingest.snapshot()
        self.assertFalse(snapshot["sourceClockValid"])
        self.assertEqual(snapshot["sourceClockReason"], "non-wrap-clock-reset")

    def test_healthy_legs_report_a_valid_clock(self) -> None:
        ingest = self.ingest([self.pump("video"), self.pump("audio")])
        snapshot = ingest.snapshot()
        self.assertTrue(snapshot["sourceClockValid"])
        self.assertIsNone(snapshot["sourceClockReason"])

    def test_the_realed_history_survives_a_wrap_and_stays_bounded(self) -> None:
        # D: (sourcePtsLast - sourcePtsFirst) is what the extent and the progress
        # readouts are built from, so the unwrapping has to reach the CONSUMER,
        # not just the probe. A half-fix that only corrected the offset would leave
        # this negative.
        ingest = object.__new__(YtDlpLiveIngest)
        ingest.source_pts = []
        ingest.source_pts_first = []
        record = ingest._record_pts(0)
        probe = MpegTsPtsProbe(want_audio=False)

        points: list[float] = []
        for tick in (WRAP_TICKS - 90_000, WRAP_TICKS - 45_000, 0, 45_000, 90_000):
            found = probe.feed(video_packet(tick))
            points.extend(found)
            record(found)

        self.assertEqual(ingest.source_pts_first[0], (WRAP_TICKS - 90_000) / PTS_HZ)
        self.assertEqual(len(ingest.source_pts[0]), 5)
        # The extent the consumers build progress from, across the boundary. A
        # half-fix that only corrected the offset would leave this negative.
        self.assertAlmostEqual(points[-1] - points[0], 2.0, places=9)
        self.assertAlmostEqual(
            ingest.source_pts[0][-1] - ingest.source_pts_first[0], 2.0, places=6
        )

        # The bounded ring, on its own history. The origin is latched once and
        # must not move with the ring.
        record([1000.0 + index for index in range(200)])
        self.assertEqual(len(ingest.source_pts[0]), 128)
        self.assertEqual(
            ingest.source_pts_first[0],
            (WRAP_TICKS - 90_000) / PTS_HZ,
            "the first PTS is latched once, not recomputed from the ring",
        )

    def test_the_snapshot_reports_a_continuous_extent_across_the_wrap(self) -> None:
        probe = MpegTsPtsProbe(want_audio=False)
        ingest = self.ingest([self.pump("video")])
        record = ingest._record_pts(0)
        for tick in (WRAP_TICKS - 90_000, WRAP_TICKS - 45_000, 0, 45_000):
            record(probe.feed(video_packet(tick)))
        ingest.pumps[0].pts_clock = (probe.clock_valid, probe.invalid_reason)

        snapshot = ingest.snapshot()
        leg = snapshot["legThroughput"][0]
        self.assertAlmostEqual(leg["sourcePtsLast"] - leg["sourcePtsFirst"], 1.5, places=6)
        self.assertGreater(leg["sourcePtsLast"], leg["sourcePtsFirst"])


class AnchorRefusalTests(unittest.TestCase):
    """D: an untrusted clock must not fall back to the mapping it replaced."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        (root / "media").mkdir(parents=True, exist_ok=True)
        self.companion = CompanionApplication(
            SimpleNamespace(
                runtime_dir=root / "media",
                providers_file=root / "providers.json",
                publish_delay=2.0,
                cookies_from_browser=None,
            )
        )
        # The anchor's rate gate needs a real interval between samples, so the
        # clock is the one thing that is fake here -- the anchor itself is not.
        self.now = 0.0

    def wire(self, audio, video):
        self.companion.asr_audio_ingest = audio
        self.companion.source_ingest = video

    @staticmethod
    def leg(first, *, clock_valid=True, reason=None, pumps=1):
        return SimpleNamespace(
            source_pts_first=[first] * pumps,
            sourceClockValid=clock_valid,
            sourceClockReason=reason,
        )

    def bare_anchor(self) -> MediaAnchor:
        return MediaAnchor(clock=lambda: self.now)

    def anchor_on(self, companion) -> MediaAnchor:
        anchor = self.bare_anchor()
        anchor.set_exact_offset(companion._source_clock_offset)
        anchor.set_sampled_fallback_allowed(companion._sampled_fallback_allowed)
        return anchor

    def fill(self, anchor, count=6, step=1.0) -> None:
        """Converge the sampled window, so a refusal is what suppresses it."""
        for index in range(count):
            self.now += step
            anchor.add_sample(100.0 + index, 95.0 + index)

    def test_the_sampled_window_is_ready_until_the_clock_is_refused(self) -> None:
        # The premise of the next test, asserted rather than assumed: with a
        # healthy clock and no exact measurement the window IS the answer.
        anchor = self.bare_anchor()
        self.fill(anchor)
        self.assertIsNotNone(anchor.window_offset)
        self.assertTrue(anchor.ready)
        self.assertIsNotNone(anchor.offset)
        self.assertTrue(anchor.sampled_fallback_allowed)

    def test_a_refused_clock_stops_publishing_rather_than_falling_back(self) -> None:
        self.wire(
            self.leg(27886.406, clock_valid=False, reason="transport-discontinuity"),
            self.leg(27881.4, pumps=2),
        )
        anchor = self.anchor_on(self.companion)
        self.fill(anchor)

        # The window HAS converged -- and is deliberately not used. Order matters:
        # asking for the exact offset is what discovers the refusal, exactly as it
        # happens in production, where reading the offset is the only thing that
        # consults the clock at all.
        self.assertIsNotNone(anchor.window_offset)
        self.assertIsNone(anchor.exact_offset)
        self.assertFalse(anchor.sampled_fallback_allowed)
        self.assertFalse(anchor.ready)
        self.assertIsNone(anchor.offset)

    def test_the_refusal_is_recorded_once_per_transition(self) -> None:
        self.wire(
            self.leg(27886.406, clock_valid=False, reason="non-wrap-clock-reset"),
            self.leg(27881.4, pumps=2),
        )
        with patch.object(server_module.logbook, "record") as record:
            for _ in range(20):
                self.assertIsNone(self.companion._source_clock_offset())
        self.assertEqual(record.call_count, 1, "the poll runs every second; log transitions")
        self.assertIn("non-wrap-clock-reset", str(record.call_args))

    def test_a_session_that_has_not_measured_yet_is_not_a_refusal(self) -> None:
        # G: nothing has been measured, so nothing is refused and the anchor keeps
        # the sampled window it has always used.
        self.wire(None, None)
        anchor = self.anchor_on(self.companion)
        self.fill(anchor)
        self.assertIsNone(self.companion._source_clock_offset())
        self.assertTrue(self.companion._sampled_fallback_allowed())
        self.assertTrue(anchor.ready)
        self.assertIsNotNone(anchor.offset)

    def test_a_wrapped_pair_publishes_exact_positions_without_the_window(self) -> None:
        # D: the wrap case must go down the EXACT path, not the sampled one. An
        # unwrapped offset that still let the window take over would be a fix that
        # changes a number nobody reads.
        self.wire(self.leg(3.0), self.leg(WRAP_SECONDS - 2.0, pumps=2))
        anchor = self.anchor_on(self.companion)
        # Deliberately NOT filled: the whole point of an exact offset is that it
        # needs no window to converge, so the sampled path is never consulted.
        self.assertEqual(anchor.samples, 0)
        self.assertIsNone(anchor.window_offset)
        self.assertIsNotNone(anchor.exact_offset)
        self.assertAlmostEqual(anchor.exact_offset, 5.0, places=6)
        self.assertTrue(anchor.ready)
        self.assertAlmostEqual(anchor.offset, 5.0, places=6)

    def test_a_leg_with_no_legs_yet_is_unmeasured_not_broken(self) -> None:
        # G: snapshot() reports sourceClockValid=False with reason "no-legs" for an
        # ingest that has not started (or has been stopped). A clock that never
        # existed cannot have broken, so this must not switch the fallback off.
        self.wire(
            self.leg(27886.406, clock_valid=False, reason="no-legs"),
            self.leg(27881.4, pumps=2, clock_valid=False, reason="no-legs"),
        )
        self.assertIsNone(self.companion._source_clock_offset())
        self.assertTrue(self.companion._sampled_fallback_allowed())

    def test_a_new_session_re_establishes_trust(self) -> None:
        # D: the refusal is a statement about ONE session. The probes are rebuilt
        # with the legs, so the next session starts with a clean slate -- without
        # this, a single discontinuity would disable the sampled fallback for the
        # rest of the application's life.
        self.wire(
            self.leg(27886.406, clock_valid=False, reason="transport-discontinuity"),
            self.leg(27881.4, pumps=2),
        )
        self.assertIsNone(self.companion._source_clock_offset())
        self.assertFalse(self.companion._sampled_fallback_allowed())

        self.companion._reset_source_clock()
        self.assertTrue(
            self.companion._sampled_fallback_allowed(),
            "a new session must not inherit the previous session's refusal",
        )
        # And it is genuinely usable again, not merely permitted.
        self.wire(self.leg(3.0), self.leg(WRAP_SECONDS - 2.0, pumps=2))
        self.assertAlmostEqual(self.companion._source_clock_offset(), 5.0, places=6)

    def test_a_refused_clock_never_starts_or_restarts_a_leg(self) -> None:
        # D: the approved behaviour is to stop publishing, not to fix the source.
        # Restarting a download leg resets source_pts_first and would drop the
        # exact anchor to a path measured 4.44s off.
        calls: list[str] = []

        def refuse(name):
            def call(*_args, **_kwargs):
                calls.append(name)
                raise AssertionError(f"the refusal path must not call {name}")
            return call

        audio = self.leg(27886.406, clock_valid=False, reason="non-wrap-clock-reset")
        video = self.leg(27881.4, pumps=2)
        for leg in (audio, video):
            leg.start = refuse("start")
            leg.restart = refuse("restart")
            leg.stop = refuse("stop")
        self.wire(audio, video)

        anchor = self.anchor_on(self.companion)
        self.fill(anchor)
        for _ in range(5):
            self.companion._source_clock_offset()
        self.assertIsNone(anchor.offset)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
