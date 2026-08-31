"""Mature Streamlink-backed HLS ingest instead of a custom playlist state machine."""

from __future__ import annotations

import threading
import time
from typing import Any, BinaryIO

from streamlink import Streamlink  # type: ignore[import-not-found]
from streamlink.stream.hls import HLSStream  # type: ignore[import-not-found]


class StreamlinkHlsIngest:
    def __init__(self, playlist_url: str, headers: dict[str, str], live_edge_segments: int = 5):
        self.playlist_url = playlist_url
        self.headers = {
            str(key): str(value)
            for key, value in headers.items()
            if key.title() in {"User-Agent", "Referer", "Origin"}
            and "\r" not in str(value)
            and "\n" not in str(value)
        }
        self.live_edge_segments = live_edge_segments
        self.bytes_relayed = 0
        self.started_at: float | None = None
        self.last_bytes_at: float | None = None
        self.error: str | None = None
        self._stream: BinaryIO | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self, destination: BinaryIO) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(destination,), name="streamlink-hls-ingest", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._stream:
            try:
                self._stream.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=5)

    def snapshot(self) -> dict[str, Any]:
        return {
            "sourceDelaySegments": self.live_edge_segments,
            "sourceBytesFetched": self.bytes_relayed,
            "sourceIdleSeconds": round(time.monotonic() - self.last_bytes_at, 1) if self.last_bytes_at else None,
            "sourceError": self.error,
        }

    def _run(self, destination: BinaryIO) -> None:
        session = Streamlink()
        session.set_option("hls-live-edge", self.live_edge_segments)
        session.set_option("stream-segment-threads", 6)
        session.set_option("stream-segment-attempts", 5)
        session.set_option("stream-segment-timeout", 15.0)
        session.set_option("hls-playlist-reload-attempts", 10)
        session.set_option("stream-timeout", 30.0)
        session.set_option("ringbuffer-size", 64 * 1024 * 1024)
        session.set_option("hls-segment-stream-data", True)
        session.http.headers.update(self.headers)
        self.started_at = time.monotonic()
        try:
            stream = HLSStream(session, self.playlist_url).open()
            self._stream = stream
            while not self._stop.is_set():
                data = stream.read(64 * 1024)
                if not data:
                    raise RuntimeError("Streamlink source ended unexpectedly")
                destination.write(data)
                destination.flush()
                self.bytes_relayed += len(data)
                self.last_bytes_at = time.monotonic()
        except (BrokenPipeError, OSError) as error:
            if not self._stop.is_set():
                self.error = f"Streamlink ingest pipe failed: {error}"
        except Exception as error:
            self.error = f"Streamlink HLS ingest failed: {type(error).__name__}: {error}"
        finally:
            if self._stream:
                try:
                    self._stream.close()
                except OSError:
                    pass
            try:
                destination.close()
            except OSError:
                pass
