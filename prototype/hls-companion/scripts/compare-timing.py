"""Replay a captured Qwen event log through BOTH timing strategies.

CURRENT: _pending_vad_start = sent - vad_event_lag
         _pending_vad_end   = sent - silence_duration
         (cleared on every final; a final with no pending pair -> "approx")

PROPOSED: join speech_started.audio_start_ms / speech_stopped.audio_end_ms to
          the completed event by item_id.
"""
import json, sys
from pathlib import Path

events = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
SILENCE = float(sys.argv[2]) / 1000.0 if len(sys.argv) > 2 else 0.4
VAD_LAG = 0.3

# ---- PROPOSED: item_id join -------------------------------------------------
spans: dict[str, dict] = {}
proposed = []
for e in events:
    raw = e["raw"]; t = raw.get("type", "")
    item = raw.get("item_id")
    if t.endswith("speech_started"):
        spans.setdefault(item, {})["start"] = raw["audio_start_ms"] / 1000.0
    elif t.endswith("speech_stopped"):
        spans.setdefault(item, {})["end"] = raw["audio_end_ms"] / 1000.0
    elif t.endswith("transcription.completed"):
        span = spans.get(item, {})
        proposed.append({
            "text": raw.get("transcript", ""),
            "start": span.get("start"),
            "end": span.get("end"),
            "ready_sent": e["sent"],
        })

# ---- CURRENT: sent-offset estimator ----------------------------------------
pend_start = pend_end = None
current = []
for e in events:
    raw = e["raw"]; t = raw.get("type", ""); sent = e["sent"]
    if t.endswith("speech_started"):
        pend_start = max(0.0, sent - VAD_LAG); pend_end = None
    elif t.endswith("speech_stopped"):
        pend_end = max(pend_start or 0.0, sent - SILENCE)
    elif t.endswith("transcription.completed"):
        if pend_start is not None and pend_end is not None:
            src, s, en = "vad", pend_start, pend_end
        else:
            src, s, en = "approx", None, sent
        current.append({"text": raw.get("transcript", ""), "start": s, "end": en, "src": src})
        pend_start = pend_end = None

print(f"{'#':>3} {'PROPOSED start..end (asr)':>28} | {'CURRENT start..end':>24} | err_start err_end  text")
print("-" * 130)
errs_s, errs_e, approx = [], [], 0
for i, (p, c) in enumerate(zip(proposed, current)):
    ps = f"{p['start']:.3f}" if p["start"] is not None else "  None"
    pe = f"{p['end']:.3f}" if p["end"] is not None else "  None"
    cs = f"{c['start']:.3f}" if c["start"] is not None else "  None"
    ce = f"{c['end']:.3f}" if c["end"] is not None else "  None"
    es = ee = None
    if p["start"] is not None and c["start"] is not None:
        es = c["start"] - p["start"]; errs_s.append(abs(es))
    if p["end"] is not None and c["end"] is not None:
        ee = c["end"] - p["end"]; errs_e.append(abs(ee))
    if c["src"] == "approx":
        approx += 1
    print(f"{i:>3} {ps:>12}..{pe:<12} | {cs:>10}..{ce:<10} | "
          f"{(f'{es:+.3f}' if es is not None else '   -- '):>7} {(f'{ee:+.3f}' if ee is not None else '   -- '):>7}  "
          f"{p['text'][:34]}")

def stat(name, xs):
    if not xs:
        print(f"  {name}: n/a"); return
    xs = sorted(xs)
    print(f"  {name}: n={len(xs)} mean={sum(xs)/len(xs):.3f}s p50={xs[len(xs)//2]:.3f}s max={xs[-1]:.3f}s")

print("\n== absolute error of CURRENT estimator vs server-reported truth ==")
stat("start boundary", errs_s)
stat("end   boundary", errs_e)
print(f"  cues CURRENT would mark timingSource=approx (tStart=None): {approx}/{len(current)}")

durs = [p["end"] - p["start"] for p in proposed if p["start"] is not None and p["end"] is not None]
if durs:
    durs_sorted = sorted(durs)
    print("\n== utterance duration distribution (PROPOSED, = display window length) ==")
    print(f"  n={len(durs)} min={durs_sorted[0]:.2f}s p50={durs_sorted[len(durs)//2]:.2f}s "
          f"p90={durs_sorted[int(len(durs)*0.9)]:.2f}s max={durs_sorted[-1]:.2f}s")

lags = [p["ready_sent"] - p["end"] for p in proposed if p["end"] is not None]
if lags:
    lags_sorted = sorted(lags)
    print("\n== ASR readiness lag (final available - speech end), audio-seconds ==")
    print(f"  n={len(lags)} p50={lags_sorted[len(lags)//2]:.2f}s p95={lags_sorted[int(len(lags)*0.95)]:.2f}s max={lags_sorted[-1]:.2f}s")
