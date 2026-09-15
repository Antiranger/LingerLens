from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from aiohttp.test_utils import AioHTTPTestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import companion.core as core_module
import companion.server as server_module
from companion import logbook
from companion.core import LiveSession, QualityOption, SelectedInputs
from companion.logbook import Logbook
from companion.server import CompanionApplication, errors


class LogbookRingTests(unittest.TestCase):
    """The ring contract the UI polls: seq, cursor, eviction, message hygiene."""

    def setUp(self) -> None:
        self.book = Logbook()

    def test_sequence_starts_at_one_and_never_repeats(self) -> None:
        seqs = [self.book.record("info", "media", f"line {index}") for index in range(5)]
        self.assertEqual(seqs, [1, 2, 3, 4, 5])
        snapshot = self.book.snapshot(0)
        self.assertEqual([record["seq"] for record in snapshot["records"]], seqs)
        self.assertEqual(snapshot["maxSeq"], 5)
        self.assertEqual(snapshot["dropped"], 0)

    def test_after_seq_returns_only_newer_records(self) -> None:
        for index in range(4):
            self.book.record("info", "media", f"line {index}")
        snapshot = self.book.snapshot(2)
        self.assertEqual([record["seq"] for record in snapshot["records"]], [3, 4])
        self.assertEqual(snapshot["records"][0]["message"], "line 2")
        # A cursor at the head means "nothing new", not "everything again":
        # the UI polls this route on a timer.
        self.assertEqual(self.book.snapshot(snapshot["maxSeq"])["records"], [])

    def test_missing_or_malformed_cursor_is_treated_as_zero(self) -> None:
        self.book.record("info", "media", "one")
        for cursor in (None, "", "abc", "-3", "1.5", "0x2", object()):
            with self.subTest(cursor=cursor):
                self.assertEqual(len(self.book.snapshot(cursor)["records"]), 1)

    def test_ring_evicts_oldest_and_counts_dropped(self) -> None:
        ring = Logbook(capacity=4)
        for index in range(10):
            ring.record("info", "media", f"line {index}")
        snapshot = ring.snapshot(0)
        self.assertEqual(
            [record["message"] for record in snapshot["records"]],
            ["line 6", "line 7", "line 8", "line 9"],
        )
        self.assertEqual(snapshot["dropped"], 6)
        # maxSeq stays ahead of the evicted records, so a UI that was away can
        # say "you missed lines" instead of stalling on a cursor it can reach.
        self.assertEqual(snapshot["maxSeq"], 10)
        self.assertEqual([record["seq"] for record in ring.snapshot(3)["records"]], [7, 8, 9, 10])

    def test_message_is_flattened_to_one_line_and_capped(self) -> None:
        self.book.record("warn", "media", "first\r\nsecond\nthird\rfourth")
        self.book.record("warn", "media", "x" * 900)
        first, second = self.book.snapshot(0)["records"]
        self.assertNotIn("\n", first["message"])
        self.assertNotIn("\r", first["message"])
        self.assertEqual(first["message"], "first second third fourth")
        self.assertTrue(second["message"].endswith("…"))
        self.assertEqual(len(second["message"]), 400)

    def test_urls_are_redacted(self) -> None:
        self.book.record("error", "media", "input https://cdn.example/live.m3u8?token=secret failed")
        message = self.book.snapshot(0)["records"][0]["message"]
        self.assertNotIn("secret", message)
        self.assertIn(logbook.REDACTED_URL, message)

    def test_record_shape_levels_and_timestamp(self) -> None:
        before = time.time()
        for level in ("info", "warn", "error"):
            self.book.record(level, "request", "ok")
        after = time.time()
        records = self.book.snapshot(0)["records"]
        self.assertEqual([record["level"] for record in records], ["info", "warn", "error"])
        for record in records:
            self.assertEqual(set(record), {"seq", "t", "level", "source", "message"})
            self.assertEqual(record["source"], "request")
            self.assertIsInstance(record["t"], float)
            self.assertGreaterEqual(record["t"], before)
            self.assertLessEqual(record["t"], after)

    def test_level_and_source_are_clamped_to_the_contract(self) -> None:
        self.book.record("", "MEDIA!!", "kept anyway")
        record = self.book.snapshot(0)["records"][0]
        self.assertEqual(record["level"], "info")
        self.assertEqual(record["source"], "media")

    def test_session_id_is_stable_hex(self) -> None:
        session_id = self.book.snapshot(0)["sessionId"]
        self.assertRegex(session_id, r"^[0-9a-f]{8}$")
        self.book.record("info", "media", "one")
        self.assertEqual(self.book.snapshot(0)["sessionId"], session_id)

    def test_reset_restarts_the_sequence_and_keeps_the_session(self) -> None:
        session_id = self.book.snapshot(0)["sessionId"]
        self.book.record("info", "media", "one")
        self.book.reset()
        snapshot = self.book.snapshot(0)
        self.assertEqual(snapshot["records"], [])
        self.assertEqual(snapshot["maxSeq"], 0)
        self.assertEqual(snapshot["dropped"], 0)
        self.assertEqual(snapshot["sessionId"], session_id)
        self.assertEqual(self.book.record("info", "media", "two"), 1)


