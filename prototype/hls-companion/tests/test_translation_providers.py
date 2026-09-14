"""Ticket 03 tests: shared translation prompt contract, Anthropic/Gemini adapters, usage, errors, fallback."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.providers import REGISTRY, create_translation
from companion.providers.base import (
    ASRCapabilities,
    ASRProvider,
    ASRStream,
    LanguageNotSupportedError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderRefusalError,
    ProviderRequestError,
    ProviderUnavailableError,
    SourceLanguagePolicy,
    StreamMeta,
    TranslationCapabilities,
    TranslationProvider,
    TranslationRequest,
    TranslationResult,
)
from companion.providers.fallback import FallbackChain
from companion.providers.mt_anthropic_messages import AnthropicMessagesTranslationProvider
from companion.providers.mt_google_genai import GoogleGenAITranslationProvider
from companion.providers.mt_openai_compat import OpenAICompatibleTranslationProvider
from companion.providers.mt_qwen_mt import QwenMTTranslationProvider
from companion.subtitle_pipeline import SubtitlePipeline, normalize_translation_usage
from companion.subtitle_store import CueStore
from companion.translation_prompt import build_translation_instruction


def make_request(
    source: str = "ja",
    target: str = "zh-Hans",
    source_text: str = "こんにちは",
    **metadata,
) -> TranslationRequest:
    return TranslationRequest(
        source_text=source_text,
        meta=StreamMeta("配信", "チャンネル", "gaming", source, target),
        history=[("前の文", "上一句"), ("次の文", "下一句")],
        glossary=[("宝鐘マリン", "宝钟玛琳")],
        **metadata,
    )


class SharedPromptContractTests(unittest.TestCase):
    """One provider-neutral prompt contract for all generic LLM adapters (spec §5.2)."""

    def test_source_only_context_is_reference_not_another_translation_target(self) -> None:
        request = make_request()
        request.history = [("前の文", "上一句"), ("これは未翻訳です", None)]
        instruction = build_translation_instruction(request)
        self.assertIn("[2] SOURCE ONLY: これは未翻訳です", instruction.user_text)
        self.assertNotIn("-> None", instruction.user_text)
        self.assertIn("不要输出它的翻译", instruction.system_text)
        self.assertIn("CURRENT CHUNK:\nこんにちは", instruction.user_text)
        from companion.providers.mt_qwen_mt import build_translation_options
        options = build_translation_options(request, 4)
        self.assertEqual(options["tm_list"], [{"source": "前の文", "target": "上一句"}])

    def test_shared_prompt_uses_canonical_names_fixed_rules_metadata_glossary_and_rolling_history(self) -> None:
        instruction = build_translation_instruction(make_request())
        system = instruction.system_text
        # Canonical stable English names from the Ticket 01 catalog, not private dictionaries.
        self.assertIn("Japanese", system)
        self.assertIn("Chinese (Simplified)", system)
        # Fixed task rules survive in the system text.
        self.assertIn("你是直播字幕翻译器", system)
        self.assertIn("只翻译 CURRENT", system)
        self.assertIn("只输出目标语言译文", system)
        # Stream metadata and glossary entries are included.
        self.assertIn("配信", system)
        self.assertIn("チャンネル", system)
        self.assertIn("宝鐘マリン => 宝钟玛琳", system)
        # Rolling history and the current cue live in the user text.
        self.assertIn("PREVIOUS CONTEXT (oldest → newest):\n[1] 前の文 -> 上一句\n[2] 次の文 -> 下一句", instruction.user_text)
        self.assertIn("CURRENT CHUNK:\nこんにちは", instruction.user_text)

    def test_continuity_prompt_contract_is_explicit_and_current_only(self) -> None:
        instruction = build_translation_instruction(make_request(
            source="en",
            target="es",
            source_text="because the latency is",
            generation=9,
            chunk_order=4,
            starts_mid_sentence=True,
            ends_mid_sentence=True,
            cut_reason="hard_deadline",
        ))
        system = instruction.system_text
        for rule in (
            "HISTORY 按时间顺序列出 CURRENT 之前的字幕块",
            "只翻译 CURRENT",
            "不重复",
            "不总结",
            "不要猜测或补完尚未出现的下一块内容",
            "保持自然的未完状态",
            "主语指代",
            "人称",
            "时态",
            "语气",
            "礼貌级别",
            "专名和术语",
            "相邻块连读应像一段连续讲话",
            "只输出目标语言译文",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, system)
        self.assertIn("可在 CURRENT 的语义范围内按目标语言自然重排", system)
        self.assertIn("PREVIOUS CONTEXT (oldest → newest):", instruction.user_text)
        self.assertIn("[1] 前の文 -> 上一句", instruction.user_text)
        self.assertIn("[2] 次の文 -> 下一句", instruction.user_text)
        self.assertIn("CURRENT CHUNK:\nbecause the latency is", instruction.user_text)
        self.assertIn("starts_mid_sentence=true", instruction.user_text)
        self.assertIn("ends_mid_sentence=true", instruction.user_text)
        self.assertIn("cut_reason=hard_deadline", instruction.user_text)

    def test_unknown_chunk_position_is_not_misrepresented_as_false(self) -> None:
        instruction = build_translation_instruction(make_request())
        self.assertIn("starts_mid_sentence=unknown", instruction.user_text)
        self.assertIn("ends_mid_sentence=unknown", instruction.user_text)
        self.assertIn("cut_reason=unknown", instruction.user_text)

    def test_mixed_language_cue_is_representable_in_generic_prompt(self) -> None:
        instruction = build_translation_instruction(make_request(source="fr+en"))
        self.assertIn("mixed French and English", instruction.system_text)
        self.assertIn("Chinese (Simplified)", instruction.system_text)

    def test_prompt_language_names_come_from_canonical_catalog(self) -> None:
        instruction = build_translation_instruction(make_request(source="pt-BR", target="zh-Hant"))
        self.assertIn("Portuguese (Brazil)", instruction.system_text)
        self.assertIn("Chinese (Traditional)", instruction.system_text)

    def test_qwen_mt_still_uses_translation_options_not_generic_prompt(self) -> None:
        provider = QwenMTTranslationProvider({
            "id": "qwen",
            "model": "qwen-mt-flash",
            "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "apiKey": "secret",
            "options": {"tmPairs": 2},
        })
        payload = provider.build_payload(make_request())
        options = payload["translation_options"]
        # Qwen-MT keeps structured translation_options with catalog-derived English names.
        self.assertEqual(options["source_lang"], "Japanese")
        self.assertEqual(options["target_lang"], "Chinese")
        self.assertEqual(options["terms"], [{"source": "宝鐘マリン", "target": "宝钟玛琳"}])
        self.assertEqual(
            options["tm_list"],
            [{"source": "前の文", "target": "上一句"}, {"source": "次の文", "target": "下一句"}],
        )
        self.assertEqual(options["domains"], "gaming")
        self.assertNotIn("system", payload)

    def test_qwen_mt_tm_list_preserves_chronological_history(self) -> None:
        provider = QwenMTTranslationProvider({
            "id": "qwen",
            "model": "qwen-mt-flash",
            "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "apiKey": "secret",
            "options": {"tmPairs": 3},
        })
        request = make_request()
        request.history = [("chunk-1", "译1"), ("chunk-2", "译2"), ("chunk-3", "译3")]
        payload = provider.build_payload(request)
        self.assertEqual(
            payload["translation_options"]["tm_list"],
            [
                {"source": "chunk-1", "target": "译1"},
                {"source": "chunk-2", "target": "译2"},
                {"source": "chunk-3", "target": "译3"},
            ],
        )
        self.assertEqual(set(payload), {"model", "messages", "stream", "translation_options"})

    def test_mixed_language_cue_is_rejected_for_qwen_mt_before_any_request(self) -> None:
        provider = QwenMTTranslationProvider({
            "id": "qwen",
            "model": "qwen-mt-flash",
            "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "apiKey": "secret",
            "options": {},
        })
        with self.assertRaisesRegex(LanguageNotSupportedError, "mixed-language"):
            provider.build_payload(make_request(source="fr+en"))
        with self.assertRaisesRegex(LanguageNotSupportedError, "unsupported source language"):
            provider.build_payload(make_request(source="yue", target="zh-Hans"))

    def test_openai_compatible_translation_only_model_keeps_structured_options(self) -> None:
        provider = OpenAICompatibleTranslationProvider({
            "id": "qwen-via-openai",
            "model": "qwen-mt-flash",
            "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "apiKey": "secret",
            "options": {"tmPairs": 1},
        })
        self.assertTrue(provider.translation_only)
        payload = provider.build_payload(make_request())
        options = payload["translation_options"]
        self.assertEqual(options["source_lang"], "Japanese")
        self.assertEqual(options["tm_list"], [{"source": "次の文", "target": "下一句"}])
        # A qwen-mt model reached via the generic kind keeps the closed pair contract.
        self.assertFalse(provider.capabilities.language.open_world_prompting)
        with self.assertRaises(LanguageNotSupportedError):
            provider.build_payload(make_request(source="fr+en"))

    def test_generic_llm_providers_are_open_world_qwen_mt_stays_closed(self) -> None:
        for provider in (
            AnthropicMessagesTranslationProvider({"id": "a", "model": "claude-haiku-4-5", "baseUrl": "https://api.anthropic.com", "apiKey": "k", "options": {}}),
            GoogleGenAITranslationProvider({"id": "g", "model": "gemini-2.5-flash", "baseUrl": "https://generativelanguage.googleapis.com/v1beta", "apiKey": "k", "options": {}}),
            OpenAICompatibleTranslationProvider({"id": "o", "model": "gpt-4.1-mini", "baseUrl": "https://api.openai.com/v1", "apiKey": "k", "options": {}}),
        ):
            language = provider.capabilities.language
            self.assertTrue(language.open_world_prompting)
            self.assertIsNone(language.supported_pairs)
        qwen = QwenMTTranslationProvider({"id": "q", "model": "qwen-mt-flash", "baseUrl": "https://example.invalid/v1", "apiKey": "k", "options": {}})
        self.assertFalse(qwen.capabilities.language.open_world_prompting)


class TranslationProviderKindTests(unittest.TestCase):
    def test_all_translation_kinds_registered(self) -> None:
        for kind in ("openai-compatible", "anthropic-messages", "google-genai"):
            self.assertIn(kind, REGISTRY)
            provider = create_translation({
                "id": "t",
                "kind": kind,
                "model": "m",
                "baseUrl": "https://example.invalid/v1",
                "apiKey": "k",
                "options": {},
            })
            self.assertIsInstance(provider, TranslationProvider)


class FakeHttpServer:
    """Local aiohttp fake of one translation API endpoint."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.responses: asyncio.Queue = asyncio.Queue()
        self.runner: web.AppRunner | None = None
        self.base_url = ""

    async def start(self, path: str, delay_seconds: float = 0.0) -> None:
        async def handler(request: web.Request) -> web.Response:
            self.requests.append({
                "method": request.method,
                "path": request.path,
                "headers": dict(request.headers),
                "json": await request.json(),
            })
            status, payload = await self.responses.get()
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
            return web.json_response(payload, status=status)

        app = web.Application()
        app.router.add_post(path, handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()


ANTHROPIC_OK = {
    "id": "msg_01",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-4-5",
    "content": [{"type": "text", "text": "你好"}],
    "stop_reason": "end_turn",
    "usage": {
        "input_tokens": 25,
        "cache_creation_input_tokens": 120,
        "cache_read_input_tokens": 1050,
        "output_tokens": 12,
    },
}


class AnthropicMessagesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeHttpServer()
        await self.server.start("/v1/messages")

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    def provider(self, api_key: str = "sk-ant-secret", **options):
        return AnthropicMessagesTranslationProvider({
            "id": "claude",
            "model": "claude-haiku-4-5",
            "baseUrl": self.server.base_url,
            "apiKey": api_key,
            "options": {"timeoutSeconds": 4, **options},
        })

    async def test_exact_endpoint_headers_and_payload(self) -> None:
        await self.server.responses.put((200, ANTHROPIC_OK))
        result = await self.provider().translate(make_request())
        self.assertEqual(result.text, "你好")
        self.assertEqual(result.provider_id, "claude")
        request = self.server.requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/v1/messages")
        self.assertEqual(request["headers"]["x-api-key"], "sk-ant-secret")
        self.assertEqual(request["headers"]["anthropic-version"], "2023-06-01")
        body = request["json"]
        self.assertEqual(body["model"], "claude-haiku-4-5")
        self.assertIsInstance(body["max_tokens"], int)
        # Top-level system string with the shared prompt; messages only user.
        self.assertIn("Japanese", body["system"])
        self.assertEqual([m["role"] for m in body["messages"]], ["user"])
        self.assertIn("CURRENT CHUNK:\nこんにちは", body["messages"][0]["content"])

    async def test_usage_is_mapped_with_cache_read_and_cache_write(self) -> None:
        await self.server.responses.put((200, ANTHROPIC_OK))
        result = await self.provider().translate(make_request())
        normalized = normalize_translation_usage(result.usage)
        self.assertIsNotNone(normalized)
        assert normalized is not None
        # Anthropic: input_tokens is non-cached; cache read/write are separate.
        self.assertEqual(normalized["nonCachedInputTokens"], 25)
        self.assertEqual(normalized["cachedInputTokens"], 1050)
        self.assertEqual(normalized["cacheWriteInputTokens"], 120)
        self.assertEqual(normalized["outputTokens"], 12)
        self.assertEqual(normalized["totalTokens"], 1207)

    async def test_error_shapes_map_to_normalized_errors(self) -> None:
        cases = [
            (401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}, ProviderAuthError),
            (403, {"type": "error", "error": {"type": "permission_error", "message": "no access"}}, ProviderAuthError),
            (400, {"type": "error", "error": {"type": "invalid_request_error", "message": "bad model"}}, ProviderRequestError),
            (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}, ProviderRateLimitError),
            (500, {"type": "error", "error": {"type": "api_error", "message": "boom"}}, ProviderUnavailableError),
            (529, {"type": "error", "error": {"type": "overloaded_error", "message": "overloaded"}}, ProviderUnavailableError),
        ]
        for status, payload, expected in cases:
            await self.server.responses.put((status, payload))
            with self.assertRaises(expected, msg=f"status {status}"):
                await self.provider().translate(make_request())

    async def test_refusal_stop_reason_and_empty_content_are_failures(self) -> None:
        refusal = {**ANTHROPIC_OK, "stop_reason": "refusal"}
        await self.server.responses.put((200, refusal))
        with self.assertRaises(ProviderRefusalError):
            await self.provider().translate(make_request())
        empty = {**ANTHROPIC_OK, "content": []}
        await self.server.responses.put((200, empty))
        with self.assertRaises(ProviderRefusalError):
            await self.provider().translate(make_request())

    async def test_missing_api_key_fails_before_any_request(self) -> None:
        with self.assertRaises(ProviderAuthError):
            await self.provider(api_key="").translate(make_request())
        self.assertEqual(self.server.requests, [])


