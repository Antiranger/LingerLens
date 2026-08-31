"""Local HLS reverse proxy for source playlists that rotate CDN hosts."""

from __future__ import annotations

import asyncio
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SourceSegment:
    sequence: int
    duration: float
    url: str
    init_url: str | None


def parse_media_playlist(text: str, playlist_url: str) -> tuple[list[SourceSegment], float]:
    media_sequence = 0
    target_duration = 2.0
    duration: float | None = None
    init_url: str | None = None
    segments: list[SourceSegment] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            media_sequence = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            target_duration = max(0.5, float(line.split(":", 1)[1]))
        elif line.startswith("#EXT-X-MAP:"):
            attributes = line.split(":", 1)[1]
            for item in attributes.split(","):
                key, _, value = item.partition("=")
                if key.strip() == "URI":
                    init_url = urllib.parse.urljoin(playlist_url, value.strip().strip('"'))
        elif line.startswith("#EXTINF:"):
            duration = float(line.split(":", 1)[1].split(",", 1)[0])
        elif line and not line.startswith("#") and duration is not None:
            segments.append(
                SourceSegment(
                    sequence=media_sequence + len(segments),
                    duration=duration,
                    url=urllib.parse.urljoin(playlist_url, line),
                    init_url=init_url,
                )
            )
            duration = None
    return segments, target_duration


