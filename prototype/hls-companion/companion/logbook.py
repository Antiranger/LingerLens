"""Bounded in-memory diagnostic log that the player UI polls over HTTP.

Why a ring and not ``logging``: the Companion is a single localhost process
whose diagnostics are read by the player page, not by a terminal. A file
handler would need rotation, permissions and a second reader; the ring is a
plain cursor API (``afterSeq``) that survives the UI reloading or reconnecting.
It is deliberately lossy -- the newest records stay, the oldest are evicted and
counted -- so a noisy FFmpeg leg cannot grow the process or push the lines the
user actually needs out of reach.

Records are sanitised here rather than at each call site, because every value
ends up in front of the user: CR/LF are flattened so one record is one visible
line, URLs are redacted because a provider error can embed a signed manifest
URL or an API key in a query string, and the length is capped so one FFmpeg
stderr dump cannot bloat a poll response.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from collections import deque
from typing import Any

# Ring size: enough to hold one failed session's whole story (ingest, FFmpeg,
# ASR, translation), small enough that a poll stays a few hundred KB at worst.
CAPACITY = 500
# One line, not a stack trace: the UI renders these in a narrow pane and every
# record is re-sent until the cursor passes it.
MAX_MESSAGE_CHARS = 400
# The UI switches on these values; adding one is an API change, not a string.
LEVELS = ("info", "warn", "error")
SOURCES = ("request", "media", "asr", "chat", "translation", "desktop")
REDACTED_URL = "<REDACTED_URL>"

# Same habit as ytdlp_ingest._redact, which strips stream URLs before they reach
# /api/status: a signed live URL is a credential for as long as it is valid.
_URL_RE = re.compile(r"https?://\S+")
# NUL/ESC and friends would either corrupt the JSON poll or drive the terminal
# of whoever pastes the payload into a bug report.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SOURCE_RE = re.compile(r"[^a-z0-9-]+")


def _one_line(message: object) -> str:
    """Flatten, redact and cap one message so it is safe to hand to the UI."""
    text = str(message).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    text = _URL_RE.sub(REDACTED_URL, _CONTROL_RE.sub(" ", text)).strip()
    if len(text) > MAX_MESSAGE_CHARS:
        # The ellipsis counts towards the cap: the field never exceeds 400
        # characters, which is what the client sizes its column against.
        text = text[: MAX_MESSAGE_CHARS - 1] + "…"
    return text


def _level(value: object) -> str:
    """Clamp a level to the documented three.

    An unknown level becomes ``info`` rather than being dropped or escalated: a
    log that invents errors trains the reader (and the UI badge) to ignore it.
    """
    text = str(value).strip().lower()
    return text if text in LEVELS else "info"


def _source(value: object) -> str:
    """Clamp an origin tag to a short lowercase token.

    Callers are expected to use ``SOURCES``; a stray tag is normalised instead
    of rejected so that logging can never fail a media or request path.
    """
    text = _SOURCE_RE.sub("", str(value).strip().lower())[:24]
    return text or "desktop"


def cursor(value: object = 0) -> int:
    """Coerce a UI cursor; anything unusable means "send me everything".

    A missing or malformed ``afterSeq`` is a client bug, so the log route
    answers with the whole ring rather than a 400: the UI polls this route on a
    timer, and failing the poll would hide the very failure being investigated.
    """
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


class Logbook:
    """Thread-safe bounded ring of diagnostic records with a poll cursor.

    FFmpeg/yt-dlp reader threads, asyncio tasks and aiohttp handlers all write
    into one instance, so every mutation happens under a lock. No caller holds
    another lock while calling in, so the ordering cannot deadlock.
    """

    def __init__(self, capacity: int = CAPACITY) -> None:
        self.capacity = max(1, int(capacity))
        self._lock = threading.Lock()
        self._records: deque[dict[str, Any]] = deque()
        self._max_seq = 0
        self._dropped = 0
        # Fixed for the process lifetime so the UI can distinguish "no new
        # lines" from "the backend restarted and this cursor is meaningless".
        self._session_id = secrets.token_hex(4)

    @property
    def session_id(self) -> str:
        with self._lock:
            return self._session_id

    @property
    def max_seq(self) -> int:
        """Highest seq ever assigned, including records the ring has evicted."""
        with self._lock:
            return self._max_seq

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    def record(self, level: str, source: str, message: str) -> int:
        """Append one record and return its sequence number.

        Sequence numbers are assigned under the lock and never reused, so a
        cursor taken from this return value stays valid for the whole process.
        """
        entry = {
            "seq": 0,  # Assigned under the lock below.
            "t": time.time(),
            "level": _level(level),
            "source": _source(source),
            "message": _one_line(message),
        }
        with self._lock:
            self._max_seq += 1
            entry["seq"] = self._max_seq
            if len(self._records) >= self.capacity:
                self._records.popleft()
                self._dropped += 1
            self._records.append(entry)
            return self._max_seq

    def snapshot(self, after_seq: Any = 0) -> dict[str, Any]:
        """Return records newer than ``after_seq`` plus the poll metadata.

        ``maxSeq`` and ``dropped`` are reported even when the ring has moved
        past the cursor: the UI must be able to say "you missed N lines"
        instead of silently showing a gap in the timeline.
        """
        cursor_value = cursor(after_seq)
        with self._lock:
            return {
                "records": [dict(entry) for entry in self._records if entry["seq"] > cursor_value],
                "maxSeq": self._max_seq,
                "dropped": self._dropped,
                "sessionId": self._session_id,
            }

    def reset(self) -> None:
        """Drop every record and restart the sequence.

        For tests, and for a caller that deliberately reuses the process for a
        new run. The session id is kept: within one process it is a fact about
        the process, not about the records currently held.
        """
        with self._lock:
            self._records.clear()
            self._max_seq = 0
            self._dropped = 0


_default = Logbook()


def default_logbook() -> Logbook:
    """The process-wide instance modules log into without plumbing one through."""
    return _default


def record(level: str, source: str, message: str) -> int:
    """Append to the default logbook; see :meth:`Logbook.record`."""
    return _default.record(level, source, message)


def snapshot(after_seq: Any = 0) -> dict[str, Any]:
    """Snapshot the default logbook; see :meth:`Logbook.snapshot`."""
    return _default.snapshot(after_seq)


def reset() -> None:
    """Reset the default logbook; see :meth:`Logbook.reset`."""
    _default.reset()