GEMINI_OK = {
    "candidates": [
        {
            "content": {"parts": [{"text": "你好"}], "role": "model"},
            "finishReason": "STOP",
            "index": 0,
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 1120,
        "cachedContentTokenCount": 1024,
        "candidatesTokenCount": 18,
        "totalTokenCount": 1138,
    },
    "modelVersion": "gemini-2.5-flash",
}


class GoogleGenAITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeHttpServer()
        await self.server.start("/v1beta/models/gemini-2.5-flash:generateContent")

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    def provider(self, api_key: str = "gemini-secret", **options):
        return GoogleGenAITranslationProvider({
            "id": "gemini",
            "model": "gemini-2.5-flash",
            "baseUrl": self.server.base_url + "/v1beta",
            "apiKey": api_key,
            "options": {"timeoutSeconds": 4, **options},
        })

    async def test_exact_endpoint_headers_and_payload(self) -> None:
        await self.server.responses.put((200, GEMINI_OK))
        result = await self.provider().translate(make_request())
        self.assertEqual(result.text, "你好")
        request = self.server.requests[0]
        self.assertEqual(request["path"], "/v1beta/models/gemini-2.5-flash:generateContent")
        self.assertEqual(request["headers"]["x-goog-api-key"], "gemini-secret")
        body = request["json"]
        self.assertIn("Japanese", body["systemInstruction"]["parts"][0]["text"])
        self.assertEqual(body["contents"][0]["role"], "user")
        self.assertIn("CURRENT CHUNK:\nこんにちは", body["contents"][0]["parts"][0]["text"])
        self.assertIn("maxOutputTokens", body["generationConfig"])

    async def test_usage_metadata_is_mapped(self) -> None:
        await self.server.responses.put((200, GEMINI_OK))
        result = await self.provider().translate(make_request())
        normalized = normalize_translation_usage(result.usage)
        self.assertIsNotNone(normalized)
        assert normalized is not None
        # promptTokenCount includes cached content; non-cached input subtracts it.
        self.assertEqual(normalized["nonCachedInputTokens"], 96)
        self.assertEqual(normalized["cachedInputTokens"], 1024)
        self.assertEqual(normalized["cacheWriteInputTokens"], 0)
        self.assertEqual(normalized["outputTokens"], 18)
        self.assertEqual(normalized["totalTokens"], 1138)

    async def test_error_shapes_map_to_normalized_errors(self) -> None:
        cases = [
            (400, {"error": {"code": 400, "message": "API key not valid", "status": "INVALID_ARGUMENT"}}, ProviderRequestError),
            (401, {"error": {"code": 401, "message": "unauthenticated", "status": "UNAUTHENTICATED"}}, ProviderAuthError),
            (403, {"error": {"code": 403, "message": "forbidden", "status": "PERMISSION_DENIED"}}, ProviderAuthError),
            (429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}, ProviderRateLimitError),
            (500, {"error": {"code": 500, "message": "internal", "status": "INTERNAL"}}, ProviderUnavailableError),
            (503, {"error": {"code": 503, "message": "unavailable", "status": "UNAVAILABLE"}}, ProviderUnavailableError),
        ]
        for status, payload, expected in cases:
            await self.server.responses.put((status, payload))
            with self.assertRaises(expected, msg=f"status {status}"):
                await self.provider().translate(make_request())

    async def test_prompt_safety_block_candidate_safety_and_empty_candidates_are_failures(self) -> None:
        blocked = {"promptFeedback": {"blockReason": "SAFETY", "safetyRatings": []}}
        await self.server.responses.put((200, blocked))
        with self.assertRaises(ProviderRefusalError):
            await self.provider().translate(make_request())
        safety_candidate = {
            "candidates": [{"finishReason": "SAFETY", "safetyRatings": [], "index": 0}],
            "usageMetadata": GEMINI_OK["usageMetadata"],
        }
        await self.server.responses.put((200, safety_candidate))
        with self.assertRaises(ProviderRefusalError):
            await self.provider().translate(make_request())
        empty = {"candidates": [], "usageMetadata": GEMINI_OK["usageMetadata"]}
        await self.server.responses.put((200, empty))
        with self.assertRaises(ProviderRefusalError):
            await self.provider().translate(make_request())

    async def test_missing_api_key_fails_before_any_request(self) -> None:
        with self.assertRaises(ProviderAuthError):
            await self.provider(api_key="").translate(make_request())
        self.assertEqual(self.server.requests, [])


