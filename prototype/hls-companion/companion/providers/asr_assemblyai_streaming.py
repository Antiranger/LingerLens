"""AssemblyAI Streaming v3 STT adapter (``assemblyai-streaming``).

Implements the official Streaming v3 WebSocket protocol for
``wss://streaming.assemblyai.com/v3/ws`` with plain ``aiohttp`` (the old v2
realtime endpoint is retired upstream and v3's protocol is fully public).
Sources (read 2026-09, see docs/research/realtime-stt-provider-protocol-audit-2026.md):

* API spec: https://www.assemblyai.com/docs/streaming/api-spec/streaming-websocket
* Message sequence: https://www.assemblyai.com/docs/streaming/message-sequence

Wire summary:

* Handshake: ``GET /v3/ws`` with ``Authorization: <key>`` (NO Bearer prefix)
  and query-string configuration (``sample_rate``, ``encoding``,
  ``speech_model``, ``language_codes``, ``language_detection``,
  ``speaker_labels``, ...). Misspelled query parameters are silently ignored
  upstream, so the adapter validates the ``Begin.configuration`` echo.
* Audio: raw binary frames of 16-bit LE mono PCM (never JSON/base64).
* Server messages: ``Begin`` (session confirmation), ``SpeechStarted``,
  ``Turn`` (``turn_order`` / ``end_of_turn`` / ``turn_is_formatted`` /
  word-level ms timestamps / ``speaker_label`` / ``language_code``),
  ``Termination``, ``SpeakerRevision`` and typed ``Error`` messages.
* Client controls: ``{"type": "ForceEndpoint"}`` (flush), ``{"type":
  "Terminate"}`` (graceful stop; a ``SpeakerRevision`` may still follow),
  ``{"type": "KeepAlive"}`` (only needed with inactivity timeouts).

Turn semantics (the reason this is NOT a generic WebSocket provider): every
message for a given ``turn_order`` COVERS the previous one; a turn becomes a
final only when ``end_of_turn == true`` AND ``turn_is_formatted == true``
(Universal Streaming can emit an unformatted terminal turn first -- treating
either condition alone as final duplicates cues).

The adapter owns exactly one session, its keepalive/terminate/close; the
SubtitlePipeline remains the only generic reconnect owner.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any, AsyncIterator

import aiohttp

from . import register
from ..languages import LanguageNotSupportedError, canonicalize_tag_or_none, primary_subtag
from .base import (
    ASRCapabilities,
    ASREvent,
    ASRLanguageCapabilities,
    ASRProvider,
    ASRStream,
    CaptionObservation,
    RecognitionToken,
    SourceLanguagePolicy,
)

# Universal-3.5 Pro language codes (official list; the model natively
# code-switches and ``language_codes`` only biases detection).
# https://www.assemblyai.com/docs/streaming/api-spec/streaming-websocket
_UNIVERSAL_3_5_PRO_LANGUAGES = (
    "en", "es", "fr", "de", "it", "pt", "tr", "nl", "sv", "no", "da", "fi",
    "hi", "vi", "ar", "he", "ja", "zh",
)

_MODEL_PRESETS: dict[str, dict[str, Any]] = {
    "universal-3-5-pro": {
        "tier": "provider_claimed",
        "languages": _UNIVERSAL_3_5_PRO_LANGUAGES,
        "code_switching": True,
        "detection": "unrestricted",
        "reports_detected_language": True,
    },
}


def _canonical_tags(tags: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(tag for tag in (canonicalize_tag_or_none(item) for item in tags) if tag))


@register("assemblyai-streaming")
class AssemblyAIStreamingASRProvider(ASRProvider):
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
    def _preset(self) -> dict[str, Any]:
        return _MODEL_PRESETS.get(self.model, {})

    @property
    def capabilities(self) -> ASRCapabilities:
        preset = self._preset
        if preset:
            language = ASRLanguageCapabilities(
                supported_tags=_canonical_tags(tuple(preset["languages"])),
                detection=preset["detection"],
                reports_detected_language=bool(preset["reports_detected_language"]),
                code_switching=bool(preset["code_switching"]),
                tier=preset["tier"],
            )
            languages = _canonical_tags(tuple(preset["languages"]))
        else:
            language = ASRLanguageCapabilities()
            languages = ()
        return ASRCapabilities(
            True,
            True,
            False,  # Turn interims cover each other; text can rewrite
            True,  # end_of_turn is server-side turn detection
            True,
            False,
            False,
            languages,
            (16000,),
            manual_commit=True,  # ForceEndpoint
            language=language,
            preferred_sample_rate=16000,
            speaker_labels=bool(self.options.get("speakerLabels", False)),
            caption_evidence=frozenset({"token_snapshot", "utterance_final", "endpoint"}),
        )

    async def stream(self, *, policy: SourceLanguagePolicy, sample_rate: int, hotwords: list[str], context: list[str]) -> ASRStream:
        del hotwords, context
        if not self.api_key:
            raise ValueError(f"API key is not configured for provider {self.id}")
        if sample_rate not in self.capabilities.sample_rates:
            raise ValueError(f"sample rate {sample_rate} is not supported by provider {self.id}")
        if policy.mode == "detect" and self.capabilities.language.detection == "none":
            raise LanguageNotSupportedError("this AssemblyAI model cannot auto-detect the source language")
        stream = _AssemblyAIStream(self, policy, sample_rate)
        await stream.connect()
        return stream


class _AssemblyAIStream(ASRStream):
    def __init__(self, provider: AssemblyAIStreamingASRProvider, policy: SourceLanguagePolicy, sample_rate: int):
        self.provider = provider
        self.policy = policy
        self.sample_rate = sample_rate
        self.session: aiohttp.ClientSession | None = None
        self.ws: Any = None
        self.closed = False
        self._last_speech_started_ms: float | None = None
        self._turn_started: set[str] = set()
        self._last_audio_at = time.monotonic()
        self._keepalive_task: asyncio.Task | None = None
        self.keepalive_seconds = float(provider.options.get("keepAliveSeconds", 0.0))
        # Graceful stop state (same shape as the Soniox adapter): after
        # Terminate the server flushes Termination and may append a
        # SpeakerRevision before closing; the consumer drains until the socket
        # closes, with a bounded fallback closer.
        self._iter_started = False
        self._drain_done = asyncio.Event()
        self._release_done = False
        self._force_close_task: asyncio.Task | None = None
        self.drain_seconds = float(provider.options.get("closeDrainTimeoutSeconds", 2.0))

    def _query(self) -> dict[str, str]:
        options = self.provider.options
        params: dict[str, str] = {
            "sample_rate": str(self.sample_rate),
            "encoding": "pcm_s16le",
            "speech_model": self.provider.model,
        }
        if self.policy.mode == "specified":
            assert self.policy.tag is not None
            params["language_codes"] = primary_subtag(self.policy.tag)
        else:
            # Detection mode: language_detection=true makes Turn messages
            # carry language_code; explicit candidates additionally bias.
            params["language_detection"] = "true"
            if self.policy.candidates:
                params["language_codes"] = ",".join(
                    dict.fromkeys(primary_subtag(tag) for tag in self.policy.candidates)
                )
        if options.get("speakerLabels"):
            params["speaker_labels"] = "true"
        if options.get("maxSpeakers"):
            params["max_speakers"] = str(int(options["maxSpeakers"]))
        if options.get("mode"):
            params["mode"] = str(options["mode"])
        if options.get("minTurnSilenceMs"):
            params["min_turn_silence"] = str(int(options["minTurnSilenceMs"]))
        if options.get("maxTurnSilenceMs"):
            params["max_turn_silence"] = str(int(options["maxTurnSilenceMs"]))
        if options.get("sessionHeartbeatSeconds"):
            params["session_heartbeat"] = str(int(options["sessionHeartbeatSeconds"]))
        if options.get("prompt"):
            params["prompt"] = str(options["prompt"])
        if options.get("keytermsPrompt"):
            params["keyterms_prompt"] = str(options["keytermsPrompt"])
        return params

    async def connect(self) -> None:
        self.session = aiohttp.ClientSession()
        query = "&".join(f"{key}={value}" for key, value in self._query().items())
        try:
            self.ws = await self.session.ws_connect(
                f"{self.provider.base_url}?{query}",
                # Official streaming auth: raw key header, no Bearer prefix.
                headers={"Authorization": self.provider.api_key},
            )
        except BaseException:
            await self._release()
            raise
        if self.keepalive_seconds > 0:
            self._keepalive_task = asyncio.create_task(
                self._keepalive(), name=f"assemblyai-keepalive-{self.provider.id}"
            )

    async def push_pcm(self, chunk: bytes, pcm_offset: float) -> None:
        del pcm_offset
        if not self.ws or self.ws.closed:
            return
        self._last_audio_at = time.monotonic()
        await self.ws.send_bytes(chunk)

    async def flush(self) -> None:
        """Force an utterance break now (official ForceEndpoint control)."""
        if self.ws and not self.ws.closed:
            await self.ws.send_json({"type": "ForceEndpoint"})

    async def commit(self) -> None:
        await self.flush()

    async def _keepalive(self) -> None:
        try:
            while self.ws and not self.ws.closed:
                if time.monotonic() - self._last_audio_at >= self.keepalive_seconds:
                    with contextlib.suppress(aiohttp.ClientError, RuntimeError):
                        await self.ws.send_json({"type": "KeepAlive"})
                    self._last_audio_at = time.monotonic()
                await asyncio.sleep(min(1.0, self.keepalive_seconds / 2))
        except asyncio.CancelledError:
            raise

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
                elif message.type == aiohttp.WSMsgType.ERROR:
                    yield ASREvent("error", message=str(self.ws.exception()))
                    return
        finally:
            # Only signal the drain: resource release is owned by aclose() so
            # the official Terminate control can still be sent afterwards.
            self._drain_done.set()

    def _map_event(self, raw: dict[str, Any]) -> list[ASREvent]:
        message_type = raw.get("type")
        if message_type == "Begin":
            # Misspelled query parameters are silently ignored upstream; the
            # Begin echo is the only way to catch a misconfigured session.
            configuration = raw.get("configuration") or {}
            echoed_model = str(configuration.get("model", ""))
            if echoed_model and echoed_model != self.provider.model:
                return [ASREvent(
                    "error",
                    message=f"AssemblyAI session started with model {echoed_model!r} instead of {self.provider.model!r}",
                    raw=raw,
                )]
            return []
        if message_type == "SpeechStarted":
            timestamp = raw.get("timestamp")
            self._last_speech_started_ms = float(timestamp) if isinstance(timestamp, (int, float)) else None
            return []
        if message_type == "SpeakerRevision":
            turn_order = raw.get("turn_order")
            speaker = raw.get("speaker_label")
            if turn_order is None:
                return []
            return [ASREvent(
                "speaker_revision",
                item_id=str(turn_order),
                speaker=str(speaker) if speaker is not None else None,
                raw=raw,
            )]
        if message_type == "Error":
            code = raw.get("code")
            detail = raw.get("detail") or raw.get("message") or ""
            return [ASREvent("error", message=f"{code}: {detail}".strip(": "), raw=raw)]
        if message_type == "Termination":
            # Post-Terminate statistics only; the session ends when the server
            # closes the socket (possibly after a trailing SpeakerRevision).
            return []
        if message_type != "Turn":
            return []

        turn_order = raw.get("turn_order")
        if turn_order is None:
            return []
        item_id = str(turn_order)
        words = raw.get("words") or []
        begin_pcm = _seconds(words[0].get("start")) if words and isinstance(words[0].get("start"), (int, float)) else self._last_speech_started_ms
        end_pcm = _seconds(words[-1].get("end")) if words and isinstance(words[-1].get("end"), (int, float)) else None
        transcript = str(raw.get("transcript", "")).strip()
        speaker = raw.get("speaker_label")
        speaker = str(speaker) if speaker is not None else self._dominant_word_speaker(words)
        language = canonicalize_tag_or_none(raw.get("language_code"))
        text = str(raw.get("utterance") or transcript).strip()

        events: list[ASREvent] = []
        if item_id not in self._turn_started and (begin_pcm is not None or text):
            # One speech_started per turn gives the pipeline the exact onset;
            # every later message for this turn covers the previous one.
            self._turn_started.add(item_id)
            events.append(ASREvent("speech_started", begin_pcm=begin_pcm, item_id=item_id, raw=raw))
        end_of_turn = bool(raw.get("end_of_turn"))
        turn_is_formatted = bool(raw.get("turn_is_formatted"))
        if end_of_turn and turn_is_formatted:
            # The ONLY final condition: both flags true. Universal Streaming
            # sends an unformatted terminal turn first -- emitting a final
            # there duplicates the cue when the formatted version lands.
            if end_pcm is not None:
                events.append(ASREvent(
                    "speech_stopped", end_pcm=end_pcm, item_id=item_id, raw=raw,
                    caption_observation=CaptionObservation("endpoint", 0, item_id, end_pcm=end_pcm),
                ))
            if text:
                events.append(ASREvent(
                    "final", text=text, begin_pcm=begin_pcm, end_pcm=end_pcm,
                    language=language, speaker=speaker, item_id=item_id, raw=raw,
                    caption_observation=CaptionObservation(
                        "utterance_final", 0, item_id,
                        tokens=tuple(_recognition_word(word, True, language) for word in words if isinstance(word, dict)),
                        stable_text=text, begin_pcm=begin_pcm, end_pcm=end_pcm,
                    ),
                ))
            return events
        if text:
            events.append(ASREvent(
                "interim", text=text, begin_pcm=begin_pcm, end_pcm=end_pcm,
                language=language, speaker=speaker, item_id=item_id, raw=raw,
                caption_observation=CaptionObservation(
                    "token_snapshot", 0, item_id,
                    tokens=tuple(_recognition_word(word, bool(word.get("word_is_final")), language) for word in words if isinstance(word, dict)),
                    tentative_text=text, begin_pcm=begin_pcm, end_pcm=end_pcm,
                ),
            ))
        return events

    @staticmethod
    def _dominant_word_speaker(words: list[dict[str, Any]]) -> str | None:
        counts: dict[str, int] = {}
        for word in words:
            speaker = word.get("speaker")
            if speaker is None:
                continue
            key = str(speaker)
            counts[key] = counts.get(key, 0) + 1
        return max(counts, key=lambda key: counts[key]) if counts else None

    async def _release(self) -> None:
        if self._release_done:
            return
        self._release_done = True
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            await asyncio.gather(self._keepalive_task, return_exceptions=True)
            self._keepalive_task = None
        if self.ws is not None:
            with contextlib.suppress(aiohttp.ClientError, RuntimeError):
                await self.ws.close()
        if self.session is not None:
            with contextlib.suppress(Exception):
                await self.session.close()

    async def _force_close_after_drain_window(self) -> None:
        try:
            await asyncio.wait_for(self._drain_done.wait(), timeout=self.drain_seconds)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        await self._release()

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            await asyncio.gather(self._keepalive_task, return_exceptions=True)
            self._keepalive_task = None
        if self.ws and not self.ws.closed:
            # Official graceful stop: Terminate -> Termination and possible
            # trailing SpeakerRevision -> server close. Drain within a bound.
            with contextlib.suppress(aiohttp.ClientError, RuntimeError):
                await self.ws.send_json({"type": "Terminate"})
        if self._iter_started and not self._drain_done.is_set():
            try:
                await asyncio.wait_for(self._drain_done.wait(), timeout=self.drain_seconds)
            except asyncio.TimeoutError:
                pass
        await self._release()


def _recognition_word(word: dict[str, Any], provider_stable: bool, language: str | None) -> RecognitionToken:
    confidence = word.get("confidence")
    return RecognitionToken(
        text=str(word.get("text", "")),
        begin_pcm=_seconds(word.get("start")),
        end_pcm=_seconds(word.get("end")),
        provider_stable=provider_stable,
        language=canonicalize_tag_or_none(word.get("language_code")) or language,
        speaker=str(word["speaker"]) if word.get("speaker") is not None else None,
        confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
    )


def _seconds(value: Any) -> float | None:
    return float(value) / 1000.0 if isinstance(value, (int, float)) else None
