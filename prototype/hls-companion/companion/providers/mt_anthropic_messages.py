"""Anthropic Messages translation adapter (kind ``anthropic-messages``).

Re-implemented from the official protocol documentation with plain
``aiohttp`` (no SDK source copied):

* ``POST {baseUrl}/v1/messages`` — https://platform.claude.com/docs/en/api/messages/create
* Headers: ``x-api-key``, ``anthropic-version: 2023-06-01``.
* Top-level ``system`` parameter; ``messages`` carries only user/assistant
  roles (a ``system`` role message is a 400 ``invalid_request_error``).
* Response text is the concatenation of ``content[]`` blocks of type
  ``text``; ``stop_reason == "refusal"`` or an empty response is a failure,
  never a successful empty translation.
* Usage: ``input_tokens`` (non-cached), ``cache_read_input_tokens``,
  ``cache_creation_input_tokens`` (cache write) and ``output_tokens`` are
  reported separately; the pipeline normalizes them into the shared usage
  contract.
* Errors — https://platform.claude.com/docs/en/api/errors :
  ``{"type": "error", "error": {"type": ..., "message": ...}}`` with 401
  ``authentication_error``, 403 ``permission_error``, 429
  ``rate_limit_error``, 500 ``api_error`` and 529 ``overloaded_error``.
"""

from __future__ import annotations

import time
from typing import Any

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

ANTHROPIC_VERSION = "2023-06-01"


@register("anthropic-messages")
class AnthropicMessagesTranslationProvider(TranslationProvider):
    def __init__(self, config: dict[str, Any]):
        self.id = config["id"]
        self.label = config.get("label", self.id)
        self.model = config["model"]
        self.base_url = config["baseUrl"].rstrip("/")
        self.api_key = config.get("_apiKey", config.get("apiKey", ""))
        self.options = config.get("options", {})

    @property
    def capabilities(self) -> TranslationCapabilities:
        # Generic LLM prompted to translate: any canonical pair best-effort.
        return TranslationCapabilities(
            True, True, True, False, 32768,
            language=TranslationLanguageCapabilities(
                open_world_prompting=True,
                tier="provider_claimed",
            ),
        )

    def build_payload(self, request: TranslationRequest) -> dict[str, Any]:
        instruction = build_translation_instruction(request)
        return {
            "model": self.model,
            "max_tokens": translation_output_limit(request, int(self.options.get("maxTokens", 256))),
            "temperature": float(self.options.get("temperature", 0.3)),
            "system": instruction.system_text,
            "messages": [{"role": "user", "content": instruction.user_text}],
        }

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        require_api_key(self.id, self.api_key)
        started = time.monotonic()
        data = await post_json(
            f"{self.base_url}/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "Content-Type": "application/json",
            },
            payload=self.build_payload(request),
            timeout=request_timeout(self.options, request),
        )
        text = _extract_text(data)
        if not text:
            raise ProviderRefusalError(
                f"anthropic-messages returned no translation text (stop_reason={data.get('stop_reason')!r})"
            )
        return TranslationResult(text, self.id, round((time.monotonic() - started) * 1000), data.get("usage"))


def _extract_text(data: dict[str, Any]) -> str:
    """Concatenate text blocks; a refusal or empty response is a failure."""
    if data.get("stop_reason") == "refusal":
        raise ProviderRefusalError("anthropic-messages refused to generate a translation")
    content = data.get("content")
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()
