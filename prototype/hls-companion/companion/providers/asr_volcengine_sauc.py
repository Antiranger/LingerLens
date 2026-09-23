"""Volcano Engine Doubao large-model streaming ASR adapter (``volcengine-sauc``).

Implements the official "v3 sauc" binary bidirectional WebSocket protocol for
``wss://openspeech.bytedance.com/api/v3/sauc/bigmodel*`` with plain aiohttp
(this is NOT a plain JSON WebSocket: every frame is 4-byte header + optional
sequence + 4-byte payload size + payload, all integers big-endian).
Sources (read 2026-09, see docs/research/realtime-stt-provider-protocol-audit-2026.md):

* Protocol: https://docs.volcengine.com/docs/6561/1354869
* SDK endpoint reference: https://www.volcengine.com/docs/6561/1395846

Framing summary:

* byte0 ``0x11`` = protocol version 0b0001 | header size 0b0001 (x4 bytes);
* byte1 = message type << 4 | flags: types ``0b0001`` full client request
  (session-start JSON), ``0b0010`` audio-only, ``0b1001`` full server
  response, ``0b1111`` error; flags ``0b0001`` sequence field present,
  ``0b0010`` last/negative packet, ``0b0011`` both;
* byte2 = serialization << 4 | compression (JSON 0b0001; none 0b0000 /
  gzip 0b0001); byte3 reserved;
* server responses carry a 4-byte sequence after the header (per flags);
  error payloads are a 4-byte code + JSON message.

Result mapping: ``result.utterances[].definite`` (bool) is the sentence
terminality field (there is no ``is_final``); ``start_time``/``end_time`` are
milliseconds; definite utterances become finals exactly once (tracked by
start_time), indefinite ones are covering interims.

Official limitation kept (never claimed away): the streaming endpoints
(``bigmodel``/``bigmodel_async``) do NOT accept ``audio.language`` -- default
coverage is Chinese/English + dialects. Japanese has an official contract
only on ``bigmodel_nostream`` (a different, non-streaming mode), so this
adapter never sends a language and never claims Japanese.

Auth (handshake headers, two official console modes):
* new console: ``X-Api-Key`` + ``X-Api-Resource-Id`` + ``X-Api-Request-Id``
  (UUID) + ``X-Api-Sequence: -1``;
* legacy console: ``X-Api-App-Key`` (APP ID, ``options.appKey``) and
  ``X-Api-Access-Key`` (the provider apiKey holds the Access Token).

The adapter owns exactly one session and its stop framing (negative-sequence
last audio packet); the SubtitlePipeline remains the only reconnect owner.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import struct
import time
import uuid
from typing import Any, AsyncIterator

import aiohttp

from . import register
from .bounded import RecentIds, NumericTombstones
from ..languages import LanguageNotSupportedError
from .base import (
    ASRCapabilities,
    ASREvent,
    ASRLanguageCapabilities,
    ASRProvider,
    ASRStream,
    CaptionObservation,
    SourceLanguagePolicy,
)

# Protocol constants (official v3 sauc framing).
_PROTOCOL_VERSION = 0b0001
_HEADER_SIZE_WORDS = 0b0001
MSG_FULL_CLIENT_REQUEST = 0b0001
MSG_AUDIO_ONLY_REQUEST = 0b0010
MSG_FULL_SERVER_RESPONSE = 0b1001
MSG_ERROR_RESPONSE = 0b1111
FLAG_WITH_SEQUENCE = 0b0001
FLAG_LAST_PACKET = 0b0010
SERIALIZATION_JSON = 0b0001
SERIALIZATION_NONE = 0b0000
COMPRESSION_NONE = 0b0000
COMPRESSION_GZIP = 0b0001

_ERROR_CODES_SUCCESS = 0x20000000


def _frame(message_type: int, flags: int, serialization: int, compression: int, sequence: int | None, payload: bytes) -> bytes:
    header = bytes([
        (_PROTOCOL_VERSION << 4) | _HEADER_SIZE_WORDS,
        (message_type << 4) | flags,
        (serialization << 4) | compression,
        0x00,
    ])
    parts = [header]
    if flags & FLAG_WITH_SEQUENCE:
        parts.append(struct.pack(">i", sequence if sequence is not None else -1))
    parts.append(struct.pack(">I", len(payload)))
    parts.append(payload)
    return b"".join(parts)


def _json_frame(message_type: int, body: dict[str, Any], *, flags: int = 0, sequence: int | None = None) -> bytes:
    return _frame(message_type, flags, SERIALIZATION_JSON, COMPRESSION_NONE, sequence, json.dumps(body).encode("utf-8"))


@register("volcengine-sauc")
class VolcengineSaucASRProvider(ASRProvider):
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
        if self.model in {"bigmodel", "bigmodel_async"}:
            # Official default coverage for the streaming sauc endpoints:
            # Chinese/English + dialects; no language parameter accepted.
            language = ASRLanguageCapabilities(
                supported_tags=("zh", "en"),
                detection="none",
                reports_detected_language=False,
                code_switching=False,
                tier="provider_claimed",
            )
            languages = ("zh", "en")
        else:
            language = ASRLanguageCapabilities()
            languages = ()
        return ASRCapabilities(
            True,
            True,
            False,
            True,  # server-side utterance segmentation (definite flags)
            True,  # utterances[].words carry ms timings
            False,
            False,  # corpus.context exists but is a request-body feature, not pipeline context
            languages,
            (16000,),  # rate is officially fixed at 16000
            manual_commit=False,
            language=language,
            preferred_sample_rate=16000,
            # Response speaker fields are undocumented upstream (Unknown in the
            # audit): enable_speaker_info can be requested via options but no
            # speaker metadata is mapped.
            speaker_labels=False,
            caption_evidence=frozenset({"text_snapshot", "utterance_final", "endpoint"}),
        )

    async def stream(self, *, policy: SourceLanguagePolicy, sample_rate: int, hotwords: list[str], context: list[str]) -> ASRStream:
        del hotwords, context
        if not self.api_key:
            raise ValueError(f"API key is not configured for provider {self.id}")
        if sample_rate != 16000:
            raise ValueError(f"sample rate {sample_rate} is not supported by provider {self.id} (sauc requires 16000)")
        if policy.mode == "detect":
            raise LanguageNotSupportedError(
                "the streaming sauc endpoints accept no language parameter; specify zh or en"
            )
        stream = _VolcengineSaucStream(self, policy, sample_rate)
        await stream.connect()
        return stream


class _VolcengineSaucStream(ASRStream):
    def __init__(self, provider: VolcengineSaucASRProvider, policy: SourceLanguagePolicy, sample_rate: int):
        self.provider = provider
        self.policy = policy
        self.sample_rate = sample_rate
        self.session: aiohttp.ClientSession | None = None
        self.ws: Any = None
        self.closed = False
        self._request_id = str(uuid.uuid4())
        self._finalized_starts = NumericTombstones()
        self._started_items = RecentIds()
        self._last_audio_at = time.monotonic()
        self._iter_started = False
        self._drain_done = asyncio.Event()
        self._release_done = False
        self._force_close_task: asyncio.Task | None = None
        self.drain_seconds = float(provider.options.get("closeDrainTimeoutSeconds", 2.0))

    def _headers(self) -> dict[str, str]:
        options = self.provider.options
        headers = {"X-Api-Request-Id": self._request_id, "X-Api-Sequence": "-1"}
        if str(options.get("authMode", "")) == "legacy":
            # Legacy console: APP ID + Access Token. The provider apiKey holds
            # the Access Token.
            headers["X-Api-App-Key"] = str(options.get("appKey", ""))
            headers["X-Api-Access-Key"] = self.provider.api_key
        else:
            headers["X-Api-Key"] = self.provider.api_key
            headers["X-Api-Resource-Id"] = str(options.get("resourceId", "volc.bigasr.sauc.concurrent"))
        return headers

    def _session_start(self) -> dict[str, Any]:
        options = self.provider.options
        request: dict[str, Any] = {
            "model_name": "bigmodel",
            "enable_punc": bool(options.get("enablePunc", True)),
            "show_utterances": bool(options.get("showUtterances", True)),
            "result_type": str(options.get("resultType", "full")),
        }
        for source, target in (
            ("enableItn", "enable_itn"),
            ("endWindowSize", "end_window_size"),
            ("vadSegmentDurationMs", "vad_segment_duration"),
            ("enableSpeakerInfo", "enable_speaker_info"),
            ("enableNonstream", "enable_nonstream"),
            ("outputZhVariant", "output_zh_variant"),
        ):
            if source in options and options[source] is not None:
                request[target] = options[source]
        if options.get("corpus") is not None:
            request["corpus"] = options["corpus"]
        return {
            "user": {"uid": str(options.get("uid", "lingerlens"))},
            "audio": {"format": "pcm", "rate": self.sample_rate, "bits": 16, "channel": 1, "codec": "raw"},
            "request": request,
        }

    async def connect(self) -> None:
        self.session = aiohttp.ClientSession()
        try:
            self.ws = await self.session.ws_connect(self.provider.base_url, headers=self._headers())
            # Session start: full client request with the JSON parameters.
            await self.ws.send_bytes(_json_frame(MSG_FULL_CLIENT_REQUEST, self._session_start()))
        except BaseException:
            await self._release()
            raise

    async def push_pcm(self, chunk: bytes, pcm_offset: float) -> None:
        del pcm_offset
        if not self.ws or self.ws.closed:
            return
        self._last_audio_at = time.monotonic()
        # Audio-only request: serialization none, compression none, payload is
        # raw little-endian PCM.
        await self.ws.send_bytes(_frame(MSG_AUDIO_ONLY_REQUEST, 0, SERIALIZATION_NONE, COMPRESSION_NONE, None, chunk))

    async def flush(self) -> None:
        # The protocol has no mid-stream flush control; utterance boundaries
        # are server-side (end_window_size / VAD). No-op keeps the contract.
        return None

    def __aiter__(self) -> AsyncIterator[ASREvent]:
        return self._events()

    async def _events(self) -> AsyncIterator[ASREvent]:
        self._iter_started = True
        try:
            if not self.ws:
                return
            async for message in self.ws:
                if message.type == aiohttp.WSMsgType.BINARY:
                    try:
                        events = self._map_frame(message.data)
                    except (ValueError, struct.error, gzip.BadGzipFile, json.JSONDecodeError) as exc:
                        yield ASREvent("error", message=f"invalid sauc frame: {exc}")
                        continue
                    for event in events:
                        yield event
                elif message.type == aiohttp.WSMsgType.TEXT:
                    yield ASREvent("error", message="unexpected text frame on sauc binary protocol")
                elif message.type == aiohttp.WSMsgType.ERROR:
                    yield ASREvent("error", message=str(self.ws.exception()))
                    return
        finally:
            self._drain_done.set()

    def _parse_server_frame(self, data: bytes) -> dict[str, Any]:
        if len(data) < 8:
            raise ValueError("frame shorter than header+size")
        byte1 = data[1]
        message_type = byte1 >> 4
        flags = byte1 & 0x0F
        compression = data[2] & 0x0F
        offset = 4
        sequence = None
        if flags & FLAG_WITH_SEQUENCE:
            (sequence,) = struct.unpack_from(">i", data, offset)
            offset += 4
        (payload_size,) = struct.unpack_from(">I", data, offset)
        offset += 4
        payload = data[offset:offset + payload_size]
        if compression == COMPRESSION_GZIP:
            payload = gzip.decompress(payload)
        return {"type": message_type, "flags": flags, "sequence": sequence, "payload": payload}

    def _map_frame(self, data: bytes) -> list[ASREvent]:
        parsed = self._parse_server_frame(data)
        if parsed["type"] == MSG_ERROR_RESPONSE:
            payload = parsed["payload"]
            if len(payload) < 4:
                raise ValueError("error frame without code")
            (code,) = struct.unpack_from(">I", payload, 0)
            try:
                detail = json.loads(payload[4:].decode("utf-8"))
                message = str(detail.get("message", "")) if isinstance(detail, dict) else str(detail)
            except (UnicodeDecodeError, json.JSONDecodeError):
                message = ""
            return [ASREvent("error", message=f"{code}: {message}".rstrip(": "), raw={"code": code, "message": message})]
        if parsed["type"] != MSG_FULL_SERVER_RESPONSE:
            return []
        try:
            body = json.loads(parsed["payload"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"response payload is not JSON: {exc}") from exc
        is_last = bool(parsed["flags"] & FLAG_LAST_PACKET)
        return self._map_result(body, is_last=is_last)

    def _map_result(self, body: dict[str, Any], *, is_last: bool) -> list[ASREvent]:
        result = body.get("result") or {}
        audio_info = body.get("audio_info") or {}
        duration = audio_info.get("duration")
        duration_seconds = float(duration) / 1000.0 if isinstance(duration, (int, float)) else None
        utterances = result.get("utterances")
        events: list[ASREvent] = []
        if not isinstance(utterances, list):
            # No utterance segmentation configured: the whole result.text is
            # interim, finalizing only on the last-packet response.
            text = str(result.get("text", "")).strip()
            if not text:
                return events
            if is_last:
                events.append(ASREvent(
                    "final", text=text, begin_pcm=0.0, end_pcm=duration_seconds,
                    language=None, item_id="0", raw=body,
                    caption_observation=CaptionObservation(
                        "utterance_final", 0, "0", stable_text=text,
                        begin_pcm=0.0, end_pcm=duration_seconds,
                    ),
                ))
            else:
                events.append(ASREvent(
                    "interim", text=text, begin_pcm=0.0, end_pcm=duration_seconds,
                    language=None, item_id="0", raw=body,
                    caption_observation=CaptionObservation(
                        "text_snapshot", 0, "0", tentative_text=text,
                        begin_pcm=0.0, end_pcm=duration_seconds,
                    ),
                ))
            return events

        for utterance in utterances:
            if not isinstance(utterance, dict):
                continue
            start_time = utterance.get("start_time")
            end_time = utterance.get("end_time")
            text = str(utterance.get("text", "")).strip()
            begin_pcm = float(start_time) / 1000.0 if isinstance(start_time, (int, float)) else None
            end_pcm = float(end_time) / 1000.0 if isinstance(end_time, (int, float)) else None
            item_id = str(start_time) if isinstance(start_time, (int, float)) else "0"
            definite = bool(utterance.get("definite"))
            if definite and item_id not in self._finalized_starts:
                # definite=true is the official sentence terminal; emit exactly
                # one final per utterance (responses repeat the full list).
                self._finalized_starts.add(item_id)
                if begin_pcm is not None and item_id not in self._started_items:
                    self._started_items.add(item_id)
                    events.append(ASREvent("speech_started", begin_pcm=begin_pcm, item_id=item_id, raw=body))
                if end_pcm is not None:
                    events.append(ASREvent(
                        "speech_stopped", end_pcm=end_pcm, item_id=item_id, raw=body,
                        caption_observation=CaptionObservation("endpoint", 0, item_id, end_pcm=end_pcm),
                    ))
                if text:
                    events.append(ASREvent(
                        "final", text=text, begin_pcm=begin_pcm, end_pcm=end_pcm,
                        language=None, item_id=item_id, raw=body,
                        caption_observation=CaptionObservation(
                            "utterance_final", 0, item_id, stable_text=text,
                            begin_pcm=begin_pcm, end_pcm=end_pcm,
                        ),
                    ))
            elif not definite and text:
                # Covering interim for the utterance still being recognized.
                if begin_pcm is not None and item_id not in self._started_items:
                    self._started_items.add(item_id)
                    events.append(ASREvent("speech_started", begin_pcm=begin_pcm, item_id=item_id, raw=body))
                events.append(ASREvent(
                    "interim", text=text, begin_pcm=begin_pcm, end_pcm=end_pcm,
                    language=None, item_id=item_id, raw=body,
                    caption_observation=CaptionObservation(
                        "text_snapshot", 0, item_id, tentative_text=text,
                        begin_pcm=begin_pcm, end_pcm=end_pcm,
                    ),
                ))
        return events

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
        if self.ws and not self.ws.closed:
            # Official stop: negative-sequence final audio packet; drain the
            # server's flags-0b0011 final response before releasing resources.
            with contextlib.suppress(aiohttp.ClientError, RuntimeError):
                await self.ws.send_bytes(_frame(
                    MSG_AUDIO_ONLY_REQUEST,
                    FLAG_WITH_SEQUENCE | FLAG_LAST_PACKET,
                    SERIALIZATION_NONE,
                    COMPRESSION_NONE,
                    -1,
                    b"",
                ))
        if self._iter_started and not self._drain_done.is_set():
            try:
                await asyncio.wait_for(self._drain_done.wait(), timeout=self.drain_seconds)
            except asyncio.TimeoutError:
                pass
        await self._release()
