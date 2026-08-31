"""Live audio-to-subtitle pipeline with failure isolation from playback.

The module deliberately accepts providers and integration callbacks instead of
importing the server or ingest implementations.  A caller attaches ``tee_sink``
to the audio byte pump **before any byte flows** (redesign Fix A) and detaches
it before ``stop()``.

Timeline contract (redesign Fix B): every cue lives on the packaging
playlist's PDT timeline as

    cue.tEnd = pdt_0 + end_pcm + C

where ``pdt_0`` is the private playlist's first segment PDT and ``C`` is the
measured constant offset between the subtitle PCM leg and the packaging media
timeline (see :class:`MediaAnchor`).  There is deliberately **no wall-clock
fallback**: before the anchor can produce an epoch, finals are parked in a
bounded pending list instead of being stamped with fabricated times.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import statistics
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from typing import Any, Protocol

from .context_manager import RollingContext
from .providers.base import (
    ASREvent,
    ASRProvider,
    ASRStream,
    StreamMeta,
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

PCM_BYTES_PER_SECOND = 16_000 * 2
PCM_CHUNK_BYTES = 3_200
PCM_CHUNK_SECONDS = PCM_CHUNK_BYTES / PCM_BYTES_PER_SECOND

MediaClock = Callable[[], "tuple[float, float] | None"]


def _median(values: list[float]) -> float:
    return statistics.median(values)


class MediaAnchor:
    """Continuously measured constant offset ``C`` between the two legs.

    Both counters advance at the same (possibly super-realtime) rate once the
    tee is attached before any byte flows, so their difference is a constant
    that startup bursts cannot perturb::

        C_sample = private_media_seconds + 0.5 * target_duration - pcm_offset

    The ``+0.5 * target_duration`` term compensates the sawtooth quantization
    of "a segment only becomes visible after it is fully written" (midpoint).
    The first ``freeze_samples`` samples are median-ed and frozen; sampling
    then continues purely to report drift.
    """

    def __init__(
        self,
        *,
        pdt_epoch: Callable[[], float | None],
        media_clock: MediaClock,
        sample_interval: float = 1.0,
        freeze_samples: int = 20,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if sample_interval < 0:
            raise ValueError("sample_interval must be non-negative")
        if freeze_samples < 1:
            raise ValueError("freeze_samples must be positive")
        self.pdt_epoch_callback = pdt_epoch
        self.media_clock = media_clock
        self.sample_interval = sample_interval
        self.freeze_samples = freeze_samples
        self.monotonic = monotonic
        self.samples: list[float] = []
        self.total_samples = 0
        self._frozen: float | None = None
        self._drift: float | None = None
        self._last_sample_at: float | None = None
        self._last_observed: tuple[float, float] | None = None
        self._pdt0: float | None = None
        self.last_error: str | None = None

    def maybe_sample(self, pcm_offset: float) -> None:
        """Take synchronized counter samples at ``sample_interval`` cadence.

        A sample is valid only when both media counters moved since the prior
        observation, or when both have remained stable for two observations.
        Rejecting one-sided movement prevents startup/EOF catch-up from
        masquerading as tens of seconds of anchor drift.
        """
        now = self.monotonic()
        if (
            self._last_sample_at is not None
            and now - self._last_sample_at < self.sample_interval
        ):
            return
        try:
            clock = self.media_clock()
        except Exception:
            return  # The publisher is playback infrastructure; never surface.
        if clock is None:
            return
        private_seconds, target_duration = clock
        if private_seconds is None or private_seconds <= 0:
            return
        self._last_sample_at = now
        current = (float(pcm_offset), float(private_seconds))
        previous = self._last_observed
        self._last_observed = current
        if previous is None:
            return
        pcm_moved = abs(current[0] - previous[0]) > 1e-6
        private_moved = abs(current[1] - previous[1]) > 1e-6
        # Valid anchor samples require both media counters to advance. At EOF
        # both are stable and the midpoint compensation would add a false
        # +0.5*targetDuration; during one-sided catch-up the difference is not
        # a same-instant measurement at all.
        if not (pcm_moved and private_moved):
            return

        self.total_samples += 1
        sample = current[1] + 0.5 * float(target_duration or 1.0) - current[0]
        if self._frozen is None:
            self.samples.append(sample)
            if len(self.samples) >= self.freeze_samples:
                self._frozen = _median(self.samples)
        else:
            self._drift = sample - self._frozen

    def _pdt_zero(self) -> float | None:
        if self._pdt0 is not None:
            return self._pdt0
        try:
            value = self.pdt_epoch_callback()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        if value is None:
            return None
        self._pdt0 = float(value)
        return self._pdt0

    @property
    def offset(self) -> float | None:
        """The constant ``C`` (frozen median, or warm-up median)."""
        if self._frozen is not None:
            return self._frozen
        if self.samples:
            return _median(self.samples)
        return None

    def epoch(self, pcm_offset: float = 0.0) -> float | None:
        """Map a PCM-leg media position onto the packaging PDT timeline."""
        pdt0 = self._pdt_zero()
        offset = self.offset
        if pdt0 is None or offset is None:
            return None
        return pdt0 + offset + pcm_offset

    def status(self) -> dict[str, Any]:
        offset = self.offset
        spread: float | None = None
        if len(self.samples) >= 4:
            quartiles = statistics.quantiles(self.samples, n=4)
            spread = quartiles[2] - quartiles[0]
        return {
            "c": round(offset, 3) if offset is not None else None,
            "frozen": self._frozen is not None,
            "samples": self.total_samples,
            "spread": round(spread, 3) if spread is not None else None,
            "drift": round(self._drift, 3) if self._drift is not None else None,
        }


class ThreadsafeTeeSink:
    """A nonblocking cross-thread sink feeding a bounded ``asyncio.Queue``.

    The producer only takes a short lock, appends bytes to a bounded staging
    deque, and schedules at most one event-loop callback.  Both staging and the
    asyncio queue drop their oldest item when full.  Exceptions never escape to
    the playback pump.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, max_chunks: int = 64) -> None:
        if max_chunks < 1:
            raise ValueError("max_chunks must be positive")
        self.loop = loop
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=max_chunks)
        self._pending: deque[bytes] = deque()
        self._max_chunks = max_chunks
        self._lock = threading.Lock()
        self._scheduled = False
        self._closed = False
        self.dropped = 0

    def __call__(self, chunk: bytes) -> None:
        try:
            if not chunk:
                return
            with self._lock:
                if self._closed:
                    self.dropped += 1
                    return
                if len(self._pending) >= self._max_chunks:
                    self._pending.popleft()
                    self.dropped += 1
                self._pending.append(bytes(chunk))
                if self._scheduled:
                    return
                self._scheduled = True
            self.loop.call_soon_threadsafe(self._drain)
        except Exception:
            # A subtitle failure must never surface in the playback thread.
            with self._lock:
                self.dropped += 1
                self._scheduled = False

    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    self._scheduled = False
                    return
                chunk = self._pending.popleft()
            if self.queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    self.queue.get_nowait()
                    self.dropped += 1
            with contextlib.suppress(asyncio.QueueFull):
                self.queue.put_nowait(chunk)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._pending.clear()


