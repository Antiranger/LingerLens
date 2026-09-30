"""Soniox ``stt-rt-v5`` built-in translation: the session returns both languages.

Official token contract (https://soniox.com/docs/translation/stt-translation):
one flat ``tokens`` array where every token carries ``translation_status`` --
``"original"`` for spoken text that is being translated, ``"translation"`` for
its translation, ``"none"`` for speech outside the configured pair. Translated
tokens have NO ``start_ms``/``end_ms``.

These tests pin the two consequences that matter to the pipeline: translated
tokens never reach the source clock, and the confirmed translation is handed out
in the same order it arrived.
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

from companion.providers import create_asr
from companion.providers.base import SourceLanguagePolicy


class FakeSonioxServer:
    def __init__(self) -> None:
        self.start_requests: list[dict[str, Any]] = []
        self._ws = None
        self._sockets: list[web.WebSocketResponse] = []
        self.outbound: asyncio.Queue[dict[str, Any] | str] = asyncio.Queue()
        self.runner: web.AppRunner | None = None
        self.url = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/transcribe-websocket", self._handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        server = site._server
        assert server is not None
        port = server.sockets[0].getsockname()[1]  # type: ignore[attr-defined]
        self.url = f"ws://127.0.0.1:{port}/transcribe-websocket"

    async def stop(self) -> None:
        assert self.runner is not None
        # Every connection, not only the newest one: a test that opens two
        # sessions must not leave the first handler waiting on a reader.
        for ws in self._sockets:
            if not ws.closed:
                await ws.close()
        await self.runner.cleanup()

    async def _handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws = ws
        self._sockets.append(ws)
        first = await ws.receive()
        self.start_requests.append(json.loads(first.data))

        async def writer() -> None:
            while True:
                payload = await self.outbound.get()
                if payload == "CLOSE":
                    await ws.close()
                    return
                await ws.send_str(json.dumps(payload))

        task = asyncio.create_task(writer())
        try:
            async for _ in ws:
                pass
        finally:
            task.cancel()
        return ws

    async def send(self, tokens: list[dict[str, Any]], **extra: Any) -> None:
        await self.outbound.put({"tokens": tokens, **extra})

    async def wait_for_start(self, count: int = 1) -> None:
        """The config frame is read by the handler task, not at connect time."""
        for _ in range(200):
            if len(self.start_requests) >= count:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("the fake server never received a config frame")


def original(text: str, start: int, end: int, final: bool = True) -> dict[str, Any]:
    return {
        "text": text, "start_ms": start, "end_ms": end,
        "is_final": final, "translation_status": "original", "language": "ja",
    }


def translated(text: str, final: bool = True) -> dict[str, Any]:
    # Official shape: no start_ms/end_ms, target language + source_language.
    return {
        "text": text, "is_final": final,
        "translation_status": "translation", "language": "zh", "source_language": "ja",
    }


class SonioxTranslationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeSonioxServer()
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    def provider(self, kind: str = "soniox-realtime", **options):
        options.setdefault("closeDrainTimeoutSeconds", 0.1)
        return create_asr({
            "id": "soniox-test",
            "kind": kind,
            "model": "stt-rt-v5",
            "baseUrl": self.server.url,
            "apiKey": "fake-key",
            "options": options,
        })

    async def connected(self, provider):
        provider.set_translation_target("zh-Hans")
        before = len(self.server.start_requests)
        stream = await provider.stream(
            policy=SourceLanguagePolicy.specified("ja"), sample_rate=16000,
            hotwords=[], context=[],
        )
        self.addAsyncCleanup(stream.aclose)
        await self.server.wait_for_start(before + 1)
        return stream

    async def next_event(self, iterator, kind: str):
        """Skip the adapter's intermediate refreshes and wait for one kind.

        A frame that carries only translation still re-publishes the running
        source text, so the stream interleaves more interim events than the
        sequence under test cares about.
        """
        for _ in range(20):
            event = await asyncio.wait_for(iterator.__anext__(), 2)
            if event.type == kind:
                return event
        raise AssertionError(f"no {kind} event arrived")

    async def test_the_config_message_asks_for_one_way_translation_into_the_target(self) -> None:
        provider = self.provider(translationType="one_way")
        self.assertTrue(provider.capabilities.native_translation.enabled)
        self.assertFalse(provider.capabilities.native_translation.two_way)
        await self.connected(provider)
        self.assertEqual(
            self.server.start_requests[0]["translation"],
            {"type": "one_way", "target_language": "zh"},
        )

    async def test_two_way_sends_both_sides_and_claims_no_single_target(self) -> None:
        provider = self.provider(
            translationType="two_way", translationLanguageA="ja", translationLanguageB="zh"
        )
        capabilities = provider.capabilities.native_translation
        self.assertTrue(capabilities.two_way)
        self.assertIsNone(capabilities.target_tags, "two-way picks a side per utterance")
        await self.connected(provider)
        self.assertEqual(
            self.server.start_requests[0]["translation"],
            {"type": "two_way", "language_a": "ja", "language_b": "zh"},
        )

    async def test_an_unset_mode_leaves_the_profile_transcript_only(self) -> None:
        provider = self.provider()
        self.assertFalse(
            provider.capabilities.native_translation.enabled,
            "an unset option must not silently switch translation on",
        )
        await self.connected(provider)
        self.assertNotIn("translation", self.server.start_requests[0])

    async def test_the_transcribe_only_protocol_keeps_translation_off(self) -> None:
        """The only thing that protocol entry adds over ``soniox-realtime``.

        A card switched onto it while still carrying a saved ``one_way`` would
        otherwise ask Soniox to translate and publish a second text stream the
        caption path has been told not to expect.
        """
        provider = self.provider(kind="soniox-realtime-transcribe", translationType="one_way")
        self.assertFalse(provider.capabilities.native_translation.enabled)
        await self.connected(provider)
        self.assertNotIn("translation", self.server.start_requests[0])

    async def test_translation_terms_ride_along_only_with_translation_on(self) -> None:
        terms = [{"source": "stroke", "target": "中风"}]
        provider = self.provider(translationType="one_way", translationTerms=terms)
        stream = await self.connected(provider)
        self.assertEqual(
            self.server.start_requests[0]["context"]["translation_terms"], terms
        )
        await stream.aclose()

    async def test_terms_do_not_ride_along_when_translation_is_off(self) -> None:
        """Official rule: ``translation_terms`` is ignored without translation."""
        provider = self.provider(translationTerms=[{"source": "stroke", "target": "中风"}])
        stream = await self.connected(provider)
        self.assertNotIn("context", self.server.start_requests[0])
        await stream.aclose()

    async def test_translated_tokens_never_enter_the_source_timeline(self) -> None:
        stream = await self.connected(self.provider(translationType="one_way"))
        await self.server.send([original("こんにちは", 0, 800), translated("你好")])
        interim = await self.next_event(stream.__aiter__(), "interim")
        self.assertEqual(interim.text, "こんにちは")

    async def test_the_confirmed_translation_rides_the_utterance_final(self) -> None:
        stream = await self.connected(self.provider(translationType="one_way"))
        iterator = stream.__aiter__()
        await self.server.send([original("こんにちは", 0, 800), translated("你好")])
        await self.next_event(iterator, "interim")
        await self.server.send([original("<end>", 800, 800)])
        final = await self.next_event(iterator, "final")
        self.assertEqual(final.translation, "你好")

    async def test_a_tentative_translation_is_published_but_never_resolved_against(self) -> None:
        stream = await self.connected(self.provider(translationType="one_way"))
        iterator = stream.__aiter__()
        await self.server.send([original("こんにちは", 0, 800)])
        await self.next_event(iterator, "interim")
        await self.server.send([translated("你", final=False)])
        update = await self.next_event(iterator, "translation")
        self.assertEqual(update.translation, "", "a revisable tail is not published as resolved")
        self.assertEqual(update.translation_stash, "你")

    async def test_speech_after_a_translation_chunk_settles_an_alignment_point(self) -> None:
        """The order of the stream is the only alignment Soniox offers.

        A caption cut inside one Provider segment can only be answered where the
        Provider itself stopped translating and went back to transcribing; that
        point is what the pipeline resolves such a cue against.
        """
        stream = await self.connected(self.provider(translationType="one_way"))
        iterator = stream.__aiter__()
        await self.server.send([original("こんにちは", 0, 800), translated("你好")])
        first = await self.next_event(iterator, "interim")
        self.assertEqual(first.translation_anchors, (), "nothing has followed the chunk yet")
        await self.server.send([original("さようなら", 800, 1600)])
        second = await self.next_event(iterator, "interim")
        self.assertEqual(second.translation_anchors, (("こんにちは", "你好"),))
        await self.server.send([translated("再见"), original("<end>", 1600, 1600)])
        final = await self.next_event(iterator, "final")
        self.assertEqual(final.text, "こんにちはさようなら")
        self.assertEqual(final.translation, "你好再见")
        self.assertEqual(
            final.translation_anchors,
            (("こんにちは", "你好"), ("こんにちはさようなら", "你好再见")),
            "the final boundary remains explicit alongside the earlier prefix anchor",
        )
        self.assertTrue(final.translation_anchors_trusted)

    async def test_same_frame_source_translation_source_order_settles_anchor(self) -> None:
        """A unified Soniox frame may contain both chunks and the next source.

        The old frame-level tracker only knew that both source and translation
        advanced; it lost their order and therefore could not expose the safe
        prefix boundary. The adapter must preserve the token-array order.
        """
        stream = await self.connected(self.provider(translationType="one_way"))
        iterator = stream.__aiter__()
        await self.server.send([
            original("Hello ", 0, 400), original("world", 400, 800),
            translated("你好世界"),
            original(" again", 800, 1200),
        ])
        update = await self.next_event(iterator, "interim")
        self.assertTrue(update.translation_anchors_trusted)
        self.assertEqual(update.translation_anchors, (("Hello world", "你好世界"),))

    async def test_translation_arriving_after_endpoint_keeps_previous_item_identity(self) -> None:
        stream = await self.connected(self.provider(translationType="one_way"))
        iterator = stream.__aiter__()
        await self.server.send([original("hello", 0, 600)])
        await self.next_event(iterator, "interim")
        await self.server.send([original("<end>", 600, 600)])
        final = await self.next_event(iterator, "final")
        self.assertEqual((final.item_id, final.translation), ("1", ""))

        await self.server.send([translated("你好")])
        late = await self.next_event(iterator, "translation")
        self.assertEqual((late.item_id, late.translation), ("1", "你好"))

        await self.server.send([original("next", 600, 1000)])
        following = await self.next_event(iterator, "interim")
        self.assertEqual(following.item_id, "2")
        self.assertEqual(following.translation, "")

    async def test_each_utterance_gets_only_its_own_translation(self) -> None:
        """The bucket resets at the endpoint, so a long session does not accumulate."""
        stream = await self.connected(self.provider(translationType="one_way"))
        iterator = stream.__aiter__()
        await self.server.send([original("一", 0, 400), translated("一译")])
        await self.next_event(iterator, "interim")
        await self.server.send([original("<end>", 400, 400)])
        first = await self.next_event(iterator, "final")
        await self.server.send([original("二", 400, 800), translated("二译")])
        await self.next_event(iterator, "interim")
        await self.server.send([original("<end>", 800, 800)])
        second = await self.next_event(iterator, "final")
        self.assertEqual(first.translation, "一译")
        self.assertEqual(second.translation, "二译")


if __name__ == "__main__":
    unittest.main()
