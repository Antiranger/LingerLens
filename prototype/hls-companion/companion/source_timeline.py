"""Low overhead mapping from source media PTS to private playback time.

The mapper is deliberately passive: callers append boundaries as media is
packaged and map subtitle timestamps later. It never waits for a second leg,
does network I/O, or buffers media.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

TS_PACKET = 188
PTS_HZ = 90_000
_AUDIO_TYPES = {0x03, 0x04, 0x0F, 0x11}

# The PES PTS field is 33 bits at 90kHz, so the source's own clock wraps every
# 2**33 / 90000 = 95443.717688... seconds = 26.512144 hours. A stream that runs
# long enough crosses it, and a leg that starts on the other side of the boundary
# from its partner reports an origin ~26.5 hours away from it. That is why these
# numbers exist; they are properties of the container format, not of a session.
PTS_MODULUS = 1 << 33
# Both guard sizes are the ones the surrounding code already uses, so no new
# threshold is introduced: 600s is what server.py already refuses as an
# implausible leg skew, and 100s is the tolerance this probe already had for a
# small backwards PTS (packets reordered in transit).
WRAP_EDGE_TICKS = 600 * PTS_HZ
REORDER_TICKS = 100 * PTS_HZ


def _decode_pts(value: bytes) -> int:
    return ((value[0] >> 1 & 7) << 30) | (value[1] << 22) | ((value[2] >> 1) << 15) | (value[3] << 7) | (value[4] >> 1)


def signed_pts_delta(a_ticks: int, v_ticks: int) -> int:
    """The shortest signed distance from ``v_ticks`` to ``a_ticks``, across the wrap.

    The clock is a 33-bit counter, so its values are only meaningful modulo
    ``PTS_MODULUS``: an origin of 3s and an origin of (W - 2s) are 5 seconds
    apart, not 26.5 hours. Subtracting the raw numbers gets that wrong by a whole
    period, which is exactly the failure a wrap landing between two legs produces.
    """
    half = PTS_MODULUS // 2
    return (a_ticks - v_ticks + half) % PTS_MODULUS - half


class MpegTsPtsProbe:
    """Incrementally extract one media stream's PES PTS without copying media.

    The probe owns the source clock for one download leg. Its job is to turn a
    wrapping 33-bit counter into a monotonic number of seconds, and to say so
    loudly -- once, permanently, for this session -- when it cannot.
    """

    def __init__(self, *, want_audio: bool) -> None:
        self.want_audio = want_audio
        self._buffer = bytearray()
        self._media_pid: int | None = None
        # O(1) clock state: the last trustworthy raw tick, how many full periods
        # have been crossed, and whether this clock may be used at all. No
        # per-packet history is kept; the diagnostic ring elsewhere is bounded.
        #
        # There is deliberately no "last returned value" here any more. R1 replaced
        # the float comparison that used it with one on raw ticks, and the field
        # survived for a while as state that was written and never read -- the kind
        # of leftover that makes the next reader think something consumes it.
        self._previous_raw: int | None = None
        self._wrap_ticks = 0
        self.clock_valid = True
        self.invalid_reason: str | None = None

    def feed(self, data: bytes) -> list[float]:
        self._buffer.extend(data)
        found = []
        cursor = 0
        size = len(self._buffer)
        while size - cursor >= TS_PACKET:
            if self._buffer[cursor] != 0x47:
                sync = next((i for i in range(cursor, size - 2 * TS_PACKET)
                             if self._buffer[i] == self._buffer[i + TS_PACKET] == self._buffer[i + 2 * TS_PACKET] == 0x47), None)
                if sync is None:
                    cursor = max(cursor, size - 2 * TS_PACKET)
                    break
                cursor = sync
            value = self._packet(bytes(self._buffer[cursor:cursor + TS_PACKET]))
            cursor += TS_PACKET
            if value is not None:
                found.append(value)
        # Compact once per read, not once per 188-byte packet.
        del self._buffer[:cursor]
        return found

    def _packet(self, packet: bytes) -> float | None:
        if len(packet) != TS_PACKET or packet[0] != 0x47 or packet[1] & 0x80:
            return None
        pid = ((packet[1] & 0x1F) << 8) | packet[2]
        start = bool(packet[1] & 0x40)
        adaptation = (packet[3] >> 4) & 3
        pos = 4
        discontinuity = False
        if adaptation in (2, 3):
            length = packet[pos]
            if pos + 1 + length > TS_PACKET:
                return None
            # The discontinuity_indicator is bit 7 of the adaptation field's
            # first flag byte, which the code below steps over. It has to be read
            # here or it is lost, and it is the only signal that separates a real
            # clock reset from a backwards jump in the numbers.
            if length > 0:
                discontinuity = bool(packet[pos + 1] & 0x80)
            pos += 1 + length
        if discontinuity and pid == self._media_pid:
            self._invalidate("transport-discontinuity")
            return None
        if adaptation not in (1, 3) or pos >= TS_PACKET:
            return None
        payload = packet[pos:]
        if start and payload[:3] == b"\x00\x00\x01" and len(payload) >= 14:
            stream_id = payload[3]
            is_audio = 0xC0 <= stream_id <= 0xDF
            selected_type = is_audio if self.want_audio else 0xE0 <= stream_id <= 0xEF
            if selected_type and self._media_pid is None:
                self._media_pid = pid
        if pid != self._media_pid:
            # Another PID's discontinuity flag describes a different stream and
            # must not invalidate this clock.
            return None
        if discontinuity:
            self._invalidate("transport-discontinuity")
            return None
        if not start or payload[:3] != b"\x00\x00\x01" or len(payload) < 14:
            return None
        flags = payload[7] & 0xC0
        needed = 10 if flags == 0xC0 else 5
        if flags not in (0x80, 0xC0) or payload[8] < needed or len(payload) < 9 + needed:
            return None
        pts = payload[9:14]
        prefix = 0x30 if flags == 0xC0 else 0x20
        if pts[0] & 0xF0 != prefix or not all(pts[i] & 1 for i in (0, 2, 4)):
            return None
        if not self.clock_valid:
            # Nothing here may quietly re-establish trust: the same packets that
            # looked fine before a reset still look fine after one. Only a new
            # session, with a new probe, owns a new clock.
            return None
        return self._unwrap(_decode_pts(payload[9:14]))

    def _unwrap(self, raw: int) -> float | None:
        previous = self._previous_raw
        if previous is not None:
            if raw < previous:
                forward_wrap = (
                    previous >= PTS_MODULUS - WRAP_EDGE_TICKS
                    and raw <= WRAP_EDGE_TICKS
                )
                if forward_wrap:
                    self._wrap_ticks += PTS_MODULUS
                elif previous - raw <= REORDER_TICKS:
                    # The long-standing tolerance for a small backwards PTS:
                    # ignore the packet and do NOT push the clock backwards.
                    return None
                else:
                    self._invalidate("non-wrap-clock-reset")
                    return None
            elif raw - previous > PTS_MODULUS // 2:
                # A packet from the epoch before the wrap, arriving after it.
                # Counting another period here would add 26.5 hours, and treating
                # it as a fresh clock would move the mapping backwards; both are
                # worse than ignoring it under the same small-reorder rule.
                late_previous_epoch = (
                    previous <= WRAP_EDGE_TICKS
                    and raw >= PTS_MODULUS - WRAP_EDGE_TICKS
                )
                backwards = previous + PTS_MODULUS - raw
                if late_previous_epoch and 0 <= backwards <= REORDER_TICKS:
                    return None
                self._invalidate("ambiguous-reverse-wrap-or-reset")
                return None
        self._previous_raw = raw
        return (raw + self._wrap_ticks) / PTS_HZ

    def _invalidate(self, reason: str) -> None:
        """Mark this leg's clock untrustworthy, recording the FIRST reason only."""
        if not self.clock_valid:
            return
        self.clock_valid = False
        self.invalid_reason = reason


