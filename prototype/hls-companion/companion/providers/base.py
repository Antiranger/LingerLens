from __future__ import annotations

import abc
import dataclasses
from typing import AsyncIterator, Literal

from ..languages import LanguageNotSupportedError, canonicalize_tag, primary_subtag

# 币种跟着厂商走，不跟界面语言走：下面这些按人民币报价，其余按美元。
# 配置里存下来的 pricePerSecondCny / pricePerMillion*Cny 是历史命名，那些
# 数字真正的单位由 provider_currency() 决定，所以读代码时别看字段名就下结论。
CNY_PROVIDER_KINDS = frozenset({
    "dashscope-qwen-realtime",
    "dashscope-task-asr",
    "dashscope-livetranslate-realtime",
    "volcengine-sauc",
    "tencent-asr",
})
SUPPORTED_CURRENCIES = ("CNY", "USD")


def provider_currency(kind: object, override: object = None) -> str:
    """Currency that a provider's configured prices are denominated in."""
    if isinstance(override, str) and override.upper() in SUPPORTED_CURRENCIES:
        return override.upper()
    return "CNY" if kind in CNY_PROVIDER_KINDS else "USD"


def format_currency_symbol(currency: object) -> str:
    return "¥" if provider_currency(None, currency) == "CNY" else "$"


class ProviderError(RuntimeError):
    """Normalized translation Provider failure (spec 5.4).

    Subclasses give the FallbackChain deterministic signals: auth/config and
    request errors mark a provider as misconfigured (no retry of the current
    request), rate limits and 5xx/529 failures count toward consecutive-failure
    cooldowns, and timeouts keep the existing deadline semantics.
    """


class ProviderAuthError(ProviderError):
    """401/403/bad API key. Not retryable for the current request."""


class ProviderRequestError(ProviderError):
    """400-level: invalid schema, unsupported model or language pair.

    Marks the Provider as misconfigured and immediately falls back; no
    automatic retry.
    """


class ProviderRefusalError(ProviderError):
    """The model refused or produced no usable translation text (empty,
    refusal, safety block, or non-text response). Treated as a failure, never
    as a successful empty translation."""


class ProviderRateLimitError(ProviderError):
    """429 rate limit. Fallback immediately; short cooldown before retry."""


class ProviderUnavailableError(ProviderError):
    """5xx / Anthropic 529 / network disconnect. Counts toward consecutive
    failures; cooldown at threshold."""


# ``asyncio.TimeoutError`` keeps the existing deadline semantics.



