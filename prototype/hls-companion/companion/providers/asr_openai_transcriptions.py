from __future__ import annotations

import asyncio
import io
import json
import wave
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from . import register
from .base import ASRCapabilities, ASREvent, ASRProvider, ASRStream


@register("openai-audio-transcriptions")
class OpenAITranscriptionASRProvider(ASRProvider):
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
        return ASRCapabilities(False, False, False, False, False, False, False, (), (16000,))

    async def stream(self, *, language: str, hotwords: list[str], context: list[str]) -> ASRStream:
        del hotwords, context
        return _OpenAITranscriptionStream(self, language)


class _OpenAITranscriptionStream(ASRStream):
    def __init__(self, provider: OpenAITranscriptionASRProvider, language: str):
        self.provider = provider
        self.language = language
        self.sample_rate = int(provider.options.get("sampleRate", 16000))
        self.window_bytes = max(2, int(float(provider.options.get("windowSeconds", 5.0)) * self.sample_rate * 2))
        self.max_pending = max(1, int(provider.options.get("maxPendingWindows", 2)))
        self.timeout = aiohttp.ClientTimeout(total=float(provider.options.get("requestTimeoutSeconds", 30)))
        self.buffer = bytearray()
        self.buffer_offset: float | None = None
        self.windows: asyncio.Queue[tuple[bytes, float] | None] = asyncio.Queue(maxsize=self.max_pending)
        self.events: asyncio.Queue[ASREvent | None] = asyncio.Queue()
        self.worker = asyncio.create_task(self._run(), name=f"asr-http-{provider.id}")
        self.flushed = False
        self.closed = False

    async def push_pcm(self, chunk: bytes, pcm_offset: float) -> None:
        if self.flushed or self.closed or not chunk:
            return
        if self.buffer_offset is None:
            self.buffer_offset = pcm_offset
        self.buffer.extend(chunk)
        while len(self.buffer) >= self.window_bytes:
            audio = bytes(self.buffer[: self.window_bytes])
            del self.buffer[: self.window_bytes]
            offset = self.buffer_offset
            self.buffer_offset += len(audio) / (self.sample_rate * 2)
            await self._enqueue_window(audio, offset)

    async def _enqueue_window(self, audio: bytes, offset: float) -> None:
        if self.windows.full():
            try:
                self.windows.get_nowait()
            except asyncio.QueueEmpty:
                pass
        await self.windows.put((audio, offset))

    async def flush(self) -> None:
        if self.flushed:
            return
        self.flushed = True
        if self.buffer and self.buffer_offset is not None:
            await self._enqueue_window(bytes(self.buffer), self.buffer_offset)
            self.buffer.clear()
        await self.windows.put(None)

    async def _run(self) -> None:
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            while True:
                item = await self.windows.get()
                if item is None:
                    break
                audio, offset = item
                try:
                    for event in await self._transcribe(session, audio, offset):
                        await self.events.put(event)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await self.events.put(ASREvent("error", message=f"{type(exc).__name__}: {exc}"))
        await self.events.put(None)

    async def _transcribe(self, session: aiohttp.ClientSession, audio: bytes, offset: float) -> list[ASREvent]:
        form = aiohttp.FormData()
        form.add_field("file", _wav_bytes(audio, self.sample_rate), filename="audio.wav", content_type="audio/wav")
        form.add_field("model", self.provider.model)
        if self.language:
            form.add_field("language", self.language)
        form.add_field("response_format", str(self.provider.options.get("responseFormat", "verbose_json")))
        headers = {"Authorization": f"Bearer {self.provider.api_key}"} if self.provider.api_key else {}
        async with session.post(f"{self.provider.base_url}/audio/transcriptions", data=form, headers=headers) as response:
            body = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}: {_error_message(body)}")
            try:
                payload: Any = json.loads(body)
            except json.JSONDecodeError:
                payload = body
        duration = len(audio) / (self.sample_rate * 2)
        if isinstance(payload, str):
            return [ASREvent("final", text=payload.strip(), begin_pcm=offset, end_pcm=offset + duration)] if payload.strip() else []
        segments = payload.get("segments") if isinstance(payload, dict) else None
        if isinstance(segments, list) and segments:
            events: list[ASREvent] = []
            for segment in segments:
                if not isinstance(segment, dict) or not str(segment.get("text", "")).strip():
                    continue
                start = float(segment.get("start", 0.0))
                end = float(segment.get("end", duration))
                events.append(ASREvent("final", text=str(segment["text"]).strip(), begin_pcm=offset + start, end_pcm=offset + end, language=payload.get("language"), raw=segment))
            return events
        text = str(payload.get("text", "")).strip() if isinstance(payload, dict) else ""
        return [ASREvent("final", text=text, begin_pcm=offset, end_pcm=offset + duration, language=payload.get("language"), raw=payload)] if text else []

    async def _iterate(self) -> AsyncIterator[ASREvent]:
        while True:
            event = await self.events.get()
            if event is None:
                return
            yield event

    def __aiter__(self) -> AsyncIterator[ASREvent]:
        return self._iterate()

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self.flush()
        await self.worker


def _wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return output.getvalue()


def _error_message(body: str) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body[:500]
    error = payload.get("error", payload) if isinstance(payload, dict) else payload
    if isinstance(error, dict):
        return str(error.get("message", error))
    return str(error)
