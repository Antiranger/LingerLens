"""In-memory cue model and bounded rolling subtitle store.

Polling uses a monotonic sequence number, never a clock: every add or update
bumps a global ``seq`` and clients ask for ``seq > afterSeq``.  Retention is
based on the newest cue's ``t_end`` (the media timeline the cues live on), not
``time.time()`` — a PDT lagging wall clock must never delete cues the playhead
has not reached yet (redesign doc RC-6).
"""

from __future__ import annotations

import dataclasses
from collections import deque
from typing import Literal

CueState = Literal["src", "translating", "done", "failed"]
TimingSource = Literal["asr", "vad", "approx"]

_IMMUTABLE_AFTER_ADD = {
    "t_start", "t_end", "src", "lang", "timing_source", "speaker",
    "generation", "chunk_order", "starts_mid_sentence", "ends_mid_sentence", "cut_reason",
}
_TERMINAL_STATES = {"done", "failed"}


@dataclasses.dataclass
class Cue:
    id: int
    seq: int
    t_start: float | None
    t_end: float
    hold: float
    src: str
    zh: str | None
    state: CueState
    lang: str
    timing_source: TimingSource
    revision: int = 1
    speaker: str | None = None
    generation: int | None = None
    chunk_order: int | None = None
    starts_mid_sentence: bool | None = None
    ends_mid_sentence: bool | None = None
    cut_reason: str | None = None
    """Provider-reported diarization label for this utterance (Ticket 02).

    Backend-owned metadata only: serialized for future UI use, never rendered
    today, and None for Providers without realtime speaker output."""

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "seq": self.seq,
            "tStart": self.t_start,
            "tEnd": self.t_end,
            "hold": self.hold,
            "src": self.src,
            "zh": self.zh,
            "state": self.state,
            "lang": self.lang,
            "timingSource": self.timing_source,
            "revision": self.revision,
            "speaker": self.speaker,
            "generation": self.generation,
            "chunkOrder": self.chunk_order,
            "startsMidSentence": self.starts_mid_sentence,
            "endsMidSentence": self.ends_mid_sentence,
            "cutReason": self.cut_reason,
        }


class CueStore:
    """Keep recent cues, support in-place revision updates and seq polling."""

    def __init__(self, retention_seconds: float = 600.0, max_cues: int = 4096) -> None:
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive")
        if max_cues <= 0:
            raise ValueError("max_cues must be positive")
        self.retention_seconds = retention_seconds
        self.max_cues = max_cues
        self._cues: deque[Cue] = deque()
        self._by_id: dict[int, Cue] = {}
        self._next_id = 1
        self._next_seq = 0

    @property
    def max_seq(self) -> int:
        """Latest issued sequence number; pass back as ``afterSeq``."""
        return self._next_seq

    def _prune(self) -> None:
        # Rebase retention on the newest cue's t_end: the store only ever sees
        # one (media) timeline, so this is the correct liveness reference.
        if self._cues:
            cutoff = self._cues[-1].t_end - self.retention_seconds
            while self._cues and (self._cues[0].t_end <= cutoff or len(self._cues) > self.max_cues):
                expired = self._cues.popleft()
                self._by_id.pop(expired.id, None)

    def _advance_seq(self) -> int:
        self._next_seq += 1
        return self._next_seq

    def add(
        self,
        *,
        t_start: float | None,
        t_end: float,
        hold: float,
        src: str,
        zh: str | None = None,
        state: CueState = "src",
        lang: str,
        timing_source: TimingSource,
        speaker: str | None = None,
        generation: int | None = None,
        chunk_order: int | None = None,
        starts_mid_sentence: bool | None = None,
        ends_mid_sentence: bool | None = None,
        cut_reason: str | None = None,
    ) -> Cue:
        cue = Cue(
            id=self._next_id,
            seq=self._advance_seq(),
            t_start=t_start,
            t_end=t_end,
            hold=hold,
            src=src,
            zh=zh,
            state=state,
            lang=lang,
            timing_source=timing_source,
            speaker=speaker,
            generation=generation,
            chunk_order=chunk_order,
            starts_mid_sentence=starts_mid_sentence,
            ends_mid_sentence=ends_mid_sentence,
            cut_reason=cut_reason,
        )
        self._next_id += 1
        self._cues.append(cue)
        self._by_id[cue.id] = cue
        self._prune()
        return cue

    def update(self, cue_id: int, **changes: object) -> Cue:
        cue = self._by_id.get(cue_id)
        if cue is None:
            raise KeyError(cue_id)
        allowed = {"state", "zh", "hold"}
        unknown = set(changes) - allowed
        if unknown:
            immutable = set(changes) & _IMMUTABLE_AFTER_ADD
            label = "immutable cue fields" if immutable else "unknown cue fields"
            raise ValueError(f"{label}: {sorted(unknown)}")
        if not changes:
            return cue
        if cue.state in _TERMINAL_STATES:
            raise ValueError(f"cue {cue_id} translation is already terminal: {cue.state}")
        next_state = changes.get("state", cue.state)
        if next_state not in {"src", "translating", "done", "failed"}:
            raise ValueError(f"invalid cue state: {next_state!r}")
        if cue.state == "src" and next_state not in {"src", "translating", "done", "failed"}:
            raise ValueError(f"invalid cue state transition: {cue.state} -> {next_state}")
        if cue.state == "translating" and next_state not in {"translating", "done", "failed"}:
            raise ValueError(f"invalid cue state transition: {cue.state} -> {next_state}")
        if cue.state == "src" and next_state == "done" and not (changes.get("zh") or cue.zh):
            raise ValueError("direct done cue requires a translation")
        if next_state == "done" and not (changes.get("zh") or cue.zh):
            raise ValueError("done cue requires a translation")
        if next_state == "failed" and "zh" in changes and changes["zh"] is not None:
            raise ValueError("failed cue cannot publish a translation")
        for name, value in changes.items():
            setattr(cue, name, value)
        cue.revision += 1
        cue.seq = self._advance_seq()
        return cue

    def get(self, cue_id: int) -> Cue | None:
        return self._by_id.get(cue_id)

    def query(self, *, after_seq: int = 0) -> list[Cue]:
        """Return cues whose sequence number advanced past ``after_seq``."""
        self._prune()
        return sorted((cue for cue in self._cues if cue.seq > after_seq), key=lambda cue: cue.seq)

    def __len__(self) -> int:
        return len(self._cues)
