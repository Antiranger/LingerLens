from __future__ import annotations

import json
import os
import stat
import importlib.util
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_SPEC = importlib.util.spec_from_file_location("laglingo_hls_core", ROOT / "companion" / "core.py")
assert CORE_SPEC and CORE_SPEC.loader
CORE = importlib.util.module_from_spec(CORE_SPEC)
sys.modules[CORE_SPEC.name] = CORE
CORE_SPEC.loader.exec_module(CORE)
BrowserCookieSnapshot = CORE.BrowserCookieSnapshot
DelayedPlaylistPublisher = CORE.DelayedPlaylistPublisher
QualityOption = CORE.QualityOption
SelectedInputs = CORE.SelectedInputs
build_ffmpeg_command = CORE.build_ffmpeg_command
build_quality_options = CORE.build_quality_options
select_quality = CORE.select_quality
validate_page_url = CORE.validate_page_url


class ProbeExecutableTests(unittest.TestCase):
    def test_probe_uses_vendored_yt_dlp_when_path_has_none(self) -> None:
        vendored = ROOT / "vendor" / "yt-dlp" / "yt-dlp.exe"
        self.assertTrue(vendored.is_file())
        with patch.object(CORE.shutil, "which", return_value=None):
            probe = CORE.YtDlpProbe()
        self.assertEqual(probe.yt_dlp, str(vendored))

    def test_probe_respects_explicit_executable_override(self) -> None:
        with patch.object(CORE.shutil, "which", return_value=None):
            probe = CORE.YtDlpProbe("C:/tools/custom-yt-dlp.exe")
        self.assertEqual(probe.yt_dlp, "C:/tools/custom-yt-dlp.exe")


