from __future__ import annotations

import abc
import dataclasses
from typing import AsyncIterator, Literal


@dataclasses.dataclass(frozen=True)
class ASRCapabilities:
    streaming: bool
    interim_results: bool
    stable_prefix: bool
    server_vad: bool
    word_timestamps: bool
    hotwords: bool
    context: bool
    languages: tuple[str, ...]
    sample_rates: tuple[int, ...]
    manual_commit: bool = False
    """Whether a mid-speech input_audio_buffer.commit is honored (Fix F)."""


ASREventType = Literal[
    "speech_started", "speech_stopped", "interim", "final", "usage", "error"
]


@dataclasses.dataclass
class ASREvent:
    type: ASREventType
    text: str = ""
    stash: str = ""
    begin_pcm: float | None = None
    end_pcm: float | None = None
    language: str | None = None
    message: str = ""
    raw: dict | None = None
    item_id: str | None = None
    """Provider-side utterance id.

    Realtime ASR reports an utterance's boundaries and its transcript in
    *separate* events that interleave with the next utterance's events (a
    ``final`` and the next ``speech_started`` were observed carrying the same
    millisecond timestamp).  ``item_id`` is the provider's own join key, so the
    pipeline never has to rely on event ordering to pair them.

    ``begin_pcm``/``end_pcm`` on VAD events are offsets into the audio the
    *provider* has received on the current session -- not wall clock, and not
    the pipeline's ``_pcm_offset``; see SubtitlePipeline._server_to_pipeline.
    """


class ASRStream(abc.ABC):
    """One ASR session. Reconnecting implementations must drop, not buffer, audio."""

    @abc.abstractmethod
    async def push_pcm(self, chunk: bytes, pcm_offset: float) -> None:
        """Push 16-bit LE mono PCM and its offset from the stream origin."""

    @abc.abstractmethod
    async def flush(self) -> None: ...

    async def commit(self) -> None:
        """Force an utterance break now (hard utterance cap, Fix F).

        Providers that do not support manual commits under server VAD simply
        keep this no-op default; the pipeline degrades gracefully.
        """
        return None

    @abc.abstractmethod
    def __aiter__(self) -> AsyncIterator[ASREvent]: ...

    @abc.abstractmethod
    async def aclose(self) -> None: ...


class ASRProvider(abc.ABC):
    id: str
    label: str
    model: str
    price_per_second_cny: float | None

    @property
    @abc.abstractmethod
    def capabilities(self) -> ASRCapabilities: ...

    @abc.abstractmethod
    async def stream(
        self, *, language: str, hotwords: list[str], context: list[str]
    ) -> ASRStream: ...


@dataclasses.dataclass(frozen=True)
class TranslationCapabilities:
    rolling_context: bool
    glossary: bool
    domains: bool
    json_output: bool
    max_input_chars: int


@dataclasses.dataclass(frozen=True)
class StreamMeta:
    title: str | None
    channel: str | None
    domain: str | None
    source_lang: str
    target_lang: str


@dataclasses.dataclass
class TranslationRequest:
    source_text: str
    meta: StreamMeta
    history: list[tuple[str, str]]
    glossary: list[tuple[str, str]]
    deadline_monotonic: float | None = None


@dataclasses.dataclass
class TranslationResult:
    text: str
    provider_id: str
    latency_ms: int
    usage: dict | None = None


class TranslationProvider(abc.ABC):
    id: str
    label: str
    model: str

    @property
    @abc.abstractmethod
    def capabilities(self) -> TranslationCapabilities: ...

    @abc.abstractmethod
    async def translate(self, request: TranslationRequest) -> TranslationResult: ...
