"""YouTube live-chat sidecar using the vendored yt-dlp live_chat subtitle."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from .auth_lease import SessionAuthLease
from .capture_clock import CaptureClock
from .core import VENDORED_YT_DLP
from .live_messages import LiveMessage, LiveMessageStore


def _text(value: Any) -> str:
    if isinstance(value, dict):
        if isinstance(value.get("simpleText"), str):
            return value["simpleText"]
        runs = value.get("runs")
        if isinstance(runs, list):
            return "".join(str(run.get("text") or "") for run in runs if isinstance(run, dict))
    return str(value or "")


def iter_youtube_chat_items(value: str | bytes | dict[str, Any] | list[Any]) -> Iterable[dict[str, Any]]:
    """Yield chat action objects from JSONL, fragment JSON, or initial HTML data."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        # Extract ytInitialData if embedded in HTML fragment
        if "window[\"ytInitialData\"]" in value or "ytInitialData =" in value:
            m = re.search(r'ytInitialData\s*=\s*(\{.*?\});\s*</script>', value, re.DOTALL)
            if m:
                try:
                    parsed_init = json.loads(m.group(1))
                    yield from iter_youtube_chat_items(parsed_init)
                except Exception:
                    pass
        for line in value.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            yield from iter_youtube_chat_items(parsed)
        return
    if isinstance(value, list):
        for item in value:
            yield from iter_youtube_chat_items(item)
        return
    if not isinstance(value, dict):
        return

    # 1. Replay action wrapper (VOD / recorded chat)
    replay = value.get("replayChatItemAction")
    if isinstance(replay, dict):
        yield replay
        return

    # 2. Live action wrapper (Ongoing live stream continuation)
    if "addChatItemAction" in value and "replayChatItemAction" not in value:
        yield {"actions": [value]}
        return
    if "liveChatContinuation" in value:
        for act in value.get("liveChatContinuation", {}).get("actions", []) or []:
            if isinstance(act, dict):
                yield {"actions": [act]} if "actions" not in act else act
        return
    if "continuationContents" in value:
        actions = value.get("continuationContents", {}).get("liveChatContinuation", {}).get("actions", []) or []
        for act in actions:
            if isinstance(act, dict):
                yield {"actions": [act]} if "actions" not in act else act
        return

    for child in value.values():
        if isinstance(child, (dict, list)):
            yield from iter_youtube_chat_items(child)


