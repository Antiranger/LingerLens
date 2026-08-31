from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Mapping

from . import REGISTRY

BUILTIN_ASR_PROVIDERS: tuple[dict[str, Any], ...] = (
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
        "id": "bailian-fun-asr-2026-02-28",
        "label": "百炼 Fun-ASR-Realtime 2026-02-28（日/中/英）",
        "kind": "dashscope-task-asr",
        "model": "fun-asr-realtime-2026-02-28",
        "baseUrl": "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
        "apiKeyEnv": "DASHSCOPE_API_KEY",
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
            "hotwordsEnabled": True,
            "contextEnabled": True,
            "startTimeoutSeconds": 10,
        },
    },
)


DEFAULT_CONFIG: dict[str, Any] = {
    "version": 2,
    "asr": {
        "active": "bailian-qwen3-realtime",
        "providers": [copy.deepcopy(provider) for provider in BUILTIN_ASR_PROVIDERS],
    },
    "translation": {
        "active": "bailian-qwen35-flash",
        "fallback": ["bailian-qwen-mt-flash"],
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
            {
                "id": "bailian-qwen-mt-flash",
                "label": "百炼 Qwen-MT-Flash（极速基线）",
                "kind": "qwen-mt",
                "model": "qwen-mt-flash",
                "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "apiKeyEnv": "DASHSCOPE_API_KEY",
                "options": {"timeoutSeconds": 4, "tmPairs": 4},
            },
        ],
    },
    "subtitle": {
        "sourceLanguage": "ja",
        "targetLanguage": "zh",
        "anchor": "start",
        "holdSecondsMin": 1.2,
        "holdSecondsMax": 7.0,
        "holdSecondsPerChar": 0.06,
        # 0 disables mid-speech forced commits: server VAD at 400ms already
        # segments into ~1.6s median units, and forcing a break cuts words and
        # produces cues with no end boundary.
        "maxUtteranceSeconds": 0.0,
        # Emit confirmed sub-sentences from the realtime ASR's stable prefix
        # while a long utterance is still being spoken (redesign Fix F P2,
        # RC-4). Without it a speaker who never pauses yields 10-30s
        # utterances whose cue only exists after the sentence is over
        # (measured 2026-08-31 countdown live: 6/21 utterances >=9.2s, worst
        # 30.2s; prefix split delivered 32 sub-sentences 8.4s earlier on
        # average). The cap keeps short utterances on the final-only path.
        "prefixSplitEnabled": True,
        "prefixSplitAfterSeconds": 3.0,
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
    if config.get("version") == 1:
        config = migrate_v1_config(config)
        atomic_write_config(path, config)
    validate_config(config)
    return resolve_secrets(config, env)


def migrate_v1_config(config: dict[str, Any]) -> dict[str, Any]:
    """Upgrade the original provider lists in place without changing records."""
    migrated = copy.deepcopy(config)
    migrated["version"] = 2
    validate_config(migrated)
    return migrated


def validate_config(config: dict[str, Any]) -> None:
    if config.get("version") != 2:
        raise ValueError("providers config version must be 2")
    for section_name in ("asr", "translation"):
        section = config.get(section_name)
        if not isinstance(section, dict) or not isinstance(section.get("providers"), list):
            raise ValueError(f"{section_name}.providers must be a list")
        ids: list[str] = []
        allowed_kinds = (
            {"dashscope-qwen-realtime", "dashscope-task-asr", "openai-audio-transcriptions"}
            if section_name == "asr"
            else {"openai-compatible", "qwen-mt"}
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
                    "pricePerMillionOutputTokensCny",
                )
            )
            for field in pricing_fields:
                if field not in provider or provider[field] is None:
                    continue
                value = provider[field]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                    raise ValueError(f"{section_name} provider {field} must be a non-negative number")
        if section.get("active") not in ids:
            raise ValueError(f"{section_name}.active must name a configured provider")
        if section_name == "translation":
            fallback = section.get("fallback", [])
            if not isinstance(fallback, list) or any(item not in ids for item in fallback):
                raise ValueError("translation.fallback must contain configured provider ids")


def resolve_secrets(
    config: dict[str, Any], env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    resolved = copy.deepcopy(config)
    environment = os.environ if env is None else env
    for provider in _providers(resolved):
        inline = provider.pop("apiKey", None)
        env_name = provider.get("apiKeyEnv")
        provider["_apiKey"] = inline if inline is not None else environment.get(env_name, "")
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
            "version": 2,
            "asr": copy.deepcopy(asr_settings),
            "translation": copy.deepcopy(translation_settings),
            "subtitle": copy.deepcopy(settings.get("subtitle", persisted.get("subtitle", {}))),
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
    return view


def update_config(path: str | Path, patch: dict[str, Any]) -> dict[str, Any]:
    allowed = {"asr", "translation", "subtitle"}
    if set(patch) - allowed:
        raise ValueError("only asr, translation, and subtitle may be updated")
    path = Path(path)
    if not path.exists():
        atomic_write_config(path, DEFAULT_CONFIG)
    with path.open("r", encoding="utf-8") as handle:
        persisted = json.load(handle)
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
    if "subtitle" in patch:
        if not isinstance(patch["subtitle"], dict):
            raise ValueError("subtitle must be an object")
        persisted["subtitle"] = copy.deepcopy(patch["subtitle"])
    validate_config(persisted)
    atomic_write_config(path, persisted)
    return load_config(path)


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
