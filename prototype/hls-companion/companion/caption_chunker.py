"""Provider-neutral, deterministic Caption Chunk construction.

The chunker consumes only normalized recognition evidence. It has no provider,
network, pipeline, or wall-clock dependencies, which keeps the publication
policy testable and prevents Adapter-specific segmentation strategies.
"""

from __future__ import annotations

import dataclasses
import difflib
import math
import re
import unicodedata
from collections import Counter, deque
from typing import Literal

from .languages import primary_subtag
from .clause_boundaries import TERMINALS, SEPARATORS, caption_boundaries
from .providers.base import CaptionCutReason, CaptionObservation, RecognitionToken

GuaranteeTier = Literal["strict", "best_effort"]

SOFT_TARGET_SPAN = 6.0
# The backstop the soft span never had. A caption whose text is confirmed but
# which still offers no lexical boundary is emitted anyway once it has been
# open this long. Without it a speaker who never pauses holds the caption open
# indefinitely, and by the time a boundary finally arrives the cue is older
# than the display budget: translation is then refused without a Provider call
# and, because the renderer admits only finished translations, nothing at all
# reaches the screen. Deliberately longer than SOFT_TARGET_SPAN, which only
# counts an overshoot; this is the point at which waiting stops paying.
HARD_DEADLINE_SECONDS = 7.0
_CONTINUATION_GAP = 1.2
# Arrival replay: <=0.6s recovered no cross-final continuations; 1.2s
# recovered several, including a Japanese topic + predicate, at lower wait
# than 2s. Only finalized residuals receive this bounded opportunity.
_RESIDUAL_GRACE = 1.2
_TIMESTAMP_JITTER = 0.06

# Session-length bounds. A live stream runs for hours, so every per-utterance
# ledger must be reclaimed on evidence availability rather than on Provider
# lifecycle events that some Providers never send. Providers such as Deepgram
# emit token_snapshot/stable_token_delta/endpoint but never utterance_final, so
# a "closed" flag is never set and any eviction gated on it is unreachable
# (measured: 64x per-utterance slowdown across an 8h session).
_ITEM_LEDGER_MAX = 512
# Above this many items, entries untouched for this long are dropped first.
_ITEM_IDLE_SECONDS = 30.0
# Speaker lanes carry deliberate cross-utterance continuity and are kept while
# the speaker is still talking; item lanes cannot receive more units once empty.
_SPEAKER_LANE_IDLE_SECONDS = 120.0
# Chunk spans are a rolling telemetry sample, not a transcript ledger. The
# cumulative count lives in _caption_chunk_total so percentiles reflect recent
# behaviour instead of all history.
_SPAN_SAMPLE_MAX = 2048

_STRONG_PUNCTUATION = TERMINALS
_WEAK_PUNCTUATION = SEPARATORS
_CJK_LANGUAGES = frozenset({"zh", "ja", "ko"})



@dataclasses.dataclass(frozen=True)
class CaptionChunk:
    text: str
    begin_pcm: float
    end_pcm: float
    language: str | None = None
    speaker: str | None = None
    cut_reason: CaptionCutReason = "clause_boundary"
    starts_mid_sentence: bool | None = None
    ends_mid_sentence: bool | None = None
    guarantee_tier: GuaranteeTier = "strict"
    exact_timing: bool = False


@dataclasses.dataclass(frozen=True)
class ChunkerDecision:
    chunks: tuple[CaptionChunk, ...] = ()
    pending_evidence: bool = False


@dataclasses.dataclass(frozen=True)
class CaptionChunkerTelemetry:
    """Observability for the chunking policy.

    ``pending_evidence`` counts utterances that have been open past
    SOFT_TARGET_SPAN with no safe cut available. It is informational: nothing
    is force-cut on a timer, so a non-zero value is not a failure and no
    "hard cap" exists to violate (the former hard_cap_cuts and
    manual_hard_commits counters were never incremented by any code path).
    """

    caption_chunks: int = 0
    chunk_span_p50: float = 0.0
    chunk_span_p95: float = 0.0
    chunk_span_max: float = 0.0
    chunk_cut_reasons: tuple[tuple[str, int], ...] = ()
    pending_evidence_over_soft_span: int = 0
    span_over_soft_target: int = 0
    local_agreement_commits: int = 0
    local_agreement_rewrites: int = 0
    residual_flushes: int = 0
    final_reconciliation_conflicts: int = 0


