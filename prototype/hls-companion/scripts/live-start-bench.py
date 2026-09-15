#!/usr/bin/env python3
"""Repeatable red/green benchmark for LingerLens live start-up and throughput.

Drives the running Companion over its local API and reports the two numbers
that decide whether a download change helped:

* ``timeToFirstMediaSeconds`` -- wall clock from /api/start returning to the
  first byte of media appearing on the private playlist.
* ``mediaOverWall`` -- steady-state media seconds gained per wall second once
  media is flowing. Below 1.0 means the source cannot keep up with the live
  edge and the player's buffer drains until it stalls.

Subtitles are forced off so the media path is measured in isolation. Nothing
here prints URLs, cookies, or provider config.

Usage:
    python scripts/live-start-bench.py --url https://www.youtube.com/watch?v=... \
        --quality auto --seconds 90 --label baseline
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8765"


def call(base: str, path: str, payload: dict | None = None, timeout: float = 120.0) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data is not None else "GET",
    )
    # The companion binds loopback only; never let a system proxy intercept it.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def measure(base: str, url: str, quality: str, max_height: int, seconds: float, settle: float) -> dict:
    call(base, "/api/stop", {})
    time.sleep(1.0)

    # Mirror the real UI flow: the user always probes to pick a quality before
    # they can press start, so /api/start is measured with a warm probe.
    probe_started = time.monotonic()
    call(base, "/api/probe", {"url": url})
    probe_seconds = time.monotonic() - probe_started

    started = time.monotonic()
    call(base, "/api/start", {
        "url": url,
        "qualityId": quality,
        "maxHeight": max_height,
        "subtitles": {"enabled": False},
    })
    accepted = time.monotonic()

    first_media_at: float | None = None
    first_media_seconds = 0.0
    settle_at: float | None = None
    settle_media = 0.0
    idle_samples: list[float] = []
    last: dict = {}
    error: str | None = None
    # Guards against a downloader that silently starts at the beginning of the
    # DVR window instead of the live edge: media would flow fine while the
    # viewer watches content that is an hour old.
    behind_live: float | None = None

    while time.monotonic() - accepted < seconds:
        time.sleep(1.0)
        try:
            last = call(base, "/api/status", timeout=20.0)
        except (urllib.error.URLError, TimeoutError, OSError) as failure:
            error = f"status poll failed: {type(failure).__name__}"
            break
        if last.get("error"):
            error = str(last["error"])
            break
        media = float(last.get("privateMediaSeconds") or 0.0)
        now = time.monotonic()
        if first_media_at is None and media > 0:
            first_media_at = now
            first_media_seconds = media
            pdt_epoch = last.get("pdtEpoch")
            if pdt_epoch:
                behind_live = round(time.time() - float(pdt_epoch), 1)
        if first_media_at is not None:
            for ingest in last.get("sourceIngest") or []:
                idle = ingest.get("sourceIdleSeconds")
                if idle is not None:
                    idle_samples.append(float(idle))
            # Ignore the burst that lands right after the first segment: the
            # steady-state ratio is the one that decides whether the player's
            # buffer drains over a long session.
            if settle_at is None and now - first_media_at >= settle:
                settle_at = now
                settle_media = media

    final_media = float(last.get("privateMediaSeconds") or 0.0)
    stopped_at = time.monotonic()
    call(base, "/api/stop", {})

    steady = None
    if settle_at is not None and stopped_at > settle_at:
        steady = round((final_media - settle_media) / (stopped_at - settle_at), 3)

    return {
        "probeSeconds": round(probe_seconds, 2),
        "startAcceptedSeconds": round(accepted - started, 2),
        "timeToFirstMediaSeconds": round(first_media_at - started, 2) if first_media_at else None,
        "firstMediaBurstSeconds": round(first_media_seconds, 2),
        "mediaOverWall": steady,
        "firstSegmentBehindLiveSeconds": behind_live,
        "totalMediaSeconds": round(final_media, 2),
        "sourceIdleP50": round(statistics.median(idle_samples), 2) if idle_samples else None,
        "sourceIdleP95": round(sorted(idle_samples)[int(len(idle_samples) * 0.95)], 2) if len(idle_samples) >= 20 else None,
        "sourceIdleMax": round(max(idle_samples), 2) if idle_samples else None,
        "quality": (last.get("quality") or {}).get("qualityId"),
        "estimatedBitrate": (last.get("quality") or {}).get("estimatedBitrate"),
        "error": error,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--quality", default="auto")
    parser.add_argument("--max-height", type=int, default=1080)
    parser.add_argument("--seconds", type=float, default=90.0)
    parser.add_argument("--settle", type=float, default=10.0, help="Seconds of media flow to discard before measuring the steady rate")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--label", default="run")
    args = parser.parse_args()

    for index in range(args.runs):
        result = measure(args.base, args.url, args.quality, args.max_height, args.seconds, args.settle)
        print(json.dumps({"label": args.label, "run": index + 1, **result}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
