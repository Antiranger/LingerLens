"""Bounded independent live-message translation workers."""

from __future__ import annotations

import asyncio
import json
import re
from collections import OrderedDict, deque
from typing import Any

from .live_messages import LiveMessage, LiveMessageStore
from .logbook import record as log_record
from .providers.base import StreamMeta, TranslationProvider, TranslationRequest, ProviderRateLimitError, ProviderAuthError
from .subtitle_pipeline import normalize_translation_usage
from .providers.http import translation_session

URL_RE = re.compile(r"^(?:https?://|www\.)\S+$", re.IGNORECASE)
WORD_RE = re.compile(r"[A-Za-z0-9\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


def should_skip_translation(text: str) -> bool:
    stripped = text.strip()
    return not stripped or bool(URL_RE.fullmatch(stripped)) or not bool(WORD_RE.search(stripped))


class MessageTranslationPipeline:
    def __init__(
        self,
        store: LiveMessageStore,
        translation_provider: TranslationProvider | None = None,
        fallback_provider: TranslationProvider | None = None,
        pricing_by_provider: dict[str, dict[str, float | None]] | None = None,
        meta: StreamMeta | None = None,
        max_queue_size: int = 30,
        concurrency: int = 1,
        timeout_seconds: float = 3.0,
        provider: TranslationProvider | None = None,
        target_lang: str | None = None,
    ) -> None:
        self.concurrency = max(1, min(int(concurrency), 8))
        self._cache = OrderedDict()
        self._revision = 0
        self._cooldown_until = 0.0
        self.store = store
        self.translation_provider = translation_provider or provider
        self.fallback_provider = fallback_provider
        self.pricing_by_provider = pricing_by_provider or {}
        self.meta = meta or StreamMeta(None, None, None, "und", target_lang or "zh-Hans")
        self.max_queue_size = max(1, int(max_queue_size))
        self.timeout_seconds = float(timeout_seconds)
        self._queue: deque[LiveMessage] = deque()
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._running = False
        self._enabled = False
        self._enabled_epoch = 0

        self._failures = {}
        self._attempt_failures = {}
        self._last_failure = None
        # One diagnostic record per failure episode: a failing batch drops up
        # to 20 messages at once and the shared ring must stay readable.
        self._failure_recorded: str | None = None
        self._calls = 0
        self._unknown_usage_calls = 0
        self._usage_by_provider: dict[str, dict[str, int]] = {}

    def set_enabled(self, enabled: bool) -> None:
        if self._enabled != bool(enabled):
            self._enabled_epoch += 1
        self._enabled = bool(enabled)
        if not self._enabled:
            while self._queue:
                message, *_ = self._queue.popleft()
                self.store.update_translation(message.id, state="skipped")

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def start(self) -> None:
        if self._running:
            return
        if self.translation_provider is None:
            raise ValueError("translation provider is required")
        self._running = True
        self._task = asyncio.create_task(self._workers(), name="live-message-translation")

    async def stop(self) -> None:
        self._running = False
        self.set_enabled(False)
        self._wake.set()
        task, self._task = self._task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def enqueue(self, message: LiveMessage) -> bool:
        if not self._running or not self._enabled or message.kind != "text":
            if message.translation_state == "pending":
                self.store.update_translation(message.id, state="disabled" if not self._enabled else "skipped")
            return False
        if should_skip_translation(message.text):
            self.store.update_translation(message.id, state="skipped")
            return False
        if len(self._queue) >= self.max_queue_size:
            oldest, *_ = self._queue.popleft()
            self.store.update_translation(oldest.id, state="skipped")
        self._queue.append((message, self.translation_provider, self.meta,
                            asyncio.get_running_loop().time() + self.timeout_seconds, self._revision, self._enabled_epoch))
        self._wake.set()
        return True

    def reconfigure(self, provider: TranslationProvider, meta: StreamMeta, pricing=None) -> None:
        self.translation_provider = provider
        self.meta = meta
        self._revision += 1
        self._cache.clear()
        self._cooldown_until = 0.0
        if pricing is not None:
            self.pricing_by_provider.update(pricing)

    async def _workers(self) -> None:
        tasks = [asyncio.create_task(self._worker()) for _ in range(self.concurrency)]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _worker(self) -> None:
        async with translation_session():
            await self._worker_loop()

    async def _worker_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while self._running:
            if not self._queue:
                self._wake.clear()
                await self._wake.wait()
                continue
            first = self._queue.popleft()
            entries = [first]
            _, provider, meta, _, revision, epoch = first
            capabilities = getattr(provider, "capabilities", None)
            can_batch = bool(getattr(getattr(capabilities, "language", None), "open_world_prompting", False))
            chars = len(first[0].text)
            # Drain only already-arrived messages; never wait to fill a batch.
            while can_batch and self._queue and len(entries) < 20:
                next_entry = self._queue[0]
                if (next_entry[1] is not provider or next_entry[2] != meta
                        or next_entry[4:] != first[4:] or chars + len(next_entry[0].text) > 4000):
                    break
                entries.append(self._queue.popleft())
                chars += len(next_entry[0].text)
            pending = []
            for entry in entries:
                message, _, _, deadline, _, _ = entry
                if not self._enabled or epoch != self._enabled_epoch or loop.time() >= deadline or meta.target_lang != self.meta.target_lang:
                    self.store.update_translation(message.id, state="skipped")
                    continue
                key = (message.text, meta.target_lang, revision)
                cached = self._cache.get(key)
                if cached and loop.time() - cached[0] < 120:
                    self._cache.move_to_end(key)
                    self.store.update_translation(message.id, cached[1], state="done")
                else:
                    pending.append(entry)
            if not pending:
                continue
            deadline = min(entry[3] for entry in pending)
            batch = len(pending) > 1
            source = json.dumps([{"id": str(i), "text": entry[0].text} for i, entry in enumerate(pending)], ensure_ascii=False) if batch else pending[0][0].text
            request = TranslationRequest(source_text=source, meta=meta, history=[], glossary=[],
                                         deadline_monotonic=deadline, purpose="live_chat_batch" if batch else "live_chat")
            translated = None
            failure_reason = "deadline"
            for attempt in range(3):
                if self._cooldown_until > loop.time():
                    await asyncio.sleep(min(self._cooldown_until - loop.time(), max(0, deadline - loop.time())))
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    attempt_provider = self.fallback_provider if not batch and attempt > 0 and self.fallback_provider is not None else provider
                    candidate = await asyncio.wait_for(attempt_provider.translate(request), remaining)
                    self._record_usage(candidate.provider_id, candidate.usage)
                    text = (candidate.text or "").strip()
                    if not text:
                        raise ValueError("empty translation")
                    if batch:
                        if text.startswith("```") and text.endswith("```"):
                            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                        values = json.loads(text)
                        if not isinstance(values, list) or len(values) != len(pending):
                            raise ValueError("incomplete batch translation")
                        mapped = {}
                        for value in values:
                            if not isinstance(value, dict) or not isinstance(value.get("id"), str) or not isinstance(value.get("text"), str) or not value["text"].strip() or value["id"] in mapped:
                                raise ValueError("invalid batch translation")
                            mapped[value["id"]] = value["text"].strip()
                        if set(mapped) != {str(i) for i in range(len(pending))}:
                            raise ValueError("mismatched batch IDs")
                        translated = [mapped[str(i)] for i in range(len(pending))]
                    else:
                        if not text:
                            raise ValueError("empty translation")
                        translated = [text]
                    break
                except asyncio.CancelledError:
                    for entry in pending:
                        self.store.update_translation(entry[0].id, state="skipped")
                    raise
                except ProviderRateLimitError as error:
                    failure_reason = "rate_limit"
                    self._attempt_failures[failure_reason] = self._attempt_failures.get(failure_reason, 0) + 1
                    self._cooldown_until = max(self._cooldown_until, loop.time() + max(10.0, getattr(error, "retry_after_seconds", 10.0)))
                    break
                except ProviderAuthError:
                    failure_reason = "authentication"
                    self._attempt_failures[failure_reason] = self._attempt_failures.get(failure_reason, 0) + 1
                    self._cooldown_until = max(self._cooldown_until, loop.time() + 30.0)
                    break
                except Exception as error:
                    if isinstance(error, asyncio.TimeoutError):
                        failure_reason = "timeout"
                    elif isinstance(error, json.JSONDecodeError):
                        failure_reason = "json_format"
                    elif isinstance(error, ValueError):
                        failure_reason = {"empty translation": "empty", "incomplete batch translation": "batch_count", "invalid batch translation": "batch_item", "mismatched batch IDs": "batch_ids"}.get(str(error), "response_format")
                    else:
                        failure_reason = "provider_error"
                    self._attempt_failures[failure_reason] = self._attempt_failures.get(failure_reason, 0) + 1
                    if attempt < 2:
                        retry_delay = 0.2 * (2 ** attempt)
                        if deadline - loop.time() <= retry_delay:
                            break
                        await asyncio.sleep(retry_delay)
            for i, entry in enumerate(pending):
                message = entry[0]
                if not self._enabled or epoch != self._enabled_epoch or meta.target_lang != self.meta.target_lang:
                    self.store.update_translation(message.id, state="skipped")
                elif translated is None:
                    self._failures[failure_reason] = self._failures.get(failure_reason, 0) + 1
                    self._last_failure = failure_reason
                    if failure_reason != self._failure_recorded:
                        # The messages are dropped from the overlay with no
                        # other trace, so name the first failure of an episode.
                        log_record("warn", "translation", f"live chat translation failed: {failure_reason}")
                        self._failure_recorded = failure_reason
                    self.store.update_translation(message.id, state="failed")
                else:
                    key = (message.text, meta.target_lang, revision)
                    self._cache[key] = (loop.time(), translated[i])
                    self._cache.move_to_end(key)
                    while len(self._cache) > 500:
                        self._cache.popitem(last=False)
                    self.store.update_translation(message.id, translated[i], state="done")
                    self._failure_recorded = None

    def _record_usage(self, provider_id: str, raw_usage: dict[str, Any] | None) -> None:
        self._calls += 1
        normalized = normalize_translation_usage(raw_usage)
        usage = self._usage_by_provider.setdefault(provider_id, {
            "calls": 0, "nonCachedInputTokens": 0, "cachedInputTokens": 0,
            "cacheWriteInputTokens": 0, "outputTokens": 0, "totalTokens": 0,
        })
        usage["calls"] += 1
        if normalized is None:
            self._unknown_usage_calls += 1
            return
        for field in ("nonCachedInputTokens", "cachedInputTokens", "cacheWriteInputTokens", "outputTokens", "totalTokens"):
            usage[field] += normalized[field]

    def status(self) -> dict[str, Any]:
        aggregate = {
            "calls": self._calls,
            "unknownUsageCalls": self._unknown_usage_calls,
            "nonCachedInputTokens": sum(item["nonCachedInputTokens"] for item in self._usage_by_provider.values()),
            "cachedInputTokens": sum(item["cachedInputTokens"] for item in self._usage_by_provider.values()),
            "cacheWriteInputTokens": sum(item["cacheWriteInputTokens"] for item in self._usage_by_provider.values()),
            "outputTokens": sum(item["outputTokens"] for item in self._usage_by_provider.values()),
            "totalTokens": sum(item["totalTokens"] for item in self._usage_by_provider.values()),
            "byProvider": {key: dict(value) for key, value in self._usage_by_provider.items()},
        }
        cost: float | None = 0.0
        reason: str | None = None
        if self._unknown_usage_calls:
            cost = None
            reason = "translation usage unavailable for one or more calls"
        costs_by_currency: dict[str, float] = {}
        for provider_id, usage in self._usage_by_provider.items():
            prices = self.pricing_by_provider.get(provider_id, {})
            required = (prices.get("input"), prices.get("cachedInput"), prices.get("output"))
            if any(value is None for value in required):
                cost = None
                reason = f"translation pricing incomplete for provider {provider_id}"
                break
            provider_cost = (
                usage["nonCachedInputTokens"] * float(required[0])
                + usage["cachedInputTokens"] * float(required[1])
                + usage["outputTokens"] * float(required[2])
            ) / 1_000_000
            code = str(prices.get("currency") or "USD").upper()
            costs_by_currency[code] = costs_by_currency.get(code, 0.0) + provider_cost
            cost = float(cost or 0.0) + provider_cost
        if not self._usage_by_provider and cost == 0.0:
            cost = None
            reason = "translation usage unavailable"
        # 混合币种时同样给不出单一合计，交由前端逐币种列出。
        if len(costs_by_currency) > 1:
            cost = None
            reason = "translation providers bill in more than one currency"
        return {
            "running": self._running,
            "enabled": self._enabled,
            "backlog": len(self._queue),
            "failureReasons": dict(self._failures),
            "attemptFailureReasons": dict(self._attempt_failures),
            "lastFailureReason": self._last_failure,
            "translationUsage": aggregate,
            "translationEstimatedCostCny": round(cost, 9) if cost is not None else None,
            "translationCostCurrency": (
                next(iter(costs_by_currency)) if len(costs_by_currency) == 1 else None
            ),
            "costsByCurrency": [
                {"currency": code, "amount": round(amount, 9)}
                for code, amount in sorted(costs_by_currency.items())
            ],
            "translationEstimateReason": reason,
        }


MessageTranslator = MessageTranslationPipeline
LiveMessageTranslationWorker = MessageTranslationPipeline
