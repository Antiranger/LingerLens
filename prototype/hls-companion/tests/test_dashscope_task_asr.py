"""Fake-WebSocket protocol tests for the dashscope-task-asr adapter.

Fixtures mirror the official DashScope/Fun-ASR/Qwen-Audio realtime WebSocket
contract (run-task -> task-started -> binary audio -> result-generated ->
finish-task/task-finished):
https://www.alibabacloud.com/help/en/model-studio/fun-asr-realtime-websocket-api
https://www.alibabacloud.com/help/en/model-studio/fun-asr-client-events
https://www.alibabacloud.com/help/en/model-studio/fun-asr-server-events

Ticket 02 audit focus:

* ``qwen-audio-3.0-asr-flash-streaming`` accepts up to 4 ``language_hints``
  and supports instant ``vocabulary`` hotwords (the previously sent
  ``parameters.hotwords`` key is NOT in the current official parameter list
  and must no longer be sent);
* dialogue ``context`` belongs in ``payload.input.context``, and only for the
  models whose official docs list it;
* Fun-ASR / Paraformer behavior and profiles are unchanged.
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.languages import LanguageNotSupportedError
from companion.providers import create_asr
from companion.providers.base import SourceLanguagePolicy, validate_source_policy


class FakeDashScopeServer:
    def __init__(self) -> None:
        self.handshakes: list[dict[str, Any]] = []
        self.start_requests: list[dict[str, Any]] = []
        self.finish_requests: list[dict[str, Any]] = []
        self.audio = bytearray()
        self.outbound: asyncio.Queue[dict[str, Any] | str] = asyncio.Queue()
        self.runner: web.AppRunner | None = None
        self._ws: web.WebSocketResponse | None = None
        self.url = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/api-ws/v1/inference", self._handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/api-ws/v1/inference"

    async def stop(self) -> None:
        assert self.runner is not None
        # Force-close the socket first: runner.cleanup() waits for handlers,
        # and a failed assertion in a test body must not hang the suite.
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        await self.runner.cleanup()

    async def _handler(self, request: web.Request) -> web.WebSocketResponse:
        self.handshakes.append({"auth": request.headers.get("Authorization"), "inspection": request.headers.get("X-DashScope-DataInspection")})
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws = ws

        async def writer() -> None:
            while True:
                payload = await self.outbound.get()
                if payload == "CLOSE":
                    await ws.close()
                    return
                await ws.send_str(json.dumps(payload))

        task = asyncio.create_task(writer())
        task_id = ""
        try:
            async for message in ws:
                if message.type == aiohttp.WSMsgType.BINARY:
                    self.audio.extend(message.data)
                elif message.type == aiohttp.WSMsgType.TEXT:
                    raw = json.loads(message.data)
                    action = raw.get("header", {}).get("action")
                    if action == "run-task":
                        self.start_requests.append(raw)
                        task_id = raw["header"]["task_id"]
                        await ws.send_str(json.dumps({"header": {"task_id": task_id, "event": "task-started", "attributes": {}}, "payload": {}}))
                    elif action == "finish-task":
                        self.finish_requests.append(raw)
                        await ws.send_str(json.dumps({
                            "header": {"task_id": task_id, "event": "task-finished", "attributes": {}},
                            "payload": {"output": {"sentence": None}},
                        }))
        finally:
            task.cancel()
        return ws

    async def send(self, payload: dict[str, Any] | str) -> None:
        await self.outbound.put(payload)

    async def wait_for(self, check, timeout: float = 2.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while not check():
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError("fake server condition not met")
            await asyncio.sleep(0.01)


class DashScopeTaskASRTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeDashScopeServer()
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    def provider(self, model: str = "fun-asr-realtime-2026-02-28", **options):
        return create_asr({
            "id": "dashscope-test",
            "kind": "dashscope-task-asr",
            "model": model,
            "baseUrl": self.server.url,
            "apiKey": "fake-key",
            "options": options,
        })

    async def test_handshake_and_run_task_contract(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"),
            sample_rate=16000,
            hotwords=["LingerLens"],
            context=["context is off for this preset"],
        )
        await self.server.wait_for(lambda: bool(self.server.start_requests))
        self.assertEqual(self.server.handshakes[0]["auth"], "Bearer fake-key")
        self.assertEqual(self.server.handshakes[0]["inspection"], "enable")

        request = self.server.start_requests[0]
        header = request["header"]
        self.assertEqual(header["action"], "run-task")
        self.assertEqual(header["streaming"], "duplex")
        # Official contract: UUID WITH hyphens.
        self.assertRegex(header["task_id"], r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        payload = request["payload"]
        self.assertEqual(payload["model"], "fun-asr-realtime-2026-02-28")
        self.assertEqual(payload["parameters"]["language_hints"], ["ja"])
        self.assertEqual(payload["parameters"]["format"], "pcm")
        self.assertEqual(payload["parameters"]["sample_rate"], 16000)
        # Audit fix: parameters.hotwords is not in the current official docs.
        self.assertNotIn("hotwords", payload["parameters"])
        self.assertNotIn("vocabulary", payload["parameters"])

        # Audio only flows after task-started (adapter waits in connect()).
        await stream.push_pcm(b"\x01\x00" * 1600, 0.0)
        await self.server.wait_for(lambda: len(self.server.audio) == 3200)

        await stream.flush()
        await self.server.wait_for(lambda: bool(self.server.finish_requests))
        self.assertEqual(self.server.finish_requests[0]["header"]["action"], "finish-task")
        await stream.aclose()

    async def test_result_generated_lifecycle(self) -> None:
        stream = await self.provider().stream(
            policy=SourceLanguagePolicy.specified("ja"), sample_rate=16000, hotwords=[], context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.start_requests))
        await self.server.send({
            "header": {"task_id": "t", "event": "result-generated", "attributes": {}},
            "payload": {"output": {"sentence": {
                "begin_time": 170, "end_time": 400, "text": "OK,",
                "heartbeat": False, "sentence_begin": True, "sentence_end": False, "sentence_id": 1,
                "words": [{"begin_time": 170, "end_time": 295, "text": "OK", "punctuation": ","}],
            }}},
        })
        await self.server.send({
            "header": {"task_id": "t", "event": "result-generated", "attributes": {}},
            "payload": {"output": {"sentence": {
                "begin_time": 170, "end_time": 920, "text": "OK, got it.",
                "heartbeat": False, "sentence_begin": False, "sentence_end": True, "sentence_id": 1,
            }}},
        })
        # Heartbeat packets carry no speech and must be dropped.
        await self.server.send({
            "header": {"task_id": "t", "event": "result-generated", "attributes": {}},
            "payload": {"output": {"sentence": {
                "begin_time": 0, "end_time": 0, "text": "",
                "heartbeat": True, "sentence_begin": False, "sentence_end": False, "sentence_id": 0,
            }}},
        })
        # task-failed without error fields falls back to a generic message.
        await self.server.send({"header": {"task_id": "t", "event": "task-failed", "attributes": {}}, "payload": {}})
        # task-failed with error fields (server shape: header.error_code/message).
        await self.server.send({
            "header": {"task_id": "t", "event": "task-failed", "error_code": "InvalidParameter", "error_message": "bad model"},
            "payload": {},
        })

        iterator = stream.__aiter__()
        events = [await asyncio.wait_for(iterator.__anext__(), 2) for _ in range(6)]
        self.assertEqual(events[0].type, "speech_started")
        self.assertEqual(events[0].begin_pcm, 0.17)
        self.assertEqual(events[0].item_id, "1")
        self.assertEqual((events[1].type, events[1].text), ("interim", "OK,"))
        self.assertEqual(events[2].type, "speech_stopped")
        self.assertEqual(events[2].end_pcm, 0.92)
        self.assertEqual((events[3].type, events[3].text), ("final", "OK, got it."))
        self.assertEqual((events[3].begin_pcm, events[3].end_pcm), (0.17, 0.92))
        # task-failed without error fields falls back to a generic message;
        # the official shape carries header.error_code/error_message.
        self.assertEqual(events[4].type, "error")
        self.assertEqual(events[4].message, "ASR task failed")
        self.assertEqual(events[5].type, "error")
        self.assertEqual(events[5].message, "bad model")
        await stream.aclose()

    async def test_qwen_audio_preset_detect_candidates_up_to_four(self) -> None:
        provider = self.provider(model="qwen-audio-3.0-asr-flash-streaming")
        language = provider.capabilities.language
        self.assertEqual(language.detection, "candidates")
        self.assertEqual(language.max_candidates, 4)
        self.assertEqual(language.tier, "provider_claimed")
        validate_source_policy(SourceLanguagePolicy.detect(candidates=("ja", "en", "zh", "ko")), language)
        with self.assertRaises(LanguageNotSupportedError):
            validate_source_policy(SourceLanguagePolicy.detect(candidates=("ja", "en", "zh", "ko", "fr")), language)
        # Fun-ASR presets stay specified-only.
        fun_language = self.provider().capabilities.language
        self.assertEqual(fun_language.detection, "none")

        stream = await provider.stream(
            policy=SourceLanguagePolicy.detect(candidates=("ja", "en", "zh", "ko")),
            sample_rate=16000, hotwords=[], context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.start_requests))
        self.assertEqual(
            self.server.start_requests[0]["payload"]["parameters"]["language_hints"],
            ["ja", "en", "zh", "ko"],
        )
        await stream.aclose()

    async def test_qwen_audio_instant_vocabulary_passthrough(self) -> None:
        vocabulary = [{"text": "ラグリンゴ", "weight": 3}]
        stream = await self.provider(
            model="qwen-audio-3.0-asr-flash-streaming",
            vocabulary=vocabulary,
        ).stream(
            policy=SourceLanguagePolicy.specified("ja"), sample_rate=16000, hotwords=[], context=[],
        )
        await self.server.wait_for(lambda: bool(self.server.start_requests))
        parameters = self.server.start_requests[0]["payload"]["parameters"]
        # Official instant-hotword option for this model; passed through
        # verbatim (the adapter does not invent a hotword schema).
        self.assertEqual(parameters["vocabulary"], vocabulary)
        self.assertNotIn("hotwords", parameters)
        await stream.aclose()

    async def test_qwen_audio_context_lives_in_payload_input(self) -> None:
        stream = await self.provider(
            model="qwen-audio-3.0-asr-flash-streaming",
        ).stream(
            policy=SourceLanguagePolicy.specified("ja"), sample_rate=16000,
            hotwords=[], context=["配信の主題：ゲーム実況", "メンバー名前"],
        )
        await self.server.wait_for(lambda: bool(self.server.start_requests))
        payload = self.server.start_requests[0]["payload"]
        # Official docs place dialogue context under input.context (max 5).
        self.assertEqual(payload["input"]["context"], ["配信の主題：ゲーム実況", "メンバー名前"])
        self.assertNotIn("context", payload["parameters"])
        await stream.aclose()

    async def test_paraformer_keeps_specified_only_and_does_not_send_context(self) -> None:
        stream = await self.provider(model="paraformer-realtime-v2").stream(
            policy=SourceLanguagePolicy.specified("zh"), sample_rate=16000,
            hotwords=[], context=["should not be sent"],
        )
        await self.server.wait_for(lambda: bool(self.server.start_requests))
        payload = self.server.start_requests[0]["payload"]
        self.assertEqual(payload["parameters"]["language_hints"], ["zh"])
        self.assertNotIn("context", payload.get("input", {}))
        await stream.aclose()
        with self.assertRaises(LanguageNotSupportedError):
            await self.provider(model="paraformer-realtime-v2").stream(
                policy=SourceLanguagePolicy.detect(candidates=("zh", "en")),
                sample_rate=16000, hotwords=[], context=[],
            )


    async def test_qwen31_uses_task_protocol_from_the_qwen_connection(self) -> None:
        provider = create_asr({"id": "qwen31", "kind": "dashscope-qwen-realtime",
            "model": "qwen-audio-3.1-asr-flash-streaming", "baseUrl": self.server.url,
            "apiKey": "fake-key", "options": {"vadModel": "near_meeting_16k"}})
        self.assertEqual(provider.capabilities.language.max_candidates, 4)
        stream = await provider.stream(policy=SourceLanguagePolicy.specified("en"),
            sample_rate=16000, hotwords=[], context=["Test context"])
        try:
            request = self.server.start_requests[0]
            self.assertEqual(request["header"]["action"], "run-task")
            self.assertEqual(request["payload"]["model"], provider.model)
            self.assertEqual(request["payload"]["parameters"]["vad_model"], "near_meeting_16k")
            self.assertEqual(request["payload"]["input"]["context"], [{"role": "user", "content": [{"type": "input_text", "text": "Test context"}]}])
        finally:
            await stream.aclose()

    def test_confirmed_word_times_are_preserved_but_interim_words_are_not_committed(self):
        from companion.providers.asr_dashscope_task import _DashScopeTaskStream
        sentence = {"sentence_id": 1, "sentence_end": True, "begin_time": 100,
            "end_time": 2400, "text": "Hello world.", "words": [
                {"begin_time": 100, "end_time": 900, "text": "Hello", "punctuation": ""},
                {"begin_time": 1400, "end_time": 2400, "text": "world", "punctuation": "."}]}
        raw = {"header": {"event": "result-generated"}, "payload": {"output": {"sentence": sentence}}}
        observation = _DashScopeTaskStream._map_event(raw).caption_observation
        self.assertEqual([(t.text, t.begin_pcm, t.end_pcm) for t in observation.tokens], [("Hello", .1, .9), ("world.", 1.4, 2.4)])
        sentence["sentence_end"] = False
        self.assertEqual(_DashScopeTaskStream._map_event(raw).caption_observation.tokens, ())
        sentence["sentence_end"] = True
        sentence["words"] = sentence["words"][:1]
        fallback = _DashScopeTaskStream._map_event(raw).caption_observation
        self.assertEqual(fallback.tokens, ())
        self.assertEqual(fallback.stable_text, "Hello world.")

    def test_qwen_protocol_switch_preserves_custom_endpoints(self):
        from companion.providers.asr_qwen_realtime import QwenRealtimeASRProvider
        def make(model, url):
            return QwenRealtimeASRProvider({"id": "qwen", "model": model, "baseUrl": url})
        new = make("qwen-audio-3.1-asr-flash-streaming", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime")
        self.assertEqual(new._streaming_provider.base_url, "wss://dashscope.aliyuncs.com/api-ws/v1/inference")
        old = make("qwen3-asr-flash-realtime", "wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference")
        self.assertEqual(old.base_url, "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime")
        custom = make("qwen-audio-3.1-asr-flash-streaming", "wss://example.com/custom/realtime")
        self.assertEqual(custom._streaming_provider.base_url, "wss://example.com/custom/realtime")


    def test_live_qwen31_fixture_reaches_the_chunker_with_independent_word_times(self):
        import dataclasses
        from companion.caption_chunker import CaptionChunker
        from companion.providers.asr_dashscope_task import _DashScopeTaskStream
        raw = json.loads((Path(__file__).parent / "fixtures/qwen31_synthetic_asr.json").read_text(encoding="utf-8"))
        observation = _DashScopeTaskStream._map_event(raw).caption_observation
        chunker = CaptionChunker(realtime=True)
        chunks = list(chunker.observe(dataclasses.replace(observation, generation=1, language="en")).chunks)
        chunks += list(chunker.flush_utterance(None).chunks)
        self.assertEqual([(c.begin_pcm, c.end_pcm) for c in chunks], [(.32, 4.04), (4.76, 8.52)])
        self.assertTrue(all(c.end_pcm - c.begin_pcm <= 7 for c in chunks))
        self.assertEqual(" ".join(c.text for c in chunks), raw["payload"]["output"]["sentence"]["text"].strip())


if __name__ == "__main__":
    unittest.main()