@dataclasses.dataclass
class _Unit:
    text: str
    begin: float | None
    end: float | None
    language: str | None
    speaker: str | None
    provider_stable: bool
    item_id: str = ""
    is_piece: bool = False
    exact_timing: bool = True
    endpoint: bool = False


@dataclasses.dataclass
class _ItemState:
    units: list[_Unit] = dataclasses.field(default_factory=list)
    accepted_parts: list[str] = dataclasses.field(default_factory=list)
    accepted_tokens: list[RecognitionToken] = dataclasses.field(default_factory=list)
    previous_tokens: tuple[RecognitionToken, ...] | None = None
    token_committed_count: int = 0
    previous_text: str | None = None
    text_committed: str = ""
    stable_prefix_committed: str = ""
    language: str | None = None
    speaker: str | None = None
    begin_pcm: float | None = None
    closed: bool = False
    accepted_end: float | None = None
    pending_reported: bool = False
    """Whether this item has already been counted as open past SOFT_TARGET_SPAN."""
    """Monotonic time of the last evidence touching this item.

    Drives age-based reclamation so a Provider that never closes an item still
    cannot grow the ledger without bound.
    """
    last_touched: float = 0.0


@dataclasses.dataclass
class _CaptionState:
    units: list[_Unit] = dataclasses.field(default_factory=list)
    begin_pcm: float | None = None
    last_end_pcm: float | None = None
    deadline: float | None = None
    hard_deadline: float | None = None
    """Wall-clock backstop armed when the lane first receives text.

    Kept separate from ``deadline``: that one is the residual grace, re-armed
    on every unit and only once the Provider has closed the item, so it can
    never rescue an utterance the Provider is still holding open.
    """
    last_chunk_ended_mid: bool | None = None
    emitted_chunks: int = 0
    pending_reported: bool = False
    last_touched: float = 0.0
    """Monotonic time of the last unit routed into this lane."""