class UsageNormalizationTests(unittest.TestCase):
    def test_openai_compatible_shape(self) -> None:
        normalized = normalize_translation_usage({
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "prompt_tokens_details": {"cached_tokens": 400},
        })
        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertEqual(normalized["nonCachedInputTokens"], 600)
        self.assertEqual(normalized["cachedInputTokens"], 400)
        self.assertEqual(normalized["cacheWriteInputTokens"], 0)
        self.assertEqual(normalized["outputTokens"], 200)

    def test_anthropic_shape_never_assumes_input_tokens_includes_cache(self) -> None:
        normalized = normalize_translation_usage({
            "input_tokens": 10,
            "cache_read_input_tokens": 500,
            "cache_creation_input_tokens": 60,
            "output_tokens": 5,
        })
        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertEqual(
            (normalized["nonCachedInputTokens"], normalized["cachedInputTokens"], normalized["cacheWriteInputTokens"], normalized["outputTokens"]),
            (10, 500, 60, 5),
        )

    def test_missing_or_inconsistent_usage_stays_unavailable(self) -> None:
        self.assertIsNone(normalize_translation_usage(None))
        self.assertIsNone(normalize_translation_usage({"prompt_tokens": 10}))
        self.assertIsNone(normalize_translation_usage({"promptTokenCount": 10, "cachedContentTokenCount": 20, "candidatesTokenCount": 1}))