@dataclasses.dataclass
class PipelineStats:
    suppressed_by_ingest_error: int = 0
    translation_dropped: int = 0
    translation_failures: int = 0
    translation_attempts: int = 0
    asr_reconnects: int = 0
    final_deduplicated: int = 0
    final_discarded: int = 0
    pcm_dropped: int = 0
    forced_commits: int = 0
    commit_failures: int = 0
    overlong_cues: int = 0
    source_only_cues: int = 0
    unmapped_dropped: int = 0
    unjoined_finals: int = 0
    prefix_cues: int = 0
    final_tails: int = 0
    final_absorbed: int = 0
    split_conflicts: int = 0
    prefix_rewrites: int = 0
    last_error: str | None = None
    last_translation_error: str | None = None
    last_translation_latency_ms: int | None = None
    avg_translation_latency_ms: float | None = None
    last_translation_attempt_at: float | None = None


@dataclasses.dataclass
class _PendingFinal:
    text: str
    begin_pcm: float | None
    end_pcm: float
    timing_source: TimingSource
    lang: str | None
    audio_end_wall: float


@dataclasses.dataclass
class _SplitState:
    """Prefix-split bookkeeping for one in-flight utterance (item id keyed).

    ``emitted`` accumulates the raw stable-prefix slices already emitted as
    cues; ``chain_end`` is the pipeline audio position used as the tEnd of the
    last emitted slice (and the tStart of the next). The provider's final is
    reconciled against ``emitted`` so only the un-emitted tail becomes a cue.
    """

    emitted: str = ""
    chain_end: float | None = None
    rewritten: bool = False


