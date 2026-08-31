from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator

import aiohttp

from . import register
from .base import ASRCapabilities, ASREvent, ASRProvider, ASRStream


@register("dashscope-task-asr")
class DashScopeTaskASRProvider(ASRProvider):
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
        return ASRCapabilities(
            True,
            True,
            False,  # task-ASR interims are mutable whole-sentence hypotheses
            True,
            True,
            bool(self.options.get("hotwordsEnabled", True)),
            bool(self.options.get("contextEnabled", True)),
            languages,
            (sample_rate,),
        )

    async def stream(self, *, language: str, hotwords: list[str], context: list[str]) -> ASRStream:
        if not self.api_key:
            raise ValueError(f"API key is not configured for provider {self.id}")
        stream = _DashScopeTaskStream(self, language, hotwords, context)
        await stream.connect()
        return stream


class _DashScopeTaskStream(ASRStream):
    def __init__(self, provider: DashScopeTaskASRProvider, language: str, hotwords: list[str], context: list[str]):
        self.provider = provider
        self.language = language
        self.hotwords = hotwords
        self.context = context
        # DashScope documents a UUID-form task id (with hyphens), not uuid.hex.
        self.task_id = str(uuid.uuid4())
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None

    async def connect(self) -> None:
        self.session = aiohttp.ClientSession()
        self.ws = await self.session.ws_connect(
            self.provider.base_url,
            headers={"Authorization": f"Bearer {self.provider.api_key}", "X-DashScope-DataInspection": "enable"},
        )
        options = self.provider.options
        parameters: dict[str, Any] = {
            "format": "pcm",
            "sample_rate": int(options.get("sampleRate", 16000)),
            "language_hints": [self.language],
            "semantic_punctuation_enabled": bool(options.get("semanticPunctuationEnabled", False)),
            "max_sentence_silence": int(options.get("maxSentenceSilence", 800)),
        }
        if "multiThresholdModeEnabled" in options:
            parameters["multi_threshold_mode_enabled"] = bool(options["multiThresholdModeEnabled"])
        if "heartbeat" in options:
            parameters["heartbeat"] = bool(options["heartbeat"])
        if "speechNoiseThreshold" in options:
            parameters["speech_noise_threshold"] = float(options["speechNoiseThreshold"])
        # Fun-ASR 2026-02-28 does not support immediate hotwords or context;
        # provider presets explicitly disable them. Keep the legacy parameters
        # for task-ASR models that opt in.
        if self.hotwords and options.get("hotwordsEnabled", True):
            parameters["hotwords"] = self.hotwords
        if self.context and options.get("contextEnabled", True):
            parameters["context"] = self.context[-5:]
        await self.ws.send_json({
            "header": {"action": "run-task", "task_id": self.task_id, "streaming": "duplex"},
            "payload": {"task_group": "audio", "task": "asr", "function": "recognition", "model": self.provider.model, "parameters": parameters, "input": {}},
        })
        # Official contract: audio may only be sent after task-started. Waiting
        # here also prevents the first live PCM chunks from racing the server.
        try:
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
        return ASREvent(
            "final" if final else "interim",
            text=sentence.get("text", ""),
            begin_pcm=_milliseconds(sentence.get("begin_time")),
            end_pcm=_milliseconds(sentence.get("end_time")),
            item_id=str(sentence_id) if sentence_id is not None else None,
            raw=raw,
        )

    async def aclose(self) -> None:
        if self.ws and not self.ws.closed:
            await self.ws.close()
        if self.session:
            await self.session.close()


def _milliseconds(value: Any) -> float | None:
    return float(value) / 1000.0 if isinstance(value, (int, float)) else None
