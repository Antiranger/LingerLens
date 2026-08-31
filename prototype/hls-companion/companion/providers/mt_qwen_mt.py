from __future__ import annotations

import time
from typing import Any

import aiohttp

from . import register
from .base import TranslationCapabilities, TranslationProvider, TranslationRequest, TranslationResult
from .mt_openai_compat import _check_request, _headers, _json_response, _timeout

_LANGUAGE_NAMES = {"ja": "Japanese", "zh": "Chinese", "en": "English", "ko": "Korean"}


@register("qwen-mt")
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
        return TranslationCapabilities(True, True, True, False, 32768)

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        _check_request(self.id, self.api_key, request)
        started = time.monotonic()
        tm_pairs = int(self.options.get("tmPairs", 4))
        translation_options: dict[str, Any] = {
            "source_lang": _LANGUAGE_NAMES.get(request.meta.source_lang, request.meta.source_lang),
            "target_lang": _LANGUAGE_NAMES.get(request.meta.target_lang, request.meta.target_lang),
            "terms": [{"source": src, "target": dst} for src, dst in request.glossary],
            "tm_list": [{"source": src, "target": dst} for src, dst in request.history[-tm_pairs:]],
        }
        if request.meta.domain:
            translation_options["domains"] = request.meta.domain
        # translation_options goes at the TOP LEVEL of the request body.
        # ``extra_body`` is an OpenAI *Python SDK* convention: the SDK merges
        # that dict into the top-level body before sending.  We post raw JSON,
        # so nesting it made the server ignore the field entirely -- the model
        # then guessed, and answered in English or replied conversationally
        # instead of translating.
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": request.source_text}],
            "stream": False,
            "translation_options": translation_options,
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_timeout(self.options, request))) as session:
            async with session.post(f"{self.base_url}/chat/completions", headers=_headers(self.api_key), json=payload) as response:
                data = await _json_response(response)
        text = data["choices"][0]["message"]["content"].strip()
        return TranslationResult(text, self.id, round((time.monotonic() - started) * 1000), data.get("usage"))
