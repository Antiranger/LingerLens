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
CORE_SPEC = importlib.util.spec_from_file_location("lingerlens_hls_core", ROOT / "companion" / "core.py")
assert CORE_SPEC and CORE_SPEC.loader
CORE = importlib.util.module_from_spec(CORE_SPEC)
sys.modules[CORE_SPEC.name] = CORE
CORE_SPEC.loader.exec_module(CORE)
BrowserCookieSnapshot = CORE.BrowserCookieSnapshot
ProbeInfoSnapshot = CORE.ProbeInfoSnapshot
DelayedPlaylistPublisher = CORE.DelayedPlaylistPublisher
QualityOption = CORE.QualityOption
SelectedInputs = CORE.SelectedInputs
build_ffmpeg_command = CORE.build_ffmpeg_command
build_quality_options = CORE.build_quality_options
select_quality = CORE.select_quality
selected_inputs = CORE.selected_inputs
validate_page_url = CORE.validate_page_url


def _project(info: dict) -> str:
    """What yt-dlp prints for the probe's two --print projections.

    yt-dlp keeps every field named by the template and drops everything else, so
    projecting a full document here is how the tests reproduce its output.
    """
    top = {name: info[name] for name in CORE.PROBE_INFO_FIELDS if name in info}
    formats = [
        {name: item[name] for name in CORE.PROBE_FORMAT_FIELDS if name in item}
        for item in info.get("formats") or []
    ]
    return json.dumps(top, ensure_ascii=False) + "\n" + json.dumps(formats, ensure_ascii=False)


def _fragments(prefix: str, count: int = 3) -> list[dict]:
    return [{"url": f"https://f.invalid/{prefix}/{index}.ts", "duration": 1.0} for index in range(count)]


# A full yt-dlp info document, shaped like the ones a live YouTube probe returns:
# the formats carry fragment lists that grow with the broadcast, and the document
# carries top-level keys the app never reads. Everything the app does read is
# present, so a projection that drops one of those fields changes the menu that
# ProbePayloadTests compares.
FULL_LIVE_INFO = {
    "id": "test",
    "title": "【Star Fox Adventures】MY CHILDHOOD",
    "channel": "Takanashi Kiara Ch. hololive",
    "uploader": "Takanashi Kiara Ch. hololive",
    "categories": ["Gaming"],
    "is_live": True,
    "live_status": "is_live",
    "extractor": "youtube",
    "extractor_key": "Youtube",
    "webpage_url": "https://www.youtube.com/watch?v=test",
    "duration": None,
    "http_headers": {"User-Agent": "Mozilla/5.0", "Referer": "https://www.youtube.com/"},
    "thumbnails": [{"url": "https://i.invalid/1.jpg", "width": 336}],
    "automatic_captions": {"en": [{"url": "https://c.invalid/en.json3"}]},
    "formats": [
        {
            "format_id": "233", "url": "https://m.invalid/audio.m3u8", "ext": "mp4",
            "protocol": "m3u8_native", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128,
            "fragments": _fragments("audio"), "manifest_url": "https://m.invalid/audio.m3u8",
        },
        {
            "format_id": "301", "url": "https://m.invalid/1080.m3u8", "ext": "mp4",
            "protocol": "m3u8_native", "vcodec": "avc1.64002a", "acodec": "none",
            "height": 1080, "width": 1920, "fps": 60, "tbr": 6000,
            "fragments": _fragments("video"), "manifest_url": "https://m.invalid/1080.m3u8",
        },
    ],
}

FULL_BILILIVE_INFO = {
    "id": "22637261",
    "title": "B站直播",
    "channel": "主播",
    "uploader": "主播",
    "categories": ["live"],
    "is_live": True,
    "live_status": "is_live",
    "extractor": "bililive",
    "extractor_key": "BiliLive",
    "webpage_url": "https://live.bilibili.com/22637261",
    "http_headers": {"User-Agent": "Mozilla/5.0"},
    "formats": [
        {
            "format_id": "avc-hls-a", "quality": 10000, "format_note": "原画",
            "url": "https://a.invalid/live.m3u8", "ext": "fmp4", "protocol": "m3u8_native",
            "vcodec": "avc1", "acodec": None, "height": 1080, "width": 1920, "fps": 60, "tbr": 5000,
            "fragments": _fragments("bili"), "manifest_url": "https://a.invalid/live.m3u8",
        },
    ],
}

