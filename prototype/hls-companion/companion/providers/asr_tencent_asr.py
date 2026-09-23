"""Tencent Cloud realtime ASR adapter (``tencent-asr``).

Implements the official Tencent realtime speech recognition WebSocket
protocol with plain aiohttp (URL-signed handshake + binary PCM + classic JSON
responses). Sources (read 2026-09, see
docs/research/realtime-stt-provider-protocol-audit-2026.md):

* Classic protocol: https://cloud.tencent.com/document/product/1093/48982
* V2 protocol + signature: https://cloud.tencent.com/document/product/1093/131127
* Speaker (meeting) sentences shape: https://cloud.tencent.com/document/product/1093/130881

Wire summary:

* Handshake: ``wss://asr.cloud.tencent.com/asr/v2/<appid>?<signed query>``.
  Query params (sorted by key for signing): ``engine_model_type``,
  ``voice_id`` (a FRESH uuid per connection -- invalidated on disconnect),
  ``voice_format=1`` (PCM), ``word_info``, ``needvad``, ``secretid``,
  ``timestamp``, ``expired`` (must be > timestamp, < 90 days), ``nonce``
  and ``signature`` = base64(HMAC-SHA1(canonical_string, SecretKey)) where
  canonical_string = ``asr.cloud.tencent.com/asr/v2/<appid>?<sorted
  k=v&...>`` (everything except signature). The signature value is
  URL-encoded in the final URL.
  SECURITY: the full signed URL is a credential-bearing string -- it is
  never logged and never included in error messages.
* Audio: binary PCM frames (200 ms pacing is the pipeline's business).
* Responses (classic, confirmed shape): ``{"code": 0, "result":
  {"slice_type": 0|1|2, "index": N, "start_time": ms, "end_time": ms,
  "voice_text_str": text, "word_list": [...]}}`` -- slice_type 0/1 are
  interim, 2 is the terminal slice (final).
* Speaker engines may answer with the confirmed second shape
  ``result.sentences.sentence_list[]``: ``sentence_type`` 0 (识别中) ->
  interim, 1 (已确认) -> final, plus ``speaker_id``. Any OTHER unknown shape
  is ignored -- never guessed.
* ``code != 0`` is a typed error (code + message); the classic protocol ends
  by closing the connection, so the adapter does that (bounded) and never
  invents an end-of-stream control frame.

Language coverage is decided by ``engine_model_type`` (e.g. ``16k_ja`` for
Japanese, ``16k_zh_en_speaker_2.0`` for zh/en + speaker clustering); there is
no detection/code-switching contract.

The adapter owns exactly one session; the SubtitlePipeline remains the only
generic reconnect owner.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import random
import time
import uuid
from typing import Any, AsyncIterator

import aiohttp

from . import register
from .bounded import RecentIds
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

# Known engine language sets (classic protocol engine_model_type values the
# audit confirmed). Unknown custom engines stay experimental.
_ENGINE_LANGUAGES: dict[str, tuple[str, ...]] = {
    "16k_zh": ("zh",),
    "16k_zh_en": ("zh", "en"),
    "16k_zh_en_speaker": ("zh", "en"),
    "16k_zh_en_speaker_2.0": ("zh", "en"),
    "16k_zh_dialect": ("zh",),
    "16k_zh-medical": ("zh",),
    "16k_en": ("en",),
    "16k_ja": ("ja",),
    "16k_multi_lang": ("zh", "en", "ja", "ko", "fr", "de", "it", "pt", "es", "ru", "id", "ms", "th", "vi", "ar"),
}


def _canonical_tags(tags: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(tag for tag in (canonicalize_tag_or_none(item) for item in tags) if tag))


@register("tencent-asr")
class TencentAsrProvider(ASRProvider):
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
    def _engine(self) -> str:
        return str(self.options.get("engineModelType", self.model))

    @property
    def capabilities(self) -> ASRCapabilities:
        engine = self._engine
        languages = _ENGINE_LANGUAGES.get(engine)
        if languages is not None:
            detection = "unrestricted" if engine == "16k_multi_lang" else "none"
            language = ASRLanguageCapabilities(
                supported_tags=_canonical_tags(languages),
                detection=detection,
                reports_detected_language=False,
                code_switching=False,
                tier="provider_claimed",
            )
            language_tags = _canonical_tags(languages)
        else:
            language = ASRLanguageCapabilities()  # unknown engine: experimental
            language_tags = ()
        word_info = int(self.options.get("wordInfo", 0) or 0)
        speaker_engine = "speaker" in engine
        return ASRCapabilities(
            True,
            True,
            False,
            True,  # server-side VAD segmentation (slice_type)
            word_info > 0,
            False,
            False,
            language_tags,
            (16000,),
            manual_commit=False,
            language=language,
            preferred_sample_rate=16000,
            # speaker engines carry speaker labels in the confirmed shapes.
            speaker_labels=speaker_engine and word_info > 0,
            caption_evidence=frozenset({"text_snapshot", "utterance_final", "endpoint"}),
        )

    async def stream(self, *, policy: SourceLanguagePolicy, sample_rate: int, hotwords: list[str], context: list[str]) -> ASRStream:
        del hotwords, context
        if not self.api_key:
            raise ValueError("API key (SecretKey) is not configured for provider %s" % self.id)
        if not self.options.get("secretId"):
            raise ValueError("options.secretId is required for provider %s" % self.id)
        if not self.options.get("appId"):
            raise ValueError("options.appId is required for provider %s" % self.id)
        if sample_rate != 16000:
            raise ValueError(f"sample rate {sample_rate} is not supported by provider {self.id} (16k engines require 16000)")
        if policy.mode == "detect":
            if self._engine != "16k_multi_lang":
                raise LanguageNotSupportedError("this Tencent engine requires a specified source language")
        elif not policy.tag:
            raise LanguageNotSupportedError("Tencent realtime ASR requires a source language policy")
        stream = _TencentAsrStream(self, policy, sample_rate)
        await stream.connect()
        return stream


class _TencentAsrStream(ASRStream):
    def __init__(self, provider: TencentAsrProvider, policy: SourceLanguagePolicy, sample_rate: int):
        self.provider = provider
        self.policy = policy
        self.sample_rate = sample_rate
        # Official: voice_id is per-connection and invalidated on disconnect.
        self.voice_id = str(uuid.uuid4())
        self.session: aiohttp.ClientSession | None = None
        self.ws: Any = None
        self.closed = False
        self._started_items = RecentIds()

    def _signed_url(self) -> str:
        """Build the HMAC-SHA1 signed handshake URL.

        The canonical string is host-path + sorted query (everything except
        signature); the signature is base64(HMAC-SHA1(canonical, SecretKey))
        and is URL-encoded in the final URL. The result is credential-bearing:
        it is returned for the handshake only and never logged.
        """
        options = self.provider.options
        now = int(time.time())
        params: dict[str, str] = {
            "engine_model_type": str(options.get("engineModelType") or self.provider.model),
            "expired": str(now + int(options.get("signatureTtlSeconds", 3600))),
            "nonce": str(random.randint(1, 9_999_999_999)),
            "secretid": str(options["secretId"]),
            "timestamp": str(now),
            "voice_format": str(int(options.get("voiceFormat", 1))),
            "voice_id": self.voice_id,
        }
        if options.get("wordInfo") is not None:
            params["word_info"] = str(int(options["wordInfo"]))
        if options.get("needvad") is not None:
            params["needvad"] = str(int(options["needvad"]))
        if options.get("vadSilenceTimeMs") is not None:
            params["vad_silence_time"] = str(int(options["vadSilenceTimeMs"]))
        if options.get("maxSpeakTimeMs") is not None:
            params["max_speak_time"] = str(int(options["maxSpeakTimeMs"]))
        if options.get("filterEmptyResult") is not None:
            params["filter_empty_result"] = str(int(options["filterEmptyResult"]))
        base_url = self.provider.base_url.replace("<appid>", str(options["appId"]))
        canonical = (
            base_url.split("://", 1)[1]
            + "?"
            + "&".join(f"{key}={params[key]}" for key in sorted(params))
        )
        digest = hmac.new(self.provider.api_key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha1).digest()
        params["signature"] = base64.b64encode(digest).decode("ascii")
        query = "&".join(
            f"{key}={urllib_parse_quote(value, safe='')}" if key == "signature" else f"{key}={value}"
            for key, value in params.items()
        )
        return f"{base_url}?{query}"

    async def connect(self) -> None:
        self.session = aiohttp.ClientSession()
        try:
            self.ws = await self.session.ws_connect(self._signed_url())
        except BaseException:
            await self._release()
            raise

    async def push_pcm(self, chunk: bytes, pcm_offset: float) -> None:
        del pcm_offset
        if not self.ws or self.ws.closed:
            return
        await self.ws.send_bytes(chunk)

    async def flush(self) -> None:
        # No confirmed mid-stream flush control exists in the classic
        # protocol (the old "end" frame is unevidenced); boundaries come from
        # the server (needvad / max_speak_time).
        return None

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
                return

    def _map_event(self, raw: dict[str, Any]) -> list[ASREvent]:
        code = raw.get("code")
        if isinstance(code, int) and code != 0:
            # Typed error; deliberately excludes the signed URL.
            return [ASREvent("error", message=f"{code}: {raw.get('message', '')}".rstrip(": "), raw={"code": code, "message": raw.get("message")})]
        result = raw.get("result")
        if not isinstance(result, dict):
            return []

        if "slice_type" in result:
            return self._map_slice_result(result)
        sentences = result.get("sentences")
        if isinstance(sentences, dict) and isinstance(sentences.get("sentence_list"), list):
            return self._map_sentences(sentences["sentence_list"])
        # Unknown response shape (e.g. V2 speaker engines pending live
        # capture): ignore rather than guess field semantics.
        return []

    def _map_slice_result(self, result: dict[str, Any]) -> list[ASREvent]:
        slice_type = result.get("slice_type")
        index = result.get("index")
        item_id = str(index) if index is not None else "0"
        text = str(result.get("voice_text_str", "")).strip()
        start = _ms_to_seconds(result.get("start_time"))
        end = _ms_to_seconds(result.get("end_time"))
        speaker = self._dominant_word_speaker(result.get("word_list") or [])
        events: list[ASREvent] = []
        if slice_type == 2:
            # Terminal slice -> speech boundaries + final.
            if item_id not in getattr(self, "_started_items", set()):
                events.append(ASREvent("speech_started", begin_pcm=start, item_id=item_id, raw={"result": result}))
            if end is not None:
                events.append(ASREvent(
                    "speech_stopped", end_pcm=end, item_id=item_id, raw={"result": result},
                    caption_observation=CaptionObservation("endpoint", 0, item_id, end_pcm=end),
                ))
            if text:
                events.append(ASREvent(
                    "final", text=text, begin_pcm=start, end_pcm=end,
                    language=None, speaker=speaker, item_id=item_id,
                    raw={"result": result},
                    caption_observation=CaptionObservation(
                        "utterance_final", 0, item_id, stable_text=text,
                        begin_pcm=start, end_pcm=end,
                    ),
                ))
            return events
        if slice_type in (0, 1):
            if item_id not in getattr(self, "_started_items", set()):
                self._started_items = getattr(self, "_started_items", set())
                self._started_items.add(item_id)
                events.append(ASREvent("speech_started", begin_pcm=start, item_id=item_id, raw={"result": result}))
            if text:
                events.append(ASREvent(
                    "interim", text=text, begin_pcm=start, end_pcm=end,
                    language=None, speaker=speaker, item_id=item_id,
                    raw={"result": result},
                    caption_observation=CaptionObservation(
                        "text_snapshot", 0, item_id, tentative_text=text,
                        begin_pcm=start, end_pcm=end,
                    ),
                ))
        return events

    def _map_sentences(self, sentence_list: list[Any]) -> list[ASREvent]:
        events: list[ASREvent] = []
        for sentence in sentence_list:
            if not isinstance(sentence, dict):
                continue
            sentence_id = sentence.get("sentence_id")
            item_id = str(sentence_id) if sentence_id is not None else "0"
            text = str(sentence.get("sentence", "")).strip()
            start = _ms_to_seconds(sentence.get("start_time"))
            end = _ms_to_seconds(sentence.get("end_time"))
            speaker = str(sentence["speaker_id"]) if sentence.get("speaker_id") is not None else None
            sentence_type = sentence.get("sentence_type")
            if sentence_type == 1 and text:
                if start is not None:
                    events.append(ASREvent("speech_started", begin_pcm=start, speaker=speaker, item_id=item_id, raw={"sentence": sentence}))
                if end is not None:
                    events.append(ASREvent("speech_stopped", end_pcm=end, speaker=speaker, item_id=item_id, raw={"sentence": sentence}))
                events.append(ASREvent(
                    "final", text=text, begin_pcm=start, end_pcm=end,
                    language=None, speaker=speaker, item_id=item_id,
                    raw={"sentence": sentence},
                    caption_observation=CaptionObservation(
                        "utterance_final", 0, item_id, stable_text=text,
                        begin_pcm=start, end_pcm=end,
                    ),
                ))
            elif sentence_type == 0 and text:
                if start is not None:
                    events.append(ASREvent("speech_started", begin_pcm=start, speaker=speaker, item_id=item_id, raw={"sentence": sentence}))
                events.append(ASREvent(
                    "interim", text=text, begin_pcm=start, end_pcm=end,
                    language=None, speaker=speaker, item_id=item_id,
                    raw={"sentence": sentence},
                    caption_observation=CaptionObservation(
                        "text_snapshot", 0, item_id, tentative_text=text,
                        begin_pcm=start, end_pcm=end,
                    ),
                ))
        return events

    @staticmethod
    def _dominant_word_speaker(words: list[Any]) -> str | None:
        counts: dict[str, int] = {}
        for word in words:
            if not isinstance(word, dict) or word.get("speaker") is None:
                continue
            key = str(word["speaker"])
            counts[key] = counts.get(key, 0) + 1
        return max(counts, key=lambda key: counts[key]) if counts else None

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
        # Classic protocol end: close the connection; the server finalizes the
        # session. voice_id is invalid afterwards (fresh one on reconnect).
        await self._release()


def _ms_to_seconds(value: Any) -> float | None:
    return float(value) / 1000.0 if isinstance(value, (int, float)) else None


def urllib_parse_quote(value: str, safe: str = "") -> str:
    from urllib.parse import quote

    return quote(value, safe=safe)
