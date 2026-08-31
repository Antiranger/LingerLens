from __future__ import annotations

import asyncio
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.providers import REGISTRY, create_asr, create_translation
from companion.providers.asr_dashscope_task import _DashScopeTaskStream
from companion.providers.asr_qwen_realtime import _QwenRealtimeStream
from companion.providers.base import (
    StreamMeta,
    TranslationCapabilities,
    TranslationProvider,
    TranslationRequest,
    TranslationResult,
)
from companion.providers.config import DEFAULT_CONFIG, load_config, masked_config, update_config, validate_config
from companion.providers.fallback import FallbackChain
from companion.providers.mt_openai_compat import OpenAICompatibleTranslationProvider, _build_prompt


class ProviderRegistryTests(unittest.TestCase):
    def test_all_four_documented_kinds_are_registered_and_typed(self) -> None:
        self.assertEqual(
            set(REGISTRY),
            {"dashscope-qwen-realtime", "dashscope-task-asr", "openai-compatible", "qwen-mt"},
        )
        config = load_config(ROOT / "runtime" / "providers.example.json", env={"DASHSCOPE_API_KEY": "secret"})
        self.assertEqual(create_asr(config["asr"]["providers"][0]).capabilities.stable_prefix, True)
        fun = next(item for item in DEFAULT_CONFIG["asr"]["providers"] if item["model"] == "fun-asr-realtime-2026-02-28")
        self.assertEqual(create_asr(fun).capabilities.word_timestamps, True)
        self.assertEqual(create_asr(fun).capabilities.stable_prefix, False)
        self.assertEqual(create_asr(fun).capabilities.languages, ("zh", "en", "ja"))
        self.assertEqual(create_translation(config["translation"]["providers"][0]).capabilities.rolling_context, True)
        # Qwen-MT consumes prior turns as tm_list rather than as chat history,
        # but it does consume them: declaring False made the pipeline blank
        # request.history before the provider ran, so tm_list was always empty.
        self.assertEqual(create_translation(config["translation"]["providers"][1]).capabilities.rolling_context, True)

    def test_protocol_event_mapping(self) -> None:
        interim = _QwenRealtimeStream._map_event({"type": "conversation.item.input_audio_transcription.text", "text": "確定", "stash": "候補", "language": "ja"})
        self.assertEqual((interim.type, interim.text, interim.stash), ("interim", "確定", "候補"))
        final = _DashScopeTaskStream._map_event({
            "header": {"event": "result-generated"},
            "payload": {"output": {"sentence": {
                "sentence_id": 7,
                "text": "完了",
                "sentence_end": True,
                "begin_time": 125,
                "end_time": 900,
            }}},
        })
        self.assertEqual((final.type, final.begin_pcm, final.end_pcm, final.item_id), ("final", 0.125, 0.9, "7"))


