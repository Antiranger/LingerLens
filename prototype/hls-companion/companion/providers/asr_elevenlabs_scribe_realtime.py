"""ElevenLabs Scribe v2 Realtime STT adapter (``elevenlabs-scribe-realtime``).

Implements the official Scribe v2 Realtime WebSocket for
``wss://api.elevenlabs.io/v1/speech-to-text/realtime`` with plain aiohttp.
Sources (read 2026-09, see docs/research/realtime-stt-provider-protocol-audit-2026.md):

* API reference: https://elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime

Wire summary (note how differently this is shaped from the other providers):

* Configuration rides entirely in the handshake QUERY parameters
  (``model_id``, ``audio_format=pcm_16000``, ``language_code``,
  ``commit_strategy``, VAD thresholds, ``include_timestamps``,
  ``include_language_detection``, ...). There is NO first-frame session
  configuration message.
* Auth: ``xi-api-key`` header (a short-lived ``token`` query param is the
  browser-only alternative and is deliberately not used server-side).
* Audio: JSON text frames ``{"message_type": "input_audio_chunk",
  "audio_base_64": "<base64 pcm16>", "commit": true|false}``. With
  ``commit_strategy=manual`` a ``commit:true`` chunk is what forces the
  transcript commit; ``vad`` commits on server-detected silence.
* Server messages: ``session_started`` (echo, no transcript), then
  ``partial_transcript`` -> interim, ``committed_transcript`` -> final and
  (with include_timestamps) a delayed ``committed_transcript_with_timestamps``
  variant that may carry ``language_code``. Errors are TYPED events
  (``rate_limited``, ``quota_exceeded``, ``throttled``, ...), not wrapped.
* Close: a direct WebSocket close -- the protocol defines no close control
  frame, so the adapter never invents one.

The realtime text carries no word timestamps; the adapter never fabricates
timing (cues fall back to the pipeline's approximation), and word timing from
the delayed timestamps message stays in ``raw``.

The adapter owns exactly one session; the SubtitlePipeline remains the only
generic reconnect owner.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from collections import deque
from urllib.parse import urlencode
from typing import Any, AsyncIterator

import aiohttp

from .net_proxy import proxy_kwargs
from . import register
from ..languages import LanguageNotSupportedError, canonicalize_tag_or_none, primary_subtag
from .base import (
    ASRCapabilities,
    ASREvent,
    ASRLanguageCapabilities,
    ASRProvider,
    ASRStream,
    CaptionObservation,
    SourceLanguagePolicy,
)

# Typed error events (official reference lists these message_type values).
_ERROR_MESSAGE_TYPES = {
    "rate_limited",
    "quota_exceeded",
    "auth_error",
    "throttled",
    "queue_overflow",
    "resource_exhausted",
    "session_time_limit_exceeded",
    "input_error",
    "invalid_request",
    "chunk_size_exceeded",
    "insufficient_audio_activity",
    "transcriber_error",
}

_KNOWN_MESSAGE_TYPES = {
    "session_started",
    "partial_transcript",
    "committed_transcript",
    "committed_transcript_with_timestamps",
} | _ERROR_MESSAGE_TYPES

_AUDIO_FORMATS = {
    8000: "pcm_8000",
    16000: "pcm_16000",
    22050: "pcm_22050",
    24000: "pcm_24000",
    44100: "pcm_44100",
    48000: "pcm_48000",
}


@register("elevenlabs-scribe-realtime")
class ElevenLabsScribeRealtimeASRProvider(ASRProvider):
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
    def _commit_strategy(self) -> str:
        strategy = str(self.options.get("commitStrategy", "vad"))
        return strategy if strategy in ("manual", "vad") else "manual"

    @property
    def capabilities(self) -> ASRCapabilities:
        if self.model == "scribe_v2_realtime":
            language = ASRLanguageCapabilities(
                supported_tags=None,  # ISO-639-1/3 language_code, auto by default
                detection="unrestricted",
                reports_detected_language=bool(self.options.get("includeLanguageDetection", False)),
                code_switching=False,  # secondary_languages exists; no code-switching contract
                tier="provider_claimed",
            )
            languages: tuple[str, ...] = ()
        else:
            # Unknown custom models stay experimental and claim nothing.
            language = ASRLanguageCapabilities()
            languages = ()
        vad = self._commit_strategy == "vad"
        return ASRCapabilities(
            True,
            True,
            False,
            vad,
            # Realtime text carries no word timing (only the delayed
            # with_timestamps message does; its words stay in raw).
            False,
            False,
            False,
            languages,
            tuple(sorted(_AUDIO_FORMATS)),
            manual_commit=not vad,
            language=language,
            preferred_sample_rate=16000,
            # Diarization is undocumented for the realtime API: never claimed.
            speaker_labels=False,
            caption_evidence=frozenset({"text_snapshot", "utterance_final"}),
        )

    async def stream(self, *, policy: SourceLanguagePolicy, sample_rate: int, hotwords: list[str], context: list[str]) -> ASRStream:
        del hotwords, context
        if not self.api_key:
            raise ValueError(f"API key is not configured for provider {self.id}")
        if sample_rate not in _AUDIO_FORMATS:
            raise ValueError(f"sample rate {sample_rate} is not supported by provider {self.id}")
        if policy.mode == "detect" and self.capabilities.language.detection == "none":
            raise LanguageNotSupportedError("this model cannot auto-detect the source language")
        stream = _ElevenLabsScribeStream(self, policy, sample_rate)
        await stream.connect()
        return stream


class _ElevenLabsScribeStream(ASRStream):
    def __init__(self, provider: ElevenLabsScribeRealtimeASRProvider, policy: SourceLanguagePolicy, sample_rate: int):
        self.provider = provider
        self.policy = policy
        self.sample_rate = sample_rate
        self.session: aiohttp.ClientSession | None = None
        self.ws: Any = None
        self.closed = False
        self._next_item = 0
        self._pending_timestamp_copies = deque(maxlen=64)

    def _query(self) -> dict[str, str]:
        options = self.provider.options
        params: dict[str, str] = {
            "model_id": self.provider.model,
            "audio_format": _AUDIO_FORMATS[self.sample_rate],
            "commit_strategy": self.provider._commit_strategy,
        }
        if self.policy.mode == "specified" and self.policy.tag:
            params["language_code"] = primary_subtag(self.policy.tag)
        # detect: no language_code -- the provider auto-detects unless forced.
        if options.get("vadThreshold") is not None:
            params["vad_threshold"] = str(float(options["vadThreshold"]))
        if options.get("vadSilenceThresholdSecs") is not None:
            params["vad_silence_threshold_secs"] = str(float(options["vadSilenceThresholdSecs"]))
        if options.get("minSpeechDurationMs") is not None:
            params["min_speech_duration_ms"] = str(int(options["minSpeechDurationMs"]))
        if options.get("minSilenceDurationMs") is not None:
            params["min_silence_duration_ms"] = str(int(options["minSilenceDurationMs"]))
        if options.get("includeTimestamps"):
            params["include_timestamps"] = "true"
        if options.get("includeLanguageDetection"):
            params["include_language_detection"] = "true"
        if options.get("keyterms"):
            keyterms = options["keyterms"][:50]
            params["keyterms"] = ",".join(str(term) for term in keyterms)
        if options.get("noVerbatim"):
            params["no_verbatim"] = "true"
        if options.get("filterBackgroundAudio"):
            params["filter_background_audio"] = "true"
        return params

    async def connect(self) -> None:
        self.session = aiohttp.ClientSession()
        query = urlencode(self._query())
        try:
            self.ws = await self.session.ws_connect(
                f"{self.provider.base_url}?{query}",
                headers={"xi-api-key": self.provider.api_key},
                **proxy_kwargs(self.provider.base_url),
            )
        except BaseException:
            await self._release()
            raise

    async def push_pcm(self, chunk: bytes, pcm_offset: float) -> None:
        del pcm_offset
        if not self.ws or self.ws.closed:
            return
        await self._send_chunk(chunk, commit=False)

    async def flush(self) -> None:
        """Manual commit boundary: an (empty) audio chunk with commit=true."""
        if self.ws and not self.ws.closed:
            await self._send_chunk(b"", commit=True)

    async def commit(self) -> None:
        await self.flush()

    async def _send_chunk(self, chunk: bytes, *, commit: bool) -> None:
        assert self.ws is not None
        payload: dict[str, Any] = {
            "message_type": "input_audio_chunk",
            # Report §2.9 [verified]: the wire field is "audio_base_64".
            "audio_base_64": base64.b64encode(chunk).decode("ascii"),
        }
        if commit:
            payload["commit"] = True
        await self.ws.send_json(payload)

    def __aiter__(self) -> AsyncIterator[ASREvent]:
        return self._events()

    async def _events(self) -> AsyncIterator[ASREvent]:
        if not self.ws:
            return
        async for message in self.ws:
            if message.type == aiohttp.WSMsgType.TEXT:
                try:
                    raw = json.loads(message.data)
                except json.JSONDecodeError as exc:
                    yield ASREvent("error", message=str(exc))
                    continue
                event = self._map_event(raw)
                if event is not None:
                    yield event
            elif message.type == aiohttp.WSMsgType.ERROR:
                yield ASREvent("error", message=str(self.ws.exception()))
                return

    def _map_event(self, raw: dict[str, Any]) -> ASREvent | None:
        message_type = raw.get("message_type")
        if message_type == "partial_transcript":
            text = str(raw.get("text", ""))
            if not text:
                return None
            item_id = str(raw.get("commit_id") or f"scribe:{self._next_item}")
            return ASREvent(
                "interim", text=text, language=canonicalize_tag_or_none(raw.get("language_code")), raw=raw,
                item_id=item_id,
                caption_observation=CaptionObservation("text_snapshot", 0, item_id, tentative_text=text),
            )
        if message_type in ("committed_transcript", "committed_transcript_with_timestamps"):
            text = str(raw.get("text", "")).strip()
            if not text:
                return None
            # With include_timestamps=true, the service may send the ordinary
            # committed transcript and then a delayed timestamped copy of the
            # same commit. The second message enriches metadata; it must not
            # create a duplicate subtitle cue.
            if message_type == 'committed_transcript_with_timestamps':
                # Match one late metadata copy, not all equal spoken phrases.
                for pending in self._pending_timestamp_copies:
                    if pending[0] == text and (not raw.get('commit_id') or str(raw['commit_id']) == pending[1]):
                        self._pending_timestamp_copies.remove(pending)
                        return None
            item_id = str(raw.get('commit_id') or f'scribe:{self._next_item}')
            self._next_item += 1
            if message_type == 'committed_transcript':
                self._pending_timestamp_copies.append((text, item_id))
            return ASREvent(
                "final",
                text=text,
                language=canonicalize_tag_or_none(raw.get("language_code")),
                raw=raw,
                item_id=item_id,
                caption_observation=CaptionObservation("utterance_final", 0, item_id, stable_text=text),
            )
        if message_type == "session_started":
            # Configuration echo; carries no transcript content.
            return None
        if message_type in _ERROR_MESSAGE_TYPES:
            detail = raw.get("message") or raw.get("detail") or ""
            return ASREvent("error", message=f"{message_type}: {detail}".rstrip(": "), raw=raw)
        if message_type not in _KNOWN_MESSAGE_TYPES and "error" in str(message_type):
            # Forward-compat: unknown typed error events still surface.
            return ASREvent("error", message=str(message_type), raw=raw)
        return None

    async def _release(self) -> None:
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
        # The protocol defines no close control frame: a direct WebSocket
        # close is the official end of a session.
        await self._release()
