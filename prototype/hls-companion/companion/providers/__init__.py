from __future__ import annotations

from typing import Any, Callable, TypeVar, cast

from .base import ASRProvider, TranslationProvider

ProviderFactory = Callable[[dict[str, Any]], object]
TFactory = TypeVar("TFactory", bound=ProviderFactory)
REGISTRY: dict[str, ProviderFactory] = {}


def register(kind: str) -> Callable[[TFactory], TFactory]:
    def decorator(factory: TFactory) -> TFactory:
        if kind in REGISTRY:
            raise ValueError(f"provider kind already registered: {kind}")
        REGISTRY[kind] = cast(ProviderFactory, factory)
        return factory

    return decorator


def create_asr(config: dict[str, Any]) -> ASRProvider:
    provider = _create(config)
    if not isinstance(provider, ASRProvider):
        raise TypeError(f"provider kind {config.get('kind')!r} is not an ASR provider")
    return provider


def create_translation(config: dict[str, Any]) -> TranslationProvider:
    provider = _create(config)
    if not isinstance(provider, TranslationProvider):
        raise TypeError(f"provider kind {config.get('kind')!r} is not a translation provider")
    return provider


def _create(config: dict[str, Any]) -> object:
    kind = config.get("kind")
    factory = REGISTRY.get(kind) if isinstance(kind, str) else None
    if factory is None:
        available = ", ".join(sorted(REGISTRY)) or "(none)"
        raise ValueError(f"unknown provider kind {kind!r}; available kinds: {available}")
    return factory(config)


# Import built-ins for registration side effects.
from . import asr_dashscope_task as _asr_dashscope_task  # noqa: E402,F401
from . import asr_dashscope_livetranslate as _asr_dashscope_livetranslate  # noqa: E402,F401
from . import asr_qwen_realtime as _asr_qwen_realtime  # noqa: E402,F401
from . import asr_openai_transcriptions as _asr_openai_transcriptions  # noqa: E402,F401
from . import asr_deepgram_streaming as _asr_deepgram_streaming  # noqa: E402,F401
from . import asr_soniox_realtime as _asr_soniox_realtime  # noqa: E402,F401
from . import asr_openai_realtime_transcription as _asr_openai_realtime_transcription  # noqa: E402,F401
from . import asr_assemblyai_streaming as _asr_assemblyai_streaming  # noqa: E402,F401
from . import asr_volcengine_sauc as _asr_volcengine_sauc  # noqa: E402,F401
from . import asr_elevenlabs_scribe_realtime as _asr_elevenlabs_scribe_realtime  # noqa: E402,F401
from . import asr_speechmatics_realtime as _asr_speechmatics_realtime  # noqa: E402,F401
from . import asr_tencent_asr as _asr_tencent_asr  # noqa: E402,F401
from . import mt_openai_compat as _mt_openai_compat  # noqa: E402,F401
from . import mt_anthropic_messages as _mt_anthropic_messages  # noqa: E402,F401
from . import mt_google_genai as _mt_google_genai  # noqa: E402,F401

__all__ = [
    "REGISTRY",
    "create_asr",
    "create_translation",
    "register",
]
