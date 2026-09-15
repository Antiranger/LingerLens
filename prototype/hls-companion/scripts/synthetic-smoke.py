#!/usr/bin/env python3
"""Generate a local AVC/AAC live source and verify delayed fMP4 HLS publication."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_SPEC = importlib.util.spec_from_file_location("lingerlens_hls_core", ROOT / "companion" / "core.py")
assert CORE_SPEC and CORE_SPEC.loader
CORE = importlib.util.module_from_spec(CORE_SPEC)
sys.modules[CORE_SPEC.name] = CORE
CORE_SPEC.loader.exec_module(CORE)
LiveSession = CORE.LiveSession
QualityOption = CORE.QualityOption
SelectedInputs = CORE.SelectedInputs
executable = CORE.executable


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="lingerlens-smoke-") as raw:
        runtime = Path(raw) / "runtime"
        quality = QualityOption("synthetic-1080p", "synthetic", 1920, 1080, 30, "avc1", "mp4a", False, False, 2_000_000, "synthetic", None)
        inputs = SelectedInputs(quality, "synthetic", None, {}, {})
        private = runtime / "private"
        # Packaging flags come from core.hls_output_args so this smoke test
        # exercises exactly what production produces. It previously hand-rolled
        # a friendlier list (independent_segments, -g 30, no split_by_time) and
        # therefore could not detect the mid-GOP segment defect.
        command = [
            executable("ffmpeg"), "-hide_banner", "-loglevel", "warning", "-nostdin", "-re",
            "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000",
            "-t", "14", "-c:v", "libx264", "-preset", "veryfast", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "128k", "-map", "0:v:0", "-map", "1:a:0",
            *CORE.hls_output_args(private),
        ]
        session = LiveSession(runtime)
        session.start("https://www.youtube.com/watch?v=synthetic", inputs, publish_delay=3.0, command_override=command)
        deadline = time.monotonic() + 20
        try:
            while time.monotonic() < deadline:
                status = session.status()
                if status.get("playlistReady") and status.get("publishedSegments", 0) >= 3:
                    # Freeze one coherent public-window snapshot: copy the
                    # playlist first, then exactly the files it references.
                    snapshot = Path(raw) / "snapshot"
                    snapshot.mkdir()
                    import re
                    import shutil
                    copied = False
                    playlist_text = ""
                    for _ in range(10):
                        playlist_text = (session.public_dir / "live.m3u8").read_text(encoding="utf-8")
                        names = ["init.mp4", *re.findall(r"^([^#].*\.m4s)$", playlist_text, flags=re.MULTILINE)]
                        try:
                            for name in names:
                                shutil.copyfile(session.public_dir / name.strip(), snapshot / name.strip())
                            copied = True
                            break
                        except FileNotFoundError:
                            time.sleep(0.1)
                    if not copied:
                        raise RuntimeError("Could not freeze a coherent public HLS window")
                    playlist = snapshot / "live.m3u8"
                    playlist.write_text(playlist_text, encoding="utf-8")
                    probe = subprocess.run(
                        [executable("ffprobe"), "-v", "error", "-show_entries", "stream=codec_name,width,height", "-of", "json", str(playlist)],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                    )
                    if probe.returncode != 0:
                        raise RuntimeError(probe.stderr.strip())
                    payload = json.loads(probe.stdout)
                    codecs = {stream.get("codec_name") for stream in payload.get("streams", [])}
                    if not {"h264", "aac"}.issubset(codecs):
                        raise RuntimeError(f"Unexpected codecs: {sorted(codecs)}")
                    print(json.dumps({"ok": True, "status": status, "streams": payload["streams"]}, indent=2))
                    return 0
                if status.get("state") == "error":
                    raise RuntimeError(status.get("error"))
                time.sleep(0.4)
            raise RuntimeError(f"Timed out waiting for delayed public playlist: {session.status()}")
        finally:
            session.stop()


if __name__ == "__main__":
    raise SystemExit(main())
