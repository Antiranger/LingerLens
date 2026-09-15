"""Measure whether subtitles actually reach the screen, and when.

The backend soak records what the pipeline *produced*: every cue, when it became
ready, how long translation took. None of that says whether the words appeared
on the glass. A cue can be produced and still never be shown -- hidden behind a
quality change, dropped by the scheduler, or simply arriving after its own end
time has already played out. That gap is the thing this sampler exists to close.

It attaches to a running window over the Chrome DevTools Protocol and polls a
read-only seam the renderer exposes (`window.__lingerlensSoakProbe`), which
returns the *rendered* subtitle rows together with the media wall clock at that
instant. From those two things together you can compute, per cue:

  * did it ever appear at all?
  * how long after its own start did it appear?
  * did it appear only after it had already ended?  <- the failure you care about
  * how much of the run was spent with no subtitle on screen at all?

Start the app with a debugging port, then run this next to it:

    $env:LINGERLENS_BACKEND_LOG = "$PWD\output\soak\app.log"
    .\release\win-unpacked\LingerLens.exe --remote-debugging-port=9222
    py -3.10 prototype\hls-companion\scripts\soak-onscreen-sampler.py --seconds 5400

Analyse a finished run with the same script:

    py -3.10 prototype\hls-companion\scripts\soak-onscreen-sampler.py --analyze output\soak\20260915-180000
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CDP_DEFAULT = "http://127.0.0.1:9222"

# Read-only snapshot the renderer exposes. Kept as a string so this file needs no
# knowledge of the player's internals beyond the one seam.
PROBE = "window.__lingerlensSoakProbe ? window.__lingerlensSoakProbe() : null"

# The cue table the renderer already keeps. Read from the renderer rather than the
# backend because the backend needs a per-run session token the window holds.
CUE_TABLE = """
(() => {
  if (!window.__lingerlensSubtitleCues) return null;
  const rows = [];
  for (const cue of window.__lingerlensSubtitleCues.values()) {
    rows.push({
      id: String(cue.id), seq: cue.seq, revision: cue.revision,
      tStart: cue.tStart, tEnd: cue.tEnd, state: cue.state, lang: cue.lang,
      src: String(cue.src || '').slice(0, 300),
      zh: String(cue.zh || '').slice(0, 300),
    });
  }
  return rows;
})()
"""


class Session:
    """One long-lived DevTools connection, reused for every poll."""

    def __init__(self, cdp: str, timeout: float = 30.0) -> None:
        import websocket  # noqa: PLC0415 - only needed once a target exists

        with urllib.request.urlopen(f"{cdp}/json/list", timeout=10) as response:
            targets = json.load(response)
        page = next((t for t in targets if t.get("type") == "page"), None)
        if page is None:
            raise SystemExit(f"{cdp} answered but exposes no page target; is the window up?")
        # Chromium rejects a request carrying an Origin header, hence suppress_origin.
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"],
                                              timeout=timeout, suppress_origin=True)
        self.next_id = 0

    def evaluate(self, expression: str):
        self.next_id += 1
        message_id = self.next_id
        self.ws.send(json.dumps({"id": message_id, "method": "Runtime.evaluate",
                                 "params": {"expression": expression, "returnByValue": True,
                                            "awaitPromise": False}}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") != message_id:
                continue  # an event or a stale reply; keep reading for ours
            payload = message.get("result", {})
            if "exceptionDetails" in payload:
                raise RuntimeError(payload["exceptionDetails"].get("text", "evaluate failed"))
            return payload.get("result", {}).get("value")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001 - shutting down; nothing useful to do
            pass


def sample(args) -> int:
    out = Path(args.output or time.strftime("output/soak/%Y%m%d-%H%M%S")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    samples_path = out / "onscreen.jsonl"
    cues_path = out / "onscreen-cues.jsonl"
    print(f"sampling -> {out}", flush=True)

    try:
        session = Session(args.cdp)
    except (urllib.error.URLError, OSError) as error:
        print(f"cannot reach {args.cdp}: {error}", file=sys.stderr)
        print("start the app with --remote-debugging-port=9222 first", file=sys.stderr)
        return 2

    started = time.monotonic()
    blank_samples = 0
    total_samples = 0
    cues_seen: set[str] = set()
    last_cue_poll = 0.0
    failure: str | None = None

    try:
        with samples_path.open("w", encoding="utf-8", newline="\n") as samples, \
                cues_path.open("w", encoding="utf-8", newline="\n") as cue_log:
            while time.monotonic() - started < args.seconds:
                tick = time.monotonic()
                elapsed = round(tick - started, 3)
                try:
                    snapshot = session.evaluate(PROBE)
                except Exception as error:  # noqa: BLE001 - a closed window is a normal end
                    failure = f"probe:{type(error).__name__}:{error}"
                    print(json.dumps({"event": "probe-error", "error": failure}), flush=True)
                    break
                if snapshot is None:
                    failure = "probe-missing: window.__lingerlensSoakProbe is not defined"
                    print(json.dumps({"event": "fatal", "error": failure}), flush=True)
                    break

                total_samples += 1
                rows = snapshot.get("rows") or []
                if not rows:
                    blank_samples += 1
                samples.write(json.dumps({"elapsed": elapsed, **snapshot}, ensure_ascii=False) + "\n")
                samples.flush()

                # The cue table grows slowly; polling it every tick would dominate
                # the traffic for no extra resolution.
                if tick - last_cue_poll >= args.cue_interval:
                    last_cue_poll = tick
                    try:
                        table = session.evaluate(CUE_TABLE) or []
                    except Exception:  # noqa: BLE001
                        table = []
                    fresh = [cue for cue in table if cue.get("id") not in cues_seen]
                    if table:
                        cue_log.write(json.dumps({"elapsed": elapsed, "cues": table},
                                                 ensure_ascii=False) + "\n")
                        cue_log.flush()
                    cues_seen.update(str(cue.get("id")) for cue in table)
                    if fresh:
                        print(json.dumps({"event": "cues", "elapsed": elapsed,
                                          "new": len(fresh), "total": len(cues_seen)}), flush=True)

                if args.verbose:
                    print(json.dumps({"event": "sample", "elapsed": elapsed,
                                      "rows": len(rows), "hidden": snapshot.get("hidden"),
                                      "wall": snapshot.get("wall"),
                                      "state": snapshot.get("sessionState")}, ensure_ascii=False), flush=True)

                remaining = args.interval - (time.monotonic() - tick)
                if remaining > 0:
                    time.sleep(remaining)
    finally:
        session.close()

    summary = {
        "output": str(out),
        "requestedSeconds": args.seconds,
        "observedSeconds": round(time.monotonic() - started, 3),
        "samples": total_samples,
        "blankSamples": blank_samples,
        "blankRatio": round(blank_samples / total_samples, 4) if total_samples else None,
        "uniqueCuesSeen": len(cues_seen),
        "failure": failure,
        "artifacts": {"samples": str(samples_path), "cues": str(cues_path)},
    }
    (out / "onscreen-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"event": "summary", **summary}, ensure_ascii=False), flush=True)
    return 1 if failure else 0


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * pct))], 3)


def analyze(args) -> int:
    run = Path(args.analyze).resolve()
    samples = [json.loads(line) for line in (run / "onscreen.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if not samples:
        print("no samples", file=sys.stderr)
        return 1

    # Last snapshot of the cue table wins: a cue can be revised (its translation
    # arrives after the source line did), and the final version is the one the
    # viewer was supposed to see.
    cues: dict[str, dict] = {}
    cue_path = run / "onscreen-cues.jsonl"
    if cue_path.exists():
        for line in cue_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            for cue in json.loads(line).get("cues", []):
                cues[str(cue.get("id"))] = cue

    first_seen: dict[str, float] = {}
    last_seen: dict[str, float] = {}
    timed_samples = 0
    for row in samples:
        wall = row.get("wall")
        # Only samples with a real media clock can time anything. Falling back to
        # `elapsed` here would silently mix the sampler's own stopwatch with the
        # media timeline and produce lags that look plausible and mean nothing.
        if not isinstance(wall, (int, float)):
            continue
        timed_samples += 1
        for entry in row.get("rows") or []:
            key = str(entry.get("id"))
            first_seen.setdefault(key, wall)
            last_seen[key] = wall

    print(f"run            {run}")
    print(f"samples        {len(samples)}  ({samples[0]['elapsed']}s .. {samples[-1]['elapsed']}s)")
    print(f"timed samples  {timed_samples}  (the rest had no media clock: nothing was playing)")
    blank = sum(1 for row in samples if not row.get("rows"))
    print(f"blank samples  {blank}/{len(samples)}  ({blank / len(samples):.1%} of the run had no subtitle on screen)")
    print(f"cues in table  {len(cues)}   cues rendered {len(first_seen)}")

    rendered = [c for c in cues.values() if c.get("zh")]
    never = [c for c in rendered if str(c.get("id")) not in first_seen]
    if rendered:
        print(f"translated cues {len(rendered)}   never rendered {len(never)}"
              f"  ({len(never) / len(rendered):.1%})")
    else:
        print("translated cues 0  (nothing was translated during this run)")

    # Lag is measured against the cue's own window on the media timeline. The
    # renderer deliberately draws slightly ahead of the playhead, so a small
    # negative lag is correct behaviour rather than early output.
    lags, late, truncated = [], [], []
    for cue in rendered:
        key = str(cue.get("id"))
        appeared = first_seen.get(key)
        start, end = cue.get("tStart"), cue.get("tEnd")
        if appeared is None or not isinstance(start, (int, float)):
            continue
        lags.append(appeared - start)
        if isinstance(end, (int, float)):
            if appeared > end:
                late.append((key, round(appeared - start, 3), round(appeared - end, 3)))
            window = end - start
            shown = last_seen.get(key, appeared) - appeared
            # Sampled at 0.2s, so anything within two ticks of its window is fine.
            if window > 1.0 and shown < window - 0.5:
                truncated.append((key, round(window, 3), round(shown, 3)))

    if lags:
        print(f"appear lag     P50 {percentile(lags, 0.50)}s  P95 {percentile(lags, 0.95)}s  "
              f"max {round(max(lags), 3)}s  min {round(min(lags), 3)}s")
    print(f"appeared only after its own end: {len(late)}")
    for cue_id, after_start, after_end in late[:20]:
        print(f"    {cue_id}  +{after_start}s after start, +{after_end}s after end")
    print(f"vanished before its own end:     {len(truncated)}")
    for cue_id, window, shown in truncated[:20]:
        print(f"    {cue_id}  window {window}s, on screen {shown}s")

    states: dict[str, int] = {}
    for cue in cues.values():
        states[str(cue.get("state"))] = states.get(str(cue.get("state")), 0) + 1
    print(f"cue states     {dict(sorted(states.items()))}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--analyze", help="analyse a finished run directory instead of sampling")
    parser.add_argument("--cdp", default=CDP_DEFAULT, help="DevTools endpoint of the running window")
    parser.add_argument("--output", help="run directory (default output/soak/<timestamp>)")
    parser.add_argument("--seconds", type=float, default=5400.0, help="how long to sample")
    parser.add_argument("--interval", type=float, default=0.2, help="seconds between screen samples")
    parser.add_argument("--cue-interval", type=float, default=2.0, help="seconds between cue-table snapshots")
    parser.add_argument("--verbose", action="store_true", help="print every sample")
    args = parser.parse_args()
    return analyze(args) if args.analyze else sample(args)


if __name__ == "__main__":
    raise SystemExit(main())