@dataclass(frozen=True)
class TimelinePiece:
    source_start: float
    source_end: float | None
    private_start: float
    private_end: float | None


class SourceTimeline:
    """Piecewise-linear source-PTS to private-media mapping."""

    def __init__(self) -> None:
        self._pieces: list[TimelinePiece] = []
        self._starts: list[float] = []

    @property
    def pieces(self) -> tuple[TimelinePiece, ...]:
        return tuple(self._pieces)

    def append(self, piece: TimelinePiece) -> None:
        if piece.source_end is not None and piece.source_end < piece.source_start:
            raise ValueError("source_end must not precede source_start")
        if piece.private_end is not None and piece.private_end < piece.private_start:
            raise ValueError("private_end must not precede private_start")
        if self._pieces and piece.source_start < self._pieces[-1].source_start:
            raise ValueError("source pieces must be appended in source order")
        self._pieces.append(piece)
        self._starts.append(piece.source_start)

    def map(self, source_seconds: float) -> float | None:
        if not self._pieces:
            return None
        index = bisect_right(self._starts, float(source_seconds)) - 1
        if index < 0:
            return None
        piece = self._pieces[index]
        if piece.source_end is not None and source_seconds > piece.source_end:
            return None
        if piece.source_end is None or piece.private_end is None:
            return piece.private_start + (source_seconds - piece.source_start)
        source_span = piece.source_end - piece.source_start
        private_span = piece.private_end - piece.private_start
        ratio = private_span / source_span if source_span else 1.0
        return piece.private_start + (source_seconds - piece.source_start) * ratio