class SourceHlsProxy:
    """Expose one remote media playlist through stable localhost URLs."""

    def __init__(self, playlist_url: str, headers: dict[str, str], source_delay_seconds: float = 5.0):
        self.playlist_url = playlist_url
        self.source_delay_seconds = source_delay_seconds
        self.headers = {
            str(key): str(value)
            for key, value in headers.items()
            if key.title() in {"User-Agent", "Referer", "Origin"}
            and "\r" not in str(value)
            and "\n" not in str(value)
        }
        self.segments: dict[int, SourceSegment] = {}
        self.latest_init_url: str | None = None
        self.playlist_fetches = 0
        self.segment_fetches = 0
        self.bytes_sent = 0
        self.last_segment_at: float | None = None
        self.error: str | None = None
        self.cache: dict[int, bytes] = {}
        self.prefetch_tasks: dict[int, asyncio.Task[bytes]] = {}
        self.advertised: deque[SourceSegment] = deque()
        self.next_advertise_sequence: int | None = None
        self._lock = asyncio.Lock()

    async def local_playlist(self, base_path: str) -> str:
        async with self._lock:
            text = await asyncio.to_thread(self._fetch_text, self.playlist_url)
            segments, target_duration = parse_media_playlist(text, self.playlist_url)
            segments = self._tail_by_duration(segments, 35.0)
            self.playlist_fetches += 1
            for segment in segments:
                self.segments[segment.sequence] = segment
                if segment.init_url:
                    self.latest_init_url = segment.init_url
            if segments:
                eligible = self._eligible_segments(segments)
                self._schedule_prefetch(eligible[-10:])
                advertised_floor = self.advertised[0].sequence if self.advertised else segments[0].sequence
                minimum = min(advertised_floor, segments[0].sequence - 8)
                self.segments = {sequence: item for sequence, item in self.segments.items() if sequence >= minimum}
                self.cache = {sequence: data for sequence, data in self.cache.items() if sequence >= minimum}
                for sequence, task in list(self.prefetch_tasks.items()):
                    if sequence < minimum:
                        task.cancel()
                        self.prefetch_tasks.pop(sequence, None)
        # Give the parallel downloads a short head start, then advertise only a
        # contiguous run whose bytes are already local. FFmpeg must never see a
        # remote segment that may later fail or arrive slower than real time.
        eligible = self._eligible_segments(segments)
        attempts = 40 if not self.advertised else 1
        for _ in range(attempts):
            self._advance_advertised(eligible)
            if self.advertised:
                break
            await asyncio.sleep(0.25)
        return self._build_local_playlist(list(self.advertised), target_duration, base_path)

    async def segment_bytes(self, sequence: int) -> bytes:
        cached = self.cache.get(sequence)
        if cached is not None:
            return cached
        task = self.prefetch_tasks.get(sequence)
        if task:
            try:
                return await task
            except Exception:
                self.prefetch_tasks.pop(sequence, None)
        self.error = f"Advertised source segment {sequence} is missing from the local cache"
        raise RuntimeError(self.error)

    def _schedule_prefetch(self, segments: list[SourceSegment]) -> None:
        for segment in segments:
            if segment.sequence in self.cache or segment.sequence in self.prefetch_tasks:
                continue
            task = asyncio.create_task(self._download_once(segment))
            task.add_done_callback(self._consume_task_exception)
            self.prefetch_tasks[segment.sequence] = task
            task.add_done_callback(lambda _task, sequence=segment.sequence: self.prefetch_tasks.pop(sequence, None))

    @staticmethod
    def _consume_task_exception(task: asyncio.Task[bytes]) -> None:
        if not task.cancelled():
            task.exception()

    async def _download_once(self, segment: SourceSegment) -> bytes:
        data = await asyncio.to_thread(self._fetch_bytes, segment.url)
        self.cache[segment.sequence] = data
        self.segment_fetches += 1
        self.bytes_sent += len(data)
        self.last_segment_at = asyncio.get_running_loop().time()
        return data

    async def init_bytes(self) -> bytes:
        if not self.latest_init_url:
            await self.local_playlist("")
        if not self.latest_init_url:
            raise RuntimeError("Source playlist has no initialization segment")
        return await asyncio.to_thread(self._fetch_bytes, self.latest_init_url)

    def snapshot(self) -> dict[str, Any]:
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            now = None
        return {
            "sourcePlaylistFetches": self.playlist_fetches,
            "sourceSegmentsFetched": self.segment_fetches,
            "sourceBytesFetched": self.bytes_sent,
            "sourceIdleSeconds": round(now - self.last_segment_at, 1) if now is not None and self.last_segment_at else None,
            "sourceError": self.error,
        }

    @staticmethod
    def _tail_by_duration(segments: list[SourceSegment], seconds: float) -> list[SourceSegment]:
        duration = 0.0
        start = len(segments)
        for index in range(len(segments) - 1, -1, -1):
            duration += segments[index].duration
            start = index
            if duration >= seconds:
                break
        return segments[start:]

    def _eligible_segments(self, segments: list[SourceSegment]) -> list[SourceSegment]:
        if self.source_delay_seconds <= 0:
            return segments
        hidden = 0.0
        cutoff = len(segments)
        for index in range(len(segments) - 1, -1, -1):
            hidden += segments[index].duration
            if hidden >= self.source_delay_seconds:
                cutoff = index
                break
        return segments[:cutoff]

    def _advance_advertised(self, eligible: list[SourceSegment]) -> None:
        by_sequence = {segment.sequence: segment for segment in eligible}
        if self.next_advertise_sequence is None:
            ready_sequences = [segment.sequence for segment in eligible if segment.sequence in self.cache]
            if not ready_sequences:
                return
            self.next_advertise_sequence = min(ready_sequences)
        while self.next_advertise_sequence in self.cache and self.next_advertise_sequence in by_sequence:
            segment = by_sequence[self.next_advertise_sequence]
            if not self.advertised or segment.sequence > self.advertised[-1].sequence:
                self.advertised.append(segment)
            self.next_advertise_sequence += 1
        duration = sum(segment.duration for segment in self.advertised)
        while len(self.advertised) > 3 and duration - self.advertised[0].duration >= 30.0:
            duration -= self.advertised[0].duration
            self.advertised.popleft()

    @staticmethod
    def _build_local_playlist(segments: list[SourceSegment], target_duration: float, base_path: str) -> str:
        media_sequence = segments[0].sequence if segments else 0
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            f"#EXT-X-TARGETDURATION:{max(1, int(target_duration + 0.999))}",
            f"#EXT-X-MEDIA-SEQUENCE:{media_sequence}",
        ]
        if segments and segments[0].init_url:
            lines.append(f'#EXT-X-MAP:URI="{base_path}/init.mp4"')
        for segment in segments:
            lines.extend([f"#EXTINF:{segment.duration:.6f},", f"{base_path}/segment/{segment.sequence}.ts"])
        return "\n".join(lines) + "\n"

    def _request(self, url: str) -> urllib.request.Request:
        return urllib.request.Request(url, headers={**self.headers, "Connection": "close"})

    def _fetch_text(self, url: str) -> str:
        with urllib.request.urlopen(self._request(url), timeout=15) as response:
            return response.read().decode("utf-8", "replace")

    def _fetch_bytes(self, url: str) -> bytes:
        with urllib.request.urlopen(self._request(url), timeout=20) as response:
            return response.read()
