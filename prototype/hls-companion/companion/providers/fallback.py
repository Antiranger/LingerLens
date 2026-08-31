from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Sequence

from .base import TranslationCapabilities, TranslationProvider, TranslationRequest, TranslationResult


@dataclass
class _Health:
    consecutive_failures: int = 0
    cooldown_until: float = 0.0


class FallbackChain(TranslationProvider):
    """Try translation providers in order and cool repeatedly failing providers."""

    def __init__(
        self,
        providers: Sequence[TranslationProvider],
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not providers:
            raise ValueError("fallback chain requires at least one provider")
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        self.providers = list(providers)
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock
        self.health = {provider.id: _Health() for provider in self.providers}
        self.id = "fallback:" + ",".join(provider.id for provider in self.providers)
        self.label = "Fallback chain"
        self.model = ",".join(provider.model for provider in self.providers)

    @property
    def capabilities(self) -> TranslationCapabilities:
        capabilities = [provider.capabilities for provider in self.providers]
        return TranslationCapabilities(
            rolling_context=all(item.rolling_context for item in capabilities),
            glossary=all(item.glossary for item in capabilities),
            domains=all(item.domains for item in capabilities),
            json_output=all(item.json_output for item in capabilities),
            max_input_chars=min(item.max_input_chars for item in capabilities),
        )

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        now = self.clock()
        errors: list[tuple[str, BaseException]] = []
        attempted = False
        for provider in self.providers:
            state = self.health[provider.id]
            if state.cooldown_until > now:
                continue
            if state.cooldown_until:
                state.cooldown_until = 0.0
                state.consecutive_failures = 0
            attempted = True
            try:
                result = await provider.translate(request)
            except BaseException as exc:
                state.consecutive_failures += 1
                if state.consecutive_failures >= self.failure_threshold:
                    state.cooldown_until = self.clock() + self.cooldown_seconds
                errors.append((provider.id, exc))
                continue
            state.consecutive_failures = 0
            state.cooldown_until = 0.0
            return result
        if not attempted:
            raise RuntimeError("all translation providers are cooling down")
        detail = "; ".join(f"{provider_id}: {error}" for provider_id, error in errors)
        raise RuntimeError(f"all translation providers failed: {detail}") from errors[-1][1]
