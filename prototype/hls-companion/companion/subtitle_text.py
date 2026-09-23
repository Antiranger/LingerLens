"""Pure subtitle text normalization, filtering, splitting, and timing helpers.

Splitting is a **rendering** concern only (line wrapping in the player);
since the alignment redesign one ASR final maps to exactly one cue, and
``split_with_timing`` is retained solely for render-layer line breaking,
never to create new schedule units.
"""

from __future__ import annotations

import dataclasses
import re
import unicodedata
from collections.abc import Sequence

_ASR_MARKER_RE = re.compile(r"<\|.*?\|>")
_NOISE_TAG_RE = re.compile(
    r"(?:\[\s*(?:bgm|music|音楽|音乐)\s*\]|"
    r"[（(]\s*(?:bgm|music|音楽|音乐)\s*[）)])",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")
_FILLER_WORDS = frozenset({"えー", "あの", "うーん", "はい"})
_STRONG_BOUNDARIES = frozenset("。！？!?")
_WEAK_BOUNDARIES = frozenset("、,")


@dataclasses.dataclass(frozen=True)
class TimedTextPart:
    """One render line and its interpolated PCM range (render layer only)."""

    text: str
    begin_pcm: float | None
    end_pcm: float


def _is_punctuation_or_symbol(character: str) -> bool:
    return unicodedata.category(character)[0] in {"P", "S"}


def clean_subtitle_text(text: str | None) -> str | None:
    """Normalize an ASR final and return ``None`` when it should be discarded.

    Rules are deliberately ordered to match the subtitle pipeline specification.
    """

    if text is None:
        return None
    cleaned = unicodedata.normalize("NFKC", str(text))
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    cleaned = _ASR_MARKER_RE.sub("", cleaned)
    cleaned = _NOISE_TAG_RE.sub("", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()

    # Japanese full stops become a single ellipsis; other repeated punctuation
    # keeps its semantic kind while dropping ASR stutter.
    cleaned = re.sub(r"。{2,}", "…", cleaned)
    cleaned = re.sub(r"、{2,}", "、", cleaned)
    cleaned = re.sub(r"!{2,}", "!", cleaned)
    cleaned = re.sub(r"\?{2,}", "?", cleaned)
    cleaned = re.sub(r"！{2,}", "!", cleaned)
    cleaned = re.sub(r"？{2,}", "?", cleaned)
    cleaned = cleaned.strip()

    if len(cleaned) < 2:
        return None
    if all(character.isspace() or _is_punctuation_or_symbol(character) for character in cleaned):
        return None
    if cleaned in _FILLER_WORDS:
        return None
    return cleaned


def subtitle_match_key(text: str | None) -> str:
    """The comparable form of one subtitle string.

    Provider-side translation joins a cue against the text the ASR session
    reported for it. The cue has been through :func:`clean_subtitle_text`, which
    NFKC-folds the full-width ``！`` Soniox really does emit and collapses a
    stuttered ``。。``; the Provider side must go through the same fold or the two
    halves of one sentence never compare equal and the cue silently loses its
    translation. Text too short or too empty for a subtitle keeps a key anyway:
    here only equality matters, and a cue that was never published cannot match.

    Spacing is then dropped entirely. A Provider writes a space between two
    Japanese words (DashScope does it mid-utterance); the caption never keeps it,
    because the chunker joins a turn's text by deleting the gap between CJK
    neighbours. Comparing with the spaces still in would split one sentence into
    a keyed side that matches and a keyed side that never does.
    """

    cleaned = clean_subtitle_text(text)
    if cleaned is not None:
        return _WHITESPACE_RE.sub("", cleaned)
    folded = _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFKC", str(text or ""))).strip()
    return _WHITESPACE_RE.sub("", folded)


def is_duplicate_final(current: str, previous: str | None) -> bool:
    """Return whether an ASR final is an exact/near-prefix resend."""

    if previous is None:
        return False
    return current == previous or (previous.startswith(current) and len(previous) - len(current) < 3)


def newly_confirmed_sentences(prefix: str, already_emitted: str = "") -> str | None:
    """Return the newly complete-sentence text carried by a stable prefix.

    The realtime provider's ``text`` field is confirmed wording that grows
    monotonically while the utterance is still being spoken. Everything up to
    and including the *last* strong sentence boundary in it is final text that
    will not be rewritten, so it may be emitted as cues before the utterance's
    ``final`` event (redesign Fix F, prefix split). Boundaries that land in the
    same confirmation batch are returned as one slice: they were confirmed at
    the same audio position, so they share one timing estimate and read better
    as one cue (a lone 「そうそうそう。」 would flash for a fraction of a
    second otherwise).

    Returns only the part beyond ``already_emitted`` (the concatenation of
    previously returned slices), or ``None`` when nothing new is complete.
    """

    if already_emitted and not prefix.startswith(already_emitted):
        return None  # caller treats this as a prefix rewrite, not new text
    remainder = prefix[len(already_emitted):]
    index = max(remainder.rfind(boundary) for boundary in _STRONG_BOUNDARIES)
    if index < 0:
        return None
    return remainder[: index + 1]


def _take_fragment(text: str, limit: int) -> tuple[str, str]:
    window = text[:limit]
    for boundaries in (_STRONG_BOUNDARIES, _WEAK_BOUNDARIES):
        positions = [index + 1 for index, character in enumerate(window) if character in boundaries]
        if positions:
            split_at = positions[-1]
            return text[:split_at].strip(), text[split_at:].strip()
    return text[:limit].strip(), text[limit:].strip()


def split_subtitle_text(text: str, max_chars: int = 80) -> list[str]:
    """Split text into non-empty fragments no longer than ``max_chars``."""

    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    remaining = text.strip()
    parts: list[str] = []
    while remaining:
        if len(remaining) <= max_chars:
            parts.append(remaining)
            break
        part, remaining = _take_fragment(remaining, max_chars)
        if not part:  # Defensive fallback for unusual whitespace-only windows.
            part, remaining = remaining[:max_chars], remaining[max_chars:].strip()
        parts.append(part)
    return parts


def split_with_timing(
    text: str,
    begin_pcm: float | None,
    end_pcm: float,
    max_chars: int = 80,
) -> list[TimedTextPart]:
    """Split text for *rendering* and interpolate each line's PCM boundaries.

    The interpolation is explicitly a presentation guess (character-count
    ratio); it must never be used to schedule independent cues anymore.
    """

    parts = split_subtitle_text(text, max_chars=max_chars)
    if not parts:
        return []
    if begin_pcm is None:
        return [TimedTextPart(part, None, end_pcm) for part in parts]

    duration = max(0.0, end_pcm - begin_pcm)
    weights: Sequence[int] = [len(part) for part in parts]
    total = sum(weights)
    elapsed_weight = 0
    result: list[TimedTextPart] = []
    for index, (part, weight) in enumerate(zip(parts, weights)):
        part_begin = begin_pcm + duration * elapsed_weight / total
        elapsed_weight += weight
        part_end = end_pcm if index == len(parts) - 1 else begin_pcm + duration * elapsed_weight / total
        result.append(TimedTextPart(part, part_begin, part_end))
    return result


def calculate_hold(
    src: str,
    zh: str | None = None,
    *,
    minimum: float = 1.2,
    seconds_per_char: float = 0.06,
    maximum: float = 8.0,
) -> float:
    """Calculate display hold time from translated text, falling back to source."""

    if minimum > maximum:
        raise ValueError("minimum cannot exceed maximum")
    value = seconds_per_char * len(zh or src)
    return max(minimum, min(value, maximum))