@dataclasses.dataclass(frozen=True)
class SourceLanguagePolicy:
    """The user's instruction for recognizing spoken audio.

    Exactly two MVP modes:

    * ``specified`` -- one canonical ``tag``; no candidates.
    * ``detect`` -- optional canonical ``candidates`` (empty means unrestricted
      detection, which only some Providers accept), optional ``preferred``
      (must be one of the candidates), and ``allow_code_switching``.

    ``auto`` is never stored as a language tag.
    """

    mode: Literal["specified", "detect"]
    tag: str | None = None
    candidates: tuple[str, ...] = ()
    preferred: str | None = None
    allow_code_switching: bool = False

    @staticmethod
    def specified(tag: str) -> "SourceLanguagePolicy":
        return SourceLanguagePolicy(mode="specified", tag=canonicalize_tag(tag))

    @staticmethod
    def detect(
        candidates: tuple[str, ...] = (),
        preferred: str | None = None,
        allow_code_switching: bool = False,
    ) -> "SourceLanguagePolicy":
        return SourceLanguagePolicy(
            mode="detect",
            candidates=tuple(candidates),
            preferred=preferred,
            allow_code_switching=allow_code_switching,
        )

    def __post_init__(self) -> None:
        if self.mode == "specified":
            if not self.tag:
                raise ValueError("a specified source language policy requires a tag")
            object.__setattr__(self, "tag", canonicalize_tag(self.tag))
            if self.candidates or self.preferred:
                raise ValueError("a specified source language policy takes no candidates")
        elif self.mode == "detect":
            if self.tag is not None:
                raise ValueError("a detect source language policy takes no fixed tag")
            canonical_candidates = tuple(dict.fromkeys(canonicalize_tag(item) for item in self.candidates))
            object.__setattr__(self, "candidates", canonical_candidates)
            if self.preferred is not None:
                preferred = canonicalize_tag(self.preferred)
                if preferred not in canonical_candidates:
                    raise ValueError("preferred detection language must be one of the candidates")
                object.__setattr__(self, "preferred", preferred)
        else:
            raise ValueError(f"unknown source language policy mode: {self.mode!r}")

    @staticmethod
    def from_json(value: object) -> "SourceLanguagePolicy":
        """Parse a stored policy, accepting the legacy v2 plain-tag string."""
        if isinstance(value, str):
            return SourceLanguagePolicy.specified(value)
        if not isinstance(value, dict):
            raise ValueError("sourceLanguage must be a tag string or a policy object")
        mode = value.get("mode")
        if mode == "specified":
            if value.get("candidates") or value.get("preferred"):
                raise ValueError("a specified source language policy takes no candidates")
            return SourceLanguagePolicy.specified(str(value.get("tag") or ""))
        if mode == "detect":
            candidates = value.get("candidates", [])
            if not isinstance(candidates, list) or any(not isinstance(item, str) for item in candidates):
                raise ValueError("detect candidates must be a list of language tags")
            preferred = value.get("preferred")
            return SourceLanguagePolicy.detect(
                candidates=tuple(candidates),
                preferred=str(preferred) if preferred else None,
                allow_code_switching=bool(value.get("allowCodeSwitching", False)),
            )
        raise ValueError("sourceLanguage mode must be 'specified' or 'detect'")

    def to_json(self) -> dict[str, object]:
        if self.mode == "specified":
            return {"mode": "specified", "tag": self.tag}
        payload: dict[str, object] = {
            "mode": "detect",
            "candidates": list(self.candidates),
            "allowCodeSwitching": self.allow_code_switching,
        }
        if self.preferred is not None:
            payload["preferred"] = self.preferred
        return payload

    @property
    def fallback_language(self) -> str | None:
        """Language assumed when the Provider reports none for a cue."""
        if self.mode == "specified":
            return self.tag
        return self.preferred or (self.candidates[0] if self.candidates else None)


@dataclasses.dataclass(frozen=True)
class ASRLanguageCapabilities:
    """What a Provider profile can do with languages (not just a tag list).

    ``supported_tags`` is None for open-ended models. ``detection`` is one of
    ``none`` (a language must be specified), ``unrestricted`` (detect from
    anything) or ``candidates`` (detect within a caller-supplied list bounded
    by ``max_candidates``). ``tier`` is the support evidence level:
    ``verified``, ``provider_claimed`` or ``experimental``. Unknown custom
    models stay experimental and never claim detection or code-switching.
    """

    supported_tags: tuple[str, ...] | None = None
    detection: Literal["none", "unrestricted", "candidates"] = "none"
    max_candidates: int | None = None
    reports_detected_language: bool = False
    code_switching: bool = False
    tier: Literal["verified", "provider_claimed", "experimental"] = "experimental"
    detection_tags: tuple[str, ...] | None = None
    """Tags detection candidates are checked against when detection is bounded
    by a smaller set than ``supported_tags`` (e.g. Deepgram nova-3: many
    specified languages, but code-switching ``multi`` detection only covers a
    fixed 10-language set). None means candidates are checked against
    ``supported_tags``."""


ObservationKind = Literal[
    "stable_token_delta",
    "token_snapshot",
    "stable_prefix_snapshot",
    "text_snapshot",
    "utterance_final",
    "endpoint",
]

CaptionCutReason = Literal[
    "clause_boundary",
    "terminal_punctuation",
    "weak_punctuation",
    "speaker_change",
    "strong_gap",
    "weak_gap",
    "language_hint",
    "hard_deadline",
    "utterance_endpoint",
    "final_window",
]


@dataclasses.dataclass(frozen=True)
class RecognitionToken:
    """Provider-neutral lexical recognition evidence.

    Adapters must exclude protocol/control tokens before constructing this
    value. Times are seconds on the provider session audio timeline; callers
    may replace them with pipeline PCM coordinates before chunking.
    ``provider_stable=False`` means LingerLens may only publish the token after
    LocalAgreement policy commits it.
    """

    text: str
    begin_pcm: float | None
    end_pcm: float | None
    provider_stable: bool
    language: str | None = None
    speaker: str | None = None
    confidence: float | None = None
    # Raw immutable tokenizer pieces preserve their exact whitespace and may
    # end inside a word. Only the shared linguistic chunker may cut them.
    is_piece: bool = False


