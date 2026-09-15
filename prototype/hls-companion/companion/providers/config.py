from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Mapping

from . import REGISTRY
from ..languages import canonicalize_target_tag
from .base import SourceLanguagePolicy, SUPPORTED_CURRENCIES, provider_currency

BUILTIN_ASR_PROVIDERS: tuple[dict[str, Any], ...] = (
    {
        "id": "bailian-fun-asr-2026-02-28",
        "label": "百炼 Fun-ASR-Realtime 2026-02-28（日/中/英）",
        "kind": "dashscope-task-asr",
        "model": "fun-asr-realtime-2026-02-28",
        "baseUrl": "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
        "apiKey": "",
        "options": {
            "sampleRate": 16000,
            "languages": ["zh", "en", "ja"],
            "semanticPunctuationEnabled": False,
            "maxSentenceSilence": 400,
            # Official Fun-ASR option: avoid an endlessly growing VAD sentence
            # when a live host speaks without pausing.
            "multiThresholdModeEnabled": True,
            "heartbeat": True,
            "hotwordsEnabled": False,
            "contextEnabled": False,
            "startTimeoutSeconds": 10,
        },
    },
    {
        "id": "bailian-qwen3-realtime",
        "label": "百炼 Qwen3-ASR-Flash-Realtime",
        "kind": "dashscope-qwen-realtime",
        "model": "qwen3-asr-flash-realtime",
        "baseUrl": "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
        "apiKeyEnv": "DASHSCOPE_API_KEY",
        "pricePerSecondCny": 0.00033,
        "options": {
            "sampleRate": 16000,
            "turnDetection": {
                "type": "server_vad",
                "threshold": 0.2,
                "silenceDurationMs": 400,
            },
        },
    },
    {
        "id": "bailian-qwen-audio-3.0-asr-streaming",
        "label": "百炼 Qwen-Audio-3.0-ASR-Flash-Streaming（实时·多语言）",
        "kind": "dashscope-task-asr",
        "model": "qwen-audio-3.0-asr-flash-streaming",
        "baseUrl": "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
        "apiKeyEnv": "DASHSCOPE_API_KEY",
        "options": {
            "sampleRate": 16000,
            "languages": ["zh", "en", "ja"],
            "semanticPunctuationEnabled": False,
            "maxSentenceSilence": 800,
            "multiThresholdModeEnabled": True,
            "heartbeat": True,
            "startTimeoutSeconds": 10,
        },
    },
    {
        "id": "bailian-paraformer",
        "label": "百炼 Paraformer-Realtime-V2（成本基线）",
        "kind": "dashscope-task-asr",
        "model": "paraformer-realtime-v2",
        "baseUrl": "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
        "apiKeyEnv": "DASHSCOPE_API_KEY",
        "pricePerSecondCny": 0.00024,
        "options": {
            "sampleRate": 16000,
            "languages": ["zh", "en", "ja", "ko"],
            "semanticPunctuationEnabled": False,
            "maxSentenceSilence": 800,
            # Audit 2026-09: parameters.hotwords is not in the current official
            # docs and input.context is not documented for this model. Hotword
            # support goes through options.vocabulary/vocabularyId instead.
            "contextEnabled": False,
            "startTimeoutSeconds": 10,
        },
    },
    {
        "id": "deepgram-nova3-multilingual",
        "label": "Deepgram Nova-3（全球多语言·混合识别）",
        "kind": "deepgram-streaming",
        "model": "nova-3",
        "baseUrl": "wss://api.deepgram.com/v1/listen",
        "apiKeyEnv": "DEEPGRAM_API_KEY",
        "options": {
            "interimResults": True,
            "smartFormat": True,
            # Official recommendation for code-switching sessions.
            "endpointingMs": 100,
            "vadEvents": True,
            # Official docs pair UtteranceEnd with this parameter; without it
            # the UtteranceEnd speech_stopped events never fire.
            "utteranceEndMs": 1000,
            # Diarization is a paid add-on: opt in per profile (diarize: true).
            "keepAliveSeconds": 8,
        },
    },
    {
        "id": "soniox-stt-rt-v5",
        "label": "Soniox STT RT v5（多语言·稳定前缀）",
        "kind": "soniox-realtime",
        "model": "stt-rt-v5",
        "baseUrl": "wss://stt-rt.soniox.com/transcribe-websocket",
        "apiKeyEnv": "SONIOX_API_KEY",
        "options": {
            "enableLanguageIdentification": True,
            "enableEndpointDetection": True,
            # Diarization is included in the realtime rate (official pricing).
            "enableSpeakerDiarization": True,
            "maxEndpointDelayMs": 700,
            "endpointSensitivity": 0.3,
            "keepAliveSeconds": 5,
        },
    },
    {
        "id": "assemblyai-universal-3-5-pro",
        "label": "AssemblyAI Universal-3.5 Pro Realtime（原生混合·英语✓）",
        "kind": "assemblyai-streaming",
        "model": "universal-3-5-pro",
        "baseUrl": "wss://streaming.assemblyai.com/v3/ws",
        "apiKeyEnv": "ASSEMBLYAI_API_KEY",
        "options": {
            "mode": "balanced",
            "speakerLabels": True,
            "maxSpeakers": 6,
            "closeDrainTimeoutSeconds": 2.0,
        },
    },
    {
        "id": "volcengine-bigasr-sauc",
        "label": "火山引擎豆包大模型流式 ASR（中英）",
        "kind": "volcengine-sauc",
        "model": "bigmodel_async",
        "baseUrl": "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
        "apiKeyEnv": "VOLCENGINE_ACCESS_TOKEN",
        "options": {
            # New-console auth: X-Api-Key + resource id. Legacy console auth:
            # authMode "legacy" + appKey (apiKey holds the Access Token).
            "resourceId": "volc.bigasr.sauc.concurrent",
            "enableItn": True,
            "endWindowSize": 800,
            "closeDrainTimeoutSeconds": 2.0,
        },
    },
    {
        "id": "elevenlabs-scribe-v2-rt",
        "label": "ElevenLabs Scribe v2 Realtime（多语言）",
        "kind": "elevenlabs-scribe-realtime",
        "model": "scribe_v2_realtime",
        "baseUrl": "wss://api.elevenlabs.io/v1/speech-to-text/realtime",
        "apiKeyEnv": "ELEVENLABS_API_KEY",
        "options": {
            "commitStrategy": "manual",
            "includeLanguageDetection": True,
        },
    },
    {
        "id": "speechmatics-rt-enhanced",
        "label": "Speechmatics Realtime Enhanced（单语·时间戳）",
        "kind": "speechmatics-realtime",
        "model": "enhanced",
        "baseUrl": "wss://global.rt.speechmatics.com/v2/",
        "apiKeyEnv": "SPEECHMATICS_API_KEY",
        "options": {
            "enablePartials": True,
            "maxDelaySeconds": 4,
        },
    },
    {
        "id": "tencent-asr-v2-speaker",
        "label": "腾讯云实时 ASR（16k_zh_en_speaker_2.0·话者分离）",
        "kind": "tencent-asr",
        "model": "16k_zh_en_speaker_2.0",
        "baseUrl": "wss://asr.cloud.tencent.com/asr/v2/<appid>",
        "apiKeyEnv": "TENCENT_SECRET_KEY",
        "options": {
            # Fill in your console values: appId + secretId (SecretKey goes in
            # the API Key field or TENCENT_SECRET_KEY). Japanese: engineModelType
            # "16k_ja" on the classic engine.
            "appId": "",
            "secretId": "",
            "engineModelType": "16k_zh_en_speaker_2.0",
            "voiceFormat": 1,
            "wordInfo": 1,
            "needvad": 1,
        },
    },
    {
        "id": "openai-gpt-live-transcribe",
        "label": "OpenAI GPT-Live-Transcribe（低延迟·24 kHz）",
        "kind": "openai-realtime-transcription",
        "model": "gpt-live-transcribe",
        "baseUrl": "wss://api.openai.com/v1/realtime",
        "apiKeyEnv": "OPENAI_API_KEY",
        "options": {
            "delay": "low",
            "turnDetection": {"type": "server_vad", "threshold": 0.2, "prefixPaddingMs": 300, "silenceDurationMs": 400},
        },
    },
    {
        "id": "openai-gpt-transcribe",
        "label": "OpenAI GPT-Transcribe（检测语言·24 kHz）",
        "kind": "openai-realtime-transcription",
        "model": "gpt-transcribe",
        "baseUrl": "wss://api.openai.com/v1/realtime",
        "apiKeyEnv": "OPENAI_API_KEY",
        "options": {
            "turnDetection": {"type": "server_vad", "threshold": 0.2, "prefixPaddingMs": 300, "silenceDurationMs": 400},
        },
    },

)


