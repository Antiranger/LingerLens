"""Inspect the long utterances: how does the stable prefix evolve inside them?"""
import json, sys
from pathlib import Path

events = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
MIN_DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0

spans, finals = {}, {}
for e in events:
    raw, t, item = e["raw"], e["raw"].get("type", ""), e["raw"].get("item_id")
    if t.endswith("speech_started"):
        spans.setdefault(item, {})["start"] = raw["audio_start_ms"] / 1000.0
    elif t.endswith("speech_stopped"):
        spans.setdefault(item, {})["end"] = raw["audio_end_ms"] / 1000.0
    elif t.endswith("transcription.completed"):
        finals[item] = (e["sent"], raw.get("transcript", ""))

for item, (final_sent, transcript) in finals.items():
    sp = spans.get(item, {})
    s, en = sp.get("start"), sp.get("end")
    dur = (en - s) if (s is not None and en is not None) else None
    if dur is None or dur < MIN_DUR:
        continue
    print(f"\n=== 长句 span {s:.2f}..{en:.2f} ({dur:.1f}s), final 到达 sent={final_sent:.2f} ===")
    print(f"  final: {transcript}")
    print(f"  --- 稳定前缀(text)的增长过程 ---")
    prev = None
    for e in events:
        raw = e["raw"]
        if raw.get("item_id") != item or not raw.get("type", "").endswith("transcription.text"):
            continue
        text = raw.get("text") or ""
        if text and text != prev:
            prev = text
            behind = final_sent - e["sent"]
            print(f"    sent={e['sent']:6.2f} (比final早{behind:5.2f}s) len={len(text):3d}  {text}")
    print(f"  --- stash 尾巴样例(最后一条) ---")
    for e in reversed(events):
        raw = e["raw"]
        if raw.get("item_id") == item and raw.get("type", "").endswith("transcription.text"):
            print(f"    text={raw.get('text','')!r}")
            print(f"    stash={raw.get('stash','')!r}")
            break
