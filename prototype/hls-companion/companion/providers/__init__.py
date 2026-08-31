from __future__ import annotations

from typing import Any, Callable

from .base import ASRProvider, TranslationProvider

ProviderFactory = Callable[[dict[str, Any]], object]
REGISTRY: dict[str, ProviderFactory] = {}


def register(kind: str) -> Callable[[ProviderFactory], ProviderFactory]:
    def decorator(factory: ProviderFactory) -> ProviderFactory:
        if kind in REGISTRY:
            raise ValueError(f"provider kind already registered: {kind}")
        REGISTRY[kind] = factory
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
    factory = REGISTRY.get(kind)
    if factory is None:
        available = ", ".join(sorted(REGISTRY)) or "(none)"
        raise ValueError(f"unknown provider kind {kind!r}; available kinds: {available}")
    return factory(config)


# Import built-ins for registration side effects.
from . import asr_dashscope_task as _asr_dashscope_task  # noqa: E402,F401
from . import asr_qwen_realtime as _asr_qwen_realtime  # noqa: E402,F401
from . import mt_openai_compat as _mt_openai_compat  # noqa: E402,F401
from . import mt_qwen_mt as _mt_qwen_mt  # noqa: E402,F401

__all__ = [
    "REGISTRY",
    "create_asr",
    "create_translation",
    "register",
]
