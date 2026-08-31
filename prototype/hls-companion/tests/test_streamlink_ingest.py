from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("laglingo_streamlink_ingest", ROOT / "companion" / "streamlink_ingest.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class StreamlinkIngestTests(unittest.TestCase):
    def test_filters_sensitive_or_unsafe_headers(self) -> None:
        ingest = MODULE.StreamlinkHlsIngest(
            "https://example.com/live.m3u8",
            {"User-Agent": "ua", "Cookie": "secret", "Referer": "https://example.com/", "Origin": "bad\r\nInjected: yes"},
        )
        self.assertEqual(ingest.headers, {"User-Agent": "ua", "Referer": "https://example.com/"})
        self.assertEqual(ingest.live_edge_segments, 5)


if __name__ == "__main__":
    unittest.main()
