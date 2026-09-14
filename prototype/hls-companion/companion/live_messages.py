"""Canonical live-message model and revision-aware bounded store."""

from __future__ import annotations

import dataclasses
import threading
import time
from collections import deque
from typing import Any, Callable

from .capture_clock import CaptureClock

TRANSLATION_STATES = {"disabled", "pending", "done", "failed", "skipped"}


@dataclasses.dataclass
class LiveMessage:
    id: str
    platform: str
    media_time: float | None
    received_at: float
    received_monotonic: float
    platform_sent_at: float | None
    author: dict[str, Any]
    kind: str
    text: str
    translation: str | None = None
    translation_state: str = "disabled"
    revision: int = 1
    seq: int = 0

    @property
    def author_name(self) -> str:
        return str(self.author.get("name") or "")

    @property
    def pts(self) -> float | None:
        """Legacy relative view; production JSON exposes only mediaTime."""
        return self.media_time

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "seq": self.seq,
            "platform": self.platform,
            "mediaTime": self.media_time,
            "receivedAt": self.received_at,
            "platformSentAt": self.platform_sent_at,
            "author": {
                "id": self.author.get("id"),
                "name": str(self.author.get("name") or ""),
                "badges": [str(item) for item in self.author.get("badges", []) if item],
            },
            "kind": self.kind,
            "text": self.text,
            "translation": self.translation,
            "translationState": self.translation_state,
            "revision": self.revision,
        }

    to_json = to_dict