class DefaultLogbookTests(unittest.TestCase):
    def setUp(self) -> None:
        logbook.reset()

    def tearDown(self) -> None:
        logbook.reset()

    def test_module_level_helpers_use_one_shared_instance(self) -> None:
        logbook.record("info", "desktop", "hello")
        self.assertEqual(logbook.default_logbook().snapshot(0)["maxSeq"], 1)
        self.assertEqual(logbook.snapshot(0)["records"][0]["message"], "hello")
        self.assertEqual(logbook.snapshot(1)["records"], [])


class RequestTargetTests(unittest.TestCase):
    def test_request_target_never_echoes_the_url(self) -> None:
        # The private-HLS path token is the credential that keeps FFmpeg's copy
        # of the delayed stream private; a request log must never carry it.
        request = SimpleNamespace(
            path="/_private-hls/super-secret-token/live.m3u8",
            match_info=SimpleNamespace(
                route=SimpleNamespace(resource=SimpleNamespace(canonical="/_private-hls/{token}/{name}"))
            ),
        )
        self.assertEqual(server_module._request_target(request), "/_private-hls/{token}/{name}")


class LogApiTests(AioHTTPTestCase):
    async def get_application(self):
        logbook.reset()
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        args = argparse.Namespace(
            runtime_dir=root / "media",
            providers_file=root / "providers.json",
            publish_delay=2.0,
            cookies_from_browser=None,
            host="127.0.0.1",
            port=8765,
        )
        companion = CompanionApplication(args)
        companion.control.start = lambda: None
        companion.control.stop = lambda: None
        app = companion.routes()
        app.middlewares.append(errors)
        return app

    async def asyncTearDown(self) -> None:
        await super().asyncTearDown()
        self.temporary.cleanup()

    async def test_logs_route_returns_the_documented_shape(self) -> None:
        logbook.record("info", "media", "FFmpeg packaging started")
        response = await self.client.get("/api/logs")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(set(payload), {"records", "maxSeq", "dropped", "sessionId"})
        self.assertEqual(payload["maxSeq"], 1)
        self.assertEqual(payload["dropped"], 0)
        self.assertRegex(payload["sessionId"], r"^[0-9a-f]{8}$")
        record = payload["records"][0]
        self.assertEqual(set(record), {"seq", "t", "level", "source", "message"})
        self.assertEqual(record["seq"], 1)
        self.assertEqual(record["level"], "info")
        self.assertEqual(record["source"], "media")
        self.assertEqual(record["message"], "FFmpeg packaging started")
        self.assertIsInstance(record["t"], float)

    async def test_logs_route_filters_by_cursor_and_tolerates_junk(self) -> None:
        for index in range(3):
            logbook.record("info", "media", f"line {index}")
        payload = await (await self.client.get("/api/logs?afterSeq=2")).json()
        self.assertEqual([record["seq"] for record in payload["records"]], [3])
        payload = await (await self.client.get("/api/logs?afterSeq=3")).json()
        self.assertEqual(payload["records"], [])
        self.assertEqual(payload["maxSeq"], 3)
        for query in ("", "?afterSeq=", "?afterSeq=nonsense", "?afterSeq=-2"):
            with self.subTest(query=query):
                payload = await (await self.client.get(f"/api/logs{query}")).json()
                self.assertEqual(len(payload["records"]), 3)

    async def test_rejected_request_is_logged_as_warn_request(self) -> None:
        response = await self.client.post("/api/target-delay", json={"seconds": "soon"})
        self.assertEqual(response.status, 400)
        records = logbook.snapshot(0)["records"]
        self.assertEqual([(record["level"], record["source"]) for record in records], [("warn", "request")])
        self.assertIn("POST /api/target-delay", records[0]["message"])
        self.assertIn("target total delay must be a number", records[0]["message"])

    async def test_unexpected_failure_is_logged_as_error_request(self) -> None:
        with patch.object(server_module, "load_config", side_effect=KeyError("providers")):
            response = await self.client.get("/api/languages")
        self.assertEqual(response.status, 500)
        records = logbook.snapshot(0)["records"]
        self.assertEqual([(record["level"], record["source"]) for record in records], [("error", "request")])
        self.assertIn("KeyError", records[0]["message"])
        self.assertIn("GET /api/languages", records[0]["message"])


