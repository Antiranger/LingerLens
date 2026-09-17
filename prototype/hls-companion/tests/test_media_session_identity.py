"""S1 (backend half): one media session, one identity.

The desktop page needs to answer a question no existing field can: "is the
session the server is describing the same one I stopped, or a different one that
started afterwards?" A Stop is a local action, so the backend keeps reporting the
old session for as long as its teardown takes, and the poll must not act on that.
Today the only release signal is a sample that happens to say ``idle`` -- and a
poll can legitimately never see one, because teardown finishes between two
samples or because another tab claims the next session first.

``playlistUrl`` is the same string for every session, ``uptimeSeconds`` is a
duration rather than an identity, ``pdtEpoch`` can be absent while a session is
still being prepared, and ``logbook.sessionId`` belongs to the backend process
and outlives every media session inside it. So the identity is generated, not
inferred: 128 random bits per successful start.

This file covers the backend contract only -- when the value exists, that it is
stable, and when it is cleared. The page-side barrier that consumes it is not
landed yet, so nothing here claims the stop/start interleavings are fixed.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import core as CORE

URL = "https://www.youtube.com/watch?v=test"
# A child that stays alive until it is stopped, so "running" is a real state
# rather than a race against process startup.
ALIVE = [sys.executable, "-c", "import time; time.sleep(30)"]


def selected_inputs() -> "CORE.SelectedInputs":
    quality = CORE.QualityOption(
        "test", "test", 1920, 1080, 30, "avc1", "mp4a", False, False, 1, "v", None
    )
    return CORE.SelectedInputs(quality, "https://video.example/live.m3u8", None, {}, {})


class MediaSessionIdentityTests(unittest.TestCase):
    def test_no_session_has_no_identity(self) -> None:
        """G: an idle backend reports no media session at all."""
        with tempfile.TemporaryDirectory() as raw:
            session = CORE.LiveSession(Path(raw) / "runtime")
            status = session.status()
            self.assertIn("mediaSessionId", status)
            self.assertIsNone(status["mediaSessionId"])

    def test_everything_that_could_serve_as_a_key_is_identical_across_two_sessions(self) -> None:
        """D: the same source twice, indistinguishable by anything but the identity."""
        with tempfile.TemporaryDirectory() as raw:
            session = CORE.LiveSession(Path(raw) / "runtime")
            session.start(URL, selected_inputs(), publish_delay=7, command_override=ALIVE)
            first = dict(session.status())
            session.start(URL, selected_inputs(), publish_delay=7, command_override=ALIVE)
            second = dict(session.status())
            session.stop()

        self.assertEqual(first["pageUrl"], second["pageUrl"])
        self.assertEqual(first["quality"], second["quality"])
        self.assertEqual(first["playlistUrl"], second["playlistUrl"])
        self.assertEqual(first["state"], second["state"])
        self.assertNotEqual(
            first["mediaSessionId"],
            second["mediaSessionId"],
            "two media sessions on one source share an identity: the page cannot tell them apart",
        )

    def test_the_identity_is_stable_for_the_whole_session(self) -> None:
        """D: a poll may compare it repeatedly, so it must not be regenerated."""
        with tempfile.TemporaryDirectory() as raw:
            session = CORE.LiveSession(Path(raw) / "runtime")
            session.start(URL, selected_inputs(), publish_delay=7, command_override=ALIVE)
            try:
                first = session.status()["mediaSessionId"]
                self.assertRegex(first, r"^[0-9a-f]{32}$")
                self.assertEqual(session.status()["mediaSessionId"], first)
                self.assertEqual(session.status()["mediaSessionId"], first)
                self.assertEqual(session.media_session_id, first)
            finally:
                session.stop()

    def test_stop_clears_the_identity(self) -> None:
        """D: the stopped session must stop being describable."""
        with tempfile.TemporaryDirectory() as raw:
            session = CORE.LiveSession(Path(raw) / "runtime")
            session.start(URL, selected_inputs(), publish_delay=7, command_override=ALIVE)
            self.assertIsNotNone(session.status()["mediaSessionId"])
            session.stop()
            self.assertIsNone(session.status()["mediaSessionId"])
            self.assertEqual(session.status()["state"], "idle")

    def test_a_failed_start_leaves_no_identity(self) -> None:
        """D: a start that never produced a packaging process is not a session."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            session = CORE.LiveSession(root / "runtime")
            with self.assertRaises(OSError):
                session.start(
                    URL,
                    selected_inputs(),
                    publish_delay=7,
                    command_override=[str(root / "no-such-ffmpeg")],
                )
            self.assertIsNone(session.status()["mediaSessionId"])

    def test_a_failed_restart_does_not_inherit_the_previous_identity(self) -> None:
        """D: the old identity must not survive into a session that never began."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            session = CORE.LiveSession(root / "runtime")
            session.start(URL, selected_inputs(), publish_delay=7, command_override=ALIVE)
            self.assertIsNotNone(session.status()["mediaSessionId"])
            with self.assertRaises(OSError):
                session.start(
                    URL,
                    selected_inputs(),
                    publish_delay=7,
                    command_override=[str(root / "no-such-ffmpeg")],
                )
            self.assertIsNone(
                session.status()["mediaSessionId"],
                "a failed restart left the previous session's identity in place",
            )


if __name__ == "__main__":
    unittest.main()
