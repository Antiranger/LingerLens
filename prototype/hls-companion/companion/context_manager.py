"""Source-ordered rolling translation continuity context.

Source text is registered before translation starts. Workers may complete out
of order and fill in translations later, without holding subsequent requests.
Entries use Caption Chunk identity and media time. Metadata-free callers such
as live-message translation never enter this history.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class ContextPair:
    source: str
    translation: str | None
    generation: int
    chunk_order: int
    media_t_end: float


class RollingContext:
    """Hold preceding source text and optional translations in source order."""

    def __init__(self, context_pairs: int = 10, context_seconds: float = 90.0) -> None:
        if context_pairs < 0:
            raise ValueError("context_pairs cannot be negative")
        if context_seconds < 0:
            raise ValueError("context_seconds cannot be negative")
        self.context_pairs = context_pairs
        self.context_seconds = context_seconds
        self._pairs: dict[tuple[int, int], ContextPair] = {}

    def add(
        self,
        source: str,
        translation: str | None,
        timestamp: float | None = None,
        *,
        generation: int | None = None,
        chunk_order: int | None = None,
        media_t_end: float | None = None,
    ) -> bool:
        """Register source text, or fill in its eventual translation.

        ``timestamp`` remains accepted as a compatibility alias for
        ``media_t_end``. A pair without generation/order metadata is not a
        Caption Chunk and is deliberately ignored, which keeps live messages
        and older ad-hoc callers out of subtitle continuity history.
        """
        if media_t_end is None:
            media_t_end = timestamp
        if generation is None or chunk_order is None:
            return False
        if generation < 0:
            raise ValueError("generation cannot be negative")
        if chunk_order < 1:
            raise ValueError("chunk_order must be positive")
        if media_t_end is None:
            raise ValueError("media_t_end is required for ordered context")
        key = (generation, chunk_order)
        previous = self._pairs.get(key)
        if (previous is not None and translation is None
                and previous.source == source and previous.media_t_end == float(media_t_end)):
            return False  # Re-enqueueing a source never erases its translation.
        if previous is not None and not (
            previous.translation is None and translation is not None
            and previous.source == source and previous.media_t_end == float(media_t_end)
        ):
            raise ValueError(
                f"duplicate context chunk order: generation={generation}, chunk_order={chunk_order}"
            )
        self._pairs[key] = ContextPair(
            source=source,
            translation=translation,
            generation=generation,
            chunk_order=chunk_order,
            media_t_end=float(media_t_end),
        )
        return True

    def history(
        self,
        now: float | None = None,
        *,
        generation: int | None = None,
        before_order: int | None = None,
        at_media_time: float | None = None,
        pair_limit: int | None = None,
        include_untranslated: bool = False,
    ) -> list[tuple[str, str | None]]:
        """Return earlier context, optionally including source still translating.

        Ordered caption callers must provide ``generation``, ``before_order``
        and ``at_media_time``. The old ``history(now)`` shape remains harmless:
        it returns no caption history rather than mixing generations or
        completion timestamps into a request.
        """
        del now
        if generation is None or before_order is None or at_media_time is None:
            return []
        limit = self.context_pairs if pair_limit is None else min(self.context_pairs, max(0, pair_limit))
        if limit == 0:
            return []
        cutoff = at_media_time - self.context_seconds
        eligible = sorted(
            (
                pair for pair in self._pairs.values()
                if pair.generation == generation
                and pair.chunk_order < before_order
                and cutoff <= pair.media_t_end <= at_media_time
                and (include_untranslated or pair.translation is not None)
            ),
            key=lambda pair: pair.chunk_order,
        )
        return [(pair.source, pair.translation) for pair in eligible[-limit:]]

    def trim(
        self,
        now: float | None = None,
        *,
        generation: int | None = None,
        at_media_time: float | None = None,
    ) -> None:
        """Discard obsolete generations and media-expired pairs."""
        if at_media_time is None:
            at_media_time = now
        if at_media_time is None:
            return
        cutoff = at_media_time - self.context_seconds
        if generation is None:
            self._pairs = {
                key: pair for key, pair in self._pairs.items()
                if pair.media_t_end >= cutoff
            }
            return
        self._pairs = {
            key: pair for key, pair in self._pairs.items()
            if (
                pair.generation == generation
                and pair.media_t_end >= cutoff
            ) or pair.generation > generation
        }

    def contains(self, *, generation: int, chunk_order: int) -> bool:
        """Whether a completed translation is available for this identity."""
        pair = self._pairs.get((generation, chunk_order))
        return pair is not None and pair.translation is not None

    def orders(self, generation: int) -> tuple[int, ...]:
        """Test/diagnostic view of retained source orders for one generation."""
        return tuple(sorted(order for pair_generation, order in self._pairs if pair_generation == generation))
