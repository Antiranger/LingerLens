"""Provider connections follow the UI network mode through the environment.

aiohttp ignores ``HTTP(S)_PROXY`` by default, so before ``net_proxy`` every ASR
and translation connection went direct whatever the user selected. Soniox is
only reachable through a proxy from some networks, which showed up as
intermittent ``ConnectionTimeoutError`` and no subtitles.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from companion.providers.net_proxy import proxy_for, proxy_kwargs  # noqa: E402

_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy")


class ProxyForTests(unittest.TestCase):
    def run_with(self, url: str, **values: str) -> str | None:
        saved = {name: os.environ.pop(name) for name in _NAMES if name in os.environ}
        try:
            with patch.dict(os.environ, values):
                return proxy_for(url)
        finally:
            os.environ.update(saved)

    def test_no_proxy_configured_goes_direct(self) -> None:
        self.assertIsNone(self.run_with("wss://stt-rt.soniox.com/transcribe-websocket"))

    def test_wss_uses_https_proxy(self) -> None:
        self.assertEqual(
            self.run_with("wss://stt-rt.soniox.com/transcribe-websocket", HTTPS_PROXY="http://127.0.0.1:7890"),
            "http://127.0.0.1:7890",
        )

    def test_ws_uses_http_proxy_then_all_proxy(self) -> None:
        self.assertEqual(self.run_with("ws://example.com/x", HTTP_PROXY="http://p:1"), "http://p:1")
        self.assertEqual(self.run_with("ws://example.com/x", ALL_PROXY="http://p:2"), "http://p:2")

    def test_local_and_private_hosts_never_use_the_proxy(self) -> None:
        for url in ("http://127.0.0.1:8045/v1", "http://localhost:8045/v1", "http://192.168.1.5/v1", "http://[::1]:8045/v1"):
            with self.subTest(url=url):
                self.assertIsNone(self.run_with(url, HTTPS_PROXY="http://p:1", HTTP_PROXY="http://p:1"))

    def test_no_proxy_list_is_honoured(self) -> None:
        self.assertIsNone(self.run_with("https://api.example.com/v1", HTTPS_PROXY="http://p:1", NO_PROXY="example.com"))

    def test_socks_proxy_falls_back_to_direct(self) -> None:
        # aiohttp cannot tunnel through SOCKS; failing every connection would be worse.
        with self.assertLogs("companion.providers.net_proxy", level="WARNING"):
            self.assertIsNone(self.run_with("wss://stt-rt.soniox.com/x", HTTPS_PROXY="socks5://127.0.0.1:1080"))

    def test_proxy_kwargs_is_empty_without_a_proxy(self) -> None:
        saved = {name: os.environ.pop(name) for name in _NAMES if name in os.environ}
        try:
            self.assertEqual(proxy_kwargs("wss://stt-rt.soniox.com/x"), {})
            with patch.dict(os.environ, {"HTTPS_PROXY": "http://p:1"}):
                self.assertEqual(proxy_kwargs("wss://stt-rt.soniox.com/x"), {"proxy": "http://p:1"})
        finally:
            os.environ.update(saved)


if __name__ == "__main__":
    unittest.main()
