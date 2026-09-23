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
from companion.languages import LanguageNotSupportedError
from companion.providers.base import (
    SourceLanguagePolicy,
    StreamMeta,
    TranslationCapabilities,
    TranslationProvider,
    TranslationRequest,
    TranslationResult,
    validate_source_policy,
)
from companion.providers.config import DEFAULT_CONFIG, load_config, masked_config, update_config, validate_config
from companion.providers.fallback import FallbackChain
from companion.providers.mt_openai_compat import OpenAICompatibleTranslationProvider, _build_prompt


class ProviderRegistryTests(unittest.TestCase):
    def test_all_documented_kinds_are_registered_and_typed(self) -> None:
        self.assertEqual(
            set(REGISTRY),
            {
                "dashscope-qwen-realtime",
                "dashscope-task-asr",
                "dashscope-livetranslate-realtime",
                "assemblyai-streaming",
                "volcengine-sauc",
                "elevenlabs-scribe-realtime",
                "speechmatics-realtime",
                "tencent-asr",
                "openai-audio-transcriptions",
                "deepgram-streaming",
                "soniox-realtime",
                "soniox-realtime-transcribe",
                "openai-realtime-transcription",
                "openai-compatible",
                "anthropic-messages",
                "google-genai",
            },
        )
        config = load_config(ROOT / "runtime" / "providers.example.json", env={"DASHSCOPE_API_KEY": "secret"})
        self.assertEqual(create_asr(config["asr"]["providers"][0]).capabilities.stable_prefix, False)
        self.assertEqual(len(DEFAULT_CONFIG["asr"]["providers"]), 1)
        fun = DEFAULT_CONFIG["asr"]["providers"][0]
        self.assertEqual(create_asr(fun).capabilities.word_timestamps, True)
        self.assertEqual(create_asr(fun).capabilities.stable_prefix, False)
        self.assertEqual(create_asr(fun).capabilities.languages, ("zh", "en", "ja"))
        self.assertEqual(create_translation(config["translation"]["providers"][0]).capabilities.rolling_context, True)
        self.assertNotIn("qwen-mt", REGISTRY)
        self.assertFalse(DEFAULT_CONFIG["translation"]["fallback"])
        self.assertTrue(all(item["kind"] != "qwen-mt" for item in DEFAULT_CONFIG["translation"]["providers"]))

    def test_protocol_event_mapping(self) -> None:
        interim = _QwenRealtimeStream._map_event({"type": "conversation.item.input_audio_transcription.text", "text": "確定", "stash": "候補", "language": "ja"})
        self.assertEqual((interim.type, interim.text, interim.stash), ("interim", "確定", "候補"))
        self.assertEqual(interim.caption_observation.kind, "stable_prefix_snapshot")
        self.assertEqual(interim.caption_observation.stable_text, "確定")
        self.assertEqual(interim.caption_observation.tentative_text, "候補")
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

    def test_validation_rejects_provider_kinds_in_the_wrong_catalog(self) -> None:
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["asr"]["providers"][0]["kind"] = "openai-compatible"
        with self.assertRaisesRegex(ValueError, "not valid for asr"):
            validate_config(config)
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["translation"]["providers"][0]["kind"] = "openai-audio-transcriptions"
        with self.assertRaisesRegex(ValueError, "not valid for translation"):
            validate_config(config)

    def test_restricted_atomic_update_preserves_provider_records_and_inline_key(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "providers.json"
            persisted_input = copy.deepcopy(DEFAULT_CONFIG)
            persisted_input["asr"]["providers"][0]["apiKey"] = "inline-secret"
            path.write_text(json.dumps(persisted_input), encoding="utf-8")
            updated = update_config(path, {"asr": {"active": "bailian-fun-asr-2026-02-28"}, "translation": {"fallback": []}, "subtitle": {"sourceLanguage": "ja", "targetLanguage": "zh"}})
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(updated["asr"]["active"], "bailian-fun-asr-2026-02-28")
            self.assertEqual(persisted["asr"]["providers"], persisted_input["asr"]["providers"])
            self.assertEqual(persisted["asr"]["providers"][0]["apiKey"], "inline-secret")
            self.assertFalse(any(path.parent.glob(".providers.json.*")))
            with self.assertRaisesRegex(ValueError, "only asr.active"):
                update_config(path, {"asr": {"apiKey": "forbidden"}})


class NewStreamingProviderCapabilityTests(unittest.TestCase):
    """Ticket 02 presets: effective capability metadata, pre-start key/sample-rate
    errors, and the refreshed DashScope model presets."""

    def _asr(self, kind: str, model: str, **overrides):
        return create_asr({
            "id": "t",
            "kind": kind,
            "model": model,
            "baseUrl": "wss://example.invalid/ws",
            "apiKey": "k",
            **overrides,
        })

    def test_deepgram_nova3_capabilities(self) -> None:
        provider = self._asr("deepgram-streaming", "nova-3")
        caps = provider.capabilities
        self.assertTrue(caps.word_timestamps)
        self.assertFalse(caps.stable_prefix)  # interims are mutable hypotheses
        language = caps.language
        self.assertEqual(language.detection, "unrestricted")
        self.assertTrue(language.code_switching)
        self.assertTrue(language.reports_detected_language)
        self.assertEqual(language.tier, "provider_claimed")
        # nova-3 supports zh specified, but multi detection cannot detect it.
        validate_source_policy(SourceLanguagePolicy.specified("zh-Hans"), language)
        validate_source_policy(SourceLanguagePolicy.detect(candidates=("ja", "en")), language)
        with self.assertRaises(LanguageNotSupportedError):
            validate_source_policy(SourceLanguagePolicy.detect(candidates=("zh",)), language)
        with self.assertRaises(LanguageNotSupportedError):
            # Cantonese is not in the official nova-3 language list.
            validate_source_policy(SourceLanguagePolicy.specified("yue"), language)

    def test_deepgram_unknown_model_stays_experimental(self) -> None:
        provider = self._asr("deepgram-streaming", "custom-model-x")
        language = provider.capabilities.language
        self.assertEqual(language.tier, "experimental")
        self.assertEqual(language.detection, "none")
        self.assertFalse(language.code_switching)

    def test_soniox_v5_capabilities(self) -> None:
        provider = self._asr("soniox-realtime", "stt-rt-v5")
        caps = provider.capabilities
        self.assertTrue(caps.stable_prefix)
        self.assertTrue(caps.word_timestamps)
        self.assertTrue(caps.context)
        self.assertFalse(caps.manual_commit)
        self.assertEqual(caps.language.detection, "unrestricted")
        self.assertTrue(caps.language.code_switching)
        self.assertTrue(caps.language.reports_detected_language)
        self.assertEqual(caps.language.tier, "provider_claimed")

    def test_openai_realtime_presets(self) -> None:
        live = self._asr("openai-realtime-transcription", "gpt-live-transcribe")
        self.assertEqual(live.capabilities.preferred_sample_rate, 24000)
        self.assertTrue(live.capabilities.manual_commit)
        self.assertFalse(live.capabilities.word_timestamps)
        self.assertFalse(live.capabilities.language.reports_detected_language)
        self.assertEqual(live.capabilities.language.detection, "unrestricted")
        self.assertEqual(live.capabilities.language.tier, "provider_claimed")

        transcribe = self._asr("openai-realtime-transcription", "gpt-transcribe")
        self.assertTrue(transcribe.capabilities.language.reports_detected_language)

        unknown = self._asr("openai-realtime-transcription", "gpt-4o-realtime-preview")
        self.assertEqual(unknown.capabilities.language.tier, "experimental")
        self.assertEqual(unknown.capabilities.language.detection, "none")

    def test_new_provider_presets_declare_official_capabilities(self) -> None:
        """Ticket 02 new kinds: capability claims match the protocol audit."""
        assemblyai = self._asr("assemblyai-streaming", "universal-3-5-pro")
        self.assertTrue(assemblyai.requires_api_key)
        self.assertTrue(assemblyai.capabilities.manual_commit)  # ForceEndpoint
        self.assertTrue(assemblyai.capabilities.word_timestamps)
        self.assertTrue(assemblyai.capabilities.language.code_switching)
        self.assertEqual(assemblyai.capabilities.language.tier, "provider_claimed")
        self.assertEqual(assemblyai.capabilities.preferred_sample_rate, 16000)

        volc = self._asr("volcengine-sauc", "bigmodel_async")
        # Streaming sauc accepts no language parameter: zh/en only, never ja.
        self.assertEqual(volc.capabilities.language.supported_tags, ("zh", "en"))
        self.assertNotIn("ja", volc.capabilities.language.supported_tags)
        self.assertFalse(volc.capabilities.speaker_labels)
        self.assertEqual(volc.capabilities.language.tier, "provider_claimed")

        elevenlabs = self._asr("elevenlabs-scribe-realtime", "scribe_v2_realtime")
        self.assertFalse(elevenlabs.capabilities.word_timestamps)
        self.assertTrue(elevenlabs.capabilities.language.detection == "unrestricted")

        speechmatics = self._asr("speechmatics-realtime", "enhanced")
        self.assertEqual(speechmatics.capabilities.language.detection, "none")
        self.assertTrue(speechmatics.capabilities.word_timestamps)

        # Speaker labels require BOTH the speaker engine and word_info timing.
        tencent = create_asr({
            "id": "t", "kind": "tencent-asr", "model": "16k_zh_en_speaker_2.0",
            "baseUrl": "wss://example.invalid/ws", "apiKey": "k",
            "options": {"wordInfo": 1},
        })
        self.assertTrue(tencent.capabilities.speaker_labels)
        self.assertTrue(tencent.requires_api_key)
        no_words = create_asr({
            "id": "t2", "kind": "tencent-asr", "model": "16k_zh_en_speaker_2.0",
            "baseUrl": "wss://example.invalid/ws", "apiKey": "k",
            "options": {"wordInfo": 0},
        })
        self.assertFalse(no_words.capabilities.speaker_labels)

        for kind, model in (
            ("assemblyai-streaming", "custom-model"),
            ("volcengine-sauc", ""),
            ("elevenlabs-scribe-realtime", "custom"),
            ("speechmatics-realtime", ""),
            ("tencent-asr", "unknown_engine"),
        ):
            with self.subTest(kind=kind):
                unknown = self._asr(kind, model)
                self.assertEqual(unknown.capabilities.language.tier, "experimental")
                self.assertEqual(unknown.capabilities.language.detection, "none")

    def test_cloud_asr_requires_api_key_but_local_whisper_does_not(self) -> None:
        self.assertTrue(self._asr("deepgram-streaming", "nova-3").requires_api_key)
        self.assertTrue(self._asr("soniox-realtime", "stt-rt-v5").requires_api_key)
        self.assertTrue(self._asr("openai-realtime-transcription", "gpt-live-transcribe").requires_api_key)
        self.assertFalse(self._asr("openai-audio-transcriptions", "whisper-1").requires_api_key)

    def test_dashscope_preset_refresh(self) -> None:
        fun_asr = self._asr("dashscope-task-asr", "fun-asr-realtime")
        self.assertEqual(fun_asr.capabilities.language.tier, "provider_claimed")
        fun_asr_tags = fun_asr.capabilities.language.supported_tags
        assert fun_asr_tags is not None
        self.assertIn("ko", fun_asr_tags)
        self.assertIn("ar", fun_asr_tags)

        qwen_audio = self._asr("dashscope-task-asr", "qwen-audio-3.0-asr-flash-streaming")
        self.assertEqual(qwen_audio.capabilities.language.tier, "provider_claimed")
        qwen_audio_tags = qwen_audio.capabilities.language.supported_tags
        assert qwen_audio_tags is not None
        self.assertIn("fr", qwen_audio_tags)

        # The previously verified dated preset is untouched.
        dated = self._asr("dashscope-task-asr", "fun-asr-realtime-2026-02-28")
        self.assertEqual(dated.capabilities.language.tier, "verified")
        self.assertEqual(dated.capabilities.language.supported_tags, ("zh", "en", "ja"))


class PromptTests(unittest.TestCase):
    def test_openai_prompt_keeps_history_in_one_user_message(self) -> None:
        request = _request()
        system, user = _build_prompt(request)
        self.assertIn("术语表", system)
        self.assertIn("主播 -> 主播", user)
        self.assertIn("CURRENT CHUNK:\nこんにちは", user)
        self.assertIn("starts_mid_sentence=unknown", user)

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