FULL_TWITCH_INFO = {
    "id": "example_channel",
    "title": "Twitch 直播",
    "channel": "example_channel",
    "uploader": "example_channel",
    "categories": ["Just Chatting"],
    "is_live": True,
    "live_status": "is_live",
    "extractor": "twitch",
    "extractor_key": "TwitchStream",
    "webpage_url": "https://www.twitch.tv/example_channel",
    "http_headers": {"User-Agent": "Mozilla/5.0"},
    "formats": [
        {
            "format_id": "source", "format_note": "Source", "url": "https://usher.invalid/source.m3u8",
            "ext": "mp4", "protocol": "m3u8_native", "vcodec": "avc1", "acodec": None,
            "height": 1080, "width": 1920, "fps": 60, "tbr": 6000, "fragments": _fragments("src"),
        },
        {
            "format_id": "720p60", "format_note": "720p60", "url": "https://usher.invalid/720.m3u8",
            "ext": "mp4", "protocol": "m3u8_native", "vcodec": "avc1", "acodec": "mp4a.40.2",
            "height": 720, "width": 1280, "fps": 60, "tbr": 3500,
        },
    ],
}


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

    def test_probe_rejects_upcoming_and_post_live_results(self) -> None:
        probe = CORE.YtDlpProbe("yt-dlp")
        for info in (
            {"is_live": False, "live_status": "is_upcoming"},
            {"is_live": False, "live_status": "post_live"},
            {"is_live": False, "live_status": "was_live"},
        ):
            completed = type("Completed", (), {"returncode": 0, "stdout": json.dumps(info), "stderr": ""})()
            with self.subTest(info=info), patch.object(CORE.subprocess, "run", return_value=completed), self.assertRaisesRegex(RuntimeError, "Only currently ongoing"):
                probe.extract("https://www.youtube.com/watch?v=test")

    def test_probe_does_not_inherit_desktop_control_stdin(self) -> None:
        probe = CORE.YtDlpProbe("yt-dlp")
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": json.dumps({"is_live": True}), "stderr": ""},
        )()
        with patch.object(CORE.subprocess, "run", return_value=completed) as run:
            probe.extract("https://www.youtube.com/watch?v=test")
        self.assertIs(run.call_args.kwargs["stdin"], CORE.subprocess.DEVNULL)


