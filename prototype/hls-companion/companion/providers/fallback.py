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
    validate_translation_pair,
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

    def _eligibility_reason(
        self,
        provider: TranslationProvider,
        request: TranslationRequest,
        now: float,
    ) -> str | None:
        """Why this provider cannot serve THIS cue right now, or None if it can.

        Pure: it reads health and never writes it, which is what keeps a local
        budget or language decision from being recorded as a provider failure.
        It also refuses to predict the network -- being disabled, cooling, over
        the declared input limit, or knowing it cannot translate the pair are all
        facts the chain already has. An unknown or open-world language contract
        stays eligible: this does not probe and does not invent a second health
        model.
        """
        state = self.health[provider.id]
        if state.disabled_reason is not None:
            return "disabled"
        if state.cooldown_until > now:
            return "cooling"
        limit = getattr(provider.capabilities, "max_input_chars", 0) or 0
        if limit > 0 and len(request.source_text) > limit:
            return "input-limit"
        try:
            validate_translation_pair(
                request.meta.source_lang,
                request.meta.target_lang,
                provider.capabilities.language,
            )
        except LanguageNotSupportedError:
            # Declared not to handle this pair. Holding the primary's budget open
            # so that a provider which cannot answer this cue still gets a turn
            # costs the primary time and buys nothing.
            return "unsupported-pair"
        return None

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        # Real failures and local skips are kept apart. Only the first group says
        # anything about a provider, and only it may become an exception cause:
        # folding a budget decision into `errors` made a healthy fallback look
        # like the reason a cue failed, and left `errors[-1]` describing a call
        # that never happened.
        errors: list[tuple[str, BaseException]] = []
        skips: list[tuple[str, str]] = []
        attempted = False
        deadline = request.deadline_monotonic
        now = self.clock()
        # A reservation only means something if some later provider could be tried
        # for THIS cue right now. Reserving for a disabled, cooling, too-small or
        # language-incompatible fallback would shorten the primary's deadline and
        # buy nothing.
        reserve = 0.0
        if self.fallback_reserve_seconds > 0 and any(
            self._eligibility_reason(provider, request, now) is None
            for provider in self.providers[1:]
        ):
            reserve = self.fallback_reserve_seconds
        for index, provider in enumerate(self.providers):
            # Refresh the clock every iteration. A provider call awaited, and a
            # `now` captured before it is what made the old loop skip a fallback
            # whose cooldown had already expired while the primary was running.
            now = self.clock()
            if deadline is not None and deadline - now <= 0:
                # The cue's own budget is gone, so no provider may be STARTED.
                # Checked outside the call's try/except on purpose: this is the
                # chain running out of time, not a provider failing, and nothing
                # may be recorded against a provider that was never called.
                #
                # Note which deadline this is. The primary is called with a LOCAL
                # deadline of `deadline - reserve`; that one expiring must still
                # let the fallback run, which is the entire point of the reserve.
                # Only the original budget expiring stops new attempts.
                raise asyncio.TimeoutError("translation chain deadline has expired")
            reason = self._eligibility_reason(provider, request, now)
            if reason is not None:
                skips.append((_name_of(provider), reason))
                continue
            state = self.health[provider.id]
            if state.cooldown_until:
                # A cooldown window that has passed gives the provider a clean
                # slate. Kept here, next to the call, so exactly one place owns
                # this transition.
                state.cooldown_until = 0.0
                state.consecutive_failures = 0
            provider_request = request
            if index == 0 and reserve > 0 and deadline is not None:
                remaining = deadline - now
                if remaining <= reserve:
                    # Not enough left to cover both. The whole remainder goes to
                    # the providers that can still be tried, because handing it
                    # to the primary is what left the fallback an already-expired
                    # deadline at exactly the boundary the reserve exists for.
                    skips.append((
                        _name_of(provider),
                        f"not called: {remaining:.2f}s left does not cover the "
                        f"{reserve:.2f}s fallback reserve",
                    ))
                    continue
                provider_request = dataclasses.replace(
                    request, deadline_monotonic=deadline - reserve
                )
            attempted = True
            try:
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
            # Nothing was called, so "failed" would be a statement about
            # providers that never ran. The reasons are listed instead.
            detail = "; ".join(f"{name}: {why}" for name, why in skips)
            raise RuntimeError(
                "all translation providers are cooling down or disabled"
                + (f" ({detail})" if detail else "")
            )
        detail = "; ".join(
            [f"{name}: {_describe(error)}" for name, error in errors]
            + [f"{name}: not called ({why})" for name, why in skips]
        )
        exc = RuntimeError(f"all translation providers failed: {detail}")
        raise exc from (errors[-1][1] if errors else None)


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
