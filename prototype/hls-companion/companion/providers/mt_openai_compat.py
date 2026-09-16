from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

from . import register
from ..translation_prompt import build_translation_instruction, translation_output_limit
from .base import (
    ProviderRefusalError,
    TranslationCapabilities,
    TranslationLanguageCapabilities,
    TranslationProvider,
    TranslationRequest,
    TranslationResult,
)
from .http import post_json, request_timeout, require_api_key
from .mt_qwen_mt import QWEN_MT_LANGUAGE_TAGS, _extract_chat_text, build_translation_options

# Qwen-MT are dedicated translation models, not chat models. They reject the
# "system" role outright ("Role must be in [user, assistant]") and take their
# instructions through ``translation_options`` instead of a prompt. The model
# name is the only signal we have, because the settings dialog lets the user
# point this generic provider at any endpoint.
_TRANSLATION_ONLY_MODELS = ("qwen-mt",)

# DashScope serves the same models from a China and an international host, and
# only one of them used to be recognised.
_DASHSCOPE_HOSTS = frozenset({
    "dashscope.aliyuncs.com",
    "dashscope-intl.aliyuncs.com",
})


def _is_dashscope_host(base_url: str) -> bool:
    """Whether ``base_url`` targets a DashScope endpoint (host match only)."""
    host = (urlparse(base_url).hostname or "").lower()
    return host in _DASHSCOPE_HOSTS


def _is_translation_only(model: str) -> bool:
    return any(marker in model.lower() for marker in _TRANSLATION_ONLY_MODELS)


def _build_prompt(request: TranslationRequest) -> tuple[str, str]:
    """The shared provider-neutral prompt as a (system, user) tuple."""
    instruction = build_translation_instruction(request)
    return instruction.system_text, instruction.user_text


@register("openai-compatible")
class OpenAICompatibleTranslationProvider(TranslationProvider):
    def __init__(self, config: dict[str, Any]):
        self.id = config["id"]
        self.label = config.get("label", self.id)
        self.model = config["model"]
        self.base_url = config["baseUrl"].rstrip("/")
        self.api_key = config.get("_apiKey", config.get("apiKey", ""))
        self.options = config.get("options", {})
        self.translation_only = _is_translation_only(self.model)

    @property
    def capabilities(self) -> TranslationCapabilities:
        # Translation-only models take history as tm_list, not as chat turns,
        # and have no room for a free-form glossary prompt or JSON output.
        if self.translation_only:
            return TranslationCapabilities(
                True, True, True, False, 32768,
                language=TranslationLanguageCapabilities(
                    source_tags=QWEN_MT_LANGUAGE_TAGS,
                    target_tags=QWEN_MT_LANGUAGE_TAGS,
                    open_world_prompting=False,
                    tier="provider_claimed",
                ),
            )
        # A generic chat model prompted to translate: any canonical pair is
        # accepted best-effort; per-pair quality is never implied.
        return TranslationCapabilities(
            True, True, True, True, 32768,
            language=TranslationLanguageCapabilities(
                open_world_prompting=True,
                tier="provider_claimed",
            ),
        )

    def build_payload(self, request: TranslationRequest) -> dict[str, Any]:
        if self.translation_only:
            return self._build_translation_payload(request)
        system, user = _build_prompt(request)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": float(self.options.get("temperature", 0.3)),
            "max_tokens": translation_output_limit(request, int(self.options.get("maxTokens", 256))),
            "stream": False,
        }
        # Vendor extensions belong at the top level of the request body.
        # ``extra_body`` is an OpenAI *Python SDK* convention -- the SDK merges
        # it into the body before sending. We post raw JSON, so nesting it made
        # the server silently ignore the field.
        #
        # Match the HOST, not a substring of the whole URL. The previous
        # `"dashscope.aliyuncs.com" in self.base_url` test missed the
        # international endpoint (dashscope-intl.aliyuncs.com), where a Qwen3
        # reasoning model then spent max_tokens on thinking and returned either
        # empty content (counted as a Provider failure) or its reasoning text as
        # the subtitle. It also accepted unrelated URLs that merely contained
        # that text, sending a vendor field a strict server answers 400 to.
        if _is_dashscope_host(self.base_url):
            payload["enable_thinking"] = bool(self.options.get("enableThinking", False))
        # Thinking level, for endpoints that expose one. Sent ONLY when the
        # provider config sets it: a server that does not know the field may
        # answer 400, and 400 is classified as ProviderRequestError, which
        # disables that provider for the rest of the session. So the field is an
        # opt-in the operator declares by configuring it, never a guess.
        #
        # Measured 2026-09-16 against the configured fallback (deepseek-flash,
        # 5 repeats per arm, real cue, 25s budget so nothing was cut by the
        # clock -- only the thinking differed):
        #     (not sent)                    1.55s   185 reasoning tokens
        #     reasoning_effort="none"       0.92s     0
        #     thinking={"type":"disabled"}  0.69s     0
        #     reasoning_effort="low"        2.16s   303   <- MORE than default
        #     reasoning_effort="minimal"    2.19s   324   <- MORE than default
        #     reasoning={"effort":"low"}    1.56s   215   <- ignored
        #     enable_thinking=false         1.49s   189   <- ignored
        # That endpoint honours "none" but NOT the OpenAI intuition that a lower
        # level thinks less: "low" and "minimal" both reasoned more than sending
        # nothing did. The value is therefore passed through verbatim -- nothing
        # here maps a friendly name onto a vendor value, because the mapping is
        # not the same everywhere and a wrong guess is invisible.
        reasoning_effort = str(self.options.get("reasoningEffort") or "").strip()
        if reasoning_effort and reasoning_effort.lower() != "default":
            payload["reasoning_effort"] = reasoning_effort
        return payload

    def _build_translation_payload(self, request: TranslationRequest) -> dict[str, Any]:
        tm_pairs = int(self.options.get("tmPairs", 4))
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": request.source_text}],
            "stream": False,
            "translation_options": build_translation_options(request, tm_pairs),
        }

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        require_api_key(self.id, self.api_key)
        started = time.monotonic()
        data = await post_json(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            payload=self.build_payload(request),
            timeout=request_timeout(self.options, request),
        )
        text = _extract_chat_text(data)
        if not text:
            raise ProviderRefusalError(f"{self.id} returned an empty translation")
        return TranslationResult(text, self.id, round((time.monotonic() - started) * 1000), data.get("usage"))