class SubtitlePipeline:
    """Convert a tee of MPEG-TS audio into source and translated subtitle cues."""

    def __init__(
        self,
        *,
        asr_provider: ASRProvider,
        cue_store: CueStore,
        meta: StreamMeta,
        translation_provider: TranslationProvider | None = None,
        fallback_translation_provider: TranslationProvider | None = None,
        glossary: list[tuple[str, str]] | None = None,
        hotwords: list[str] | None = None,
        asr_context: list[str] | None = None,
        pdt_epoch: Callable[[], float | None] | None = None,
        media_clock: MediaClock | None = None,
        ingest_status: Callable[[], Any] | None = None,
        ffmpeg_path: str = "ffmpeg",
        tee_max_chunks: int = 64,
        pcm_queue_chunks: int = 32,
        source_language: str | None = None,
        hold_minimum: float = 1.2,
        hold_seconds_per_char: float = 0.06,
        hold_maximum: float = 7.0,
        max_subtitle_chars: int = 80,
        overlong_chars: int = 40,
        silence_duration_ms: int = 400,
        max_utterance_seconds: float = 0.0,
        prefix_split_enabled: bool = True,
        prefix_split_after_seconds: float = 3.0,
        anchor_sample_interval: float = 1.0,
        anchor_freeze_samples: int = 20,
        translation_workers: int = 4,
        translation_timeout_seconds: float = 6.0,
        context_pairs: int = 10,
        context_seconds: float = 90.0,
        recovery_seconds: float = 30.0,
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        subprocess_factory: Callable[..., Any] = asyncio.create_subprocess_exec,
    ) -> None:
        if pcm_queue_chunks < 1:
            raise ValueError("pcm_queue_chunks must be positive")
        self.asr_provider = asr_provider
        self.translation_provider = translation_provider
        self.fallback_translation_provider = fallback_translation_provider
        self.store = cue_store
        self.meta = meta
        self.source_language = source_language or meta.source_lang
        self.glossary = list(glossary or [])[:30]
        self.hotwords = list(hotwords or [])
        self.asr_context = list(asr_context or [])
        self.ingest_status_callback = ingest_status
        self.ffmpeg_path = ffmpeg_path
        self.tee_max_chunks = tee_max_chunks
        self.pcm_queue_chunks = pcm_queue_chunks
        self.hold_minimum = hold_minimum
        self.hold_seconds_per_char = hold_seconds_per_char
        self.hold_maximum = hold_maximum
        self.max_subtitle_chars = max_subtitle_chars
        self.overlong_chars = overlong_chars
        self.silence_duration_seconds = max(0.0, silence_duration_ms / 1000.0)
        self.max_utterance_seconds = max(0.0, max_utterance_seconds)
        # Prefix split (redesign Fix F, P2 item): emit confirmed sub-sentences
        # from the realtime provider's stable prefix while a long utterance is
        # still being spoken. Measured on the 2026-08-31 countdown live
        # (JInec6ORhIk): 6 of 21 utterances ran >=9.2s (worst 30.2s) because the
        # MC never pauses 400ms; waiting for their finals meant the whole
        # sentence appeared only after it was over (RC-4). Splitting the
        # confirmed prefix delivered 32 sub-sentences an average 8.4s earlier.
        # Only Qwen's realtime protocol declares a confirmed monotonic prefix.
        # Task-ASR providers (Fun-ASR/Paraformer) emit mutable whole-sentence
        # hypotheses; splitting them as stable text would duplicate/rewrite cues.
        self.prefix_split_enabled = prefix_split_enabled and asr_provider.capabilities.stable_prefix
        self.prefix_split_after_seconds = max(0.0, prefix_split_after_seconds)
        self.translation_workers = max(1, int(translation_workers))
        self.translation_timeout_seconds = translation_timeout_seconds
        self.recovery_seconds = recovery_seconds
        self.wall_clock = wall_clock
        self.monotonic = monotonic
        self.subprocess_factory = subprocess_factory
        self.context = RollingContext(context_pairs, context_seconds)
        self.stats = PipelineStats()

        self.anchor = MediaAnchor(
            pdt_epoch=pdt_epoch or (lambda: None),
            media_clock=media_clock or (lambda: None),
            sample_interval=anchor_sample_interval,
            freeze_samples=anchor_freeze_samples,
            monotonic=monotonic,
        )

        self.tee_sink: ThreadsafeTeeSink | None = None
        self._pcm_queue: asyncio.Queue[tuple[bytes, float]] | None = None
        self._translation_queue: asyncio.Queue[Cue] = asyncio.Queue()
        self._tasks: list[asyncio.Task[Any]] = []
        self._process: Any = None
        self._stream: ASRStream | None = None
        self._running = False
        self._stopping = False
        self._pcm_offset = 0.0
        self._last_sent_pcm_offset = 0.0
        # Per-utterance VAD boundaries keyed by the provider's item id. A single
        # pair of "pending start/end" slots cannot work: the next utterance's
        # speech_started routinely arrives in the same millisecond as the
        # previous utterance's final, so the slots get clobbered before the
        # final that owns them is handled.
        self._vad_spans: "OrderedDict[str, dict[str, float]]" = OrderedDict()
        # Prefix-split state per utterance; reset alongside the VAD spans
        # whenever a new ASR session restarts the provider's audio clock.
        self._split_state: "OrderedDict[str, _SplitState]" = OrderedDict()
        # Breadcrumbs mapping the ASR session's audio clock onto our pcm clock;
        # see _server_to_pipeline. Reset whenever a new ASR session starts.
        self._push_breadcrumbs: deque[tuple[float, float]] = deque(maxlen=1200)
        self._stream_pushed_seconds = 0.0
        self._previous_final: str | None = None
        self._previous_final_end: float | None = None
        self._degrade_level = 0
        self._empty_since: float | None = None
        self._active_translation_provider_id: str | None = None
        self._manual_commit_ok = bool(getattr(self.asr_provider.capabilities, "manual_commit", False))
        self._last_forced_commit_at: float | None = None
        # Finals that arrived before the media anchor could map them.  They are
        # parked (bounded, oldest dropped) instead of being stamped with a
        # fabricated wall-clock time (redesign RC-1c).
        self._pending_finals: deque[_PendingFinal] = deque(maxlen=32)
        self._audio_end_walls: dict[int, float] = {}
        self._ready_lags: deque[float] = deque(maxlen=60)
        self._timing_source_counts = {"asr": 0, "vad": 0, "approx": 0}

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> Callable[[bytes], None]:
        """Start workers and return the playback-safe tee callback."""
        if self._running:
            assert self.tee_sink is not None
            return self.tee_sink
        self._stopping = False
        loop = asyncio.get_running_loop()
        self.tee_sink = ThreadsafeTeeSink(loop, self.tee_max_chunks)
        self._pcm_queue = asyncio.Queue(maxsize=self.pcm_queue_chunks)
        try:
            self._process = await self.subprocess_factory(
                self.ffmpeg_path,
                "-hide_banner", "-loglevel", "error", "-nostdin",
                "-f", "mpegts", "-i", "pipe:0", "-vn", "-ac", "1",
                "-ar", "16000", "-f", "s16le", "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._running = True
            self._tasks = [
                asyncio.create_task(self._ts_writer(), name="subtitle-ts-writer"),
                asyncio.create_task(self._pcm_reader(), name="subtitle-pcm-reader"),
                asyncio.create_task(self._pcm_sender(), name="subtitle-pcm-sender"),
                asyncio.create_task(self._asr_manager(), name="subtitle-asr-manager"),
                asyncio.create_task(self._anchor_sampler(), name="subtitle-anchor-sampler"),
            ]
            if self.translation_provider is not None:
                # Several workers, not one: a single sequential consumer cannot
                # keep up. Measured on real audio, 60s of speech yields ~23
                # sentences and each translation call costs seconds, so a lone
                # worker falls permanently behind and the queue cap then
                # discards cues. Cue display order comes from tStart in the
                # player, so out-of-order completion is harmless.
                self._tasks.extend(
                    asyncio.create_task(self._translation_worker(), name=f"subtitle-translation-{index}")
                    for index in range(self.translation_workers)
                )
        except Exception as exc:
            self.stats.last_error = self._error_text(exc)
            await self.stop()
            raise
        return self.tee_sink

    async def stop(self) -> None:
        """Idempotently stop all workers without propagating provider/process errors."""
        if self._stopping:
            return
        self._stopping = True
        self._running = False
        if self.tee_sink is not None:
            self.tee_sink.close()
        stream, self._stream = self._stream, None
        if stream is not None:
            with contextlib.suppress(Exception):
                await stream.flush()
            with contextlib.suppress(Exception):
                await stream.aclose()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        process, self._process = self._process, None
        if process is not None:
            with contextlib.suppress(Exception):
                if process.stdin:
                    process.stdin.close()
                    wait_closed = getattr(process.stdin, "wait_closed", None)
                    if wait_closed:
                        await wait_closed()
            if getattr(process, "returncode", None) is None:
                with contextlib.suppress(Exception):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 1.0)
                except Exception:
                    with contextlib.suppress(Exception):
                        process.kill()
                    with contextlib.suppress(Exception):
                        await process.wait()
        self._stopping = False

    async def _anchor_sampler(self) -> None:
        """Sample the media anchor and release finals parked during warm-up."""
        try:
            while self._running:
                self.anchor.maybe_sample(self._pcm_offset)
                # Finals may all arrive during a startup burst before the first
                # usable anchor sample. Once the anchor becomes ready, release
                # them even if no later ASR final arrives to trigger a flush.
                if self._pending_finals and self.anchor.epoch() is not None:
                    self._flush_pending_finals()
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            raise

    async def _ts_writer(self) -> None:
        assert self.tee_sink is not None and self._process is not None
        try:
            while self._running:
                chunk = await self.tee_sink.queue.get()
                self._process.stdin.write(chunk)
                await self._process.stdin.drain()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_error(exc)

    def _ingest_pcm_chunk(self, chunk: bytes, chunk_start: float) -> None:
        """Advance the PCM clock and dispatch one complete chunk."""
        self._pcm_offset = max(
            self._pcm_offset,
            chunk_start + len(chunk) / PCM_BYTES_PER_SECOND,
        )
        queue = self._pcm_queue
        if queue is None:
            return
        item = (chunk, chunk_start)
        if queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
            self.stats.pcm_dropped += 1
        queue.put_nowait(item)

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
                self._ingest_pcm_chunk(chunk, self._pcm_offset)
            if buffer:
                self._pcm_offset += len(buffer) / PCM_BYTES_PER_SECOND
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_error(exc)

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

    async def _pcm_sender(self) -> None:
        assert self._pcm_queue is not None
        while self._running:
            chunk, offset = await self._pcm_queue.get()
            self._last_sent_pcm_offset = offset + len(chunk) / PCM_BYTES_PER_SECOND
            stream = self._stream
            if stream is None:
                continue
            self._push_breadcrumbs.append((self._stream_pushed_seconds, offset))
            self._stream_pushed_seconds += len(chunk) / PCM_BYTES_PER_SECOND
            try:
                await stream.push_pcm(chunk, offset)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_error(exc)
                if self._stream is stream:
                    self._stream = None
                with contextlib.suppress(Exception):
                    await stream.aclose()
                continue
            await self._maybe_force_commit(stream)

    def _open_utterance_start(self) -> float | None:
        """Start offset of the utterance the provider is still accumulating."""
        for span in reversed(self._vad_spans.values()):
            if "start" in span and "end" not in span:
                return span["start"]
        return None

    async def _maybe_force_commit(self, stream: ASRStream) -> None:
        """Hard-cap utterance length. Disabled by default (max_utterance_seconds=0).

        Measured on real audio, forcing a commit mid-speech cuts inside words
        (producing fragments like a lone "お。"), and the final it triggers
        arrives with no preceding speech_stopped, so the cue loses its end
        boundary. Server VAD at 400ms already segments into 1.6s median units,
        which is why this is off unless explicitly configured.
        """
        if not self._manual_commit_ok or self.max_utterance_seconds <= 0:
            return
        open_start = self._open_utterance_start()
        if open_start is None:
            return
        if self._last_sent_pcm_offset - open_start <= self.max_utterance_seconds:
            return
        now = self.monotonic()
        cooldown = self.max_utterance_seconds / 2.0
        if self._last_forced_commit_at is not None and now - self._last_forced_commit_at < cooldown:
            return
        self._last_forced_commit_at = now
        try:
            await stream.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Provider does not accept manual commits under server_vad (known
            # open question, redesign Fix J): degrade to VAD-only segmentation.
            self._manual_commit_ok = False
            self.stats.commit_failures += 1
            self._record_error(exc)
        else:
            self.stats.forced_commits += 1

    async def _asr_manager(self) -> None:
        backoff = 0.5
        while self._running:
            if self._stream is not None:
                await asyncio.sleep(0.05)
                continue
            try:
                stream = await self.asr_provider.stream(
                    language=self.source_language,
                    hotwords=self.hotwords,
                    context=self.asr_context,
                )
                if not self._running:
                    await stream.aclose()
                    return
                # A fresh session restarts the provider's audio clock at zero,
                # so the old breadcrumbs and any half-open VAD spans no longer
                # refer to anything.
                self._push_breadcrumbs.clear()
                self._stream_pushed_seconds = 0.0
                self._vad_spans.clear()
                self._split_state.clear()
                self._stream = stream
                backoff = 0.5
                await self._consume_asr_events(stream)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_error(exc)
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
            if stream is not self._stream or not self._running:
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
        if event.type == "speech_started":
            # The provider reports the true onset offset; no lag compensation.
            # Estimating this from _last_sent_pcm_offset was measured wrong by
            # ~0.03-0.17s at the start and, worse, systematically early at the
            # end (see speech_stopped below).
            start = self._server_to_pipeline(event.begin_pcm)
            if start is not None:
                self._span_for(event.item_id)["start"] = max(0.0, start)
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
        elif event.type == "interim":
            self._handle_interim(event)
        elif event.type == "final":
            await self._handle_final(event)

    def _split_state_for(self, item_id: str) -> _SplitState:
        """Get (creating if needed) the prefix-split state for an utterance."""
        state = self._split_state.get(item_id)
        if state is None:
            state = _SplitState()
            self._split_state[item_id] = state
            while len(self._split_state) > 64:
                self._split_state.popitem(last=False)
        return state

    def _handle_interim(self, event: ASREvent) -> None:
        """Emit confirmed sub-sentences while a long utterance is still open.

        The provider's stable prefix (``text``) is confirmed wording that grows
        monotonically during the utterance; the ``stash`` tail may be rewritten
        and is never emitted here. Timing chains off the VAD span start:
        the first slice's tStart is the exact speech onset, every slice's tEnd
        is the audio position at the moment its closing boundary was confirmed
        (measured event lag on real audio: 0.04-0.10s), and the next slice
        starts where the previous ended. Errors do not accumulate because each
        tEnd re-anchors to the audio clock instead of adding a guessed
        duration; the final's exact speech_stopped endpoint later closes the
        last gap (the tail cue).
        """
        if not self.prefix_split_enabled:
            return
        item = event.item_id
        text = event.text or ""
        if not item or not text:
            return
        span = self._vad_spans.get(item)
        if span is None or "start" not in span:
            # No audio anchor to chain from (missed speech_started): keep the
            # utterance final-only rather than fabricate a chain origin.
            return
        state = self._split_state_for(item)
        if state.rewritten or (state.emitted and not text.startswith(state.emitted)):
            # The "stable" prefix rewrote text we already emitted. Stop
            # splitting this utterance; the final reconciles as best it can.
            if not state.rewritten:
                state.rewritten = True
                self.stats.prefix_rewrites += 1
            return
        newly = newly_confirmed_sentences(text, state.emitted)
        if newly is None:
            return
        confirm_position = self._last_sent_pcm_offset
        begin = state.chain_end if state.chain_end is not None else span["start"]
        if state.chain_end is None and confirm_position - span["start"] < self.prefix_split_after_seconds:
            # Utterances shorter than the cap keep today's final-only
            # behaviour; on real audio the prefix of a short utterance is
            # confirmed at/after the final anyway (measured gain: 0.00s).
            return
        end = max(begin, confirm_position)
        cleaned = clean_subtitle_text(newly)
        state.emitted += newly
        state.chain_end = end
        if cleaned is None:
            self.stats.final_discarded += 1
            return
        if self._ingest_is_unhealthy():
            self.stats.suppressed_by_ingest_error += 1
            return
        pending = _PendingFinal(
            text=cleaned,
            begin_pcm=begin,
            end_pcm=end,
            timing_source="vad",
            lang=event.language,
            audio_end_wall=self.wall_clock() - max(0.0, self._pcm_offset - end),
        )
        if len(self._pending_finals) == self._pending_finals.maxlen:
            self._pending_finals.popleft()
            self.stats.unmapped_dropped += 1
        self._pending_finals.append(pending)
        self.stats.prefix_cues += 1
        self._flush_pending_finals()

    async def _handle_final(self, event: ASREvent) -> list[Cue]:
        cleaned = clean_subtitle_text(event.text)
        if cleaned is None:
            self.stats.final_discarded += 1
            return []
        # Only treat a repeat as a resend when it follows closely. At 400ms
        # segmentation a speaker genuinely repeating a short interjection
        # ("うん。" twice in a row) is common, and dropping the second one
        # silently deletes real speech.
        near_in_time = (
            self._previous_final_end is not None
            and self._last_sent_pcm_offset - self._previous_final_end < 1.0
        )
        if near_in_time and is_duplicate_final(cleaned, self._previous_final):
            self.stats.final_deduplicated += 1
            return []
        self._previous_final = cleaned
        self._previous_final_end = self._last_sent_pcm_offset
        if self._ingest_is_unhealthy():
            self.stats.suppressed_by_ingest_error += 1
            return []

        # Join this transcript to its own VAD boundaries by the provider's item
        # id, so interleaved events cannot mispair them.
        span = self._vad_spans.pop(event.item_id or "", {})
        begin_pcm = span.get("start")
        end_pcm = span.get("end")
        if begin_pcm is not None and end_pcm is not None:
            timing_source = "asr"
        elif begin_pcm is not None:
            # Utterance ended without a speech_stopped (manual commit, or the
            # session closing). The start is still authoritative.
            end_pcm, timing_source = self._last_sent_pcm_offset, "approx"
        else:
            begin_pcm, end_pcm, timing_source = None, self._last_sent_pcm_offset, "approx"
            self.stats.unjoined_finals += 1
        end_pcm = max(end_pcm, begin_pcm if begin_pcm is not None else 0.0)

        # Reconcile against sub-sentences already emitted from the stable
        # prefix: the final must contribute only text that has not been shown.
        text_for_cue = cleaned
        state = self._split_state.pop(event.item_id or "", None) if event.item_id else None
        if state is not None and state.emitted:
            if event.text.startswith(state.emitted):
                remainder = clean_subtitle_text(event.text[len(state.emitted):])
                if remainder is None:
                    # Everything the final adds was already displayed as
                    # prefix cues; nothing left to schedule.
                    self.stats.final_absorbed += 1
                    return []
                text_for_cue = remainder
                if state.chain_end is not None:
                    begin_pcm = state.chain_end
                    end_pcm = max(end_pcm, begin_pcm)
                self.stats.final_tails += 1
            else:
                # The final disagrees with the confirmed prefix we already
                # emitted (contract violation upstream). Show the whole final
                # rather than lose content; count it for diagnosis.
                self.stats.split_conflicts += 1

        # Steady-state estimate of when this sentence's audio end entered the
        # pipeline; used for readyLag (redesign Fix E).
        audio_end_wall = self.wall_clock() - max(0.0, self._pcm_offset - max(end_pcm, 0.0))

        pending = _PendingFinal(
            text=text_for_cue,
            begin_pcm=begin_pcm,
            end_pcm=end_pcm,
            timing_source=timing_source,
            lang=event.language,
            audio_end_wall=audio_end_wall,
        )
        if len(self._pending_finals) == self._pending_finals.maxlen:
            self._pending_finals.popleft()
            self.stats.unmapped_dropped += 1
        self._pending_finals.append(pending)
        return self._flush_pending_finals()

    def _flush_pending_finals(self) -> list[Cue]:
        """Materialize parked finals once the media anchor can map them."""
        cues: list[Cue] = []
        while self._pending_finals:
            epoch = self.anchor.epoch()
            if epoch is None:
                return cues
            pending = self._pending_finals.popleft()
            cues.append(self._materialize_cue(pending, epoch))
        return cues

    def _materialize_cue(self, pending: _PendingFinal, epoch: float) -> Cue:
        # One ASR final = exactly one cue (Fix G). Splitting is a rendering
        # concern (line wrapping in the player), never new schedule units.
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
        t_start = epoch + pending.begin_pcm if pending.begin_pcm is not None else None
        state = "src" if self.translation_provider is not None else "done"
        cue = self.store.add(
            t_start=t_start,
            t_end=epoch + pending.end_pcm,
            hold=hold,
            src=pending.text,
            state=state,
            lang=pending.lang or self.source_language,
            timing_source=pending.timing_source,
        )
        self._timing_source_counts[pending.timing_source] = self._timing_source_counts.get(pending.timing_source, 0) + 1
        self._audio_end_walls[cue.id] = pending.audio_end_wall
        if len(self._audio_end_walls) > 512:
            oldest = next(iter(self._audio_end_walls))
            self._audio_end_walls.pop(oldest, None)
        if self.translation_provider is not None:
            self._enqueue_translation(cue)
        else:
            self.stats.source_only_cues += 1
        return cue

    @property
    def _queue_limit(self) -> int:
        return max(16, 4 * self.translation_workers)

    def _enqueue_translation(self, cue: Cue) -> None:
        while self._translation_queue.qsize() >= self._queue_limit:
            try:
                dropped = self._translation_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            # It was never started; source-only is the correct durable state
            # and must be *displayable* (failed), not "still waiting".
            with contextlib.suppress(KeyError):
                self.store.update(dropped.id, state="failed")
            # get_nowait() bypasses the worker's task_done(), so balance the
            # unfinished-task counter here or join() would never settle.
            self._translation_queue.task_done()
            self.stats.translation_dropped += 1
            self.stats.source_only_cues += 1
        self._translation_queue.put_nowait(cue)
        # Degradation thresholds scale with concurrency: with N workers a
        # backlog of N is simply "all workers busy", not distress.
        backlog = self._translation_queue.qsize()
        if backlog > 2 * self.translation_workers:
            self._degrade_level = 2
        elif backlog > self.translation_workers:
            self._degrade_level = max(self._degrade_level, 1)
        self._empty_since = None

    async def _translation_worker(self) -> None:
        while self._running:
            try:
                cue = await asyncio.wait_for(self._translation_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                self._update_recovery(0)
                continue
            # Everything that can raise must live inside the try below. When
            # request construction sat outside it, any exception there killed
            # the worker task outright and silently: cues then piled up in
            # "src" forever and no status field showed why.
            try:
                backlog = self._translation_queue.qsize()
                self._update_recovery(backlog)
                provider = self.translation_provider
                if self._degrade_level >= 2 and self.fallback_translation_provider is not None:
                    provider = self.fallback_translation_provider
                if provider is None:
                    continue  # the finally below still runs task_done()
                pair_limit = 2 if self._degrade_level >= 1 else None
                history = self.context.history(self.wall_clock(), pair_limit=pair_limit)
                if not provider.capabilities.rolling_context:
                    history = []
                request = TranslationRequest(
                    source_text=cue.src,
                    meta=self.meta,
                    history=history,
                    glossary=self.glossary if provider.capabilities.glossary else [],
                    deadline_monotonic=self.monotonic() + self.translation_timeout_seconds,
                )
                with contextlib.suppress(KeyError):
                    self.store.update(cue.id, state="translating")
                self._active_translation_provider_id = provider.id
                self.stats.translation_attempts += 1
                self.stats.last_translation_attempt_at = self.wall_clock()
                result = await asyncio.wait_for(
                    provider.translate(request), timeout=max(0.001, self.translation_timeout_seconds)
                )
                translated = result.text.strip()
                if not translated:
                    raise ValueError("translation provider returned empty text")
                self._record_translation_latency(result.latency_ms)
                self.context.add(cue.src, translated, self.wall_clock())
                self.context.trim(self.wall_clock())
                with contextlib.suppress(KeyError):
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
                self._record_ready_lag(cue.id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Timeout or failure is a *terminal* source-only state; the
                # player shows the original text instead of "translating".
                self.stats.translation_failures += 1
                self.stats.last_translation_error = self._error_text(exc)
                self._record_error(exc)
                with contextlib.suppress(KeyError):
                    self.store.update(cue.id, state="failed")
                self.stats.source_only_cues += 1
                self._record_ready_lag(cue.id)
            finally:
                self._translation_queue.task_done()
                self._update_recovery(self._translation_queue.qsize())

    def _record_ready_lag(self, cue_id: int) -> None:
        """readyLag = translation-ready wall time − audio-end-entered time."""
        audio_end_wall = self._audio_end_walls.pop(cue_id, None)
        if audio_end_wall is None:
            return
        lag = max(0.0, self.wall_clock() - audio_end_wall)
        self._ready_lags.append(lag)

    def _ready_lag_percentiles(self) -> tuple[float | None, float | None]:
        if not self._ready_lags:
            return None, None
        ordered = sorted(self._ready_lags)
        def percentile(fraction: float) -> float:
            index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
            return ordered[index]
        return round(percentile(0.5), 3), round(percentile(0.95), 3)

    def _record_translation_latency(self, latency_ms: int) -> None:
        self.stats.last_translation_latency_ms = int(latency_ms)
        current = self.stats.avg_translation_latency_ms
        self.stats.avg_translation_latency_ms = (
            float(latency_ms) if current is None else round(0.7 * current + 0.3 * float(latency_ms), 1)
        )

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

    def status(self) -> dict[str, Any]:
        epoch = self.anchor.epoch()
        anchor_status = self.anchor.status()
        ready_p50, ready_p95 = self._ready_lag_percentiles()
        price = getattr(self.asr_provider, "price_per_second_cny", None)
        return {
            "running": self._running,
            "pdtEpoch": self.anchor._pdt_zero(),
            "mediaAnchorC": anchor_status["c"],
            "mediaAnchorFrozen": anchor_status["frozen"],
            "mediaAnchorSamples": anchor_status["samples"],
            "mediaAnchorSpread": anchor_status["spread"],
            "mediaAnchorDrift": anchor_status["drift"],
            "pcmOffset": round(self._pcm_offset, 3),
            "asrSeconds": round(self._pcm_offset, 3),
            "estimatedCostCny": round(self._pcm_offset * price, 6) if price is not None else None,
            "teeDropped": self.tee_sink.dropped if self.tee_sink else 0,
            "pcmDropped": self.stats.pcm_dropped,
            "forcedCommits": self.stats.forced_commits,
            "commitFailures": self.stats.commit_failures,
            "overlongCues": self.stats.overlong_cues,
            "sourceOnlyCues": self.stats.source_only_cues,
            "unmappedDropped": self.stats.unmapped_dropped,
            "unjoinedFinals": self.stats.unjoined_finals,
            "prefixCues": self.stats.prefix_cues,
            "finalTails": self.stats.final_tails,
            "finalAbsorbed": self.stats.final_absorbed,
            "splitConflicts": self.stats.split_conflicts,
            "prefixRewrites": self.stats.prefix_rewrites,
            "finalDiscarded": self.stats.final_discarded,
            "finalDeduplicated": self.stats.final_deduplicated,
            "timingSourceCounts": dict(self._timing_source_counts),
            "readyLagP50": ready_p50,
            "readyLagP95": ready_p95,
            "suppressedByIngestError": self.stats.suppressed_by_ingest_error,
            "translationBacklog": self._translation_queue.qsize(),
            "translationDropped": self.stats.translation_dropped,
            "translationFailures": self.stats.translation_failures,
            "translationAttempts": self.stats.translation_attempts,
            "translationWorkers": self.translation_workers,
            "translationWorkersAlive": sum(
                1 for task in self._tasks
                if task.get_name().startswith("subtitle-translation") and not task.done()
            ),
            "lastTranslationAttemptAt": self.stats.last_translation_attempt_at,
            "lastTranslationError": self.stats.last_translation_error,
            "lastTranslationLatencyMs": self.stats.last_translation_latency_ms,
            "avgTranslationLatencyMs": self.stats.avg_translation_latency_ms,
            "degradeLevel": self._degrade_level,
            "asrReconnects": self.stats.asr_reconnects,
            "asrProviderId": self.asr_provider.id,
            "translationProviderId": self._active_translation_provider_id or (
                self.translation_provider.id if self.translation_provider is not None else None
            ),
            "lastError": self.stats.last_error,
            "timelineEpoch": epoch,
        }

    def _record_error(self, exc: BaseException) -> None:
        self.stats.last_error = self._error_text(exc)

    @staticmethod
    def _error_text(exc: BaseException) -> str:
        message = str(exc).strip()
        return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