class FakeASR(ASRProvider):
    id = "fake-asr"
    label = "Fake ASR"
    model = "fake"
    price_per_second_cny = None

    @property
    def capabilities(self) -> ASRCapabilities:
        return ASRCapabilities(True, True, True, True, False, False, False, ("ja",), (16000,))

    async def stream(self, *, policy: SourceLanguagePolicy, sample_rate: int, hotwords: list[str], context: list[str]) -> ASRStream:
        raise NotImplementedError


class ScriptedTranslation(TranslationProvider):
    def __init__(self, provider_id: str, outcomes: list[object], usage: dict | None = None):
        self.id = provider_id
        self.label = provider_id
        self.model = provider_id
        self.outcomes = outcomes
        self.usage = usage
        self.calls = 0

    @property
    def capabilities(self) -> TranslationCapabilities:
        return TranslationCapabilities(True, True, True, False, 1000)

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        del request
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return TranslationResult(str(outcome), self.id, 1, self.usage)


class CacheWriteBillingTests(unittest.IsolatedAsyncioTestCase):
    def pipeline(self, pricing: dict) -> SubtitlePipeline:
        return SubtitlePipeline(
            asr_provider=FakeASR(),
            translation_pricing_by_provider=pricing,
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "ja", "zh-Hans"),
        )

    async def test_cache_write_tokens_are_billed_at_the_cache_write_price(self) -> None:
        pipeline = self.pipeline({
            "claude": {"input": 2.0, "cachedInput": 0.2, "cacheWrite": 2.5, "output": 10.0},
        })
        pipeline._record_translation_usage("claude", {
            "input_tokens": 100,
            "cache_read_input_tokens": 400,
            "cache_creation_input_tokens": 200,
            "output_tokens": 50,
        })
        status = pipeline.status()
        usage = status["translationUsage"]
        self.assertEqual(usage["cacheWriteInputTokens"], 200)
        # 100*2 + 400*0.2 + 200*2.5 + 50*10 = 1280 / 1e6
        self.assertEqual(status["translationEstimatedCostCny"], 0.00128)
        self.assertIsNone(status["translationEstimateReason"])

    async def test_cache_write_tokens_without_a_price_stay_unavailable_not_zero(self) -> None:
        pipeline = self.pipeline({
            "claude": {"input": 2.0, "cachedInput": 0.2, "output": 10.0},
        })
        pipeline._record_translation_usage("claude", {
            "input_tokens": 100,
            "cache_read_input_tokens": 400,
            "cache_creation_input_tokens": 200,
            "output_tokens": 50,
        })
        status = pipeline.status()
        self.assertIsNone(status["translationEstimatedCostCny"])
        self.assertIn("pricing incomplete", status["translationEstimateReason"])

    async def test_no_cache_write_tokens_needs_no_cache_write_price(self) -> None:
        pipeline = self.pipeline({
            "gemini": {"input": 1.0, "cachedInput": 0.1, "output": 4.0},
        })
        pipeline._record_translation_usage("gemini", {
            "promptTokenCount": 1100,
            "cachedContentTokenCount": 1000,
            "candidatesTokenCount": 10,
        })
        status = pipeline.status()
        self.assertEqual(status["translationUsage"]["nonCachedInputTokens"], 100)
        # 100*1 + 1000*0.1 + 10*4 = 240 / 1e6
        self.assertEqual(status["translationEstimatedCostCny"], 0.00024)


