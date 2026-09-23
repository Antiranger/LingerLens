"""D1: the status payload keeps what a reader can act on, and drops the promise.

`sourceRecovery` published a state and an action -- "reconnecting",
"reconnect-and-report" -- that nothing in this repository or in the browser
extension ever read. It was a recovery promise no code kept, printed next to real
fields a reader could act on, and the two ways to make it honest were to delete it
or to wire it up. It is deleted: wiring it up means automatically restarting a
download leg, and a leg restart resets `source_pts_first`, which drops the exact
subtitle anchor to a path measured 4.44 seconds wrong.

The user approved deleting the whole block (plan v2's pre-implementation approval
list, item D1), which is the one decision the plan said could not be answered from
inside the repository.

These tests drive the REAL handler. A test that greps server.py for the string
"sourceRecovery" would pass on a handler that still emitted it under another
name; a test that reads the returned JSON cannot.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import types
import unittest
from importlib.util import find_spec
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.server import CompanionApplication  # noqa: E402

# Every key this payload is allowed to have lost, and the ones a reader uses.
SURVIVING_KEYS = ("sourceIngest", "sourceDelaySeconds", "videoContentSeconds", "state")
REMOVED_KEYS = ("sourceRecovery",)


class FakeIngest:
    """A download leg that records being started, and refuses to be restarted."""

    def __init__(self, snapshot: dict) -> None:
        self._snapshot = snapshot
        self.calls: list[str] = []

    def snapshot(self) -> dict:
        return dict(self._snapshot)

    def start(self, *_args, **_kwargs):
        self.calls.append("start")
        raise AssertionError("status must not start a download leg")

    def restart(self, *_args, **_kwargs):
        self.calls.append("restart")
        raise AssertionError("status must not restart a download leg")

    def stop(self, *_args, **_kwargs):
        self.calls.append("stop")
        raise AssertionError("status must not stop a download leg")


class StatusContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "media").mkdir(parents=True, exist_ok=True)

    def _application(self) -> CompanionApplication:
        return CompanionApplication(
            argparse.Namespace(
                runtime_dir=self.root / "media",
                providers_file=self.root / "providers.json",
                publish_delay=2,
                cookies_from_browser=None,
            )
        )

    async def _status(self, application: CompanionApplication) -> dict:
        response = await application.status(None)
        return json.loads(response.text)

    async def test_an_idle_server_publishes_no_recovery_projection(self) -> None:
        """D: the block is gone from the branch that used to emit "idle"."""
        payload = await self._status(self._application())
        for key in REMOVED_KEYS:
            self.assertNotIn(key, payload)
        for key in SURVIVING_KEYS:
            self.assertIn(key, payload)
        self.assertEqual(payload["sessionRecovery"]["state"], "idle")
        self.assertEqual(payload["sessionRecovery"]["attempts"], 0)

    async def test_a_running_but_silent_source_publishes_no_recovery_projection(self) -> None:
        """D: this is the branch that built the block from the policy's decision."""
        application = self._application()
        ingest = FakeIngest(
            {"sourceIdleSeconds": 45.0, "running": True, "sourceError": None}
        )
        application.source_ingest = ingest

        payload = await self._status(application)

        for key in REMOVED_KEYS:
            self.assertNotIn(key, payload)
        self.assertEqual(ingest.calls, [], "status must not touch the download leg")
        self.assertEqual(payload["sourceIngest"][0]["sourceIdleSeconds"], 45.0)
        self.assertIn("subtitles", payload)
        self.assertIn("mediaClock", payload)
        self.assertIn("targetDelaySeconds", payload)

    async def test_an_errored_source_still_reports_the_error_without_the_block(self) -> None:
        """D: the error branch keeps its own real fields."""
        application = self._application()
        application.source_ingest = FakeIngest(
            {"sourceIdleSeconds": 45.0, "running": False, "sourceError": "yt-dlp exited"}
        )
        # The promotion below only happens for a session the server believes is
        # running, so the session is the one thing doubled here.
        application.session.status = lambda: {"state": "running"}

        payload = await self._status(application)

        self.assertNotIn("sourceRecovery", payload)
        self.assertEqual(payload["state"], "error")
        self.assertEqual(payload["error"], "yt-dlp exited")

    async def test_reading_status_never_starts_or_restarts_a_leg(self) -> None:
        """G: repeated silent-source polls change nothing about the leg."""
        application = self._application()
        ingest = FakeIngest(
            {"sourceIdleSeconds": 45.0, "running": True, "sourceError": None}
        )
        application.source_ingest = ingest

        for _ in range(3):
            await self._status(application)

        self.assertEqual(ingest.calls, [])
        self.assertEqual(application.session.status()["state"], "idle")

    async def test_unknown_broadcast_delay_is_not_fabricated_from_the_target(self) -> None:
        application = self._application()
        application.target_delay_seconds = 20
        application.session.status = lambda: {
            "state": "running", "hiddenMediaSeconds": 8, "privateEdgeWallTime": 1234.5,
        }
        payload = await self._status(application)
        self.assertEqual(payload["privateEdgeWallTime"], 1234.5)
        self.assertEqual(payload["targetDelaySeconds"], 20)
        self.assertIsNone(payload["sourceDelaySeconds"])
        self.assertIsNone(payload["estimatedTotalDelaySeconds"])


class WiringRemovalTests(unittest.TestCase):
    def test_the_policy_module_is_gone(self) -> None:
        """D: no wiring can consult a module that no longer exists."""
        self.assertIsNone(find_spec("companion.recovery_policy"))

    def test_the_application_has_no_policy_member(self) -> None:
        """D: the only construction site is gone with it."""
        self.assertFalse(hasattr(CompanionApplication, "recovery_policy"))
        self.assertNotIn("recovery_policy", vars(CompanionApplication(self._namespace())))

    def test_a_reintroduced_policy_would_be_caught(self) -> None:
        """D: a tripwire, so re-adding the wiring cannot pass unnoticed.

        A fake module is installed BEFORE the application is built. If any code
        path still imported it -- lazily, inside status(), say -- or called
        decide(), this raises instead of quietly publishing the block again.
        """
        calls: list[dict] = []

        def explode(**_kwargs):
            calls.append(_kwargs)
            raise AssertionError("the recovery policy must not be consulted")

        fake = types.ModuleType("companion.recovery_policy")
        fake.RecoveryPolicy = type("RecoveryPolicy", (), {"decide": explode})
        fake.quiet_threshold = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("quiet_threshold must not be consulted")
        )
        self.addCleanup(sys.modules.pop, "companion.recovery_policy", None)
        sys.modules["companion.recovery_policy"] = fake

        application = CompanionApplication(self._namespace())
        self.assertEqual(calls, [])
        self.assertFalse(hasattr(application, "recovery_policy"))

    def _namespace(self) -> argparse.Namespace:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "media").mkdir(parents=True, exist_ok=True)
        return argparse.Namespace(
            runtime_dir=root / "media",
            providers_file=root / "providers.json",
            publish_delay=2,
            cookies_from_browser=None,
        )


if __name__ == "__main__":
    unittest.main()