class CaptionChunker:
    """Convert normalized recognition evidence into immutable Caption Chunks."""

    def __init__(self, *, realtime: bool = False) -> None:
        self.realtime = realtime
        self.generation: int | None = None
        self._items: dict[str, _ItemState] = {}
        self._captions: dict[tuple[str, str | None], _CaptionState] = {}
        self._spans: deque[float] = deque(maxlen=_SPAN_SAMPLE_MAX)
        self._caption_chunk_total = 0
        self._cut_reasons: Counter[str] = Counter()
        self._span_overshoots = 0
        self._pending_evidence_over_span = 0
        self._local_agreement_commits = 0
        self._local_agreement_rewrites = 0
        self._residual_flushes = 0
        self._final_reconciliation_conflicts = 0

    def reset(self, generation: int) -> None:
        self.generation = generation
        self._items.clear()
        self._captions.clear()
        # Span samples belong to one generation's audio clock; the cumulative
        # chunk count is a session statistic and deliberately survives a reset.
        self._spans.clear()

    def open_item(self, item_id: str, begin_pcm: float | None) -> None:
        """Register a timed utterance before lexical evidence arrives.

        VAD starts establish the origin for pending-evidence telemetry;
        advancing audio never forces a caption cut or ASR commit.
        """
        if self.generation is None or not item_id:
            return
        state = self._items.setdefault(item_id, _ItemState())
        if state.closed:
            return
        state.begin_pcm = _minimum_time(state.begin_pcm, begin_pcm)

    def observe(self, observation: CaptionObservation, *, now: float = 0.0) -> ChunkerDecision:
        if self.generation is None:
            self.generation = observation.generation
        if observation.generation != self.generation:
            return ChunkerDecision()
        state = self._items.setdefault(observation.item_id, _ItemState())
        if state.closed:
            return ChunkerDecision()
        state.begin_pcm = _minimum_time(state.begin_pcm, observation.begin_pcm)
        state.language = observation.language or _dominant(t.language for t in observation.tokens) or state.language
        state.speaker = observation.speaker or state.speaker
        if observation.kind == "stable_token_delta":
            for token in observation.tokens:
                if token.text:
                    self._accept_token(state, token, provider_stable=True)
        elif observation.kind == "token_snapshot":
            if self.realtime:
                for token in observation.tokens:
                    if token.provider_stable:
                        self._accept_token(state, token, provider_stable=True)
            else:
                self._observe_token_snapshot(state, observation.tokens)
        elif observation.kind == "stable_prefix_snapshot":
            self._observe_stable_prefix(state, observation)
        elif observation.kind == "text_snapshot":
            if not self.realtime:
                self._observe_text_snapshot(state, observation)
        elif observation.kind == "utterance_final":
            self._reconcile_final(state, observation)
            state.closed = True

        chunks = list(self.expire(now).chunks)
        affected = set()
        for unit in state.units:
            unit.item_id = observation.item_id
            unit.language = unit.language or state.language
            unit.speaker = unit.speaker or state.speaker
            # A speaker label selects a possible continuation, not an
            # indefinitely reusable expression. Unknown labels stay item-local.
            key = ("speaker:" + unit.speaker if unit.speaker else "item:" + unit.item_id,
                   _primary_language(unit.language))
            caption = self._captions.setdefault(key, _CaptionState())
            if (caption.units and caption.units[-1].item_id != unit.item_id
                    and unit.begin is not None and caption.last_end_pcm is not None
                    and (unit.begin - caption.last_end_pcm > _CONTINUATION_GAP
                         or unit.begin < caption.last_end_pcm - _TIMESTAMP_JITTER)):
                chunks.append(self._close_caption(key))
                caption = self._captions.setdefault(key, _CaptionState())
            caption.units.append(unit)
            caption.last_end_pcm = _maximum_time(caption.last_end_pcm, unit.end)
            caption.deadline = None
            if caption.hard_deadline is None:
                # Armed once per lane, so it bounds how long THIS caption may stay
                # open rather than being pushed back by every new unit.
                caption.hard_deadline = now + HARD_DEADLINE_SECONDS
            caption.last_touched = now
            affected.add(key)
        state.units.clear()
        if observation.kind in {"endpoint", "utterance_final"}:
            for key, caption in self._captions.items():
                for unit in reversed(caption.units):
                    if unit.item_id == observation.item_id:
                        unit.endpoint = True
                        affected.add(key)
                        break
        for key in sorted(affected, key=repr):
            caption = self._captions[key]
            chunks.extend(self._drain_ready(caption))
            # An endpoint releases only evidence already stable. It does not
            # close the item: e.g. VAD stop can precede final transcription.
            if self.realtime and caption.units and observation.kind in {"endpoint", "utterance_final"}:
                if all(u.item_id == observation.item_id for u in caption.units):
                    chunks.append(self._emit(caption, len(caption.units) - 1, "utterance_endpoint"))
            if caption.units and all(
                self._items[u.item_id].closed for u in caption.units if u.item_id in self._items
            ):
                if caption.deadline is None:
                    caption.deadline = now + _RESIDUAL_GRACE
            else:
                caption.deadline = None
        if state.closed:
            # Keep only a bounded tombstone ledger for duplicate finals.
            state.accepted_parts.clear()
            state.accepted_tokens.clear()
            state.previous_tokens = None
            state.previous_text = None
            state.text_committed = state.stable_prefix_committed = ""
        state.last_touched = now
        # Reclamation runs on availability, not on `closed`: several Providers
        # (Deepgram among them) never send utterance_final, so a guard on that
        # flag reclaimed nothing and the ledger grew by one entry per utterance
        # for the whole session.
        self._reclaim_items(now)
        # An item lane is keyed by the utterance's own item id, so once its
        # units are gone no further unit can ever be routed into it. Speaker
        # lanes are keyed by label and legitimately span utterances, so they
        # survive until the speaker has been quiet for a while.
        for key in affected:
            caption = self._captions.get(key)
            if caption is None:
                continue
            caption.last_touched = now
            if key[0].startswith("item:") and not caption.units:
                del self._captions[key]
        self._reclaim_lanes(now)
        return ChunkerDecision(tuple(chunks))

    def _reclaim_items(self, now: float) -> None:
        """Bound the per-utterance item ledger.

        Items are needed while a lane may still reference them and while a
        duplicate final may still arrive. Neither lasts indefinitely, so once
        the ledger exceeds its bound the least recently touched entries are
        dropped. ``closed`` items are the strongest candidates and go first.
        """
        if len(self._items) <= _ITEM_LEDGER_MAX:
            return
        target = _ITEM_LEDGER_MAX // 2
        ordered = sorted(self._items.items(), key=lambda kv: kv[1].last_touched)
        for item_id, item_state in ordered:
            if len(self._items) <= target:
                break
            # Never drop the item currently being observed.
            if item_state.last_touched >= now:
                continue
            if item_state.closed or now - item_state.last_touched >= _ITEM_IDLE_SECONDS:
                del self._items[item_id]
        # Still oversized (e.g. a burst of items all touched at `now`): fall
        # back to plain oldest-first eviction so the bound is always honoured.
        while len(self._items) > _ITEM_LEDGER_MAX:
            oldest = min(self._items.items(), key=lambda kv: kv[1].last_touched)[0]
            del self._items[oldest]

    def _reclaim_lanes(self, now: float) -> None:
        """Bound the caption-lane table without breaking speaker continuity."""
        if len(self._captions) <= _ITEM_LEDGER_MAX:
            return
        ordered = sorted(self._captions.items(), key=lambda kv: kv[1].last_touched)
        for key, caption in ordered:
            if len(self._captions) <= _ITEM_LEDGER_MAX // 2:
                break
            if caption.units:
                continue
            idle_limit = (
                _SPEAKER_LANE_IDLE_SECONDS if key[0].startswith("speaker:")
                else 0.0
            )
            if now - caption.last_touched >= idle_limit:
                del self._captions[key]

    @property
    def next_deadline(self) -> float | None:
        return min(
            (
                deadline
                for state in self._captions.values()
                for deadline in (state.deadline, state.hard_deadline)
                if deadline is not None
            ),
            default=None,
        )

    def expire(self, now: float) -> ChunkerDecision:
        """Close finalized residuals using caller-supplied monotonic time.

        This never finalizes an ASR item or advances its audio timestamps.
        The caller must drive deadlines even when no new audio/events arrive.
        """
        chunks = []
        for key, caption in list(self._captions.items()):
            if caption.hard_deadline is not None and now >= caption.hard_deadline:
                # Confirmed text only: a lane that accumulated nothing is dropped
                # rather than turned into an empty cue.
                if caption.units:
                    chunks.append(self._close_caption(key, "hard_deadline"))
                else:
                    del self._captions[key]
                continue
            if caption.deadline is not None and now >= caption.deadline:
                chunks.append(self._close_caption(key))
        return ChunkerDecision(tuple(chunks))

    def _close_caption(
        self,
        key: tuple[str, str | None],
        reason: CaptionCutReason = "utterance_endpoint",
    ) -> CaptionChunk:
        caption = self._captions.pop(key)
        if reason == "utterance_endpoint":
            # Residual flushes count their own path only; a hard-deadline cut is
            # visible as chunkCutReasons["hard_deadline"], incremented in _emit.
            self._residual_flushes += 1
        return self._emit(caption, len(caption.units) - 1, reason)

    def advance_audio(self, frontier_pcm: float) -> ChunkerDecision:
        pending = False
        for caption in self._captions.values():
            if not caption.units:
                continue
            start = self._open_start(caption)
            if start is not None and frontier_pcm - start >= SOFT_TARGET_SPAN:
                pending = True
                if not caption.pending_reported:
                    caption.pending_reported = True
                    self._pending_evidence_over_span += 1
        for item in self._items.values():
            if not item.closed and not item.accepted_parts and item.begin_pcm is not None:
                if frontier_pcm - item.begin_pcm >= SOFT_TARGET_SPAN:
                    pending = True
                    if not item.pending_reported:
                        item.pending_reported = True
                        self._pending_evidence_over_span += 1
        # PCM advancement cannot make a lexical boundary linguistically safe, so
        # advancing audio reports pending evidence and never requests a cut.
        return ChunkerDecision(pending_evidence=pending)

    def flush_utterance(self, item_id: str | None) -> ChunkerDecision:
        """Explicit lifecycle flush, never an ASR endpoint callback."""
        chunks = []
        for key, caption in list(self._captions.items()):
            if item_id is not None and not any(u.item_id == item_id for u in caption.units):
                continue
            if caption.units:
                self._residual_flushes += 1
                chunks.append(self._emit(caption, len(caption.units)-1, "utterance_endpoint"))
            del self._captions[key]
        for key, item in self._items.items():
            if item_id is None or key == item_id:
                item.closed = True
        return ChunkerDecision(tuple(chunks))

    def telemetry(self) -> CaptionChunkerTelemetry:
        ordered = sorted(self._spans)
        return CaptionChunkerTelemetry(
            caption_chunks=self._caption_chunk_total,
            chunk_span_p50=_percentile(ordered, 0.50),
            chunk_span_p95=_percentile(ordered, 0.95),
            chunk_span_max=max(ordered, default=0.0),
            chunk_cut_reasons=tuple(sorted(self._cut_reasons.items())),
            pending_evidence_over_soft_span=self._pending_evidence_over_span,
            span_over_soft_target=self._span_overshoots,
            local_agreement_commits=self._local_agreement_commits,
            local_agreement_rewrites=self._local_agreement_rewrites,
            residual_flushes=self._residual_flushes,
            final_reconciliation_conflicts=self._final_reconciliation_conflicts,
        )

    def _observe_token_snapshot(self, state: _ItemState, tokens: tuple[RecognitionToken, ...]) -> list[CaptionChunk]:
        previous = state.previous_tokens
        if previous is not None and not _token_snapshot_extends(previous, tokens):
            self._local_agreement_rewrites += 1
        if previous is None:
            state.previous_tokens = tokens
            return []
        agreement = _token_agreement(previous, tokens)
        # Always retain the newest lexical unit because it may still grow.
        promote_to = max(state.token_committed_count, agreement - 1)
        chunks: list[CaptionChunk] = []
        for token in tokens[state.token_committed_count:promote_to]:
            accepted = self._accept_token(state, token, provider_stable=False)
            state.token_committed_count += 1
            if accepted:
                self._local_agreement_commits += 1

        state.previous_tokens = tokens
        return chunks

    def _observe_stable_prefix(self, state: _ItemState, observation: CaptionObservation) -> list[CaptionChunk]:
        stable = _normalize_text(observation.stable_text)
        old = state.stable_prefix_committed
        if stable.startswith(old):
            delta = stable[len(old):]
        else:
            self._final_reconciliation_conflicts += 1
            delta = ""
        if delta:
            self._accept_text(state, delta, observation, provider_stable=True)
            state.stable_prefix_committed = stable
        return []

    def _observe_text_snapshot(self, state: _ItemState, observation: CaptionObservation) -> list[CaptionChunk]:
        current = _normalize_text(observation.tentative_text or observation.stable_text)
        previous = state.previous_text
        if previous is not None and not current.startswith(previous):
            self._local_agreement_rewrites += 1
        if previous is None:
            state.previous_text = current
            return []
        common = _longest_common_prefix(previous, current)
        safe = _safe_text_prefix(common, _primary_language(observation.language or state.language))
        if safe.startswith(state.text_committed):
            delta = safe[len(state.text_committed):]
            if delta:
                self._accept_text(state, delta, observation, provider_stable=False)
                state.text_committed = safe
                self._local_agreement_commits += 1
        else:
            self._final_reconciliation_conflicts += 1
        state.previous_text = current
        return []

    def _reconcile_final(self, state: _ItemState, observation: CaptionObservation) -> None:
        final = _normalize_text(observation.stable_text or observation.tentative_text)
        accepted = _normalize_text(_join_texts(state.accepted_parts))
        residual, conflict = _final_residual(accepted, final)
        if conflict:
            self._final_reconciliation_conflicts += 1
        if not residual:
            return
        tokens = tuple(t for t in observation.tokens if not any(
            _same_accepted_token(t, old) for old in state.accepted_tokens))
        # Finals can carry a full token snapshot or just the residual delta.
        # Use exact token ranges only when they reconstruct the residual.
        if tokens and "".join(_normalize_text("".join(t.text for t in tokens)).split()) == "".join(residual.split()):
            for token in tokens:
                self._accept_token(state, token, provider_stable=True)
        else:
            self._accept_text(state, residual, observation, provider_stable=True)

    def _accept_token(self, state: _ItemState, token: RecognitionToken, *, provider_stable: bool) -> bool:
        # Hybrid Providers can first expose a word in a mutable snapshot and
        # later return that same timestamped word as an immutable delta. If
        # LocalAgreement already policy-committed it, the stronger evidence
        # upgrades provenance but must not append the word a second time.
        for index, accepted in enumerate(state.accepted_tokens):
            if _same_accepted_token(accepted, token):
                if provider_stable and not accepted.provider_stable:
                    state.accepted_tokens[index] = dataclasses.replace(token, provider_stable=True)
                return False
        state.accepted_tokens.append(token)
        state.units.append(_Unit(
            token.text,
            token.begin_pcm,
            token.end_pcm,
            token.language,
            token.speaker,
            provider_stable,
            is_piece=token.is_piece,
            exact_timing=token.begin_pcm is not None and token.end_pcm is not None,
        ))
        state.accepted_parts.append(token.text)
        state.begin_pcm = _minimum_time(state.begin_pcm, token.begin_pcm)
        state.accepted_end = _maximum_time(state.accepted_end, token.end_pcm)
        return True

    def _accept_text(self, state: _ItemState, text: str, observation: CaptionObservation, *, provider_stable: bool) -> None:
        clean = text.strip()
        if not clean:
            return
        begin = state.accepted_end
        if begin is None:
            begin = observation.begin_pcm
        end = observation.end_pcm
        state.units.append(_Unit(
            clean, begin, end,
            observation.language or state.language,
            observation.speaker or state.speaker,
            provider_stable,
            exact_timing=False,
        ))
        state.accepted_parts.append(clean)
        state.begin_pcm = _minimum_time(state.begin_pcm, begin)
        state.accepted_end = _maximum_time(state.accepted_end, end)

    def _drain_ready(self, state: _CaptionState) -> list[CaptionChunk]:
        if not state.units:
            return []
        text, edges = _unit_text_edges(state.units)
        endpoints = {edge for edge, unit in zip(edges, state.units) if unit.endpoint}
        language = _primary_language(_dominant(u.language for u in state.units))
        if self.realtime:
            from .punctuation_boundaries import select_boundaries
            candidates = select_boundaries(text, edges, [u.begin for u in state.units],
                [u.end for u in state.units], language, endpoints)
            chunks = []
            removed = 0
            for index, reason in candidates:
                chunks.append(self._emit(state, index - removed, reason))
                removed = index + 1
            return chunks
        candidates = caption_boundaries(text, edges, language, endpoints)
        # If a short complete sentence is already in this response, preserve
        # it instead of publishing its internal subordinate clauses. This
        # consumes available evidence without introducing a waiting window.
        selected = []
        sentence_start = 0
        for index, reason in candidates:
            if reason == "terminal_punctuation":
                begin, end = state.units[sentence_start].begin, state.units[index].end
                if begin is not None and end is not None and end - begin <= SOFT_TARGET_SPAN:
                    selected = [c for c in selected if c[0] < sentence_start]
                sentence_start = index + 1
            selected.append((index, reason))
        chunks = []
        removed = 0
        for index, reason in selected:
            if index < removed:
                continue
            chunks.append(self._emit(state, index-removed, reason))
            removed = index + 1
        return chunks

    def _emit(self, state: _CaptionState, index: int, reason: CaptionCutReason) -> CaptionChunk:
        selected = state.units[:index + 1]
        del state.units[:index + 1]
        begin, end = _chunk_times(selected, state)
        state.begin_pcm = next(
            (unit.begin for unit in state.units if unit.begin is not None),
            end,
        )
        strict = all(unit.exact_timing for unit in selected)
        span = max(0.0, end - begin)
        if span > SOFT_TARGET_SPAN + 1e-9:
            self._span_overshoots += 1
        ends_mid: bool | None
        if reason == "terminal_punctuation":
            ends_mid = False
        elif reason == "clause_boundary":
            ends_mid = True
        else:
            ends_mid = None
        if state.emitted_chunks == 0:
            starts_mid = False
        elif state.last_chunk_ended_mid is True:
            starts_mid = True
        elif state.last_chunk_ended_mid is False:
            starts_mid = False
        else:
            starts_mid = None
        chunk = CaptionChunk(
            text=_join_units(selected),
            begin_pcm=begin,
            end_pcm=end,
            language=_dominant(unit.language for unit in selected),
            speaker=_dominant(unit.speaker for unit in selected),
            cut_reason=reason,
            starts_mid_sentence=starts_mid,
            ends_mid_sentence=ends_mid,
            guarantee_tier="strict" if strict else "best_effort",
            exact_timing=all(unit.exact_timing for unit in selected),
        )
        state.last_chunk_ended_mid = ends_mid
        state.emitted_chunks += 1
        self._spans.append(span)
        self._caption_chunk_total += 1
        self._cut_reasons[reason] += 1
        state.pending_reported = False
        return chunk

    @staticmethod
    def _open_start(state: _CaptionState) -> float | None:
        if state.units:
            for unit in state.units:
                if unit.begin is not None:
                    return unit.begin
        return state.begin_pcm


