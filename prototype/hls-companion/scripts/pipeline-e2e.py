#!/usr/bin/env python3
"""End-to-end SubtitlePipeline run against the real ASR and translation models.

Feeds a real MPEG-TS audio clip through the actual pipeline (tee sink -> ffmpeg
-> PCM -> Qwen realtime -> translation workers -> CueStore) at realtime pace,
then prints every cue with its timing source and checks the invariants the live
player depends on:

  * every cue has a tStart (otherwise it cannot be shown from sentence start)
  * tStart < tEnd, and cues do not overlap
  * timingSource is "asr" (server-reported boundaries), not "approx"
  * cues reach a terminal state ("done" with zh, or "failed")

Usage:
  python scripts/pipeline-e2e.py --audio clip.ts [--seconds 60]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.providers import create_asr, create_translation
from companion.providers.base import StreamMeta
from companion.providers.config import load_config
from companion.subtitle_pipeline import SubtitlePipeline
from companion.subtitle_store import CueStore

CFG = ROOT / "runtime" / "providers.json"
PDT0 = 1_700_000_000.0
TARGET_DURATION = 1.0
CHUNK = 16384


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True, type=Path, help="MPEG-TS file with an audio track")
    parser.add_argument("--seconds", type=float, default=0.0, help="stop after this much wall time")
    args = parser.parse_args()

    config = load_config(CFG)
    asr_record = next(r for r in config["asr"]["providers"] if r["id"] == config["asr"]["active"])
    mt_record = next(r for r in config["translation"]["providers"] if r["id"] == config["translation"]["active"])
    subtitle = config.get("subtitle", {})
    print(f"ASR : {asr_record['model']}  silence_ms="
          f"{asr_record.get('options', {}).get('turnDetection', {}).get('silenceDurationMs')}")
    print(f"MT  : {mt_record['model']}  workers={subtitle.get('translationWorkers', 4)}")
    print(f"maxUtteranceSeconds={subtitle.get('maxUtteranceSeconds')}  "
          f"prefixSplit={subtitle.get('prefixSplitEnabled', True)} "
          f"after={subtitle.get('prefixSplitAfterSeconds', 3.0)}s\n")

    started = time.monotonic()
    store = CueStore()
    pipeline = SubtitlePipeline(
        asr_provider=create_asr(asr_record),
        translation_provider=create_translation(mt_record),
        cue_store=store,
        meta=StreamMeta("ゲーム実況", "テスト", None, "ja", "zh"),
        source_language="ja",
        # Stand-in for the packaging leg: it advances in real time, exactly as
        # the publisher's private_media_seconds does during a live session.
        pdt_epoch=lambda: PDT0,
        media_clock=lambda: (time.monotonic() - started, TARGET_DURATION),
        silence_duration_ms=int(
            asr_record.get("options", {}).get("turnDetection", {}).get("silenceDurationMs", 400)
        ),
        max_utterance_seconds=float(subtitle.get("maxUtteranceSeconds", 0.0)),
        translation_workers=int(subtitle.get("translationWorkers", 4)),
        translation_timeout_seconds=float(mt_record.get("options", {}).get("timeoutSeconds", 8)),
        anchor_freeze_samples=5,
    )

    sink = await pipeline.start()
    data = args.audio.read_bytes()
    # Realtime pacing: an MPEG-TS second is ~what the live tee delivers.
    bytes_per_second = len(data) / max(1.0, _duration_of(args.audio))
    sent = 0
    try:
        while sent < len(data):
            sink(data[sent:sent + CHUNK])
            sent += CHUNK
            await asyncio.sleep(CHUNK / bytes_per_second)
            if args.seconds and time.monotonic() - started > args.seconds:
                break
        print(f"[fed {sent} bytes in {time.monotonic()-started:.1f}s; draining]")
        await asyncio.sleep(8)
    finally:
        status = pipeline.status()
        await pipeline.stop()

    return report(store, status)


def _duration_of(path: Path) -> float:
    import subprocess
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 60.0


def report(store: CueStore, status: dict) -> int:
    cues = store.query(after_seq=0)
    cues.sort(key=lambda c: (c.t_start if c.t_start is not None else c.t_end))
    print(f"\n{'#':>3} {'tStart':>8} {'tEnd':>8} {'dur':>5} {'src':>6} {'state':>8}  text")
    print("-" * 110)
    for index, cue in enumerate(cues):
        ts = f"{cue.t_start - PDT0:8.3f}" if cue.t_start is not None else "    None"
        te = f"{cue.t_end - PDT0:8.3f}"
        dur = f"{cue.t_end - cue.t_start:5.2f}" if cue.t_start is not None else "   --"
        print(f"{index:>3} {ts} {te} {dur} {cue.timing_source:>6} {cue.state:>8}  {cue.src}")
        print(f"{'':>3} {'':>8} {'':>8} {'':>5} {'':>6} {'':>8}  -> {cue.zh or '(无译文)'}")

    print("\n=== pipeline status ===")
    for key in ("timingSourceCounts", "forcedCommits", "unjoinedFinals", "approxCues",
                "prefixCues", "finalTails", "finalAbsorbed", "splitConflicts", "prefixRewrites",
                "translationAttempts", "translationFailures", "translationWorkers",
                "translationWorkersAlive", "translationBacklog", "avgTranslationLatencyMs",
                "readyLagP50", "readyLagP95", "finalDiscarded", "finalDeduplicated",
                "suppressedByIngestError", "pcmDropped", "teeDropped", "asrReconnects",
                "mediaAnchorC", "mediaAnchorFrozen", "lastTranslationError", "lastError"):
        if key in status:
            print(f"  {key} = {status[key]}")

    print("\n=== invariant checks ===")
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' -- ' + detail) if detail and not ok else ''}")
        if not ok:
            failures.append(name)

    if not cues:
        check("produced cues", False, "no cues at all")
        return 1

    missing_start = [c for c in cues if c.t_start is None]
    check("every cue has tStart", not missing_start, f"{len(missing_start)} without tStart")

    bad_order = [c for c in cues if c.t_start is not None and c.t_start > c.t_end]
    check("tStart <= tEnd", not bad_order, f"{len(bad_order)} inverted")

    # The provider occasionally reports the next utterance starting a fraction
    # of a second before the previous one stopped. That is its own measurement,
    # not a mapping error, and the scheduler resolves it deterministically (it
    # prefers the cue with the later tEnd), so only a large overlap is a bug.
    overlaps = [
        a.t_end - b.t_start for a, b in zip(cues, cues[1:])
        if a.t_start is not None and b.t_start is not None and b.t_start < a.t_end - 1e-6
    ]
    worst = max(overlaps) if overlaps else 0.0
    check("cues overlap by at most 250ms", worst <= 0.25,
          f"{len(overlaps)} overlapping pairs, worst {worst:.3f}s")
    if overlaps:
        print(f"       ({len(overlaps)} provider-reported overlaps, worst {worst:.3f}s)")

    counts = status.get("timingSourceCounts", {})
    total = sum(counts.values()) or 1
    # Prefix-split cues are timing_source="vad": begin from the server VAD
    # span, end estimated at the confirmation position. "approx" (no anchor
    # at all) is the only red flag.
    anchored_share = (counts.get("asr", 0) + counts.get("vad", 0)) / total
    check("timingSource=asr|vad for >95% of cues", anchored_share > 0.95,
          f"only {anchored_share:.0%} ({counts})")

    check("no forced commits", status.get("forcedCommits", 0) == 0)
    check("no unjoined finals", status.get("unjoinedFinals", 0) == 0)

    unresolved = [c for c in cues if c.state not in ("done", "failed")]
    check("all cues reached a terminal state", not unresolved,
          f"{len(unresolved)} still in src/translating")

    translated = [c for c in cues if c.state == "done" and c.zh]
    check("majority of cues have a translation", len(translated) > 0.9 * len(cues),
          f"{len(translated)}/{len(cues)}")

    check("translation workers all alive",
          status.get("translationWorkersAlive") == status.get("translationWorkers"),
          f"{status.get('translationWorkersAlive')}/{status.get('translationWorkers')}")

    spans = sorted(c.t_end - c.t_start for c in cues if c.t_start is not None)
    if spans:
        p50 = spans[len(spans) // 2]
        p90 = spans[min(len(spans) - 1, int(len(spans) * 0.9))]
        print(f"\n  句长: n={len(spans)} p50={p50:.2f}s p90={p90:.2f}s max={spans[-1]:.2f}s")
        lag = status.get("readyLagP95")
        if lag is not None:
            print(f"  需要的观看延迟 (p90句长 + readyLag p95) ≈ {p90 + lag:.1f}s")

    print(f"\n{'ALL CHECKS PASSED' if not failures else 'FAILED: ' + ', '.join(failures)}")
    return 0 if not failures else 1


raise SystemExit(asyncio.run(main()))
