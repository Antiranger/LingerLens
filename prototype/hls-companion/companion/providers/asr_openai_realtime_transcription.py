"""OpenAI Realtime Transcription adapter (``openai-realtime-transcription``).

Implements the CURRENT OpenAI Realtime transcription-session protocol over a
plain ``aiohttp`` WebSocket (no official SDK dependency). Source (read 2026-12):
https://developers.openai.com/api/docs/guides/realtime-transcription (official
Realtime transcription guide).

Wire summary (current schema, NOT the legacy realtime-preview payloads):

* Handshake: ``GET /v1/realtime`` with ``Authorization: Bearer <key>``.
* Session setup: one ``session.update`` with
  ``session.type == "transcription"`` and
  ``session.audio.input == {format: {type: "audio/pcm", rate: 24000},
  transcription: {model, ...}, turn_detection}``.
* Audio: JSON ``input_audio_buffer.append`` with base64 24 kHz PCM16.
* Turn control: server VAD via ``turn_detection``; manual
  ``input_audio_buffer.commit`` for forced utterance breaks and flush.
* Server events used: ``input_audio_buffer.speech_started``/``speech_stopped``
  (with ``item_id`` and millisecond audio offsets),
  ``conversation.item.input_audio_transcription.delta`` and ``.completed``.
  ``item_id`` correlates transcription events across speech turns; event
  ordering between turns is NOT guaranteed by OpenAI.

Model presets (per the official guide):

* ``gpt-live-transcribe``: low-latency incremental deltas; accepts ``languages``
  candidate hints but never returns detected-language predictions.
* ``gpt-transcribe``: committed-turn workflow; ``completed`` events may carry
  detected ``languages: [{code}]``. It never gets ``languages`` hints.

Neither model returns word-level timestamps, speaker labels, or confidence;
the adapter never fabricates them (timing falls back to server VAD events).

The adapter owns exactly one session and keepalive/finalize/close; the
SubtitlePipeline remains the only generic reconnect owner.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from typing import Any, AsyncIterator

import aiohttp

from . import register
from ..languages import canonicalize_tag_or_none, primary_subtag
from .base import (
    ASRCapabilities,
    ASREvent,
    ASRLanguageCapabilities,
    ASRProvider,
    ASRStream,
    CaptionObservation,
    SourceLanguagePolicy,
)

# Model-level capability presets (official guide, see module docstring).
# Unknown custom models stay experimental and claim no detection support.
_MODEL_PRESETS: dict[str, dict[str, Any]] = {
    "gpt-live-transcribe": {
        "tier": "provider_claimed",
        "reports_detected_language": False,
        "language_hints": True,
    },
    "gpt-transcribe": {
        "tier": "provider_claimed",
        "reports_detected_language": True,
        "language_hints": False,
    },
}


@register("openai-realtime-transcription")
class OpenAIRealtimeTranscriptionASRProvider(ASRProvider):
    requires_api_key = True

    def __init__(self, config: dict[str, Any]):
        self.id = config["id"]
        self.label = config.get("label", self.id)
        self.model = config["model"]
        self.base_url = str(config["baseUrl"]).rstrip("/")
        self.api_key = config.get("_apiKey", config.get("apiKey", ""))
        self.options = config.get("options", {})
        self.price_per_second_cny = config.get("pricePerSecondCny")

    @property
    def _preset(self) -> dict[str, Any]:
        return _MODEL_PRESETS.get(self.model, {})

    @property
    def capabilities(self) -> ASRCapabilities:
        preset = self._preset
        return ASRCapabilities(
            True,
            True,  # interim deltas
            False,  # deltas are incremental, not a confirmed stable prefix
            True,  # server VAD
            False,  # the official contract returns no word timestamps
            False,
            False,
            (),
            (24000,),
            True,  # input_audio_buffer.commit is honored
            language=ASRLanguageCapabilities(
                supported_tags=None,  # full language list is not published
                detection="unrestricted" if preset else "none",
                reports_detected_language=bool(preset.get("reports_detected_language", False)),
                tier=preset.get("tier", "experimental"),
            ),
            preferred_sample_rate=24000,
            caption_evidence=frozenset({"text_snapshot", "utterance_final", "endpoint"}),
        )

    async def stream(self, *, policy: SourceLanguagePolicy, sample_rate: int, hotwords: list[str], context: list[str]) -> ASRStream:
        del hotwords, context
        if not self.api_key:
            raise ValueError(f"API key is not configured for provider {self.id}")
        if sample_rate != 24000:
            raise ValueError(f"sample rate {sample_rate} is not supported by provider {self.id} (audio/pcm requires 24000)")
        stream = _OpenAIRealtimeStream(self, policy, sample_rate)
        await stream.connect()
        return stream


class _OpenAIRealtimeStream(ASRStream):
    def __init__(self, provider: OpenAIRealtimeTranscriptionASRProvider, policy: SourceLanguagePolicy, sample_rate: int):
        self.provider = provider
        self.policy = policy
        self.sample_rate = sample_rate
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.closed = False
        self._deltas: dict[str, str] = {}

    def _session_update(self) -> dict[str, Any]:
        options = self.provider.options
        transcription: dict[str, Any] = {"model": self.provider.model}
        if self.provider._preset.get("language_hints"):
            # gpt-live-transcribe takes `languages` candidate hints (never the
            # singular `language` field); gpt-transcribe is detection-first.
            languages = self._language_hints()
            if languages:
                transcription["languages"] = languages
            if options.get("delay"):
                transcription["delay"] = str(options["delay"])
        turn = self._turn_detection()
        return {
            "type": "session.update",
            "session": {
                "type": "transcription",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": self.sample_rate},
                        "transcription": transcription,
                        "turn_detection": turn,
                    }
                },
            },
        }

    def _language_hints(self) -> list[str]:
        if self.policy.mode == "specified" and self.policy.tag:
            return [primary_subtag(self.policy.tag)]
        return [primary_subtag(tag) for tag in self.policy.candidates]

    def _turn_detection(self) -> dict[str, Any] | None:
        turn = self.provider.options.get("turnDetection", {"type": "server_vad"})
        if turn is None:
            return None
        return {
            "type": "server_vad",
            "threshold": float(turn.get("threshold", 0.5)),
            "prefix_padding_ms": int(turn.get("prefixPaddingMs", 300)),
            "silence_duration_ms": int(turn.get("silenceDurationMs", 500)),
        }

    async def connect(self) -> None:
        self.session = aiohttp.ClientSession()
        try:
            self.ws = await self.session.ws_connect(
                self.provider.base_url,
                headers={"Authorization": f"Bearer {self.provider.api_key}"},
            )
            await self.ws.send_json(self._session_update())
        except BaseException:
            # Any handshake or session-setup failure (bad key/401, network
            # error, or a pipeline cancellation racing the connect) must not
            # leak the ClientSession or a half-open WebSocket.
            await self.aclose()
            raise

    async def push_pcm(self, chunk: bytes, pcm_offset: float) -> None:
        del pcm_offset
        if not self.ws or self.ws.closed:
            return
        await self.ws.send_json({
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(chunk).decode("ascii"),
        })

    async def flush(self) -> None:
        if self.ws and not self.ws.closed:
            with contextlib.suppress(aiohttp.ClientError, RuntimeError):
                await self.ws.send_json({"type": "input_audio_buffer.commit"})

    async def commit(self) -> None:
        """Manual mid-speech utterance break (hard cap, redesign Fix F)."""
        if self.ws and not self.ws.closed:
            await self.ws.send_json({"type": "input_audio_buffer.commit"})

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
                break

    def _map_event(self, raw: dict[str, Any]) -> ASREvent | None:
        """Map a current-schema realtime event onto the neutral event model.

        Detected languages are canonicalized here at the Adapter edge. Word
        timestamps are never fabricated: this protocol reports none.
        """
        event_type = raw.get("type", "")
        item_id = raw.get("item_id")
        if event_type == "input_audio_buffer.speech_started":
            return ASREvent("speech_started", begin_pcm=_milliseconds(raw.get("audio_start_ms")), item_id=item_id, raw=raw)
        if event_type == "input_audio_buffer.speech_stopped":
            end = _milliseconds(raw.get("audio_end_ms"))
            return ASREvent(
                "speech_stopped", end_pcm=end, item_id=item_id, raw=raw,
                caption_observation=CaptionObservation("endpoint", 0, str(item_id or "0"), end_pcm=end),
            )
        if event_type == "conversation.item.input_audio_transcription.delta":
            if item_id is not None:
                self._deltas[item_id] = self._deltas.get(item_id, "") + str(raw.get("delta", ""))
            accumulated = self._deltas.get(item_id or "", "")
            return ASREvent(
                "interim",
                text=accumulated,
                item_id=item_id,
                raw=raw,
                caption_observation=CaptionObservation(
                    "text_snapshot", 0, str(item_id or "0"), tentative_text=accumulated,
                ),
            )
        if event_type == "conversation.item.input_audio_transcription.completed":
            self._deltas.pop(item_id or "", None)
            language = self._detected_language(raw)
            usage = raw.get("usage")
            transcript = str(raw.get("transcript", ""))
            return ASREvent(
                "final",
                text=transcript,
                language=language,
                item_id=item_id,
                raw=raw if not isinstance(usage, dict) else {**raw, "usage": usage},
                caption_observation=CaptionObservation(
                    "utterance_final", 0, str(item_id or "0"), stable_text=transcript,
                ),
            )
        if event_type in {"error", "conversation.item.input_audio_transcription.failed"}:
            error = raw.get("error", raw)
            return ASREvent("error", message=error.get("message", str(error)) if isinstance(error, dict) else str(error), raw=raw)
        # session.updated / session.created and other session lifecycle events
        # carry no subtitle content.
        return None

    @staticmethod
    def _detected_language(raw: dict[str, Any]) -> str | None:
        languages = raw.get("languages")
        if not isinstance(languages, list) or not languages:
            return None
        first = languages[0]
        code = first.get("code") if isinstance(first, dict) else first
        return canonicalize_tag_or_none(code)

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.ws and not self.ws.closed:
            await self.ws.close()
        if self.session:
            await self.session.close()


def _milliseconds(value: Any) -> float | None:
    return float(value) / 1000.0 if isinstance(value, (int, float)) else None
