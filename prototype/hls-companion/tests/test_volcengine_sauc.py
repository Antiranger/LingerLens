"""Fake-WebSocket protocol tests for the volcengine-sauc ASR adapter.

Fixtures mirror the official Volcano Engine Doubao large-model streaming ASR
"v3 sauc" binary protocol:
https://docs.volcengine.com/docs/6561/1354869 (protocol)
https://www.volcengine.com/docs/6561/1395846 (SDK endpoint reference)

Framing under test (all integers big-endian):

* 4-byte header: byte0 ``0x11`` (version 0b0001 | header size 1x4B);
  byte1 high nibble message type (0b0001 full client request, 0b0010 audio
  only, 0b1001 full server response, 0b1111 error), low nibble flags
  (0b0001 sequence present, 0b0010 last packet / negative sequence,
  0b0011 both); byte2 high nibble serialization (0b0001 JSON), low nibble
  compression (0b0000 none / 0b0001 gzip); byte3 reserved.
* 4-byte payload size between header/sequence and payload.
* Server responses carry a 4-byte sequence after the header; error frames
  carry a 4-byte code + JSON message payload.
* Final audio packet: flags 0b0011 negative-sequence audio frame; the server
  answers with a flags-0b0011 final response.

Result mapping: ``utterances[].definite == true`` -> final,
``false`` -> interim; ``start_time``/``end_time`` are milliseconds.
Official limitation kept: the streaming endpoints do NOT accept
``audio.language`` (Japanese needs bigmodel_nostream, out of scope), so this
adapter never claims Japanese.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import struct
import sys
import unittest
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.providers import create_asr
from companion.providers.base import SourceLanguagePolicy

# Framing constants mirroring the official protocol definition.
HEADER_SIZE = 4
PROTOCOL_VERSION = 0b0001
HEADER_SIZE_WORDS = 0b0001
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


def client_header(message_type: int, flags: int = 0, serialization: int = SERIALIZATION_JSON, compression: int = COMPRESSION_NONE) -> bytes:
    byte0 = (PROTOCOL_VERSION << 4) | HEADER_SIZE_WORDS
    byte1 = (message_type << 4) | flags
    byte2 = (serialization << 4) | compression
    return bytes([byte0, byte1, byte2, 0x00])


def parse_server_frame(data: bytes) -> dict[str, Any]:
    """Parse a server frame exactly per the documented layout."""
    byte0, byte1, byte2, _ = data[:4]
    message_type = byte1 >> 4
    flags = byte1 & 0x0F
    compression = byte2 & 0x0F
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


def full_server_response(payload: dict[str, Any], *, flags: int = FLAG_WITH_SEQUENCE, sequence: int = 1, compression: int = COMPRESSION_NONE) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    if compression == COMPRESSION_GZIP:
        body = gzip.compress(body)
    byte0 = (PROTOCOL_VERSION << 4) | HEADER_SIZE_WORDS
    byte1 = (MSG_FULL_SERVER_RESPONSE << 4) | flags
    byte2 = (SERIALIZATION_JSON << 4) | compression
    header = bytes([byte0, byte1, byte2, 0x00])
    parts = [header]
    if flags & FLAG_WITH_SEQUENCE:
        parts.append(struct.pack(">i", sequence))
    parts.append(struct.pack(">I", len(body)))
    parts.append(body)
    return b"".join(parts)


def error_response(code: int, message: str) -> bytes:
    body = json.dumps({"message": message}).encode("utf-8")
    byte1 = (MSG_ERROR_RESPONSE << 4) | FLAG_WITH_SEQUENCE
    header = bytes([(PROTOCOL_VERSION << 4) | HEADER_SIZE_WORDS, byte1, (SERIALIZATION_JSON << 4) | COMPRESSION_NONE, 0x00])
    return header + struct.pack(">i", 0) + struct.pack(">I", 4 + len(body)) + struct.pack(">I", code) + body


def result_payload(text: str, utterances: list[dict[str, Any]], duration_ms: int = 4000) -> dict[str, Any]:
    return {"audio_info": {"duration": duration_ms}, "result": {"text": text, "utterances": utterances}}


def utterance(text: str, start_ms: int, end_ms: int, *, definite: bool, words: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"definite": definite, "start_time": start_ms, "end_time": end_ms, "text": text}
    if words is not None:
        item["words"] = words
    return item


class FakeVolcengineServer:
    def __init__(self, *, gzip_response: bool = False) -> None:
        self.handshakes: list[dict[str, Any]] = []
        self.client_frames: list[dict[str, Any]] = []
        self.audio = bytearray()
        self.last_audio_flags: int | None = None
        self.outbound: asyncio.Queue[bytes | str] = asyncio.Queue()
        self.gzip_response = gzip_response
        self.runner: web.AppRunner | None = None
        self._ws: web.WebSocketResponse | None = None
        self.url = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/api/v3/sauc/bigmodel_async", self._handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/api/v3/sauc/bigmodel_async"

    async def stop(self) -> None:
        assert self.runner is not None
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        await self.runner.cleanup()

    async def _handler(self, request: web.Request) -> web.WebSocketResponse:
        self.handshakes.append({key: request.headers.get(key) for key in (
            "X-Api-Key", "X-Api-Resource-Id", "X-Api-Request-Id", "X-Api-Sequence",
            "X-Api-App-Key", "X-Api-Access-Key",
        )})
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws = ws

        async def writer() -> None:
            while True:
                payload = await self.outbound.get()
                if payload == "CLOSE":
                    await ws.close()
                    return
                await ws.send_bytes(payload)

        task = asyncio.create_task(writer())
        sequence = 0
        try:
            async for message in ws:
                if message.type != aiohttp.WSMsgType.BINARY:
                    continue
                byte1 = message.data[1]
                message_type = byte1 >> 4
                flags = byte1 & 0x0F
                compression = message.data[2] & 0x0F
                offset = 4
                if flags & FLAG_WITH_SEQUENCE:
                    offset += 4
                (payload_size,) = struct.unpack_from(">I", message.data, offset)
                payload = message.data[offset + 4:offset + 4 + payload_size]
                if compression == COMPRESSION_GZIP:
                    payload = gzip.decompress(payload)
                self.client_frames.append({"type": message_type, "flags": flags})
                if message_type == MSG_FULL_CLIENT_REQUEST:
                    self.start_requests = self.start_requests if hasattr(self, "start_requests") else []
                    self.start_requests.append(json.loads(payload))
                elif message_type == MSG_AUDIO_ONLY_REQUEST:
                    if flags & FLAG_LAST_PACKET:
                        self.last_audio_flags = flags
                        # Final response: flags 0b0011 (sequence + last).
                        await ws.send_bytes(full_server_response(
                            result_payload("已结束。", [utterance("已结束。", 0, 4000, definite=True)]),
                            flags=FLAG_WITH_SEQUENCE | FLAG_LAST_PACKET,
                            sequence=-1,
                            compression=COMPRESSION_GZIP if self.gzip_response else COMPRESSION_NONE,
                        ))
                        continue
                    self.audio.extend(payload)
                    sequence += 1
                    gzip_mode = COMPRESSION_GZIP if self.gzip_response else COMPRESSION_NONE
                    await ws.send_bytes(full_server_response(
                        result_payload("测试", [utterance("测试", 0, 1000, definite=False)]),
                        flags=FLAG_WITH_SEQUENCE,
                        sequence=sequence,
                        compression=gzip_mode,
                    ))
        finally:
            task.cancel()
        return ws

    async def send(self, payload: bytes | str) -> None:
        await self.outbound.put(payload)

    async def wait_for(self, check, timeout: float = 2.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while not check():
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError("fake server condition not met")
            await asyncio.sleep(0.01)


class VolcengineSaucTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeVolcengineServer()
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    def provider(self, **options):
        options.setdefault("resourceId", "volc.bigasr.sauc.concurrent")
        return create_asr({
            "id": "volc-test",
            "kind": "volcengine-sauc",
            "model": "bigmodel_async",
            "baseUrl": self.server.url,
            "apiKey": "fake-key",
            "options": options,
        })

    async def test_handshake_headers_and_session_start_framing(self) -> None:
        stream = await self.provider(enableItn=True, endWindowSize=800).stream(
            policy=SourceLanguagePolicy.specified("zh"), sample_rate=16000, hotwords=[], context=[],
        )
        await stream.push_pcm(b"\x01\x00" * 3200, 0.0)
        await self.server.wait_for(lambda: len(self.server.audio) >= 6400)

        handshake = self.server.handshakes[0]
        self.assertEqual(handshake["X-Api-Key"], "fake-key")
        self.assertEqual(handshake["X-Api-Resource-Id"], "volc.bigasr.sauc.concurrent")
        self.assertRegex(handshake["X-Api-Request-Id"] or "", r"^[0-9a-f-]{36}$")
        self.assertEqual(handshake["X-Api-Sequence"], "-1")

        start = self.server.start_requests[0]
        self.assertEqual(start["audio"], {"format": "pcm", "rate": 16000, "bits": 16, "channel": 1, "codec": "raw"})
        self.assertEqual(start["request"]["model_name"], "bigmodel")
        self.assertTrue(start["request"]["show_utterances"])
        self.assertEqual(start["request"]["result_type"], "full")
        self.assertEqual(start["request"]["end_window_size"], 800)
        # Streaming sauc does NOT accept audio.language (Japanese needs
        # bigmodel_nostream) -- the adapter must not send it.
        self.assertNotIn("language", start["audio"])
        await stream.aclose()

    async def test_utterance_definite_mapping_and_gzip_response(self) -> None:
        # Fresh server returning gzip-compressed responses.
        await self.server.stop()
        self.server = FakeVolcengineServer(gzip_response=True)
        await self.server.start()
        self.provider  # keep symmetry
        provider = self.provider()
        # Point at the new server instance.
        provider = create_asr({
            "id": "volc-test-gzip", "kind": "volcengine-sauc", "model": "bigmodel_async",
            "baseUrl": self.server.url, "apiKey": "fake-key",
            "options": {"resourceId": "volc.bigasr.sauc.concurrent"},
        })
        stream = await provider.stream(
            policy=SourceLanguagePolicy.specified("zh"), sample_rate=16000, hotwords=[], context=[],
        )
        iterator = stream.__aiter__()
        await stream.push_pcm(b"\x01\x00" * 3200, 0.0)
        started = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(started.type, "speech_started")
        self.assertEqual(started.item_id, "0")
        interim = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(interim.type, "interim")
        self.assertEqual(interim.text, "测试")
        self.assertAlmostEqual(interim.begin_pcm, 0.0)
        self.assertAlmostEqual(interim.end_pcm, 1.0)  # ms -> s

        # A response flipping the utterance to definite finalizes it once.
        await self.server.send(full_server_response(
            result_payload("这是字节跳动，", [
                utterance("这是字节跳动，", 0, 1705, definite=True, words=[
                    {"text": "这", "start_time": 740, "end_time": 860, "blank_duration": 0},
                ]),
            ]),
            flags=FLAG_WITH_SEQUENCE, sequence=99,
        ))
        # The interim already opened the utterance span, so the definite
        # response only adds speech_stopped + final (no duplicate start).
        events = [await asyncio.wait_for(iterator.__anext__(), 2) for _ in range(2)]
        self.assertEqual(events[0].type, "speech_stopped")
        self.assertEqual(events[0].item_id, "0")
        self.assertAlmostEqual(events[0].end_pcm, 1.705)
        final = events[1]
        self.assertEqual(final.type, "final")
        self.assertEqual(final.text, "这是字节跳动，")
        self.assertEqual(final.item_id, "0")

        # Repeating the same definite utterance must NOT re-emit a final.
        await self.server.send(full_server_response(
            result_payload("这是字节跳动，", [
                utterance("这是字节跳动，", 0, 1705, definite=True),
                utterance("今日头条母公司。", 2110, 3696, definite=False),
            ]),
            flags=FLAG_WITH_SEQUENCE, sequence=100,
        ))
        started2 = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(started2.type, "speech_started")
        self.assertEqual(started2.item_id, "2110")
        interim2 = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(interim2.type, "interim")
        self.assertEqual(interim2.text, "今日头条母公司。")
        self.assertEqual(interim2.item_id, "2110")

        # Second utterance becoming definite produces its own final.
        await self.server.send(full_server_response(
            result_payload("这是字节跳动，今日头条母公司。", [
                utterance("这是字节跳动，", 0, 1705, definite=True),
                utterance("今日头条母公司。", 2110, 3696, definite=True),
            ]),
            flags=FLAG_WITH_SEQUENCE | FLAG_LAST_PACKET, sequence=-1,
        ))
        # Already started via the interim path: definite adds stop + final.
        events2 = [await asyncio.wait_for(iterator.__anext__(), 2) for _ in range(2)]
        self.assertEqual(events2[0].type, "speech_stopped")
        self.assertEqual(events2[1].type, "final")
        self.assertEqual(events2[1].text, "今日头条母公司。")
        self.assertEqual(events2[1].item_id, "2110")
        await stream.aclose()

    async def test_last_packet_uses_negative_sequence_and_final_response(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("zh"), sample_rate=16000, hotwords=[], context=[],
        )
        iterator = stream.__aiter__()
        await stream.push_pcm(b"\x01\x00" * 3200, 0.0)
        started = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(started.type, "speech_started")
        await asyncio.wait_for(iterator.__anext__(), 2)  # interim for the auto response
        close_task = asyncio.create_task(stream.aclose())
        await self.server.wait_for(lambda: self.server.last_audio_flags is not None)
        self.assertEqual(self.server.last_audio_flags, FLAG_WITH_SEQUENCE | FLAG_LAST_PACKET)
        # The final response (flags 0b0011) is drained while aclose waits.
        stopped = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(stopped.type, "speech_stopped")
        final = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(final.type, "final")
        self.assertEqual(final.text, "已结束。")
        await asyncio.wait_for(close_task, 3)
        await self.server.wait_for(lambda: self.server._ws.closed)

    async def test_error_frame_maps_to_error_event(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("zh"), sample_rate=16000, hotwords=[], context=[],
        )
        iterator = stream.__aiter__()
        await self.server.send(error_response(45000151, "audio format incorrect"))
        error = await asyncio.wait_for(iterator.__anext__(), 2)
        self.assertEqual(error.type, "error")
        self.assertIn("45000151", error.message)
        self.assertIn("audio format incorrect", error.message)
        await stream.aclose()

    async def test_legacy_auth_mode_headers(self) -> None:
        provider = self.provider(authMode="legacy", appKey="app-123")
        # Rebuild with the app key as the access credential (legacy console).
        provider = create_asr({
            "id": "volc-legacy", "kind": "volcengine-sauc", "model": "bigmodel_async",
            "baseUrl": self.server.url, "apiKey": "access-token",
            "options": {"authMode": "legacy", "appKey": "app-123"},
        })
        stream = await provider.stream(
            policy=SourceLanguagePolicy.specified("zh"), sample_rate=16000, hotwords=[], context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.handshakes))
        handshake = self.server.handshakes[0]
        self.assertEqual(handshake["X-Api-App-Key"], "app-123")
        self.assertEqual(handshake["X-Api-Access-Key"], "access-token")
        self.assertIsNone(handshake["X-Api-Key"])
        await stream.aclose()

    async def test_capabilities_declare_zh_en_only(self) -> None:
        caps = self.provider().capabilities
        language = caps.language
        self.assertEqual(language.detection, "none")
        # Official: streaming accepts no audio.language; Japanese has no
        # contract on the streaming endpoints (bigmodel_nostream only).
        self.assertNotIn("ja", language.supported_tags or ())
        self.assertEqual(language.tier, "provider_claimed")
        self.assertTrue(caps.word_timestamps)
        self.assertFalse(caps.speaker_labels)  # response speaker fields undocumented

    async def test_missing_key_raises_before_handshake(self) -> None:
        provider = create_asr({
            "id": "volc-nokey", "kind": "volcengine-sauc", "model": "bigmodel_async",
            "baseUrl": self.server.url, "apiKey": "", "options": {},
        })
        with self.assertRaises(ValueError):
            await provider.stream(
                policy=SourceLanguagePolicy.specified("zh"), sample_rate=16000, hotwords=[], context=[],
            )


if __name__ == "__main__":
    unittest.main()
