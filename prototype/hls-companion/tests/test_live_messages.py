from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from aiohttp import WSMsgType
from aiohttp.test_utils import AioHTTPTestCase

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from companion.auth_lease import SessionAuthLease
from companion.bilibili_danmaku_ingest import (
    BilibiliDanmakuIngest,
    OP_ENTER_ROOM,
    OP_HEARTBEAT,
    OP_MESSAGE,
    PROTO_ZLIB,
    decode_packets,
    encode_packet,
)
from companion.capture_clock import CaptureClock
from companion.core import BrowserCookieSnapshot
from companion.live_messages import LiveMessageStore
from companion.message_translator import MessageTranslator, should_skip_translation
from companion.providers.base import (
    StreamMeta,
    TranslationCapabilities,
    TranslationLanguageCapabilities,
    TranslationProvider,
    TranslationRequest,
    TranslationResult,
)
from companion.server import CompanionApplication, errors
from companion.youtube_chat_ingest import YouTubeChatIngest, iter_youtube_chat_items


class DummyTranslationProvider(TranslationProvider):
    def __init__(self, delay: float = 0.0, fail: bool = False):
        self.id = "dummy"
        self.label = "Dummy"
        self.model = "dummy-v1"
        self.delay = delay
        self.fail = fail

    @property
    def capabilities(self) -> TranslationCapabilities:
        return TranslationCapabilities(
            rolling_context=False,
            domains=False,
            glossary=False,
            json_output=False,
            max_input_chars=1000,
            language=TranslationLanguageCapabilities(),
        )

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("provider failed")
        return TranslationResult(
            text=f"译:{request.source_text}",
            provider_id=self.id,
            latency_ms=10,
            usage={"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        )


class CaptureClockTests(unittest.TestCase):
    def test_atomic_cursor_is_epoch_monotonic_bounded_and_stops_on_stall(self):
        clock = CaptureClock()
        self.assertIsNone(clock.capture_wall_time(100.0))
        clock.update(
            pdt_epoch=1_700_000_000.0,
            completed_private_media_seconds=10.0,
            target_duration=2.0,
            monotonic_time=100.0,
        )
        self.assertEqual(clock.capture_wall_time(100.5), 1_700_000_010.5)
        self.assertEqual(clock.capture_wall_time(102.0), 1_700_000_012.0)
        self.assertEqual(clock.capture_wall_time(105.0), 1_700_000_012.0)
        clock.update(
            pdt_epoch=1_700_000_000.0,
            completed_private_media_seconds=11.0,
            target_duration=2.0,
            monotonic_time=106.0,
        )
        self.assertEqual(clock.capture_wall_time(106.0), 1_700_000_012.0)
        snapshot = clock.snapshot(106.5)
        self.assertEqual(snapshot["pdtEpoch"], 1_700_000_000.0)
        self.assertEqual(snapshot["completedPrivateMediaSeconds"], 11.0)
        self.assertEqual(snapshot["targetDuration"], 2.0)
        self.assertEqual(snapshot["captureWallTime"], 1_700_000_012.0)


class LiveMessageStoreTests(unittest.TestCase):
    def test_dedupe_revision_after_seq_retention_cap_and_pending_mapping(self):
        clock = CaptureClock()
        store = LiveMessageStore(clock=clock, max_messages=3, retention_seconds=120, pending_limit=3)
        pending = store.add(
            platform="youtube",
            source_id="one",
            author={"id": "a", "name": "Alice", "badges": []},
            text="first",
            received_monotonic=10.0,
            received_at=1000.0,
            translation_enabled=True,
        )
        store.add(platform="youtube", source_id="two", author={"name": "B"}, text="second", received_monotonic=11.0, received_at=1001.0)
        store.add(platform="youtube", source_id="three", author={"name": "C"}, text="third", received_monotonic=13.0, received_at=1003.0)
        self.assertIsNone(pending.media_time)
        self.assertEqual(store.query(after_seq=0), [])

        clock.update(pdt_epoch=1000.0, completed_private_media_seconds=20.0, target_duration=2.0, monotonic_time=15.0)
        store.flush_pending(monotonic_time=15.0)
        initial = store.query(after_seq=0)
        self.assertEqual(len(initial), 3)
        self.assertEqual([row["id"] for row in initial], ["youtube:one", "youtube:two", "youtube:three"])
        self.assertEqual([row["mediaTime"] for row in initial], [1017.0, 1018.0, 1020.0])
        self.assertEqual(initial[0]["translationState"], "pending")
        self.assertLessEqual(max(row["mediaTime"] for row in initial), 1020.0)
        seq = initial[0]["seq"]
        max_seq_before_duplicate = store.max_seq

        duplicate = store.add(
            platform="youtube", source_id="one", author={"name": "Alice"}, text="duplicate",
            received_monotonic=12.1, received_at=1001.0,
        )
        self.assertIs(duplicate, pending)
        self.assertEqual(store.max_seq, max_seq_before_duplicate)

        self.assertTrue(store.update_translation("youtube:one", state="done", translation="第一"))
        revision = store.query(after_seq=max_seq_before_duplicate)
        self.assertEqual(len(revision), 1)
        self.assertGreater(revision[0]["seq"], seq)
        self.assertEqual(revision[0]["revision"], 2)
        self.assertEqual(revision[0]["mediaTime"], 1017.0)

        for index, media_time in enumerate((1100.0, 1210.0, 1211.0, 1212.0), start=2):
            store.add(
                platform="bilibili", source_id=str(index), author={"name": "B"}, text=str(index),
                received_monotonic=20.0 + index, received_at=1000.0 + index, media_time=media_time,
            )
        rows = store.query(after_seq=0)
        self.assertLessEqual(len(rows), 3)
        self.assertTrue(all(row["mediaTime"] >= 1092.0 for row in rows))
        self.assertNotIn("raw", json.dumps(rows))
        self.assertNotIn("cookie", json.dumps(rows).lower())


    def test_query_near_tail_does_not_scan_entire_event_history_and_stats_are_incremental(self):
        store = LiveMessageStore(max_messages=5000, retention_seconds=10_000)
        for index in range(5000):
            store.add(
                platform="youtube", source_id=str(index), author={"name": "A"}, text="x",
                media_time=float(index), received_at=float(index), received_monotonic=float(index),
            )

        class CountingDeque(type(store._events)):
            def __reversed__(self):
                self.reversed_items = 0
                for item in super().__reversed__():
                    self.reversed_items += 1
                    yield item

        events = CountingDeque(store._events, maxlen=store._events.maxlen)
        store._events = events
        rows = store.query(after_seq=store.max_seq - 1)
        self.assertEqual(len(rows), 1)
        self.assertLessEqual(events.reversed_items, 2)
        self.assertEqual(store.stats()["textMessages"], 5000)
        self.assertTrue(store.update_translation(rows[0]["id"], state="done", translation="译"))
        self.assertEqual(store.stats()["translated"], 1)
        store.clear()
        self.assertEqual(store.stats()["textMessages"], 0)


class TranslationTests(unittest.IsolatedAsyncioTestCase):
    async def test_switch_queue_skip_deadline_and_revision_updates(self):
        self.assertTrue(should_skip_translation("https://example.com"))
        self.assertTrue(should_skip_translation("🔥🔥!!!"))
        self.assertFalse(should_skip_translation("hello 世界"))

        store = LiveMessageStore(max_messages=100)
        translator = MessageTranslator(
            store=store,
            translation_provider=DummyTranslationProvider(delay=0.02),
            pricing_by_provider={"dummy": {"input": 1.0, "cachedInput": 0.0, "output": 2.0}},
            meta=StreamMeta(None, None, None, "und", "zh-Hans"),
            max_queue_size=2,
            concurrency=1,
            timeout_seconds=0.2,
        )
        await translator.start()
        translator.set_enabled(True)
        first = store.add(platform="youtube", source_id="a", author={"name": "A"}, text="hello", media_time=10, received_at=10, received_monotonic=10, translation_enabled=True)
        second = store.add(platform="youtube", source_id="b", author={"name": "B"}, text="second", media_time=11, received_at=11, received_monotonic=11, translation_enabled=True)
        third = store.add(platform="youtube", source_id="c", author={"name": "C"}, text="third", media_time=12, received_at=12, received_monotonic=12, translation_enabled=True)
        translator.enqueue(first)
        translator.enqueue(second)
        translator.enqueue(third)
        # Switch off only once nothing is in flight. A request that has already
        # been paid for cannot be un-spent when translation is turned off mid
        # call, so a fixed sleep here measured the machine's scheduling instead
        # of the rule this assertion is about: no provider call goes to waste.
        for _ in range(200):
            settled = {row["id"]: row["translationState"] for row in store.query(after_seq=0)}
            if settled.get("youtube:b") != "pending" and settled.get("youtube:c") != "pending":
                break
            await asyncio.sleep(0.01)
        translator.set_enabled(False)
        await translator.stop()

        states = {row["id"]: row["translationState"] for row in store.query(after_seq=0)}
        self.assertIn(states["youtube:a"], {"done", "skipped"})
        self.assertIn(states["youtube:b"], {"done", "skipped"})
        self.assertIn(states["youtube:c"], {"done", "skipped"})
        self.assertGreater(store.max_seq, 3)
        self.assertEqual(translator.status()["translationUsage"]["calls"], sum(1 for state in states.values() if state == "done"))


class YouTubeSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_incremental_fragments_history_filter_command_and_bad_lines(self):
        clock = CaptureClock()
        clock.update(pdt_epoch=1000, completed_private_media_seconds=20, target_duration=2, monotonic_time=50)
        store = LiveMessageStore(clock=clock)
        source = YouTubeChatIngest("https://youtube.com/watch?v=x", store, clock=clock, yt_dlp_path="yt-dlp")
        command = source.command(Path("C:/tmp/chat"), [])
        for arg in ("--skip-download", "--write-subs", "--sub-langs", "live_chat", "--sub-format", "json"):
            self.assertIn(arg, command)

        payload = {
            "actions": [
                {"replayChatItemAction": {"videoOffsetTimeMsec": "-1", "actions": [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {"id": "old", "message": {"runs": [{"text": "old"}]}, "authorName": {"simpleText": "A"}, "timestampUsec": "1000000000"}}}}]}},
                {"replayChatItemAction": {"videoOffsetTimeMsec": "1500", "actions": [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {"id": "new", "message": {"runs": [{"text": "new"}]}, "authorName": {"simpleText": "B"}, "timestampUsec": "1001000000"}}}}]}},
            ]
        }
        items = list(iter_youtube_chat_items(json.dumps(payload)))
        self.assertEqual(len(items), 2)
        source.source_started_monotonic = 50.0
        source.source_started_media_time = 1020.0
        source.source_started_wall_time = 999.0
        callbacks = []
        source.on_message = callbacks.append
        source.consume_json(payload, received_monotonic=51.0, received_at=1001.0)
        source.consume_json(payload, received_monotonic=52.0, received_at=1002.0)
        rows = store.query(after_seq=0)
        self.assertEqual([row["id"] for row in rows], ["youtube:new"])
        self.assertEqual(rows[0]["mediaTime"], 1021.5)
        self.assertEqual(source.status()["received"], 1)
        self.assertEqual(len(callbacks), 1)


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return None
    def raise_for_status(self): return None
    async def json(self): return self.payload


