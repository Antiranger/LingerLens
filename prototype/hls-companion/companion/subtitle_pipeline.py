"""Live private-HLS-to-subtitle pipeline with playback failure isolation.

The private HLS playlist is the single media authority for both subtitle audio
and the delayed public playlist. FFmpeg preserves its timestamps while decoding
PCM, so every cue maps directly onto the playlist's PDT timeline:

    cue.tEnd = media_epoch + end_pcm

There is no independently sampled ingest clock and no wall-clock fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from typing import Any, Protocol

from .caption_chunker import CaptionChunk, CaptionChunker, ChunkerDecision
from .acoustic_onset import AcousticOnsetIndex
from .context_manager import RollingContext
from .providers.http import translation_session
from .logbook import record as log_record
from .media_anchor import MediaAnchor
from .languages import canonicalize_tag_or_none, canonicalize_target_tag
from .providers.base import (
    ASREvent,
    ASRProvider,
    CaptionCutReason,
    ASRStream,
    SourceLanguagePolicy,
    StreamMeta,
    ProviderRefusalError,
    TranslationProvider,
    TranslationRequest,
)
from .subtitle_store import Cue, CueStore, TimingSource
from .subtitle_text import (
    calculate_hold,
    clean_subtitle_text,
    is_duplicate_final,
    newly_confirmed_sentences,
)
from .translation_budget import TranslationBudget, TranslationBudgetPolicy

PCM_BYTES_PER_SECOND = 16_000 * 2
PCM_CHUNK_BYTES = 3_200
PCM_CHUNK_SECONDS = PCM_CHUNK_BYTES / PCM_BYTES_PER_SECOND
ASR_VIEWER_LEAD_ALLOWANCE_SECONDS = 1.0
ASR_VIEWER_LEAD_POLL_SECONDS = 0.05

# Flow control on the audio handed to the ASR was attempted twice and both
# signals were measured wrong on 2026-09-18, so the feed is deliberately not
# throttled. What that cost, and why, is recorded here because the same two ideas
# will look attractive again:
#
#   * Pacing to the renderer's playhead (commit 49e8f8c) drained the ASR queue
#     (asrAdapterDelay P50 2.4-5.8s -> 1.5s) but captions then arrived late, since
#     the lead it removed IS the caption's margin: median 0.66s after the window
#     start against 0.23s, P90 3.37s against 0.74s, 24 of 39 captions late
#     against 4 of 16 and 10 of 34 before.
#   * Bounding the queue by the provider's own confirmation (commit a27d08d) was
#     worse, because evidence only advances while somebody is SPEAKING. During
#     music or game audio the confirmation stops, the feed was held back, and the
#     next sentence arrived at a starved provider. Same stream, same room, 125s:
#     34 captions with the bound off against 3 with it on, and asrAdapterDelay P50
#     1.3s against 6.4s. A silent provider is not a busy one.
#
# The measurements that showed this are still reported: `viewerLeadSeconds` (how
# far the audio runs ahead of the viewer) and `asrQueueSeconds` (how much has been
# handed over that the provider has not confirmed).
VIEWER_POSITION_TTL_SECONDS = 8.0
# A stated playhead this far from our own clock is not a playhead (a reloaded
# page, a stale tab, a wrong system clock). Reject it rather than report it.
VIEWER_POSITION_SANITY_SECONDS = 600.0

# Finals held while the media timeline is unavailable. The count bound protects
# memory; the grace lets a slightly late cue still be published once the
# timeline lands, while anything older than the whole delay window is beyond the
# viewer's playhead and can never be shown.
_PENDING_FINALS_MAX = 256
_PENDING_FINAL_GRACE_SECONDS = 10.0
# Teardown budget. Stop is a user action: it may spend this long letting the
# provider finish the sentence it already received, and not one millisecond
# more, whatever a socket or a provider does with a close.
#
# This must stay ABOVE the close window an ASR Adapter owns itself: each one
# sends its official stop frame and then drains tail finals for its own
# `closeDrainTimeoutSeconds` (default 2s) before releasing. Cutting that short
# does not save the time -- it discards the drain, and the caller then waits out
# its own window anyway. Measured 2026-09-17 with Soniox: a 2s cap here turned a
# 2.4s Stop into 5.5s, because the cancelled close consumed both budgets.
_STOP_ASR_CLOSE_SECONDS = 5.0

# The subtitle decoder's stderr has never had a reader. FFmpeg writes its
# diagnostics there for as long as the process lives, so the pipe fills and the
# write that blocks is the one immediately before the next PCM byte: a burst of
# decoder errors stops the audio that feeds recognition. Isolated repro:
# 0.1s PCM -> 1 MiB stderr -> 0.1s PCM leaves the PCM stopped at 0.1s.
#
# The read size bounds one read and one trim. The tail is a BYTE cap and not a
# line cap, because a single line can be arbitrarily long and a line count
# therefore cannot bound memory. Both numbers are choices of this change and not
# measured optima; there is one tail per decoder, so neither is configurable.
_STDERR_READ_BYTES = 4096
_STDERR_TAIL_BYTES = 65536
# What one snapshot may quote: the last complete non-empty lines of the tail.
# Two snapshots per decoder lifecycle, so the worst case is 14 records out of the
# logbook's 500 -- an error storm cannot push out the lines the user needs.
_STDERR_TAIL_LINES = 6
_STDERR_MAX_SNAPSHOTS = 2
# Terminate-to-kill grace. Not a new budget: it is the original stop's one second
# made explicit, and it runs concurrently with the provider close rather than
# after it.
_STOP_TERMINATE_GRACE_SECONDS = 1.0
# The decoder's stdout is read in the same block size the PCM reader uses, so
# teardown iterations cost what a session iteration costs.
_STDOUT_DRAIN_BYTES = PCM_CHUNK_BYTES


def _consume_task_result(task: "asyncio.Task[Any]") -> None:
    """Retrieve a finished task's result so it cannot warn later.

    Teardown cancels things it no longer waits for. A task that raised before it
    was cancelled would otherwise report "exception was never retrieved" long
    after the teardown returned, which reads like a new failure.
    """
    if task.cancelled():
        return
    with contextlib.suppress(Exception):
        task.exception()


def _usage_integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    integer = int(value)
    return integer if integer >= 0 and integer == value else None


def normalize_translation_usage(usage: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize reported translation usage without estimating tokens.

    The normalized contract is non-cached input, cached input (cache read),
    cache-write input, and output tokens. Raw shapes are detected, not
    configured:

    * Gemini (``promptTokenCount``): ``promptTokenCount`` INCLUDES cached
      content, so non-cached input subtracts ``cachedContentTokenCount``.
    * Anthropic (``cache_read_input_tokens``/``cache_creation_input_tokens``):
      ``input_tokens`` is already the non-cached input; cache read and cache
      write are reported separately (do NOT assume ``input_tokens`` includes
      cache read/write).
    * OpenAI-compatible: ``prompt_tokens`` (or ``input_tokens``) includes
      cached tokens reported under ``prompt_tokens_details.cached_tokens``.

    An entire-missing or inconsistent usage object stays unavailable (None),
    never silently zero.
    """
    if not isinstance(usage, dict):
        return None
    if "promptTokenCount" in usage:
        prompt_tokens = _usage_integer(usage.get("promptTokenCount"))
        output_tokens = _usage_integer(usage.get("candidatesTokenCount"))
        cached_tokens = _usage_integer(usage.get("cachedContentTokenCount"))
        if cached_tokens is None:
            cached_tokens = 0
        if prompt_tokens is None or output_tokens is None or cached_tokens > prompt_tokens:
            return None
        total_tokens = _usage_integer(usage.get("totalTokenCount"))
        return _normalized_usage(prompt_tokens - cached_tokens, cached_tokens, 0, output_tokens, total_tokens, usage)
    if "cache_read_input_tokens" in usage or "cache_creation_input_tokens" in usage:
        non_cached = _usage_integer(usage.get("input_tokens"))
        cached_tokens = _usage_integer(usage.get("cache_read_input_tokens"))
        cache_write_tokens = _usage_integer(usage.get("cache_creation_input_tokens"))
        output_tokens = _usage_integer(usage.get("output_tokens"))
        if non_cached is None or output_tokens is None:
            return None
        return _normalized_usage(
            non_cached,
            cached_tokens if cached_tokens is not None else 0,
            cache_write_tokens if cache_write_tokens is not None else 0,
            output_tokens,
            _usage_integer(usage.get("total_tokens")),
            usage,
        )
    input_tokens = _usage_integer(usage.get("prompt_tokens"))
    if input_tokens is None:
        input_tokens = _usage_integer(usage.get("input_tokens"))
    output_tokens = _usage_integer(usage.get("completion_tokens"))
    if output_tokens is None:
        output_tokens = _usage_integer(usage.get("output_tokens"))
    if input_tokens is None or output_tokens is None:
        return None
    details = usage.get("prompt_tokens_details")
    cached_tokens = _usage_integer(details.get("cached_tokens")) if isinstance(details, dict) else 0
    if cached_tokens is None or cached_tokens > input_tokens:
        return None
    total_tokens = _usage_integer(usage.get("total_tokens"))
    return _normalized_usage(input_tokens - cached_tokens, cached_tokens, 0, output_tokens, total_tokens, usage)


def _normalized_usage(
    non_cached_input: int,
    cached_input: int,
    cache_write_input: int,
    output_tokens: int,
    total_tokens: int | None,
    raw: dict[str, Any],
) -> dict[str, Any]:
    if total_tokens is None:
        total_tokens = non_cached_input + cached_input + cache_write_input + output_tokens
    return {
        "nonCachedInputTokens": non_cached_input,
        "cachedInputTokens": cached_input,
        "cacheWriteInputTokens": cache_write_input,
        "outputTokens": output_tokens,
        "totalTokens": total_tokens,
        "rawUsage": dict(raw),
    }


@dataclasses.dataclass
class PipelineStats:
    suppressed_by_ingest_error: int = 0
    translation_dropped: int = 0
    translation_failures: int = 0
    translation_deadline_expired: int = 0
    translation_provider_failures: int = 0
    translation_attempts: int = 0
    asr_reconnects: int = 0
    final_discarded: int = 0
    pcm_dropped: int = 0
    pending_finals_dropped: int = 0
    overlong_cues: int = 0
    source_only_cues: int = 0
    unmapped_observations: int = 0
    acoustic_onset_refinements: int = 0
    acoustic_onset_max_adjustment_ms: float = 0.0
    speaker_revisions: int = 0
    native_translation_updates: int = 0
    """Provider-side translation refreshes for an utterance still in flight."""
    native_fallback_used: int = 0
    native_translation_late_patches: int = 0
    """Source-only cues later upgraded to translated cues in-place."""
    last_error: str | None = None
    last_translation_error: str | None = None
    last_translation_latency_ms: int | None = None
    avg_translation_latency_ms: float | None = None
    last_translation_attempt_at: float | None = None
    translation_context_missing_immediate_predecessor: int = 0


class TranslationDeadlineExpired(asyncio.TimeoutError):
    """A cue missed its local display window before translation completed."""


@dataclasses.dataclass
class _PendingFinal:
    text: str
    begin_pcm: float | None
    end_pcm: float
    timing_source: TimingSource
    lang: str | None
    audio_end_wall: float
    evidence_available_mono: float | None = None
    chunk_emitted_mono: float | None = None
    speaker: str | None = None
    generation: int | None = None
    chunk_order: int | None = None
    starts_mid_sentence: bool | None = None
    ends_mid_sentence: bool | None = None
    cut_reason: CaptionCutReason | None = None
    item_id: str | None = None


@dataclasses.dataclass
class _CueLatency:
    end_pcm: float
    evidence_available: float | None
    chunk_emitted: float
    cue_stored: float
    translation_started: float | None = None
    provider_finished: float | None = None
    audio_pushed: float | None = None


