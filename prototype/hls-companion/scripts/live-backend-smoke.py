#!/usr/bin/env python3
"""Drive a REAL companion server over REAL HTTP against a REAL live stream.

Why this exists: every other test in this repository runs in-process. That is
enough for logic, and it is not enough for wiring -- the R1 defect that this
script found had 687 passing tests behind it, because the unit tests asked a fake
leg a question the real leg cannot answer. A live run found it in one line of log.
So this is the cheap, repeatable version of "run the real thing", and it is meant
to be run before claiming any change to the status payload, the session
lifecycle, the source clock or the subtitle pipeline actually works.

Safety properties, all deliberate:

  * hard wall-clock deadline for the whole script, checked in every loop;
  * its own runtime dir under TEMP -- never the user's live config or media dir;
  * the providers file is opened read-only in practice: auth-snapshot.json is only
    written by cookie import, which this never performs;
  * the server is killed by PID and by its registered children, never by name;
  * only PROJECTED fields are recorded -- no whole payloads, so no signed URL or
    credential can reach the output file;
  * it sends exactly the body player.js sends, because the server gates subtitles
    on `body["subtitles"]["enabled"]`, and a run without it looks fine while
    never touching the subtitle path at all.

Usage (run it with the desktop venv, which is the interpreter the app uses):

    .venv-desktop/Scripts/python.exe prototype/hls-companion/scripts/live-backend-smoke.py
    ... --stream https://www.youtube.com/@ANNnewsCH/live --watch 150 --out out.json

Cost: a run with subtitles enabled makes real ASR and real translation calls for
as long as --watch lasts. Set --no-subtitles to exercise only playback.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SERVER = REPO / "prototype" / "hls-companion" / "companion" / "server.py"
DEFAULT_PROVIDERS = REPO / "prototype" / "hls-companion" / "runtime" / "providers.json"
DEFAULT_STREAM = "https://www.youtube.com/@ANNnewsCH/live"
WRAP_TICKS = 1 << 33
HALF_WRAP = 1 << 32
PTS_HZ = 90_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stream", default=DEFAULT_STREAM)
    parser.add_argument("--providers", type=Path, default=DEFAULT_PROVIDERS)
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--watch", type=float, default=150.0, help="seconds of live session to observe")
    parser.add_argument("--deadline", type=float, default=420.0, help="hard cap for the whole script")
    parser.add_argument("--out", type=Path, default=None, help="evidence JSON (default: a temp file)")
    parser.add_argument("--no-subtitles", action="store_true",
                        help="omit the subtitles block: no ASR leg, no provider spend")
    parser.add_argument("--language", default="ja", help="source language tag for the subtitle request")
    parser.add_argument("--target", default="zh-Hans")
    parser.add_argument("--python", default=sys.executable, help="interpreter for the companion server")
    return parser.parse_args()


def http(base: str, method: str, path: str, body=None, timeout: float = 60):
    data = None
    request = urllib.request.Request(base + path, method=method)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, data, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            return response.status, (json.loads(raw) if raw.strip() else None), None
    except urllib.error.HTTPError as error:
        return error.code, None, error.read().decode("utf-8", "replace")[:400]
    except Exception as error:                                       # noqa: BLE001
        return None, None, f"{type(error).__name__}: {error}"


def leg_view(leg: dict) -> dict:
    """The per-PUMP PTS lives in legThroughput, one level below the ingest dict."""
    return {
        "role": leg.get("role"),
        "running": leg.get("running"),
        "sourceClockValid": leg.get("sourceClockValid"),
        "sourceClockReason": leg.get("sourceClockReason"),
        "sourceIdleSeconds": leg.get("sourceIdleSeconds"),
        "pumpCount": leg.get("legs"),
        "legThroughput": [
            {
                "label": pump.get("label"),
                "sourcePtsFirst": pump.get("sourcePtsFirst"),
                "sourcePtsLast": pump.get("sourcePtsLast"),
                "sourcePtsSamples": pump.get("sourcePtsSamples"),
                "clockValid": pump.get("clockValid"),
                "clockReason": pump.get("clockReason"),
                "bytesPerSecond": pump.get("bytesPerSecond"),
            }
            for pump in (leg.get("legThroughput") or [])
        ],
    }


def project(status: dict) -> dict:
    subtitles = status.get("subtitles") or {}
    usage = status.get("usage") or {}
    return {
        "state": status.get("state"),
        "error": status.get("error"),
        "mediaSessionId": status.get("mediaSessionId"),
        "uptimeSeconds": status.get("uptimeSeconds"),
        "targetDelaySeconds": status.get("targetDelaySeconds"),
        "hiddenMediaSeconds": status.get("hiddenMediaSeconds"),
        "videoContentSeconds": status.get("videoContentSeconds"),
        "sourceIdleSeconds": status.get("sourceIdleSeconds"),
        "hasSourceRecovery": "sourceRecovery" in status,
        "ingest": [leg_view(item) for item in (status.get("sourceIngest") or [])],
        "subtitleKeys": sorted(subtitles.keys()),
        "subtitles": {
            key: value for key, value in subtitles.items()
            if isinstance(value, (int, float, bool)) or value is None
            or (isinstance(value, str) and len(value) <= 60)
        },
        "mediaAnchor": subtitles.get("mediaAnchor"),
        "timingSourceCounts": subtitles.get("timingSourceCounts"),
        "asrEstimatedCostCny": usage.get("asrEstimatedCostCny"),
        "mediaClock": status.get("mediaClock"),
    }


def child_pids(root: int) -> list[int]:
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ParentProcessId={root}').ProcessId"],
            capture_output=True, text=True, timeout=25,
        )
        return [int(line) for line in completed.stdout.split() if line.strip().isdigit()]
    except Exception:                                                # noqa: BLE001
        return []


def kill_tree(pid: int) -> list[str]:
    done: list[str] = []
    for child in child_pids(pid):
        done.append(f"child {child}")
        subprocess.run(["taskkill", "/PID", str(child), "/T", "/F"], capture_output=True, timeout=25)
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=25)
    return done


def two_leg_offset(sample: dict) -> dict | None:
    """C = A0 - V0 from the two INDEPENDENT legs, raw and tick-modular.

    Both numbers are reported: they differ exactly when a 33-bit wrap falls
    between the two legs' first packets, which is the case R1 exists for.
    """
    def first_pts(role: str):
        for item in sample["ingest"]:
            if item["role"] != role:
                continue
            for pump in item["legThroughput"]:
                if pump["sourcePtsFirst"] is not None:
                    return pump["sourcePtsFirst"]
        return None

    audio_first, video_first = first_pts("asr-audio"), first_pts("media")
    if audio_first is None or video_first is None:
        return {"asrAudioFirst": audio_first, "mediaVideoFirst": video_first}
    unwrapped = ((round(audio_first * PTS_HZ) - round(video_first * PTS_HZ) + HALF_WRAP)
                 % WRAP_TICKS) - HALF_WRAP
    return {
        "asrAudioFirst": audio_first,
        "mediaVideoFirst": video_first,
        "cRawSeconds": round(audio_first - video_first, 6),
        "cTickModularSeconds": round(unwrapped / PTS_HZ, 6),
        "wrapPeriodSeconds": round(WRAP_TICKS / PTS_HZ, 6),
    }


def main() -> int:
    args = parse_args()
    start = time.monotonic()
    deadline = start + args.deadline
    base = f"http://127.0.0.1:{args.port}"
    out: dict = {
        "stream": args.stream, "port": args.port, "subtitlesRequested": not args.no_subtitles,
        "samples": [], "events": [],
    }

    def event(kind: str, **kw) -> None:
        out["events"].append({"at": round(time.monotonic() - start, 1), "kind": kind, **kw})

    body = {
        "url": args.stream,
        "qualityId": "auto",
        "targetDelaySeconds": 15,
        "liveMessages": {"enabled": False},
    }
    if not args.no_subtitles:
        # player.js:735-748, verbatim in shape.
        body["subtitles"] = {
            "enabled": True,
            "sourceLanguage": {"mode": "specified", "tag": args.language},
            "targetLanguage": args.target,
        }

    runtime = Path(tempfile.mkdtemp(prefix="ll-live-smoke-"))
    server_out = runtime / "server.out"
    server_err = runtime / "server.err"
    out["runtimeDir"] = str(runtime)
    print(f"[smoke] runtime dir {runtime}", flush=True)
    handle = subprocess.Popen(
        [args.python, str(SERVER), "--host", "127.0.0.1", "--port", str(args.port),
         "--runtime-dir", str(runtime), "--providers-file", str(args.providers)],
        stdout=server_out.open("wb"), stderr=server_err.open("wb"),
        cwd=str(SERVER.parents[1]),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    out["serverPid"] = handle.pid
    event("server-spawned", pid=handle.pid)

    try:
        idle = None
        for _ in range(40):
            if time.monotonic() > deadline or handle.poll() is not None:
                break
            code, payload, _ = http(base, "GET", "/api/status", timeout=5)
            if code == 200 and payload is not None:
                idle = payload
                break
            time.sleep(1.0)
        if idle is None:
            event("server-never-answered", exitCode=handle.poll())
        else:
            event("idle-status")
            out["idle"] = project(idle)
            out["checks"] = {
                "D1_sourceRecovery_absent": "sourceRecovery" not in idle,
                "S1_mediaSessionId_present": "mediaSessionId" in idle,
                "S1_mediaSessionId_idle_value": idle.get("mediaSessionId"),
            }

        if idle is not None and time.monotonic() < deadline:
            code, payload, error = http(base, "POST", "/api/start", body, timeout=180)
            event("start-response", status=code, error=error,
                  keys=sorted(payload.keys()) if isinstance(payload, dict) else None)
            if isinstance(payload, dict):
                out["startMediaSessionId"] = (payload.get("status") or {}).get("mediaSessionId")

            while time.monotonic() - start < args.watch and time.monotonic() < deadline:
                code, payload, error = http(base, "GET", "/api/status", timeout=10)
                if code == 200 and payload is not None:
                    out["samples"].append({"at": round(time.monotonic() - start, 1), **project(payload)})
                else:
                    event("status-failed", status=code, error=error)
                time.sleep(4.0)

            for asset in ("/playback-recovery.js", "/player.js"):
                try:
                    with urllib.request.urlopen(base + asset, timeout=20) as response:
                        text = response.read().decode("utf-8", "replace")
                    out["checks"][f"asset{asset}"] = {"status": response.status, "bytes": len(text)}
                except Exception as asset_error:                     # noqa: BLE001
                    out["checks"][f"asset{asset}"] = f"FAILED: {asset_error}"

            code, payload, _ = http(base, "GET", "/api/subtitles", timeout=20)
            if code == 200 and isinstance(payload, dict):
                cues = payload.get("cues") or []
                out["subtitlesEndpoint"] = {
                    "status": code, "maxSeq": payload.get("maxSeq"),
                    "cueCount": len(cues) if isinstance(cues, list) else None,
                    "stats": payload.get("stats"),
                }

            code, payload, _ = http(base, "GET", "/api/logs", timeout=20)
            if code == 200 and payload is not None:
                records = payload if isinstance(payload, list) else (payload.get("records") or [])
                out["logCount"] = len(records)
                out["logs"] = [{"level": r.get("level"), "source": r.get("source"),
                                "message": str(r.get("message"))[:200]} for r in records[-60:]]
                # A session whose clock is valid must not be refused.
                out["checks"]["R1_clockRefusals"] = sum(
                    1 for r in records if "源时钟不可用" in str(r.get("message"))
                )

            code, _, error = http(base, "POST", "/api/stop", {}, timeout=60)
            event("stop-response", status=code, error=error)
            time.sleep(6)
            code, payload, _ = http(base, "GET", "/api/status", timeout=10)
            if code == 200 and payload is not None:
                out["afterStop"] = project(payload)
    finally:
        event("teardown-begin")
        out["killedPids"] = kill_tree(handle.pid) + kill_tree(handle.pid)
        time.sleep(2)
        out["serverExitCode"] = handle.poll()
        out["serverStillAlive"] = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"@(Get-Process -Id {handle.pid} -ErrorAction SilentlyContinue).Count"],
            capture_output=True, text=True, timeout=25,
        ).stdout.strip()
        for name, path in (("stdout", server_out), ("stderr", server_err)):
            if path.exists():
                out[f"server{name.capitalize()}"] = path.read_text(
                    encoding="utf-8", errors="replace")[-4000:]
        shutil.rmtree(runtime, ignore_errors=True)
        if out.get("samples"):
            out["twoLegOffset"] = two_leg_offset(out["samples"][-1])

    target = args.out or (Path(tempfile.gettempdir()) / "ll-live-evidence.json")
    target.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[smoke] wrote {target} ({target.stat().st_size} bytes)", flush=True)
    summary = {k: out.get(k) for k in
               ("checks", "serverStillAlive", "startMediaSessionId", "logCount",
                "twoLegOffset", "subtitlesEndpoint") if out.get(k) is not None}
    if out.get("subtitlesEndpoint"):
        summary["cues"] = out["subtitlesEndpoint"].get("cueCount")
        summary["anchor"] = (out["subtitlesEndpoint"].get("stats") or {}).get("mediaAnchor")
    print(json.dumps(summary, indent=1, ensure_ascii=False, default=str)[:4000], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
