"""Pure rolling translation context and deterministic prompt construction."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class StreamMeta:
    title: str | None
    channel: str | None
    domain: str | None
    source_lang: str
    target_lang: str


@dataclasses.dataclass(frozen=True)
class ContextPair:
    source: str
    translation: str
    timestamp: float


_LANGUAGE_NAMES = {
    "ja": "日语",
    "zh": "中文",
    "en": "英语",
    "ko": "韩语",
    "de": "德语",
    "fr": "法语",
    "es": "西班牙语",
}


class RollingContext:
    """Hold translated pairs and expose a count- and age-trimmed history window."""

    def __init__(self, context_pairs: int = 10, context_seconds: float = 90.0) -> None:
        if context_pairs < 0:
            raise ValueError("context_pairs cannot be negative")
        if context_seconds < 0:
            raise ValueError("context_seconds cannot be negative")
        self.context_pairs = context_pairs
        self.context_seconds = context_seconds
        self._pairs: list[ContextPair] = []

    def add(self, source: str, translation: str, timestamp: float) -> None:
        self._pairs.append(ContextPair(source, translation, timestamp))

    def history(self, now: float, *, pair_limit: int | None = None) -> list[tuple[str, str]]:
        limit = self.context_pairs if pair_limit is None else min(self.context_pairs, max(0, pair_limit))
        cutoff = now - self.context_seconds
        eligible = [pair for pair in self._pairs if pair.timestamp >= cutoff]
        if limit == 0:
            return []
        return [(pair.source, pair.translation) for pair in eligible[-limit:]]

    def trim(self, now: float) -> None:
        """Discard entries too old ever to appear in the configured window."""

        cutoff = now - self.context_seconds
        self._pairs = [pair for pair in self._pairs if pair.timestamp >= cutoff]


def _language_name(code: str) -> str:
    return _LANGUAGE_NAMES.get(code, code)


def build_system_prompt(
    meta: StreamMeta,
    glossary: list[tuple[str, str]],
    *,
    glossary_limit: int = 30,
) -> str:
    """Build the exact fixed system-prompt skeleton from the implementation plan."""

    terms = glossary[:glossary_limit]
    glossary_lines = "\n".join(f"{term} => {target}" for term, target in terms)
    source = _language_name(meta.source_lang)
    target = _language_name(meta.target_lang)
    title = meta.title or "未知"
    channel = meta.channel or "未知"
    domain = meta.domain or "未知"
    return (
        f"你是直播字幕翻译器。把 CURRENT 从{source}译成{target}。\n"
        "规则：\n"
        "1. 只输出 CURRENT 的译文，不要输出解释、不要重复 HISTORY。\n"
        "2. HISTORY 只用于理解指代、省略主语和话题，不要翻译它。\n"
        "3. 译文要像直播字幕：简洁、口语、可一眼读完。\n"
        "4. 不要补全说话人没说完的内容，不要添加未表达的事实。\n"
        "5. 人名/专有名词严格遵循术语表。\n"
        "6. 只输出译文本身，不加引号、不加前缀。\n"
        f"直播信息：{title} / {channel} / 领域：{domain}\n"
        f"术语表：\n{glossary_lines}"
    )


def build_user_prompt(source_text: str, history: list[tuple[str, str]]) -> str:
    """Build the mutable HISTORY/CURRENT user-prompt suffix."""

    history_lines = "\n".join(f"{source} -> {translation}" for source, translation in history)
    return f"HISTORY:\n{history_lines}\nCURRENT:\n{source_text}"


def build_prompt(
    source_text: str,
    meta: StreamMeta,
    history: list[tuple[str, str]],
    glossary: list[tuple[str, str]],
) -> tuple[str, str]:
    return build_system_prompt(meta, glossary), build_user_prompt(source_text, history)