@dataclasses.dataclass(frozen=True)
class CaptionObservation:
    """A discriminated snapshot/delta of recognition evidence.

    Delta kinds append exactly once. Snapshot kinds replace the complete
    mutable state for ``generation/item_id`` (including an empty snapshot).
    ``utterance_final`` is authoritative reconciliation evidence and does not
    imply that one final must become one Subtitle Cue. ``endpoint`` only
    releases already stable evidence; it MUST NOT close an item because VAD
    stop can precede completed transcription. Final segment completion is
    authoritative for that item, not a guarantee of grammatical completeness.
    Mutable text_snapshot/tentative_text is never provider-stable evidence.
    """

    kind: ObservationKind
    generation: int
    item_id: str
    revision: int | None = None
    tokens: tuple[RecognitionToken, ...] = ()
    stable_text: str = ""
    tentative_text: str = ""
    begin_pcm: float | None = None
    end_pcm: float | None = None
    language: str | None = None
    speaker: str | None = None


@dataclasses.dataclass(frozen=True)
class NativeTranslationCapabilities:
    """Provider-side translation on the ASR session itself.

    A few realtime models (Soniox two-way translation, Qwen3-LiveTranslate)
    recognise speech *and* emit its translation on the same WebSocket. When
    ``enabled`` the Profile owns translation: LingerLens does not call a
    separate translation Provider for these cues, and the Adapter's own target
    language selection is what the Provider was configured with.

    ``enabled`` is per *profile*, not per model: it is True only when the saved
    options actually ask the Provider to translate, so the same model with the
    option off stays a plain ASR profile.
    """

    enabled: bool = False
    target_tags: tuple[str, ...] | None = None
    """Targets the Profile will translate into; None means open-ended."""
    two_way: bool = False
    """Whether the Provider keeps both languages of a pair live at once."""
    glossary: bool = False
    """Whether the Profile's own term/context list steers the translation."""
    reports_source_language: bool = True
    tier: Literal["verified", "provider_claimed", "experimental"] = "experimental"


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
    """Whether a mid-speech input_audio_buffer.commit is honored.

    Declarative only: no caller reads this today. The pipeline deliberately
    never forces a mid-speech commit, because advancing audio cannot make a
    lexical boundary linguistically safe -- cut decisions belong to
    CaptionChunker. Kept on the Adapter contract so the capability stays
    documented rather than silently lost.
    """
    language: ASRLanguageCapabilities = ASRLanguageCapabilities()
    preferred_sample_rate: int | None = None
    """Sample rate the pipeline should negotiate for this Provider (16/24 kHz)."""
    speaker_labels: bool = False
    """Whether this profile reports realtime speaker/diarization labels.

    True only when the model preset supports diarization AND the profile
    options enable it. Speaker metadata is backend-owned (Ticket 02): it is
    carried on ASREvent/Cue for future UI use, never rendered today."""
    caption_evidence: frozenset[ObservationKind] = frozenset(("utterance_final",))
    """Recognition evidence kinds emitted by this Adapter.

    The default preserves final-only fake/providers and positional
    constructors. Hybrid Adapters declare every kind they can emit."""
    native_translation: NativeTranslationCapabilities = NativeTranslationCapabilities()
    """Provider-side translation carried on the same session (see above)."""



def validate_source_policy(policy: SourceLanguagePolicy, capabilities: ASRLanguageCapabilities) -> None:
    """Reject a source policy the ASR profile cannot honor (pre-start)."""
    if policy.allow_code_switching and not capabilities.code_switching:
        raise LanguageNotSupportedError("this ASR profile does not support code-switching (mixed-language) detection")
    if policy.mode == "specified":
        assert policy.tag is not None
        if not _tag_supported(policy.tag, capabilities.supported_tags):
            raise LanguageNotSupportedError(
                f"source language {policy.tag} is not supported by this ASR profile"
                + (f" (supported: {', '.join(capabilities.supported_tags)})" if capabilities.supported_tags else "")
            )
        return
    if capabilities.detection == "none":
        raise LanguageNotSupportedError("this ASR profile cannot auto-detect the source language; specify one")
    if not policy.candidates and capabilities.detection == "candidates":
        raise LanguageNotSupportedError("this ASR profile requires detection candidate languages")
    if capabilities.max_candidates is not None and len(policy.candidates) > capabilities.max_candidates:
        raise LanguageNotSupportedError(
            f"too many detection candidates: at most {capabilities.max_candidates} for this ASR profile"
        )
    # Detection candidates are validated against detection_tags when the
    # profile's detectable set is smaller than its specified-language set.
    candidate_scope = (
        capabilities.detection_tags if capabilities.detection_tags is not None else capabilities.supported_tags
    )
    unsupported = [tag for tag in policy.candidates if not _tag_supported(tag, candidate_scope)]
    if unsupported:
        raise LanguageNotSupportedError(
            f"detection candidates not supported by this ASR profile: {', '.join(unsupported)}"
        )