class FallbackDeterminismTests(unittest.IsolatedAsyncioTestCase):
    async def test_primary_deadline_reserves_budget_for_fallback(self) -> None:
        now = [10.0]

        class DeadlineProvider(ScriptedTranslation):
            def __init__(self, provider_id: str, outcome: object):
                super().__init__(provider_id, [outcome])
                self.deadlines: list[float | None] = []

            async def translate(self, request):
                self.deadlines.append(request.deadline_monotonic)
                return await super().translate(request)

        first = DeadlineProvider("first", asyncio.TimeoutError("slow"))
        second = DeadlineProvider("second", "fallback")
        chain = FallbackChain(
            [first, second],
            fallback_reserve_seconds=2.0,
            clock=lambda: now[0],
        )
        request = make_request()
        request.deadline_monotonic = 16.0
        result = await chain.translate(request)
        self.assertEqual(result.provider_id, "second")
        self.assertEqual(first.deadlines, [14.0])
        self.assertEqual(second.deadlines, [16.0])

    async def test_auth_and_request_errors_disable_the_provider_for_the_session(self) -> None:
        now = [10.0]
        first = ScriptedTranslation("first", [ProviderAuthError("401 bad key"), "recovered"])
        second = ScriptedTranslation("second", ["fallback", "fallback-again"])
        chain = FallbackChain([first, second], failure_threshold=3, cooldown_seconds=60, clock=lambda: now[0])
        self.assertEqual((await chain.translate(make_request())).text, "fallback")
        self.assertEqual((await chain.translate(make_request())).text, "fallback-again")
        # The auth failure marked the provider misconfigured: no retry per cue.
        self.assertEqual(first.calls, 1)
        self.assertIsNotNone(chain.health["first"].disabled_reason)
        self.assertIn("401", chain.health["first"].disabled_reason or "")

    async def test_language_pair_rejection_falls_back_and_disables_qwen_mt(self) -> None:
        first = ScriptedTranslation("qwen", [LanguageNotSupportedError("qwen-mt cannot translate the mixed-language cue fr+en")])
        second = ScriptedTranslation("claude", ["translated"])
        chain = FallbackChain([first, second], failure_threshold=1, cooldown_seconds=60)
        result = await chain.translate(make_request(source="fr+en"))
        self.assertEqual((result.text, result.provider_id), ("translated", "claude"))
        self.assertIsNotNone(chain.health["qwen"].disabled_reason)

    async def test_rate_limit_falls_back_immediately_and_cools_down(self) -> None:
        now = [10.0]
        first = ScriptedTranslation("first", [ProviderRateLimitError("429"), "recovered"])
        second = ScriptedTranslation("second", ["fallback", "still fallback"])
        chain = FallbackChain([first, second], failure_threshold=3, cooldown_seconds=60, clock=lambda: now[0])
        self.assertEqual((await chain.translate(make_request())).text, "fallback")
        # 429 cools immediately even below the failure threshold.
        self.assertEqual((await chain.translate(make_request())).text, "still fallback")
        self.assertEqual(first.calls, 1)

    async def test_unavailable_and_timeout_count_toward_the_failure_threshold(self) -> None:
        now = [10.0]
        first = ScriptedTranslation("first", [ProviderUnavailableError("503"), asyncio.TimeoutError(), "recovered"])
        second = ScriptedTranslation("second", ["fb1", "fb2", "fb3"])
        chain = FallbackChain([first, second], failure_threshold=2, cooldown_seconds=60, clock=lambda: now[0])
        self.assertEqual((await chain.translate(make_request())).text, "fb1")
        self.assertEqual((await chain.translate(make_request())).text, "fb2")
        self.assertEqual(first.calls, 2)  # cooled at threshold 2
        now[0] = 71.0
        self.assertEqual((await chain.translate(make_request())).text, "recovered")

    async def test_all_providers_disabled_reports_not_attempted(self) -> None:
        first = ScriptedTranslation("first", [ProviderAuthError("401")])
        chain = FallbackChain([first], failure_threshold=1, cooldown_seconds=60)
        with self.assertRaisesRegex(RuntimeError, "all translation providers failed"):
            await chain.translate(make_request())
        with self.assertRaisesRegex(RuntimeError, "cooling down or disabled"):
            await chain.translate(make_request())


