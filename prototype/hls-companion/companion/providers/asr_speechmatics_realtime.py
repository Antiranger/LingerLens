"""Speechmatics Realtime v2 STT adapter (``speechmatics-realtime``).

Implements the official Realtime WebSocket v2 protocol with plain aiohttp.
Sources (read 2026-09, see docs/research/realtime-stt-provider-protocol-audit-2026.md):

* API reference: https://docs.speechmatics.com/api-ref/realtime-transcription-websocket

Wire summary:

* Handshake: ``Authorization: Bearer <key>`` against ``wss://<region>.rt.speechmatics.com/v2/``.
* Session start: text message ``StartRecognition`` with ``audio_format``
  (``{"type": "raw", "encoding": "pcm_s16le", "sample_rate": 16000}``) and
  ``transcription_config`` (``language`` REQUIRED and single-language,
  ``model`` -- the deprecated ``operating_point`` is never sent,
  ``enable_partials``, optional ``diarization``/``max_delay``).
* Audio: binary ``AddAudio`` frames; the server acks each with
  ``AudioAdded {seq_no}``.
* Results: ``RecognitionStarted`` (session confirmation), then
  ``AddPartialTranscript`` -> interim (partials carry no usable confidence)
  and ``AddTranscript`` -> final. ``metadata.start_time/end_time`` and word
  ``start_time/end_time`` are SECONDS. Word alternatives carry
  ``content``/``confidence``/``language``/``speaker`` (diarization on).
* Manual flush: ``{"message": "ForceEndOfUtterance"}``.
* End: client ``{"message": "EndOfStream", "last_seq_no": N}`` ->
  server ``{"message": "EndOfTranscript"}`` -> socket closes.
* Errors arrive as messages and the server closes; the pipeline owns the
  retry policy (official guidance: >=5-10s spacing for 4005/4013/1011).

The adapter owns exactly one session; the SubtitlePipeline remains the only
generic reconnect owner.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, AsyncIterator

import aiohttp

from . import register
from ..languages import LanguageNotSupportedError, canonicalize_tag_or_none
from .base import (
    ASRCapabilities,
    ASREvent,
    ASRLanguageCapabilities,
    ASRProvider,
    ASRStream,
    CaptionObservation,
    SourceLanguagePolicy,
)


@register("speechmatics-realtime")
class SpeechmaticsRealtimeASRProvider(ASRProvider):
    requires_api_key = True

    def __init__(self, config: dict[str, Any]):
        self.id = config["id"]
        self.label = config.get("label", self.id)
        self.model = config["model"]
        self.base_url = str(config["baseUrl"])
        self.api_key = config.get("_apiKey", config.get("apiKey", ""))
        self.options = config.get("options", {})
        self.price_per_second_cny = config.get("pricePerSecondCny")

    @property
    def capabilities(self) -> ASRCapabilities:
        if self.model in {"standard", "enhanced", "melia-1"}:
            # Single-language contract: transcription_config.language is
            # required and there is no detection/code-switching agreement.
            language = ASRLanguageCapabilities(
                supported_tags=None,  # official list is large; docs claim multi-language coverage
                detection="none",
                reports_detected_language=True,  # word alternatives carry a language tag
                code_switching=False,
                tier="provider_claimed",
            )
        else:
            language = ASRLanguageCapabilities()
        return ASRCapabilities(
            True,
            True,
            False,
            True,  # server utterance segmentation (AddTranscript boundaries)
            True,
            False,
            False,
            (),
            (16000,),
            manual_commit=True,  # ForceEndOfUtterance
            language=language,
            preferred_sample_rate=16000,
            speaker_labels=bool(self.options.get("diarization", False)),
            caption_evidence=frozenset({"text_snapshot", "utterance_final", "endpoint"}),
        )

    async def stream(self, *, policy: SourceLanguagePolicy, sample_rate: int, hotwords: list[str], context: list[str]) -> ASRStream:
        del hotwords, context
        if not self.api_key:
            raise ValueError(f"API key is not configured for provider {self.id}")
        if sample_rate != 16000:
            raise ValueError(f"sample rate {sample_rate} is not supported by provider {self.id} (pcm_s16le requires 16000)")
        if policy.mode != "specified" or not policy.tag:
            raise LanguageNotSupportedError("Speechmatics realtime requires a specified single language")
        stream = _SpeechmaticsStream(self, policy, sample_rate)
        await stream.connect()
        return stream


class _SpeechmaticsStream(ASRStream):
    def __init__(self, provider: SpeechmaticsRealtimeASRProvider, policy: SourceLanguagePolicy, sample_rate: int):
        self.provider = provider
        self.policy = policy
        self.sample_rate = sample_rate
        self.session: aiohttp.ClientSession | None = None
        self.ws: Any = None
        self.closed = False
        self._seq_no = 0
        self._iter_started = False
        self._drain_done = asyncio.Event()
        self._release_done = False
        self.drain_seconds = float(provider.options.get("closeDrainTimeoutSeconds", 2.0))
        self._utterance = 0
        self._current_item: str | None = None
        self._started_items: set[str] = set()

    def _start_recognition(self) -> dict[str, Any]:
        options = self.provider.options
        assert self.policy.tag is not None
        config: dict[str, Any] = {
            "language": canonicalize_tag_or_none(self.policy.tag) or self.policy.tag,
            "model": self.provider.model,  # current field (operating_point deprecated)
            "enable_partials": bool(options.get("enablePartials", True)),
        }
        if options.get("diarization"):
            config["diarization"] = "speaker"
            max_speakers = int(options.get("maxSpeakers", 6))
            if max_speakers >= 2:
                config["speaker_diarization_config"] = {"max_speakers": max_speakers}
        if options.get("maxDelaySeconds") is not None:
            config["max_delay"] = max(0.7, min(4.0, float(options["maxDelaySeconds"])))
        if options.get("endOfUtteranceSilenceTriggerSecs") is not None:
            config["conversation_config"] = {
                "end_of_utterance_silence_trigger": max(0.0, min(2.0, float(options["endOfUtteranceSilenceTriggerSecs"])))
            }
        return {
            "message": "StartRecognition",
            "audio_format": {"type": "raw", "encoding": "pcm_s16le", "sample_rate": self.sample_rate},
            "transcription_config": config,
        }

    async def connect(self) -> None:
        self.session = aiohttp.ClientSession()
        try:
            self.ws = await self.session.ws_connect(
                self.provider.base_url,
                headers={"Authorization": f"Bearer {self.provider.api_key}"},
            )
            await self.ws.send_json(self._start_recognition())
        except BaseException:
            await self._release()
            raise

    async def push_pcm(self, chunk: bytes, pcm_offset: float) -> None:
        del pcm_offset
        if not self.ws or self.ws.closed:
            return
        self._seq_no += 1
        await self.ws.send_bytes(chunk)

    async def flush(self) -> None:
        if self.ws and not self.ws.closed:
            await self.ws.send_json({"message": "ForceEndOfUtterance"})

    async def commit(self) -> None:
        await self.flush()

    def __aiter__(self) -> AsyncIterator[ASREvent]:
        return self._events()

    async def _events(self) -> AsyncIterator[ASREvent]:
        self._iter_started = True
        try:
            if not self.ws:
                return
            async for message in self.ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    try:
                        raw = json.loads(message.data)
                    except json.JSONDecodeError as exc:
                        yield ASREvent("error", message=str(exc))
                        continue
                    for event in self._map_event(raw):
                        yield event
                    if raw.get("message") == "EndOfTranscript":
                        return
                elif message.type == aiohttp.WSMsgType.ERROR:
                    yield ASREvent("error", message=str(self.ws.exception()))
                    return
        finally:
            self._drain_done.set()

    def _map_event(self, raw: dict[str, Any]) -> list[ASREvent]:
        message = raw.get("message")
        if message == "Error":
            code = raw.get("code")
            reason = raw.get("reason") or raw.get("message") or ""
            return [ASREvent("error", message=f"{code}: {reason}".rstrip(": "), raw=raw)]
        if message in ("RecognitionStarted", "AudioAdded", "Warning", "EndOfTranscript", "SetRecognitionConfigAck"):
            # Session lifecycle/acks carry no transcript content.
            return []
        if message not in ("AddPartialTranscript", "AddTranscript"):
            return []
        metadata = raw.get("metadata") or {}
        start = _float_or_none(metadata.get("start_time"))
        end = _float_or_none(metadata.get("end_time"))
        transcript = str(metadata.get("transcript", "")).strip()
        words = self._words(raw)
        if message == "AddPartialTranscript":
            item_id = self._current_item
            if item_id is None:
                self._utterance += 1
                item_id = str(self._utterance)
                self._current_item = item_id
            events: list[ASREvent] = []
            if item_id not in self._started_items and (start is not None or transcript):
                self._started_items.add(item_id)
                events.append(ASREvent("speech_started", begin_pcm=start, item_id=item_id, raw=raw))
            if transcript:
                events.append(ASREvent(
                    "interim", text=transcript, begin_pcm=start, end_pcm=end,
                    language=self._dominant_language(words), speaker=self._dominant_speaker(words),
                    # Partial confidence is meaningless upstream: never set.
                    item_id=item_id, raw=raw,
                    caption_observation=CaptionObservation(
                        "text_snapshot", 0, item_id, tentative_text=transcript,
                        begin_pcm=start, end_pcm=end,
                    ),
                ))
            return events

        # AddTranscript: this segment's terminal state. A final CLOSES the
        # utterance the partials opened (same item id); only a final without
        # preceding partials opens a new one.
        if self._current_item is not None:
            item_id = self._current_item
        else:
            self._utterance += 1
            item_id = str(self._utterance)
        self._current_item = None
        events = []
        if item_id not in self._started_items and start is not None:
            self._started_items.add(item_id)
            events.append(ASREvent("speech_started", begin_pcm=start, item_id=item_id, raw=raw))
        if end is not None:
            events.append(ASREvent(
                "speech_stopped", end_pcm=end, item_id=item_id, raw=raw,
                caption_observation=CaptionObservation("endpoint", 0, item_id, end_pcm=end),
            ))
        if transcript:
            events.append(ASREvent(
                "final", text=transcript, begin_pcm=start, end_pcm=end,
                language=self._dominant_language(words), speaker=self._dominant_speaker(words),
                item_id=item_id, raw=raw,
                caption_observation=CaptionObservation(
                    "utterance_final", 0, item_id, stable_text=transcript,
                    begin_pcm=start, end_pcm=end,
                ),
            ))
        return events

    @staticmethod
    def _words(raw: dict[str, Any]) -> list[dict[str, Any]]:
        words: list[dict[str, Any]] = []
        for result in raw.get("results") or []:
            alternatives = result.get("alternatives") or []
            if alternatives:
                words.append(alternatives[0])
        return words

    @staticmethod
    def _dominant_language(alternatives: list[dict[str, Any]]) -> str | None:
        counts: dict[str, int] = {}
        for alternative in alternatives:
            tag = canonicalize_tag_or_none(alternative.get("language"))
            if tag:
                counts[tag] = counts.get(tag, 0) + 1
        return max(counts, key=lambda tag: counts[tag]) if counts else None

    @staticmethod
    def _dominant_speaker(alternatives: list[dict[str, Any]]) -> str | None:
        counts: dict[str, int] = {}
        for alternative in alternatives:
            speaker = alternative.get("speaker")
            if speaker is None:
                continue
            key = str(speaker)
            counts[key] = counts.get(key, 0) + 1
        return max(counts, key=lambda key: counts[key]) if counts else None

    async def _release(self) -> None:
        if self._release_done:
            return
        self._release_done = True
        if self.ws is not None:
            with contextlib.suppress(aiohttp.ClientError, RuntimeError):
                await self.ws.close()
        if self.session is not None:
            with contextlib.suppress(Exception):
                await self.session.close()

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.ws and not self.ws.closed:
            # Official end: EndOfStream -> tail transcripts -> EndOfTranscript.
            with contextlib.suppress(aiohttp.ClientError, RuntimeError):
                await self.ws.send_json({"message": "EndOfStream", "last_seq_no": self._seq_no})
        if self._iter_started and not self._drain_done.is_set():
            try:
                await asyncio.wait_for(self._drain_done.wait(), timeout=self.drain_seconds)
            except asyncio.TimeoutError:
                pass
        await self._release()


def _float_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None