class LiveMessageStore:
    """Stable-ID dedupe with seq/revision polling and media-time retention."""

    def __init__(
        self,
        *,
        clock: CaptureClock | None = None,
        max_messages: int = 5000,
        max_capacity: int | None = None,
        retention_seconds: float = 120.0,
        pending_limit: int = 500,
    ) -> None:
        self.clock = clock
        self.max_messages = max(1, int(max_capacity if max_capacity is not None else max_messages))
        self.retention_seconds = max(0.0, float(retention_seconds))
        self.pending_limit = max(1, int(pending_limit))
        self._lock = threading.RLock()
        self._messages: deque[LiveMessage] = deque()
        self._pending: deque[LiveMessage] = deque()
        self._by_id: dict[str, LiveMessage] = {}
        self._events: deque[tuple[int, str]] = deque(maxlen=max(10000, self.max_messages * 4))
        self._max_seq = 0
        self._received = 0
        self._pending_dropped = 0
        self._latest_media_time: float | None = None
        self._text_messages = 0
        self._translated = 0
        self._translation_failed = 0
        self._translation_skipped = 0
        self._callbacks: list[Callable[[LiveMessage], Any]] = []

    @property
    def max_seq(self) -> int:
        with self._lock:
            return self._max_seq

    def __len__(self) -> int:
        with self._lock:
            return len(self._messages) + len(self._pending)

    def add_on_message_callback(self, callback: Callable[[LiveMessage], Any]) -> None:
        with self._lock:
            self._callbacks.append(callback)

    def _next_event_locked(self, message: LiveMessage) -> None:
        self._max_seq += 1
        message.seq = self._max_seq
        self._events.append((message.seq, message.id))

    def add_with_result(
        self,
        *,
        platform: str,
        source_id: str,
        author: dict[str, Any],
        text: str,
        received_monotonic: float | None = None,
        received_at: float | None = None,
        platform_sent_at: float | None = None,
        media_time: float | None = None,
        kind: str = "text",
        translation_enabled: bool = False,
    ) -> tuple[LiveMessage, bool]:
        """Return the stored message and whether this call inserted it.

        A nonzero sequence identifies a published store event, not a new source
        delivery: duplicate stable IDs retain their prior sequence. Sources must
        use the explicit accepted flag for receive counters and callbacks.
        """
        stable_id = source_id if str(source_id).startswith(f"{platform}:") else f"{platform}:{source_id}"
        callbacks: list[Callable[[LiveMessage], Any]] = []
        with self._lock:
            existing = self._by_id.get(stable_id)
            if existing is not None:
                return existing, False
            mono = time.monotonic() if received_monotonic is None else float(received_monotonic)
            wall = time.time() if received_at is None else float(received_at)
            mapped = media_time
            if mapped is None and self.clock is not None:
                # Align local receipt with the acquired video, independent of
                # the sender's clock or the platform's delivery latency.
                mapped = self.clock.monotonic_to_media_time(mono)
            message = LiveMessage(
                id=stable_id,
                platform=platform,
                media_time=float(mapped) if mapped is not None else None,
                received_at=wall,
                received_monotonic=mono,
                platform_sent_at=float(platform_sent_at) if platform_sent_at is not None else None,
                author=dict(author),
                kind=kind,
                text=str(text),
                translation_state="pending" if translation_enabled and kind == "text" else "disabled",
            )
            self._by_id[stable_id] = message
            self._received += 1
            if message.media_time is None:
                if len(self._pending) >= self.pending_limit:
                    evicted = self._pending.popleft()
                    self._by_id.pop(evicted.id, None)
                    self._pending_dropped += 1
                self._pending.append(message)
                return message, True
            self._messages.append(message)
            self._count_message_locked(message, 1)
            self._next_event_locked(message)
            self._prune_locked(message.media_time)
            callbacks = list(self._callbacks)
        for callback in callbacks:
            try:
                callback(message)
            except Exception:
                pass
        return message, True

    def add(self, **kwargs: Any) -> LiveMessage:
        message, _accepted = self.add_with_result(**kwargs)
        return message

    def append(self, message: LiveMessage | None = None, **kwargs: Any) -> LiveMessage:
        if message is not None:
            return self.add(
                platform=message.platform,
                source_id=message.id,
                author=message.author,
                text=message.text,
                received_monotonic=message.received_monotonic,
                received_at=message.received_at,
                platform_sent_at=message.platform_sent_at,
                media_time=message.media_time,
                kind=message.kind,
                translation_enabled=message.translation_state == "pending",
            )
        return self.add(
            platform=str(kwargs.get("platform") or "unknown"),
            source_id=str(kwargs.get("platform_message_id") or kwargs.get("source_id") or int(time.monotonic() * 1000)),
            author={
                "id": kwargs.get("author_id"),
                "name": kwargs.get("author_name") or "",
                "badges": [kwargs.get("author_badge")] if kwargs.get("author_badge") else [],
            },
            text=str(kwargs.get("text") or ""),
            received_monotonic=kwargs.get("monotonic_recv_time", kwargs.get("timestamp_monotonic")),
            received_at=kwargs.get("timestamp_wall_clock", kwargs.get("wall_clock_time")),
            media_time=kwargs.get("media_time"),
            translation_enabled=bool(kwargs.get("translation_enabled", False)),
        )

    def flush_pending(self, monotonic_time: float | None = None) -> int:
        callbacks: list[tuple[Callable[[LiveMessage], Any], LiveMessage]] = []
        mapped_count = 0
        with self._lock:
            capture_cursor = self.clock.capture_wall_time(monotonic_time) if self.clock else None
            if capture_cursor is None or not self._pending:
                return 0
            pending = sorted(self._pending, key=lambda item: item.received_monotonic)
            newest_receive = pending[-1].received_monotonic
            pdt_floor = self.clock.pdt_epoch if self.clock else None
            self._pending.clear()
            for message in pending:
                # Anchor the newest pending receive to the authoritative cursor
                # at flush, then preserve earlier monotonic receive intervals.
                # Never invent system-wall-clock media time or project past the
                # capture cursor.
                mapped = capture_cursor - max(0.0, newest_receive - message.received_monotonic)
                if pdt_floor is not None:
                    mapped = max(float(pdt_floor), mapped)
                message.media_time = min(capture_cursor, mapped)
                self._messages.append(message)
                self._count_message_locked(message, 1)
                self._next_event_locked(message)
                mapped_count += 1
                callbacks.extend((callback, message) for callback in self._callbacks)
            self._prune_locked(capture_cursor)
        for callback, message in callbacks:
            try:
                callback(message)
            except Exception:
                pass
        return mapped_count

    update_pts_for_unanchored = flush_pending

    def update_translation(
        self,
        message_id: str,
        translation: str | None = None,
        *,
        state: str | None = None,
        provider_id: str | None = None,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> bool:
        del provider_id, latency_ms, error  # Never expose provider diagnostics per message.
        with self._lock:
            message = self._by_id.get(message_id)
            if message is None:
                return False
            next_state = state or ("done" if translation is not None else "failed")
            if next_state not in TRANSLATION_STATES:
                raise ValueError("invalid translation state")
            if message.media_time is not None:
                self._count_translation_state_locked(message.translation_state, -1)
            message.translation = translation if next_state == "done" else None
            message.translation_state = next_state
            if message.media_time is not None:
                self._count_translation_state_locked(message.translation_state, 1)
            message.revision += 1
            if message.media_time is not None:
                self._next_event_locked(message)
            return True

    def get(self, message_id: str) -> LiveMessage | None:
        with self._lock:
            return self._by_id.get(message_id)

    get_by_id = get

    def query(self, after_seq: int = 0, **legacy: Any) -> list[dict[str, Any]] | tuple[list[Any], Any]:
        # Canonical path.
        if not legacy and isinstance(after_seq, int):
            with self._lock:
                latest: dict[str, LiveMessage] = {}
                for seq, message_id in reversed(self._events):
                    if seq <= after_seq:
                        break
                    if message_id in latest:
                        continue
                    message = self._by_id.get(message_id)
                    if message is not None and message.media_time is not None:
                        latest[message_id] = message
                return [message.to_dict() for message in sorted(latest.values(), key=lambda item: item.seq)]
        # Harmless legacy alias for older internal callers.
        cursor = legacy.get("cursor", after_seq)
        return_models = bool(legacy.get("return_models", False))
        limit = int(legacy.get("limit", 100))
        rows = self.query(after_seq=int(cursor or 0))[:limit]
        if return_models:
            models = [self.get(row["id"]) for row in rows]
            return [item for item in models if item is not None], (rows[-1]["seq"] if rows else cursor)
        return rows, (rows[-1]["seq"] if rows else cursor)

    def _count_translation_state_locked(self, state: str, delta: int) -> None:
        if state == "done":
            self._translated += delta
        elif state == "failed":
            self._translation_failed += delta
        elif state == "skipped":
            self._translation_skipped += delta

    def _count_message_locked(self, message: LiveMessage, delta: int) -> None:
        if message.kind == "text":
            self._text_messages += delta
        self._count_translation_state_locked(message.translation_state, delta)

    def _prune_locked(self, newest_media_time: float | None = None) -> None:
        if newest_media_time is not None:
            self._latest_media_time = max(self._latest_media_time or newest_media_time, newest_media_time)
        cutoff = self._latest_media_time - self.retention_seconds if self._latest_media_time is not None else None
        while self._messages and (
            len(self._messages) > self.max_messages
            or (cutoff is not None and self._messages[0].media_time is not None and self._messages[0].media_time < cutoff)
        ):
            removed = self._messages.popleft()
            self._by_id.pop(removed.id, None)
            self._count_message_locked(removed, -1)

    def snapshot(self) -> list[LiveMessage]:
        with self._lock:
            return list(self._messages)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "received": self._received,
                "textMessages": self._text_messages,
                "translated": self._translated,
                "translationFailed": self._translation_failed,
                "translationSkipped": self._translation_skipped,
                "pendingClock": len(self._pending),
                "pendingDropped": self._pending_dropped,
                "buffered": len(self._messages),
                "maxSeq": self._max_seq,
            }

    def clear(self) -> None:
        with self._lock:
            self._messages.clear()
            self._pending.clear()
            self._by_id.clear()
            self._events.clear()
            self._max_seq = 0
            self._received = 0
            self._pending_dropped = 0
            self._latest_media_time = None
            self._text_messages = 0
            self._translated = 0
            self._translation_failed = 0
            self._translation_skipped = 0
