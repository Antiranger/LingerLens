"""Linguistic caption edges, independent of ASR finality and elapsed time.

Only return existing token edges. Japanese tokenizer pieces are analyzed as
one text batch, so no cut can fall inside a morpheme or its auxiliary chain.
Unsupported weak constructions remain pending rather than becoming word cuts.
"""
from __future__ import annotations

import bisect
import functools
import re

# The single source of truth for caption boundary punctuation. These were
# previously duplicated, and the copies disagreed: "…" was a strong terminal in
# clause_boundaries and caption_chunker but not in punctuation_boundaries, and
# "—" was a weak boundary only in caption_chunker -- so the same audio cut
# differently depending on which engine ran. Both characters are legitimate
# boundaries, so the union is authoritative here and every other module imports
# it.
TERMINALS = frozenset(".!?…。！？")
SEPARATORS = frozenset(",;:—、，；：")

_CLOSERS = "\"'”’」』）)]"
_SEQUENCE = re.compile(r"^(?:それから|そして|次に|その後|続いて|じゃあ)")
_JA_ACKNOWLEDGEMENTS = {"はい", "うん", "いいえ", "有り難う", "今日は", "今晩は", "お早う"}


@functools.lru_cache(maxsize=1)
def _tagger():
    import fugashi
    import unidic_lite
    return fugashi.Tagger(f'-d "{unidic_lite.DICDIR}"')


def _protected(text):
    return bool(re.search(r"(?:https?://|www\.)\S+$|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}[.!?]?$", text)
                or re.fullmatch(r"\d+[、，,.]|(?:[A-Za-z]\.){2,}", text.strip()))


def _japanese_kind(words, before, after, *, terminal, endpoint, right):
    left = [w for w in words[:before] if w.feature.pos1 != "補助記号"]
    if not left:
        return None
    # A following particle/auxiliary is positive evidence against a cut,
    # irrespective of punctuation inserted by the recognizer.
    following = words[after] if after < len(words) else None
    # UniDic uses attributive inflection before sentence-final の in spoken
    # questions. Keep this evidence before stripping final particles below.
    final_no_question = (terminal and len(left) >= 2
                         and left[-1].surface == "の" and left[-1].feature.pos2 == "終助詞"
                         and left[-2].feature.pos1 in {"動詞", "形容詞", "助動詞"}
                         and "連体形" in left[-2].feature.cForm
                         and any(w.surface in {"?", "？"} for w in words[max(0, before-2):before]))
    while left and left[-1].feature.pos2 == "終助詞":
        left.pop()
    if not left:
        return None
    last = left[-1]
    f = last.feature
    # UniDic can tag sentence-initial でも as particles. It is independent
    # after a finite predicate and a full stop, but not after a te-form.
    discourse_demo = terminal and right.startswith("でも") and any(
        form in f.cForm for form in ("終止形", "意志推量形", "命令形"))
    if not discourse_demo and following is not None and (
        following.feature.pos1 in {"助詞", "助動詞", "接尾辞"}
        or following.feature.pos2 == "非自立可能"
    ):
        return None
    if final_no_question:
        return "terminal_punctuation"
    predicate = len(left) > 1 and left[-2].feature.pos1 in {"動詞", "形容詞", "助動詞"}
    # Conditional inflection and predicate + causal/adversative conjunction
    # complete subordinate clauses. Case particles after nouns do not.
    if "仮定形" in f.cForm:
        return "clause_boundary"
    if f.pos2 == "接続助詞":
        if last.surface in {"て", "で", "たり", "だり"}:
            # A te-form also occurs inside auxiliary and fixed expressions.
            # Require an explicit new sequential clause, never a bare suffix.
            return "clause_boundary" if predicate and _SEQUENCE.match(right.lstrip()) else None
        return "clause_boundary" if predicate else None
    # Causal ので / colloquial んで share nominalizer lemma の + copula で.
    if len(left) >= 3 and last.surface == "で" and left[-2].feature.lemma == "の":
        if left[-3].feature.pos1 in {"動詞", "形容詞", "助動詞"}:
            return "clause_boundary"
    if not (terminal or endpoint):
        return None
    # Sentence-final quoting とか + final particle may report a complete
    # predicate (美味しくなれとかね。). A bare nominal list still stays open.
    if terminal and len(left) >= 3 and [w.surface for w in left[-2:]] == ["と", "か"]:
        quoted = left[-3].feature
        if quoted.pos1 in {"動詞", "形容詞", "助動詞"} and any(
            form in quoted.cForm for form in ("終止形", "意志推量形", "命令形")
        ):
            return "terminal_punctuation"
    if f.pos1 == "接頭辞":
        return None
    if f.pos1 in {"動詞", "形容詞", "助動詞"}:
        if any(form in f.cForm for form in ("終止形", "意志推量形", "命令形")):
            return "terminal_punctuation" if terminal else "clause_boundary"
        return None
    if f.pos1 == "感動詞":
        # A single character (notably お) may be an unfinished honorific
        # despite the recognizer's period and the tokenizer's interjection tag.
        standalone = all(w.feature.pos1 == "感動詞" for w in left) and sum(len(w.surface) for w in left) >= 2
        return "terminal_punctuation" if terminal and (standalone or f.lemma in _JA_ACKNOWLEDGEMENTS) else None
    # Nominal utterances are useful captions, but a following noun can still
    # extend a compound. Do not treat a single prefix/short interjection as one.
    if terminal and f.pos1 in {"名詞", "代名詞", "形状詞"}:
        if following is None or following.feature.pos1 not in {"名詞", "接尾辞"}:
            return "terminal_punctuation"
    return None


