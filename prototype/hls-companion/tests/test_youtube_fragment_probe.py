from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "laglingo_youtube_fragment_probe",
    ROOT / "scripts" / "youtube_fragment_403_probe.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class YoutubeFragmentProbeTests(unittest.TestCase):
    def test_path_style_sequence_is_extracted_and_replaced_without_exposing_query(self) -> None:
        url = "https://rr.example.googlevideo.com/videoplayback/id/test/itag/301/noclen/1/sq/123/goap/x/file/seg.ts"
        self.assertEqual(MODULE.sequence_from_url(url), 123)
        replaced = MODULE.replace_sequence(url, 124)
        self.assertEqual(MODULE.sequence_from_url(replaced), 124)
        self.assertIn("/sq/124/", replaced)
        self.assertNotIn("?sq=", replaced)

    def test_query_style_sequence_is_extracted_and_replaced(self) -> None:
        url = "https://rr.example.googlevideo.com/videoplayback?id=test&itag=299&noclen=1&sq=88"
        self.assertEqual(MODULE.sequence_from_url(url), 88)
        replaced = MODULE.replace_sequence(url, 89)
        self.assertEqual(MODULE.sequence_from_url(replaced), 89)
        self.assertIn("sq=89", replaced)

    def test_headers_follow_ytarchive_shape_and_drop_sensitive_values(self) -> None:
        clean = MODULE.safe_headers({"User-Agent": "original", "Cookie": "secret", "Authorization": "secret", "Origin": "https://youtube.com"})
        headers = MODULE.ytarchive_headers("https://rr.example.googlevideo.com/videoplayback?itag=299&noclen=1&sq=1", clean)
        self.assertEqual(headers["Host"], "rr.example.googlevideo.com")
        self.assertEqual(headers["Referer"], "https://rr.example.googlevideo.com/")
        self.assertEqual(headers["Origin"], "https://www.youtube.com")
        self.assertNotIn("Cookie", headers)
        self.assertNotIn("Authorization", headers)


if __name__ == "__main__":
    unittest.main()
