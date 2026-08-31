from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp

from . import register
from .base import TranslationCapabilities, TranslationProvider, TranslationRequest, TranslationResult

_LANGUAGE_NAMES = {"ja": "日语", "zh": "中文", "en": "英语", "ko": "韩语"}
# Qwen-MT are dedicated translation models, not chat models. They reject the
# "system" role outright ("Role must be in [user, assistant]") and take their
# instructions through ``translation_options`` instead of a prompt. The model
# name is the only signal we have, because the settings dialog lets the user
# point this generic provider at any endpoint.
_TRANSLATION_ONLY_MODELS = ("qwen-mt",)
_MT_LANGUAGE_NAMES = {"ja": "Japanese", "zh": "Chinese", "en": "English", "ko": "Korean"}


def _is_translation_only(model: str) -> bool:
    return any(marker in model.lower() for marker in _TRANSLATION_ONLY_MODELS)


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
            return TranslationCapabilities(True, True, True, False, 32768)
        return TranslationCapabilities(True, True, True, True, 32768)

    def build_payload(self, request: TranslationRequest) -> dict[str, Any]:
        if self.translation_only:
            return self._build_translation_payload(request)
        system, user = _build_prompt(request)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": float(self.options.get("temperature", 0.3)),
            "max_tokens": int(self.options.get("maxTokens", 256)),
            "stream": False,
        }
        # Vendor extensions belong at the top level of the request body.
        # ``extra_body`` is an OpenAI *Python SDK* convention -- the SDK merges
        # it into the body before sending. We post raw JSON, so nesting it made
        # the server silently ignore the field.
        if "dashscope.aliyuncs.com" in self.base_url:
            payload["enable_thinking"] = bool(self.options.get("enableThinking", False))
        return payload

    def _build_translation_payload(self, request: TranslationRequest) -> dict[str, Any]:
        tm_pairs = int(self.options.get("tmPairs", 4))
        translation_options: dict[str, Any] = {
            "source_lang": _MT_LANGUAGE_NAMES.get(request.meta.source_lang, request.meta.source_lang),
            "target_lang": _MT_LANGUAGE_NAMES.get(request.meta.target_lang, request.meta.target_lang),
            "terms": [{"source": src, "target": dst} for src, dst in request.glossary],
            "tm_list": [{"source": src, "target": dst} for src, dst in request.history[-tm_pairs:]],
        }
        if request.meta.domain:
            translation_options["domains"] = request.meta.domain
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": request.source_text}],
            "stream": False,
            "translation_options": translation_options,
        }

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        _check_request(self.id, self.api_key, request)
        started = time.monotonic()
        timeout = _timeout(self.options, request)
        payload = self.build_payload(request)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.post(f"{self.base_url}/chat/completions", headers=_headers(self.api_key), json=payload) as response:
                data = await _json_response(response)
        text = data["choices"][0]["message"]["content"].strip()
        return TranslationResult(text, self.id, round((time.monotonic() - started) * 1000), data.get("usage"))


def _build_prompt(request: TranslationRequest) -> tuple[str, str]:
    source = _LANGUAGE_NAMES.get(request.meta.source_lang, request.meta.source_lang)
    target = _LANGUAGE_NAMES.get(request.meta.target_lang, request.meta.target_lang)
    glossary = "\n".join(f"{src} => {dst}" for src, dst in request.glossary) or "（无）"
    system = (
        f"你是直播字幕翻译器。把 CURRENT 从{source}译成{target}。\n"
        "规则：\n1. 只输出 CURRENT 的译文，不要输出解释、不要重复 HISTORY。\n"
        "2. HISTORY 只用于理解指代、省略主语和话题，不要翻译它。\n"
        "3. 译文要像直播字幕：简洁、口语、可一眼读完。\n"
        "4. 不要补全说话人没说完的内容，不要添加未表达的事实。\n"
        "5. 人名/专有名词严格遵循术语表。\n6. 只输出译文本身，不加引号、不加前缀。\n"
        f"直播信息：{request.meta.title or ''} / {request.meta.channel or ''} / 领域：{request.meta.domain or ''}\n"
        f"术语表：\n{glossary}"
    )
    history = "\n".join(f"{src} -> {dst}" for src, dst in request.history) or "（无）"
    return system, f"HISTORY:\n{history}\nCURRENT:\n{request.source_text}"


def _check_request(provider_id: str, api_key: str, request: TranslationRequest) -> None:
    if not api_key:
        raise ValueError(f"API key is not configured for provider {provider_id}")
    if request.deadline_monotonic is not None and time.monotonic() >= request.deadline_monotonic:
        raise asyncio.TimeoutError("translation deadline has expired")


def _timeout(options: dict[str, Any], request: TranslationRequest) -> float:
    timeout = float(options.get("timeoutSeconds", 6))
    if request.deadline_monotonic is not None:
        timeout = min(timeout, request.deadline_monotonic - time.monotonic())
    if timeout <= 0:
        raise asyncio.TimeoutError("translation deadline has expired")
    return timeout


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


async def _json_response(response: aiohttp.ClientResponse) -> dict[str, Any]:
    data = await response.json(content_type=None)
    if response.status >= 400:
        message = data.get("error", {}).get("message", str(data)) if isinstance(data, dict) else str(data)
        raise RuntimeError(f"translation HTTP {response.status}: {message}")
    return data
