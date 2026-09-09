"""yt-dlp-owned YouTube live acquisition for the local CMAF pipeline.

Why two processes instead of one merged "312+234" download: yt-dlp delegates
muxed multi-format live HLS to a single internal ffmpeg whose HLS demuxer
reads both playlists serially. On low-latency streams (1s fragments, short
playlist windows) it chronically falls behind and logs "skipping N segments
ahead, expired from playlists", punching holes into the media timeline.
Two single-format yt-dlp processes each keep up (measured: zero mid-stream
skips), so video and audio are acquired independently and muxed downstream by
our own packaging ffmpeg.

Each process writes MPEG-TS to stdout; a localhost TCP pump per process hands
the bytes to the packaging ffmpeg (`-i tcp://127.0.0.1:<port>`), because a
single ffmpeg process cannot inherit two anonymous pipes on Windows.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from .source_timeline import MpegTsPtsProbe

ROOT = Path(__file__).resolve().parents[1]
VENDORED_YT_DLP = ROOT / "vendor" / "yt-dlp" / "yt-dlp.exe"


class _TcpPump:
    """Accept localhost TCP connections and stream proc.stdout bytes into them.

    The pump deliberately does not read stdout until a client (the packaging
    ffmpeg) connects; OS pipe backpressure then naturally throttles yt-dlp.
    """

    def __init__(
        self,
        label: str,
        on_first_byte: Callable[[], None] | None = None,
        pts_probe: MpegTsPtsProbe | None = None,
        on_pts: Callable[[list[float]], None] | None = None,
    ):
        self.label = label
        # Fires once, when this leg has produced real media. That is the only
        # log-independent proof that yt-dlp is past extraction and no longer
        # needs the short-lived credential files.
        self._on_first_byte = on_first_byte
        self._pts_probe = pts_probe
        self._on_pts = on_pts
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(0.5)
        self.port = self.listener.getsockname()[1]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._source: Any = None
        self.forwarded_chunks = 0
        self.forwarded_bytes = 0

    @property
    def url(self) -> str:
        return f"tcp://127.0.0.1:{self.port}"

    def start(self, source: Any) -> None:
        self._source = source
        self._thread = threading.Thread(target=self._run, name=f"tcp-pump-{self.label}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                while not self._stop.is_set():
                    # BufferedReader.read(size) is allowed to wait until the
                    # requested size is filled.  A live audio HLS leg may
                    # produce only a few kilobytes between segments, so that
                    # turns a ready-to-decode burst into seconds of latency.
                    # read1() returns after one underlying read, preserving
                    # the existing backpressure without waiting for EOF.
                    read1 = getattr(self._source, "read1", None)
                    chunk = (read1(65536) if read1 is not None else self._source.read(65536))
                    if not chunk:
                        return
                    if self._pts_probe is not None:
                        points = self._pts_probe.feed(chunk)
                        if points and self._on_pts is not None:
                            self._on_pts(points)
                    conn.sendall(chunk)
                    self.forwarded_chunks += 1
                    self.forwarded_bytes += len(chunk)
                    if self.forwarded_chunks == 1 and self._on_first_byte:
                        try:
                            self._on_first_byte()
                        except Exception:
                            pass
            except ValueError:
                # Closing a Windows pipe while BufferedReader.read() is pending
                # can surface as ValueError/PyMemoryView_FromBuffer. It is an
                # expected shutdown result only after stop() has claimed the pump.
                if self._stop.is_set():
                    return
                raise
            except (BrokenPipeError, ConnectionResetError, OSError):
                # ffmpeg went away; wait for the next connection.
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def stop(self) -> None:
        self._stop.set()
        try:
            self.listener.close()
        except OSError:
            pass
        if self._thread:
            self._thread.join(timeout=3)


class YtDlpLiveIngest:
    """Run current yt-dlp as the sole owner of YouTube extraction/download.

    A "v+a" format selector spawns one yt-dlp process per leg; a single muxed
    selector spawns one process. Every leg is pumped over localhost TCP.
    """

    def __init__(
        self,
        page_url: str,
        format_selector: str,
        auth_args: list[str],
        yt_dlp: str | None = None,
        auth_cleanup: Callable[[], None] | None = None,
        info_json_path: str | None = None,
        selected_protocol: str | None = None,
    ):
        self.page_url = page_url
        # When the caller can hand over a fresh probe result, yt-dlp loads it
        # instead of re-running the whole YouTube extraction it already paid
        # for during /api/probe (measured: ~8s off the critical path per leg).
        self.info_json_path = info_json_path
        self.selected_protocol = selected_protocol
        self.format_selector = format_selector
        self.selectors = [part for part in format_selector.split("+") if part] or [format_selector]
        self.auth_args = list(auth_args)
        self.yt_dlp = yt_dlp or self._default_executable()
        self.auth_cleanup = auth_cleanup
        self._auth_cleaned = False
        self._legs_past_extraction: set[int] = set()
        self.processes: list[subprocess.Popen[bytes]] = []
        self.pumps: list[_TcpPump] = []
        self._log_threads: list[threading.Thread] = []
        self.started_at: float | None = None
        self.last_output_at: float | None = None
        self.error: str | None = None
        self.log_tail: deque[str] = deque(maxlen=30)
        self._stop_requested = False
        self._lock = threading.Lock()
        self._last_leg_marks: tuple[float, list[int]] | None = None
        self.source_pts: list[list[float]] = []

    @staticmethod
    def _default_executable() -> str:
        if VENDORED_YT_DLP.is_file():
            return str(VENDORED_YT_DLP)
        found = shutil.which("yt-dlp")
        if not found:
            raise RuntimeError("Current yt-dlp executable is not available")
        return found

    def command(self, selector: str | None = None) -> list[str]:
        proxy = self._environment_proxy()
        proxy_args = ["--proxy", proxy] if proxy else []
        # Live findings 2026-09-09 (docs/subtitle-audio-leg-design-2026-09-09.md):
        # current yt-dlp ALWAYS delegates is_live HLS to its external ffmpeg
        # downloader (HlsFD.can_download hard-refuses is_live), so the m3u8
        # native flags below only cover yt-dlp's own HTTP and non-live paths.
        # The ffmpeg demuxer is where media bytes actually flow, and it must
        # NOT go through the HTTP proxy: measured here, proxied ffmpeg loses
        # connection reuse ("Cannot reuse HTTP connection for different host")
        # and sags to 0.55x on 1080p60 / 0.8x on 720p60, while direct
        # googlevideo sustains 1.0x even for 1080p60. googlevideo is directly
        # reachable on networks where youtube.com is not. Set
        # LAGLINGO_FFMPEG_PROXY=1 to restore forwarding on networks that need it.
        ffmpeg_proxy_args = (
            ["--downloader-args", f"ffmpeg_i:-http_proxy {proxy}"]
            if proxy and os.environ.get("LAGLINGO_FFMPEG_PROXY") == "1"
            else []
        )
        # ffmpeg HLS input hardening for live: reconnect quickly instead of
        # dying on a dropped socket.
        ffmpeg_live_args = [
            "--downloader-args",
            "ffmpeg_i:-reconnect 1 -reconnect_streamed 1 -reconnect_on_network_error 1 -reconnect_delay_max 2",
        ]
        is_hls = not self.selected_protocol or self.selected_protocol.startswith("m3u8")
        hls_args = [
            "--hls-use-mpegts",
            # NOTE (2026-09-09 live-verified): for is_live formats yt-dlp
            # delegates to the external ffmpeg downloader regardless of this
            # flag, so --concurrent-fragments does NOT apply to live HLS.
            # These flags still cover yt-dlp's own HTTP (manifest refresh,
            # extraction retries) and any non-live HLS fallback.
            "--downloader",
            "m3u8:native",
            "--fragment-retries",
            "infinite",
            # Live is a race against a rolling playlist window: the old
            # exp=1:20 backoff turned a ~10s network blip into a measured 74s
            # outage (docs/subtitle-dropout-rootcause-2026-09-08.md). Retry
            # every second, so a short blip costs seconds, not a minute.
            "--retry-sleep",
            "fragment:1",
            "--retries",
            "infinite",
            "--retry-sleep",
            "http:1",
            "--concurrent-fragments",
            "4",
        ] if is_hls else []
        return [
            self.yt_dlp,
            *proxy_args,
            "--no-config",
            "--no-playlist",
            "--no-live-from-start",
            *hls_args,
            *ffmpeg_proxy_args,
            *ffmpeg_live_args,
            "--no-progress",
            *self.auth_args,
            "-f",
            selector or self.format_selector,
            "-o",
            "-",
            *(["--load-info-json", self.info_json_path] if self.info_json_path else [self.page_url]),
        ]

    @staticmethod
    def _environment_proxy() -> str | None:
        for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
            value = os.environ.get(name, "").strip()
            if value:
                return value
        return None

    def input_urls(self) -> list[str]:
        """Local TCP endpoints (video leg first) for the packaging ffmpeg."""
        return [pump.url for pump in self.pumps]

    def start(self) -> None:
        if any(process.poll() is None for process in self.processes):
            raise RuntimeError("yt-dlp live ingest is already running")
        self._stop_requested = False
        self.error = None
        self.log_tail.clear()
        self.started_at = time.monotonic()
        self.last_output_at = self.started_at
        self._legs_past_extraction = set()
        self.pumps = [
            _TcpPump(
                label,
                on_first_byte=self._leg_reached_download(index),
                pts_probe=MpegTsPtsProbe(want_audio=label == "audio"),
                on_pts=self._record_pts(index),
            )
            for index, label in enumerate(("video", "audio")[: len(self.selectors)])
        ]
        for index, (selector, pump) in enumerate(zip(self.selectors, self.pumps)):
            process = subprocess.Popen(
                self.command(selector),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
            )
            self.processes.append(process)
            log_thread = threading.Thread(
                target=self._read_log,
                args=(process, index),
                name=f"yt-dlp-live-log-{index}",
                daemon=True,
            )
            self._log_threads.append(log_thread)
            log_thread.start()
            pump.start(process.stdout)

    def _record_pts(self, index: int) -> Callable[[list[float]], None]:
        while len(self.source_pts) <= index:
            self.source_pts.append([])

        def record(points: list[float]) -> None:
            # Keep only a bounded diagnostic history; the media bytes remain
            # untouched and the hot path does not allocate per TS packet.
            target = self.source_pts[index]
            target.extend(points)
            if len(target) > 128:
                del target[:-128]

        return record

    def stop(self) -> None:
        self._stop_requested = True
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
        for process in self.processes:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

        # Let process exit/EOF release the stdout and stderr readers before
        # closing their BufferedReader objects. The previous order closed the
        # streams first while the pump/log threads were inside read(), producing
        # the observed ValueError/PyMemoryView_FromBuffer shutdown exceptions.
        for pump in self.pumps:
            pump.stop()
        for thread in self._log_threads:
            thread.join(timeout=3)

        for process in self.processes:
            for stream in (process.stdout, process.stderr):
                if stream:
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        pass

        # A malformed/fake process may not signal EOF after wait(). Closing the
        # streams above is the final unblock; the readers now recognize it as a
        # claimed shutdown instead of leaking an exception from daemon threads.
        for pump in self.pumps:
            pump.stop()
        for thread in self._log_threads:
            thread.join(timeout=1)
        self.processes = []
        self.pumps = []
        self._log_threads = []
        self._cleanup_auth()

    def snapshot(self) -> dict[str, Any]:
        running = any(process.poll() is None for process in self.processes)
        now = time.monotonic()
        legs: list[dict[str, Any]] = []
        previous_marks = self._last_leg_marks
        current_bytes = [pump.forwarded_bytes for pump in self.pumps]
        for index, pump in enumerate(self.pumps):
            rate: float | None = None
            if previous_marks and index < len(previous_marks[1]):
                elapsed = now - previous_marks[0]
                if elapsed > 0:
                    rate = (current_bytes[index] - previous_marks[1][index]) / elapsed
            legs.append(
                {
                    "label": pump.label,
                    "forwardedBytes": current_bytes[index],
                    "bytesPerSecond": round(rate, 1) if rate is not None else None,
                }
            )
        # Only advance the baseline when the clock ticked; a zero-elapsed
        # snapshot keeps the older baseline so the next poll still yields a
        # rate instead of dividing by zero.
        if previous_marks is None or now > previous_marks[0]:
            self._last_leg_marks = (now, current_bytes)
        return {
            "downloader": "yt-dlp",
            "ytDlpVersion": self._version_label(),
            "formatSelector": self.format_selector,
            "legs": len(self.selectors),
            "running": running,
            "sourceIdleSeconds": round(time.monotonic() - self.last_output_at, 1) if running and self.last_output_at else None,
            "sourceError": self.error,
            "legThroughput": legs,
            # Last yt-dlp stderr lines (URLs already redacted) so a silently
            # falling-behind download is diagnosable from /api/status without
            # shell access to the companion host.
            "logTail": list(self.log_tail)[-8:],
        }

    def _leg_reached_download(self, index: int) -> Callable[[], None]:
        def mark() -> None:
            with self._lock:
                self._legs_past_extraction.add(index)
                # The restricted cookie and probe-info files must outlive
                # extraction of every leg, so only clean up once all legs are
                # past it (or have exited).
                if len(self._legs_past_extraction) >= len(self.selectors):
                    self._cleanup_auth()

        return mark

    def _read_log(self, process: subprocess.Popen[bytes], index: int) -> None:
        if not process.stderr:
            return
        try:
            for raw_line in process.stderr:
                clean = raw_line.decode("utf-8", "replace").strip()
                if clean:
                    self.log_tail.append(self._redact(clean))
                self.last_output_at = time.monotonic()
        except (OSError, ValueError) as exc:
            if not self._stop_requested and process.poll() is None and not self.error:
                self.error = f"yt-dlp log pipe failed: {type(exc).__name__}: {exc}"
        code = process.poll()
        produced_media = index < len(self.pumps) and self.pumps[index].forwarded_chunks > 0
        # A leg that exited can no longer need its credentials. Note this is a
        # backstop only: yt-dlp block-buffers its own stderr on a pipe, so the
        # log never times the credential teardown -- forwarded bytes do.
        self._leg_reached_download(index)()
        if code is not None and not self._stop_requested and not self.error:
            detail = self.log_tail[-1] if self.log_tail else "no yt-dlp diagnostic"
            if code != 0:
                self.error = f"yt-dlp live download exited with code {code}: {detail}"
            elif not produced_media:
                self.error = f"yt-dlp live download exited before producing media: {detail}"

    def _cleanup_auth(self) -> None:
        if self._auth_cleaned or not self.auth_cleanup:
            return
        self._auth_cleaned = True
        self.auth_cleanup()

    def _version_label(self) -> str:
        name = Path(self.yt_dlp).name
        return "vendored-2026.08.19" if Path(self.yt_dlp).resolve() == VENDORED_YT_DLP.resolve() else name

    @staticmethod
    def _redact(line: str) -> str:
        for marker in ("https://", "http://"):
            start = line.find(marker)
            if start >= 0:
                end = line.find(" ", start)
                line = line[:start] + "<REDACTED_URL>" + (line[end:] if end >= 0 else "")
        return line
