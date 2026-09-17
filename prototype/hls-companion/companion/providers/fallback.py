from __future__ import annotations

import asyncio
import dataclasses
import time
from dataclasses import dataclass
from typing import Callable, Sequence

from ..languages import LanguageNotSupportedError
from .base import (
    ProviderAuthError,
    ProviderRequestError,
    ProviderRateLimitError,
    TranslationCapabilities,
    TranslationLanguageCapabilities,
    TranslationProvider,
    TranslationRequest,
    TranslationResult,
)


@dataclass
class _Health:
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    # Deterministic configuration/auth/request problems persist for the whole
    # session: retrying them can only fail again, so the provider is skipped
    # instead of burning one request per cue.
    disabled_reason: str | None = None


def _describe(error: BaseException) -> str:
    """``TypeName: message``, because the timeout family stringifies to nothing.

    ``http.post_json`` re-raises ``asyncio.TimeoutError`` untouched so the cue
    deadline keeps its meaning, and ``str()`` of a bare TimeoutError -- like
    ConnectionError and OSError -- is ``''``. Formatting with a bare ``{error}``
    therefore logged ``"some-provider: "`` with nothing after the colon. A live
    session recorded 21 translation failures in 77 minutes that way, without
    recording a single reason for any of them.
    """
    name = type(error).__name__
    text = str(error).strip()
    return f"{name}: {text}" if text else name


def _name_of(provider: object) -> str:
    """What the user calls this provider, for logs a human has to act on.

    The id is a configuration slug that can stop describing the endpoint:
    a live session logged ``bailian-qwen35-flash`` while that provider was
    labelled ``gemini-3.7-flash-low`` and pointed at a local gateway, which made
    a deliberately configured provider look like one nobody had ever set up.
    Falls back to the id when a provider carries no label.
    """
    label = getattr(provider, "label", None)
    if label:
        return str(label)
    return str(getattr(provider, "id", "?"))


