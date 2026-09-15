#!/usr/bin/env python3
"""Long-run live soak monitor for LingerLens media and subtitle health.

This is a diagnostic harness, not a product path. It starts an isolated
Companion on a private loopback port, consumes one public live stream, and
records status/subtitle telemetry without touching an existing Electron
instance. Provider credentials are read by Companion from the configured
providers file; this script never opens that file and never echoes its path or
its contents.

Each run writes one directory:

* ``samples.jsonl``  -- one record per ``--interval`` poll of /api/status
* ``cues.jsonl``     -- every cue /api/subtitles returned, with the wall-clock
                        offset at which it was first observed
* ``companion.log``  -- the child Companion's stdout/stderr
* ``summary.json``   -- the roll-up: media-over-wall, stall percentiles, queue
                        maxima, cue state counts
* ``media/``         -- the Companion runtime directory for this run (FFmpeg
                        scratch; LiveSession wipes it on stop)

``summary.json`` records the ``--url`` you measured, so treat a run directory as
private to that stream. Nothing else here writes a URL: the ingest log tails
Companion exposes arrive already redacted by ``ytdlp_ingest._redact``.

Default output is ``output/soak/<YYYYMMDD-HHMMSS>/`` under the repo root, which
``.gitignore`` covers, so a soak never stages media or captions for commit.
``--output`` still takes an explicit directory.

Usage:
    py -3.10 prototype\\hls-companion\\scripts\\soak-live-monitor.py \\
        --url https://www.youtube.com/watch?v=... --seconds 3600 \\
        --providers-file prototype\\hls-companion\\runtime\\providers.json
    py -3.10 prototype\\hls-companion\\scripts\\soak-analyze.py output\\soak\\20260915-180000

Field provenance: every /api/status, /api/subtitles, /api/probe and sourceIngest
key read below was re-verified against ``companion/server.py`` and the code that
builds its response (``core.py``, ``subtitle_pipeline.py``, ``subtitle_store.py``,
``ytdlp_ingest.py``). Keys the server no longer produces are kept in the output
as an explicit ``None`` behind a comment, never silently dropped.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMPANION = ROOT / "prototype" / "hls-companion" / "companion" / "server.py"
DEFAULT_OUTPUT_ROOT = ROOT / "output" / "soak"


def request(base: str, path: str, body: dict | None = None, timeout: float = 30.0) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    http_request = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    # The companion binds loopback only; never let a system proxy intercept it.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(http_request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_server(base: str, process: subprocess.Popen[str], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Companion exited before ready (code {process.returncode})")
        try:
            request(base, "/api/status", timeout=2.0)
            return
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            time.sleep(0.25)
    raise TimeoutError("Companion did not answer /api/status")


def default_output_dir() -> Path:
    """``output/soak/<YYYYMMDD-HHMMSS>/`` under the repo root."""
    return DEFAULT_OUTPUT_ROOT / time.strftime("%Y%m%d-%H%M%S", time.localtime())


def compact_ingest(items: list[dict]) -> list[dict]:
    """Keep the per-leg ingest columns stable across Companion changes.

    ``role`` is added by server.py around the raw snapshot; the rest come from
    ``YtDlpLiveIngest.snapshot`` (media and asr-audio legs). Five keys this
    harness historically read are no longer produced by any media/asr-audio
    ingest snapshot, so they are pinned to ``None`` rather than dropped: an old
    run and a new run stay diffable column-for-column.
    """
    result: list[dict] = []
    for item in items:
        result.append(
            {
                "role": item.get("role"),
                # No longer produced: the ingest snapshot carries no "state".
                # Only the chat ingests report one, and they are never part of
                # status["sourceIngest"].
                "state": None,
                "sourceIdleSeconds": item.get("sourceIdleSeconds"),
                # No longer produced: stall seconds live on the top-level status
                # key (publisher snapshot in core.py), not on the ingest item.
                "sourceStallSeconds": None,
                # No longer produced: the downloader keeps no reconnect counter.
                "reconnects": None,
                # No longer produced: replaced by sourceBytesFetched on the
                # ingest item and forwardedBytes inside each legThroughput leg.
                "bytesRead": None,
                # No longer produced: the ingest reports failures as sourceError.
                "lastError": None,
                "sourceError": item.get("sourceError"),
                "running": item.get("running"),
                "legThroughput": item.get("legThroughput"),
                "logTail": item.get("logTail"),
            }
        )
    return result


def cue_summary(cue: dict) -> dict:
    # Keep enough content to verify that real captions are being produced,
    # while omitting provider internals and avoiding unbounded JSON growth.
    return {
        "id": cue.get("id"),
        "seq": cue.get("seq"),
        "tStart": cue.get("tStart"),
        "tEnd": cue.get("tEnd"),
        "source": str(cue.get("src") or "")[:240],
        "translation": str(cue.get("zh") or "")[:240],
        "state": cue.get("state"),
    }


def start_payload(url: str, max_height: int) -> dict:
    return {
        "url": url,
        "qualityId": "auto",
        "maxHeight": max_height,
        "targetDelaySeconds": 15,
        "subtitles": {
            "enabled": True,
            "sourceLanguage": {"mode": "specified", "tag": "en"},
            "targetLanguage": "zh-Hans",
        },
        "liveMessages": {"enabled": False, "translate": False},
    }


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * pct))
    return round(ordered[index], 3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Public live page URL to measure")
    parser.add_argument("--seconds", type=float, default=3600.0)
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--port", type=int, default=39991, help="Private loopback port for this run's Companion")
    parser.add_argument("--max-height", type=int, default=360)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Run directory for samples/cues/log/summary (default: <repo>/output/soak/<YYYYMMDD-HHMMSS>)",
    )
    parser.add_argument(
        "--providers-file",
        required=True,
        type=Path,
        help="Companion providers JSON. Passed through to the child untouched; never read or echoed here.",
    )
    args = parser.parse_args()

    output = (args.output if args.output is not None else default_output_dir()).resolve()
    output.mkdir(parents=True, exist_ok=True)
    media_dir = output / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output / "samples.jsonl"
    cues_path = output / "cues.jsonl"
    server_log_path = output / "companion.log"
    base = f"http://127.0.0.1:{args.port}"

    log_handle = server_log_path.open("w", encoding="utf-8", newline="\n")
    process = subprocess.Popen(
        [
            sys.executable,
            str(COMPANION),
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
            "--runtime-dir",
            str(media_dir),
            "--providers-file",
            str(args.providers_file.resolve()),
        ],
        cwd=str(ROOT),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    records: list[dict] = []
    cues: list[dict] = []
    last_seq = 0
    start_monotonic: float | None = None
    first_private: float | None = None
    last_private: float | None = None
    failure: str | None = None

    try:
        wait_for_server(base, process)
        probe = request(base, "/api/probe", {"url": args.url}, timeout=45.0)
        qualities = [
            {
                "qualityId": q.get("qualityId"),
                "width": q.get("width"),
                "height": q.get("height"),
                "fps": q.get("fps"),
                "estimatedBitrate": q.get("estimatedBitrate"),
                "requiresTranscode": q.get("requiresTranscode"),
            }
            for q in probe.get("qualities", [])
        ]
        print(
            json.dumps(
                {
                    "event": "probe",
                    "title": probe.get("title"),
                    # No longer produced: /api/probe returns camelCase "isLive"
                    # built from the raw yt-dlp info["is_live"], so the raw key
                    # this harness originally read is absent from the response.
                    "isLive": None,
                    "channel": probe.get("channel") or probe.get("uploader"),
                    "qualities": qualities,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        started = request(base, "/api/start", start_payload(args.url, args.max_height), timeout=90.0)
        start_monotonic = time.monotonic()
        print(json.dumps({"event": "started", "state": started.get("status", {}).get("state"), "quality": started.get("quality")}, ensure_ascii=False), flush=True)

        with jsonl_path.open("w", encoding="utf-8", newline="\n") as sample_handle, cues_path.open("w", encoding="utf-8", newline="\n") as cue_handle:
            while time.monotonic() - start_monotonic < args.seconds:
                tick = time.monotonic()
                try:
                    status = request(base, "/api/status", timeout=20.0)
                    subtitle_response = request(base, f"/api/subtitles?afterSeq={last_seq}", timeout=20.0)
                except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                    failure = f"poll:{type(error).__name__}:{error}"
                    print(json.dumps({"event": "poll-error", "error": failure}, ensure_ascii=False), flush=True)
                    break

                private = float(status.get("privateMediaSeconds") or 0.0)
                if private > 0 and first_private is None:
                    first_private = private
                if private > 0:
                    last_private = private
                elapsed = round(time.monotonic() - start_monotonic, 3)
                subtitles = status.get("subtitles") or {}
                source_cues = [cue_summary(cue) for cue in subtitle_response.get("cues") or []]
                if source_cues:
                    cues.extend(source_cues)
                    for cue in source_cues:
                        cue_handle.write(json.dumps({"elapsed": elapsed, **cue}, ensure_ascii=False) + "\n")
                    cue_handle.flush()
                max_seq = subtitle_response.get("maxSeq")
                if isinstance(max_seq, int):
                    last_seq = max(last_seq, max_seq)

                record = {
                    "wallTime": time.time(),
                    "elapsed": elapsed,
                    "state": status.get("state"),
                    "error": status.get("error"),
                    "uptimeSeconds": status.get("uptimeSeconds"),
                    "privateMediaSeconds": private,
                    "hiddenMediaSeconds": status.get("hiddenMediaSeconds"),
                    "pendingSegments": status.get("pendingSegments"),
                    "publishedSegments": status.get("publishedSegments"),
                    "sourceStallSeconds": status.get("sourceStallSeconds"),
                    "playlistReady": status.get("playlistReady"),
                    "pdtEpoch": status.get("pdtEpoch"),
                    "targetDelaySeconds": status.get("targetDelaySeconds"),
                    "subtitles": {
                        "running": subtitles.get("running"),
                        "asrSeconds": subtitles.get("asrSeconds"),
                        "pendingFinals": subtitles.get("pendingFinals"),
                        "translationBacklog": subtitles.get("translationBacklog"),
                        "translationAttempts": subtitles.get("translationAttempts"),
                        "translationFailures": subtitles.get("translationFailures"),
                        "translationDeadlineExpired": subtitles.get("translationDeadlineExpired"),
                        "translationProviderFailures": subtitles.get("translationProviderFailures"),
                        "translationDropped": subtitles.get("translationDropped"),
                        "translationQueueDelayP95": subtitles.get("translationQueueDelayP95"),
                        "translationProviderDelayP95": subtitles.get("translationProviderDelayP95"),
                        "totalReadyDelayP95": subtitles.get("totalReadyDelayP95"),
                        "translationProviderLabel": subtitles.get("translationProviderLabel"),
                        "translationSuccessReadyLagP50": subtitles.get("translationSuccessReadyLagP50"),
                        "translationSuccessReadyLagP95": subtitles.get("translationSuccessReadyLagP95"),
                        "terminalOutcomeLagP50": subtitles.get("terminalOutcomeLagP50"),
                        "terminalOutcomeLagP95": subtitles.get("terminalOutcomeLagP95"),
                        "mediaAnchor": subtitles.get("mediaAnchor"),
                        "lastError": subtitles.get("lastError"),
                        "lastTranslationError": subtitles.get("lastTranslationError"),
                        "asrReconnects": subtitles.get("asrReconnects"),
                        "captionChunks": subtitles.get("captionChunks"),
                        "sourceOnlyCues": subtitles.get("sourceOnlyCues"),
                    },
                    "ingest": compact_ingest(status.get("sourceIngest") or []),
                    "newCueCount": len(source_cues),
                    "subtitleMaxSeq": last_seq,
                }
                records.append(record)
                sample_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                sample_handle.flush()

                print(
                    json.dumps(
                        {
                            "event": "sample",
                            "elapsed": elapsed,
                            "state": status.get("state"),
                            "private": private,
                            "hidden": status.get("hiddenMediaSeconds"),
                            "stall": status.get("sourceStallSeconds"),
                            "anchor": subtitles.get("mediaAnchor"),
                            "pendingFinals": subtitles.get("pendingFinals"),
                            "translationBacklog": subtitles.get("translationBacklog"),
                            "newCues": len(source_cues),
                            "cueTotal": len(cues),
                            "lastError": subtitles.get("lastError") or status.get("error"),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                remaining = args.interval - (time.monotonic() - tick)
                if remaining > 0:
                    time.sleep(remaining)
    except Exception as error:
        failure = f"run:{type(error).__name__}:{error}"
        print(json.dumps({"event": "fatal", "error": failure}, ensure_ascii=False), flush=True)
    finally:
        try:
            # Companion's own logging is what fills companion.log; its argv holds
            # the providers path, and nothing here re-prints it.
            request(base, "/api/stop", {}, timeout=20.0)
        except Exception:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        log_handle.close()

    elapsed_total = records[-1]["elapsed"] if records else 0.0
    private_gain = (last_private - first_private) if first_private is not None and last_private is not None else None
    media_over_wall = round(private_gain / elapsed_total, 3) if private_gain is not None and elapsed_total > 0 else None
    steady_records = [record for record in records if float(record.get("elapsed") or 0.0) >= 60.0]
    steady_media_over_wall = None
    if len(steady_records) >= 2:
        steady_wall = float(steady_records[-1]["elapsed"]) - float(steady_records[0]["elapsed"])
        steady_media = float(steady_records[-1]["privateMediaSeconds"]) - float(steady_records[0]["privateMediaSeconds"])
        if steady_wall > 0:
            steady_media_over_wall = round(steady_media / steady_wall, 3)
    stalls = [float(r["sourceStallSeconds"]) for r in records if r.get("sourceStallSeconds") is not None]
    pending = [int(r["subtitles"].get("pendingFinals") or 0) for r in records]
    backlogs = [int(r["subtitles"].get("translationBacklog") or 0) for r in records]
    anchors = [r["subtitles"].get("mediaAnchor") or {} for r in records]
    summary = {
        # The measured page URL, by design: a run directory identifies the stream
        # it describes. Keep output/soak/ private to this machine.
        "url": args.url,
        "requestedSeconds": args.seconds,
        "observedSeconds": elapsed_total,
        "pollSamples": len(records),
        # Sorted by str() so a null state cannot raise TypeError against a
        # string one; ordering of the real string states is unchanged.
        "stateCounts": {state: sum(1 for r in records if r.get("state") == state) for state in sorted({r.get("state") for r in records}, key=str)},
        "mediaOverWall": media_over_wall,
        "steadyMediaOverWallAfter60s": steady_media_over_wall,
        "firstPrivateMediaSeconds": first_private,
        "lastPrivateMediaSeconds": last_private,
        "sourceStallP50": percentile(stalls, 0.50),
        "sourceStallP95": percentile(stalls, 0.95),
        "sourceStallMax": max(stalls) if stalls else None,
        "pendingFinalsMax": max(pending) if pending else None,
        "translationBacklogMax": max(backlogs) if backlogs else None,
        "anchorReadySamples": sum(1 for a in anchors if a.get("ready")),
        "anchorSampleCountMax": max((int(a.get("samples") or 0) for a in anchors), default=0),
        "cueCount": len(cues),
        "translationStateCounts": {
            state: sum(1 for cue in cues if cue.get("state") == state)
            for state in sorted({cue.get("state") for cue in cues}, key=str)
        },
        "failure": failure,
        "artifacts": {
            "samples": str(jsonl_path),
            "cues": str(cues_path),
            "companionLog": str(server_log_path),
        },
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"event": "summary", **summary}, ensure_ascii=False), flush=True)
    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
