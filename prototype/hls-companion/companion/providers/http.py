"""Shared async JSON HTTP transport for translation adapters.

One POST helper used by ``openai-compatible``, ``anthropic-messages`` and
``google-genai`` so HTTP status codes map onto the normalized Provider error
classes (spec §5.4) in exactly one place:

* 401/403            -> ``ProviderAuthError``       (no retry of this request)
* 429                -> ``ProviderRateLimitError``  (short cooldown)
* 5xx (incl. 529)    -> ``ProviderUnavailableError``(failure counting)
* other 4xx          -> ``ProviderRequestError``    (marks a config problem)
* network disconnect -> ``ProviderUnavailableError``
* timeout            -> ``asyncio.TimeoutError``    (existing deadline semantics)

``asyncio.TimeoutError`` passes through untouched so the pipeline deadline
keeps meaning "translation deadline expired". Each response is released when
its request ends; the reusable session closes when its worker scope exits.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

import aiohttp

from .base import (
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderUnavailableError,
)
from .base import TranslationRequest


_session: ContextVar[aiohttp.ClientSession | None] = ContextVar("translation_http_session", default=None)


@asynccontextmanager
async def translation_session():
    """Reuse connections for one worker; release them on stop/cancellation.

    Task-local ownership also covers fallbacks and profile changes without
    retaining provider objects. Credentials stay on each request, and cookies
    are disabled so switching accounts cannot replay a previous response cookie.
    Standalone probes use the same scope for a single call.
    """
    existing = _session.get()
    if existing is not None and not existing.closed:
        yield existing
        return
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        token = _session.set(session)
        try:
            yield session
        finally:
            _session.reset(token)


def require_api_key(provider_id: str, api_key: str) -> None:
    """Missing credentials are an auth/config failure, reported before any request."""
    if not api_key:
        raise ProviderAuthError(f"API key is not configured for provider {provider_id}")


def request_timeout(options: dict[str, Any], request: TranslationRequest) -> float:
    """Effective timeout: the configured budget, capped by the cue deadline."""
    timeout = float(options.get("timeoutSeconds", 6))
    if request.deadline_monotonic is not None:
        timeout = min(timeout, request.deadline_monotonic - time.monotonic())
    if timeout <= 0:
        raise asyncio.TimeoutError("translation deadline has expired")
    return timeout


def _error_message(data: Any) -> str:
    """All three Providers put the human-readable error at ``error.message``."""
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
    return str(data)


async def post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    """POST one JSON body and return the parsed object, with normalized errors."""
    session = _session.get()
    if session is None or session.closed:
        async with translation_session():
            return await post_json(url, headers=headers, payload=payload, timeout=timeout)
    try:
        async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
            status = response.status
            retry_after = getattr(response, "headers", {}).get("Retry-After")
            try:
                data = await response.json(content_type=None)
            except (asyncio.TimeoutError, aiohttp.ClientError):
                raise
            except ValueError:
                # A non-JSON body still carries the authoritative status code.
                data = {}
    except (asyncio.TimeoutError, TimeoutError):
        raise
    except aiohttp.ClientError as exc:
        raise ProviderUnavailableError(f"translation request failed: {exc}") from exc
    if status >= 400:
        detail = f"translation HTTP {status}: {_error_message(data)}"
        if status in (401, 403):
            raise ProviderAuthError(detail)
        if status == 429:
            error = ProviderRateLimitError(detail)
            try:
                error.retry_after_seconds = max(0.0, float(retry_after))
            except (TypeError, ValueError):
                from email.utils import parsedate_to_datetime
                from datetime import datetime, timezone
                try:
                    error.retry_after_seconds = max(0.0, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    error.retry_after_seconds = 10.0
            raise error
        if status >= 500:
            raise ProviderUnavailableError(detail)
        raise ProviderRequestError(detail)
    if not isinstance(data, dict):
        raise ProviderRequestError("translation response was not a JSON object")
    return data