class ProbePayloadTests(unittest.TestCase):
    """The probe asks for the fields this codebase reads, and nothing else.

    It used to ask for the whole info document. On a DVR live stream that
    document carries every media fragment since the broadcast started: 88,456
    fragments / 153MB / 16.2s on a two-hour stream (2026-09-19) against a hard
    15s deadline that the same stream still met at twenty minutes old. Nothing
    here reads ``fragments``, so that payload was carried across the pipe and
    thrown away.
    """

    # Every key the consumers read, listed here so the projection cannot drift
    # away from them: build_quality_options (height, width, fps, vcodec, acodec,
    # tbr, abr, format_id, protocol, ext, quality, format_note),
    # _build_muxed_live_quality_options (the same, plus quality/format_note for
    # BiliLive's dedupe key), best_aac_audio (abr, tbr, protocol, format_id),
    # selected_inputs (format_id, url, http_headers) and server.py (title,
    # channel, uploader, is_live, categories, extractor_key).
    FORMAT_KEYS = {
        "format_id", "url", "ext", "protocol", "vcodec", "acodec", "height", "width", "fps",
        "tbr", "abr", "vbr", "audio_channels", "quality", "format_note", "filesize", "http_headers",
    }
    INFO_KEYS = {
        "id", "title", "channel", "uploader", "categories", "is_live", "live_status",
        "extractor", "extractor_key", "webpage_url", "duration", "http_headers",
    }

    def extract(self, stdout: str, url: str = "https://www.youtube.com/watch?v=test"):
        probe = CORE.YtDlpProbe("yt-dlp")
        completed = type("Completed", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()
        with patch.object(CORE.subprocess, "run", return_value=completed) as run:
            info = probe.extract(url)
        return info, run.call_args.args[0]

    def test_probe_never_asks_yt_dlp_for_fragment_lists(self) -> None:
        _, command = self.extract(_project(FULL_LIVE_INFO))
        self.assertNotIn("-J", command)
        self.assertNotIn("fragments", " ".join(str(part) for part in command))
        formats_template = next(part for part in command if part.startswith("%(formats.:."))
        info_template = next(
            part for part in command if part.startswith("%(.{") and part != formats_template
        )
        self.assertEqual(self.FORMAT_KEYS, set(CORE.PROBE_FORMAT_FIELDS))
        self.assertEqual(self.INFO_KEYS, set(CORE.PROBE_INFO_FIELDS))
        for name in sorted(self.FORMAT_KEYS):
            self.assertIn(name, formats_template)
        for name in sorted(self.INFO_KEYS):
            self.assertIn(name, info_template)

    def test_probe_merges_the_two_projected_lines_into_one_info_dict(self) -> None:
        info, _ = self.extract(_project(FULL_LIVE_INFO))
        self.assertIs(info["is_live"], True)
        self.assertEqual(info["title"], FULL_LIVE_INFO["title"])
        self.assertEqual([item["format_id"] for item in info["formats"]], ["233", "301"])
        self.assertEqual(info["formats"][1]["url"], "https://m.invalid/1080.m3u8")

    def test_probe_still_accepts_one_full_info_document(self) -> None:
        # An older invocation (or a future change back to -J) must keep working.
        info, _ = self.extract(json.dumps(FULL_LIVE_INFO))
        self.assertIs(info["is_live"], True)
        self.assertEqual(len(info["formats"]), 2)

    def test_the_projected_payload_builds_the_same_menu_as_the_full_one(self) -> None:
        cases = (
            ("youtube separate audio", "https://www.youtube.com/watch?v=test", FULL_LIVE_INFO),
            ("bililive muxed", "https://live.bilibili.com/22637261", FULL_BILILIVE_INFO),
            ("twitch source", "https://www.twitch.tv/example_channel", FULL_TWITCH_INFO),
        )
        for label, url, payload in cases:
            with self.subTest(stream=label):
                full = build_quality_options(payload)
                trimmed, _ = self.extract(_project(payload), url)
                projected = build_quality_options(trimmed)
                self.assertEqual([option.qualityId for option in projected], [option.qualityId for option in full])
                # Labels carry format_note/quality through, so a field dropped
                # from the projection shows up here as "未知清晰度".
                self.assertEqual([option.label for option in projected], [option.label for option in full])
                chosen, reference = select_quality(projected, "auto"), select_quality(full, "auto")
                self.assertEqual(chosen.qualityId, reference.qualityId)
                self.assertEqual(chosen.label, reference.label)
                self.assertEqual(
                    selected_inputs(trimmed, chosen).video_url,
                    selected_inputs(payload, reference).video_url,
                )
                self.assertEqual(
                    selected_inputs(trimmed, chosen).video_headers,
                    selected_inputs(payload, reference).video_headers,
                )

    def test_a_fragment_list_in_the_document_never_reaches_the_projection(self) -> None:
        projected = _project(FULL_LIVE_INFO)
        self.assertNotIn("fragments", projected)
        self.assertLess(len(projected), len(json.dumps(FULL_LIVE_INFO)))


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

    def test_bililive_unknown_media_metadata_still_builds_one_muxed_avc_quality(self) -> None:
        info = {
            "extractor_key": "BiliLive",
            "formats": [{
                "format_id": "bili-avc-hls",
                "format_note": "高清",
                "url": "https://example.invalid/live.m3u8",
                "ext": "fmp4",
                "protocol": "m3u8_native",
                "vcodec": "avc",
                "acodec": None,
                "height": None,
                "width": None,
                "fps": None,
                "tbr": None,
            }],
        }
        options = build_quality_options(info)
        self.assertEqual(len(options), 1)
        option = options[0]
        self.assertEqual(option.videoFormatId, "bili-avc-hls")
        self.assertIsNone(option.audioFormatId)
        self.assertFalse(option.separateAudio)
        self.assertFalse(option.requiresTranscode)
        self.assertIsNone(option.height)
        self.assertIn("未知", option.label)
        inputs = selected_inputs(info, option)
        self.assertIsNone(inputs.audio_url)

    def test_bililive_dedupes_cdn_mirrors_and_auto_prefers_avc_hls_over_flv_and_hevc(self) -> None:
        info = {
            "extractor_key": "BiliLive",
            "formats": [
                {"format_id": "avc-hls-a", "quality": 10000, "format_note": "原画", "url": "https://a.invalid/live.m3u8", "protocol": "m3u8_native", "vcodec": "avc1", "acodec": None, "height": 1080},
                {"format_id": "avc-hls-b", "quality": 10000, "format_note": "原画", "url": "https://b.invalid/live.m3u8", "protocol": "m3u8_native", "vcodec": "avc1", "acodec": None, "height": 1080},
                {"format_id": "avc-flv", "quality": 10000, "format_note": "原画", "url": "https://c.invalid/live.flv", "protocol": "http", "vcodec": "avc1", "acodec": None, "height": 1080},
                {"format_id": "hevc-hls", "quality": 20000, "format_note": "超清", "url": "https://d.invalid/live.m3u8", "protocol": "m3u8_native", "vcodec": "hev1", "acodec": None, "height": 2160},
            ],
        }
        options = build_quality_options(info)
        self.assertEqual(sum(option.videoFormatId.startswith("avc-hls") for option in options), 1)
        self.assertEqual(select_quality(options, "auto").videoFormatId, "avc-hls-a")
        hevc = next(option for option in options if option.videoFormatId == "hevc-hls")
        self.assertTrue(hevc.requiresTranscode)

    def test_twitch_muxed_hls_auto_prefers_source_within_max_height(self) -> None:
        info = {
            "extractor_key": "TwitchStream",
            "formats": [
                {"format_id": "source", "format_note": "Source", "url": "https://usher.invalid/source.m3u8", "protocol": "m3u8_native", "vcodec": "avc1", "acodec": None, "height": 1080, "fps": 60, "tbr": 6000},
                {"format_id": "720p60", "format_note": "720p60", "url": "https://usher.invalid/720.m3u8", "protocol": "m3u8_native", "vcodec": "avc1", "acodec": "mp4a.40.2", "height": 720, "fps": 60, "tbr": 3500},
                {"format_id": "hevc", "format_note": "HEVC", "url": "https://usher.invalid/hevc.m3u8", "protocol": "m3u8_native", "vcodec": "hev1", "acodec": "mp4a.40.2", "height": 720, "fps": 60},
            ],
        }
        options = build_quality_options(info)
        selected = select_quality(options, "auto", max_height=1080)
        self.assertEqual(selected.videoFormatId, "source")
        self.assertFalse(selected.separateAudio)
        self.assertIsNone(selected.audioFormatId)
        self.assertEqual(select_quality(options, "auto", max_height=720).videoFormatId, "720p60")

    def test_page_url_boundaries_accept_live_rooms_and_twitch_channels_only(self) -> None:
        self.assertEqual(validate_page_url("https://live.bilibili.com/22637261?live_from=71002"), "https://live.bilibili.com/22637261?live_from=71002")
        self.assertEqual(validate_page_url("https://www.twitch.tv/example_channel"), "https://www.twitch.tv/example_channel")
        for url in (
            "https://example.com/live",
            "https://www.bilibili.com/video/BV1xx",
            "https://www.twitch.tv/videos/123",
            "https://www.twitch.tv/directory/category/games",
            "https://clips.twitch.tv/TestClip",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_page_url(url)


class ProbeInfoSnapshotTests(unittest.TestCase):
    def test_only_bililive_hls_fmp4_metadata_is_normalized_for_safe_stdout_download(self) -> None:
        bili = ProbeInfoSnapshot({
            "extractor_key": "BiliLive",
            "formats": [
                {"format_id": "hls", "protocol": "m3u8_native", "ext": "fmp4"},
                {"format_id": "flv", "protocol": "http", "ext": "fmp4"},
            ],
        }, ["hls"])
        ordinary = ProbeInfoSnapshot({
            "extractor_key": "Other",
            "formats": [{"format_id": "unsafe", "protocol": "m3u8_native", "ext": "fmp4"}],
        })
        try:
            bili_info = json.loads(Path(bili.path).read_text(encoding="utf-8"))
            ordinary_info = json.loads(Path(ordinary.path).read_text(encoding="utf-8"))
            self.assertEqual(bili_info["formats"][0]["ext"], "mp4")
            self.assertEqual(bili_info["formats"][1]["ext"], "fmp4")
            self.assertEqual(ordinary_info["formats"][0]["ext"], "fmp4")
        finally:
            bili.close()
            ordinary.close()


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
        map_index = command.index("-hls_fmp4_init_filename")
        self.assertEqual(command[map_index + 1], "init.mp4")
        rendered = " ".join(command)
        self.assertIn("-c copy", rendered)
        self.assertIn("-hls_segment_type fmp4", rendered)
        self.assertIn("-hls_list_size 250", rendered)
        self.assertIn("-hls_delete_threshold 60", rendered)
        self.assertIn("delete_segments", rendered)
        # Long enough that a player sitting well behind the live edge, or
        # briefly rebuffering, cannot fall off the end of the playlist.
        publisher = CORE.DelayedPlaylistPublisher(Path("private"), Path("public"), 7)
        self.assertEqual(publisher.window_seconds, 180)
        self.assertEqual(publisher.startup_buffer_seconds, 12)
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
        self.assertIn("-f mpegts -analyzeduration 2000000 -probesize 4000000 -isync 0 -i tcp://127.0.0.1:40002", tcp_rendered)
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

    def test_intentional_stop_does_not_report_clean_ffmpeg_exit_as_error(self) -> None:
        quality = QualityOption("test", "test", 1920, 1080, 30, "avc1", "mp4a", False, False, 1, "v", None)
        selected = SelectedInputs(quality, "https://video.example/live.m3u8", None, {}, {})
        with tempfile.TemporaryDirectory() as raw:
            session = CORE.LiveSession(Path(raw) / "runtime")
            session.start(
                "https://www.youtube.com/watch?v=test",
                selected,
                publish_delay=7,
                command_override=[sys.executable, "-c", "import time; time.sleep(0.2)"],
            )
            # Mirror the flag set by stop() before the child exits. The
            # status path must treat the resulting clean exit as expected.
            session._stop_requested = True
            deadline = time.monotonic() + 2
            while session.process is not None and session.process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            status = session.status()
            session.stop()
            self.assertEqual(status["state"], "idle")
            self.assertIsNone(status["error"])

    def test_request_stop_claims_clean_exit_before_input_cleanup(self) -> None:
        quality = QualityOption("test", "test", 1920, 1080, 30, "avc1", "mp4a", False, False, 1, "v", None)
        selected = SelectedInputs(quality, "https://video.example/live.m3u8", None, {}, {})
        with tempfile.TemporaryDirectory() as raw:
            session = CORE.LiveSession(Path(raw) / "runtime")
            session.start(
                "https://www.youtube.com/watch?v=test",
                selected,
                publish_delay=7,
                command_override=[sys.executable, "-c", "import time; time.sleep(0.2)"],
            )
            # Server cleanup claims the packaging process before closing the
            # independently owned yt-dlp input legs.
            session.request_stop()
            deadline = time.monotonic() + 2
            while session.process is not None and session.process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            status = session.status()
            session.stop()
            self.assertEqual(status["state"], "idle")
            self.assertIsNone(status["error"])


class PlaylistPublisherTests(unittest.TestCase):
    def test_edge_uses_playlist_clock_including_missing_media(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            private, public = root / "private", root / "public"
            private.mkdir()
            public.mkdir()
            self._write_segments(private, 3)
            (private / "live.m3u8").write_text(
                "#EXTM3U\n#EXT-X-PROGRAM-DATE-TIME:2026-08-30T12:00:00Z\n"
                "#EXTINF:1,\nseg_000000000.m4s\n"
                "#EXT-X-PROGRAM-DATE-TIME:2026-08-30T12:00:10Z\n"
                "#EXTINF:2,\nseg_000000001.m4s\n"
                "#EXTINF:3,\nseg_000000002.m4s\n", encoding="utf-8")
            publisher = DelayedPlaylistPublisher(private, public, publish_delay=0, startup_buffer_seconds=0)
            self.assertIsNone(publisher.snapshot()["privateEdgeWallTime"])
            publisher._tick()
            status = publisher.snapshot()
            self.assertEqual(status["privateMediaSeconds"], 6)
            self.assertEqual(status["privateEdgeWallTime"], 1788091215)
            publisher._tick()
            self.assertEqual(publisher.snapshot()["privateEdgeWallTime"], 1788091215)

    @staticmethod
    def _write_segments(private: Path, count: int) -> None:
        (private / "init.mp4").write_bytes(b"init")
        for index in range(count):
            (private / f"seg_{index:09d}.m4s").write_bytes(f"s{index}".encode())
        (private / "live.m3u8").write_text(
            "#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-MAP:URI=\"init.mp4\"\n"
            "#EXT-X-PROGRAM-DATE-TIME:2026-08-30T12:00:00.000+00:00\n"
            + "".join(f"#EXTINF:1.0,\nseg_{index:09d}.m4s\n" for index in range(count)),
            encoding="utf-8",
        )

    def test_initial_playlist_waits_for_player_delay_budget(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            private = root / "private"
            public = root / "public"
            private.mkdir()
            public.mkdir()
            publisher = DelayedPlaylistPublisher(
                private,
                public,
                publish_delay=3,
                startup_buffer_seconds=12,
            )

            self._write_segments(private, 14)
            publisher._tick()
            self.assertFalse((public / "live.m3u8").exists())

            self._write_segments(private, 15)
            publisher._tick()
            playlist = (public / "live.m3u8").read_text(encoding="utf-8")
            self.assertEqual(playlist.count("#EXTINF:"), 12)
            self.assertEqual(publisher.snapshot()["hiddenMediaSeconds"], 3.0)

    def test_delays_publication_and_trims_ring(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            private = root / "private"
            public = root / "public"
            private.mkdir()
            public.mkdir()
            self._write_segments(private, 4)
            publisher = DelayedPlaylistPublisher(
                private,
                public,
                publish_delay=0,
                window_seconds=2.2,
                startup_buffer_seconds=0,
            )
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

    def test_default_public_window_and_files_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            private = root / "private"
            public = root / "public"
            private.mkdir()
            public.mkdir()
            publisher = DelayedPlaylistPublisher(private, public, publish_delay=0, startup_buffer_seconds=0)

            self._write_segments(private, 200)
            publisher._tick()

            playlist = (public / "live.m3u8").read_text(encoding="utf-8")
            self.assertEqual(playlist.count("#EXTINF:"), 180)
            self.assertLessEqual(len(list(public.glob("seg_*.m4s"))), 186)

    def test_source_stall_seconds_tracks_segment_arrival(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            private = root / "private"
            public = root / "public"
            private.mkdir()
            public.mkdir()
            publisher = DelayedPlaylistPublisher(private, public, publish_delay=0, startup_buffer_seconds=0)

            self._write_segments(private, 3)
            with patch("time.monotonic", return_value=1000.0):
                publisher._tick()
            # Healthy stream: a new segment every second keeps the stall near zero.
            with patch("time.monotonic", return_value=1002.0):
                publisher._tick()
                self.assertAlmostEqual(publisher.snapshot()["sourceStallSeconds"], 2.0, places=1)
            # Simulated outage: playlist stops growing, the detector climbs.
            with patch("time.monotonic", return_value=1042.0):
                publisher._tick()
                self.assertAlmostEqual(publisher.snapshot()["sourceStallSeconds"], 42.0, places=1)
                # Recovery: a fresh segment resets the detector immediately.
                self._write_segments(private, 4)
                publisher._tick()
                self.assertAlmostEqual(publisher.snapshot()["sourceStallSeconds"], 0.0, places=1)

    def test_missing_private_segments_are_evicted_from_pending_and_seen_names(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            private = root / "private"
            public = root / "public"
            private.mkdir()
            public.mkdir()
            publisher = DelayedPlaylistPublisher(private, public, publish_delay=0, startup_buffer_seconds=0)

            self._write_segments(private, 4)
            publisher._tick()
            for path in private.glob("seg_*.m4s"):
                path.unlink()
            (private / "live.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
            publisher._tick()

            self.assertEqual(len(publisher.pending), 0)
            self.assertLessEqual(len(publisher.seen_names), len(publisher.published))

    def test_session_stop_removes_all_media_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw) / "runtime"
            session = CORE.LiveSession(runtime)
            session.private_dir.mkdir(parents=True)
            session.public_dir.mkdir(parents=True)
            (session.private_dir / "seg_000000000.m4s").write_bytes(b"private")
            (session.public_dir / "seg_000000000.m4s").write_bytes(b"public")

            session.stop()

            self.assertFalse(runtime.exists())

    def test_session_resolves_relative_runtime_dir_before_ffmpeg_sets_cwd(self) -> None:
        session = CORE.LiveSession(Path(".scratch") / "lingerlens-relative-runtime")

        self.assertTrue(session.runtime_dir.is_absolute())
        self.assertEqual(session.private_dir, session.runtime_dir / "private")
        self.assertEqual(session.public_dir, session.runtime_dir / "public")


if __name__ == "__main__":
    unittest.main()
