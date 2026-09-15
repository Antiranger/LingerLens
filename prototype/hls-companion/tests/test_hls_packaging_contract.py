"""Regression tests for the private-HLS packaging contract.

Before the fix, ``build_ffmpeg_command`` passed ``split_by_time`` together with
``-hls_time 1``. FFmpeg then cut every second regardless of keyframe placement,
so on a 2s-GOP source it emitted 20 one-second segments of which roughly half
began mid-GOP. No MSE player can decode a fragment until the next keyframe, so
any restart, seek or rebuffer that landed on one froze the picture while audio
kept playing. The shipped smoke test could not see this because it hand-rolled a
different (keyframe-aligned) argument list.

These tests pin the contract and the single source of truth.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CORE_SPEC = importlib.util.spec_from_file_location("lingerlens_hls_core_pkg", ROOT / "companion" / "core.py")
assert CORE_SPEC and CORE_SPEC.loader
CORE = importlib.util.module_from_spec(CORE_SPEC)
sys.modules[CORE_SPEC.name] = CORE
CORE_SPEC.loader.exec_module(CORE)

PLAYER_JS = ROOT / "web-player" / "player.js"
SERVER_PY = ROOT / "companion" / "server.py"


def hls_flags(args: list[str]) -> str:
    return args[args.index("-hls_flags") + 1]


def strip_js_comments(source: str) -> str:
    """Remove // comments so assertions test code, not prose about the code."""
    source = re.sub(r"/\*[\s\S]*?\*/", "", source)
    return re.sub(r"(?m)^\s*//.*$", "", source)


def strip_py_comments(source: str) -> str:
    return re.sub(r"(?m)#.*$", "", source)


class PackagingFlagTests(unittest.TestCase):
    def test_split_by_time_is_never_requested(self) -> None:
        args = CORE.hls_output_args(Path("/tmp/private"))
        self.assertNotIn(
            "split_by_time",
            hls_flags(args),
            "split_by_time forces cuts between keyframes, producing segments "
            "an MSE player cannot start decoding",
        )

    def test_independent_segments_is_advertised(self) -> None:
        # The flag set only cuts on keyframes, so the playlist must say so.
        # The pre-fix state advertised nothing while cutting mid-GOP.
        args = CORE.hls_output_args(Path("/tmp/private"))
        self.assertIn("independent_segments", hls_flags(args))

    def test_init_filename_stays_relative(self) -> None:
        # EXT-X-MAP must remain relative so the private playlist can be read
        # through the loopback HTTP endpoint on Windows. FFmpeg resolves the
        # name against its own cwd, which core.py sets to the private dir.
        args = CORE.hls_output_args(Path("/tmp/private"))
        self.assertEqual(args[args.index("-hls_fmp4_init_filename") + 1], "init.mp4")

    def test_segment_and_playlist_paths_live_in_the_private_dir(self) -> None:
        private = Path("/tmp/private")
        args = CORE.hls_output_args(private)
        self.assertEqual(args[args.index("-hls_segment_filename") + 1], str(private / "seg_%09d.m4s"))
        self.assertEqual(args[-1], str(private / "live.m3u8"))

    def test_ffmpeg_is_started_with_the_private_dir_as_cwd(self) -> None:
        # This is what makes the relative init.mp4 land beside the playlist.
        source = (ROOT / "companion" / "core.py").read_text(encoding="utf-8")
        self.assertRegex(source, r"subprocess\.Popen\([\s\S]{0,600}?cwd=self\.private_dir")

    def test_build_ffmpeg_command_reuses_the_shared_args(self) -> None:
        source = (ROOT / "companion" / "core.py").read_text(encoding="utf-8")
        body = source[source.index("def build_ffmpeg_command"):]
        self.assertIn("hls_output_args(private_dir)", body)

    def test_smoke_scripts_share_the_production_args(self) -> None:
        """A smoke test with its own flags cannot detect production defects."""
        for name in ("synthetic-smoke.py", "subtitle-alignment-smoke.py"):
            source = strip_py_comments((ROOT / "scripts" / name).read_text(encoding="utf-8"))
            self.assertNotIn("split_by_time", source, f"{name} still forks the packager")
            self.assertIn("hls_output_args", source, f"{name} must reuse the shared packager")


class DelayCouplingTests(unittest.TestCase):
    """Segment duration must not be able to change playback latency."""

    def test_player_uses_second_based_live_config(self) -> None:
        source = strip_js_comments(PLAYER_JS.read_text(encoding="utf-8"))
        config = source[source.index("new Hls({"):source.index("enableWorker")]
        self.assertIn("liveSyncDuration:", config)
        self.assertIn("liveMaxLatencyDuration:", config)
        self.assertNotIn(
            "liveSyncDurationCount",
            config,
            "count-based latency multiplies by targetduration, so it silently "
            "doubles when the source GOP is 2s instead of 1s",
        )
        self.assertNotIn("liveMaxLatencyDurationCount", config)

    def test_player_live_sync_matches_the_server_constant(self) -> None:
        """server.py derives publish_delay from this; the player must agree."""
        server = strip_py_comments(SERVER_PY.read_text(encoding="utf-8"))
        server_value = float(
            re.search(r"PLAYER_LIVE_SYNC_SECONDS\s*=\s*([0-9.]+)", server).group(1)
        )
        player = strip_js_comments(PLAYER_JS.read_text(encoding="utf-8"))
        player_value = float(re.search(r"liveSyncDuration:\s*([0-9.]+)", player).group(1))
        self.assertEqual(
            server_value,
            player_value,
            "publish delay is target_delay - PLAYER_LIVE_SYNC_SECONDS; if the "
            "player sits further back than that constant the two disagree",
        )

    def test_max_latency_exceeds_target_latency(self) -> None:
        player = strip_js_comments(PLAYER_JS.read_text(encoding="utf-8"))
        target = float(re.search(r"liveSyncDuration:\s*([0-9.]+)", player).group(1))
        maximum = float(re.search(r"liveMaxLatencyDuration:\s*([0-9.]+)", player).group(1))
        self.assertGreater(
            maximum,
            target * 2,
            "hls.js re-seeks whenever latency exceeds the maximum, which reads "
            "as stutter, so it must sit well above the target",
        )


if __name__ == "__main__":
    unittest.main()