def _chunk_times(units: list[_Unit], state: _CaptionState) -> tuple[float, float]:
    begins = [unit.begin for unit in units if unit.begin is not None]
    ends = [unit.end for unit in units if unit.end is not None]
    begin = begins[0] if begins else (state.begin_pcm or 0.0)
    end = max(ends) if ends else (state.last_end_pcm if state.last_end_pcm is not None else begin)
    return float(begin), max(float(begin), float(end))


def _join_units(units: list[_Unit]) -> str:
    return _unit_text_edges(units)[0].strip()


def _unit_text_edges(units: list[_Unit]) -> tuple[str, list[int]]:
    text = ""
    edges = []
    for unit in units:
        text = text + unit.text if unit.is_piece else _join_texts([text, unit.text])
        edges.append(len(text))
    return text, edges


def _join_texts(parts: list[str]) -> str:
    output = ""
    for raw in parts:
        text = raw
        if not text:
            continue
        if not output:
            output = text
            continue
        if output[-1].isspace() or text[0].isspace() or _is_cjk(output[-1]) or _is_cjk(text[0]) or text[0] in _STRONG_PUNCTUATION | _WEAK_PUNCTUATION:
            output += text
        else:
            output += " " + text
    return re.sub(r"[ \t]+", " ", output).strip()


