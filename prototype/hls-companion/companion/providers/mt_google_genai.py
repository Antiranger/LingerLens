"""Google Gemini GenerateContent translation adapter (kind ``google-genai``).

Re-implemented from the official protocol documentation with plain
``aiohttp`` (no SDK source copied):

* ``POST {baseUrl}/models/{model}:generateContent`` —
  https://ai.google.dev/api/generate-content
* Header: ``x-goog-api-key``.
* ``systemInstruction`` and ``contents[]`` with nested ``parts[].text``.
* Response text is the concatenation of
  ``candidates[0].content.parts[].text``; a safety block
  (``promptFeedback.blockReason`` or candidate ``finishReason`` ``SAFETY``
  etc.), an empty candidate list or empty text is a failure, never a
  successful empty translation.
* ``usageMetadata``: ``promptTokenCount`` INCLUDES
  ``cachedContentTokenCount`` (the pipeline subtracts it for non-cached
  input), ``candidatesTokenCount`` is the output count.
* Errors: Google RPC shape
  ``{"error": {"code": ..., "message": ..., "status": ...}}`` with 401/403
  auth, 429 ``RESOURCE_EXHAUSTED`` and 5xx unavailability mapped onto the
  normalized Provider errors.
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

# finishReason values that mean "no usable translation was generated".
_REFUSAL_FINISH_REASONS = {
    "SAFETY",
    "RECITATION",
    "PROHIBITED_CONTENT",
    "BLOCKLIST",
    "IMAGE_SAFETY",
    "IMAGE_PROHIBITED_CONTENT",
    "ESCALATION",
    "LANGUAGE",
    "SPII",
}


@register("google-genai")
class GoogleGenAITranslationProvider(TranslationProvider):
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
            "systemInstruction": {"parts": [{"text": instruction.system_text}]},
            "contents": [{"role": "user", "parts": [{"text": instruction.user_text}]}],
            "generationConfig": {
                "temperature": float(self.options.get("temperature", 0.3)),
                "maxOutputTokens": translation_output_limit(request, int(self.options.get("maxTokens", 256))),
            },
        }

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        require_api_key(self.id, self.api_key)
        started = time.monotonic()
        data = await post_json(
            f"{self.base_url}/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
            payload=self.build_payload(request),
            timeout=request_timeout(self.options, request),
        )
        text = _extract_text(data)
        if not text:
            raise ProviderRefusalError("google-genai returned no translation text")
        return TranslationResult(text, self.id, round((time.monotonic() - started) * 1000), data.get("usageMetadata"))


def _extract_text(data: dict[str, Any]) -> str:
    """Candidate text; prompt/candidate safety blocks and empty text are failures."""
    feedback = data.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        raise ProviderRefusalError(f"google-genai blocked the prompt: {feedback['blockReason']}")
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return ""
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        return ""
    finish_reason = candidate.get("finishReason")
    if finish_reason in _REFUSAL_FINISH_REASONS:
        raise ProviderRefusalError(f"google-genai candidate was blocked: {finish_reason}")
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        return ""
    return "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict)).strip()
