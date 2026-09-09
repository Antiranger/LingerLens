"""Low overhead mapping from source media PTS to private playback time.

The mapper is deliberately passive: callers append boundaries as media is
packaged and map subtitle timestamps later. It never waits for a second leg,
does network I/O, or buffers media.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass


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