def _is_cjk(character: str) -> bool:
    code = ord(character)
    return (
        0x3040 <= code <= 0x30FF
        or 0x3400 <= code <= 0x9FFF
        or 0xAC00 <= code <= 0xD7AF
    )


def _dominant(values) -> str | None:
    counts: Counter[str] = Counter(value for value in values if value)
    return counts.most_common(1)[0][0] if counts else None


def _same_recognition_token(left: RecognitionToken, right: RecognitionToken) -> bool:
    return (
        _normalize_text(left.text) == _normalize_text(right.text)
        and _time_close(left.begin_pcm, right.begin_pcm)
        and _time_close(left.end_pcm, right.end_pcm)
        and left.speaker == right.speaker
    )


def _same_accepted_token(left: RecognitionToken, right: RecognitionToken) -> bool:
    # Jitter belongs to mutable snapshot agreement. Two immutable adjacent
    # occurrences of the same word are distinct, even less than 60ms apart.
    if left.provider_stable and right.provider_stable:
        return (_normalize_text(left.text) == _normalize_text(right.text)
                and left.begin_pcm == right.begin_pcm and left.end_pcm == right.end_pcm
                and left.speaker == right.speaker)
    return _same_recognition_token(left, right)


def _token_agreement(left: tuple[RecognitionToken, ...], right: tuple[RecognitionToken, ...]) -> int:
    length = 0
    for old, new in zip(left, right):
        if not _same_recognition_token(old, new):
            break
        length += 1
    return length


