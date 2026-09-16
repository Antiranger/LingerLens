"""Regression tests for translation-adapter hardening.

Three defects found in the audit, each with the code path it came from:

1. ``_extract_chat_text`` (mt_qwen_mt.py) never inspected ``finish_reason``, so a
   ``content_filter`` response (text partially removed) or a ``length`` response
   (text truncated mid-sentence) was published as a valid subtitle. The sibling
   adapters already checked their equivalents (mt_anthropic_messages.py
   ``stop_reason == "refusal"``, mt_google_genai.py ``finishReason``).

2. ``TranslationCapabilities.max_input_chars`` was declared (base.py), combined
   with ``min()`` in FallbackChain.capabilities (fallback.py:91) and then never
   read anywhere. An over-long cue reached the Provider, returned a 400, and was
   classified as ProviderRequestError -- which disables that Provider for the
   whole session (fallback.py). With the default single-Provider configuration
   one long sentence ended all subtitles until restart.

3. ``enable_thinking`` was gated on ``"dashscope.aliyuncs.com" in base_url``
   (mt_openai_compat.py), which missed the international host and matched
   unrelated URLs that merely contained that text.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.providers.base import (  # noqa: E402
    ProviderRequestError,
    ProviderRefusalError,
    StreamMeta,
    TranslationCapabilities,
    TranslationProvider,
    TranslationRequest,
    TranslationResult,
)
from companion.providers.fallback import FallbackChain  # noqa: E402
from companion.providers.mt_openai_compat import (  # noqa: E402
    OpenAICompatibleTranslationProvider,
    _is_dashscope_host,
)
from companion.providers.mt_qwen_mt import _extract_chat_text  # noqa: E402


def chat_response(content: str | None, finish_reason: str | None = None) -> dict:
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ]
    }


class FinishReasonTests(unittest.TestCase):
    def test_normal_completion_is_returned(self) -> None:
        self.assertEqual(_extract_chat_text(chat_response("你好", "stop")), "你好")
        # A missing finish_reason must stay acceptable: not every compatible
        # server sends one, and rejecting it would break working setups.
        self.assertEqual(_extract_chat_text(chat_response("你好")), "你好")

    def test_truncated_completion_is_rejected(self) -> None:
        with self.assertRaises(ProviderRefusalError) as ctx:
            _extract_chat_text(chat_response("这是一句被截断的", "length"))
        self.assertIn("length", str(ctx.exception))

    def test_filtered_completion_is_rejected(self) -> None:
        with self.assertRaises(ProviderRefusalError) as ctx:
            _extract_chat_text(chat_response("部分内容已被移除", "content_filter"))
        self.assertIn("content_filter", str(ctx.exception))

    def test_malformed_response_is_empty_not_an_exception(self) -> None:
        self.assertEqual(_extract_chat_text({}), "")
        self.assertEqual(_extract_chat_text({"choices": []}), "")
        self.assertEqual(_extract_chat_text(chat_response(None, "stop")), "")


class DashScopeHostTests(unittest.TestCase):
    def test_both_regional_hosts_are_recognised(self) -> None:
        self.assertTrue(_is_dashscope_host("https://dashscope.aliyuncs.com/compatible-mode/v1"))
        self.assertTrue(_is_dashscope_host("https://dashscope-intl.aliyuncs.com/compatible-mode/v1"))

    def test_lookalike_and_unrelated_urls_are_rejected(self) -> None:
        # The old substring test accepted every one of these.
        self.assertFalse(_is_dashscope_host("https://example.com/dashscope.aliyuncs.com/v1"))
        self.assertFalse(_is_dashscope_host("https://evil.example/?u=dashscope.aliyuncs.com"))
        self.assertFalse(_is_dashscope_host("https://api.openai.com/v1"))
        self.assertFalse(_is_dashscope_host(""))
        self.assertFalse(_is_dashscope_host("not a url"))


class _FixedProvider(TranslationProvider):
    def __init__(self, provider_id: str, *, limit: int, calls: list[str]) -> None:
        self.id = provider_id
        self.label = provider_id
        self.model = provider_id
        self._limit = limit
        self._calls = calls

    @property
    def capabilities(self) -> TranslationCapabilities:
        return TranslationCapabilities(True, True, True, False, self._limit)

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        self._calls.append(request.source_text)
        return TranslationResult("translated:" + request.source_text, self.id, 1, None)


def make_request(text: str) -> TranslationRequest:
    return TranslationRequest(
        source_text=text,
        meta=StreamMeta(None, None, None, "ja", "zh-Hans"),
        history=[],
        glossary=[],
    )


class ReasoningEffortTests(unittest.TestCase):
    """The thinking level is opt-in, verbatim, and never guessed.

    Measured 2026-09-16 against the configured DeepSeek fallback, 5 repeats per
    arm: sending nothing reasoned 185 tokens and took 1.55s; "none" reasoned 0
    and took 0.92s; but "low" and "minimal" reasoned MORE than sending nothing
    (303 and 324 tokens, 2.16s and 2.19s). So the adapter must not map a friendly
    name onto a vendor value -- it must send what it was given, or nothing.
    """

    def payload(self, options: dict) -> dict:
        provider = OpenAICompatibleTranslationProvider(
            {
                "id": "mt",
                "label": "mt",
                "kind": "openai-compatible",
                "model": "m",
                "baseUrl": "https://api.deepseek.com/v1",
                "apiKey": "k",
                "options": options,
            }
        )
        return provider.build_payload(make_request("テスト"))

    def test_nothing_is_sent_by_default(self) -> None:
        self.assertNotIn("reasoning_effort", self.payload({}))

    def test_prefixed_dashscope_vendors_are_not_sent_a_foreign_field(self) -> None:
        # The DashScope host branch owns enable_thinking; reasoning_effort must not
        # ride along to a server that was never asked to accept it.
        self.assertNotIn("reasoning_effort", self.payload({"enableThinking": True}))

    def test_a_configured_level_is_passed_through_verbatim(self) -> None:
        self.assertEqual(self.payload({"reasoningEffort": "none"})["reasoning_effort"], "none")
        self.assertEqual(self.payload({"reasoningEffort": "low"})["reasoning_effort"], "low")

    def test_the_default_keyword_means_send_nothing(self) -> None:
        for value in ("default", "DEFAULT", "  ", ""):
            self.assertNotIn("reasoning_effort", self.payload({"reasoningEffort": value}))


class MaxInputCharsTests(unittest.IsolatedAsyncioTestCase):
    async def test_over_long_cue_is_skipped_without_calling_the_provider(self) -> None:
        calls: list[str] = []
        chain = FallbackChain([_FixedProvider("mt", limit=10, calls=calls)])
        # With every provider skipped the chain reports that nothing served the
        # cue; the point is that the Provider itself was never invoked.
        with self.assertRaises(RuntimeError):
            await chain.translate(make_request("x" * 11))
        self.assertEqual(calls, [], "the provider must not be called at all")

    async def test_a_cue_within_the_limit_still_translates(self) -> None:
        calls: list[str] = []
        chain = FallbackChain([_FixedProvider("mt", limit=10, calls=calls)])
        result = await chain.translate(make_request("1234567890"))
        self.assertEqual(result.text, "translated:1234567890")
        self.assertEqual(len(calls), 1)

    async def test_over_long_cue_does_not_disable_the_provider_for_the_session(self) -> None:
        """The regression: a 400 used to disable the Provider until restart."""
        calls: list[str] = []
        chain = FallbackChain([_FixedProvider("mt", limit=10, calls=calls)])
        with self.assertRaises(RuntimeError):
            await chain.translate(make_request("x" * 50))
        # The next, correctly sized sentence must still be translated.
        result = await chain.translate(make_request("short"))
        self.assertEqual(result.text, "translated:short")
        self.assertEqual(chain.health["mt"].disabled_reason, None)

    async def test_zero_limit_means_unlimited(self) -> None:
        calls: list[str] = []
        chain = FallbackChain([_FixedProvider("mt", limit=0, calls=calls)])
        result = await chain.translate(make_request("y" * 5000))
        self.assertTrue(result.text.endswith("y" * 10))
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