class FakeStream(ASRStream):
    async def push_pcm(self, chunk: bytes, pcm_offset: float) -> None: ...
    async def flush(self) -> None: ...
    def __aiter__(self):
        async def events():
            if False:
                yield None
        return events()
    async def aclose(self) -> None: ...


class PipelineIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Claude/Gemini fake APIs translate through the existing Pipeline with usage attribution."""

    async def asyncSetUp(self) -> None:
        self.server = FakeHttpServer()
        await self.server.start("/v1/messages")

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    async def test_pipeline_translates_through_chain_and_attributes_usage_to_actual_fallback_provider(self) -> None:
        await self.server.responses.put((200, ANTHROPIC_OK))
        failing = ScriptedTranslation("primary", [ProviderUnavailableError("503 down")])
        claude = AnthropicMessagesTranslationProvider({
            "id": "claude",
            "model": "claude-haiku-4-5",
            "baseUrl": self.server.base_url,
            "apiKey": "sk-ant-secret",
            "options": {"timeoutSeconds": 4},
        })
        chain = FallbackChain([failing, claude], failure_threshold=3)
        pipeline = SubtitlePipeline(
            asr_provider=FakeASR(),
            translation_provider=chain,
            translation_pricing_by_provider={
                "claude": {"input": 2.0, "cachedInput": 0.2, "cacheWrite": 2.5, "output": 10.0},
            },
            cue_store=CueStore(),
            meta=StreamMeta(None, None, None, "ja", "zh-Hans"),
        )
        cue = pipeline.store.add(t_start=0.0, t_end=1.0, hold=1.2, src="こんにちは", lang="ja", timing_source="asr")
        pipeline._audio_end_walls[cue.id] = pipeline.wall_clock()
        pipeline._enqueue_translation(cue)
        pipeline._running = True
        worker = asyncio.create_task(pipeline._translation_worker())
        await asyncio.wait_for(pipeline._translation_queue.join(), 5)
        pipeline._running = False
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

        stored = pipeline.store.get(cue.id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.zh, "你好")
        self.assertEqual(stored.state, "done")
        status = pipeline.status()
        by_provider = status["translationUsage"]["byProvider"]
        # Usage is attributed to the actual fallback provider, not the chain or primary.
        self.assertIn("claude", by_provider)
        self.assertNotIn("primary", by_provider)
        self.assertEqual(by_provider["claude"]["cacheWriteInputTokens"], 120)
        # 25*2 + 1050*0.2 + 120*2.5 + 12*10 = 680 / 1e6 CNY.
        self.assertEqual(status["translationEstimatedCostCny"], 0.00068)

    async def test_pipeline_translates_through_gemini_fake_api(self) -> None:
        gemini_server = FakeHttpServer()
        await gemini_server.start("/v1beta/models/gemini-2.5-flash:generateContent")
        try:
            await gemini_server.responses.put((200, GEMINI_OK))
            gemini = GoogleGenAITranslationProvider({
                "id": "gemini",
                "model": "gemini-2.5-flash",
                "baseUrl": gemini_server.base_url + "/v1beta",
                "apiKey": "gemini-secret",
                "options": {"timeoutSeconds": 4},
            })
            pipeline = SubtitlePipeline(
                asr_provider=FakeASR(),
                translation_provider=gemini,
                translation_pricing_by_provider={
                    "gemini": {"input": 1.0, "cachedInput": 0.1, "output": 4.0},
                },
                cue_store=CueStore(),
                meta=StreamMeta(None, None, None, "ja", "zh-Hans"),
            )
            cue = pipeline.store.add(t_start=0.0, t_end=1.0, hold=1.2, src="こんにちは", lang="ja", timing_source="asr")
            pipeline._audio_end_walls[cue.id] = pipeline.wall_clock()
            pipeline._enqueue_translation(cue)
            pipeline._running = True
            worker = asyncio.create_task(pipeline._translation_worker())
            await asyncio.wait_for(pipeline._translation_queue.join(), 5)
            pipeline._running = False
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

            stored = pipeline.store.get(cue.id)
            self.assertIsNotNone(stored)
            assert stored is not None
            self.assertEqual((stored.zh, stored.state), ("你好", "done"))
            status = pipeline.status()
            by_provider = status["translationUsage"]["byProvider"]
            self.assertEqual(by_provider["gemini"]["nonCachedInputTokens"], 96)
            self.assertEqual(by_provider["gemini"]["cachedInputTokens"], 1024)
            # 96*1 + 1024*0.1 + 18*4 = 270.4 / 1e6 CNY.
            self.assertEqual(status["translationEstimatedCostCny"], 0.0002704)
        finally:
            await gemini_server.stop()


if __name__ == "__main__":
    unittest.main()
