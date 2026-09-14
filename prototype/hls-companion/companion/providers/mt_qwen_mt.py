"""Qwen-MT dedicated translation adapter (kind ``qwen-mt``).

Qwen-MT models are dedicated translation models, not chat models. They take
their instructions through structured ``translation_options`` (English
language names, glossary ``terms``, ``tm_list`` translation memory, optional
``domains``) instead of the shared generic prompt.

Language names are derived from the Ticket 01 canonical catalog
(``companion/languages.py``); the two entries below are the Provider's own
protocol vocabulary for languages that have no catalog primary entry. Pairs
outside the closed contract are rejected BEFORE any HTTP request, so the
FallbackChain can skip to a provider that can represent the cue.
"""

from __future__ import annotations

import time
from typing import Any

from ..languages import LanguageNotSupportedError, display_name, primary_language_name, primary_subtag
from .base import (
    ProviderRefusalError,
    TranslationCapabilities,
    TranslationLanguageCapabilities,
    TranslationProvider,
    TranslationRequest,
    TranslationResult,
)
from .http import post_json, request_timeout, require_api_key

# Qwen-MT language-pair contract (provider-claimed preset). Tags are canonical.
QWEN_MT_LANGUAGE_TAGS: tuple[str, ...] = (
    "zh-Hans", "zh-Hant", "en", "ja", "ko", "fr", "de", "es",
    "pt-PT", "ru", "ar", "it", "th", "vi", "id", "ms",
)
_QWEN_MT_PRIMARY_LANGUAGES: frozenset[str] = frozenset(primary_subtag(tag) for tag in QWEN_MT_LANGUAGE_TAGS)

# Qwen-MT protocol vocabulary for languages with no catalog primary entry
# (the catalog only carries zh-Hans/zh-Hant and pt-PT/pt-BR). Everything else
# comes from the canonical catalog's primary-language names.
_MT_PRIMARY_NAME_OVERRIDES = {"zh": "Chinese", "pt": "Portuguese"}


def mt_language_name(tag: str) -> str:
    """Map a canonical tag to Qwen-MT's English language name at the edge."""
    primary = primary_subtag(tag)
    return _MT_PRIMARY_NAME_OVERRIDES.get(primary) or primary_language_name(tag) or display_name(tag)


def validate_mt_pair(source_lang: str, target_lang: str) -> None:
    """Reject a cue the closed pair contract cannot represent, before any request."""
    if "+" in source_lang:
        raise LanguageNotSupportedError(
            f"qwen-mt cannot translate the mixed-language cue {source_lang}; "
            "a generic LLM provider handles it instead"
        )
    if primary_subtag(source_lang) not in _QWEN_MT_PRIMARY_LANGUAGES:
        raise LanguageNotSupportedError(f"qwen-mt cannot translate {source_lang} -> {target_lang}: unsupported source language")
    if primary_subtag(target_lang) not in _QWEN_MT_PRIMARY_LANGUAGES:
        raise LanguageNotSupportedError(f"qwen-mt cannot translate {source_lang} -> {target_lang}: unsupported target language")


def build_translation_options(request: TranslationRequest, tm_pairs: int) -> dict[str, Any]:
    """Structured translation_options for one request (raises on unsupported pairs)."""
    validate_mt_pair(request.meta.source_lang, request.meta.target_lang)
    translation_options: dict[str, Any] = {
        "source_lang": mt_language_name(request.meta.source_lang),
        "target_lang": mt_language_name(request.meta.target_lang),
        "terms": [{"source": src, "target": dst} for src, dst in request.glossary],
        # Dedicated MT requires actual source/target pairs. Do not invent a
        # translation or send null targets for source-only prompt context.
        "tm_list": [{"source": src, "target": dst} for src, dst in request.history if dst is not None][-tm_pairs:],
    }
    if request.meta.domain:
        translation_options["domains"] = request.meta.domain
    return translation_options


class QwenMTTranslationProvider(TranslationProvider):
    def __init__(self, config: dict[str, Any]):
        self.id = config["id"]
        self.label = config.get("label", self.id)
        self.model = config["model"]
        self.base_url = config["baseUrl"].rstrip("/")
        self.api_key = config.get("_apiKey", config.get("apiKey", ""))
        self.options = config.get("options", {})

    @property
    def capabilities(self) -> TranslationCapabilities:
        # rolling_context is True: the model does consume prior turns, just in
        # its own shape (``tm_list`` translation memory) rather than as chat
        # history.  Declaring False made the pipeline blank ``request.history``
        # before we got here, so tm_list was always empty.
        return TranslationCapabilities(
            True, True, True, False, 32768,
            language=TranslationLanguageCapabilities(
                source_tags=QWEN_MT_LANGUAGE_TAGS,
                target_tags=QWEN_MT_LANGUAGE_TAGS,
                open_world_prompting=False,
                tier="provider_claimed",
            ),
        )

    def build_payload(self, request: TranslationRequest) -> dict[str, Any]:
        tm_pairs = int(self.options.get("tmPairs", 4))
        # translation_options goes at the TOP LEVEL of the request body.
        # ``extra_body`` is an OpenAI *Python SDK* convention: the SDK merges
        # that dict into the top-level body before sending.  We post raw JSON,
        # so nesting it made the server ignore the field entirely -- the model
        # then guessed, and answered in English or replied conversationally
        # instead of translating.
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


def _extract_chat_text(data: dict[str, Any]) -> str:
    """OpenAI chat-completions content, rejecting incomplete completions.

    ``finish_reason`` must be checked: `content_filter` returns text that was
    partially removed and `length` returns text truncated mid-sentence, and both
    used to be published as if they were valid subtitles. The sibling adapters
    already do this (mt_anthropic_messages.py checks ``stop_reason == "refusal"``,
    mt_google_genai.py checks ``finishReason``); this is the shared extractor for
    the OpenAI-compatible and Qwen-MT paths, so the check belongs here.
    """
    try:
        choice = data["choices"][0]
        content = choice["message"].get("content")
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""
    finish_reason = choice.get("finish_reason")
    if finish_reason in {"content_filter", "length"}:
        raise ProviderRefusalError(
            f"incomplete translation: finish_reason={finish_reason}"
        )
    return str(content or "").strip()