class _RunningProcess:
    """Stand-in for a child that stays alive, so no real FFmpeg is spawned."""

    def __init__(self) -> None:
        self.stdin = io.BytesIO()
        self.stdout = None
        self.stderr = io.BytesIO(b"")

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0


class _ExitedProcess:
    def __init__(self, code: int) -> None:
        self.code = code
        self.stdin = None
        self.stdout = None
        self.stderr = None

    def poll(self) -> int:
        return self.code


def _selected_inputs() -> SelectedInputs:
    quality = QualityOption(
        qualityId="auto",
        label="auto",
        width=1280,
        height=720,
        fps=30.0,
        videoCodec="avc1",
        audioCodec="mp4a",
        separateAudio=False,
        requiresTranscode=False,
        estimatedBitrate=1200,
        videoFormatId="95",
        audioFormatId=None,
    )
    return SelectedInputs(
        quality=quality,
        video_url="https://media.example/live.m3u8",
        audio_url=None,
        video_headers={},
        audio_headers={},
    )


class MediaLifecycleLogTests(unittest.TestCase):
    """LiveSession is the only place that knows FFmpeg's fate; it must log it."""

    def setUp(self) -> None:
        logbook.reset()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.session = LiveSession(Path(self.temporary.name) / "media")

    def tearDown(self) -> None:
        self.session.stop()

    def test_ffmpeg_launch_is_logged_as_media_info(self) -> None:
        with patch.object(core_module.subprocess, "Popen", return_value=_RunningProcess()):
            self.session.start("https://live.example/watch?v=1", _selected_inputs(), 3.0, ["fake-ffmpeg"])
        records = logbook.snapshot(0)["records"]
        self.assertEqual([(record["level"], record["source"]) for record in records], [("info", "media")])
        self.assertIn("auto", records[0]["message"])

    def test_unlaunchable_ffmpeg_is_logged(self) -> None:
        with patch.object(core_module.subprocess, "Popen", side_effect=FileNotFoundError("ffmpeg")):
            with self.assertRaises(FileNotFoundError):
                self.session.start("https://live.example/watch?v=1", _selected_inputs(), 3.0, ["fake-ffmpeg"])
        records = logbook.snapshot(0)["records"]
        self.assertEqual([(record["level"], record["source"]) for record in records], [("error", "media")])
        self.assertIn("could not be started", records[0]["message"])

    def test_unexpected_ffmpeg_exit_is_logged_with_the_tail(self) -> None:
        self.session.process = _ExitedProcess(9)
        self.session.log_tail.extend(
            ["frame= 120 fps=25", "https://media.example/live.m3u8?token=secret: Server returned 403"]
        )
        status = self.session.status()
        self.assertEqual(status["state"], "error")
        self.assertIn("code 9", status["error"])
        # The existing status contract must not change: same tail, same shape.
        self.assertEqual(status["ffmpegLogTail"], list(self.session.log_tail)[-6:])
        records = logbook.snapshot(0)["records"]
        self.assertEqual(records[0]["level"], "error")
        self.assertEqual(records[0]["source"], "media")
        self.assertIn("code 9", records[0]["message"])
        # The same FFmpeg tail is visible in the log, with the signed URL the
        # status payload still carries redacted on the way in.
        self.assertEqual(records[1]["message"], "frame= 120 fps=25")
        self.assertEqual(records[2]["message"], f"{logbook.REDACTED_URL} Server returned 403")
        self.assertNotIn("secret", json.dumps(records))

    def test_repeated_status_polls_do_not_repeat_the_failure(self) -> None:
        self.session.process = _ExitedProcess(1)
        self.session.log_tail.append("Conversion failed!")
        self.session.status()
        first = logbook.snapshot(0)["maxSeq"]
        self.session.status()
        self.session.status()
        self.assertEqual(logbook.snapshot(0)["maxSeq"], first)


if __name__ == "__main__":
    unittest.main()
