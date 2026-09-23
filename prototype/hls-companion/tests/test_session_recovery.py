"""Whole-session recovery tests.

The recovery owner is the Companion session, so a downloader or packaging
failure rebuilds the source clock, subtitles, and player handoff together.
These tests keep the classification and bounded retry contract deterministic
without contacting a real streaming service.
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from companion.server import CompanionApplication


def application() -> CompanionApplication:
    root = Path(tempfile.mkdtemp())
    (root / "media").mkdir(parents=True, exist_ok=True)
    return CompanionApplication(
        argparse.Namespace(
            runtime_dir=root / "media",
            providers_file=root / "providers.json",
            publish_delay=2,
            cookies_from_browser=None,
        ),
        enable_native_control=False,
    )


class RecoveryClassificationTests(unittest.TestCase):
    def test_downloader_exit_is_a_whole_session_failure(self) -> None:
        reason = CompanionApplication._session_recovery_reason(
            {"state": "running", "playlistReady": True, "targetDuration": 5},
            {"running": False, "sourceError": None},
        )
        self.assertEqual(reason, "直播下载进程已退出")

    def test_source_error_wins_over_idle_measurement(self) -> None:
        reason = CompanionApplication._session_recovery_reason(
            {"state": "running", "playlistReady": True, "targetDuration": 1},
            {"running": False, "sourceError": "signed media URL expired", "sourceIdleSeconds": 60},
        )
        self.assertEqual(reason, "signed media URL expired")

    def test_idle_threshold_scales_with_segment_duration(self) -> None:
        healthy = CompanionApplication._session_recovery_reason(
            {"state": "running", "playlistReady": True, "targetDuration": 5},
            {"running": True, "sourceIdleSeconds": 6.9},
        )
        stalled = CompanionApplication._session_recovery_reason(
            {"state": "running", "playlistReady": True, "targetDuration": 5},
            {"running": True, "sourceIdleSeconds": 7.1},
        )
        self.assertIsNone(healthy)
        self.assertIn("没有媒体数据", stalled)

    def test_packaging_stall_is_rebuilt_even_when_download_is_healthy(self) -> None:
        reason = CompanionApplication._session_recovery_reason(
            {
                "state": "running",
                "playlistReady": True,
                "targetDuration": 1,
                "sourceStallSeconds": 8,
            },
            {"running": True, "sourceIdleSeconds": 0.2},
        )
        self.assertIn("媒体分片", reason)

    def test_startup_without_a_playlist_has_a_grace_period(self) -> None:
        before = CompanionApplication._session_recovery_reason(
            {"state": "running", "playlistReady": False, "uptimeSeconds": 40},
            {"running": True, "sourceIdleSeconds": 2},
        )
        after = CompanionApplication._session_recovery_reason(
            {"state": "running", "playlistReady": False, "uptimeSeconds": 50},
            {"running": True, "sourceIdleSeconds": 2},
        )
        self.assertIsNone(before)
        self.assertIn("没有产生可播放分片", after)


class BoundedRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """Stub the operations recovery calls, never the lock wrapper itself.

    These tests used to replace ``_session_operation`` with a catch-all, so the
    call site could pass keyword arguments the real wrapper would have refused.
    Production raised TypeError on every attempt for exactly that reason while
    the suite stayed green.
    """

    async def test_successful_recovery_reprobes_and_uses_one_new_session(self) -> None:
        app = application()
        app._active_start_body = {"url": "https://www.youtube.com/watch?v=test", "qualityId": "auto"}
        calls: list[tuple[str, dict]] = []

        async def start_session_body(
            body: dict, *, automatic: bool, force_probe: bool
        ) -> dict:
            calls.append(("_start_session_body", {"automatic": automatic, "force_probe": force_probe}))
            return {"ok": True}

        app._start_session_body = start_session_body  # type: ignore[method-assign]
        await app._recover_session("yt-dlp exited")

        self.assertEqual([name for name, _ in calls], ["_start_session_body"])
        self.assertTrue(calls[0][1]["automatic"])
        self.assertTrue(calls[0][1]["force_probe"])
        self.assertEqual(app._recovery_state, "monitoring")
        self.assertFalse(app._recovery_in_progress)

    async def test_recovery_stops_after_three_attempts(self) -> None:
        app = application()
        app._active_start_body = {"url": "https://www.youtube.com/watch?v=test"}
        calls: list[str] = []

        async def start_session_body(
            body: dict, *, automatic: bool, force_probe: bool
        ) -> None:
            calls.append("_start_session_body")
            raise RuntimeError("live is over")

        app._start_session_body = start_session_body  # type: ignore[method-assign]
        with patch("companion.server.SESSION_RECOVERY_BACKOFF_SECONDS", (0, 0, 0)):
            await app._recover_session("直播下载进程已退出")

        self.assertEqual(calls.count("_start_session_body"), 3)
        self.assertEqual(app._recovery_state, "failed")
        self.assertIsNone(app._active_start_body)
        self.assertFalse(app._recovery_in_progress)

    async def test_a_refused_teardown_still_ends_the_recovery_supervisor(self) -> None:
        """A decoder holding a live task refuses to report a clean teardown.

        The refusal must stay a report rather than become a supervisor crash with
        the session still claimed: that is what made every later status poll
        re-emit the same error forever.
        """
        app = application()
        app._active_start_body = {"url": "https://www.youtube.com/watch?v=test"}

        async def start_session_body(
            body: dict, *, automatic: bool, force_probe: bool
        ) -> None:
            raise RuntimeError("live is over")

        async def teardown() -> None:
            raise RuntimeError("subtitle decoder teardown did not finish: stdout drain")

        app._start_session_body = start_session_body  # type: ignore[method-assign]
        app._teardown_session = teardown  # type: ignore[method-assign]
        with patch("companion.server.SESSION_RECOVERY_BACKOFF_SECONDS", (0, 0, 0)):
            await app._recover_session("直播下载进程已退出")

        self.assertIsNone(app._active_start_body, "a stuck session cannot stay claimed")
        self.assertEqual(app._recovery_state, "failed")
        self.assertFalse(app._recovery_in_progress)
        self.assertIn("已尝试", app._recovery_error or "")

    def test_replay_body_does_not_retain_auth_token_or_cookie_values(self) -> None:
        app = application()
        body = {
            "url": "https://www.youtube.com/watch?v=test",
            "authToken": "temporary-token",
            "cookies": [{"name": "SID", "value": "secret"}],
            "targetDelaySeconds": 15,
        }
        replay = app._replayable_start_body(body)
        self.assertNotIn("authToken", replay)
        self.assertNotIn("cookies", replay)
        self.assertEqual(replay["targetDelaySeconds"], 15)


if __name__ == "__main__":
    unittest.main()
