"""Exercise the real browser-mode routes without live media or credentials."""
from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import AioHTTPTestCase, make_mocked_request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from companion.server import CompanionApplication, require_local_request


class LocalRequestGuardTests(AioHTTPTestCase):
    async def get_application(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.companion = CompanionApplication(argparse.Namespace(
            runtime_dir=root / "media", providers_file=root / "providers.json",
            host="127.0.0.1", port=8765, publish_delay=3.0, cookies_from_browser=None,
        ), enable_native_control=False)
        return self.companion.routes()

    async def test_untrusted_sites_cannot_read_or_control_the_companion(self):
        with patch.object(self.companion, "_teardown_session", new_callable=AsyncMock) as stop:
            for headers in (
                {"Origin": "https://untrusted.example"},
                {"Origin": "http://127.0.0.1:1"},
                {"Origin": "null"},
                {"Host": "untrusted.example"},
                {"Sec-Fetch-Site": "cross-site"},
            ):
                for route in ("/api/status", "/api/model-settings", "/"):
                    response = await self.client.get(route, headers=headers)
                    self.assertEqual(response.status, 403, (route, headers))
                # text/plain is a simple request: browsers can send it without
                # a CORS preflight. It must not reach any control handler.
                response = await self.client.post("/api/stop", data="{}", headers=headers)
                self.assertEqual(response.status, 403, headers)
            stop.assert_not_awaited()

    async def test_same_origin_and_non_browser_clients_can_use_local_routes(self):
        origin = str(self.client.make_url("/")).rstrip("/")
        for headers in ({}, {"Origin": origin, "Sec-Fetch-Site": "same-origin"}):
            response = await self.client.get("/api/status", headers=headers)
            self.assertEqual(response.status, 200)
            with patch.object(self.companion, "_teardown_session", new_callable=AsyncMock) as stop:
                response = await self.client.post("/api/stop", json={}, headers=headers)
                self.assertEqual(response.status, 200)
                stop.assert_awaited_once()

    def test_ipv6_and_default_ports_are_valid_origins(self):
        for host, origin in (("[::1]:8765", "http://[::1]:8765"),
                             ("localhost:80", "http://localhost")):
            require_local_request(make_mocked_request("GET", "/", headers={"Host": host, "Origin": origin}))


if __name__ == "__main__":
    unittest.main()
