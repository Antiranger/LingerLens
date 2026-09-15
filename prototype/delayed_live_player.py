#!/usr/bin/env python3
"""PROTOTYPE: play a yt-dlp-supported livestream behind ingest by a fixed delay."""

from __future__ import annotations

import argparse
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import BinaryIO

DEFAULT_DELAY_SECONDS = 10.0
DEFAULT_MAX_HEIGHT = 720
READ_SIZE = 64 * 1024


@dataclass
class RelayStats:
    lock: threading.Lock
    bytes_received: int = 0
    bytes_released: int = 0
    buffered_bytes: int = 0
    first_chunk_at: float | None = None
    latest_chunk_at: float | None = None

    def received(self, size: int, now: float) -> None:
        with self.lock:
            self.bytes_received += size
            self.buffered_bytes += size
            self.first_chunk_at = self.first_chunk_at or now
            self.latest_chunk_at = now

    def released(self, size: int) -> None:
        with self.lock:
            self.bytes_released += size
            self.buffered_bytes -= size

    def snapshot(self) -> tuple[int, int, int, float | None, float | None]:
        with self.lock:
            return (
                self.bytes_received,
                self.bytes_released,
                self.buffered_bytes,
                self.first_chunk_at,
                self.latest_chunk_at,
            )


def executable(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Required executable not found on PATH: {name}")
    return path


def format_selector(max_height: int) -> str:
    return (
        f"best[height<={max_height}][vcodec!=none][acodec!=none]/"
        "best[vcodec!=none][acodec!=none]/best"
    )


def build_ytdlp_command(args: argparse.Namespace, yt_dlp: str) -> list[str]:
    command = [
        yt_dlp,
        "--no-playlist",
        "--no-live-from-start",
        "--hls-use-mpegts",
        "--no-progress",
        "--format",
        format_selector(args.max_height),
        "--output",
        "-",
    ]
    if args.cookies_from_browser:
        command.extend(["--cookies-from-browser", args.cookies_from_browser])
    command.append(args.url)
    return command


def build_player_command(args: argparse.Namespace, player: str) -> list[str]:
    if args.headless:
        return [
            player,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-i",
            "pipe:0",
            "-map",
            "0:v?",
            "-map",
            "0:a?",
            "-f",
            "null",
            "-",
        ]

    return [
        player,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-fflags",
        "+genpts",
        "-framedrop",
        "-sync",
        "audio",
        "-window_title",
        f"LingerLens prototype — {args.delay:g}s delayed live",
        "-i",
        "pipe:0",
    ]


def read_stream(
    source: BinaryIO,
    chunks: queue.Queue[tuple[float, bytes] | None],
    stats: RelayStats,
    stop: threading.Event,
) -> None:
    try:
        while not stop.is_set():
            data = source.read(READ_SIZE)
            if not data:
                break
            now = time.monotonic()
            stats.received(len(data), now)
            chunks.put((now, data))
    finally:
        chunks.put(None)


def write_delayed_stream(
    destination: BinaryIO,
    chunks: queue.Queue[tuple[float, bytes] | None],
    stats: RelayStats,
    stop: threading.Event,
    delay_seconds: float,
) -> None:
    pending: list[bytes] = []
    release_at: float | None = None
    source_finished = False
    try:
        while not stop.is_set() and release_at is None:
            item = chunks.get()
            if item is None:
                source_finished = True
                break
            arrived_at, data = item
            release_at = arrived_at + delay_seconds
            pending.append(data)

        while not stop.is_set() and release_at is not None and time.monotonic() < release_at:
            timeout = min(release_at - time.monotonic(), 0.1)
            try:
                item = chunks.get(timeout=max(timeout, 0.001))
            except queue.Empty:
                continue
            if item is None:
                source_finished = True
                break
            _, data = item
            pending.append(data)

        # Flush the accumulated startup window in original byte order. ffplay
        # then has real media headroom instead of receiving one tiny fragment.
        for data in pending:
            if stop.is_set():
                break
            destination.write(data)
            stats.released(len(data))
        destination.flush()

        while not stop.is_set() and not source_finished:
            item = chunks.get()
            if item is None:
                break
            _, data = item
            destination.write(data)
            destination.flush()
            stats.released(len(data))
    except (BrokenPipeError, OSError):
        stop.set()
    finally:
        try:
            destination.close()
        except OSError:
            pass


def terminate(process: subprocess.Popen[bytes] | None) -> None:
    if not process or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def print_status(stats: RelayStats, delay_seconds: float, started_at: float) -> None:
    received, released, buffered, first_at, latest_at = stats.snapshot()
    if first_at is None:
        print("[LingerLens] Waiting for the first media bytes from yt-dlp...", flush=True)
        return

    ingest_age = time.monotonic() - first_at
    phase = (
        f"building delay buffer {min(ingest_age, delay_seconds):.1f}/{delay_seconds:.1f}s"
        if released == 0
        else "playing delayed stream"
    )
    mib = buffered / (1024 * 1024)
    received_mib = received / (1024 * 1024)
    idle = time.monotonic() - (latest_at or started_at)
    print(
        f"[LingerLens] {phase} | queued {mib:.1f} MiB | "
        f"received {received_mib:.1f} MiB | ingest idle {idle:.1f}s",
        flush=True,
    )


def run(args: argparse.Namespace) -> int:
    yt_dlp = executable("yt-dlp")
    player = executable("ffmpeg" if args.headless else "ffplay")
    ytdlp_command = build_ytdlp_command(args, yt_dlp)
    player_command = build_player_command(args, player)

    if args.dry_run:
        print("yt-dlp:", subprocess.list2cmdline(ytdlp_command))
        print("player:", subprocess.list2cmdline(player_command))
        return 0

    print(f"[LingerLens] Starting fixed {args.delay:g}s delay prototype")
    print(f"[LingerLens] Format preference: muxed stream up to {args.max_height}p")
    print("[LingerLens] The player opens now but media starts only after the delay buffer is built.")
    print("[LingerLens] Press Ctrl+C or close the player to stop.\n")

    stop = threading.Event()
    chunks: queue.Queue[tuple[float, bytes] | None] = queue.Queue()
    stats = RelayStats(lock=threading.Lock())
    ytdlp_process: subprocess.Popen[bytes] | None = None
    player_process: subprocess.Popen[bytes] | None = None

    try:
        ytdlp_process = subprocess.Popen(
            ytdlp_command,
            stdout=subprocess.PIPE,
            stderr=None,
            bufsize=0,
        )
        player_process = subprocess.Popen(
            player_command,
            stdin=subprocess.PIPE,
            stderr=None,
            bufsize=0,
        )
        if ytdlp_process.stdout is None or player_process.stdin is None:
            raise RuntimeError("Failed to open the media relay pipes")

        reader = threading.Thread(
            target=read_stream,
            args=(ytdlp_process.stdout, chunks, stats, stop),
            name="yt-dlp-reader",
            daemon=True,
        )
        writer = threading.Thread(
            target=write_delayed_stream,
            args=(player_process.stdin, chunks, stats, stop, args.delay),
            name="delayed-player-writer",
            daemon=True,
        )
        reader.start()
        writer.start()

        started_at = time.monotonic()
        while not stop.wait(1):
            print_status(stats, args.delay, started_at)
            player_code = player_process.poll()
            ytdlp_code = ytdlp_process.poll()
            if player_code is not None:
                stop.set()
                if player_code != 0:
                    print(f"[LingerLens] Player exited with code {player_code}", file=sys.stderr)
                break
            if ytdlp_code is not None and not reader.is_alive():
                if ytdlp_code != 0:
                    print(f"[LingerLens] yt-dlp exited with code {ytdlp_code}", file=sys.stderr)
                break

        writer.join(timeout=args.delay + 3)
        return player_process.poll() or ytdlp_process.poll() or 0
    except KeyboardInterrupt:
        print("\n[LingerLens] Stopping prototype...")
        return 130
    finally:
        stop.set()
        terminate(ytdlp_process)
        terminate(player_process)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PROTOTYPE: play a yt-dlp-supported livestream behind ingest by a fixed delay."
    )
    parser.add_argument("url", nargs="?", help="YouTube/Bilibili live-room URL")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS)
    parser.add_argument("--max-height", type=int, default=DEFAULT_MAX_HEIGHT)
    parser.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER",
        help="Pass through to yt-dlp for login-required streams, e.g. chrome or edge",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print child commands without running them")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Decode to a null sink with ffmpeg instead of opening ffplay (diagnostics)",
    )
    args = parser.parse_args()
    if not args.url:
        parser.error("a live URL is required")
    if args.delay < 0:
        parser.error("--delay must be zero or greater")
    if args.max_height <= 0:
        parser.error("--max-height must be positive")
    return args


if __name__ == "__main__":
    try:
        raise SystemExit(run(parse_args()))
    except RuntimeError as error:
        print(f"[LingerLens] {error}", file=sys.stderr)
        raise SystemExit(2)