def caption_boundaries(text: str, edges: list[int], language: str | None,
                       endpoint_edges: set[int]) -> list[tuple[int, str]]:
    """Return (unit index, reason), using no invented character timestamps."""
    if not text.strip():
        return []
    if language == "ja":
        return _japanese_boundaries(text, edges, endpoint_edges)
    output = []
    for index, end in enumerate(edges):
        left = text[:end].rstrip().rstrip(_CLOSERS)
        right = text[end:].lstrip()
        if not left:
            continue
        last_word = left.split()[-1]
        # A bare list number stays protected. A sentence ending in a number
        # ("round 19.") can close, unless available right text continues a
        # split decimal. This exception does not relax URL/abbreviation rules.
        number_sentence_end = (language == "en" and re.fullmatch(r"\d+\.", last_word)
                               and bool(re.search(r"[A-Za-z]", left[:-len(last_word)]))
                               and not re.match(r"\d", right))
        if _protected(last_word) and not number_sentence_end:
            continue
        if left[-1] in TERMINALS:
            output.append((index, "terminal_punctuation"))
        elif left[-1] in SEPARATORS:
            # Conservative language-specific clause evidence. Commas alone,
            # word gaps and connectors without a completed left clause fail.
            if language == "en" and re.search(r"\b(?:I|we|you|he|she|they|it)\s+(?:(?:have|has|had)\s+)?\w+ed\b.*[,;]$", left, re.I):
                if re.match(r"(?:but|so|however|and|because)\b", right, re.I):
                    output.append((index, "clause_boundary"))
            elif language == "zh" and re.search(r"(?:了|出来|完成|结束)[，,；;]$", left) and len(left) >= 5:
                output.append((index, "clause_boundary"))
    return output


def _japanese_boundaries(text, edges, endpoint_edges):
    words = list(_tagger()(text))
    ends = []
    starts = []
    cursor = 0
    for word in words:
        start = text.find(word.surface, cursor)
        starts.append(start)
        cursor = start + len(word.surface)
        ends.append(cursor)
    output = []
    for index, edge in enumerate(edges):
        before = bisect.bisect_right(ends, edge)
        # The provider edge itself must be a morpheme boundary.
        if before < len(starts) and starts[before] < edge:
            continue
        left = text[:edge].rstrip()
        if not left:
            continue
        right = text[edge:].lstrip()
        # Attach separately streamed punctuation to its preceding words.
        if right and right[0] in TERMINALS | SEPARATORS | set(_CLOSERS):
            continue
        stripped = left.rstrip(_CLOSERS)
        terminal = bool(stripped and stripped[-1] in TERMINALS)
        if _protected(stripped):
            continue
        after = before
        while after < len(words) and words[after].feature.pos1 == "補助記号":
            after += 1
        kind = _japanese_kind(words, before, after, terminal=terminal,
                              endpoint=edge in endpoint_edges, right=right)
        if kind:
            output.append((index, kind))
    return output