class FormatSelectionTests(unittest.TestCase):
    def test_prefers_compatible_1080p_and_can_pair_separate_audio(self) -> None:
        info = {
            "formats": [
                {"format_id": "a1", "url": "https://audio.example/a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128},
                {"format_id": "v1", "url": "https://video.example/v", "width": 1920, "height": 1080, "fps": 60, "vcodec": "avc1.64002a", "acodec": "none", "tbr": 6000},
                {"format_id": "m1", "url": "https://video.example/m", "width": 1280, "height": 720, "fps": 30, "vcodec": "avc1.4d401f", "acodec": "mp4a.40.2", "tbr": 2500},
                {"format_id": "vp9", "url": "https://video.example/vp9", "width": 1920, "height": 1080, "fps": 30, "vcodec": "vp09.00.40.08", "acodec": "opus", "tbr": 3500},
            ]
        }
        options = build_quality_options(info)
        selected = select_quality(options, "auto")
        self.assertEqual(selected.height, 1080)
        self.assertTrue(selected.separateAudio)
        self.assertFalse(selected.requiresTranscode)
        self.assertTrue(any(option.requiresTranscode for option in options))

    def test_rejects_non_platform_url(self) -> None:
        with self.assertRaises(ValueError):
            validate_page_url("https://example.com/live")


class CookieTests(unittest.TestCase):
    def test_snapshot_is_restricted_filtered_and_deleted(self) -> None:
        snapshot = BrowserCookieSnapshot(
            [
                {"domain": ".youtube.com", "path": "/", "name": "SID", "value": "secret", "secure": True},
                {"domain": ".example.com", "path": "/", "name": "bad", "value": "skip"},
            ]
        )
        path = Path(snapshot.yt_dlp_args()[1])
        try:
            content = path.read_text(encoding="utf-8")
            self.assertIn("youtube.com", content)
            self.assertNotIn("example.com", content)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        finally:
            snapshot.close()
        self.assertFalse(path.exists())


class FfmpegCommandTests(unittest.TestCase):
    def test_stream_copy_fmp4_command_uses_two_inputs_without_cookie_header(self) -> None:
        quality = QualityOption("1080p", "1080p", 1920, 1080, 60, "avc1", "mp4a", True, False, 6000000, "v", "a")
        selected = SelectedInputs(
            quality,
            "https://video.example/v.m3u8",
            "https://audio.example/a.m3u8",
            {"User-Agent": "ua", "Cookie": "do-not-leak"},
            {"Referer": "https://youtube.com/"},
        )
        command = build_ffmpeg_command(selected, Path("runtime/private"), ffmpeg="ffmpeg")
        rendered = " ".join(command)
        self.assertIn("-c copy", rendered)
        self.assertIn("-hls_segment_type fmp4", rendered)
        # Long enough that a player sitting well behind the live edge, or
        # briefly rebuffering, cannot fall off the end of the playlist.
        self.assertEqual(CORE.DelayedPlaylistPublisher(Path("private"), Path("public"), 7).window_seconds, 120)
        self.assertIn("1:a:0", rendered)
        self.assertIn("aac_adtstoasc", rendered)
        # Timestamp-continuity filters guard the MSE timeline against source
        # discontinuities while remaining pure stream copy.
        self.assertIn("-bsf:v setts=", rendered)
        self.assertIn("-bsf:a setts=", rendered)
        self.assertIn("PREV_OUTDURATION", rendered)
        self.assertNotIn("do-not-leak", rendered)
        local_command = build_ffmpeg_command(
            selected,
            Path("runtime/private"),
            ffmpeg="ffmpeg",
            input_urls=["http://127.0.0.1:8765/video.m3u8", "http://127.0.0.1:8765/audio.m3u8"],
        )
        self.assertNotIn("-re", local_command)
        tcp_command = build_ffmpeg_command(
            selected,
            Path("runtime/private"),
            ffmpeg="ffmpeg",
            input_urls=["tcp://127.0.0.1:40001", "tcp://127.0.0.1:40002"],
        )
        tcp_rendered = " ".join(tcp_command)
        # The ingest is always H.264 + AAC in MPEG-TS, so FFmpeg's default 5s
        # probe is dead time in front of the first published segment.
        self.assertIn("-f mpegts -analyzeduration 2000000 -probesize 4000000 -i tcp://127.0.0.1:40001", tcp_rendered)
        self.assertIn("-f mpegts -analyzeduration 2000000 -probesize 4000000 -i tcp://127.0.0.1:40002", tcp_rendered)
        self.assertNotIn("-reconnect", tcp_rendered)
        self.assertIn("-map 1:a:0", tcp_rendered)
        pipe_command = build_ffmpeg_command(
            selected,
            Path("runtime/private"),
            ffmpeg="ffmpeg",
            pipe_input_count=1,
            pipe_format="mpegts",
        )
        self.assertIn("-f mpegts -i pipe:0", " ".join(pipe_command))
        self.assertIn("-map 0:a:0", " ".join(pipe_command))
        self.assertNotIn("1:a:0", pipe_command)

    def test_unexpected_clean_ffmpeg_exit_is_reported_as_error(self) -> None:
        quality = QualityOption("test", "test", 1920, 1080, 30, "avc1", "mp4a", False, False, 1, "v", None)
        selected = SelectedInputs(quality, "https://video.example/live.m3u8", None, {}, {})
        with tempfile.TemporaryDirectory() as raw:
            session = CORE.LiveSession(Path(raw) / "runtime")
            session.start(
                "https://www.youtube.com/watch?v=test",
                selected,
                publish_delay=7,
                command_override=[sys.executable, "-c", "pass"],
            )
            deadline = time.monotonic() + 2
            while session.status()["state"] == "running" and time.monotonic() < deadline:
                time.sleep(0.02)
            status = session.status()
            session.stop()
            self.assertEqual(status["state"], "error")
            self.assertIn("stopped unexpectedly", status["error"])


class PlaylistPublisherTests(unittest.TestCase):
    def test_delays_publication_and_trims_ring(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            private = root / "private"
            public = root / "public"
            private.mkdir()
            public.mkdir()
            (private / "init.mp4").write_bytes(b"init")
            for index in range(4):
                (private / f"seg_{index:09d}.m4s").write_bytes(f"s{index}".encode())
            (private / "live.m3u8").write_text(
                "#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-MAP:URI=\"init.mp4\"\n"
                "#EXT-X-PROGRAM-DATE-TIME:2026-08-30T12:00:00.000+00:00\n"
                + "".join(f"#EXTINF:1.0,\nseg_{index:09d}.m4s\n" for index in range(4)),
                encoding="utf-8",
            )
            publisher = DelayedPlaylistPublisher(private, public, publish_delay=0, window_seconds=2.2)
            publisher._tick()
            playlist = (public / "live.m3u8").read_text(encoding="utf-8")
            self.assertIn("#EXT-X-MAP", playlist)
            self.assertNotIn("seg_000000000.m4s", playlist)
            self.assertIn("seg_000000003.m4s", playlist)
            self.assertLessEqual(len(list(public.glob("*.m4s"))), 9)
            self.assertEqual(publisher.snapshot()["pdtEpoch"], 1788091200.0)
            first_published = [segment.name for segment in publisher.published]
            publisher._tick()
            self.assertEqual([segment.name for segment in publisher.published], first_published)


if __name__ == "__main__":
    unittest.main()