class ConfigTests(unittest.TestCase):
    def test_missing_config_is_created_resolved_and_masked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "providers.json"
            config = load_config(path, env={"DASHSCOPE_API_KEY": "env-secret"})
            self.assertTrue(path.exists())
            self.assertEqual(config["asr"]["providers"][0]["_apiKey"], "env-secret")
            view = masked_config(config)
            rendered = json.dumps(view, ensure_ascii=False)
            self.assertNotIn("env-secret", rendered)
            self.assertTrue(view["asr"]["providers"][0]["apiKeyConfigured"])

    def test_inline_key_is_removed_from_runtime_record_and_masked(self) -> None:
        config = copy.deepcopy(DEFAULT_CONFIG)
        provider = config["asr"]["providers"][0]
        provider.pop("apiKeyEnv")
        provider["apiKey"] = "inline-secret"
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "providers.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            loaded = load_config(path, env={})
        runtime_provider = loaded["asr"]["providers"][0]
        self.assertNotIn("apiKey", runtime_provider)
        self.assertEqual(runtime_provider["_apiKey"], "inline-secret")
        self.assertEqual(masked_config(loaded)["asr"]["providers"][0]["apiKey"], "***")

    def test_validation_lists_kinds_and_rejects_bad_active(self) -> None:
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["asr"]["providers"][0]["kind"] = "unknown"
        with self.assertRaisesRegex(ValueError, "available kinds"):
            validate_config(config)
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["translation"]["active"] = "missing"
        with self.assertRaisesRegex(ValueError, "translation.active"):
            validate_config(config)

    def test_restricted_atomic_update_preserves_provider_records_and_inline_key(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "providers.json"
            persisted_input = copy.deepcopy(DEFAULT_CONFIG)
            persisted_input["asr"]["providers"][0]["apiKey"] = "inline-secret"
            path.write_text(json.dumps(persisted_input), encoding="utf-8")
            updated = update_config(path, {"asr": {"active": "bailian-paraformer"}, "translation": {"fallback": []}, "subtitle": {"sourceLanguage": "ja", "targetLanguage": "zh"}})
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(updated["asr"]["active"], "bailian-paraformer")
            self.assertEqual(persisted["asr"]["providers"], persisted_input["asr"]["providers"])
            self.assertEqual(persisted["asr"]["providers"][0]["apiKey"], "inline-secret")
            self.assertFalse(any(path.parent.glob(".providers.json.*")))
            with self.assertRaisesRegex(ValueError, "only asr.active"):
                update_config(path, {"asr": {"apiKey": "forbidden"}})


class PromptTests(unittest.TestCase):
    def test_openai_prompt_keeps_history_in_one_user_message(self) -> None:
        request = _request()
        system, user = _build_prompt(request)
        self.assertIn("术语表", system)
        self.assertIn("主播 -> 主播", user)
        self.assertIn("CURRENT:\nこんにちは", user)

    def test_generic_openai_compatible_provider_does_not_require_dashscope_extension(self) -> None:
        provider = OpenAICompatibleTranslationProvider({
            "id": "custom",
            "model": "gpt-4.1-mini",
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "secret",
            "options": {},
        })
        self.assertEqual(provider.base_url, "https://api.openai.com/v1")
        payload = provider.build_payload(_request())
        self.assertNotIn("extra_body", payload)
        self.assertEqual(payload["messages"][1]["role"], "user")


class FakeProvider(TranslationProvider):
    def __init__(self, provider_id: str, outcomes: list[object]):
        self.id = provider_id
        self.label = provider_id
        self.model = provider_id
        self.outcomes = outcomes
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
        return TranslationResult(str(outcome), self.id, 1)


class FallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_falls_back_and_skips_provider_during_cooldown(self) -> None:
        now = [10.0]
        first = FakeProvider("first", [RuntimeError("down"), "recovered"])
        second = FakeProvider("second", ["fallback", "still fallback", "fallback after cooldown"])
        chain = FallbackChain([first, second], failure_threshold=1, cooldown_seconds=60, clock=lambda: now[0])
        self.assertEqual((await chain.translate(_request())).provider_id, "second")
        self.assertEqual((await chain.translate(_request())).provider_id, "second")
        self.assertEqual(first.calls, 1)
        now[0] = 71.0
        self.assertEqual((await chain.translate(_request())).provider_id, "first")
        self.assertEqual(first.calls, 2)


class SpikeCliTests(unittest.TestCase):
    def test_help_does_not_require_api_key_or_audio(self) -> None:
        environment = dict(os.environ)
        environment.pop("DASHSCOPE_API_KEY", None)
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "asr-spike.py"), "--help"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--audio", result.stdout)
        self.assertIn("--provider-id", result.stdout)


def _request() -> TranslationRequest:
    return TranslationRequest(
        source_text="こんにちは",
        meta=StreamMeta("配信", "チャンネル", "gaming", "ja", "zh"),
        history=[("主播", "主播")],
        glossary=[("宝鐘マリン", "宝钟玛琳")],
    )


if __name__ == "__main__":
    unittest.main()
