from __future__ import annotations

import asyncio
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

# Model-level presets. Capability metadata is per model, never per kind:
# "max_hints" is the official language_hints limit (qwen-audio-3.0 allows up
# to 4, Fun-ASR only 1), "instant_vocabulary" marks models supporting the
# instant "vocabulary" hotword parameter, and "context" marks models whose
# docs list input.context dialogue support. Unknown custom models fall back
# to the experimental preset, which never claims detection or code-switching.
# Language lists from the current official DashScope model docs (2026):
# https://www.alibabacloud.com/help/en/model-studio/asr-model and
# https://www.alibabacloud.com/help/en/model-studio/fun-asr-client-events
_FUN_ASR_MAIN_LANGUAGES = (
    "zh", "en", "ja", "ko", "vi", "th", "id", "ms", "tl", "hi", "ar", "fr",
    "de", "es", "pt", "ru", "it", "nl", "sv", "da", "fi", "no", "el", "pl",
    "cs", "hu", "ro", "bg", "hr", "sk",
)

_MODEL_PRESETS: dict[str, dict[str, Any]] = {
    "fun-asr-realtime-2026-02-28": {
        "tier": "verified", "languages": ("zh", "en", "ja"),
        "max_hints": 1, "instant_vocabulary": False, "context": False,
    },
    "fun-asr-realtime": {
        "tier": "provider_claimed", "languages": _FUN_ASR_MAIN_LANGUAGES,
        "max_hints": 1, "instant_vocabulary": False, "context": True,
    },
    "fun-asr-realtime-2025-11-07": {
        "tier": "provider_claimed", "languages": _FUN_ASR_MAIN_LANGUAGES,
        "max_hints": 1, "instant_vocabulary": False, "context": True,
    },
    "fun-asr-realtime-2025-09-15": {
        "tier": "provider_claimed", "languages": ("zh", "en"),
        "max_hints": 1, "instant_vocabulary": False, "context": False,
    },
    "qwen-audio-3.0-asr-flash-streaming": {
        "tier": "provider_claimed", "languages": _FUN_ASR_MAIN_LANGUAGES,
        "max_hints": 4, "instant_vocabulary": True, "context": True,
    },
    "paraformer-realtime-v2": {
        "tier": "provider_claimed", "languages": ("zh", "en", "ja", "ko"),
        "max_hints": 1, "instant_vocabulary": False, "context": False,
    },
}