DEFAULT_CONFIG: dict[str, Any] = {
    "version": 3,
    "asr": {
        "active": "bailian-fun-asr-2026-02-28",
        "providers": [copy.deepcopy(BUILTIN_ASR_PROVIDERS[0])],
    },
    "translation": {
        "active": "bailian-qwen35-flash",
        "fallback": [],
        "providers": [
            {
                "id": "bailian-qwen35-flash",
                "label": "百炼 Qwen3.5-Flash（高质量·带上下文）",
                "kind": "openai-compatible",
                "model": "qwen3.5-flash",
                "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "apiKeyEnv": "DASHSCOPE_API_KEY",
                "options": {
                    "temperature": 0.3,
                    "maxTokens": 256,
                    "timeoutSeconds": 6,
                    "enableThinking": False,
                    "contextPairs": 10,
                    "contextSeconds": 90,
                },
            },
        ],
    },
    "subtitle": {
        "sourceLanguage": {"mode": "specified", "tag": "ja"},
        "targetLanguage": "zh-Hans",
        "anchor": "start",
        "holdSecondsMin": 1.2,
        "holdSecondsMax": 7.0,
        "holdSecondsPerChar": 0.06,
        # Cut policy lives entirely in the shared CaptionChunker. The former
        # maxUtteranceSeconds / prefixSplitEnabled / prefixSplitAfterSeconds
        # knobs configured a second segmentation mechanism that no shipped ASR
        # Adapter could reach, and are gone with it.
        "translationWorkers": 4,
        "manualOffsetSeconds": 0.0,
        "bilingual": True,
    },
}