class FallbackChain(TranslationProvider):
    """Try translation providers in order and cool repeatedly failing providers.

    Deterministic behavior by normalized error class (spec §5.4):

    * ``ProviderAuthError`` / ``ProviderRequestError`` /
      ``LanguageNotSupportedError`` (config/auth/request problems, including a
      cue the provider's pair contract cannot represent): the provider is
      marked misconfigured for the session and skipped thereafter; the chain
      falls through to the next provider immediately.
    * ``ProviderRateLimitError`` (429): fall through immediately and cool the
      provider for ``cooldown_seconds`` regardless of the failure threshold.
    * ``ProviderUnavailableError`` (5xx/529/disconnect), timeouts
      (``asyncio.TimeoutError`` keeps its deadline semantics) and any other
      failure: count toward ``failure_threshold``; the provider cools for
      ``cooldown_seconds`` once the threshold is reached.

    Empty/refusal responses arrive here as ``ProviderRefusalError`` (a normal
    counted failure): they are never treated as successful empty translations.
    """

    def __init__(
        self,
        providers: Sequence[TranslationProvider],
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
        fallback_reserve_seconds: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not providers:
            raise ValueError("fallback chain requires at least one provider")
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if fallback_reserve_seconds < 0:
            raise ValueError("fallback_reserve_seconds must be non-negative")
        self.providers = list(providers)
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.fallback_reserve_seconds = fallback_reserve_seconds
        self.clock = clock
        self.health = {provider.id: _Health() for provider in self.providers}
        self.id = "fallback:" + ",".join(provider.id for provider in self.providers)
        self.label = "Fallback chain"
        self.model = ",".join(provider.model for provider in self.providers)

    @property
    def handles_failover(self) -> bool:
        """The chain owns one bounded primary-to-fallback attempt."""
        return len(self.providers) > 1

    @property
    def capabilities(self) -> TranslationCapabilities:
        capabilities = [provider.capabilities for provider in self.providers]
        return TranslationCapabilities(
            rolling_context=all(item.rolling_context for item in capabilities),
            glossary=all(item.glossary for item in capabilities),
            domains=all(item.domains for item in capabilities),
            json_output=all(item.json_output for item in capabilities),
            max_input_chars=min(item.max_input_chars for item in capabilities),
            language=_combined_language_capabilities([item.language for item in capabilities]),
        )

    def _could_try_any(
        self,
        providers: Sequence[TranslationProvider],
        now: float,
        source_text: str,
    ) -> bool:
        """Whether any of these providers is not already ruled out.

        Mirrors the loop's own preconditions -- not misconfigured for the
        session, not cooling down, and a cue its declared input limit can hold.
        It is a statement about eligibility, not about the network: no probing,
        no new circuit breaker.
        """
        for provider in providers:
            state = self.health[provider.id]
            if state.disabled_reason is not None:
                continue
            if state.cooldown_until > now:
                continue
            limit = getattr(provider.capabilities, "max_input_chars", 0) or 0
            if limit > 0 and len(source_text) > limit:
                continue
            return True
        return False

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        now = self.clock()
        errors: list[tuple[str, BaseException]] = []
        attempted = False
        # A reservation is only meaningful if some later provider could actually
        # be tried right now. Reserving for a disabled or cooling one would
        # shorten the primary's deadline and buy nothing.
        reserve = 0.0
        if self.fallback_reserve_seconds > 0 and self._could_try_any(
            self.providers[1:], now, request.source_text
        ):
            reserve = self.fallback_reserve_seconds
        for index, provider in enumerate(self.providers):
            state = self.health[provider.id]
            if state.disabled_reason is not None:
                continue
            if state.cooldown_until > now:
                continue
            if state.cooldown_until:
                state.cooldown_until = 0.0
                state.consecutive_failures = 0
            attempted = True
            # Enforce the declared input limit BEFORE calling. Nothing read
            # max_input_chars, so an over-long cue reached the Provider, came
            # back as a 400 (context_length_exceeded), and was classified as
            # ProviderRequestError -- which disables that Provider for the rest
            # of the session. With the default single-Provider configuration
            # that meant one long sentence silently ended all subtitles until a
            # restart. A cue that does not fit is a cue-level skip instead.
            limit = getattr(provider.capabilities, "max_input_chars", 0) or 0
            if limit > 0 and len(request.source_text) > limit:
                errors.append((
                    _name_of(provider),
                    ProviderRequestError(
                        f"cue of {len(request.source_text)} chars exceeds the "
                        f"{limit}-char limit for {_name_of(provider)}"
                    ),
                ))
                continue
            try:
                provider_request = request
                # Keep a small, explicit budget for the next provider.  The
                # pipeline's cue deadline is a total latency budget; without
                # this reservation a primary timeout consumes all of it and
                # the fallback immediately sees an expired request.
                #
                # When the remaining budget does not even cover the reserve,
                # give the WHOLE remainder to the providers that can still be
                # tried. Handing it to the primary instead was the old
                # behaviour, and it meant a slow primary spent the entire cue
                # budget and left the fallback an already-expired deadline --
                # the reservation this block exists to provide, inverted at
                # exactly the boundary where it matters. Skipping is a budget
                # decision, not a provider failure: it touches no health state,
                # so nothing is disabled or cooled by it.
                if index == 0 and reserve > 0 and request.deadline_monotonic is not None:
                    remaining = request.deadline_monotonic - self.clock()
                    if remaining <= reserve:
                        errors.append((
                            _name_of(provider),
                            RuntimeError(
                                f"skipped: {remaining:.2f}s left does not cover the "
                                f"{reserve:.2f}s fallback reserve"
                            ),
                        ))
                        continue
                    provider_request = dataclasses.replace(
                        request,
                        deadline_monotonic=request.deadline_monotonic - reserve,
                    )
                result = await provider.translate(provider_request)
            except (ProviderAuthError, ProviderRequestError, LanguageNotSupportedError) as exc:
                # Deterministic config/auth/request problem: retrying this
                # provider can only fail again this session.
                state.disabled_reason = _describe(exc)
                errors.append((_name_of(provider), exc))
                continue
            except ProviderRateLimitError as exc:
                # 429: fall through immediately and cool regardless of threshold.
                state.consecutive_failures += 1
                state.cooldown_until = self.clock() + self.cooldown_seconds
                errors.append((_name_of(provider), exc))
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Unavailable (5xx/529/disconnect), timeouts, refusals and
                # unexpected errors count toward the consecutive-failure
                # threshold before the provider cools.
                state.consecutive_failures += 1
                if state.consecutive_failures >= self.failure_threshold:
                    state.cooldown_until = self.clock() + self.cooldown_seconds
                errors.append((_name_of(provider), exc))
                continue
            state.consecutive_failures = 0
            state.cooldown_until = 0.0
            return result
        if not attempted:
            raise RuntimeError("all translation providers are cooling down or disabled")
        detail = "; ".join(f"{name}: {_describe(error)}" for name, error in errors)
        raise RuntimeError(f"all translation providers failed: {detail}") from errors[-1][1]


def _combined_language_capabilities(
    languages: Sequence[TranslationLanguageCapabilities],
) -> TranslationLanguageCapabilities:
    """Union semantics: the chain can translate a pair if any member can.

    A generic open-world member makes the whole chain open-world; otherwise
    closed contracts are unioned so pre-start validation reflects what at
    least one member can honor.
    """
    if any(item.open_world_prompting for item in languages):
        return TranslationLanguageCapabilities(open_world_prompting=True, tier="provider_claimed")
    source_tags: set[str] = set()
    target_tags: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for item in languages:
        if item.supported_pairs is not None:
            pairs.extend(item.supported_pairs)
        if item.source_tags is not None:
            source_tags.update(item.source_tags)
        if item.target_tags is not None:
            target_tags.update(item.target_tags)
    return TranslationLanguageCapabilities(
        source_tags=tuple(sorted(source_tags)) or None,
        target_tags=tuple(sorted(target_tags)) or None,
        supported_pairs=tuple(dict.fromkeys(pairs)) or None,
        open_world_prompting=False,
        tier="provider_claimed",
    )