def iter_parsed_youtube_chat_items(action: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Iterate over all messages in a chat action without dropping subsequent ones."""
    offset_raw = action.get("videoOffsetTimeMsec")
    try:
        offset_ms = float(offset_raw) if offset_raw is not None else None
    except (TypeError, ValueError):
        offset_ms = None
    actions = action.get("actions") or []
    for wrapper in actions:
        if not isinstance(wrapper, dict):
            continue
        item = wrapper.get("addChatItemAction", {}).get("item", {}) if isinstance(wrapper, dict) else {}
        renderer = item.get("liveChatTextMessageRenderer") if isinstance(item, dict) else None
        if not isinstance(renderer, dict):
            continue
        message_id = renderer.get("id")
        text = _text(renderer.get("message")).strip()
        if not message_id or not text:
            continue
        sent_raw = renderer.get("timestampUsec")
        try:
            sent_at = float(sent_raw) / 1_000_000 if sent_raw is not None else None
        except (TypeError, ValueError):
            sent_at = None
        badges = []
        for badge in renderer.get("authorBadges", []) or []:
            tooltip = badge.get("liveChatAuthorBadgeRenderer", {}).get("tooltip") if isinstance(badge, dict) else None
            if tooltip:
                badges.append(str(tooltip))
        yield {
            "sourceId": str(message_id),
            "text": text,
            "author": {
                "id": renderer.get("authorExternalChannelId"),
                "name": _text(renderer.get("authorName")) or "Anonymous",
                "badges": badges,
            },
            "platformSentAt": sent_at,
            "offsetMs": offset_ms,
        }


def parse_youtube_chat_item(action: dict[str, Any]) -> dict[str, Any] | None:
    """Backward compatibility helper returning the first parsed message."""
    for item in iter_parsed_youtube_chat_items(action):
        return item
    return None


class YouTubeChatIngest:
    def __init__(
        self,
        url: str,
        store: LiveMessageStore,
        *,
        clock: CaptureClock | None = None,
        auth_lease: SessionAuthLease | None = None,
        on_message: Callable[[LiveMessage], None] | None = None,
        yt_dlp_path: str | None = None,
        proxy: str | None = None,
        reconnect_base: float = 1.0,
        reconnect_max: float = 20.0,
    ) -> None:
        self.url = url
        self.store = store
        self.clock = clock
        self.auth_lease = auth_lease
        self.on_message = on_message
        self.yt_dlp_path = yt_dlp_path or (str(VENDORED_YT_DLP) if VENDORED_YT_DLP.is_file() else shutil.which("yt-dlp") or "yt-dlp")
        self.proxy = proxy
        self.reconnect_base = reconnect_base
        self.reconnect_max = reconnect_max
        self.source_started_monotonic: float | None = None
        self.source_started_media_time: float | None = None
        self.source_started_wall_time: float | None = None
        self._running = False
        self._task: asyncio.Task | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._state = "idle"
        self._last_error: str | None = None
        self._received = 0
        self._reconnects = 0

    def command(self, output_dir: Path, auth_args: list[str]) -> list[str]:
        return [
            self.yt_dlp_path,
            "--no-config",
            "--no-playlist",
            "--skip-download",
            "--write-subs",
            "--sub-langs",
            "live_chat",
            "--sub-format",
            "json",
            "--paths",
            str(output_dir),
            "-o",
            "chat.%(ext)s",
            *(["--proxy", self.proxy] if self.proxy else []),
            "--no-progress",
            *auth_args,
            self.url,
        ]

    async def start(self, url: str | None = None) -> None:
        if url:
            self.url = url
        if self._running:
            return
        self._running = True
        self._state = "connecting"
        self._task = asyncio.create_task(self._run(), name="youtube-live-chat")

    async def stop(self) -> None:
        self._running = False
        task, self._task = self._task, None
        if task:
            task.cancel()
        if task:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._state = "idle"

    async def _run(self) -> None:
        delay = self.reconnect_base
        while self._running:
            consumer = None
            try:
                consumer = self.auth_lease.acquire("youtube_chat") if self.auth_lease else None
                with tempfile.TemporaryDirectory(prefix="laglingo-live-chat-") as raw_dir:
                    directory = Path(raw_dir)
                    self.source_started_monotonic = time.monotonic()
                    self.source_started_media_time = self.clock.capture_wall_time(self.source_started_monotonic) if self.clock else None
                    self.source_started_wall_time = time.time()
                    auth_args = consumer.yt_dlp_args() if consumer else []
                    self._process = await self._spawn_process(*self.command(directory, auth_args))
                    self._state = "connecting"
                    try:
                        await self._consume_process(directory)
                        code = await self._process.wait()
                    finally:
                        await self._stop_process()
                    if self._running:
                        self._state = "unavailable" if code == 0 and self._received == 0 else "error"
                        self._last_error = "YouTube live_chat unavailable" if self._state == "unavailable" else f"yt-dlp live_chat exited with code {code}"
            except asyncio.CancelledError:
                break
            except Exception as error:
                self._state = "error"
                self._last_error = f"{type(error).__name__}: {error}"
            finally:
                await self._stop_process()
                if consumer:
                    consumer.release()
            if not self._running:
                break
            self._reconnects += 1
            self._state = "reconnecting"
            await asyncio.sleep(delay)
            delay = min(self.reconnect_max, max(self.reconnect_base, delay * 2))

    async def _spawn_process(self, *command: str) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *command,
            # The desktop companion reserves its own stdin for the Electron
            # stop-control pipe.  yt-dlp never needs interactive input; leave
            # the handle detached so both processes cannot consume the same
            # Windows console/pipe stream.
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

    async def _consume_process(self, directory: Path) -> None:
        offsets: dict[Path, int] = {}
        tails: dict[Path, bytes] = {}
        fragment_versions: dict[Path, tuple[int, int]] = {}
        while self._running and self._process and self._process.returncode is None:
            paths = list(directory.glob("*live_chat*.json*"))
            fragment_versions = {p: version for p, version in fragment_versions.items() if p in paths}
            for path in paths:
                # yt-dlp sleeps for timeoutMs BEFORE appending its processed
                # JSONL. Read completed raw responses during that wait. The
                # store's stable-ID dedupe handles the later JSONL copy.
                if re.search(r"\.json(?:\.part)?-Frag\d+$", path.name):
                    try:
                        stat = path.stat()
                        version = (stat.st_size, stat.st_mtime_ns)
                        if fragment_versions.get(path) == version:
                            continue
                        fragment_versions[path] = version
                        if not 0 < stat.st_size <= 4 * 1024 * 1024:
                            continue
                        data = json.loads(path.read_bytes())
                        content = data.get("continuationContents") if isinstance(data, dict) else None
                        if isinstance(content, dict) and isinstance(content.get("liveChatContinuation"), dict):
                            self.consume_json(data, received_monotonic=time.monotonic(), received_at=time.time())
                    except (OSError, ValueError):
                        pass  # A still-writing response is retried only when it changes.
                    continue
                if not (path.name.endswith(".json") or path.name.endswith(".json.part")):
                    continue
                try:
                    size = path.stat().st_size
                    offset = offsets.get(path, 0)
                    if size < offset:
                        offset = 0
                        tails.pop(path, None)
                    if size > offset:
                        with path.open("rb") as handle:
                            handle.seek(offset)
                            chunk = handle.read(min(size - offset, 1024 * 1024))
                        offsets[path] = offset + len(chunk)
                        buffered = tails.pop(path, b"") + chunk
                        boundary = buffered.rfind(b"\n")
                        if boundary >= 0:
                            self.consume_json(buffered[:boundary], received_monotonic=time.monotonic(), received_at=time.time())
                        tail = buffered[boundary + 1:]
                        if len(tail) > 4 * 1024 * 1024:
                            raise ValueError("live chat JSON line exceeds 4 MiB")
                        tails[path] = tail
                except OSError:
                    continue
            if self._process.stdout:
                try:
                    line = await asyncio.wait_for(self._process.stdout.readline(), timeout=0.2)
                    if line:
                        self.consume_json(line, received_monotonic=time.monotonic(), received_at=time.time())
                except asyncio.TimeoutError:
                    pass
            await asyncio.sleep(0.05)

    def consume_json(self, value: Any, *, received_monotonic: float, received_at: float) -> None:
        items = [item for action in iter_youtube_chat_items(value) for item in iter_parsed_youtube_chat_items(action)]
        stamps = [p["platformSentAt"] for p in items if p["platformSentAt"] is not None]
        newest = max(stamps) if stamps else None
        anchor = self.clock.monotonic_to_media_time(received_monotonic) if self.clock else None
        for parsed in items:
            if not parsed:
                continue
            offset_ms = parsed["offsetMs"]
            if offset_ms is not None and offset_ms < 0:
                continue
            sent_at = parsed["platformSentAt"]
            # timestampUsec is the sender's wall clock, not the local media
            # clock. Live batches can arrive several seconds after this
            # process starts (especially with a delayed player), so a
            # local-start cutoff would discard every legitimate message.
            # If no video offset is available, the bounded batch-relative
            # mapping below only places nearby messages on the media timeline;
            # older replay rows remain pending until the clock can anchor them.
            media_time = None
            if offset_ms is not None and self.source_started_media_time is not None:
                media_time = self.source_started_media_time + offset_ms / 1000.0
            if offset_ms is None and anchor is not None and newest is not None and sent_at is not None:
                # Fix the batch schedule on arrival, anchored to locally acquired
                # media. Only relative sender intervals are used (at most 10s).
                interval = newest - sent_at
                if 0 <= interval <= 10:
                    media_time = anchor - interval
            message, accepted = self.store.add_with_result(
                platform="youtube",
                source_id=parsed["sourceId"],
                author=parsed["author"],
                text=parsed["text"],
                received_monotonic=received_monotonic,
                received_at=received_at,
                platform_sent_at=sent_at,
                media_time=media_time,
                translation_enabled=False,
            )
            if accepted:
                self._state = "running"
                self._last_error = None
                self._received += 1
                if self.on_message:
                    self.on_message(message)

    async def _stop_process(self) -> None:
        process, self._process = self._process, None
        if process and process.returncode is None:
            if os.name == "nt":
                # The bundled executable can spawn a child that inherits stdout.
                # Kill only this owned process tree so its pipe reaches EOF.
                await asyncio.to_thread(subprocess.run,
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()

    def status(self) -> dict[str, Any]:
        return {
            "state": self._state,
            "platform": "youtube",
            "running": self._running,
            "connected": self._state == "running",
            "received": self._received,
            "reconnects": self._reconnects,
            "lastError": self._last_error,
        }


YouTubeLiveChatIngest = YouTubeChatIngest
