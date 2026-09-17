"""Punctuation-first caption cuts with narrowly scoped format/Japanese vetoes.

Only existing ASR edges and currently arrived text are used. No persistent
state, timers, translation or model calls.

This engine is wired into CaptionChunker for realtime providers
(caption_chunker.py:458-461 selects it via `select_boundaries`).
"""
from __future__ import annotations
import bisect
import re
import unicodedata
from .clause_boundaries import TERMINALS, SEPARATORS as WEAK, _tagger
from .languages import primary_subtag

CLOSERS = '\"\'”’」』）)]】》'
PUNCT = TERMINALS | WEAK
URL = re.compile(r'(?:https?://|www\.)\S+$|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}[.!?]?$', re.I)
ABBR = re.compile(r'(?:[A-Za-z]\.){2,}$')
TITLES = {'en': {'mr','mrs','ms','dr','prof'}, 'es': {'sr','sra','dr','dra'}, 'pt': {'sr','sra','dr','dra'}}


def _ja_vetoes(text, edges):
    words = list(_tagger()(text))
    ends, starts = [], []
    cursor = 0
    for w in words:
        start = text.find(w.surface, cursor)
        starts.append(start)
        cursor = start + len(w.surface)
        ends.append(cursor)
    vetoes = {}
    for i, edge in enumerate(edges):
        prefix = text[:edge].rstrip().rstrip(CLOSERS)
        if not prefix or prefix[-1] not in PUNCT: continue
        before = bisect.bisect_right(ends, edge)
        if before < len(starts) and starts[before] < edge:
            vetoes[i] = 'morpheme_internal'; continue
        left = before-1
        while left >= 0 and words[left].feature.pos1 == '補助記号': left -= 1
        after = before
        while after < len(words) and words[after].feature.pos1 == '補助記号': after += 1
        if left < 0: continue
        if prefix[-1] in WEAK:
            # Repeated punctuation/fillers do not complete a pending noun modifier.
            while left >= 0:
                if words[left].feature.pos1 in {'補助記号','感動詞'}:
                    left -= 1
                elif left > 0 and words[left].surface == 'か' and words[left-1].surface == 'なん':
                    left -= 2
                else:
                    break
            if left < 0: continue
        last = words[left]
        preceding_noun = left > 0 and words[left-1].feature.pos1 in {'名詞','代名詞'}
        if preceding_noun and prefix[-1] in WEAK and last.surface == 'の' and last.feature.pos2 in {'格助詞','準体助詞'}:
            vetoes[i] = 'ja_left_genitive'; continue
        if after >= len(words): continue  # no waiting for right evidence
        following = words[after]
        # Standalone でも after a finite predicate begins a new clause.
        discourse_demo = prefix[-1] in TERMINALS and text[edge:].lstrip().startswith('でも') and any(f in last.feature.cForm for f in ('終止形','命令形','意志推量形'))
        if not discourse_demo and following.feature.pos1 in {'助詞','助動詞','接尾辞'} and (
            prefix[-1] in WEAK
            or (last.surface in {'て','で','の'} and last.feature.pos1 == '助詞')
            # A terminal after a NOUN is not a sentence end either: the Provider places
            # 。 mid-phrase and 焦点。+は今後 was observed live, so the cue was cut in
            # the middle of a phrase whose particle followed. Gated on 名詞 so that a
            # real sentence ending in それ。 (代名詞), なるほどな。 (終助詞) or
            # 終わりました。 (助動詞) still cuts -- those are the cases
            # test_discourse_starters_do_not_block_previous_sentence protects.
            or last.feature.pos1 == '名詞'
        ):
            vetoes[i] = 'ja_attached_right'; continue
        if prefix[-1] not in WEAK: continue
        if last.feature.pos1 == '形容詞' and '連用形' in last.feature.cForm:
            vetoes[i] = 'ja_adverbial_form'
    return vetoes


def select_boundaries(text, edges, begins, ends, language, endpoints):
    """Return (original unit index, cut reason), in audio order."""
    lang = primary_subtag(language or 'und')
    vetoes = _ja_vetoes(text, edges) if lang == 'ja' else {}
    selected = []
    start = 0
    for i, edge in enumerate(edges):
        piece = text[edges[i-1] if i else 0:edge]
        if not piece.strip(): continue
        prefix = text[:edge].rstrip().rstrip(CLOSERS).rstrip()
        if not prefix or prefix[-1] not in PUNCT: continue
        right = text[edge:].lstrip()
        mark = prefix[-1]
        begin, end = begins[start], ends[i]
        if mark in WEAK and (begin is None or end is None or end-begin < 4.0-1e-9): continue
        reason = None
        word = unicodedata.normalize('NFKC',prefix.split()[-1])
        if right and right[0] in PUNCT | set(CLOSERS): reason='attach_punctuation'
        elif URL.search(word) or ABBR.fullmatch(word) or prefix.endswith('..'): reason='protected_token'
        elif re.search(r'\d[.,，]$',prefix) and (right[:1].isdigit() or (not right and edge not in endpoints)): reason='numeric_continuation'
        elif mark=='.' and word[:-1].lower() in TITLES.get(lang,set()) and right[:1].isupper(): reason='title_before_name'
        elif i in vetoes: reason=vetoes[i]
        if reason: continue
        selected.append((i, 'terminal_punctuation' if mark in TERMINALS else 'clause_boundary'))
        start=i+1
    merged=[]
    for i, reason in selected:
        if reason=='terminal_punctuation' and merged and merged[-1][1]=='clause_boundary':
            prior=merged[-1][0]
            segment_start=merged[-2][0]+1 if len(merged)>1 else 0
            if ends[prior] is not None and ends[i] is not None and begins[segment_start] is not None and 0<=ends[i]-ends[prior]<=1 and ends[i]-begins[segment_start]<=8:
                merged.pop()
        merged.append((i,reason))
    return merged
