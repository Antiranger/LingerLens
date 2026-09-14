"""Twitch anonymous IRC WebSocket live chat source."""

from __future__ import annotations

import asyncio
import contextlib
import random
import re
import time
from urllib.request import getproxies
from typing import Any, Callable

import aiohttp

from .capture_clock import CaptureClock
from .live_messages import LiveMessage, LiveMessageStore

TWITCH_IRC_WS_URL = "wss://irc-ws.chat.twitch.tv:443"


def parse_twitch_channel(url: str) -> str:
    """Extract lowercase channel login from Twitch URL."""
    clean = url.split("?")[0].rstrip("/")
    parts = [p for p in clean.split("/") if p]
    if not parts:
        return ""
    channel = parts[-1].lower()
    return re.sub(r"[^a-z0-9_]", "", channel)


def parse_twitch_irc_line(line: str) -> dict[str, Any] | None:
    """Parse IRC line into structured message if PRIVMSG."""
    if not line or "PRIVMSG" not in line:
        return None
    tags: dict[str, str] = {}
    remaining = line
    if remaining.startswith("@"):
        tag_str, remaining = remaining[1:].split(" ", 1)
        for item in tag_str.split(";"):
            if "=" in item:
                k, v = item.split("=", 1)
                tags[k] = v
            else:
                tags[item] = ""

    # Parse prefix and command
    match = re.match(r"^:([^! ]+)!?([^ ]*)\s+PRIVMSG\s+#[^ ]+\s+:(.*)$", remaining)
    if not match:
        return None
    
    sender_nick = match.group(1)
    text = match.group(3).strip()
    if not text:
        return None

    display_name = tags.get("display-name") or sender_nick
    msg_id = tags.get("id") or f"twitch-{time.time()}-{random.randint(1000, 9999)}"
    
    raw_sent = tags.get("tmi-sent-ts")
    try:
        sent_at = float(raw_sent) / 1000.0 if raw_sent else None
    except (TypeError, ValueError):
        sent_at = None

    badges = [b.split("/")[0] for b in (tags.get("badges") or "").split(",") if b]

    return {
        "sourceId": str(msg_id),
        "text": text,
        "author": {
            "id": tags.get("user-id") or sender_nick,
            "name": display_name,
            "badges": badges,
        },
        "platformSentAt": sent_at,
        "offsetMs": None,
    }


class TwitchChatIngest:
    """Streams live chat messages from Twitch via anonymous IRC WebSocket."""

    def __init__(
        self,
        url: str,
        store: LiveMessageStore,
        *,
        clock: CaptureClock | None = None,
        auth_lease: Any | None = None,
        on_message: Callable[[LiveMessage], None] | None = None,
        proxy: str | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.url = url
        self.channel = parse_twitch_channel(url)
        self.store = store
        self.clock = clock
        self.auth_lease = auth_lease
        self.on_message = on_message
        proxies = getproxies()
        self.proxy = proxy or proxies.get("https") or proxies.get("http") or proxies.get("all")
        self.on_error = on_error
        self.received = 0
        self.reconnects = 0
        self.last_error: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._state = "idle"

    async def start(self) -> None:
        if self._task is None and self.channel:
            self._stop_event.clear()
            self._state = "connecting"
            self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._state = "idle"

    stop = close

    def status(self) -> dict[str, Any]:
        return {
            "state": self._state,
            "platform": "twitch",
            "connected": self._state == "running",
            "channel": self.channel,
            "received": self.received,
            "reconnects": self.reconnects,
            "lastError": self.last_error,
        }

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stop_event.is_set():
            connected_at = None
            try:
                connector = aiohttp.TCPConnector(enable_cleanup_closed=True)
                async with aiohttp.ClientSession(connector=connector) as session:
                    ws_kw: dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=10)}
                    if self.proxy:
                        ws_kw["proxy"] = self.proxy
                    async with session.ws_connect(TWITCH_IRC_WS_URL, **ws_kw) as ws:
                        nick = f"justinfan{random.randint(10000, 99999)}"
                        await ws.send_str("CAP REQ :twitch.tv/tags twitch.tv/commands\r\n")
                        await ws.send_str("PASS SCHMOOPIE\r\n")
                        await ws.send_str(f"NICK {nick}\r\n")
                        await ws.send_str(f"JOIN #{self.channel}\r\n")
                        connected_at = time.monotonic()

                        async for msg in ws:
                            if self._stop_event.is_set():
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                for line in msg.data.splitlines():
                                    if " 366 " in line:
                                        self._state = "running"
                                        self.last_error = None
                                        if self.on_error:
                                            self.on_error("")
                                    if line.startswith("PING"):
                                        await ws.send_str("PONG :tmi.twitch.tv\r\n")
                                        continue
                                    parsed = parse_twitch_irc_line(line)
                                    if parsed:
                                        self._state = "running"
                                        self.last_error = None
                                        if self.on_error:
                                            self.on_error("")
                                        msg_obj, accepted = self.store.add_with_result(
                                            source_id=parsed["sourceId"], platform="twitch",
                                            text=parsed["text"], author=parsed["author"],
                                            received_at=time.time(), received_monotonic=time.monotonic(),
                                            platform_sent_at=parsed["platformSentAt"],
                                        )
                                        if accepted:
                                            self.received += 1
                                            if self.on_message:
                                                self.on_message(msg_obj)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.last_error = str(exc)
                if self.on_error:
                    self.on_error(self.last_error)
            if self._stop_event.is_set():
                break
            if connected_at is not None and time.monotonic() - connected_at >= 30:
                backoff = 1.0
            self.reconnects += 1
            self._state = "reconnecting"
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 10.0)
