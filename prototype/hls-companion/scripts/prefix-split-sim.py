"""Simulate 'split the interim stable prefix at sentence boundaries' vs 'wait for the final'.

Qwen realtime streams, per item:
  transcription.text  { text: <confirmed stable prefix>, stash: <tentative tail> }
and only at the end:
  transcription.completed { transcript: <whole utterance> }

Policy under test (P2 candidate):
  as soon as the STABLE PREFIX contains a new complete sentence (terminated by
  。！？), emit that sentence as its own cue -- do not wait for the utterance
  to end.

Question answered: for long utterances, how many audio-seconds earlier does
this deliver each sentence than the current 'wait for completed' behaviour?
"""
import json, re, sys
from pathlib import Path

BOUNDARY = re.compile(r"[。！？!?]")


def sentences_of(text):
    """Split into complete (boundary-terminated) sentences; drop the open tail."""
    out, start = [], 0
    for m in BOUNDARY.finditer(text):
        out.append(text[start:m.end()])
        start = m.end()
    return out


def run(path):
    events = json.loads(Path(path).read_text(encoding="utf-8"))

    spans = {}          # item -> {start,end}
    stable = {}         # item -> latest stable prefix
    emitted = {}        # item -> count of sentences already emitted
    rows = []           # (item, sentence, prefix_available_sent)
    finals = {}         # item -> (sent_at_final, transcript)

    for e in events:
        raw, sent = e["raw"], e["sent"]
        t = raw.get("type", "")
        item = raw.get("item_id")
        if t.endswith("speech_started"):
            spans.setdefault(item, {})["start"] = raw["audio_start_ms"] / 1000.0
        elif t.endswith("speech_stopped"):
            spans.setdefault(item, {})["end"] = raw["audio_end_ms"] / 1000.0
        elif t.endswith("transcription.text"):
            text = raw.get("text") or ""
            stable[item] = text
            done = sentences_of(text)
            already = emitted.get(item, 0)
            for s in done[already:]:
                rows.append((item, s, sent))
            emitted[item] = max(already, len(done))
        elif t.endswith("transcription.completed"):
            finals[item] = (sent, raw.get("transcript", ""))

    print(f"### {Path(path).name}")
    utterances = [(i, spans.get(i, {}), finals[i]) for i in finals]
    long_ones = [(i, s, f) for i, s, f in utterances
                 if s.get("start") is not None and s.get("end") is not None
                 and s["end"] - s["start"] >= 6.0]
    print(f"  utterances={len(utterances)}  长句(>=6s)={len(long_ones)}")

    total_gain, n = 0.0, 0
    for item, span, (final_sent, transcript) in utterances:
        subs = [r for r in rows if r[0] == item]
        if len(subs) < 2:
            continue          # not a multi-sentence utterance; nothing to split
        dur = (span.get("end") or final_sent) - (span.get("start") or 0)
        print(f"\n  --- item span {span.get('start')}..{span.get('end')} "
              f"({dur:.1f}s), final 到达 sent={final_sent:.2f}")
        print(f"      完整 final: {transcript}")
        for _, sentence, avail in subs:
            gain = final_sent - avail
            total_gain += gain
            n += 1
            print(f"      [prefix sent={avail:6.2f}]  比 final 早 {gain:6.2f}s   {sentence}")
    if n:
        print(f"\n  == 平均提前 {total_gain / n:.2f}s，共 {n} 个可提前发出的分句 ==")
    else:
        print("  == 没有多分句的 utterance（VAD 已经切得足够细）==")


for p in sys.argv[1:]:
    run(p)
    print()
