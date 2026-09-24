from __future__ import annotations

import asyncio
import base64
import json
import uuid
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

_LANGUAGES = ("zh", "yue", "en", "ja", "de", "ko", "ru", "fr", "pt", "ar", "it", "es", "hi", "id", "th", "tr", "uk", "vi")


def _canonical_tags(tags: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(tag for tag in (canonicalize_tag_or_none(item) for item in tags) if tag)


@register("dashscope-qwen-realtime")
class QwenRealtimeASRProvider(ASRProvider):
    requires_api_key = True

    def __init__(self, config: dict[str, Any]):
        self.id = config["id"]
        self.label = config.get("label", self.id)
        self.model = config["model"]
        self.base_url = config["baseUrl"]
        self.api_key = config.get("_apiKey", config.get("apiKey", ""))
        self.options = config.get("options", {})
        self.price_per_second_cny = config.get("pricePerSecondCny")

    @property
    def capabilities(self) -> ASRCapabilities:
        return ASRCapabilities(
            True, True, True, True, False, False, False, _LANGUAGES, (16000,), True,
            language=ASRLanguageCapabilities(
                supported_tags=_canonical_tags(_LANGUAGES),
                detection="none",
                reports_detected_language=True,
                tier="provider_claimed",
            ),
            preferred_sample_rate=16000,
            caption_evidence=frozenset({"stable_prefix_snapshot", "utterance_final", "endpoint"}),
        )

    async def stream(self, *, policy: SourceLanguagePolicy, sample_rate: int, hotwords: list[str], context: list[str]) -> ASRStream:
        del hotwords, context
        if not self.api_key:
            raise ValueError(f"API key is not configured for provider {self.id}")
        if policy.mode != "specified" or not policy.tag:
            raise LanguageNotSupportedError("Qwen realtime requires a specified source language")
        stream = _QwenRealtimeStream(self, primary_subtag(policy.tag), sample_rate)
        await stream.connect()
        return stream


class _QwenRealtimeStream(ASRStream):
    def __init__(self, provider: QwenRealtimeASRProvider, language: str, sample_rate: int):
        self.provider = provider
        self.language = language
        self.sample_rate = sample_rate
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.closed = False

    async def connect(self) -> None:
        # Everything that can fail lives inside this try. A failed handshake is
        # the common case in production -- _asr_manager retries forever with
        # backoff (subtitle_pipeline.py:713-756) -- and without this the
        # ClientSession and its TCPConnector were never closed: measured 40
        # failed handshakes -> 40 unclosed sessions and 40 unclosed connectors,
        # i.e. a file-descriptor leak proportional to reconnect count.
        try:
            self.session = aiohttp.ClientSession()
            url = f"{self.provider.base_url}?model={self.provider.model}"
            self.ws = await self.session.ws_connect(url, headers={"Authorization": f"Bearer {self.provider.api_key}"}, **proxy_kwargs(url))
            options = self.provider.options
            turn = options.get("turnDetection", {})
            await self.ws.send_json({
                "event_id": f"event_{uuid.uuid4().hex}",
                "type": "session.update",
                "session": {
                    "input_audio_format": "pcm",
                    "sample_rate": self.sample_rate,
                    "input_audio_transcription": {"language": self.language},
                    "turn_detection": {
                        "type": turn.get("type", "server_vad"),
                        "threshold": float(turn.get("threshold", 0.2)),
                        # 400ms: official fast-segmentation recommendation; a 600ms
                        # window merges continuous speech into 10-20s "sentences"
                        # that are useless as subtitle units (redesign Fix F).
                        "silence_duration_ms": int(turn.get("silenceDurationMs", 400)),
                    },
                },
            })
        except BaseException:
            await self.aclose()
            raise

    async def push_pcm(self, chunk: bytes, pcm_offset: float) -> None:
        del pcm_offset
        if not self.ws or self.ws.closed:
            return
        await self.ws.send_json({"type": "input_audio_buffer.append", "audio": base64.b64encode(chunk).decode("ascii")})

    async def flush(self) -> None:
        if self.ws and not self.ws.closed:
            await self.ws.send_json({"type": "input_audio_buffer.commit"})

    async def commit(self) -> None:
        """Manual mid-speech utterance break (hard cap, redesign Fix F)."""
        if self.ws and not self.ws.closed:
            await self.ws.send_json({"type": "input_audio_buffer.commit"})

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

    def __aiter__(self) -> AsyncIterator[ASREvent]:
        return self._events()

    @staticmethod
    def _map_event(raw: dict[str, Any]) -> ASREvent | None:
        """Map a Qwen realtime frame onto the provider-neutral event model.

        Timestamps live on the VAD events, never on ``completed`` -- verified by
        capturing the raw event stream for real live audio:

            speech_started  keys = [audio_start_ms, event_id, item_id]
            speech_stopped  keys = [audio_end_ms,   event_id, item_id]
            completed       keys = [item_id, content_index, language,
                                    emotion, usage, transcript]

        ``completed`` carries no ``begin_time``/``end_time``/``audio_*_ms`` of
        any kind, so an earlier attempt to read them there always fell through
        to an estimator.  ``item_id`` ties the three events together.
        """
        event_type = raw.get("type", "")
        item_id = raw.get("item_id")
        if event_type == "input_audio_buffer.speech_started":
            return ASREvent("speech_started", begin_pcm=_seconds(raw.get("audio_start_ms")), item_id=item_id, raw=raw)
        if event_type == "input_audio_buffer.speech_stopped":
            end = _seconds(raw.get("audio_end_ms"))
            return ASREvent(
                "speech_stopped", end_pcm=end, item_id=item_id, raw=raw,
                caption_observation=CaptionObservation("endpoint", 0, str(item_id or "0"), end_pcm=end),
            )
        if event_type == "conversation.item.input_audio_transcription.text":
            # ``text`` is the confirmed stable prefix (grows monotonically);
            # ``stash`` is a tentative tail that WILL be rewritten.
            return ASREvent(
                "interim",
                text=raw.get("text", ""),
                stash=raw.get("stash", ""),
                language=canonicalize_tag_or_none(raw.get("language")),
                item_id=item_id,
                raw=raw,
                caption_observation=CaptionObservation(
                    "stable_prefix_snapshot", 0, str(item_id or "0"),
                    stable_text=str(raw.get("text", "")),
                    tentative_text=str(raw.get("stash", "")),
                ),
            )
        if event_type == "conversation.item.input_audio_transcription.completed":
            return ASREvent(
                "final",
                text=raw.get("transcript", ""),
                language=canonicalize_tag_or_none(raw.get("language")),
                item_id=item_id,
                raw=raw,
                caption_observation=CaptionObservation(
                    "utterance_final", 0, str(item_id or "0"),
                    stable_text=str(raw.get("transcript", "")),
                ),
            )
        if event_type in {"error", "conversation.item.input_audio_transcription.failed"}:
            error = raw.get("error", raw)
            return ASREvent("error", message=error.get("message", str(error)) if isinstance(error, dict) else str(error), raw=raw)
        return None

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.ws and not self.ws.closed:
            try:
                await self.ws.send_json({"type": "session.finish"})
            except (aiohttp.ClientError, RuntimeError):
                pass
            await self.ws.close()
        if self.session:
            await self.session.close()


def _seconds(value: Any) -> float | None:
    return float(value) / 1000.0 if isinstance(value, (int, float)) else None