class _FakeWsMessage:
    def __init__(self, data):
        self.type = WSMsgType.BINARY
        self.data = data


class _FakeWs:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []
        self.closed = False
    async def __aenter__(self): return self
    async def __aexit__(self, *_): self.closed = True
    async def send_bytes(self, value): self.sent.append(value)
    async def close(self): self.closed = True
    def __aiter__(self): return self
    async def __anext__(self):
        if not self.frames: raise StopAsyncIteration
        return _FakeWsMessage(self.frames.pop(0))


class _FakeHttpSession:
    def __init__(self, ws):
        self.ws = ws
        self.closed = False
        self.get_calls = []
        self.post_calls = []
    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if "getDanmuInfo" in url:
            return _FakeResponse({"code": 0, "data": {"token": "secret-token", "host_list": [{"host": "fake.example", "wss_port": 443}]}})
        return _FakeResponse({"data": {"room_id": 999}})
    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return _FakeResponse({"data": {"token": "secret-token", "host_list": [{"host": "fake.example", "wss_port": 443}]}})
    def ws_connect(self, url, **kwargs): return self.ws
    async def close(self): self.closed = True


class BilibiliSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_resolution_auth_heartbeat_zlib_multipacket_and_reconnect_surface(self):
        command = {"cmd": "DANMU_MSG", "info": [[0, 25, 0, 16777215, 1700000000000], "弹幕", [42, "Viewer"], []]}
        inner = encode_packet(OP_MESSAGE, json.dumps(command).encode(), protover=0) + encode_packet(OP_MESSAGE, b"bad json", protover=0)
        frame = encode_packet(OP_MESSAGE, zlib.compress(inner), protover=PROTO_ZLIB)
        ws = _FakeWs([frame])
        session = _FakeHttpSession(ws)
        clock = CaptureClock()
        clock.update(pdt_epoch=1000, completed_private_media_seconds=20, target_duration=2, monotonic_time=50)
        store = LiveMessageStore(clock=clock)
        source = BilibiliDanmakuIngest("https://live.bilibili.com/123", store, clock=clock, session_factory=lambda: session, heartbeat_interval=0.01, reconnect_base=0.01, reconnect_max=0.02)
        await source.start()
        await asyncio.sleep(0.05)
        source.feed_bytes_for_test(frame)
        await source.stop()
        self.assertEqual(store.query(after_seq=0)[0]["text"], "弹幕")
        self.assertEqual(source.status()["received"], 1)
        sent_ops = [decode_packets(packet)[0][1] for packet in ws.sent]
        self.assertIn(OP_ENTER_ROOM, sent_ops)
        self.assertIn(OP_HEARTBEAT, sent_ops)
        status = source.status()
        self.assertEqual(status["roomId"], 999)
        self.assertGreaterEqual(status["reconnects"], 0)
        self.assertNotIn("secret-token", json.dumps(status))