class SubtitlePipeline:
    """Decode private HLS audio into source and translated subtitle cues."""

    def __init__(
        self,
        *,
        asr_provider: ASRProvider,
        cue_store: CueStore,
        meta: StreamMeta,
        translation_provider: TranslationProvider | None = None,
        fallback_translation_provider: TranslationProvider | None = None,
        translation_pricing_by_provider: dict[str, dict[str, float | None]] | None = None,
        asr_currency: str = "USD",
        glossary: list[tuple[str, str]] | None = None,
        hotwords: list[str] | None = None,
        asr_context: list[str] | None = None,
        ingest_status: Callable[[], Any] | None = None,
        ffmpeg_path: str = "ffmpeg",
        pcm_queue_chunks: int = 32,
        source_policy: SourceLanguagePolicy | None = None,
        sample_rate: int = 16000,
        hold_minimum: float = 1.2,
        hold_seconds_per_char: float = 0.06,
        hold_maximum: float = 7.0,
        overlong_chars: int = 40,
        silence_duration_ms: int = 400,
        translation_workers: int = 4,
        translation_timeout_seconds: float = 6.0,
        context_pairs: int = 10,
        context_seconds: float = 90.0,
        recovery_seconds: float = 30.0,
        playback_delay_seconds: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        subprocess_factory: Callable[..., Any] = asyncio.create_subprocess_exec,
        media_anchor: MediaAnchor | None = None,
        anchor_probe: Callable[[], float | None] | None = None,
        video_backlog: Callable[[], float | None] | None = None,
        source_pts_mapper: Callable[[float], float | None] | None = None,
        native_translation_bus: Any | None = None,
    ) -> None:
        if pcm_queue_chunks < 1:
            raise ValueError("pcm_queue_chunks must be positive")
        self.asr_provider = asr_provider
        self.translation_provider = translation_provider
        self.fallback_translation_provider = fallback_translation_provider
        self.translation_pricing_by_provider = dict(translation_pricing_by_provider or {})
        # 该 ASR Provider 的报价币种；费用只按它计价，不做任何换算。
        self.asr_currency = str(asr_currency or "USD").upper()
        self.store = cue_store
        self.meta = meta
        # The user's source-language instruction for ASR; the pipeline passes
        # it to the Provider unchanged. ``source_language`` is the fallback
        # identity used only when the Provider reports no language for a cue.
        self.source_policy = source_policy or SourceLanguagePolicy.specified(meta.source_lang)
        self.source_language = self.source_policy.fallback_language or "und"
        # Instance-owned PCM clock (MVP: 16 or 24 kHz; negotiated from the
        # active ASR profile's preferred sample rate by the caller).
        if sample_rate not in (16000, 24000):
            raise ValueError("sample_rate must be 16000 or 24000")
        self.sample_rate = sample_rate
        self.acoustic_onset = AcousticOnsetIndex(sample_rate)
        self.pcm_bytes_per_second = sample_rate * 2
        self.glossary = list(glossary or [])[:30]
        self.hotwords = list(hotwords or [])
        self.asr_context = list(asr_context or [])
        self.ingest_status_callback = ingest_status
        self.ffmpeg_path = ffmpeg_path
        self.pcm_queue_chunks = pcm_queue_chunks
        self.hold_minimum = hold_minimum
        self.hold_seconds_per_char = hold_seconds_per_char
        self.hold_maximum = hold_maximum
        self.overlong_chars = overlong_chars
        self.silence_duration_seconds = max(0.0, silence_duration_ms / 1000.0)
        self.translation_workers = max(1, int(translation_workers))
        self.translation_timeout_seconds = translation_timeout_seconds
        self.recovery_seconds = recovery_seconds
        self.playback_delay_seconds = playback_delay_seconds
        self.wall_clock = wall_clock
        self.monotonic = monotonic
        self._translation_budget_policy = TranslationBudgetPolicy(
            translation_timeout_seconds,
            playback_delay_seconds=playback_delay_seconds,
            wall_clock=wall_clock,
            monotonic=monotonic,
        )
        self.subprocess_factory = subprocess_factory
        self.context = RollingContext(context_pairs, context_seconds)
        self.stats = PipelineStats()
        # Provider-side translation (Soniox translation, Qwen3-LiveTranslate):
        # the Adapter writes each utterance's own translation into this bus and
        # the session-backed Translation Provider resolves cues from it. The
        # Provider translated the speech it had segmented, so for these Profiles
        # its segment is the caption unit -- ``segments_only`` turns off the
        # punctuation cuts that would otherwise leave a cue covering half a
        # segment, which is where the translation for a whole segment goes
        # unclaimed. What the cut still cannot be attributed to is rescued by
        # the Adapter's own ``translation_anchors``; see ``native_session``.
        self.native_translation_bus = native_translation_bus
        self.caption_chunker = CaptionChunker(
            realtime=True, segments_only=native_translation_bus is not None
        )
        self._caption_deadline_changed = asyncio.Event()
        self._generation = 0
        self._next_chunk_order = 1
        self._caption_exact_timing: dict[str, bool] = {}
        # The diagnostic ring is shared with the media lifecycle, so a provider
        # that stays down for minutes records one line per outage instead of
        # one per cue or per reconnect attempt.
        self._asr_failure_recorded = False
        self._translation_failure_recorded = False
        self._begin_generation()

        self.media_epoch: float | None = None
        self.private_playlist_url: str | None = None
        # P3-B (docs/subtitle-audio-leg-design-2026-09-09.md): when the
        # subtitle leg reads its own independent audio-only download instead
        # of the private HLS, PCM positions must be mapped into the video
        # leg's media space through the continuously measured anchor.
        self.media_anchor = media_anchor
        self.anchor_probe = anchor_probe
        self.video_backlog_callback = video_backlog
        self.source_pts_mapper = source_pts_mapper
        self.input_format: str | None = None
        self._pcm_queue: asyncio.Queue[tuple[bytes, float]] | None = None
        self._translation_queue: asyncio.Queue[Cue] = asyncio.Queue()
        self._translation_budgets: dict[int, TranslationBudget] = {}
        self._tasks: list[asyncio.Task[Any]] = []
        self._process: Any = None
        # Bounded retention of the decoder's OWN diagnostics. Reset with every
        # decoder lifecycle, because it describes one process.
        self._stderr_task: asyncio.Task[Any] | None = None
        self._stderr_tail = bytearray()
        self._stderr_bytes = 0
        self._stderr_tail_truncated = False
        self._stderr_snapshots = 0
        # Teardown has exactly one owner. `stop()` reuses this task instead of
        # starting a second teardown, `start()` refuses to run while it is
        # unfinished, and `_residue` names whatever was still alive when the
        # deadline expired -- kept so a retry can reap it rather than forgotten.
        self._stop_task: asyncio.Task[None] | None = None
        self._teardown_done = False
        self._residue: list[str] = []
        self._stream: ASRStream | None = None
        self._running = False
        self._stopping = False
        self._pcm_offset = 0.0
        self._last_sent_pcm_offset = 0.0
        # The renderer's playhead, on the caption timeline, as last stated with
        # its status poll. Measurement only: see ASR_QUEUE_LIMIT_SECONDS.
        self._viewer_wall_time: float | None = None
        self._viewer_wall_time_at = 0.0
        # The furthest position the ASR has confirmed. The sender compares the
        # audio it is about to hand over against it.
        self._asr_evidence_pcm: float | None = None
        # Stage-backlog correction the anchor adds to its window median, plus
        # the rolling minima it is derived from. Exposed in status so the
        # correction is visible instead of implicit.
        self._video_backlog_window: deque[float] = deque(maxlen=5)
        self._audio_backlog_window: deque[float] = deque(maxlen=5)
        self._anchor_backlog_video: float | None = None
        self._anchor_backlog_audio: float | None = None
        self._anchor_correction: float | None = None
        self._last_anchor_video_seconds: float | None = None
        if media_anchor is not None:
            # The window median differences two stage-output counters, so it is
            # short by the stages' backlogs. Supplied as a separate term rather
            # than folded into the samples, which keeps the rate gate working on
            # the smooth counters.
            media_anchor.set_correction(lambda: self._anchor_correction or 0.0)
        # Per-utterance VAD boundaries keyed by the provider's item id. A single
        # pair of "pending start/end" slots cannot work: the next utterance's
        # speech_started routinely arrives in the same millisecond as the
        # previous utterance's final, so the slots get clobbered before the
        # final that owns them is handled.
        self._vad_spans: "OrderedDict[str, dict[str, float]]" = OrderedDict()
        # Breadcrumbs mapping the ASR session's audio clock onto our pcm clock;
        # see _server_to_pipeline. Reset whenever a new ASR session starts.
        self._push_breadcrumbs: deque[tuple[float, float]] = deque(maxlen=1200)
        self._audio_push_times: deque[tuple[float, float]] = deque(maxlen=1200)
        self._evidence_times: deque[tuple[float, float]] = deque(maxlen=1200)
        self._stream_pushed_seconds = 0.0
        self._degrade_level = 0
        self._empty_since: float | None = None
        self._active_translation_provider_id: str | None = None
        self._translation_usage_by_provider: dict[str, dict[str, Any]] = {}
        # CaptionChunker may emit several chunks in one decision; stage them
        # briefly so the existing materialization path preserves their order.
        self._pending_finals: deque[_PendingFinal] = deque()
        self._audio_end_walls: dict[int, float] = {}
        self._source_ready_lags: deque[float] = deque(maxlen=60)
        self._translation_success_ready_lags: deque[float] = deque(maxlen=60)
        # (finished monotonic time, elapsed since the cue's audio was sent).
        # Unlike the legacy PCM-frontier estimate, this clock cannot jump when
        # the download bursts, stalls or the wall clock is corrected.
        self._translation_processing: deque[tuple[float, float]] = deque(maxlen=60)
        self._translation_latency_at: float | None = None
        self._terminal_outcome_lags: deque[float] = deque(maxlen=60)
        self._cue_latencies: dict[int, _CueLatency] = {}
        # Which Provider utterance a cue was built from. Kept beside the store
        # rather than on Cue: the id is a session-local join key with no meaning
        # to a renderer, and Cue is a serialized contract.
        self._cue_item_ids: dict[int, str] = {}
        self._stage_lags: dict[str, deque[float]] = {
            "asrAdapter": deque(maxlen=60),
            "chunkerPolicy": deque(maxlen=60),
            "translationQueue": deque(maxlen=60),
            "translationProvider": deque(maxlen=60),
            "storeUpdate": deque(maxlen=60),
            "totalReady": deque(maxlen=60),
        }
        self._latency_completed = 0
        self._latency_unknown = 0
        self._latency_unknown_reasons: dict[str, int] = {}
        self._latency_last_sample_at: float | None = None
        self._timing_source_counts = {"asr": 0, "vad": 0}

    @property
    def running(self) -> bool:
        return self._running

    def update_target_language(self, target_language: str) -> bool:
        """Apply a translation target to queued and future cues without restarting ASR."""
        canonical = canonicalize_target_tag(target_language)
        if canonical == self.meta.target_lang:
            return False
        self.meta = dataclasses.replace(self.meta, target_lang=canonical)
        # Translation history belongs to one target language. Starting with an
        # empty context prevents old-language answers steering the new target.
        self.context = RollingContext(self.context.context_pairs, self.context.context_seconds)
        return True

    def _begin_generation(self) -> None:
        """Start an isolated recognition generation and reset local ordering."""
        self._generation += 1
        self._next_chunk_order = 1
        self._caption_exact_timing.clear()
        self.caption_chunker.reset(self._generation)
        self._caption_deadline_changed.set()
        if self.native_translation_bus is not None:
            # A new Provider session renumbers its utterances from zero, so the
            # previous session's segments must not be resolvable against them.
            self.native_translation_bus.reset(self._generation)

    async def start(self, audio_url: str, media_epoch: float | None, input_format: str | None = None) -> None:
        """Start workers on the caption audio source.

        Default source is the authoritative private HLS playlist (segment
        zero). With ``input_format`` set (P3-B dedicated audio leg), the
        source is a raw stream endpoint (e.g. ``tcp://`` from the ingest
        pump) and ffmpeg is told the container format explicitly.

        ``media_epoch`` may be None in audio-leg mode: recognition, ASR
        connect, and anchor convergence all proceed while the private HLS
        first segment is still pending, and ``set_media_epoch`` releases the
        held finals when the epoch lands. This overlaps the three startup
        phases instead of serializing them (subtitle startup latency).
        """
        if self._running:
            return
        if self._residue:
            # A previous decoder is still alive. Starting another one on top of
            # it is how the old code silently overwrote an unreaped process, so
            # this refuses instead of pretending the cleanup happened.
            raise RuntimeError(
                "the previous subtitle decoder was not cleaned up: " + ", ".join(self._residue)
            )
        if self._stop_task is not None and not self._stop_task.done():
            raise RuntimeError("the previous subtitle teardown is still running")
        if not audio_url:
            raise ValueError("caption audio source URL is required")
        self.private_playlist_url = audio_url
        self.input_format = input_format
        self.media_epoch = float(media_epoch) if media_epoch is not None else None
        self._stopping = False
        self._teardown_done = False
        self._residue = []
        self._stderr_task = None
        self._stderr_tail = bytearray()
        self._stderr_bytes = 0
        self._stderr_tail_truncated = False
        self._stderr_snapshots = 0
        self._begin_generation()
        self._pcm_queue = asyncio.Queue(maxsize=self.pcm_queue_chunks)
        try:
            input_args = (
                ["-live_start_index", "0", "-i", audio_url]
                if input_format is None
                else [
                    "-f", input_format,
                    # The TCP endpoint is a known MPEG-TS stream. FFmpeg's
                    # normal pipe probe waits for several seconds of media;
                    # this bounded probe is enough for the fixed audio/video
                    # formats emitted by YtDlpLiveIngest.
                    "-analyzeduration", "500000",
                    "-probesize", "32768",
                    "-i", audio_url,
                ]
            )
            self._process = await self.subprocess_factory(
                self.ffmpeg_path,
                "-hide_banner", "-loglevel", "error", "-nostdin",
                *input_args,
                "-vn", "-af", "aresample=async=1:first_pts=0", "-ac", "1",
                "-ar", str(self.sample_rate), "-f", "s16le", "pipe:1",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._running = True
            # Start draining stderr before anything else: it is the pipe whose
            # blockage stops PCM, so it must never be the last reader to come up.
            self._stderr_task = asyncio.create_task(
                self._stderr_reader(self._process), name="subtitle-stderr-reader"
            )
            self._stderr_task.add_done_callback(self._on_stderr_done)
            self._tasks = [
                asyncio.create_task(self._pcm_reader(), name="subtitle-pcm-reader"),
                asyncio.create_task(self._pcm_sender(), name="subtitle-pcm-sender"),
                asyncio.create_task(self._asr_manager(), name="subtitle-asr-manager"),
                asyncio.create_task(self._caption_deadline_worker(), name="subtitle-caption-deadline"),
            ]
            if self.media_anchor is not None and self.anchor_probe is not None:
                self._tasks.append(
                    asyncio.create_task(self._anchor_sampler(), name="subtitle-anchor-sampler")
                )
            if self.translation_provider is not None:
                # Keep several workers so independent recognition generations
                # can progress concurrently. Caption Chunks inside one
                # generation are admitted one at a time below, preserving the
                # immediate predecessor in rolling translation context.
                self._tasks.extend(
                    asyncio.create_task(self._translation_worker(), name=f"subtitle-translation-{index}")
                    for index in range(self.translation_workers)
                )
        except Exception as exc:
            self.stats.last_error = self._error_text(exc)
            # A failed teardown must not replace the reason start() failed.
            with contextlib.suppress(Exception):
                await self.stop()
            raise
        return None

    def set_media_epoch(self, media_epoch: float) -> None:
        """Late-bind the timeline epoch and release any finals held for it."""
        if self.media_epoch is not None:
            return
        self.media_epoch = float(media_epoch)
        self._flush_pending_finals()

    def _leg_backlog(self, snapshot: Any, output_seconds: float) -> float | None:
        """How much received media one leg's stage is holding back.

        ``output_seconds`` is what the stage has emitted; the leg's own PTS
        extent is what it has received. The difference is the backlog, which a
        single reading overstates by up to one segment because a segment cannot
        be counted until it is complete.
        """
        extents = [
            float(leg["sourcePtsLast"]) - float(leg["sourcePtsFirst"])
            for leg in ((snapshot or {}).get("legThroughput") or [])
            if leg.get("sourcePtsFirst") is not None and leg.get("sourcePtsLast") is not None
        ]
        if not extents:
            return None
        return min(extents) - output_seconds

    def _update_anchor_correction(self) -> None:
        """Refresh `C += video backlog - audio backlog` once per sample tick.

        Computed here rather than inside the correction callable because
        `MediaAnchor.offset` is read several times per status request, and the
        rolling minimum must advance once per tick, not once per reader.
        """
        if self.media_anchor is None:
            return
        try:
            audio_snapshot = (
                self.ingest_status_callback() if self.ingest_status_callback is not None else None
            )
        except Exception as exc:  # a bad probe must never kill the pipeline
            self.stats.last_error = self._error_text(exc)
            audio_snapshot = None
        try:
            video_backlog = self.video_backlog_callback() if self.video_backlog_callback is not None else None
        except Exception as exc:  # noqa: BLE001
            self.stats.last_error = self._error_text(exc)
            video_backlog = None
        audio_backlog = self._leg_backlog(audio_snapshot, self._pcm_offset)
        for window, raw in ((self._video_backlog_window, video_backlog), (self._audio_backlog_window, audio_backlog)):
            # A restarted leg re-bases its PTS, which would make the difference
            # meaningless; clamp instead of feeding a garbage correction.
            if raw is not None and -1.0 <= raw <= 60.0:
                window.append(raw)
        self._anchor_backlog_video = min(self._video_backlog_window) if self._video_backlog_window else 0.0
        self._anchor_backlog_audio = min(self._audio_backlog_window) if self._audio_backlog_window else 0.0
        self._anchor_correction = self._anchor_backlog_video - self._anchor_backlog_audio

    async def _anchor_sampler(self) -> None:
        """Feed the media anchor with (video-leg, audio-leg) positions.

        2.5s cadence: the video leg's private-media counter advances in 1s
        segment quanta, so a 1s cadence sees dV in {0,1,2,3} and the rate
        gate rejects nearly everything. Averaging over 2.5s smooths the
        quantization into the pass band while still rejecting stalls (0x)
        and backlog bursts (25x).

        The sampled pair stays the two stage-output counters. Substituting the
        legs' raw PTS extents here instead was measured to collapse accepted
        samples from 30 to 6 of 30, because a PTS extent moves only when a
        whole segment lands and the gate then rejects the intervals where just
        one side moved. The stage backlog those counters omit is supplied
        separately as `correction`.
        """
        while self._running:
            await asyncio.sleep(2.5)
            if self.media_anchor is None or self.anchor_probe is None:
                return
            try:
                video_seconds = self.anchor_probe()
                self.media_anchor.add_sample(video_seconds, self._pcm_offset)
                # A stalled video leg has no new video timeline position. Its
                # raw stage backlog grows while audio keeps arriving, but that
                # growth is not a new audio/video clock relationship. Keep the
                # last trusted correction until the video advances again;
                # otherwise subtitles are remapped while the picture is frozen.
                video_advanced = (
                    video_seconds is not None
                    and (
                        self._last_anchor_video_seconds is None
                        or float(video_seconds) > self._last_anchor_video_seconds + 1e-6
                    )
                )
                if video_seconds is not None:
                    self._last_anchor_video_seconds = float(video_seconds)
                if video_advanced:
                    self._update_anchor_correction()
            except Exception as exc:  # a bad probe must never kill the pipeline
                self.stats.last_error = self._error_text(exc)
            if self.media_anchor.ready and self._pending_finals:
                # Finals held while the anchor converged (or during a skip
                # reseed) are released as soon as the mapping is trustworthy.
                self._flush_pending_finals()

    async def stop(self) -> None:
        """Idempotently stop all workers without propagating provider/process errors.

        Every caller awaits the SAME teardown task, so two overlapping Stops
        clean up once, and a caller that is itself cancelled does not abandon the
        cleanup half-owned: ``shield`` keeps it running.
        """
        if self._teardown_done:
            return
        await asyncio.shield(self._ensure_stop_task())

    def _ensure_stop_task(self) -> "asyncio.Task[None]":
        """The one teardown owner for the current decoder lifecycle."""
        task = self._stop_task
        if task is None or task.done():
            task = asyncio.create_task(self._stop_impl(), name="subtitle-stop")
            self._stop_task = task
        return task

    async def _stop_impl(self) -> None:
        """One teardown, on ONE absolute deadline, reporting what it could not end.

        The previous shape gave flush/aclose five seconds, then handed the ASR
        drain a fresh five seconds, and after the kill awaited the process with
        no bound at all. A decoder that ignored terminate could spend ten seconds
        and still be alive, while the caller had already forgotten the reference.
        Here every wait -- provider close, ASR drain, worker cancellation, both
        pipes, process reap -- spends what is left of a single deadline, and
        whatever survives it is REPORTED rather than cleared away.
        """
        self._stopping = True
        self._running = False
        deadline = time.monotonic() + _STOP_ASR_CLOSE_SECONDS
        terminate_deadline = time.monotonic() + _STOP_TERMINATE_GRACE_SECONDS
        # task -> the step that did not finish, which becomes the residue report.
        pending: dict[asyncio.Task[Any], str] = {}

        def remaining() -> float:
            return max(0.0, deadline - time.monotonic())

        # End production first, and let the reap run CONCURRENTLY with the
        # provider close below: the grace is one second of wall clock, not one
        # second appended after everything else has already finished.
        process, self._process = self._process, None
        reap: asyncio.Task[Any] | None = None
        if process is not None and getattr(process, "returncode", None) is None:
            with contextlib.suppress(Exception):
                process.terminate()
            reap = asyncio.ensure_future(process.wait())

        stream = self._stream
        if stream is not None:
            for step in ("flush", "aclose"):
                call = getattr(stream, step, None)
                if call is None:
                    continue
                await self._bounded_call(call(), remaining(), pending, f"stream.{step}")
            # Several realtime protocols flush their last final tokens only after
            # the close/finalize control (Soniox empty frame, AssemblyAI
            # Terminate, Volcengine negative packet). Give the existing ASR
            # consumer a bounded chance to drain those events before cancelling
            # workers; otherwise a correct Adapter still loses the last cue.
            await self._await_then_cancel(
                {task for task in self._tasks if task.get_name() == "subtitle-asr-manager"},
                remaining(),
                pending,
                "asr final drain",
            )
            if self._stream is stream:
                self._stream = None
        self._flush_caption_session()
        await self._cancel_then_await(
            [task for task in self._tasks if task not in pending],
            remaining(),
            pending,
            "worker cancellation",
        )
        pcm_task = next(
            (task for task in self._tasks if task.get_name() == "subtitle-pcm-reader"), None
        )
        self._discard_translation_queue()
        self._audio_end_walls.clear()
        self._pending_finals.clear()

        # Reap BEFORE waiting on the pipes. The stderr reader ends at EOF, and EOF
        # only happens once its writer is gone, so waiting for the reader first
        # would spend the whole budget on a process that has not been killed yet
        # and then report the reader as residue it could never have avoided.
        if reap is not None:
            if not reap.done():
                grace = min(max(0.0, terminate_deadline - time.monotonic()), remaining())
                if grace > 0:
                    await asyncio.wait({reap}, timeout=grace)
            if reap.done():
                _consume_task_result(reap)
            else:
                with contextlib.suppress(Exception):
                    process.kill()
                await self._await_then_cancel({reap}, remaining(), pending, "decoder process")
            if not reap.done():
                # Keep the handle: a retry of stop() must still be able to kill
                # and reap it, and start() must refuse to run while it is alive.
                self._process = process

        # Only now may anything else read stdout. Two readers on one StreamReader
        # would interleave, and the tail would land in whichever of them read
        # first. This sends nothing to the ASR, which is closing.
        if pcm_task is None or pcm_task.done():
            stdout = getattr(process, "stdout", None) if process is not None else None
            if stdout is not None and getattr(process, "returncode", None) is not None:
                await self._bounded_call(
                    self._discard_stdout_tail(stdout), remaining(), pending, "stdout drain"
                )

        # The stderr reader outlives its writer on purpose: it is the only thing
        # keeping a dying decoder from blocking on a full stderr pipe, so it is
        # not cancelled together with the workers above.
        stderr_task = self._stderr_task
        if stderr_task is not None and not stderr_task.done():
            await self._await_then_cancel({stderr_task}, remaining(), pending, "stderr drain")
        if self._stderr_task is not None and self._stderr_task.done():
            _consume_task_result(self._stderr_task)
            self._stderr_task = None

        if self._stderr_bytes:
            self._emit_decoder_diagnostic(
                "error" if pending else "info",
                "字幕解码器诊断末尾（累计 "
                f"{self._stderr_bytes} 字节"
                + ("，尾部已按字节上限截断" if self._stderr_tail_truncated else "")
                + "）",
            )

        for task in list(pending):
            if task.done():
                _consume_task_result(task)
                pending.pop(task)
        self._tasks = [task for task in self._tasks if not task.done()]
        self._residue = sorted(set(pending.values()))
        self._stopping = False
        if self._residue:
            # Never report a clean teardown while a task or a process is still
            # alive. The handles stay reachable, and the caller must not start a
            # new decoder on top of them -- forgetting them here is exactly how
            # the old shape lost an unreaped process behind a cleared attribute.
            raise RuntimeError(
                "subtitle decoder teardown did not finish: " + ", ".join(self._residue)
            )
        self._teardown_done = True

    async def _bounded_call(
        self,
        awaitable: Any,
        budget: float,
        pending: dict[asyncio.Task[Any], str],
        label: str,
    ) -> None:
        """Run one close handshake under the shared deadline.

        ``asyncio.wait_for`` cancels the awaitable AND awaits its unwinding, so a
        coroutine that swallows cancellation extends the wait past the timeout it
        was handed -- the bound would be a promise rather than a bound. Waiting
        on the deadline and cancelling afterwards makes it a wall-clock bound,
        and anything that survives is named in the residue instead of awaited.
        """
        await self._await_then_cancel({asyncio.ensure_future(awaitable)}, budget, pending, label)

    async def _await_then_cancel(
        self,
        tasks: Any,
        budget: float,
        pending: dict[asyncio.Task[Any], str],
        label: str,
    ) -> None:
        """Give tasks ``budget`` to finish, then cancel and keep what is left."""
        given = set(tasks)
        for task in given:
            if task.done():
                _consume_task_result(task)
        live = {task for task in given if not task.done()}
        if not live:
            return
        still = live
        if budget > 0:
            _, still = await asyncio.wait(live, timeout=budget)
        for task in still:
            task.cancel()
        for task in still:
            task.add_done_callback(_consume_task_result)
            pending[task] = label

    async def _cancel_then_await(
        self,
        tasks: Any,
        budget: float,
        pending: dict[asyncio.Task[Any], str],
        label: str,
    ) -> None:
        """Cancel tasks, then give them ``budget`` to actually stop."""
        given = list(tasks)
        if not given:
            return
        for task in given:
            task.cancel()
        still = {task for task in given if not task.done()}
        if still and budget > 0:
            _, still = await asyncio.wait(still, timeout=budget)
        for task in given:
            if task.done():
                _consume_task_result(task)
        for task in still:
            task.add_done_callback(_consume_task_result)
            pending[task] = label

    async def _enqueue_pcm_chunk(self, chunk: bytes, chunk_start: float) -> None:
        """Queue decoded PCM with bounded backpressure for the production reader."""
        self.acoustic_onset.feed(chunk, chunk_start)
        self._pcm_offset = max(
            self._pcm_offset,
            chunk_start + len(chunk) / self.pcm_bytes_per_second,
        )
        queue = self._pcm_queue
        if queue is not None:
            await queue.put((chunk, chunk_start))

    async def _pcm_reader(self) -> None:
        assert self._process is not None
        buffer = bytearray()
        try:
            while self._running:
                data = await self._process.stdout.read(PCM_CHUNK_BYTES - len(buffer))
                if not data:
                    break
                buffer.extend(data)
                if len(buffer) < PCM_CHUNK_BYTES:
                    continue
                chunk = bytes(buffer)
                buffer.clear()
                await self._enqueue_pcm_chunk(chunk, self._pcm_offset)
            if buffer:
                self._pcm_offset += len(buffer) / self.pcm_bytes_per_second
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_error(exc)

    async def _stderr_reader(self, process: Any) -> None:
        """Drain the decoder's stderr so it can never block the PCM before it.

        Deliberately not conditioned on ``_running``: Stop clears that flag first
        and then waits for this decoder to exit, and a reader that quit at that
        moment would hand a dying process a full pipe to block on.

        ``process`` is passed in rather than read from ``self._process``, which
        teardown nulls while this reader is still required.
        """
        stderr = getattr(process, "stderr", None)
        if stderr is None:
            return
        since_yield = 0
        first_block = True
        while True:
            block = await stderr.read(_STDERR_READ_BYTES)
            if not block:
                return
            self._stderr_bytes += len(block)
            self._stderr_tail.extend(block)
            extra = len(self._stderr_tail) - _STDERR_TAIL_BYTES
            if extra > 0:
                del self._stderr_tail[:extra]
                self._stderr_tail_truncated = True
            if first_block:
                first_block = False
                # One record when the decoder starts complaining, one at the end.
                # Anything more would make an error storm the only thing the
                # diagnostics bar shows, which is the failure mode this reader
                # exists to survive rather than to reproduce in the log.
                self._emit_decoder_diagnostic(
                    "warn", f"字幕解码器开始输出诊断（累计 {self._stderr_bytes} 字节）"
                )
            since_yield += len(block)
            if since_yield >= _STDERR_TAIL_BYTES:
                # A burst that is already buffered must not keep the event loop
                # from running the tasks that deliver PCM and ASR events.
                since_yield = 0
                await asyncio.sleep(0)

    def _on_stderr_done(self, task: asyncio.Task[Any]) -> None:
        """Fault owner for the stderr reader: never leave an unread writer alive.

        The reader cannot recover on its own. If it fails and nothing acts, the
        decoder keeps writing into a pipe nobody reads -- the original stall,
        now with a record saying it happened. So the failure ends this decoder
        and hands the teardown to the same single owner ``stop()`` uses, instead
        of starting an ownerless task out of a callback.
        """
        if task.cancelled():
            return
        error = task.exception()
        if error is None or self._stopping:
            return
        self._record_error(error)
        self._emit_decoder_diagnostic(
            "error", f"字幕解码器诊断读取失败：{self._error_text(error)}"
        )
        process = self._process
        if process is not None and getattr(process, "returncode", None) is None:
            try:
                process.kill()
            except Exception as exc:
                # A kill that fails belongs to the same teardown result: the
                # decoder stays alive and that must be visible, not swallowed.
                self._record_error(exc)
        self._ensure_stop_task()

    def _emit_decoder_diagnostic(self, level: str, summary: str) -> bool:
        """Publish one bounded snapshot of what the decoder has said so far.

        Bounded twice on purpose: a fixed number of snapshots per decoder
        lifecycle and a fixed number of lines per snapshot. A decoder writing
        megabytes of errors is at once the case where an unbounded diagnostic
        would evict everything else the user needs from the logbook -- and the
        case that produced the stall this reader exists to prevent.

        The level states what was observed rather than what it implies: text on
        stderr is not by itself a fatal error, so the first capture is a warning
        and only a real read failure is an error.
        """
        if self._stderr_snapshots >= _STDERR_MAX_SNAPSHOTS:
            return False
        self._stderr_snapshots += 1
        log_record(level, "media", summary)
        for line in self._decoder_tail_lines():
            log_record(level, "media", line)
        return True

    def _decoder_tail_lines(self) -> list[str]:
        """The last complete, non-empty lines of the retained tail.

        A fragment at the front is dropped rather than shown. The trim happens on
        a byte boundary, so the leading fragment can begin mid-token inside a
        signed URL -- and such a fragment still survives the logbook's
        scheme-based redaction while carrying the rest of the query string. Only
        the first line can be a fragment; every later line starts at a newline by
        construction.
        """
        text = bytes(self._stderr_tail).decode("utf-8", errors="replace")
        if self._stderr_tail_truncated:
            _, separator, text = text.partition("\n")
            if not separator:
                # The whole retained window is one unfinished line. Quoting a
                # slice of it is neither a diagnostic nor something the
                # scheme-based redaction can be trusted with, so state the size
                # instead.
                text = ""
        lines = [line.strip() for line in text.splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            return [
                f"（无完整诊断行可引用，累计 {self._stderr_bytes} 字节，超长诊断已省略）"
            ]
        return lines[-_STDERR_TAIL_LINES:]

    async def _discard_stdout_tail(self, stdout: Any) -> None:
        """Discard what is left in stdout once the PCM reader is gone.

        Teardown-only, and it hands nothing to the ASR: the point is that a
        decoder which is still writing cannot be blocked by a full stdout pipe
        while it is being reaped. The PCM reader owns this pipe for the whole
        session, which is why this may only run after that reader has stopped.
        """
        while True:
            block = await stdout.read(_STDOUT_DRAIN_BYTES)
            if not block:
                return

    def _server_to_pipeline(self, server_seconds: float | None) -> float | None:
        """Map an ASR-session audio offset onto our ``_pcm_offset`` timeline.

        The provider counts the audio *it received on this session*; we count
        the audio we *produced* since the stream began. The two diverge whenever
        a PCM chunk is dropped under backpressure or the ASR session reconnects,
        so the difference is not a constant and cannot be assumed to be zero.
        Breadcrumbs recorded at push time make the conversion exact; with no
        drops they collapse to a single constant offset.
        """
        if server_seconds is None:
            return None
        if not self._push_breadcrumbs:
            return None
        pushed, offset = self._push_breadcrumbs[0]
        if server_seconds <= pushed:
            return offset + (server_seconds - pushed)
        for candidate_pushed, candidate_offset in self._push_breadcrumbs:
            if candidate_pushed > server_seconds:
                break
            pushed, offset = candidate_pushed, candidate_offset
        return offset + (server_seconds - pushed)

    def set_viewer_wall_time(self, wall_time: float | str | None) -> None:
        """Record where the renderer's playhead is, on the caption timeline.

        The renderer states this with the status poll it already sends once a
        second, and the value is the same playing wall clock the caption
        scheduler compares cue windows against. Deriving it from HLS requests
        instead is not possible: measured 2026-09-18, hls.js is 6-15s ahead of
        the playhead when it fetches (median 11.9s), so a request only bounds the
        playhead to a nine-second window.
        """
        if wall_time is None:
            return
        try:
            value = float(wall_time)
        except (TypeError, ValueError):
            return
        if not math.isfinite(value) or value <= 0.0:
            return
        if abs(value - self.wall_clock()) > VIEWER_POSITION_SANITY_SECONDS:
            return
        self._viewer_wall_time = value
        self._viewer_wall_time_at = self.monotonic()

    def _viewer_pcm_position(self) -> float | None:
        """The viewer's playhead on the audio leg's PCM timeline, or None.

        None (never stated, stale, or no mapper ready yet) means "do not
        throttle" -- exactly the behaviour that had no flow control at all, so a
        missing or untrustworthy reference can only ever be conservative.
        """
        if self._viewer_wall_time is None:
            return None
        if self.monotonic() - self._viewer_wall_time_at > VIEWER_POSITION_TTL_SECONDS:
            return None
        epoch = self.media_epoch
        anchor = self.media_anchor
        if epoch is None or anchor is None or anchor.offset is None:
            return None
        return (self._viewer_wall_time - epoch) - anchor.offset

    def viewer_lead_seconds(self) -> float | None:
        """How far the audio handed to the ASR runs ahead of the viewer."""
        position = self._viewer_pcm_position()
        if position is None:
            return None
        return self._last_sent_pcm_offset - position

    async def _pcm_sender(self) -> None:
        assert self._pcm_queue is not None
        while self._running:
            chunk, offset = await self._pcm_queue.get()
            chunk_seconds = len(chunk) / self.pcm_bytes_per_second
            stream = self._stream
            if stream is None:
                continue
            await self._wait_for_viewer_lead(stream)
            if not self._running or self._stream is not stream:
                continue
            self._last_sent_pcm_offset = offset + chunk_seconds
            self._push_breadcrumbs.append((self._stream_pushed_seconds, offset))
            self._stream_pushed_seconds += len(chunk) / self.pcm_bytes_per_second
            try:
                await asyncio.wait_for(stream.push_pcm(chunk, offset), timeout=5.0)
                self._audio_push_times.append((self._last_sent_pcm_offset, self.monotonic()))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_error(exc)
                if self._stream is stream:
                    self._stream = None
                with contextlib.suppress(Exception):
                    await stream.aclose()
                continue
            # Cut decisions belong to the shared CaptionChunker alone. The
            # former second timer commit here (legacy max-utterance cap) was
            # disabled twice over -- max_utterance_seconds defaulted to 0.0 and
            # _caption_evidence_seen killed it after the first observation -- and
            # has been removed, so no path can cut a caption behind the
            # chunker's back.
            await self._advance_caption_frontier(self._last_sent_pcm_offset, stream)

    async def _wait_for_viewer_lead(self, stream: Any) -> None:
        """Hold the independent audio leg while video playback is stalled."""
        while self._running and self._stream is stream:
            lead = self.viewer_lead_seconds()
            if lead is None:
                return
            try:
                target = max(0.0, float(self.playback_delay_seconds()))
            except (TypeError, ValueError):
                return
            if lead <= target + ASR_VIEWER_LEAD_ALLOWANCE_SECONDS:
                return
            await asyncio.sleep(ASR_VIEWER_LEAD_POLL_SECONDS)

    async def _asr_manager(self) -> None:
        backoff = 0.5
        while self._running:
            if self._stream is not None:
                await asyncio.sleep(0.05)
                continue
            try:
                if self.native_translation_bus is not None:
                    # The Provider needs the Target Language when its session
                    # opens, because it emits translation rather than being
                    # asked for it per cue.
                    self.asr_provider.set_translation_target(self.meta.target_lang)
                stream = await self.asr_provider.stream(
                    policy=self.source_policy,
                    sample_rate=self.sample_rate,
                    hotwords=self.hotwords,
                    context=self.asr_context,
                )
                if not self._running:
                    await stream.aclose()
                    return
                # A fresh session restarts the provider's audio clock at zero,
                # so the old breadcrumbs and any half-open VAD spans no longer
                # refer to anything.
                self._flush_caption_session()
                self._push_breadcrumbs.clear()
                self._stream_pushed_seconds = 0.0
                self._vad_spans.clear()
                self._begin_generation()
                self._stream = stream
                backoff = 0.5
                self._asr_failure_recorded = False
                await self._consume_asr_events(stream)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_error(exc)
                if not self._asr_failure_recorded:
                    # Subtitles simply stop appearing while this retries, so the
                    # first failure of an outage is the only thing that explains
                    # the silence to the user.
                    log_record("warn", "asr", f"ASR stream failed, reconnecting: {self.stats.last_error}")
                    self._asr_failure_recorded = True
            finally:
                stream = self._stream
                self._stream = None
                if stream is not None:
                    with contextlib.suppress(Exception):
                        await stream.aclose()
            if self._running:
                self.stats.asr_reconnects += 1
                # ASR reconnect: audio in flight is dropped, never back-filled;
                # pcm_offset keeps advancing (timeline stays truthful).
                await asyncio.sleep(backoff)
                backoff = min(8.0, backoff * 2)

    async def _consume_asr_events(self, stream: ASRStream) -> None:
        async for event in stream:
            if stream is not self._stream or (not self._running and not self._stopping):
                return
            if event.type == "error":
                raise RuntimeError(event.message or "ASR stream error")
            await self._handle_asr_event(event)

    def _span_for(self, item_id: str | None) -> dict[str, float]:
        """Get (creating if needed) the VAD span bucket for an utterance."""
        key = item_id or ""
        span = self._vad_spans.get(key)
        if span is None:
            span = {}
            self._vad_spans[key] = span
            while len(self._vad_spans) > 64:
                self._vad_spans.popitem(last=False)
        return span

    async def _handle_asr_event(self, event: ASREvent) -> None:
        if self.native_translation_bus is not None and event.item_id is not None:
            # Provider-side translation arrives on the same frames as the
            # recognition it belongs to, so it is recorded before the caption
            # evidence below is chunked: a cue materialized from this event can
            # already be resolved by the time its translation worker picks it up.
            if event.type == "final":
                self.native_translation_bus.close_item(
                    event.item_id, source_text=event.text, translation=event.translation,
                    anchors=event.translation_anchors,
                    anchors_trusted=event.translation_anchors_trusted,
                )
            elif event.translation or event.translation_stash or event.translation_anchors:
                self.native_translation_bus.record(
                    item_id=event.item_id,
                    source_text=event.text,
                    translation=event.translation,
                    anchors=event.translation_anchors,
                    anchors_trusted=event.translation_anchors_trusted,
                )
            if event.translation_anchors_trusted and event.translation_anchors:
                self.caption_chunker.set_translation_boundaries(event.item_id, event.translation_anchors)
            if event.type in {"translation", "final"} and (event.translation or event.translation_anchors):
                await self._repair_native_translation_cues(event.item_id)
        if event.type == "speech_started":
            # The provider reports the true onset offset; no lag compensation.
            # Estimating this from _last_sent_pcm_offset was measured wrong by
            # ~0.03-0.17s at the start and, worse, systematically early at the
            # end (see speech_stopped below).
            start = self._server_to_pipeline(event.begin_pcm)
            if start is not None:
                start = max(0.0, start)
                self._span_for(event.item_id)["start"] = start
                if event.item_id:
                    self.caption_chunker.open_item(event.item_id, start)
        elif event.type == "speech_stopped":
            # audio_end_ms already points at the end of *speech*; it does not
            # include the trailing silence window. Subtracting
            # silence_duration_seconds here (as the old estimator did) double
            # counts it: measured error tracked the setting exactly --
            # 200ms->0.126s, 400ms->0.323s, 600ms->0.502s too early.
            end = self._server_to_pipeline(event.end_pcm)
            if end is not None:
                span = self._span_for(event.item_id)
                span["end"] = max(span.get("start", 0.0), end)
        observation_handled = False
        if event.caption_observation is not None:
            observation_handled = await self._handle_caption_observation(event.caption_observation, event)
        if event.type == "interim":
            if not observation_handled:
                # Every shipped Adapter attaches a CaptionObservation to its
                # interim/final events (verified across all 12 asr_*.py
                # adapters), so this means the evidence could not be mapped onto
                # the pipeline audio clock -- malformed token timestamps, or a
                # third-party Adapter that predates the normalized contract.
                # Count it and drop it rather than falling through to a second
                # segmentation mechanism whose cues never entered translation
                # context.
                self.stats.unmapped_observations += 1
        elif event.type == "final":
            if not observation_handled:
                self.stats.unmapped_observations += 1
        elif event.type == "speaker_revision":
            # Backend-owned diarization revision (e.g. AssemblyAI
            # SpeakerRevision): the adapter reports the corrected label for an
            # already-final utterance. Ticket 02 deliberately stops here -- no
            # speaker UI and no retroactive cue rewrite -- the event is counted
            # and logged so a later slice can own write-back.
            self.stats.speaker_revisions += 1
        elif event.type == "translation":
            # A Provider-side translation update for an utterance that is still
            # open. The bus was already updated above; there is no caption
            # evidence in this event, so it must not be counted as unmapped.
            self.stats.native_translation_updates += 1

    def _map_caption_observation(self, observation: Any) -> Any | None:
        """Map Provider-session evidence onto the pipeline PCM timeline.

        Invalid lexical timestamps are removed rather than being repaired or
        interpolated. Text/final evidence can then use the authoritative VAD
        start and current audio frontier without claiming word-level timing.
        """
        # An Adapter that leaves item_id None must not take the session down:
        # CaptionChunker builds its lane key as "item:" + item_id, and a raise
        # here propagates through _consume_asr_events into _asr_manager's
        # except Exception, i.e. a full ASR reconnect -- forever, if the
        # Provider keeps doing it. Every shipped Adapter already normalizes with
        # str(item_id or "0"); enforce the same contract at this boundary rather
        # than trusting it.
        item_id = str(observation.item_id or "0")
        observation = dataclasses.replace(observation, item_id=item_id)
        span = self._vad_spans.get(item_id, {})
        mapped_begin = self._server_to_pipeline(observation.begin_pcm)
        mapped_end = self._server_to_pipeline(observation.end_pcm)
        if mapped_begin is None:
            mapped_begin = span.get("start")
        if mapped_end is None:
            mapped_end = span.get("end")
        if mapped_end is None and observation.kind in {
            "stable_prefix_snapshot", "text_snapshot", "utterance_final", "endpoint",
        }:
            mapped_end = self._last_sent_pcm_offset
        mapped_tokens = []
        valid_token_timing = bool(observation.tokens)
        for token in observation.tokens:
            begin = self._server_to_pipeline(token.begin_pcm)
            end = self._server_to_pipeline(token.end_pcm)
            if begin is None or end is None or end < begin:
                valid_token_timing = False
                begin = None
                end = None
            mapped_tokens.append(dataclasses.replace(token, begin_pcm=begin, end_pcm=end))
        # A malformed token-bearing final is safer as stable text evidence. It
        # retains VAD/frontier timing and cannot accidentally advertise `asr`.
        if observation.tokens and not valid_token_timing and observation.kind in {
            "stable_token_delta", "token_snapshot",
        }:
            return None
        return dataclasses.replace(
            observation,
            generation=self._generation,
            tokens=tuple(mapped_tokens),
            begin_pcm=mapped_begin,
            end_pcm=mapped_end,
        )

    async def _handle_caption_observation(self, observation: Any, event: ASREvent) -> bool:
        mapped = self._map_caption_observation(observation)
        if mapped is None:
            return False
        token_timing = bool(mapped.tokens) and all(
            token.begin_pcm is not None and token.end_pcm is not None
            for token in mapped.tokens
        )
        if mapped.kind in {"stable_token_delta", "token_snapshot"} and mapped.tokens:
            self._caption_exact_timing[mapped.item_id] = token_timing
        elif mapped.kind in {"stable_prefix_snapshot", "text_snapshot"}:
            self._caption_exact_timing[mapped.item_id] = False
        elif mapped.kind == "utterance_final" and mapped.tokens:
            self._caption_exact_timing[mapped.item_id] = token_timing
        exact_timing = self._caption_exact_timing.get(mapped.item_id, token_timing)
        mapped = dataclasses.replace(
            mapped,
            language=mapped.language or event.language,
            speaker=mapped.speaker or event.speaker,
        )
        evidence_positions = [float(token.end_pcm) for token in mapped.tokens if token.end_pcm is not None]
        if mapped.end_pcm is not None and mapped.kind in {"utterance_final", "stable_prefix_snapshot"}:
            evidence_positions.append(float(mapped.end_pcm))
        if evidence_positions:
            confirmed = max(evidence_positions)
            self._evidence_times.append((confirmed, self.monotonic()))
            # How far the provider has actually listened. The sender keeps its
            # queue in front of this instead of in front of the playhead.
            if self._asr_evidence_pcm is None or confirmed > self._asr_evidence_pcm:
                self._asr_evidence_pcm = confirmed
        decision = self.caption_chunker.observe(mapped, now=self.monotonic())
        self._materialize_caption_decision(decision, event, exact_timing=exact_timing)
        self._caption_deadline_changed.set()
        if mapped.kind in {"endpoint", "utterance_final"}:
            self._caption_exact_timing.pop(mapped.item_id, None)
        # Normalized finals/endpoints own residual reconciliation. A Provider
        # may send both event shapes; the chunker's item-close idempotency makes
        # those duplicates harmless.
        return True

    async def _caption_deadline_worker(self) -> None:
        """One session timer; finalized tails also expire during audio stalls."""
        while self._running:
            self._caption_deadline_changed.clear()
            deadline = self.caption_chunker.next_deadline
            if deadline is None:
                await self._caption_deadline_changed.wait()
                continue
            remaining = deadline - self.monotonic()
            if remaining > 0:
                try:
                    await asyncio.wait_for(self._caption_deadline_changed.wait(), remaining)
                    continue
                except asyncio.TimeoutError:
                    pass
            self._materialize_caption_decision(self.caption_chunker.expire(self.monotonic()))

    async def _advance_caption_frontier(self, frontier_pcm: float, stream: ASRStream) -> None:
        """Report the audio frontier so the chunker can count pending evidence.

        This deliberately never asks the Provider for a commit. Advancing audio
        cannot make a lexical boundary linguistically safe, and the branch that
        used to do so was unreachable: it was gated on
        ``decision.request_hard_commit``, which no code path ever set true.
        """
        del stream
        self._materialize_caption_decision(self.caption_chunker.advance_audio(frontier_pcm))

    def _flush_caption_session(self) -> list[Cue]:
        decision = self.caption_chunker.flush_utterance(None)
        self._caption_deadline_changed.set()
        exact_timing = bool(self._caption_exact_timing) and all(self._caption_exact_timing.values())
        cues = self._materialize_caption_decision(decision, exact_timing=exact_timing)
        self._caption_exact_timing.clear()
        return cues

    def _materialize_caption_decision(
        self,
        decision: ChunkerDecision,
        event: ASREvent | None = None,
        *,
        exact_timing: bool = False,
    ) -> list[Cue]:
        for chunk in decision.chunks:
            self._queue_caption_chunk(chunk, event, exact_timing=exact_timing)
        return self._flush_pending_finals()

    def _queue_caption_chunk(
        self,
        chunk: CaptionChunk,
        event: ASREvent | None,
        *,
        exact_timing: bool,
    ) -> None:
        cleaned = clean_subtitle_text(chunk.text)
        if cleaned is None:
            self.stats.final_discarded += 1
            return
        if self._ingest_is_unhealthy():
            self.stats.suppressed_by_ingest_error += 1
            return
        # A caption can contain evidence from several ASR items or be emitted
        # by a later speaker's event. Its own units own timing provenance.
        #
        # There is no third case: CaptionChunk.begin_pcm is a non-optional float
        # (_chunk_times substitutes state.begin_pcm or 0.0), so the old
        # `"vad" if chunk.begin_pcm is not None else "approx"` was a tautology
        # and "approx" was unreachable. The UI displayed that permanently-zero
        # counter as if it meant something.
        timing_source: TimingSource = "asr" if chunk.exact_timing else "vad"
        begin_pcm = self.acoustic_onset.refine(chunk.begin_pcm, chunk.end_pcm)
        adjustment = abs(begin_pcm - chunk.begin_pcm)
        if adjustment > 0.001:
            timing_source = "approx"  # Acoustic refinement, not vendor-exact timing.
            self.stats.acoustic_onset_refinements += 1
            self.stats.acoustic_onset_max_adjustment_ms = max(
                self.stats.acoustic_onset_max_adjustment_ms, adjustment * 1000)
        emitted_at = self.monotonic()
        pending = _PendingFinal(
            text=cleaned,
            begin_pcm=begin_pcm,
            end_pcm=chunk.end_pcm,
            timing_source=timing_source,
            lang=chunk.language or (event.language if event is not None else None),
            audio_end_wall=self.wall_clock() - max(0.0, self._pcm_offset - chunk.end_pcm),
            evidence_available_mono=self._frontier_time(self._evidence_times, chunk.end_pcm),
            chunk_emitted_mono=emitted_at,
            speaker=chunk.speaker,
            generation=self._generation,
            chunk_order=self._next_chunk_order,
            starts_mid_sentence=chunk.starts_mid_sentence,
            ends_mid_sentence=chunk.ends_mid_sentence,
            cut_reason=chunk.cut_reason,
            # The chunk knows which Provider utterance its evidence came from;
            # the triggering event is only a fallback for Adapters that predate
            # the field and for deadline cuts that have no event at all.
            item_id=chunk.item_id or (event.item_id if event is not None else None),
        )
        self._next_chunk_order += 1
        self._pending_finals.append(pending)
        # A held final is only useful while the timeline it belongs to can still
        # be reached. In audio-leg mode the flush waits for the media anchor to
        # converge; if that never happens the deque used to grow without bound
        # while nothing was published at all. Bound it and count what is lost so
        # the condition is visible instead of silent.
        while len(self._pending_finals) > _PENDING_FINALS_MAX:
            self._pending_finals.popleft()
            self.stats.pending_finals_dropped += 1

    def _flush_pending_finals(self) -> list[Cue]:
        """Materialize the current CaptionChunker decision in order."""
        if self.media_epoch is None:
            return []
        if self.source_pts_mapper is None and self.media_anchor is not None and not self.media_anchor.ready:
            # Audio-leg mode: without a measured offset the cue would land on
            # a meaningless timeline. Hold (not drop) until the anchor
            # converges — typically ~3s after both legs produce media.
            #
            # Holding is bounded by time as well as by count: a cue still held
            # long after its audio finished can never be displayed on the
            # delayed player anyway, so keeping it only delays every later cue.
            self._drop_stale_pending_finals()
            return []
        cues: list[Cue] = []
        while self._pending_finals:
            pending = self._pending_finals.popleft()
            cue = self._materialize_cue(pending, self.media_epoch)
            if cue is None:
                # Anchor lost readiness mid-flush (skip reseed): requeue and
                # retry on the next flush rather than dropping the sentence.
                self._pending_finals.appendleft(pending)
                break
            cues.append(cue)
        return cues

    def _drop_stale_pending_finals(self) -> None:
        """Discard held finals that can no longer be displayed.

        Called only while the flush is blocked on a non-converged anchor. The
        cue's own audio finished at ``audio_end_wall``; once that is further back
        than the whole playback delay window, publishing it would place a
        subtitle behind the viewer's playhead, so it is resolved terminally
        instead of accumulating.
        """
        if self.playback_delay_seconds is None:
            return
        horizon = self.wall_clock() - max(0.0, float(self.playback_delay_seconds())) - _PENDING_FINAL_GRACE_SECONDS
        while self._pending_finals and self._pending_finals[0].audio_end_wall < horizon:
            self._pending_finals.popleft()
            self.stats.pending_finals_dropped += 1

    @staticmethod
    def _frontier_time(samples: deque[tuple[float, float]], position: float) -> float | None:
        """Return when the audio/evidence frontier first covered position."""
        # A full ring may have evicted the real first covering sample. An old
        # position must not borrow the oldest remaining timestamp.
        if samples and len(samples) == samples.maxlen and position < samples[0][0] - 1e-6:
            return None
        for frontier, observed_at in samples:
            if frontier + 1e-6 >= position:
                return observed_at
        return None

    def _materialize_cue(self, pending: _PendingFinal, epoch: float) -> Cue | None:
        # One Caption Chunk = one cue. Visual line wrapping remains a renderer
        # concern; one ASR utterance may now materialize several such cues.
        hold = calculate_hold(
            pending.text,
            minimum=self.hold_minimum,
            seconds_per_char=self.hold_seconds_per_char,
            maximum=self.hold_maximum,
        )
        if len(pending.text) > self.overlong_chars:
            # Utterance cap failed upstream; carry it with a longer hold
            # instead of fabricating sub-cue timestamps.
            self.stats.overlong_cues += 1
            hold = max(hold, min(self.hold_maximum, 0.09 * len(pending.text)))
        if self.source_pts_mapper is not None:
            begin_mapped = (
                self.source_pts_mapper(pending.begin_pcm) if pending.begin_pcm is not None else None
            )
            end_mapped = self.source_pts_mapper(pending.end_pcm)
            if end_mapped is None or (pending.begin_pcm is not None and begin_mapped is None):
                # PTS metadata can be absent on a provider. Preserve the
                # existing measured-anchor fallback instead of dropping cues.
                if self.media_anchor is None:
                    return None
                begin_mapped = self.media_anchor.map_pcm(pending.begin_pcm) if pending.begin_pcm is not None else None
                end_mapped = self.media_anchor.map_pcm(pending.end_pcm)
                if end_mapped is None or (pending.begin_pcm is not None and begin_mapped is None):
                    return None
        elif self.media_anchor is not None:
            begin_mapped = (
                self.media_anchor.map_pcm(pending.begin_pcm) if pending.begin_pcm is not None else None
            )
            end_mapped = self.media_anchor.map_pcm(pending.end_pcm)
            if end_mapped is None or (pending.begin_pcm is not None and begin_mapped is None):
                return None  # anchor lost readiness mid-flush; caller holds
        else:
            begin_mapped = pending.begin_pcm
            end_mapped = pending.end_pcm
        t_start = epoch + begin_mapped if begin_mapped is not None else None
        # Pending source text is internal work; the renderer admits only done translations.
        state = "src" if self.translation_provider is not None else "failed"
        self._source_ready_lags.append(max(0.0, self.wall_clock() - pending.audio_end_wall))
        cue = self.store.add(
            t_start=t_start,
            t_end=epoch + end_mapped,
            hold=hold,
            src=pending.text,
            state=state,
            # A final cue keeps its Adapter-reported canonical language;
            # without a report it falls back to the specified/preferred one.
            lang=canonicalize_tag_or_none(pending.lang) or self.source_language,
            timing_source=pending.timing_source,
            speaker=pending.speaker,
            generation=pending.generation,
            chunk_order=pending.chunk_order,
            starts_mid_sentence=pending.starts_mid_sentence,
            ends_mid_sentence=pending.ends_mid_sentence,
            cut_reason=pending.cut_reason,
            item_id=pending.item_id,
        )
        self._timing_source_counts[pending.timing_source] = self._timing_source_counts.get(pending.timing_source, 0) + 1
        if pending.item_id:
            self._cue_item_ids[cue.id] = pending.item_id
            if len(self._cue_item_ids) > 512:
                self._cue_item_ids.pop(next(iter(self._cue_item_ids)), None)
        if pending.chunk_emitted_mono is not None:
            self._cue_latencies[cue.id] = _CueLatency(
                end_pcm=pending.end_pcm,
                evidence_available=pending.evidence_available_mono,
                chunk_emitted=pending.chunk_emitted_mono,
                cue_stored=self.monotonic(),
                audio_pushed=self._frontier_time(self._audio_push_times, pending.end_pcm),
            )
        self._audio_end_walls[cue.id] = pending.audio_end_wall
        if len(self._audio_end_walls) > 512:
            oldest = next(iter(self._audio_end_walls))
            self._audio_end_walls.pop(oldest, None)
        if self.translation_provider is not None:
            self._enqueue_translation(cue)
        else:
            self.stats.source_only_cues += 1
            self._record_ready_lag(cue.id, success=False)
        return cue

    @property
    def _queue_limit(self) -> int:
        return max(16, 4 * self.translation_workers)

    @property
    def _translation_backlog(self) -> int:
        return self._translation_queue.qsize()

    def _discard_translation_queue(self) -> None:
        """Terminally resolve queued work so it cannot leak into a restart."""
        while True:
            try:
                cue = self._translation_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._translation_queue.task_done()
            self._translation_budgets.pop(cue.id, None)
            self._drop_translation_cue(cue)

        self._translation_budgets.clear()

    def _track_translation_pressure(self) -> None:
        backlog = self._translation_backlog
        if backlog > 2 * self.translation_workers:
            self._degrade_level = 2
        elif backlog > self.translation_workers:
            self._degrade_level = max(self._degrade_level, 1)
        self._empty_since = None

    def _enqueue_translation(self, cue: Cue) -> None:
        self.context.add(cue.src, None, generation=cue.generation,
                         chunk_order=cue.chunk_order, media_t_end=cue.t_end)
        self.context.trim(generation=cue.generation, at_media_time=cue.t_end)
        budget = self._translation_budget_policy.allocate(self._audio_end_walls.get(cue.id))
        # No lateness drop here, deliberately. How far the pushed audio has run
        # ahead of a sentence is only an ESTIMATE of what the viewer has already
        # passed; the renderer knows the playhead and already refuses cues whose
        # window has gone. Dropping on the estimate threw away captions the
        # renderer would still have shown: measured at a session head, three were
        # dropped with 2.6s, 3.3s and 6.3s of real time left, and none of them was
        # ever handed to a provider. The queue bound below still caps a burst.
        self._translation_budgets[cue.id] = budget
        while self._translation_backlog >= self._queue_limit:
            dropped = self._translation_queue.get_nowait()
            # get_nowait() bypasses the worker's task_done(), so balance the
            # unfinished-task counter here or join() would never settle.
            self._translation_queue.task_done()
            self._translation_budgets.pop(dropped.id, None)
            self._drop_translation_cue(dropped)

        # Preceding source text is available immediately; completed translations
        # enrich that snapshot. Neither requires a predecessor dependency.
        self._translation_queue.put_nowait(cue)
        self._track_translation_pressure()

    async def _repair_native_translation_cues(self, item_id: str) -> None:
        """Upgrade retained source-only cues when native evidence arrives later.

        The normal worker owns pending cues. This path only revisits cues already
        published as ``source_only`` so it cannot race the worker for the same
        translation boundary. It performs a non-blocking ledger lookup; if the
        Provider still has no exact boundary, the cue simply remains source-only.
        """
        bus = self.native_translation_bus
        if bus is None:
            return
        for cue in self.store.by_item_id(item_id):
            if cue.state != "source_only" or cue.zh:
                continue
            translated = await bus.resolve(
                item_id=item_id,
                source_text=cue.src,
                deadline_monotonic=self.monotonic() + 0.001,
                monotonic=self.monotonic,
                generation=cue.generation,
            )
            if not translated:
                continue
            self.context.add(
                cue.src, translated,
                generation=cue.generation,
                chunk_order=cue.chunk_order,
                media_t_end=cue.t_end,
            )
            with contextlib.suppress(KeyError, ValueError):
                self.store.update(
                    cue.id,
                    zh=translated,
                    state="done",
                    hold=calculate_hold(
                        cue.src, translated,
                        minimum=self.hold_minimum,
                        seconds_per_char=self.hold_seconds_per_char,
                        maximum=self.hold_maximum,
                    ),
                )
                self.stats.native_translation_late_patches += 1

    async def _translation_worker(self) -> None:
        async with translation_session():
            await self._translation_worker_loop()

    async def _translation_worker_loop(self) -> None:
        while self._running:
            try:
                cue = await asyncio.wait_for(self._translation_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                self._update_recovery(self._translation_backlog)
                continue
            # Everything that can raise must live inside the try below. When
            # request construction sat outside it, any exception there killed
            # the worker task outright and silently: cues then piled up in
            # "src" forever and no status field showed why.
            try:
                backlog = self._translation_backlog
                self._update_recovery(backlog)
                latency = self._cue_latencies.get(cue.id)
                if latency is not None:
                    latency.translation_started = self.monotonic()
                provider = self.translation_provider
                if self._degrade_level >= 2 and self.fallback_translation_provider is not None:
                    provider = self.fallback_translation_provider
                if provider is None:
                    # A runtime provider hot-swap (server.py:551-553 sets both
                    # providers to None) can leave a cue queued with no provider
                    # to serve it. Falling straight through to task_done() left
                    # the cue in state "src" forever and retained its
                    # _cue_latencies and _audio_end_walls entries, because only
                    # _record_ready_lag ever pops those. Resolve it terminally
                    # instead.
                    self._drop_translation_cue(cue)
                    continue  # the finally below still runs task_done()
                budget = self._translation_budgets.get(cue.id)
                if budget is None:
                    budget = self._translation_budget_policy.allocate(None)
                deadline = budget.deadline_monotonic
                remaining = budget.remaining(self.monotonic())
                if remaining <= 0:
                    raise TranslationDeadlineExpired(
                        "translation deadline expired while waiting for a free worker"
                    )
                pair_limit = 2 if self._degrade_level >= 1 else None
                if (
                    provider.capabilities.rolling_context
                    and cue.generation is not None
                    and cue.chunk_order is not None
                ):
                    history = self.context.history(
                        generation=cue.generation,
                        before_order=cue.chunk_order,
                        at_media_time=cue.t_end,
                        pair_limit=pair_limit,
                        include_untranslated=True,
                    )
                else:
                    history = []
                if (
                    provider.capabilities.rolling_context
                    and cue.generation is not None
                    and cue.chunk_order is not None
                    and cue.chunk_order > 1
                    and not self.context.contains(
                        generation=cue.generation,
                        chunk_order=cue.chunk_order - 1,
                    )
                ):
                    self.stats.translation_context_missing_immediate_predecessor += 1
                request = TranslationRequest(
                    source_text=cue.src,
                    # The translation source language comes from the cue, not
                    # one immutable stream-level string: detection and
                    # code-switching may change it sentence by sentence.
                    meta=dataclasses.replace(self.meta, source_lang=cue.lang),
                    history=history,
                    glossary=self.glossary if provider.capabilities.glossary else [],
                    deadline_monotonic=deadline,
                    generation=cue.generation,
                    chunk_order=cue.chunk_order,
                    starts_mid_sentence=cue.starts_mid_sentence,
                    ends_mid_sentence=cue.ends_mid_sentence,
                    cut_reason=cue.cut_reason,
                    # Session-backed translation resolves this cue against the
                    # Provider utterance it was built from; every other Provider
                    # ignores the field.
                    item_id=self._cue_item_ids.get(cue.id),
                )
                native_provider = bool(getattr(provider, "is_native_session", False))
                with contextlib.suppress(KeyError, ValueError):
                    self.store.update(cue.id, state="waiting_native" if native_provider else "translating")
                self._active_translation_provider_id = provider.id
                # This module spends one bounded request per cue.  A configured
                # FallbackChain owns the primary -> fallback handoff; repeating
                # it here multiplies provider calls and delays newer cues.
                remaining = budget.remaining(self.monotonic())
                if remaining <= 0:
                    raise TranslationDeadlineExpired("translation deadline expired before provider call")
                self.stats.translation_attempts += 1
                self.stats.last_translation_attempt_at = self.wall_clock()
                request_started = self.monotonic()
                result = await asyncio.wait_for(
                    provider.translate(request), timeout=max(0.001, remaining)
                )
                self._record_translation_usage(result.provider_id, result.usage)
                if native_provider and result.provider_id != provider.id:
                    self.stats.native_fallback_used += 1
                self._active_translation_provider_id = result.provider_id
                translated = (result.text or "").strip()
                if not translated:
                    raise ValueError("translation provider returned empty text")
                if latency is not None:
                    latency.provider_finished = self.monotonic()
                self._record_translation_latency(round(1000 * (self.monotonic() - request_started)))
                if request.meta.target_lang != self.meta.target_lang:
                    # The user changed target language while this Provider call
                    # was in flight. Do not publish text in the stale language;
                    # queued and future cues pick up the current StreamMeta.
                    with contextlib.suppress(KeyError, ValueError):
                        self.store.update(cue.id, state="failed")
                    self.stats.source_only_cues += 1
                    self._record_ready_lag(cue.id, success=False)
                    continue
                self.context.add(
                    cue.src,
                    translated,
                    generation=cue.generation,
                    chunk_order=cue.chunk_order,
                    media_t_end=cue.t_end,
                )
                self.context.trim(generation=cue.generation, at_media_time=cue.t_end)
                late_native_patch = (
                    native_provider
                    and cue.zh is None
                    and self._viewer_wall_time is not None
                    and self.monotonic() - self._viewer_wall_time_at <= VIEWER_POSITION_TTL_SECONDS
                    and cue.t_start is not None
                    and self._viewer_wall_time >= cue.t_start
                )
                with contextlib.suppress(KeyError, ValueError):
                    self.store.update(
                        cue.id,
                        zh=translated,
                        state="done",
                        hold=calculate_hold(
                            cue.src,
                            translated,
                            minimum=self.hold_minimum,
                            seconds_per_char=self.hold_seconds_per_char,
                            maximum=self.hold_maximum,
                        ),
                    )
                if late_native_patch:
                    self.stats.native_translation_late_patches += 1
                self._record_ready_lag(cue.id, success=True)
                self._translation_failure_recorded = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if isinstance(exc, TranslationDeadlineExpired):
                    self.stats.translation_deadline_expired += 1
                    self._drop_translation_cue(cue)
                else:
                    # A timeout raised by wait_for means the provider consumed
                    # its allotted call budget; it is not the queue's stale
                    # work path and should remain visible as a provider issue.
                    self._mark_translation_failed(cue, exc)
            finally:
                self._translation_budgets.pop(cue.id, None)
                self._translation_queue.task_done()
                self._update_recovery(self._translation_backlog)

    def _mark_translation_failed(
        self,
        cue: Cue,
        exc: BaseException,
    ) -> None:
        """Resolve one provider failure and keep it separate from stale work."""

        latency = self._cue_latencies.get(cue.id)
        if latency is not None and latency.translation_started is not None:
            latency.provider_finished = self.monotonic()
        native_source_only = (
            self.native_translation_bus is not None
            and isinstance(exc, ProviderRefusalError)
            and getattr(self.translation_provider, "fallback", None) is None
        )
        self.stats.last_translation_error = self._error_text(exc)
        if native_source_only:
            with contextlib.suppress(KeyError, ValueError):
                self.store.update(cue.id, state="source_only")
            self.stats.source_only_cues += 1
            self._record_ready_lag(cue.id, success=False)
            return
        self.stats.translation_failures += 1
        self.stats.translation_provider_failures += 1
        self._record_error(exc)
        if not self._translation_failure_recorded:
            # The cue is published as source-only, so nothing on screen says a
            # translation was attempted and lost.
            log_record(
                "error",
                "translation",
                f"translation failed, showing source text only: {self.stats.last_translation_error}",
            )
            self._translation_failure_recorded = True
        with contextlib.suppress(KeyError, ValueError):
            self.store.update(cue.id, state="failed")
        self.stats.source_only_cues += 1
        self._record_ready_lag(cue.id, success=False)

    def _drop_translation_cue(self, cue: Cue) -> None:
        """Hide stale/overflowed work without invoking a translation adapter."""

        with contextlib.suppress(KeyError, ValueError):
            self.store.update(cue.id, state="source_only" if self.native_translation_bus is not None else "failed")
        self.stats.translation_dropped += 1
        self.stats.source_only_cues += 1
        self._record_ready_lag(cue.id, success=False)

    def _record_ready_lag(self, cue_id: int, *, success: bool = False) -> None:
        """Record terminal readiness and the smallest useful stage breakdown."""
        latency = self._cue_latencies.get(cue_id)
        now = self.monotonic()
        if success and latency is not None and latency.audio_pushed is not None:
            elapsed = now - latency.audio_pushed
            if math.isfinite(elapsed) and elapsed >= 0:
                self._translation_processing.append((now, elapsed))
        self._record_stage_lags(cue_id)
        audio_end_wall = self._audio_end_walls.pop(cue_id, None)
        if audio_end_wall is None:
            return
        lag = max(0.0, self.wall_clock() - audio_end_wall)
        self._terminal_outcome_lags.append(lag)
        if success:
            self._translation_success_ready_lags.append(lag)

    def _record_stage_lags(self, cue_id: int) -> None:
        latency = self._cue_latencies.pop(cue_id, None)
        if latency is None:
            return
        ready = self.monotonic()
        audio_pushed = latency.audio_pushed
        markers = {
            "audioPushed": audio_pushed,
            "evidenceAvailable": latency.evidence_available,
            "chunkEmitted": latency.chunk_emitted,
            "translationStarted": latency.translation_started,
            "providerFinished": latency.provider_finished,
            "cueReady": ready,
        }
        reasons = [key for key, value in markers.items() if value is None]
        if not reasons:
            times = list(markers.values())
            if any(b < a - 1e-6 for a, b in zip(times, times[1:])):
                reasons.append("nonMonotonic")
        if reasons:
            self._latency_unknown += 1
            for reason in reasons:
                self._latency_unknown_reasons[reason] = self._latency_unknown_reasons.get(reason, 0) + 1
            return
        assert audio_pushed is not None
        assert latency.evidence_available is not None
        assert latency.translation_started is not None
        assert latency.provider_finished is not None
        self._stage_lags["asrAdapter"].append(max(0.0, latency.evidence_available - audio_pushed))
        self._stage_lags["chunkerPolicy"].append(max(0.0, latency.chunk_emitted - latency.evidence_available))
        self._stage_lags["translationQueue"].append(max(0.0, latency.translation_started - latency.chunk_emitted))
        self._stage_lags["translationProvider"].append(max(0.0, latency.provider_finished - latency.translation_started))
        self._stage_lags["storeUpdate"].append(max(0.0, ready - latency.provider_finished))
        self._stage_lags["totalReady"].append(max(0.0, ready - audio_pushed))
        self._latency_completed += 1
        self._latency_last_sample_at = ready

    @staticmethod
    def _lag_percentiles(values: deque[float]) -> tuple[float | None, float | None]:
        if not values:
            return None, None
        ordered = sorted(values)
        def percentile(fraction: float) -> float:
            index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
            return ordered[index]
        return round(percentile(0.5), 3), round(percentile(0.95), 3)

    def _record_translation_latency(self, latency_ms: int) -> None:
        now = self.monotonic()
        fresh = self._translation_latency_at is not None and now - self._translation_latency_at <= 60
        self._translation_latency_at = now
        self.stats.last_translation_latency_ms = int(latency_ms)
        current = self.stats.avg_translation_latency_ms if fresh else None
        self.stats.avg_translation_latency_ms = (
            float(latency_ms) if current is None else round(0.7 * current + 0.3 * float(latency_ms), 1)
        )

    def _record_translation_usage(self, provider_id: str, usage: dict[str, Any] | None) -> None:
        provider = self._translation_usage_by_provider.setdefault(provider_id, {
            "calls": 0,
            "unknownUsageCalls": 0,
            "nonCachedInputTokens": 0,
            "cachedInputTokens": 0,
            "cacheWriteInputTokens": 0,
            "outputTokens": 0,
            "totalTokens": 0,
            "rawUsage": {},
        })
        provider["calls"] += 1
        normalized = normalize_translation_usage(usage)
        if normalized is None:
            provider["unknownUsageCalls"] += 1
            if isinstance(usage, dict):
                provider["rawUsage"] = dict(usage)
            return
        for field in ("nonCachedInputTokens", "cachedInputTokens", "cacheWriteInputTokens", "outputTokens", "totalTokens"):
            provider[field] += normalized[field]
        provider["rawUsage"] = normalized["rawUsage"]

    def _translation_metering(self) -> tuple[dict[str, Any], float | None, str | None]:
        by_provider = {provider_id: dict(values) for provider_id, values in self._translation_usage_by_provider.items()}
        usage = {
            "calls": sum(item["calls"] for item in by_provider.values()),
            "unknownUsageCalls": sum(item["unknownUsageCalls"] for item in by_provider.values()),
            "nonCachedInputTokens": sum(item["nonCachedInputTokens"] for item in by_provider.values()),
            "cachedInputTokens": sum(item["cachedInputTokens"] for item in by_provider.values()),
            "cacheWriteInputTokens": sum(item["cacheWriteInputTokens"] for item in by_provider.values()),
            "outputTokens": sum(item["outputTokens"] for item in by_provider.values()),
            "totalTokens": sum(item["totalTokens"] for item in by_provider.values()),
            "byProvider": by_provider,
        }
        if usage["unknownUsageCalls"]:
            return usage, None, "translation usage unavailable for one or more completed calls"
        cost = 0.0
        # 一次会话可能同时用过主力和兜底两个 Provider，而它们的报价币种未必
        # 相同（例如百炼按人民币、Gemini 按美元）。不同币种不能相加，所以这里
        # 按币种分组累计，由调用方决定合计怎么写。
        costs_by_currency: dict[str, float] = {}
        for provider_id, provider_usage in by_provider.items():
            prices = self.translation_pricing_by_provider.get(provider_id, {})
            required = (prices.get("input"), prices.get("cachedInput"), prices.get("output"))
            if any(price is None for price in required):
                return usage, None, f"translation pricing incomplete for provider {provider_id}"
            # Cache-write pricing is optional per Provider, but reported
            # cache-write tokens without a price stay unavailable, never
            # silently billed at zero.
            cache_write_price = prices.get("cacheWrite")
            if provider_usage["cacheWriteInputTokens"] > 0 and cache_write_price is None:
                return usage, None, f"translation pricing incomplete for provider {provider_id}"
            input_price, cached_price, output_price = required
            provider_cost = (
                provider_usage["nonCachedInputTokens"] * input_price
                + provider_usage["cachedInputTokens"] * cached_price
                + provider_usage["cacheWriteInputTokens"] * (cache_write_price or 0)
                + provider_usage["outputTokens"] * output_price
            ) / 1_000_000
            provider_currency_code = str(prices.get("currency") or "USD").upper()
            provider_usage["estimatedCostCny"] = round(provider_cost, 9)
            provider_usage["costCurrency"] = provider_currency_code
            costs_by_currency[provider_currency_code] = costs_by_currency.get(provider_currency_code, 0.0) + provider_cost
            cost += provider_cost
        usage["costsByCurrency"] = [
            {"currency": code, "amount": round(amount, 9)}
            for code, amount in sorted(costs_by_currency.items())
        ]
        if not by_provider:
            return usage, None, "translation usage unavailable"
        if len(costs_by_currency) > 1:
            # 混合币种时给不出单一数字；由前端逐币种列出，而不是凭空换算。
            return usage, None, "translation providers bill in more than one currency"
        return usage, round(cost, 9), None

    def _update_recovery(self, backlog: int) -> None:
        now = self.monotonic()
        if backlog:
            self._empty_since = None
            return
        if self._degrade_level == 0:
            return
        if self._empty_since is None:
            self._empty_since = now
        elif now - self._empty_since >= self.recovery_seconds:
            self._degrade_level = 0
            self._empty_since = None

    def _ingest_is_unhealthy(self) -> bool:
        if self.ingest_status_callback is None:
            return False
        try:
            status = self.ingest_status_callback()
        except Exception as exc:
            self._record_error(exc)
            return False
        if not status:
            return False
        if isinstance(status, bool):
            return status
        if isinstance(status, str):
            # A non-empty string is conventionally the ingest's current error.
            return bool(status.strip())
        if isinstance(status, dict):
            # Only a hard, current ingest error suppresses a cue. The log tail
            # used to be pattern-matched for "skipping"/"expired from
            # playlists" too, but yt-dlp keeps those lines in its rolling tail
            # long after the condition clears -- so transcripts we had already
            # paid for were being discarded while sourceError was null.
            return bool(status.get("sourceError") or status.get("source_error"))
        return bool(status)

    def caption_drafts(self) -> list[dict[str, Any]]:
        """Preload stable source previews; the browser owns onset admission.

        A viewer-position round trip at onset imposed up to a polling interval
        of latency even when recognition was ready seconds ahead of the viewer.
        Never guess missing media anchors or send mutable ASR hypotheses here.
        """
        if not self._running or self.media_epoch is None:
            return []
        drafts = []
        for pending in self.caption_chunker.pending_captions():
            text = clean_subtitle_text(pending.text)
            if not text:
                continue
            begin = self._draft_media_time(self.acoustic_onset.refine(pending.begin_pcm, pending.end_pcm))
            end = self._draft_media_time(pending.end_pcm)
            if begin is None or end is None or end < begin:
                continue
            drafts.append({"text": text, "itemId": pending.item_id,
                "generation": self._generation, "tStart": begin, "tEnd": end,
                "lang": pending.language or self.source_language, "speaker": pending.speaker})
        return drafts

    def caption_draft(self) -> dict[str, Any] | None:
        """The line the chunker is holding, so the player can draw it early.

        A caption from a session-translating Provider waits for that Provider's
        own turn end. Measured on a real LiveTranslate session that is 0.03-0.4s
        of extra wait per turn, and up to the chunker's 7-second backstop when the
        speaker does not pause -- during which the recognized text is already in
        hand and the screen is empty. This hands the player what is held, so the
        source line grows while the sentence is being heard and the cue with its
        translation replaces it when the Provider closes the turn.

        Never a cue, and never the cue's substitute: it is not stored, exported,
        or offered to the translation bus, since the text it holds is one the
        Provider has not aligned a translation to.
        """
        if not self.caption_chunker.segments_only or not self._running:
            return None
        if self.media_epoch is None:
            return None
        heard = self._viewer_pcm_position()
        if self.media_anchor is not None and heard is None:
            # The audio leg runs ahead of the playhead by the whole playback
            # delay, so without a playhead there is no way to say which of the
            # held words the viewer has heard. A cue is not stamped in this
            # state either, and guessing here would spoil the stream.
            return None
        pending = self.caption_chunker.pending_caption(heard)
        if pending is None:
            return None
        text = clean_subtitle_text(pending.text)
        if not text:
            return None
        return {
            "text": text,
            "itemId": pending.item_id,
            "tStart": self._draft_media_time(self.acoustic_onset.refine(pending.begin_pcm, pending.end_pcm)),
            "tEnd": self._draft_media_time(pending.end_pcm),
            "lang": pending.language or self.source_language,
            "speaker": pending.speaker,
        }

    def _draft_media_time(self, pcm: float) -> float | None:
        mapped = None
        if self.source_pts_mapper is not None:
            mapped = self.source_pts_mapper(pcm)
        if mapped is None and self.media_anchor is not None:
            mapped = self.media_anchor.map_pcm(pcm)
        if mapped is None:
            if self.media_anchor is not None or self.source_pts_mapper is not None:
                return None  # A draft cannot invent a position a cue cannot prove.
            mapped = pcm
        return None if self.media_epoch is None else self.media_epoch + mapped

    def status(self) -> dict[str, Any]:
        epoch = self.media_epoch
        now = self.monotonic()
        while self._translation_processing and now - self._translation_processing[0][0] > 60:
            self._translation_processing.popleft()
        processing_p50, processing_p95 = self._lag_percentiles(deque(value for _, value in self._translation_processing))
        translation_latency_fresh = self._translation_latency_at is not None and now - self._translation_latency_at <= 60
        source_ready_p50, source_ready_p95 = self._lag_percentiles(self._source_ready_lags)
        success_ready_p50, success_ready_p95 = self._lag_percentiles(self._translation_success_ready_lags)
        terminal_ready_p50, terminal_ready_p95 = self._lag_percentiles(self._terminal_outcome_lags)
        stage_percentiles = {
            name: self._lag_percentiles(values)
            for name, values in self._stage_lags.items()
        }
        price = getattr(self.asr_provider, "price_per_second_cny", None)
        asr_cost = round(self._pcm_offset * price, 9) if price is not None else None
        asr_reason = None if price is not None else "ASR pricing unavailable"
        asr_currency_code = str(getattr(self, "asr_currency", None) or "USD").upper()
        translation_usage, translation_cost, translation_reason = self._translation_metering()
        # 合计按币种分组。只有 ASR 与翻译「都」可估算时才给合计——卡片上写明
        # 了「都可估算时显示」，只算一半的合计数是误导。混合币种同样给不出
        # 「一个」数字，此时逐币种列出，绝不引入汇率去硬凑。
        costs_by_currency: list[dict[str, Any]] = []
        single_currency = None
        total_cost = None
        if asr_cost is not None and translation_cost is not None:
            combined: dict[str, float] = {asr_currency_code: asr_cost}
            for entry in translation_usage.get("costsByCurrency") or []:
                code = str(entry.get("currency") or "USD").upper()
                combined[code] = combined.get(code, 0.0) + float(entry.get("amount") or 0.0)
            costs_by_currency = [
                {"currency": code, "amount": round(amount, 9)}
                for code, amount in sorted(combined.items())
            ]
            if len(costs_by_currency) == 1:
                single_currency = costs_by_currency[0]["currency"]
                total_cost = costs_by_currency[0]["amount"]
        if translation_usage.get("costsByCurrency"):
            translation_currency = (
                translation_usage["costsByCurrency"][0]["currency"]
                if len(translation_usage["costsByCurrency"]) == 1
                else None
            )
        else:
            translation_currency = None
        total_reason = None
        if total_cost is None:
            total_reason = (
                "costs span more than one currency"
                if len(costs_by_currency) > 1
                else "; ".join(reason for reason in (asr_reason, translation_reason) if reason)
            )
        chunker = self.caption_chunker.telemetry()
        return {
            "acousticOnsetRefinements": self.stats.acoustic_onset_refinements,
            "acousticOnsetMaxAdjustmentMs": round(self.stats.acoustic_onset_max_adjustment_ms, 2),
            "running": self._running,
            "sampleRate": self.sample_rate,
            "sourceLanguagePolicy": self.source_policy.to_json(),
            "targetLanguage": self.meta.target_lang,
            "pdtEpoch": epoch,
            "timelineSource": "private-hls" if epoch is not None else None,
            "captionSource": "audio-leg" if self.media_anchor is not None else "private-hls",
            "mediaAnchor": (
                {
                    "ready": self.media_anchor.ready,
                    "samples": self.media_anchor.samples,
                    "offset": round(self.media_anchor.offset, 3) if self.media_anchor.offset is not None else None,
                    # The source-clock offset when both legs carry the source's
                    # own timestamps. When it is present it IS the offset; when
                    # it is null the offset above came from the window instead,
                    # so a session that quietly fell back is visible here.
                    "exactOffset": (
                        round(self.media_anchor.exact_offset, 3)
                        if self.media_anchor.exact_offset is not None
                        else None
                    ),
                    # Same window median without the stage-backlog correction, so
                    # the correction in force is `offset - windowOffset`.
                    "windowOffset": (
                        round(self.media_anchor.window_offset, 3)
                        if self.media_anchor.window_offset is not None
                        else None
                    ),
                    "spread": round(self.media_anchor.spread, 3) if self.media_anchor.spread is not None else None,
                    "drift": round(self.media_anchor.drift, 3) if self.media_anchor.drift is not None else None,
                }
                if self.media_anchor is not None
                else None
            ),
            "pendingFinals": len(self._pending_finals),
            "pendingFinalsDropped": self.stats.pending_finals_dropped,
            "pcmOffset": round(self._pcm_offset, 3),
            "asrSeconds": round(self._pcm_offset, 3),
            # Audio handed over that the ASR has not confirmed yet: the quantity
            # the sender holds at ASR_QUEUE_LIMIT_SECONDS. Null until the first
            # confirmation arrives.
            "asrQueueSeconds": (
                round(self._last_sent_pcm_offset - self._asr_evidence_pcm, 3)
                if self._asr_evidence_pcm is not None
                else None
            ),
            # How far the audio handed to the ASR runs ahead of the renderer's
            # playhead. Measurement only, and the margin a caption is published
            # with. Null means no usable playhead was stated.
            "viewerLeadSeconds": (
                round(lead, 3) if (lead := self.viewer_lead_seconds()) is not None else None
            ),
            # The stage backlog the anchor adds back to its median, and the two
            # rolling minima it comes from. `offset - windowOffset` is the
            # correction actually in force.
            "anchorBacklogVideo": (
                round(self._anchor_backlog_video, 3) if self._anchor_backlog_video is not None else None
            ),
            "anchorBacklogAudio": (
                round(self._anchor_backlog_audio, 3) if self._anchor_backlog_audio is not None else None
            ),
            "anchorCorrection": (
                round(self._anchor_correction, 3) if self._anchor_correction is not None else None
            ),
            "asrUsage": {"seconds": round(self._pcm_offset, 3)},
            "asrEstimatedCostCny": asr_cost,
            "asrCostCurrency": asr_currency_code,
            "asrEstimateReason": asr_reason,
            "translationUsage": translation_usage,
            "translationEstimatedCostCny": translation_cost,
            "translationCostCurrency": translation_currency,
            "translationEstimateReason": translation_reason,
            "totalEstimatedCostCny": total_cost,
            "totalCostCurrency": single_currency,
            "costsByCurrency": costs_by_currency,
            "totalEstimateReason": total_reason,
            "estimatedCostCny": asr_cost,
            "pcmDropped": self.stats.pcm_dropped,
            "captionChunks": chunker.caption_chunks,
            "chunkSpanP50": round(chunker.chunk_span_p50, 3),
            "chunkSpanP95": round(chunker.chunk_span_p95, 3),
            "chunkSpanMax": round(chunker.chunk_span_max, 3),
            "chunkCutReasons": dict(chunker.chunk_cut_reasons),
            # Informational only: no timer force-cuts a caption, so a non-zero
            # value means an utterance stayed open past the soft 6s reference
            # without a safe boundary -- not that a cap was violated.
            "pendingEvidenceOverSoftSpan": chunker.pending_evidence_over_soft_span,
            "spanOverSoftTarget": chunker.span_over_soft_target,
            "localAgreementCommits": chunker.local_agreement_commits,
            "localAgreementRewrites": chunker.local_agreement_rewrites,
            "residualFlushes": chunker.residual_flushes,
            "finalReconciliationConflicts": chunker.final_reconciliation_conflicts,
            "translationContextMissingImmediatePredecessor": self.stats.translation_context_missing_immediate_predecessor,
            "overlongCues": self.stats.overlong_cues,
            "sourceOnlyCues": self.stats.source_only_cues,
            "nativeTranslationUpdates": self.stats.native_translation_updates,
            "nativeTranslationPending": self.native_translation_bus.pending if self.native_translation_bus is not None else 0,
            "nativeTranslationWaiting": self.native_translation_bus.waiting if self.native_translation_bus is not None else 0,
            "nativeTranslationUnaligned": self.native_translation_bus.unaligned_total if self.native_translation_bus is not None else 0,
            "nativeSegmentClosedWithoutTranslation": self.native_translation_bus.closed_without_translation_total if self.native_translation_bus is not None else 0,
            "nativeFallbackUsed": self.stats.native_fallback_used,
            "lateTranslationPatched": self.stats.native_translation_late_patches,
            "unmappedObservations": self.stats.unmapped_observations,
            "finalDiscarded": self.stats.final_discarded,
            "speakerRevisions": self.stats.speaker_revisions,
            "timingSourceCounts": dict(self._timing_source_counts),
            "readyLagP50": terminal_ready_p50,
            "readyLagP95": terminal_ready_p95,
            "sourceReadyLagP50": source_ready_p50,
            "sourceReadyLagP95": source_ready_p95,
            "translationSuccessReadyLagP50": success_ready_p50,
            "translationSuccessReadyLagP95": success_ready_p95,
            "translationProcessingP50": processing_p50,
            "translationProcessingP95": processing_p95,
            "translationProcessingSamples": len(self._translation_processing),
            "terminalOutcomeLagP50": terminal_ready_p50,
            "terminalOutcomeLagP95": terminal_ready_p95,
            "asrAdapterDelayP50": stage_percentiles["asrAdapter"][0],
            "asrAdapterDelayP95": stage_percentiles["asrAdapter"][1],
            "chunkerPolicyDelayP50": stage_percentiles["chunkerPolicy"][0],
            "chunkerPolicyDelayP95": stage_percentiles["chunkerPolicy"][1],
            "translationQueueDelayP50": stage_percentiles["translationQueue"][0],
            "translationQueueDelayP95": stage_percentiles["translationQueue"][1],
            "translationProviderDelayP50": stage_percentiles["translationProvider"][0],
            "translationProviderDelayP95": stage_percentiles["translationProvider"][1],
            "storeUpdateDelayP50": stage_percentiles["storeUpdate"][0],
            "storeUpdateDelayP95": stage_percentiles["storeUpdate"][1],
            "totalReadyDelayP50": stage_percentiles["totalReady"][0],
            "totalReadyDelayP95": stage_percentiles["totalReady"][1],
            "latencySamples": self._latency_completed,
            "latencyUnknown": self._latency_unknown,
            "latencyUnknownReasons": dict(self._latency_unknown_reasons),
            "latencyWindowSamples": len(self._stage_lags["totalReady"]),
            "latencySampleAgeSeconds": round(self.monotonic() - self._latency_last_sample_at, 3) if self._latency_last_sample_at is not None else None,
            "suppressedByIngestError": self.stats.suppressed_by_ingest_error,
            "translationBacklog": self._translation_backlog,
            "translationDropped": self.stats.translation_dropped,
            "translationFailures": self.stats.translation_failures,
            "translationDeadlineExpired": self.stats.translation_deadline_expired,
            "translationProviderFailures": self.stats.translation_provider_failures,
            "translationAttempts": self.stats.translation_attempts,
            "translationWorkers": self.translation_workers,
            "translationWorkersAlive": sum(
                1 for task in self._tasks
                if task.get_name().startswith("subtitle-translation") and not task.done()
            ),
            "lastTranslationAttemptAt": self.stats.last_translation_attempt_at,
            "lastTranslationError": self.stats.last_translation_error,
            "lastTranslationLatencyMs": self.stats.last_translation_latency_ms,
            "avgTranslationLatencyMs": self.stats.avg_translation_latency_ms if translation_latency_fresh else None,
            "degradeLevel": self._degrade_level,
            "asrReconnects": self.stats.asr_reconnects,
            "asrProviderId": self.asr_provider.id,
            "asrProviderLabel": self.asr_provider.label,
            "translationProviderId": self._active_translation_provider_id or (
                self.translation_provider.id if self.translation_provider is not None else None
            ),
            "translationProviderLabel": self._provider_label(
                self._active_translation_provider_id,
                self.translation_provider,
                self.fallback_translation_provider,
            ),
            "lastError": self.stats.last_error,
            "timelineEpoch": epoch,
        }

    @staticmethod
    def _provider_label(provider_id: str | None, *providers: TranslationProvider | None) -> str | None:
        """Resolve the user-facing profile name, including fallback-chain members."""
        for provider in providers:
            if provider is None:
                continue
            if provider_id is None or provider.id == provider_id:
                return provider.label
            for child in getattr(provider, "providers", ()):
                if child.id == provider_id:
                    return child.label
        return provider_id

    def _record_error(self, exc: BaseException) -> None:
        self.stats.last_error = self._error_text(exc)

    @staticmethod
    def _error_text(exc: BaseException) -> str:
        message = str(exc).strip()
        return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
