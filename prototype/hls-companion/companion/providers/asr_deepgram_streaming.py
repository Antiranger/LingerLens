"""Deepgram Streaming ASR adapter (``deepgram-streaming``).

Implements the official low-level WebSocket protocol for
``wss://api.deepgram.com/v1/listen`` using ``aiohttp`` directly (no Deepgram SDK
dependency). Sources (read 2026-12):

* Streaming reference: https://developers.deepgram.com/reference/speech-to-text/listen-streaming
* Low-level WebSockets: https://developers.deepgram.com/docs/lower-level-websockets
* Multilingual code-switching: https://developers.deepgram.com/docs/multilingual-code-switching
* Models & languages: https://developers.deepgram.com/docs/languages-overview

Wire summary:

* Handshake: ``GET /v1/listen`` with ``Authorization: Token <key>`` and query
  parameters (``model``, ``encoding=linear16``, ``sample_rate``, ``channels=1``,
  ``language`` or ``language=multi``, ``interim_results``, ``smart_format``,
  ``endpointing``, ``vad_events``).
* Audio: binary WebSocket frames of 16-bit LE mono PCM.
* Client control messages: ``{"type": "Finalize"}`` (flush), ``{"type":
  "KeepAlive"}`` (idle keepalive), ``{"type": "CloseStream"}`` (orderly close).
* Server messages: ``Results`` (``is_final``/``speech_final`` flags, word
  timing, ``alternatives[].languages`` detected-language tags), ``SpeechStarted``
  (with ``vad_events=true``), ``UtteranceEnd``, ``Metadata``.

The adapter owns exactly one session, its keepalive/finalize/close; the
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

# nova-3 specified-mode language list (primary codes; the capability contract's
# basic-range match covers regional/script variants such as pt-BR / zh-Hans).
# Source: https://developers.deepgram.com/docs/languages-overview
_NOVA3_SUPPORTED = (
    "af", "ar", "hy", "as", "be", "bn", "bs", "bg", "ca", "zh", "hr", "cs", "da",
    "nl", "en", "et", "fi", "fr", "ka", "de", "el", "gu", "he", "hi", "hu", "id",
    "it", "ja", "kn", "ko", "lv", "lt", "mk", "ms", "mr", "mn", "ne", "no", "ps",
    "fa", "pl", "pt", "pa", "ro", "ru", "sr", "sk", "sl", "es", "sv", "tl", "ta",
    "te", "th", "tr", "uk", "ur", "vi",
)

# nova-3 ``language=multi`` code-switching set (English, Spanish, French, German,
# Hindi, Russian, Portuguese, Japanese, Italian, Dutch).
_NOVA3_MULTI = ("en", "es", "fr", "de", "hi", "ru", "pt", "ja", "it", "nl")


def _canonical_tags(tags: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(tag for tag in (canonicalize_tag_or_none(item) for item in tags) if tag))


def _language_hint(tag: str) -> str:
    """Map a canonical policy tag onto a Deepgram ``language`` code.

    Deepgram explicitly supports ``zh-Hans``/``zh-Hant`` (our canonical zh
    script identities); other tags fall back to the primary subtag, which
    covers the provider's regional variants.
    """
    if tag in ("zh-Hans", "zh-Hant"):
        return tag
    return primary_subtag(tag)


@register("deepgram-streaming")
class DeepgramStreamingASRProvider(ASRProvider):
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
    def capabilities(self) -> ASRCapabilities:
        if self.model == "nova-3":
            # nova-3: ``multi`` code-switching detection among the official
            # 10-language set; candidates outside that set are invalid.
            language = ASRLanguageCapabilities(
                supported_tags=_canonical_tags(_NOVA3_SUPPORTED),
                detection="unrestricted",
                max_candidates=None,
                reports_detected_language=True,
                code_switching=True,
                tier="provider_claimed",
                detection_tags=_canonical_tags(_NOVA3_MULTI),
            )
            languages = _canonical_tags(_NOVA3_SUPPORTED)
        else:
            # Unknown custom models stay experimental and claim no detection.
            language = ASRLanguageCapabilities(
                supported_tags=None,
                detection="none",
                reports_detected_language=False,
                tier="experimental",
            )
            languages = ()
        return ASRCapabilities(
            True, True, False, True, True, False, False, languages, (16000, 24000),
            language=language,
            preferred_sample_rate=16000,
            speaker_labels=bool(self.options.get("diarize", False)),
            caption_evidence=frozenset({"token_snapshot", "stable_token_delta", "endpoint"}),
        )

    async def stream(self, *, policy: SourceLanguagePolicy, sample_rate: int, hotwords: list[str], context: list[str]) -> ASRStream:
        del hotwords, context
        if not self.api_key:
            raise ValueError(f"API key is not configured for provider {self.id}")
        if sample_rate not in self.capabilities.sample_rates:
            raise ValueError(f"sample rate {sample_rate} is not supported by provider {self.id}")
        if policy.mode == "detect":
            caps = self.capabilities.language
            if caps.detection == "none":
                raise LanguageNotSupportedError("this Deepgram model cannot auto-detect the source language")
            candidates = policy.candidates
            invalid = [tag for tag in candidates if primary_subtag(tag) not in _NOVA3_MULTI]
            if invalid:
                raise LanguageNotSupportedError(
                    f"Deepgram nova-3 multi detection does not cover: {', '.join(invalid)}"
                    f" (code-switching languages: {', '.join(_NOVA3_MULTI)})"
                )
        stream = _DeepgramStream(self, policy, sample_rate)
        await stream.connect()
        return stream


class _DeepgramStream(ASRStream):
    def __init__(self, provider: DeepgramStreamingASRProvider, policy: SourceLanguagePolicy, sample_rate: int):
        self.provider = provider
        self.policy = policy
        self.sample_rate = sample_rate
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.closed = False
        self._utterance = 0
        self._last_audio_at = time.monotonic()
        self._keepalive_task: asyncio.Task | None = None
        self.keepalive_seconds = float(provider.options.get("keepAliveSeconds", 8.0))

    def _query(self) -> str:
        options = self.provider.options
        params = {
            "model": self.provider.model,
            "encoding": "linear16",
            "sample_rate": str(self.sample_rate),
            "channels": "1",
            "interim_results": "true" if options.get("interimResults", True) else "false",
            "smart_format": "true" if options.get("smartFormat", True) else "false",
            "endpointing": str(options.get("endpointingMs", 300)),
            "vad_events": "true" if options.get("vadEvents", True) else "false",
        }
        # Diarization is a paid add-on: only request it when the profile opts
        # in; word-level integer "speaker" labels come back on every word.
        if options.get("diarize"):
            params["diarize"] = "true"
        # UtteranceEnd is documented as paired with the utterance_end_ms
        # parameter; profiles that rely on UtteranceEnd speech_stopped events
        # must set it (default off keeps existing profiles byte-identical).
        if options.get("utteranceEndMs"):
            params["utterance_end_ms"] = str(int(options["utteranceEndMs"]))
        if self.policy.mode == "detect":
            params["language"] = "multi"
        else:
            assert self.policy.tag is not None
            params["language"] = _language_hint(self.policy.tag)
        return "&".join(f"{key}={value}" for key, value in params.items())

    async def connect(self) -> None:
        self.session = aiohttp.ClientSession()
        url = f"{self.provider.base_url}?{self._query()}"
        try:
            self.ws = await self.session.ws_connect(
                url,
                headers={"Authorization": f"Token {self.provider.api_key}"},
            )
        except BaseException:
            # Any handshake failure (bad key/401, network error, or a pipeline
            # cancellation racing the connect) must not leak the ClientSession.
            await self.aclose()
            raise
        if self.keepalive_seconds > 0:
            self._keepalive_task = asyncio.create_task(self._keepalive(), name=f"deepgram-keepalive-{self.provider.id}")

    async def push_pcm(self, chunk: bytes, pcm_offset: float) -> None:
        del pcm_offset
        if not self.ws or self.ws.closed:
            return
        self._last_audio_at = time.monotonic()
        await self.ws.send_bytes(chunk)

    async def flush(self) -> None:
        if self.ws and not self.ws.closed:
            await self.ws.send_json({"type": "Finalize"})

    async def _keepalive(self) -> None:
        """Send the official KeepAlive control message when audio pauses."""
        try:
            while self.ws and not self.ws.closed:
                idle = time.monotonic() - self._last_audio_at
                if idle >= self.keepalive_seconds:
                    with contextlib.suppress(aiohttp.ClientError, RuntimeError):
                        await self.ws.send_json({"type": "KeepAlive"})
                    self._last_audio_at = time.monotonic()
                await asyncio.sleep(min(1.0, self.keepalive_seconds / 2))
        except asyncio.CancelledError:
            raise

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
                for event in self._map_event(raw):
                    yield event
            elif message.type == aiohttp.WSMsgType.ERROR:
                yield ASREvent("error", message=str(self.ws.exception()))
                break

    def _map_event(self, raw: dict[str, Any]) -> list[ASREvent]:
        """Map one Deepgram server message onto provider-neutral events.

        Detected languages are canonicalized here at the Adapter edge; the
        pipeline never sees provider-private codes.
        """
        message_type = raw.get("type")
        if message_type == "SpeechStarted":
            self._utterance += 1
            timestamp = raw.get("timestamp")
            return [ASREvent(
                "speech_started",
                begin_pcm=float(timestamp) if isinstance(timestamp, (int, float)) else None,
                item_id=str(self._utterance),
                raw=raw,
            )]
        if message_type == "UtteranceEnd":
            last_word_end = raw.get("last_word_end")
            end_pcm = float(last_word_end) if isinstance(last_word_end, (int, float)) else None
            item_id = str(self._utterance) if self._utterance else "0"
            return [ASREvent(
                "speech_stopped",
                end_pcm=end_pcm,
                item_id=item_id,
                raw=raw,
                caption_observation=CaptionObservation("endpoint", 0, item_id, end_pcm=end_pcm),
            )]
        if message_type != "Results":
            # Metadata and other control messages carry no subtitle content.
            return []

        channel = raw.get("channel") or {}
        alternatives = channel.get("alternatives") or []
        if not alternatives:
            return []
        alternative = alternatives[0]
        transcript = str(alternative.get("transcript", "")).strip()
        words = alternative.get("words") or []
        begin_pcm = float(words[0]["start"]) if words and isinstance(words[0].get("start"), (int, float)) else None
        end_pcm = float(words[-1]["end"]) if words and isinstance(words[-1].get("end"), (int, float)) else None
        language = self._dominant_language(alternative)
        speaker = self._dominant_speaker(words)
        item_id = str(self._utterance) if self._utterance else None
        recognition_tokens = tuple(_recognition_word(word, bool(raw.get("is_final"))) for word in words if isinstance(word, dict))

        if not raw.get("is_final"):
            if not transcript:
                return []
            return [ASREvent(
                "interim", text=transcript, begin_pcm=begin_pcm, end_pcm=end_pcm,
                language=language, speaker=speaker, item_id=item_id, raw=raw,
                caption_observation=CaptionObservation(
                    "token_snapshot", 0, str(item_id or "0"), tokens=recognition_tokens,
                    tentative_text=transcript, begin_pcm=begin_pcm, end_pcm=end_pcm,
                ),
            )]

        events: list[ASREvent] = []
        if transcript:
            # Deepgram `is_final` closes only this transcript interval. It may
            # occur several times inside one spoken utterance; only
            # `speech_final`/UtteranceEnd is the utterance endpoint. Treat the
            # words as an immutable delta so later speech under the same item
            # remains chunkable instead of being ignored as a closed item.
            events.append(ASREvent(
                "final", text=transcript, begin_pcm=begin_pcm, end_pcm=end_pcm,
                language=language, speaker=speaker, item_id=item_id, raw=raw,
                caption_observation=CaptionObservation(
                    "stable_token_delta", 0, str(item_id or "0"), tokens=recognition_tokens,
                    stable_text=transcript, begin_pcm=begin_pcm, end_pcm=end_pcm,
                ),
            ))
        if raw.get("speech_final") and end_pcm is not None:
            events.append(ASREvent(
                "speech_stopped", end_pcm=end_pcm, item_id=item_id, raw=raw,
                caption_observation=CaptionObservation("endpoint", 0, str(item_id or "0"), end_pcm=end_pcm),
            ))
        return events

    @staticmethod
    def _dominant_language(alternative: dict[str, Any]) -> str | None:
        """Dominant detected language, canonicalized at the Adapter edge.

        ``alternatives[].languages`` is sorted by word count (official docs);
        fall back to the most frequent word-level language for mixed cues.
        """
        languages = alternative.get("languages") or []
        if languages:
            return canonicalize_tag_or_none(languages[0])
        counts: dict[str, int] = {}
        for word in alternative.get("words") or []:
            tag = canonicalize_tag_or_none(word.get("language"))
            if tag:
                counts[tag] = counts.get(tag, 0) + 1
        return max(counts, key=lambda tag: counts[tag]) if counts else None

    @staticmethod
    def _dominant_speaker(words: list[dict[str, Any]]) -> str | None:
        """Most frequent word-level ``speaker`` label (diarize=true), rendered
        as a string. Deterministic on ties (first-seen wins); None when the
        profile did not request diarization."""
        counts: dict[str, int] = {}
        for word in words:
            speaker = word.get("speaker")
            if speaker is None:
                continue
            key = str(speaker)
            counts[key] = counts.get(key, 0) + 1
        return max(counts, key=lambda key: counts[key]) if counts else None

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            await asyncio.gather(self._keepalive_task, return_exceptions=True)
            self._keepalive_task = None
        if self.ws and not self.ws.closed:
            with contextlib.suppress(aiohttp.ClientError, RuntimeError):
                await self.ws.send_json({"type": "CloseStream"})
            await self.ws.close()
        if self.session:
            await self.session.close()


def _recognition_word(word: dict[str, Any], provider_stable: bool) -> RecognitionToken:
    confidence = word.get("confidence")
    return RecognitionToken(
        text=str(word.get("punctuated_word") or word.get("word") or ""),
        begin_pcm=float(word["start"]) if isinstance(word.get("start"), (int, float)) else None,
        end_pcm=float(word["end"]) if isinstance(word.get("end"), (int, float)) else None,
        provider_stable=provider_stable,
        language=canonicalize_tag_or_none(word.get("language")),
        speaker=str(word["speaker"]) if word.get("speaker") is not None else None,
        confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
    )
