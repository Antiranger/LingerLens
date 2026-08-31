#!/usr/bin/env python3
"""Redacted YouTube live-fragment 403 diagnostic loop.

This is deliberately a probe, not a general HLS proxy or player ingest. It
compares the exact HLS playlist URL for a sequence with a sequence-template
request, follows X-Head-Seqnum, and refreshes yt-dlp extraction on 403/stall.
Signed media URLs and sensitive headers are never logged.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

YTARCHIVE_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:87.0) Gecko/20100101 Firefox/87.0"
SAFE_HEADER_NAMES = {"User-Agent", "Referer", "Origin", "Accept", "Accept-Language"}


@dataclass(frozen=True)
class FragmentRef:
    sequence: int
    url: str


@dataclass(frozen=True)
class ProbeState:
    playlist_url: str
    playlist_headers: dict[str, str]
    fragments: tuple[FragmentRef, ...]
    itag: str
    host: str
    protocol: str


@dataclass(frozen=True)
class FetchResult:
    status: int | None
    byte_count: int
    head_sequence: int | None
    content_type: str | None
    elapsed_ms: int
    error_type: str | None

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300 and self.byte_count > 0


def safe_headers(raw: dict[str, Any] | None) -> dict[str, str]:
    clean: dict[str, str] = {}
    for key, value in (raw or {}).items():
        canonical = str(key).title()
        text = str(value)
        if canonical in SAFE_HEADER_NAMES and "\r" not in text and "\n" not in text:
            clean[canonical] = text
    return clean


def sequence_from_url(url: str) -> int | None:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("sq"):
        try:
            return int(query["sq"][0])
        except ValueError:
            return None
    parts = parsed.path.split("/")
    try:
        index = parts.index("sq")
        return int(parts[index + 1])
    except (ValueError, IndexError):
        return None


def replace_sequence(url: str, sequence: int) -> str:
    """Replace an existing sq value without changing the signed URL shape."""
    parsed = urllib.parse.urlparse(url)
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if any(key == "sq" for key, _ in query_pairs):
        replaced = [(key, str(sequence) if key == "sq" else value) for key, value in query_pairs]
        return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(replaced)))
    parts = parsed.path.split("/")
    try:
        index = parts.index("sq")
    except ValueError as error:
        raise ValueError("fragment URL has no sq sequence component") from error
    if index + 1 >= len(parts):
        raise ValueError("fragment URL has an incomplete sq sequence component")
    parts[index + 1] = str(sequence)
    return urllib.parse.urlunparse(parsed._replace(path="/".join(parts)))


def ytarchive_headers(url: str, source_headers: dict[str, str]) -> dict[str, str]:
    host = urllib.parse.urlparse(url).hostname or ""
    return {
        **source_headers,
        "User-Agent": YTARCHIVE_UA,
        "Host": host,
        "Referer": f"https://{host}/",
        "Origin": "https://www.youtube.com",
        "Connection": "close",
    }


def fetch(url: str, headers: dict[str, str], timeout: float) -> FetchResult:
    started = time.monotonic()
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
            return FetchResult(
                status=response.status,
                byte_count=len(data),
                head_sequence=_optional_int(response.headers.get("X-Head-Seqnum")),
                content_type=response.headers.get("Content-Type"),
                elapsed_ms=round((time.monotonic() - started) * 1000),
                error_type=None,
            )
    except urllib.error.HTTPError as error:
        error.close()
        return FetchResult(
            status=error.code,
            byte_count=0,
            head_sequence=_optional_int(error.headers.get("X-Head-Seqnum")),
            content_type=error.headers.get("Content-Type"),
            elapsed_ms=round((time.monotonic() - started) * 1000),
            error_type="HTTPError",
        )
    except (OSError, TimeoutError) as error:
        return FetchResult(
            status=None,
            byte_count=0,
            head_sequence=None,
            content_type=None,
            elapsed_ms=round((time.monotonic() - started) * 1000),
            error_type=type(error).__name__,
        )


def _optional_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def load_state(page_url: str, format_id: str, yt_dlp: str, timeout: float) -> ProbeState:
    command = [
        yt_dlp,
        "--no-config",
        "--no-playlist",
        "--no-warnings",
        "--skip-download",
        "--no-live-from-start",
        "-J",
        page_url,
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(90, round(timeout * 6)),
        check=False,
    )
    if completed.returncode != 0:
        tail = " | ".join(completed.stderr.strip().splitlines()[-4:])
        raise RuntimeError(f"yt-dlp refresh failed (exit {completed.returncode}): {tail or 'no diagnostic'}")
    info = json.loads(completed.stdout)
    formats = info.get("formats") or []
    selected = next((item for item in formats if str(item.get("format_id")) == format_id), None)
    if not selected or not selected.get("url"):
        available = ",".join(str(item.get("format_id")) for item in formats if item.get("format_id"))
        raise RuntimeError(f"format {format_id} is unavailable; observed format IDs: {available}")
    playlist_url = str(selected["url"])
    playlist_headers = safe_headers(selected.get("http_headers") or info.get("http_headers"))
    result = fetch(playlist_url, {**playlist_headers, "Connection": "close"}, timeout)
    if not result.ok:
        raise RuntimeError(f"media playlist fetch failed: status={result.status} error={result.error_type}")
    request = urllib.request.Request(playlist_url, headers={**playlist_headers, "Connection": "close"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final_url = response.url
        text = response.read().decode("utf-8", "replace")
    fragments: list[FragmentRef] = []
    for line in text.splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        absolute = urllib.parse.urljoin(final_url, item)
        sequence = sequence_from_url(absolute)
        if sequence is not None:
            fragments.append(FragmentRef(sequence, absolute))
    if not fragments:
        raise RuntimeError("media playlist contained no sq-addressable fragments")
    host = urllib.parse.urlparse(fragments[-1].url).hostname or "unknown"
    return ProbeState(
        playlist_url=playlist_url,
        playlist_headers=playlist_headers,
        fragments=tuple(fragments),
        itag=format_id,
        host=host,
        protocol=str(selected.get("protocol") or "unknown"),
    )


def public_result(result: FetchResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "bytes": result.byte_count,
        "xHeadSeqnum": result.head_sequence,
        "contentType": result.content_type,
        "elapsedMs": result.elapsed_ms,
        "error": result.error_type,
    }


def emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def run(args: argparse.Namespace) -> int:
    yt_dlp = shutil.which(args.yt_dlp) or args.yt_dlp
    started = time.monotonic()
    deadline = started + args.duration
    refreshes = 0
    successes = 0
    failures = 0
    first_sequence: int | None = None
    last_sequence: int | None = None
    last_progress_at = started
    state = load_state(args.live_url, args.format_id, yt_dlp, args.request_timeout)
    emit(
        "refresh",
        reason="startup",
        refresh=refreshes,
        formatId=state.itag,
        protocol=state.protocol,
        host=state.host,
        playlistSegments=len(state.fragments),
        playlistFirst=state.fragments[0].sequence,
        playlistLast=state.fragments[-1].sequence,
    )
    exact = state.fragments[max(0, len(state.fragments) - args.edge_offset - 1)]
    exact_result = fetch(exact.url, ytarchive_headers(exact.url, state.playlist_headers), args.request_timeout)
    template_result = fetch(replace_sequence(exact.url, exact.sequence), ytarchive_headers(exact.url, state.playlist_headers), args.request_timeout)
    emit("comparison", sequence=exact.sequence, playlistExact=public_result(exact_result), sqTemplate=public_result(template_result))

    observed_head = max(
        [value for value in (exact_result.head_sequence, template_result.head_sequence, state.fragments[-1].sequence) if value is not None]
    )
    next_sequence = max(state.fragments[0].sequence, observed_head - args.edge_offset)

    while time.monotonic() < deadline:
        reference = min(state.fragments, key=lambda item: abs(item.sequence - next_sequence))
        fragment_url = replace_sequence(reference.url, next_sequence)
        result = fetch(fragment_url, ytarchive_headers(fragment_url, state.playlist_headers), args.request_timeout)
        emit(
            "fragment",
            sequence=next_sequence,
            host=urllib.parse.urlparse(fragment_url).hostname or "unknown",
            refresh=refreshes,
            **public_result(result),
        )
        if result.ok:
            successes += 1
            first_sequence = next_sequence if first_sequence is None else first_sequence
            last_sequence = next_sequence
            last_progress_at = time.monotonic()
            next_sequence += 1
            if result.head_sequence is not None and next_sequence > result.head_sequence + 1:
                time.sleep(args.poll_interval)
            continue

        failures += 1
        should_refresh = result.status == 403 or time.monotonic() - last_progress_at >= args.stall_timeout
        if not should_refresh:
            time.sleep(args.poll_interval)
            continue
        refreshes += 1
        reason = "403" if result.status == 403 else "stall"
        try:
            state = load_state(args.live_url, args.format_id, yt_dlp, args.request_timeout)
            emit(
                "refresh",
                reason=reason,
                refresh=refreshes,
                formatId=state.itag,
                protocol=state.protocol,
                host=state.host,
                playlistSegments=len(state.fragments),
                playlistFirst=state.fragments[0].sequence,
                playlistLast=state.fragments[-1].sequence,
            )
        except Exception as error:
            emit("refresh_error", reason=reason, refresh=refreshes, error=type(error).__name__, detail=str(error))
        time.sleep(args.poll_interval)

    elapsed = round(time.monotonic() - started, 1)
    advanced = 0 if first_sequence is None or last_sequence is None else last_sequence - first_sequence + 1
    passed = elapsed >= args.duration - 1 and advanced >= args.min_progress and time.monotonic() - last_progress_at < args.stall_timeout
    emit(
        "summary",
        verdict="PASS" if passed else "FAIL",
        elapsedSeconds=elapsed,
        firstSequence=first_sequence,
        lastSequence=last_sequence,
        advancedSequences=advanced,
        successes=successes,
        failures=failures,
        refreshes=refreshes,
        idleSeconds=round(time.monotonic() - last_progress_at, 1),
    )
    return 0 if passed else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe YouTube live fragment 403/recovery without logging signed URLs")
    parser.add_argument("live_url")
    parser.add_argument("--format-id", default="301")
    parser.add_argument("--duration", type=float, default=120.0)
    parser.add_argument("--edge-offset", type=int, default=8)
    parser.add_argument("--request-timeout", type=float, default=15.0)
    parser.add_argument("--stall-timeout", type=float, default=20.0)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--min-progress", type=int, default=30)
    parser.add_argument("--yt-dlp", default="yt-dlp")
    args = parser.parse_args()
    if args.duration <= 0 or args.edge_offset < 0 or args.min_progress <= 0:
        parser.error("duration/min-progress must be positive and edge-offset cannot be negative")
    parsed = urllib.parse.urlparse(args.live_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not (host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")):
        parser.error("live_url must be an HTTPS YouTube page URL")
    return args


if __name__ == "__main__":
    try:
        raise SystemExit(run(parse_args()))
    except (RuntimeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        emit("fatal", error=type(error).__name__, detail=str(error))
        raise SystemExit(2)