def _tag_supported(tag: str, supported_tags: tuple[str, ...] | None) -> bool:
    """BCP 47 basic-range match: an unqualified supported tag (``zh``)
    covers more specific policy tags (``zh-Hans``); the reverse is false."""
    if supported_tags is None:
        return True
    if tag in supported_tags:
        return True
    primary = primary_subtag(tag)
    return any(
        primary_subtag(supported) == primary and supported == primary
        for supported in supported_tags
    )


ASREventType = Literal[
    "speech_started", "speech_stopped", "interim", "final", "usage", "error",
    "speaker_revision", "translation",
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
    speaker: str | None = None
    """Dominant speaker label for this utterance (diarization profiles only).

    Rendered from the Provider's own label; the adapter picks the most frequent
    word/token label and never invents one. None for Providers whose protocol
    reports no speaker (or where speaker output is undocumented). A post-hoc
    diarization fix is reported as a ``speaker_revision`` event with the same
    ``item_id`` -- the owning backend decides what to do with it; there is
    deliberately no speaker UI in Ticket 02.
    """
    confidence: float | None = None
    """Provider-reported transcript confidence in [0, 1], when the protocol
    carries one (e.g. Soniox token confidence averaged over the utterance).
    Never fabricated; None when the protocol reports none."""
    caption_observation: CaptionObservation | None = None
    """Optional normalized evidence for the provider-neutral CaptionChunker.

    Legacy fields remain authoritative for the current SubtitlePipeline until
    its separate integration ticket; adding this field is migration-safe."""
    translation: str = ""
    """Provider-side translation of this event's own evidence.

    Only Adapters whose Profile reports ``capabilities.native_translation``
    fill this. It is the confirmed translation text for the same speech the
    event's ``text``/observation covers, on the Provider's own segment
    boundaries -- the Adapter never splits or reorders it. Empty for every
    other Adapter, which is why it is a separate field rather than a reuse of
    ``stash`` (that one is a tentative *source* tail)."""
    translation_stash: str = ""
    """Tentative, still-revisable provider translation tail.

    Published only for diagnostics and tests: the pipeline resolves cues from
    ``translation`` alone, because a tentative tail may be rewritten after the
    cue has already been shown."""
    translation_anchors: tuple[tuple[str, str], ...] = ()
    """(source text, translation text) cumulative prefix pairs from the Adapter.

    They are semantic alignment evidence only when ``translation_anchors_trusted``
    is true. Some Providers expose arrival-time snapshots that are useful for
    diagnostics but do not prove correspondence, so the bus must not consume
    them merely because the tuple is populated."""
    translation_anchors_trusted: bool = False
    """Whether ``translation_anchors`` are Provider-order alignment boundaries.

    Soniox sets this only for boundaries derived from its documented unified
    token ordering (source chunk -> translation chunk -> next source chunk).
    Adapters whose snapshots are only coincident in time leave it false."""


class ASRStream(abc.ABC):
    """One ASR session. Reconnecting implementations must drop, not buffer, audio."""

    @abc.abstractmethod
    async def push_pcm(self, chunk: bytes, pcm_offset: float) -> None:
        """Push 16-bit LE mono PCM and its offset from the stream origin."""

    @abc.abstractmethod
    async def flush(self) -> None: ...

    async def commit(self) -> None:
        """Force an utterance break now.

        NOT called by the pipeline: forcing a break mid-speech cuts inside words
        and the resulting final arrives without a preceding speech_stopped, so
        the cue loses its end boundary. Server VAD already segments around 400ms.
        Segmentation is owned entirely by CaptionChunker. Implementations remain
        because they are protocol-correct for each Provider and are exercised by
        the Adapter tests.
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
    # Set from provider_currency() in each implementation; the pipeline reads it
    # to label the cost, so a wrong value is a wrong money figure.
    currency: str = "USD"
    requires_api_key: bool = False
    """Cloud Providers set True so a missing key is reported before playback
    starts; local/self-hosted endpoints may accept an empty key."""
    translation_target: str | None = None
    """Canonical target Language Tag for Profile-side translation.

    Set by SubtitlePipeline.set_translation_target before every session start
    for Profiles whose ``capabilities.native_translation`` is enabled. It is
    deliberately NOT a ``stream()`` keyword: that would make all twelve other
    Adapters grow a parameter they must ignore, and the target is a saved
    Profile setting (the viewer's Target Language) rather than something
    negotiated per session."""

    def set_translation_target(self, target_tag: str | None) -> None:
        """Record the Target Language a native-translation Profile must use.

        No-op for every Profile that does not translate on its own session.
        """
        self.translation_target = target_tag

    @property
    @abc.abstractmethod
    def capabilities(self) -> ASRCapabilities: ...

    @abc.abstractmethod
    async def stream(
        self,
        *,
        policy: SourceLanguagePolicy,
        sample_rate: int,
        hotwords: list[str],
        context: list[str],
    ) -> ASRStream:
        """Open one session under the negotiated policy and PCM sample rate.

        The pipeline validates the policy against ``capabilities.language``
        before starting; an adapter still re-checks and raises
        ``LanguageNotSupportedError`` rather than silently transcribing in the
        wrong language.
        """


@dataclasses.dataclass(frozen=True)
class TranslationLanguageCapabilities:
    """Language-pair ability of a translation Provider profile.

    ``open_world_prompting`` marks generic LLM Providers that accept any
    canonical pair best-effort (experimental tier per pair). ``supported_pairs``
    is None unless the Provider has a closed pair contract (e.g. dedicated MT).
    """

    source_tags: tuple[str, ...] | None = None
    target_tags: tuple[str, ...] | None = None
    supported_pairs: tuple[tuple[str, str], ...] | None = None
    open_world_prompting: bool = False
    tier: Literal["verified", "provider_claimed", "experimental"] = "experimental"


def validate_translation_pair(
    source_tag: str | None,
    target_tag: str,
    capabilities: TranslationLanguageCapabilities,
) -> None:
    """Reject a source->target pair the translation profile cannot honor."""
    if capabilities.open_world_prompting and capabilities.supported_pairs is None and capabilities.target_tags is None:
        return
    if capabilities.supported_pairs is not None:
        if source_tag is None:
            return  # detection still open: pairs are checked per cue later
        if (source_tag, target_tag) not in capabilities.supported_pairs and (None, target_tag) not in capabilities.supported_pairs:
            raise LanguageNotSupportedError(
                f"this translation profile cannot translate {source_tag} -> {target_tag}"
            )
        return
    if capabilities.target_tags is not None and not _tag_supported(target_tag, capabilities.target_tags):
        raise LanguageNotSupportedError(
            f"target language {target_tag} is not supported by this translation profile"
        )
    if capabilities.source_tags is not None and source_tag is not None and not _tag_supported(source_tag, capabilities.source_tags):
        raise LanguageNotSupportedError(
            f"source language {source_tag} is not supported by this translation profile"
        )


@dataclasses.dataclass(frozen=True)
class TranslationCapabilities:
    rolling_context: bool
    glossary: bool
    domains: bool
    json_output: bool
    max_input_chars: int
    language: TranslationLanguageCapabilities = TranslationLanguageCapabilities()


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
    # Prior source text is immediately usable; None means its translation
    # is unavailable. Never fabricate a target or wait for it to arrive.
    history: list[tuple[str, str | None]]
    glossary: list[tuple[str, str]]
    deadline_monotonic: float | None = None
    generation: int | None = None
    chunk_order: int | None = None
    starts_mid_sentence: bool | None = None
    ends_mid_sentence: bool | None = None
    cut_reason: CaptionCutReason | None = None
    purpose: str = "subtitle"
    item_id: str | None = None
    """Provider utterance id the cue was built from, when the caller knows it.

    Only session-backed translation (``native_translation``) reads this: the
    Provider's own translation segments are keyed by the same item id the
    caption evidence carried, so a cue can be resolved to the exact segment it
    came from instead of guessing from text similarity."""


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
