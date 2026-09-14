"""Bilibili ongoing-live DANMU_MSG source."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncio
import contextlib
import json
import re
import struct
import time
import zlib
from typing import Any, Callable

import aiohttp

from .auth_lease import SessionAuthLease
from .capture_clock import CaptureClock
from .live_messages import LiveMessage, LiveMessageStore

HEADER = struct.Struct(">IHHII")
HEADER_SIZE = HEADER.size
PROTO_NORMAL = 0
PROTO_HEARTBEAT = 1
PROTO_ZLIB = 2
PROTO_BROTLI = 3
OP_HEARTBEAT = 2
OP_HEARTBEAT_REPLY = 3
OP_MESSAGE = 5
OP_ENTER_ROOM = 7
OP_ENTER_ROOM_REPLY = 8


def encode_packet(op: int = 0, body: bytes = b"", protover: int = 1, seq: int = 1, action: int | None = None) -> bytes:
    operation = op if action is None else action
    return HEADER.pack(HEADER_SIZE + len(body), HEADER_SIZE, protover, operation, seq) + body


make_bilibili_packet = encode_packet


def decode_packets(data: bytes) -> list[tuple[int, int, bytes]]:
    packets: list[tuple[int, int, bytes]] = []
    offset = 0
    while offset + HEADER_SIZE <= len(data):
        packet_len, header_len, protover, operation, _seq = HEADER.unpack_from(data, offset)
        if packet_len < header_len or header_len < HEADER_SIZE or offset + packet_len > len(data):
            break
        body = data[offset + header_len:offset + packet_len]
        offset += packet_len
        try:
            if protover == PROTO_ZLIB:
                packets.extend(decode_packets(zlib.decompress(body)))
            elif protover == PROTO_BROTLI:
                import brotli
                packets.extend(decode_packets(brotli.decompress(body)))
            else:
                packets.append((protover, operation, body))
        except Exception:
            continue
    return packets


def extract_bilibili_room_id(url_or_room: str) -> int | None:
    match = re.search(r"live\.bilibili\.com/(?:blanc/)?(\d+)", str(url_or_room))
    if match:
        return int(match.group(1))
    return int(url_or_room) if str(url_or_room).isdigit() else None


def parse_bilibili_cmd(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not str(payload.get("cmd") or "").startswith("DANMU_MSG"):
        return None
    info = payload.get("info")
    if not isinstance(info, list) or len(info) < 3:
        return None
    metadata = info[0] if isinstance(info[0], list) else []
    user = info[2] if isinstance(info[2], list) else []
    badges = []
    medal = info[3] if len(info) > 3 and isinstance(info[3], list) else []
    if len(medal) > 1 and medal[1]:
        badges.append(str(medal[1]))
    platform_sent_at = None
    if len(metadata) > 4:
        try:
            candidate = float(metadata[4]) / 1000.0
            if candidate > 1_500_000_000:
                platform_sent_at = candidate
        except (TypeError, ValueError):
            pass
    source_id = payload.get("dm_v2") or payload.get("id_str")
    if not source_id:
        source_id = f"{user[0] if user else 'anon'}:{metadata[4] if len(metadata) > 4 else ''}:{info[1]}"
    return {
        "sourceId": str(source_id),
        "text": str(info[1]),
        "author": {
            "id": str(user[0]) if user else None,
            "name": str(user[1]) if len(user) > 1 else "Anonymous",
            "badges": badges,
        },
        "platformSentAt": platform_sent_at,
    }


class BilibiliDanmakuIngest:
    ROOM_INIT_URL = "https://api.live.bilibili.com/room/v1/Room/room_init"
    DANMU_INFO_URL = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo"
    AJAX_MSG_URL = "https://api.live.bilibili.com/ajax/msg"

    def __init__(
        self,
        url: str | int = 0,
        store: LiveMessageStore | None = None,
        *,
        room_id: int | str | None = None,
        clock: CaptureClock | None = None,
        auth_lease: SessionAuthLease | None = None,
        on_message: Callable[[LiveMessage], None] | None = None,
        session_factory: Callable[[], Any] = aiohttp.ClientSession,
        heartbeat_interval: float = 30.0,
        reconnect_base: float = 1.0,
        reconnect_max: float = 20.0,
        wss_url: str | None = None,
    ) -> None:
        if store is None:
            raise ValueError("LiveMessageStore is required")
        self.url = str(url)
        self.room_id = int(room_id or extract_bilibili_room_id(str(url)) or 0)
        self.store = store
        self.clock = clock
        self.auth_lease = auth_lease
        self.on_message = on_message
        self.session_factory = session_factory
        self.heartbeat_interval = heartbeat_interval
        self.reconnect_base = reconnect_base
        self.reconnect_max = reconnect_max
        self.override_wss_url = wss_url
        self._session: Any = None
        self._ws: Any = None
        self._task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._running = False
        self._state = "idle"
        self._last_error: str | None = None
        self._received = 0
        self._reconnects = 0

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self, url_or_room: str | int | None = None) -> None:
        if url_or_room is not None:
            self.url = str(url_or_room)
            self.room_id = extract_bilibili_room_id(self.url) or self.room_id
        if self._running:
            return
        self._running = True
        self._state = "connecting"
        self._task = asyncio.create_task(self._connection_loop(), name="bilibili-danmaku")

    async def stop(self) -> None:
        self._running = False
        task, self._task = self._task, None
        if task:
            task.cancel()
        heartbeat, self._heartbeat_task = self._heartbeat_task, None
        if heartbeat:
            heartbeat.cancel()
        if self._ws and not getattr(self._ws, "closed", True):
            await self._ws.close()
        self._ws = None
        if task:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._session and not getattr(self._session, "closed", False):
            await self._session.close()
        self._session = None
        self._state = "idle"

    close = stop

    async def _connection_loop(self) -> None:
        delay = self.reconnect_base
        while self._running:
            consumer = None
            try:
                consumer = self.auth_lease.acquire("bilibili_chat") if self.auth_lease else None
                if self._session is None or getattr(self._session, "closed", False):
                    self._session = self.session_factory()
                cookie_header = self._cookie_header(consumer)
                room_id, token, wss_url = await self._resolve_connection(cookie_header)
                headers = {"User-Agent": "Mozilla/5.0", "Origin": "https://live.bilibili.com"}
                if cookie_header:
                    headers["Cookie"] = cookie_header

                if not token and not self.override_wss_url:
                    await self._poll_ajax_loop(room_id, cookie_header)
                    continue

                ws_ok = False
                try:
                    async with self._session.ws_connect(wss_url, headers=headers, heartbeat=None, timeout=aiohttp.ClientTimeout(total=6.0)) as ws:
                        self._ws = ws
                        auth = json.dumps({"uid": 0, "roomid": room_id, "protover": 3, "platform": "web", "type": 2, "key": token}).encode()
                        await ws.send_bytes(encode_packet(OP_ENTER_ROOM, auth, protover=1))
                        self._state = "authenticating"
                        ws_ok = True
                        await ws.send_bytes(encode_packet(OP_HEARTBEAT, b"[object Object]", protover=1))
                        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))
                        async for message in ws:
                            if not self._running:
                                break
                            if message.type == aiohttp.WSMsgType.BINARY:
                                self._handle_binary_message(message.data)
                            elif message.type in {aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED}:
                                break
                except Exception as error:
                    self._last_error = f"WebSocket: {type(error).__name__}"
                    # WebSocket failed or blocked; fall back to official lightweight ajax/msg polling
                    if not ws_ok and self._running:
                        await self._poll_ajax_loop(room_id, cookie_header)
            except asyncio.CancelledError:
                break
            except Exception as error:
                self._last_error = f"{type(error).__name__}: {error}"
                self._state = "error"
            finally:
                if consumer:
                    consumer.release()
                if self._heartbeat_task:
                    self._heartbeat_task.cancel()
                    self._heartbeat_task = None
                self._ws = None
            if not self._running:
                break
            self._reconnects += 1
            self._state = "reconnecting"
            await asyncio.sleep(delay)
            delay = min(self.reconnect_max, max(self.reconnect_base, delay * 2))

    async def _poll_ajax_loop(self, room_id: int, cookie_header: str | None = None) -> None:
        """Lightweight zero-auth fallback polling via Bilibili official ajax/msg API."""
        self._state = "polling"
        seen_texts: dict[str, None] = {}
        delay = 1.0
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": f"https://live.bilibili.com/{room_id}",
            "Accept-Encoding": "identity",
        }
        if cookie_header:
            headers["Cookie"] = cookie_header
        while self._running:
            succeeded = False
            try:
                async with self._session.get(self.AJAX_MSG_URL, params={"roomid": room_id}, headers=headers, timeout=aiohttp.ClientTimeout(total=4.0)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("code") == 0:
                            succeeded = True
                            now_mono = time.monotonic()
                            now_wall = time.time()
                            for m in data.get("data", {}).get("room", []) or []:
                                nickname = str(m.get("nickname") or "")
                                text = str(m.get("text") or "").strip()
                                timeline = str(m.get("timeline") or "")
                                try:
                                    sent_at = datetime.strptime(timeline, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=8))).timestamp()
                                except ValueError:
                                    sent_at = None
                                uid = str(m.get("uid") or "")
                                if not text:
                                    continue
                                dedupe_key = f"{uid}:{nickname}:{timeline}:{text}"
                                if dedupe_key in seen_texts:
                                    continue
                                seen_texts[dedupe_key] = None
                                if len(seen_texts) > 500:
                                    del seen_texts[next(iter(seen_texts))]
                                message, accepted = self.store.add_with_result(
                                    platform="bilibili",
                                    source_id=dedupe_key,
                                    author={"id": uid or None, "name": nickname or "Anonymous", "badges": []},
                                    text=text,
                                    received_monotonic=now_mono,
                                    received_at=now_wall,
                                    platform_sent_at=sent_at,
                                )
                                if accepted:
                                    self._received += 1
                                    if self.on_message:
                                        self.on_message(message)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._last_error = f"AjaxPoll: {e}"
            delay = 1.0 if succeeded else min(delay * 2, 20.0)
            await asyncio.sleep(delay)

    async def _resolve_connection(self, cookie_header: str | None = None) -> tuple[int, str, str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept-Encoding": "identity",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": "https://live.bilibili.com",
            "Referer": f"https://live.bilibili.com/{self.room_id}",
        }
        if cookie_header:
            headers["Cookie"] = cookie_header
        async with self._session.get(self.ROOM_INIT_URL, params={"id": self.room_id}, headers=headers) as response:
            response.raise_for_status()
            data = await response.json()
        self.room_id = int(data.get("data", {}).get("room_id") or self.room_id)
        # Use GET with type=0 for getDanmuInfo; fallback gracefully if blocked
        config = {}
        try:
            async with self._session.get(self.DANMU_INFO_URL, params={"id": self.room_id, "type": 0}, headers=headers) as response:
                response.raise_for_status()
                resp_data = await response.json()
                if resp_data.get("code") == 0:
                    config = resp_data.get("data", {})
                else:
                    self._last_error = f"DanmuInfo rejected: code={resp_data.get('code')}"
        except Exception as error:
            self._last_error = f"DanmuInfo: {type(error).__name__}"
            config = {}
        token = str(config.get("token") or "")
        hosts = config.get("host_list") or []
        if self.override_wss_url:
            url = self.override_wss_url
        elif hosts:
            host = hosts[0]
            url = f"wss://{host['host']}:{int(host.get('wss_port') or 443)}/sub"
        else:
            url = "wss://broadcastlv.chat.bilibili.com/sub"
        return self.room_id, token, url

    @staticmethod
    def _cookie_header(consumer: Any) -> str | None:
        provider = getattr(consumer, "_provider", None)
        cookies = getattr(provider, "cookies", None) or []
        pairs = []
        for item in cookies:
            name, value = item.get("name"), item.get("value")
            if name and value is not None:
                pairs.append(f"{name}={value}")
        return "; ".join(pairs) or None

    async def _heartbeat_loop(self, ws: Any) -> None:
        packet = encode_packet(OP_HEARTBEAT, b"[object Object]", protover=1)
        while self._running and not getattr(ws, "closed", False):
            await ws.send_bytes(packet)
            await asyncio.sleep(self.heartbeat_interval)

    def _handle_binary_message(self, data: bytes) -> None:
        now_mono = time.monotonic()
        now_wall = time.time()
        for _proto, operation, body in decode_packets(data):
            if operation == OP_ENTER_ROOM_REPLY:
                reply = json.loads(body)
                if reply.get("code") != 0:
                    raise RuntimeError(f"Danmaku authentication rejected: code={reply.get('code')}")
                self._state = "running"
                self._last_error = None
                continue
            if operation != OP_MESSAGE:
                continue
            try:
                parsed = parse_bilibili_cmd(json.loads(body.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
                continue
            if not parsed:
                continue
            message, accepted = self.store.add_with_result(
                platform="bilibili",
                source_id=parsed["sourceId"],
                author=parsed["author"],
                text=parsed["text"],
                received_monotonic=now_mono,
                received_at=now_wall,
                platform_sent_at=parsed["platformSentAt"],
            )
            if accepted:
                self._received += 1
                if self.on_message:
                    self.on_message(message)

    def feed_bytes_for_test(self, data: bytes) -> None:
        self._handle_binary_message(data)

    def status(self) -> dict[str, Any]:
        return {
            "state": self._state,
            "platform": "bilibili",
            "roomId": self.room_id,
            "running": self._running,
            "connected": self._state == "running",
            "received": self._received,
            "reconnects": self._reconnects,
            "lastError": self._last_error,
        }