def _token_snapshot_extends(old: tuple[RecognitionToken, ...], new: tuple[RecognitionToken, ...]) -> bool:
    return len(new) >= len(old) and _token_agreement(old, new) == len(old)


def _time_close(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    return abs(left - right) <= _TIMESTAMP_JITTER


def _normalize_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _longest_common_prefix(left: str, right: str) -> str:
    length = 0
    for first, second in zip(left, right):
        if first != second:
            break
        length += 1
    return left[:length]


def _safe_text_prefix(text: str, language: str | None) -> str:
    text = text.rstrip()
    if not text:
        return ""
    primary = _primary_language(language)
    if primary in _CJK_LANGUAGES:
        # Hold back the final character/expression unless punctuation already
        # closes it; no whitespace segmentation is assumed for CJK.
        if text[-1] in _STRONG_PUNCTUATION | _WEAK_PUNCTUATION:
            return text
        return text[:-1]
    match = re.search(r"\s+\S+$", text)
    return text[:match.start()].rstrip() if match else ""


def _final_residual(accepted: str, final: str) -> tuple[str, bool]:
    if not accepted:
        return final, False
    if final.startswith(accepted):
        return final[len(accepted):].strip(), False
    # Adapters reconstruct lexical groups without retaining every separator.
    # A final can restore those spaces without changing any recognized text.
    # Match that identical character prefix before treating it as a rewrite;
    # whitespace word alignment otherwise drops uncommitted CJK tail text.
    compact_accepted = "".join(accepted.split())
    if compact_accepted and "".join(final.split()).startswith(compact_accepted):
        remaining = len(compact_accepted)
        for index, character in enumerate(final):
            if not character.isspace():
                remaining -= 1
            if remaining == 0:
                return final[index + 1:].strip(), False
    accepted_words = accepted.split()
    final_words = final.split()
    if not accepted_words or not final_words:
        return "", True
    matcher = difflib.SequenceMatcher(a=accepted_words, b=final_words, autojunk=False)
    blocks = [block for block in matcher.get_matching_blocks() if block.size]
    if not blocks:
        return "", True
    first = blocks[0]
    if first.a == 0 and first.b == 0:
        # A Provider correction inside the already published prefix is never
        # inserted after that prefix. Consume the same lexical extent from the
        # final and append only genuinely later material.
        return " ".join(final_words[min(len(accepted_words), len(final_words)):]), True
    last = blocks[-1]
    return " ".join(final_words[last.b + last.size:]), True


def _primary_language(language: str | None) -> str | None:
    if not language:
        return None
    try:
        return primary_subtag(language)
    except (TypeError, ValueError):
        return None


def _minimum_time(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def _maximum_time(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _percentile(ordered: list[float], fraction: float) -> float:
    if not ordered:
        return 0.0
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]
