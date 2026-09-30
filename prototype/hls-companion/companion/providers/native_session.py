"""Provider-side translation served from the ASR session itself.

A few realtime models recognise speech *and* emit its translation on the same
WebSocket: Soniox ``stt-rt-v5`` with ``translation`` enabled, and Alibaba's
``qwen3.8-livetranslate-flash-realtime``. For those Profiles there is no
second request to make -- the translation is already arriving, interleaved with
the recognition it belongs to.

This module keeps that translation on the ASR session's own terms:

* :class:`NativeTranslationBus` is the ordered ledger the Adapter writes into
  and the pipeline reads from. One Provider segment (one utterance id) is one
  entry with one growing source text and one growing translation text.
* :class:`NativeSessionTranslation` presents the ledger as an ordinary
  Translation Provider, so the pipeline's existing worker, deadline, cue-state
  and store-update machinery is reused unchanged instead of growing a second
  translation path.

Ownership: the *Adapter* decides what a native translation segment is (its
protocol already segmented the audio) and, where the protocol implies one, what
the translation it emitted covers -- those are the ``(source, translation)``
anchors it hands over. The caption chunker is kept to one Provider segment per
cue for these Profiles, because a cue that spans two segments or half of one
matches nothing the Provider aligned. A cue that still lands between two
alignment points keeps its source text alone rather than a guessed fragment.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections import OrderedDict
from typing import Callable

from ..subtitle_text import subtitle_match_key
from .base import (
    ProviderRefusalError,
    TranslationCapabilities,
    TranslationLanguageCapabilities,
    TranslationProvider,
    TranslationRequest,
    TranslationResult,
)

# Bound every input path, including final-only, failed and abandoned segments.
# Prefer completed records and protect active resolvers until overload makes
# eviction unavoidable; an evicted request fails source-only, never misjoins.
_LEDGER_MAX = 256
# How long a resolver sleeps between re-checks of a segment that is still open.
# The pipeline's own deadline bounds the total wait; this only bounds the
# latency of noticing that the Provider appended more translation text.
_POLL_SECONDS = 0.05


@dataclasses.dataclass
class _Segment:
    source: str = ""
    translation: str = ""
    closed: bool = False
    anchors: tuple[tuple[str, str], ...] = ()
    anchors_trusted: bool = False
    """Whether ``anchors`` are safe correspondence boundaries.

    Soniox can prove such boundaries from the documented ordering of its unified
    source/translation token stream. Other adapters may publish arrival-time
    snapshots for diagnostics; those stay untrusted and are never consumed.
    """
    consumed_source: str = ""
    consumed_translation: str = ""
    """Raw prefixes already handed to a cue, so the next cue of the same segment
    is given what is left of it instead of all of it again."""

    @property
    def consumed(self) -> bool:
        return bool(self.consumed_source or self.consumed_translation)


class NativeTranslationBus:
    """Ordered ledger of Provider-side (source, translation) segments.

    Written by ASR Adapters, read by :class:`NativeSessionTranslation`. All
    methods are safe to call from one event loop; the pipeline and the ASR
    event consumer share it.
    """

    def __init__(self) -> None:
        self._segments: "OrderedDict[str, _Segment]" = OrderedDict()
        self._changed = asyncio.Event()
        self._lock = asyncio.Lock()
        self.generation = 0
        self._retired: "OrderedDict[str, None]" = OrderedDict()
        self._waiters: dict[tuple[int, str | None], int] = {}
        self.unaligned_total = 0
        self.closed_without_translation_total = 0

    # -- Adapter side ----------------------------------------------------

    def record(
        self,
        *,
        item_id: str,
        source_text: str,
        translation: str,
        anchors: tuple[tuple[str, str], ...] = (),
        anchors_trusted: bool = False,
    ) -> None:
        """Replace one segment's current source, translation and alignment.

        Replace, not append: every Adapter that reports native translation
        accumulates the Provider's own deltas per utterance and republishes the
        running totals, exactly as it already does for ``text``/``stash``.
        """
        key = str(item_id)
        if key in self._retired:
            return
        segment = self._segments.get(key)
        if segment is None:
            segment = _Segment()
            self._segments[key] = segment
        # Translation-only frames have no source snapshot.
        if source_text:
            segment.source = source_text
        segment.translation = translation
        if anchors:
            segment.anchors = tuple(anchors)
        if anchors_trusted:
            segment.anchors_trusted = True
        self._changed.set()
        self._reclaim()

    def close_item(
        self,
        item_id: str,
        *,
        source_text: str = "",
        translation: str = "",
        anchors: tuple[tuple[str, str], ...] = (),
        anchors_trusted: bool = False,
    ) -> None:
        """Mark a Provider segment complete: no further text will arrive."""
        key = str(item_id)
        if key in self._retired:
            return
        segment = self._segments.get(key)
        if segment is None:
            segment = _Segment()
            self._segments[key] = segment
        if source_text:
            segment.source = source_text
        if translation:
            segment.translation = translation
        if anchors:
            segment.anchors = tuple(anchors)
        if anchors_trusted:
            segment.anchors_trusted = True
        was_closed = segment.closed
        segment.closed = True
        if not was_closed and segment.source and not segment.translation:
            self.closed_without_translation_total += 1
        self._changed.set()
        self._reclaim()

    def reset(self, generation: int | None = None) -> None:
        """Drop everything: a new Provider session renumbers its items."""
        self._segments.clear()
        self._retired.clear()
        self.generation = self.generation + 1 if generation is None else generation
        self._changed.set()

    @property
    def pending(self) -> int:
        """Segments recorded but never consumed; diagnostics only."""
        return sum(1 for segment in self._segments.values() if not segment.consumed)

    @property
    def waiting(self) -> int:
        """Active cue resolvers waiting for native translation evidence."""
        return sum(self._waiters.values())

    def preview(self, *, item_id: str | None, source_text: str) -> str | None:
        """Return the latest safe native translation prefix without consuming it.

        Used only for the on-screen draft of an utterance that is still open.
        A preview may cover less source than ``source_text`` but never more: the
        viewer can see a translation only for words already included in the held
        source draft. Consumed cue prefixes are removed from both sides, and this
        method never advances those cursors.
        """
        key = self._key_for(item_id, source_text)
        if key is None:
            return None
        segment = self._segments[key]
        heard = subtitle_match_key(source_text)
        if not heard:
            return None
        best: str | None = None
        for source, translation in _boundaries(segment):
            if (not source.startswith(segment.consumed_source)
                    or not translation.startswith(segment.consumed_translation)):
                continue
            source_remainder = source[len(segment.consumed_source):]
            translation_remainder = translation[len(segment.consumed_translation):].strip()
            source_key = subtitle_match_key(source_remainder)
            if not source_key or not translation_remainder:
                continue
            if heard.startswith(source_key):
                best = translation_remainder
        return best

    # -- Pipeline side ---------------------------------------------------

    async def resolve(
        self,
        *,
        item_id: str | None,
        source_text: str,
        deadline_monotonic: float | None,
        monotonic: Callable[[], float] = time.monotonic,
        generation: int | None = None,
    ) -> str | None:
        """Return the Provider's translation for one cue, or None if there is none.

        Returns as soon as one of the Provider's own alignment points covers the
        cue, which for a settled anchor is before the segment closes; only a cue
        that has to wait for more of the segment sleeps. Once the segment is
        closed every boundary it will ever have is known, so a cue that matches
        none of them has a terminal answer rather than a reason to keep waiting.
        """
        expected = self.generation if generation is None else generation
        waiter = (expected, item_id)
        self._waiters[waiter] = self._waiters.get(waiter, 0) + 1
        try:
            while True:
                if expected != self.generation:
                    return None
                async with self._lock:
                    text = self._take(item_id, source_text)
                    if text:
                        return text
                    if self._is_finished(item_id):
                        key = str(item_id) if item_id is not None else None
                        segment = self._segments.get(key) if key is not None else None
                        if segment is not None and segment.translation:
                            self.unaligned_total += 1
                        return None
                remaining = _POLL_SECONDS if deadline_monotonic is None else deadline_monotonic - monotonic()
                if remaining <= 0:
                    return None
                self._changed.clear()
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=min(_POLL_SECONDS, remaining))
                except asyncio.TimeoutError:
                    pass
        finally:
            count = self._waiters[waiter] - 1
            if count:
                self._waiters[waiter] = count
            else:
                self._waiters.pop(waiter)
            self._reclaim()

    # -- internals -------------------------------------------------------

    def _key_for(self, item_id: str | None, source_text: str) -> str | None:
        """Resolve the segment a cue belongs to.

        The item id is the Provider's own join key and is always preferred. When
        an Adapter reports none, the oldest unconsumed segment whose source is
        compatible with the cue is used instead of failing the cue outright.
        """
        if item_id is not None:
            return str(item_id) if str(item_id) in self._segments else None
        needle = subtitle_match_key(source_text)
        if not needle:
            return None
        for key, segment in self._segments.items():
            if segment.consumed:
                continue
            haystack = subtitle_match_key(segment.source)
            if haystack and needle == haystack:
                return key
        return None

    def _take(self, item_id: str | None, source_text: str) -> str | None:
        """Hand a cue the translation the Provider has for exactly its own text.

        A cue matches a boundary when its text equals the segment source at that
        boundary minus what an earlier cue of the same segment already took. The
        boundary is the Provider's, never one interpolated here: a realtime
        translation model states that its translated tokens are not one-to-one
        with the spoken ones, so a cut between two of its alignment points has
        no translation to give and keeps its source text alone.
        """
        key = self._key_for(item_id, source_text)
        if key is None:
            return None
        segment = self._segments[key]
        want = subtitle_match_key(source_text)
        if not want:
            return None
        for source, translation in _boundaries(segment):
            if (not source.startswith(segment.consumed_source)
                    or not translation.startswith(segment.consumed_translation)):
                continue  # a Provider rewrite this ledger can only follow by mis-slicing
            if subtitle_match_key(source[len(segment.consumed_source):]) != want:
                continue
            resolved = translation[len(segment.consumed_translation):].strip()
            segment.consumed_source = source
            segment.consumed_translation = translation
            return resolved or None
        return None

    def _is_finished(self, item_id: str | None) -> bool:
        if item_id is None:
            return False
        if str(item_id) in self._retired:
            return True
        segment = self._segments.get(str(item_id))
        return segment is not None and segment.closed

    def _reclaim(self) -> None:
        if len(self._segments) <= _LEDGER_MAX:
            return
        def priority(key: str) -> int:
            segment = self._segments[key]
            if self._waiters.get((self.generation, key)):
                return 3
            if segment.closed and (segment.consumed or not segment.translation):
                return 0
            return 1 if segment.closed else 2

        for key in sorted(self._segments, key=priority)[:len(self._segments) - _LEDGER_MAX]:
            del self._segments[key]
            self._retired[key] = None
        while len(self._retired) > _LEDGER_MAX:
            self._retired.popitem(last=False)


def _boundaries(segment: _Segment) -> list[tuple[str, str]]:
    """Return only correspondence points the Adapter is allowed to assert.

    Trusted anchors are cumulative prefixes derived from Provider ordering, not
    wall-clock coincidence. They let a hard-deadline cue consume exactly the
    prefix Soniox already translated. A completed whole segment remains the
    final boundary. Prefix rewrites are rejected rather than guessed.
    """
    points: list[tuple[str, str]] = []
    if segment.anchors_trusted:
        previous_source = ""
        previous_translation = ""
        for source, translation in segment.anchors:
            if not source or not translation:
                continue
            if previous_source and not source.startswith(previous_source):
                continue
            if previous_translation and not translation.startswith(previous_translation):
                continue
            pair = (source, translation)
            if not points or points[-1] != pair:
                points.append(pair)
            previous_source, previous_translation = source, translation
    if segment.closed and segment.source and segment.translation:
        whole = (segment.source, segment.translation)
        if not points or points[-1] != whole:
            points.append(whole)
    return points


class NativeSessionTranslation(TranslationProvider):
    is_native_session = True

    """A Translation Provider backed by the ASR session's own translation.

    Normally no second model: it resolves each cue from
    the segments the ASR Adapter already recorded. It exists so the pipeline can
    treat "the Provider translates" and "a translation model translates"
    identically -- same worker, same deadline, same cue states, same failures.
    An explicitly configured fallback may translate unaligned/late cues; this
    is a separate, billable request and is never enabled implicitly.
    """

    def __init__(
        self,
        bus: NativeTranslationBus,
        *,
        provider_id: str,
        label: str,
        model: str,
        target_tags: tuple[str, ...] | None = None,
        fallback: TranslationProvider | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.bus = bus
        self.id = provider_id
        self.label = label
        self.model = model
        self.target_tags = target_tags
        self.fallback = fallback
        self._monotonic = monotonic

    @property
    def capabilities(self) -> TranslationCapabilities:
        return TranslationCapabilities(
            # The Provider keeps its own context across the session; LingerLens
            # must not also send history, because the corpus/term list it was
            # configured with is the context the Provider actually honours.
            rolling_context=bool(self.fallback and self.fallback.capabilities.rolling_context),
            glossary=bool(self.fallback and self.fallback.capabilities.glossary),
            domains=False,
            json_output=False,
            max_input_chars=4096,
            language=TranslationLanguageCapabilities(
                source_tags=None,
                target_tags=self.target_tags,
                open_world_prompting=self.target_tags is None,
                tier="provider_claimed",
            ),
        )

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        started = self._monotonic()
        deadline = request.deadline_monotonic
        if self.fallback is not None:
            # A locally cut prefix has no safe slice of the native translation.
            # Do not spend its remaining display margin waiting for one.
            cut = request.cut_reason == "hard_deadline" or request.starts_mid_sentence is True or request.ends_mid_sentence is True
            allowance = 0.0 if cut else 2.0
            if deadline is not None:
                allowance = min(allowance, max(0.0, deadline-started-3.0))
            deadline = started + allowance
        text = await self.bus.resolve(
            item_id=request.item_id, source_text=request.source_text,
            deadline_monotonic=deadline, monotonic=self._monotonic,
            generation=request.generation,
        )
        if not text and self.fallback is not None:
            if request.generation is not None and request.generation != self.bus.generation:
                raise ProviderRefusalError("obsolete native translation generation")
            if request.deadline_monotonic is not None and self._monotonic() >= request.deadline_monotonic:
                raise ProviderRefusalError("native fallback deadline expired")
            # Same cue, source text, generation and original total deadline.
            # The actual fallback provider id/usage is returned for accounting.
            return await self.fallback.translate(request)
        if not text:
            raise ProviderRefusalError(
                "the ASR Provider reported no translation for this utterance"
            )
        return TranslationResult(
            text=text,
            provider_id=self.id,
            latency_ms=int(max(0.0, self._monotonic() - started) * 1000),
            usage=None,
        )
