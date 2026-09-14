"""Session authentication lease with independently owned short-lived consumers."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from .core import AuthenticationProvider, BrowserCookieSnapshot, DevelopmentBrowserProfileFallback


@dataclass
class AuthConsumer:
    _lease: "SessionAuthLease"
    label: str
    _provider: AuthenticationProvider
    _released: bool = False

    def yt_dlp_args(self) -> list[str]:
        if self._released:
            raise RuntimeError("authentication consumer is released")
        return self._provider.yt_dlp_args()

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._provider.close()
        self._lease._release_consumer(self)

    close = release


class SessionAuthLease:
    """Retain normalized auth in memory; materialize each subprocess separately."""

    def __init__(self, provider: AuthenticationProvider) -> None:
        self._lock = threading.RLock()
        self._closed = False
        self._initial = True
        self._consumers: dict[int, AuthConsumer] = {}
        self._cookies = [dict(item) for item in getattr(provider, "cookies", [])]
        self._browser = getattr(provider, "browser", None)
        # The provider passed by the request may already own a temporary file.
        # Capture its normalized source then close that file immediately.
        provider.close()

    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def ref_count(self) -> int:
        with self._lock:
            return (1 if self._initial and not self._closed else 0) + len(self._consumers)

    def acquire(self, label: str = "") -> AuthConsumer:
        with self._lock:
            if self._closed:
                raise RuntimeError("Cannot acquire a closed authentication lease")
            if self._cookies:
                provider: AuthenticationProvider = BrowserCookieSnapshot(self._cookies)
            else:
                provider = DevelopmentBrowserProfileFallback(self._browser)
            consumer = AuthConsumer(self, label, provider)
            self._consumers[id(consumer)] = consumer
            return consumer

    def _release_consumer(self, consumer: AuthConsumer) -> None:
        with self._lock:
            self._consumers.pop(id(consumer), None)
            self._maybe_close_locked()

    def release_initial(self) -> None:
        with self._lock:
            self._initial = False
            self._maybe_close_locked()

    def release(self) -> None:
        self.release_initial()

    def _maybe_close_locked(self) -> None:
        if not self._initial and not self._consumers:
            self._closed = True
            self._cookies.clear()
            self._browser = None

    def force_close(self) -> None:
        with self._lock:
            if self._closed and not self._consumers:
                return
            self._closed = True
            self._initial = False
            consumers = list(self._consumers.values())
            self._consumers.clear()
            self._cookies.clear()
            self._browser = None
        for consumer in consumers:
            if consumer._released:
                continue
            consumer._released = True
            consumer._provider.close()


# Backwards-compatible names used by older tests/imports.
AuthLease = SessionAuthLease
AuthLeaseManager = SessionAuthLease
