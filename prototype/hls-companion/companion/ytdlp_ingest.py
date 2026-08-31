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

ROOT = Path(__file__).resolve().parents[1]
VENDORED_YT_DLP = ROOT / "vendor" / "yt-dlp" / "yt-dlp.exe"


class _TcpPump:
    """Accept localhost TCP connections and stream proc.stdout bytes into them.

    The pump deliberately does not read stdout until a client (the packaging
    ffmpeg) connects; OS pipe backpressure then naturally throttles yt-dlp.
    A byte subscriber (tee) is installed at construction time so it observes
    the very first forwarded byte (redesign Fix A): the moment the packaging
    ffmpeg connects is the shared byte origin of both legs.
    """

    def __init__(
        self,
        label: str,
        tee: Callable[[bytes], None] | None = None,
        on_first_byte: Callable[[], None] | None = None,
    ):
        self.label = label
        # Fires once, when this leg has produced real media. That is the only
        # log-independent proof that yt-dlp is past extraction and no longer
        # needs the short-lived credential files.
        self._on_first_byte = on_first_byte
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(0.5)
        self.port = self.listener.getsockname()[1]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._source: Any = None
        self._tee: Callable[[bytes], None] | None = tee
        self.tee_dropped = 0
        self.forwarded_chunks = 0

    def set_tee(self, sink: Callable[[bytes], None] | None) -> None:
        """Attach a best-effort byte subscriber without affecting playback."""
        self._tee = sink

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
                    chunk = self._source.read(65536)
                    if not chunk:
                        return
                    conn.sendall(chunk)
                    self.forwarded_chunks += 1
                    if self.forwarded_chunks == 1 and self._on_first_byte:
                        try:
                            self._on_first_byte()
                        except Exception:
                            pass
                    sink = self._tee
                    if sink is not None:
                        try:
                            sink(chunk)
                        except Exception:
                            # Subtitle work is strictly lower priority than the
                            # packaging path. A broken subscriber is isolated.
                            self.tee_dropped += 1
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
        audio_tee: Callable[[bytes], None] | None = None,
        info_json_path: str | None = None,
    ):
        self.page_url = page_url
        # When the caller can hand over a fresh probe result, yt-dlp loads it
        # instead of re-running the whole YouTube extraction it already paid
        # for during /api/probe (measured: ~8s off the critical path per leg).
        self.info_json_path = info_json_path
        self.format_selector = format_selector
        self.selectors = [part for part in format_selector.split("+") if part] or [format_selector]
        self.auth_args = list(auth_args)
        self.yt_dlp = yt_dlp or self._default_executable()
        self.auth_cleanup = auth_cleanup
        # Installed on the audio pump at construction so the subtitle leg sees
        # byte 0 of the stream (redesign Fix A). attach_audio_tee remains for
        # tests and late subscribers; construction-time is the correct path.
        self.audio_tee = audio_tee
        self._auth_cleaned = False
        self._legs_past_extraction: set[int] = set()
        self.processes: list[subprocess.Popen[bytes]] = []
        self.pumps: list[_TcpPump] = []
        self.started_at: float | None = None
        self.last_output_at: float | None = None
        self.error: str | None = None
        self.log_tail: deque[str] = deque(maxlen=30)
        self._stop_requested = False
        self._lock = threading.Lock()

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
        # Native m3u8 downloads obey yt-dlp's --proxy. Some live formats can
        # still fall back to yt-dlp's external FFmpeg downloader; FFmpeg does
        # not honor ALL_PROXY on Windows, so forward the same proxy explicitly.
        ffmpeg_proxy_args = (
            ["--downloader-args", f"ffmpeg_i:-http_proxy {proxy}"] if proxy else []
        )
        return [
            self.yt_dlp,
            *proxy_args,
            "--no-config",
            "--no-playlist",
            "--no-live-from-start",
            "--hls-use-mpegts",
            # yt-dlp would otherwise hand live HLS to its internal ffmpeg, whose
            # HLS demuxer fetches one segment at a time on one connection --
            # and --concurrent-fragments is silently ignored on that path.
            # Measured on this link: a single connection to googlevideo tops out
            # near 3.2 Mbps regardless of link capacity (~24 Mbps aggregate), so
            # a 5.4 Mbps 1080p rendition can never keep up with the live edge and
            # the player's buffer drains until it stalls. The native downloader
            # honours --concurrent-fragments, which lifts the ceiling roughly in
            # proportion to the fragment concurrency below.
            "--downloader",
            "m3u8:native",
            "--fragment-retries",
            "infinite",
            "--retry-sleep",
            "fragment:exp=1:20",
            "--retries",
            "infinite",
            "--retry-sleep",
            "http:exp=1:20",
            "--concurrent-fragments",
            "4",
            *ffmpeg_proxy_args,
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

    def attach_audio_tee(self, sink: Callable[[bytes], None]) -> None:
        """Tee the audio leg (or the sole muxed leg) to a non-blocking sink."""
        if not self.pumps:
            raise RuntimeError("yt-dlp live ingest is not running")
        self._audio_pump().set_tee(sink)

    def detach_audio_tee(self) -> None:
        if self.pumps:
            self._audio_pump().set_tee(None)

    def tee_snapshot(self) -> dict[str, int]:
        pump = self._audio_pump() if self.pumps else None
        return {"teeDropped": pump.tee_dropped if pump else 0}

    def _audio_pump(self) -> _TcpPump:
        return self.pumps[1] if len(self.pumps) > 1 else self.pumps[0]

    def start(self) -> None:
        if any(process.poll() is None for process in self.processes):
            raise RuntimeError("yt-dlp live ingest is already running")
        self._stop_requested = False
        self.error = None
        self.log_tail.clear()
        self.started_at = time.monotonic()
        self.last_output_at = self.started_at
        self._legs_past_extraction = set()
        audio_index = len(self.selectors) - 1  # audio leg, or the sole muxed leg
        self.pumps = [
            _TcpPump(
                label,
                tee=self.audio_tee if index == audio_index else None,
                on_first_byte=self._leg_reached_download(index),
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
            threading.Thread(target=self._read_log, args=(process, index), name="yt-dlp-live-log", daemon=True).start()
            pump.start(process.stdout)

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
            for stream in (process.stdout, process.stderr):
                if stream:
                    try:
                        stream.close()
                    except OSError:
                        pass
        self.processes = []
        for pump in self.pumps:
            pump.stop()
        self.pumps = []
        self._cleanup_auth()

    def snapshot(self) -> dict[str, Any]:
        running = any(process.poll() is None for process in self.processes)
        return {
            "downloader": "yt-dlp",
            "ytDlpVersion": self._version_label(),
            "formatSelector": self.format_selector,
            "legs": len(self.selectors),
            "running": running,
            "sourceIdleSeconds": round(time.monotonic() - self.last_output_at, 1) if running and self.last_output_at else None,
            "sourceError": self.error,
            **self.tee_snapshot(),
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
        for raw_line in process.stderr:
            clean = raw_line.decode("utf-8", "replace").strip()
            if clean:
                self.log_tail.append(self._redact(clean))
            self.last_output_at = time.monotonic()
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