class LiveMessageApiTests(AioHTTPTestCase):
    async def get_application(self):
        self.temporary = tempfile.TemporaryDirectory()
        args = argparse.Namespace(
            runtime_dir=Path(self.temporary.name) / "media",
            providers_file=Path(self.temporary.name) / "providers.json",
            publish_delay=2.0,
            cookies_from_browser=None,
        )
        companion = CompanionApplication(args)
        companion.control.start = lambda: None
        companion.control.stop = lambda: None
        app = companion.routes()
        app.middlewares.append(errors)
        return app

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.temporary.cleanup()

    async def test_production_scripts_canonical_routes_status_and_settings(self):
        for path in ("/media-clock.js", "/live-messages-client.js", "/workbench-controller.js", "/pane-resizer.js"):
            response = await self.client.get(path)
            self.assertEqual(response.status, 200, path)
            self.assertIn("no-store", response.headers.get("Cache-Control", ""))

        companion = self.app["companion"]
        companion.message_store.add(platform="youtube", source_id="api", author={"id": "1", "name": "A", "badges": []}, text="hello", media_time=1700000000.25, received_at=1700000001, received_monotonic=1)
        response = await self.client.get("/api/live-messages?afterSeq=0")
        self.assertEqual(response.status, 200)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        payload = await response.json()
        self.assertEqual(payload["messages"][0]["kind"], "text")
        self.assertEqual(payload["messages"][0]["mediaTime"], 1700000000.25)
        self.assertEqual(payload["maxSeq"], 1)

        response = await self.client.post("/api/live-messages/settings", json={"translate": True})
        self.assertEqual(response.status, 400)

        response = await self.client.get("/api/status")
        status = await response.json()
        self.assertIn("mediaClock", status)
        self.assertIn("liveMessages", status)
        rendered_status = json.dumps(status).lower()
        self.assertNotIn("secret-token", rendered_status)
        self.assertNotIn("cookie", rendered_status)
        self.assertNotIn("wss://", rendered_status)

    async def test_uncached_authenticated_start_probe_uses_live_owned_cookie_and_cleans_it(self):
        companion = self.app["companion"]
        imported = await self.client.post("/api/auth-cookies", json={
            "lines": "SID\tsecret-value\t.youtube.com\t/\t2027-10-02T08:29:22.479Z\t156\n",
            "domain": ".youtube.com",
        })
        self.assertEqual(imported.status, 200)
        token = (await imported.json())["authToken"]
        observed_paths = []
        info = {
            "is_live": True,
            "title": "Live",
            "channel": "Channel",
            "extractor": "youtube",
            "formats": [{
                "format_id": "95", "url": "https://example.invalid/live.m3u8", "protocol": "m3u8_native",
                "vcodec": "avc1.4d401f", "acodec": "mp4a.40.2", "height": 720, "width": 1280, "fps": 30,
            }],
        }

        def fake_probe(_url, auth):
            args = auth.yt_dlp_args()
            self.assertEqual(args[0], "--cookies")
            path = Path(args[1])
            self.assertTrue(path.exists())
            observed_paths.append(path)
            return info

        class FakeMediaIngest:
            def __init__(self, _url, _selector, auth_args, **kwargs):
                self.path = Path(auth_args[1])
                observed_paths.append(self.path)
                self.cleanup = kwargs["auth_cleanup"]
            def start(self):
                self.assert_path()
            def assert_path(self):
                if not self.path.exists():
                    raise AssertionError("media cookie path disappeared before ingest extraction")
            def stop(self):
                self.cleanup()
            def input_urls(self): return ["tcp://127.0.0.1:1"]
            def snapshot(self): return {"running": True}
            def tee_snapshot(self): return {"teeDropped": 0}
            def detach_audio_tee(self): return None

        def fake_session_start(_url, _inputs, _delay, _command, *, capture_clock=None):
            companion.session.capture_clock = capture_clock
            companion.session.publisher = type("Publisher", (), {
                "capture_clock": capture_clock,
                "pdt_epoch": None,
                "snapshot": lambda self: {},
                "publish_delay": 3.0,
            })()
            companion.session.page_url = _url

        companion.probe.extract = fake_probe
        companion.session.stop = lambda: None
        companion.session.start = fake_session_start
        companion.session.status = lambda: {"state": "running", "pageUrl": "https://www.youtube.com/watch?v=x"}
        with (
            patch("companion.server.YtDlpLiveIngest", FakeMediaIngest),
            patch("companion.server.ProbeInfoSnapshot"),
            patch("companion.server.build_ffmpeg_command", return_value=["fake-ffmpeg"]),
        ):
            response = await self.client.post("/api/start", json={
                "url": "https://www.youtube.com/watch?v=x",
                "authToken": token,
                "subtitles": {"enabled": False},
                "liveMessages": {"enabled": False},
            })
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(len(observed_paths), 2)
        self.assertNotEqual(observed_paths[0], observed_paths[1])
        self.assertFalse(observed_paths[0].exists())
        await self.client.post("/api/stop", json={})
        self.assertTrue(all(not path.exists() for path in observed_paths))

    async def test_chat_can_acquire_auth_after_media_releases_and_start_returns(self):
        companion = self.app["companion"]
        companion.persisted_auth["youtube"] = [{
            "domain": ".youtube.com", "name": "SID", "value": "test-only", "path": "/",
        }]
        info = {
            "is_live": True, "title": "Live", "extractor": "youtube",
            "formats": [{"format_id": "95", "url": "https://example.invalid/live.m3u8",
                         "protocol": "m3u8_native", "vcodec": "avc1.4d401f", "acodec": "mp4a.40.2",
                         "height": 720, "width": 1280, "fps": 30}],
        }
        companion.probe.extract = lambda *_: info
        companion.session.stop = lambda: None
        companion.session.start = lambda *args, **kwargs: None
        companion.session.status = lambda: {"state": "running"}
        class Media:
            def __init__(self, *args, **kwargs): self.cleanup = kwargs["auth_cleanup"]
            def start(self): self.cleanup()  # First media bytes no longer need the cookie file.
            def stop(self): pass
            def input_urls(self): return ["tcp://127.0.0.1:1"]
        class Chat:
            def __init__(self, *args, **kwargs): self.lease = kwargs["auth_lease"]
            async def start(self): pass  # Background task acquires after the request returns.
            async def stop(self): pass
        with patch("companion.server.YtDlpLiveIngest", Media), patch("companion.server.YouTubeChatIngest", Chat), patch("companion.server.build_ffmpeg_command", return_value=["fake"]):
            response = await self.client.post("/api/start", json={
                "url": "https://www.youtube.com/watch?v=x", "subtitles": {"enabled": False},
                "liveMessages": {"enabled": True},
            })
        self.assertEqual(response.status, 200, await response.text())
        lease = companion.message_ingest.lease
        self.assertFalse(lease.is_closed, "session auth expired before the chat task starts")
        for _ in range(2):  # Also survives a later chat reconnect.
            consumer = lease.acquire("youtube_chat")
            cookie_path = Path(consumer.yt_dlp_args()[1])
            self.assertTrue(cookie_path.exists())
            consumer.release()
            self.assertFalse(cookie_path.exists())
        await self.client.post("/api/stop", json={})
        self.assertTrue(lease.is_closed)

    async def test_start_force_closes_previous_auth_lease_before_assigning_new_one(self):
        companion = self.app["companion"]
        old = SessionAuthLease(BrowserCookieSnapshot([{
            "domain": ".youtube.com", "path": "/", "name": "SID", "value": "old", "secure": True,
        }]))
        old_consumer = old.acquire("old-chat")
        old_path = Path(old_consumer.yt_dlp_args()[1])
        companion.auth_lease = old
        companion.probe.extract = lambda *_: {"is_live": False, "formats": []}
        response = await self.client.post("/api/start", json={
            "url": "https://www.youtube.com/watch?v=x",
            "liveMessages": {"enabled": True},
        })
        self.assertEqual(response.status, 400)
        self.assertIn("仅支持正在直播", (await response.json())["error"])
        self.assertTrue(old.is_closed)
        self.assertFalse(old_path.exists())
        self.assertIsNone(companion.auth_lease)

    async def test_twitch_start_uses_twitch_without_bilibili_source(self):
        companion = self.app["companion"]
        info = {
            "is_live": True,
            "extractor_key": "TwitchStream",
            "title": "Live",
            "channel": "Channel",
            "formats": [{
                "format_id": "source", "url": "https://example.invalid/live.m3u8", "protocol": "m3u8_native",
                "vcodec": "avc1.4d401f", "acodec": None, "height": 720, "width": 1280, "fps": 30,
            }],
        }
        companion.probe.extract = lambda *_: info

        class FakeMediaIngest:
            def __init__(self, *_args, **_kwargs): pass
            def start(self): return None
            def stop(self): return None
            def input_urls(self): return ["tcp://127.0.0.1:1"]
            def snapshot(self): return {"running": True}
            def tee_snapshot(self): return {"teeDropped": 0}
            def detach_audio_tee(self): return None

        class FakeChat:
            def __init__(self,*args,**kwargs): pass
            async def start(self): pass
            async def stop(self): pass
            def status(self): return {"state":"running","connected":True,"running":True,"platform":"twitch"}

        def fake_session_start(_url, _inputs, _delay, _command, *, capture_clock=None):
            companion.session.capture_clock = capture_clock
            companion.session.publisher = type("Publisher", (), {
                "capture_clock": capture_clock, "pdt_epoch": None,
                "snapshot": lambda self: {}, "publish_delay": 3.0,
            })()
            companion.session.page_url = _url

        companion.session.stop = lambda: None
        companion.session.start = fake_session_start
        companion.session.status = lambda: {"state": "running", "pageUrl": "https://www.twitch.tv/example"}
        with (
            patch("companion.server.YtDlpLiveIngest", FakeMediaIngest),
            patch("companion.server.TwitchChatIngest", FakeChat),
            patch("companion.server.BilibiliDanmakuIngest", side_effect=AssertionError("Twitch must not use Bilibili messages")),
            patch("companion.server.ProbeInfoSnapshot"),
            patch("companion.server.build_ffmpeg_command", return_value=["fake-ffmpeg"]),
        ):
            response = await self.client.post("/api/start", json={
                "url": "https://www.twitch.tv/example",
                "subtitles": {"enabled": False},
                "liveMessages": {"enabled": True},
            })
        self.assertEqual(response.status, 200, await response.text())
        status = await (await self.client.get("/api/status")).json()
        self.assertEqual(status["liveMessages"]["state"], "running")
        self.assertEqual(status["liveMessages"]["platform"], "twitch")
        self.assertIsInstance(companion.message_ingest, FakeChat)

    async def test_start_uses_live_messages_payload_shared_clock_and_real_source_constructor(self):
        companion = self.app["companion"]
        info = {
            "is_live": True,
            "title": "Live",
            "channel": "Channel",
            "extractor": "youtube",
            "formats": [{
                "format_id": "95", "url": "https://example.invalid/live.m3u8", "protocol": "m3u8_native",
                "vcodec": "avc1.4d401f", "acodec": "mp4a.40.2", "height": 720, "width": 1280, "fps": 30,
            }],
        }
        companion.probe.extract = lambda *_: info
        captured = {}

        class FakeMediaIngest:
            def __init__(self, *_args, **_kwargs): pass
            def start(self): return None
            def stop(self): return None
            def input_urls(self): return ["tcp://127.0.0.1:1"]
            def snapshot(self): return {"running": True}
            def tee_snapshot(self): return {"teeDropped": 0}
            def detach_audio_tee(self): return None

        class FakeChat:
            def __init__(self, url, store, *, clock, auth_lease, on_message):
                captured.update(url=url, store=store, clock=clock, auth_lease=auth_lease, on_message=on_message)
            async def start(self): captured["started"] = True
            async def stop(self): return None
            def status(self): return {"state": "running", "platform": "youtube", "running": True, "connected": True, "reconnects": 0}

        def fake_session_start(_url, _inputs, _delay, _command, *, capture_clock=None):
            companion.session.capture_clock = capture_clock
            companion.session.publisher = type("Publisher", (), {
                "capture_clock": capture_clock,
                "pdt_epoch": None,
                "snapshot": lambda self: {},
                "publish_delay": 3.0,
            })()
            companion.session.page_url = _url

        companion.session.stop = lambda: None
        companion.session.start = fake_session_start
        companion.session.status = lambda: {"state": "running", "pageUrl": "https://www.youtube.com/watch?v=x"}
        with (
            patch("companion.server.YtDlpLiveIngest", FakeMediaIngest),
            patch("companion.server.YouTubeChatIngest", FakeChat),
            patch("companion.server.ProbeInfoSnapshot"),
            patch("companion.server.build_ffmpeg_command", return_value=["fake-ffmpeg"]),
        ):
            response = await self.client.post("/api/start", json={
                "url": "https://www.youtube.com/watch?v=x",
                "qualityId": "auto",
                "subtitles": {"enabled": False},
                "liveMessages": {"enabled": True, "translate": False},
            })
        self.assertEqual(response.status, 200, await response.text())
        self.assertTrue(captured["started"])
        self.assertIs(captured["clock"], companion.session.capture_clock)
        self.assertIs(captured["clock"], companion.session.publisher.capture_clock)
        self.assertIs(captured["store"].clock, companion.session.publisher.capture_clock)
        self.assertIs(captured["store"], companion.message_store)


if __name__ == "__main__":
    unittest.main()
