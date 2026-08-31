from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("laglingo_hls_ingest", ROOT / "companion" / "hls_ingest.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
parse_media_playlist = MODULE.parse_media_playlist
SourceHlsProxy = MODULE.SourceHlsProxy


class HlsIngestTests(unittest.TestCase):
    def test_parses_sequence_relative_segments_and_init_map(self) -> None:
        text = """#EXTM3U
#EXT-X-TARGETDURATION:2
#EXT-X-MEDIA-SEQUENCE:42
#EXT-X-MAP:URI="init.mp4"
#EXTINF:2.0,
seg42.ts
#EXTINF:2.0,
https://cdn-b.example/seg43.ts
"""
        segments, target = parse_media_playlist(text, "https://cdn-a.example/live/index.m3u8")
        self.assertEqual(target, 2)
        self.assertEqual([segment.sequence for segment in segments], [42, 43])
        self.assertEqual(segments[0].url, "https://cdn-a.example/live/seg42.ts")
        self.assertEqual(segments[1].url, "https://cdn-b.example/seg43.ts")
        self.assertEqual(segments[0].init_url, "https://cdn-a.example/live/init.mp4")

    def test_publishes_only_a_contiguous_locally_cached_tail(self) -> None:
        proxy = SourceHlsProxy("https://source.example/live.m3u8", {})
        segments, _ = parse_media_playlist(
            """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:42
#EXTINF:2.0,
a.ts
#EXTINF:2.0,
b.ts
#EXTINF:2.0,
c.ts
""",
            "https://cdn.example/live.m3u8",
        )
        proxy.source_delay_seconds = 0
        proxy.cache = {42: b"a", 44: b"c"}
        eligible = proxy._eligible_segments(segments)
        proxy._advance_advertised(eligible)
        self.assertEqual([item.sequence for item in proxy.advertised], [42])
        proxy.cache[43] = b"b"
        proxy._advance_advertised(eligible)
        self.assertEqual([item.sequence for item in proxy.advertised], [42, 43, 44])
        rewritten = proxy._build_local_playlist(list(proxy.advertised), 2, "/ingest/token")
        self.assertIn("#EXT-X-MEDIA-SEQUENCE:42", rewritten)
        self.assertIn('/ingest/token/segment/44.ts', rewritten)
        self.assertIn('/segment/42.ts', rewritten)
        self.assertNotIn('cdn.example', rewritten)

    def test_long_dvr_playlist_is_reduced_to_recent_source_window(self) -> None:
        segments = [MODULE.SourceSegment(sequence, 1, f"https://cdn/{sequence}.ts", None) for sequence in range(100)]
        tail = SourceHlsProxy._tail_by_duration(segments, 35)
        self.assertEqual(len(tail), 35)
        self.assertEqual((tail[0].sequence, tail[-1].sequence), (65, 99))

    def test_source_delay_holds_back_newest_media(self) -> None:
        proxy = SourceHlsProxy("https://source.example/live.m3u8", {}, source_delay_seconds=5)
        segments = [MODULE.SourceSegment(sequence, 2, f"https://cdn/{sequence}.ts", None) for sequence in range(10, 18)]
        eligible = proxy._eligible_segments(segments)
        self.assertEqual([item.sequence for item in eligible], [10, 11, 12, 13, 14])


if __name__ == "__main__":
    unittest.main()