def load_config(path: str | Path, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        atomic_write_config(path, DEFAULT_CONFIG)
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if config.get("version") != 3:
        config = migrate_config(config)
        atomic_write_config(path, config)
    validate_config(config)
    return resolve_secrets(config, env)


def migrate_config(config: dict[str, Any]) -> dict[str, Any]:
    """Upgrade any older persisted config to the current schema version."""
    migrated = copy.deepcopy(config)
    if migrated.get("version") == 1:
        migrated["version"] = 2
    if migrated.get("version") == 2:
        migrated = migrate_v2_config(migrated)
    return migrated


def migrate_v2_config(config: dict[str, Any]) -> dict[str, Any]:
    """v2 -> v3: language fields become canonical BCP 47 contract values.

    ``sourceLanguage: "ja"`` becomes the specified policy ``{"mode":
    "specified", "tag": "ja"}``; the legacy Chinese target ``zh`` becomes
    ``zh-Hans`` (the product's existing translations are Simplified Chinese).
    Provider profiles, active/fallback ids, keys, prices and every other
    subtitle preference are preserved untouched.
    """
    migrated = copy.deepcopy(config)
    migrated["version"] = 3
    subtitle = migrated.get("subtitle")
    if isinstance(subtitle, dict):
        source = subtitle.get("sourceLanguage")
        if isinstance(source, str):
            subtitle["sourceLanguage"] = SourceLanguagePolicy.specified(source).to_json()
        elif isinstance(source, dict):
            # Re-canonicalize an already-structured policy.
            subtitle["sourceLanguage"] = SourceLanguagePolicy.from_json(source).to_json()
        target = subtitle.get("targetLanguage")
        if isinstance(target, str):
            subtitle["targetLanguage"] = canonicalize_target_tag(target)
    return migrated


def _validate_subtitle_language_settings(config: dict[str, Any]) -> None:
    subtitle = config.get("subtitle")
    if not isinstance(subtitle, dict):
        return
    if "sourceLanguage" in subtitle:
        try:
            SourceLanguagePolicy.from_json(subtitle["sourceLanguage"])
        except ValueError as exc:
            raise ValueError(f"subtitle.sourceLanguage: {exc}") from exc
    if "targetLanguage" in subtitle:
        try:
            canonicalize_target_tag(subtitle["targetLanguage"])
        except ValueError as exc:
            raise ValueError(f"subtitle.targetLanguage: {exc}") from exc


def validate_config(config: dict[str, Any]) -> None:

    if config.get("version") != 3:
        raise ValueError("providers config version must be 3")
    _validate_subtitle_language_settings(config)
    for section_name in ("asr", "translation"):
        section = config.get(section_name)
        if not isinstance(section, dict) or not isinstance(section.get("providers"), list):
            raise ValueError(f"{section_name}.providers must be a list")
        ids: list[str] = []
        allowed_kinds = (
            {
                "dashscope-qwen-realtime",
                "dashscope-task-asr",
                "openai-audio-transcriptions",
                "deepgram-streaming",
                "soniox-realtime",
                "openai-realtime-transcription",
                "assemblyai-streaming",
                "elevenlabs-scribe-realtime",
                "volcengine-sauc",
                "speechmatics-realtime",
                "tencent-asr",
            }
            if section_name == "asr"
            else {"openai-compatible", "anthropic-messages", "google-genai"}
        )
        for provider in section["providers"]:
            if not isinstance(provider, dict):
                raise ValueError(f"{section_name} provider entries must be objects")
            provider_id = provider.get("id")
            if not isinstance(provider_id, str) or not provider_id:
                raise ValueError(f"{section_name} provider id is required")
            if provider_id in ids:
                raise ValueError(f"duplicate provider id: {provider_id}")
            ids.append(provider_id)
            kind = provider.get("kind")
            if kind not in REGISTRY:
                available = ", ".join(sorted(REGISTRY))
                raise ValueError(f"unknown provider kind {kind!r}; available kinds: {available}")
            if kind not in allowed_kinds:
                raise ValueError(f"provider kind {kind!r} is not valid for {section_name}")
            pricing_fields = (
                ("pricePerSecondCny",)
                if section_name == "asr"
                else (
                    "pricePerMillionInputTokensCny",
                    "pricePerMillionCachedInputTokensCny",
                    "pricePerMillionCacheWriteTokensCny",
                    "pricePerMillionOutputTokensCny",
                )
            )
            for field in pricing_fields:
                if field not in provider or provider[field] is None:
                    continue
                value = provider[field]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                    raise ValueError(f"{section_name} provider {field} must be a non-negative number")
            # 币种可省略：省略时按厂商推导（中国厂商人民币，其余美元）。
            currency = provider.get("currency")
            if currency is not None and (
                not isinstance(currency, str) or currency.upper() not in SUPPORTED_CURRENCIES
            ):
                raise ValueError(
                    f"{section_name} provider currency must be one of {', '.join(SUPPORTED_CURRENCIES)}"
                )
        if section.get("active") not in ids:
            raise ValueError(f"{section_name}.active must name a configured provider")
        if section_name == "translation":
            fallback = section.get("fallback", [])
            if not isinstance(fallback, list) or any(item not in ids for item in fallback):
                raise ValueError("translation.fallback must contain configured provider ids")
    # Old profiles inherit once; persisted selections then remain independent.
    config.setdefault("chatTranslation", {"active": config.get("translation", {}).get("active")})
    chat = config["chatTranslation"]
    if not isinstance(chat, dict) or set(chat) - {"active"}:
        raise ValueError("chatTranslation only accepts active")
    if chat.get("active") not in {p.get("id") for p in config.get("translation", {}).get("providers", [])}:
        raise ValueError("chatTranslation.active must reference a configured translation model")


def resolve_secrets(
    config: dict[str, Any], env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    resolved = copy.deepcopy(config)
    environment = os.environ if env is None else env
    for provider in _providers(resolved):
        inline = provider.pop("apiKey", None)
        env_name = provider.get("apiKeyEnv")
        provider["_apiKey"] = inline if inline is not None else (environment.get(env_name, "") if env_name else "")
    return resolved


def masked_config(config: dict[str, Any]) -> dict[str, Any]:
    masked = copy.deepcopy(config)
    for provider in _providers(masked):
        key = provider.pop("_apiKey", provider.get("apiKey", ""))
        env_name = provider.get("apiKeyEnv")
        configured = bool(key) or bool(env_name and os.environ.get(env_name))
        if "apiKey" in provider or key:
            provider["apiKey"] = "***"
        provider["apiKeyConfigured"] = configured
        # 数字的币种永远随配置一起回给前端：字段名里的 Cny 是历史命名，界面
        # 必须按这个值标注单位，否则用户会照着错单位填价格。
        provider["currency"] = provider_currency(provider.get("kind"), provider.get("currency"))
    return masked


def update_model_settings(path: str | Path, settings: dict[str, Any]) -> dict[str, Any]:
    """Persist catalog CRUD from the loopback model-settings boundary.

    A legacy single-record payload is still accepted so existing local clients
    can upgrade without a flag day. New clients send complete provider lists;
    replacing the list is the CRUD operation and validation protects active and
    fallback references as well as the at-least-one-record invariant.
    """
    path = Path(path)
    current = load_config(path)
    persisted = _without_runtime_secrets(current)

    asr_settings = settings.get("asr", {})
    translation_settings = settings.get("translation", {})
    if not isinstance(asr_settings, dict) or not isinstance(translation_settings, dict):
        raise ValueError("asr and translation settings must be objects")

    if "providers" in asr_settings or "providers" in translation_settings:
        if not isinstance(asr_settings.get("providers"), list) or not isinstance(translation_settings.get("providers"), list):
            raise ValueError("asr.providers and translation.providers must be lists")
        candidate = {
            "version": 3,
            "chatTranslation": copy.deepcopy(settings.get("chatTranslation", {"active": translation_settings.get("active")})),
            "asr": copy.deepcopy(asr_settings),
            "translation": copy.deepcopy(translation_settings),
            "subtitle": _canonicalize_subtitle_settings(settings.get("subtitle", persisted.get("subtitle", {}))),
        }
        _preserve_omitted_secrets(candidate, current)
        validate_config(candidate)
        atomic_write_config(path, candidate)
        return load_config(path)

    active_asr = _provider_by_id(persisted["asr"], persisted["asr"]["active"])
    provider_id = str(asr_settings.get("providerId") or active_asr["id"])
    asr = _provider_by_id_optional(persisted["asr"], provider_id)
    if asr is None:
        preset = _builtin_asr_provider(provider_id)
        if preset is None:
            raise ValueError(f"unknown ASR provider: {provider_id}")
        asr = copy.deepcopy(preset)
        persisted["asr"]["providers"].append(asr)
    # Protocol and model are owned by the selected provider record. The UI may
    # customize only its endpoint and credential; changing a Qwen model name to
    # Fun-ASR without also changing protocol would be invalid.
    asr["baseUrl"] = _required_url(
        asr_settings.get("baseUrl", asr.get("baseUrl")),
        "ASR base URL",
        ("wss://", "ws://"),
    )
    if not asr_settings.get("apiKey"):
        _inherit_secret(asr, active_asr)
    _apply_secret(asr, asr_settings)
    persisted["asr"]["active"] = asr["id"]

    translation = _provider_by_kind(persisted["translation"], "openai-compatible")
    translation["baseUrl"] = _required_url(
        translation_settings.get("baseUrl", translation.get("baseUrl")),
        "translation base URL",
        ("https://", "http://"),
    ).rstrip("/")
    model = str(translation_settings.get("model", translation.get("model", ""))).strip()
    if not model:
        raise ValueError("translation model is required")
    translation["model"] = model
    translation["label"] = str(translation_settings.get("label") or f"OpenAI Compatible · {model}")
    options = translation.setdefault("options", {})
    options["temperature"] = max(0.0, min(float(translation_settings.get("temperature", options.get("temperature", 0.3))), 2.0))
    options["maxTokens"] = max(32, min(int(translation_settings.get("maxTokens", options.get("maxTokens", 256))), 4096))
    options["timeoutSeconds"] = max(1.0, min(float(translation_settings.get("timeoutSeconds", options.get("timeoutSeconds", 6))), 60.0))
    options["enableThinking"] = False
    options["contextPairs"] = max(0, min(int(translation_settings.get("contextPairs", options.get("contextPairs", 10))), 20))
    _apply_secret(translation, translation_settings)
    persisted["translation"]["active"] = translation["id"]

    validate_config(persisted)
    atomic_write_config(path, persisted)
    return load_config(path)


def model_settings_view(config: dict[str, Any]) -> dict[str, Any]:
    """Return the full local catalog including raw keys for explicit editing."""
    view = _without_runtime_secrets(config)
    for section_name in ("asr", "translation"):
        for provider in view[section_name]["providers"]:
            runtime = _provider_by_id(config[section_name], provider["id"])
            provider["apiKey"] = runtime.get("_apiKey", "")
            provider["apiKeyConfigured"] = bool(provider["apiKey"])
            # 价格字段名里的 Cny 是历史命名；界面必须按这个值标注单位。
            provider["currency"] = provider_currency(provider.get("kind"), provider.get("currency"))
    return view


def update_config(path: str | Path, patch: dict[str, Any]) -> dict[str, Any]:
    allowed = {"asr", "translation", "subtitle", "chatTranslation"}
    if set(patch) - allowed:
        raise ValueError("only asr, translation, and subtitle may be updated")
    path = Path(path)
    if not path.exists():
        atomic_write_config(path, DEFAULT_CONFIG)
    with path.open("r", encoding="utf-8") as handle:
        persisted = json.load(handle)
    if persisted.get("version") != 3:
        persisted = migrate_config(persisted)
    validate_config(persisted)
    if "asr" in patch:
        if set(patch["asr"]) != {"active"}:
            raise ValueError("only asr.active may be updated")
        persisted["asr"]["active"] = patch["asr"]["active"]
    if "translation" in patch:
        unknown = set(patch["translation"]) - {"active", "fallback"}
        if unknown:
            raise ValueError("only translation.active and translation.fallback may be updated")
        persisted["translation"].update(patch["translation"])
    if "chatTranslation" in patch:
        persisted["chatTranslation"] = copy.deepcopy(patch["chatTranslation"])
    if "subtitle" in patch:
        if not isinstance(patch["subtitle"], dict):
            raise ValueError("subtitle must be an object")
        persisted["subtitle"] = _canonicalize_subtitle_settings(patch["subtitle"])
    validate_config(persisted)
    atomic_write_config(path, persisted)
    return load_config(path)


def _canonicalize_subtitle_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Normalize the language fields of a subtitle settings payload.

    Canonicalization happens before persistence; invalid values raise
    actionable errors instead of being silently truncated.
    """
    normalized = copy.deepcopy(settings)
    if "sourceLanguage" in normalized:
        normalized["sourceLanguage"] = SourceLanguagePolicy.from_json(normalized["sourceLanguage"]).to_json()
    if "targetLanguage" in normalized:
        normalized["targetLanguage"] = canonicalize_target_tag(normalized["targetLanguage"])
    return normalized


def atomic_write_config(path: str | Path, config: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_without_runtime_secrets(config), ensure_ascii=False, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        if os.name != "nt":
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _provider_by_kind(section: dict[str, Any], kind: str) -> dict[str, Any]:
    for provider in section.get("providers", []):
        if provider.get("kind") == kind:
            return provider
    raise ValueError(f"provider kind is not configured: {kind}")


def _provider_by_id_optional(section: dict[str, Any], provider_id: str) -> dict[str, Any] | None:
    for provider in section.get("providers", []):
        if provider.get("id") == provider_id:
            return provider
    return None


def _provider_by_id(section: dict[str, Any], provider_id: str) -> dict[str, Any]:
    provider = _provider_by_id_optional(section, provider_id)
    if provider is None:
        raise ValueError(f"provider id is not configured: {provider_id}")
    return provider


def _builtin_asr_provider(provider_id: str) -> dict[str, Any] | None:
    for provider in BUILTIN_ASR_PROVIDERS:
        if provider["id"] == provider_id:
            return provider
    return None


def _required_url(value: Any, label: str, schemes: tuple[str, ...]) -> str:
    url = str(value or "").strip()
    if not url.startswith(schemes):
        raise ValueError(f"{label} must start with {' or '.join(schemes)}")
    return url


def _inherit_secret(provider: dict[str, Any], source: dict[str, Any]) -> None:
    """Reuse the current DashScope credential when switching ASR protocols."""
    if provider.get("apiKey"):
        return
    if source.get("apiKey"):
        provider["apiKey"] = source["apiKey"]
        provider.pop("apiKeyEnv", None)
    elif not provider.get("apiKeyEnv") and source.get("apiKeyEnv"):
        provider["apiKeyEnv"] = source["apiKeyEnv"]


def _apply_secret(provider: dict[str, Any], settings: dict[str, Any]) -> None:
    api_key = settings.get("apiKey")
    if api_key is None or api_key == "":
        return
    api_key = str(api_key).strip()
    if not api_key:
        return
    provider["apiKey"] = api_key
    # An explicitly entered key takes precedence over an old environment name.
    provider.pop("apiKeyEnv", None)


def _preserve_omitted_secrets(candidate: dict[str, Any], current: dict[str, Any]) -> None:
    for section_name in ("asr", "translation"):
        current_by_id = {item["id"]: item for item in current[section_name]["providers"]}
        for provider in candidate[section_name]["providers"]:
            if "apiKey" in provider:
                provider.pop("apiKeyEnv", None)
                continue
            existing = current_by_id.get(provider.get("id"))
            if existing and existing.get("_apiKey"):
                provider["apiKey"] = existing["_apiKey"]
                provider.pop("apiKeyEnv", None)


def _providers(config: dict[str, Any]):
    for section_name in ("asr", "translation"):
        for provider in config.get(section_name, {}).get("providers", []):
            yield provider


def _without_runtime_secrets(config: dict[str, Any]) -> dict[str, Any]:
    clean = copy.deepcopy(config)
    for provider in _providers(clean):
        provider.pop("_apiKey", None)
    return clean