def _canonical_tags(tags: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(tag for tag in (canonicalize_tag_or_none(item) for item in tags) if tag)


@register("dashscope-task-asr")
class DashScopeTaskASRProvider(ASRProvider):
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
        sample_rate = int(self.options.get("sampleRate", 16000))
        languages = tuple(self.options.get("languages", ("zh", "en", "ja", "ko")))
        preset = _MODEL_PRESETS.get(self.model, {})
        max_hints = int(preset.get("max_hints", 1))
        language = ASRLanguageCapabilities(
            supported_tags=_canonical_tags(tuple(preset.get("languages", languages))),
            # qwen-audio-3.0 detects within up to 4 candidate hints; Fun-ASR
            # models accept exactly one fixed hint; others specified-only.
            detection="candidates" if max_hints > 1 else "none",
            max_candidates=max_hints if max_hints > 1 else None,
            reports_detected_language=False,
            tier=preset.get("tier", "experimental"),
        )
        # Hotwords: the undocumented "parameters.hotwords" key is gone. What
        # remains is the official per-model surface: instant "vocabulary"
        # (qwen-audio-3.0 only) and the pre-created "vocabulary_id" reference.
        hotwords = bool(self.options.get("vocabulary") or self.options.get("vocabularyId"))
        context = bool(preset.get("context", False)) and bool(self.options.get("contextEnabled", True))
        return ASRCapabilities(
            True,
            True,
            False,  # task-ASR interims are mutable whole-sentence hypotheses
            True,
            True,
            hotwords,
            context,
            _canonical_tags(languages),
            (sample_rate,),
            language=language,
            preferred_sample_rate=sample_rate,
            caption_evidence=frozenset({"text_snapshot", "utterance_final"}),
        )

    async def stream(self, *, policy: SourceLanguagePolicy, sample_rate: int, hotwords: list[str], context: list[str]) -> ASRStream:
        del hotwords  # see capabilities.hotwords: vocabulary comes from options
        if not self.api_key:
            raise ValueError(f"API key is not configured for provider {self.id}")
        preset = _MODEL_PRESETS.get(self.model, {})
        max_hints = int(preset.get("max_hints", 1))
        hints: list[str]
        if policy.mode == "specified":
            assert policy.tag is not None
            hints = [primary_subtag(policy.tag)]
        elif max_hints > 1 and policy.candidates:
            if len(policy.candidates) > max_hints:
                raise LanguageNotSupportedError(
                    f"this model accepts at most {max_hints} language_hints candidates"
                )
            hints = list(dict.fromkeys(primary_subtag(tag) for tag in policy.candidates))
        else:
            raise LanguageNotSupportedError("DashScope task ASR requires a specified source language")
        stream = _DashScopeTaskStream(self, hints, sample_rate, context)
        await stream.connect()
        return stream


class _DashScopeTaskStream(ASRStream):
    def __init__(self, provider: DashScopeTaskASRProvider, language_hints: list[str], sample_rate: int, context: list[str]):
        self.provider = provider
        self.language_hints = language_hints
        self.sample_rate = sample_rate
        self.context = context
        # DashScope documents a UUID-form task id (with hyphens), not uuid.hex.
        self.task_id = str(uuid.uuid4())
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None

    async def connect(self) -> None:
        # Everything that can fail lives inside this try. A failed handshake is
        # the common case in production -- _asr_manager retries forever with
        # backoff (subtitle_pipeline.py:713-756) -- and this adapter previously
        # guarded only the task-started wait, so a failure in ws_connect or the
        # first send_json left the ClientSession and its TCPConnector open
        # forever: measured 40 failed handshakes -> 40 unclosed sessions.
        try:
            self.session = aiohttp.ClientSession()
            self.ws = await self.session.ws_connect(
                self.provider.base_url,
                headers={"Authorization": f"Bearer {self.provider.api_key}", "X-DashScope-DataInspection": "enable"},
                **proxy_kwargs(self.provider.base_url),
            )
            options = self.provider.options
            parameters: dict[str, Any] = {
                "format": "pcm",
                "sample_rate": self.sample_rate,
                "language_hints": self.language_hints,
                "semantic_punctuation_enabled": bool(options.get("semanticPunctuationEnabled", False)),
                "max_sentence_silence": int(options.get("maxSentenceSilence", 800)),
            }
            if "multiThresholdModeEnabled" in options:
                parameters["multi_threshold_mode_enabled"] = bool(options["multiThresholdModeEnabled"])
            if "heartbeat" in options:
                parameters["heartbeat"] = bool(options["heartbeat"])
            if "speechNoiseThreshold" in options:
                parameters["speech_noise_threshold"] = float(options["speechNoiseThreshold"])
            # Official hotword surface only: pre-created vocabulary tables by id,
            # and instant "vocabulary" for the models that document it (both are
            # passed through verbatim; the adapter invents no hotword schema).
            if options.get("vocabularyId"):
                parameters["vocabulary_id"] = str(options["vocabularyId"])
            if options.get("vocabulary") is not None:
                parameters["vocabulary"] = options["vocabulary"]
            payload_input: dict[str, Any] = {}
            preset = _MODEL_PRESETS.get(self.provider.model, {})
            if self.context and preset.get("context", False) and options.get("contextEnabled", True):
                # Official docs place dialogue context under input.context (max 5
                # entries) for the models that support it -- NOT under parameters.
                payload_input["context"] = self.context[-5:]
            await self.ws.send_json({
                "header": {"action": "run-task", "task_id": self.task_id, "streaming": "duplex"},
                "payload": {"task_group": "audio", "task": "asr", "function": "recognition", "model": self.provider.model, "parameters": parameters, "input": payload_input},
            })
            # Official contract: audio may only be sent after task-started. Waiting
            # here also prevents the first live PCM chunks from racing the server.
            await asyncio.wait_for(
                self._wait_for_task_started(),
                timeout=float(options.get("startTimeoutSeconds", 10)),
            )
        except BaseException:
            await self.aclose()
            raise

    async def push_pcm(self, chunk: bytes, pcm_offset: float) -> None:
        del pcm_offset
        if self.ws and not self.ws.closed:
            await self.ws.send_bytes(chunk)

    async def flush(self) -> None:
        if self.ws and not self.ws.closed:
            await self.ws.send_json({"header": {"action": "finish-task", "task_id": self.task_id, "streaming": "duplex"}, "payload": {"input": {}}})

    async def _wait_for_task_started(self) -> None:
        assert self.ws is not None
        while True:
            message = await self.ws.receive()
            if message.type == aiohttp.WSMsgType.TEXT:
                try:
                    raw = json.loads(message.data)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"invalid DashScope task-start response: {exc}") from exc
                event_name = self._event_name(raw)
                if event_name == "task-started":
                    return
                if event_name in {"task-failed", "error"}:
                    raise RuntimeError(self._error_message(raw))
            elif message.type in {aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE}:
                raise RuntimeError(str(self.ws.exception() or "DashScope task stream closed before task-started"))

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
                if event:
                    sentence = self._sentence(raw)
                    # Fun-ASR reports boundaries inside result-generated. Expose
                    # them as provider-neutral VAD events before the transcript
                    # so SubtitlePipeline can join exact begin/end timestamps.
                    if sentence and sentence.get("sentence_begin") and event.begin_pcm is not None:
                        yield ASREvent("speech_started", begin_pcm=event.begin_pcm, item_id=event.item_id, raw=raw)
                    if event.type == "final" and event.end_pcm is not None:
                        yield ASREvent("speech_stopped", end_pcm=event.end_pcm, item_id=event.item_id, raw=raw)
                    yield event
            elif message.type == aiohttp.WSMsgType.ERROR:
                yield ASREvent("error", message=str(self.ws.exception()))
                break

    def __aiter__(self) -> AsyncIterator[ASREvent]:
        return self._events()

    @staticmethod
    def _event_name(raw: dict[str, Any]) -> str:
        header = raw.get("header")
        return str((header.get("event") if isinstance(header, dict) else None) or raw.get("event") or "")

    @staticmethod
    def _sentence(raw: dict[str, Any]) -> dict[str, Any] | None:
        sentence = raw.get("payload", {}).get("output", {}).get("sentence") or raw.get("sentence")
        return sentence if isinstance(sentence, dict) else None

    @staticmethod
    def _error_message(raw: dict[str, Any]) -> str:
        header = raw.get("header") if isinstance(raw.get("header"), dict) else raw
        return str(header.get("error_message") or header.get("message") or header.get("error_code") or "ASR task failed")

    @classmethod
    def _map_event(cls, raw: dict[str, Any]) -> ASREvent | None:
        event_name = cls._event_name(raw)
        if event_name in {"task-failed", "error"}:
            return ASREvent("error", message=cls._error_message(raw), raw=raw)
        sentence = cls._sentence(raw)
        if not sentence or sentence.get("heartbeat") is True:
            return None
        final = bool(sentence.get("sentence_end"))
        sentence_id = sentence.get("sentence_id")
        text = str(sentence.get("text", ""))
        begin = _milliseconds(sentence.get("begin_time"))
        end = _milliseconds(sentence.get("end_time"))
        item_id = str(sentence_id) if sentence_id is not None else "0"
        return ASREvent(
            "final" if final else "interim",
            text=text,
            begin_pcm=begin,
            end_pcm=end,
            item_id=item_id,
            raw=raw,
            caption_observation=CaptionObservation(
                "utterance_final" if final else "text_snapshot", 0, item_id,
                stable_text=text if final else "", tentative_text="" if final else text,
                begin_pcm=begin, end_pcm=end,
            ),
        )

    async def aclose(self) -> None:
        if self.ws and not self.ws.closed:
            await self.ws.close()
        if self.session:
            await self.session.close()


def _milliseconds(value: Any) -> float | None:
    return float(value) / 1000.0 if isinstance(value, (int, float)) else None
