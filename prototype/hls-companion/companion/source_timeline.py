"""Low overhead mapping from source media PTS to private playback time.

The mapper is deliberately passive: callers append boundaries as media is
packaged and map subtitle timestamps later. It never waits for a second leg,
does network I/O, or buffers media.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

TS_PACKET = 188
PTS_HZ = 90_000.0
_AUDIO_TYPES = {0x03, 0x04, 0x0F, 0x11}


def _decode_pts(value: bytes) -> int:
    return ((value[0] >> 1 & 7) << 30) | (value[1] << 22) | ((value[2] >> 1) << 15) | (value[3] << 7) | (value[4] >> 1)


class MpegTsPtsProbe:
    """Incrementally extract one media stream's PES PTS without copying media."""

    def __init__(self, *, want_audio: bool) -> None:
        self.want_audio = want_audio
        self._buffer = bytearray()
        self._media_pid: int | None = None
        self._last_pts: float | None = None

    def feed(self, data: bytes) -> list[float]:
        self._buffer.extend(data)
        found: list[float] = []
        while len(self._buffer) >= TS_PACKET:
            if self._buffer[0] != 0x47:
                sync = next((i for i in range(min(len(self._buffer), TS_PACKET)) if i + TS_PACKET * 2 < len(self._buffer) and self._buffer[i] == self._buffer[i + TS_PACKET] == self._buffer[i + TS_PACKET * 2] == 0x47), None)
                if sync is None:
                    del self._buffer[:-TS_PACKET * 2]
                    break
                del self._buffer[:sync]
            packet = bytes(self._buffer[:TS_PACKET])
            del self._buffer[:TS_PACKET]
            value = self._packet(packet)
            if value is not None:
                found.append(value)
        return found

    def _packet(self, packet: bytes) -> float | None:
        pid = ((packet[1] & 0x1F) << 8) | packet[2]
        start = bool(packet[1] & 0x40)
        adaptation = (packet[3] >> 4) & 3
        pos = 4
        if adaptation in (2, 3):
            pos += 1 + packet[pos]
        if adaptation not in (1, 3) or pos >= TS_PACKET:
            return None
        payload = packet[pos:]
        if start and payload[:3] == b"\x00\x00\x01" and len(payload) >= 14:
            stream_id = payload[3]
            is_audio = 0xC0 <= stream_id <= 0xDF
            if is_audio == self.want_audio and self._media_pid is None:
                self._media_pid = pid
        if pid != self._media_pid or not start or payload[:3] != b"\x00\x00\x01" or len(payload) < 14:
            return None
        if (payload[7] & 0xC0) != 0x80:
            return None
        value = _decode_pts(payload[9:14]) / PTS_HZ
        if self._last_pts is not None and value < self._last_pts and self._last_pts - value <= 100:
            return None
        self._last_pts = value
        return value


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
