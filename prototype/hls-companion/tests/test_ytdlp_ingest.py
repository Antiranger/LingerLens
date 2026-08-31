from __future__ import annotations

import importlib.util
import io
import os
import socket
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("laglingo_ytdlp_ingest", ROOT / "companion" / "ytdlp_ingest.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class YtDlpLiveIngestTests(unittest.TestCase):
    def test_command_owns_live_download_and_does_not_expose_cookie_values(self) -> None:
        ingest = MODULE.YtDlpLiveIngest(
            "https://www.youtube.com/watch?v=test",
            "312+234",
            ["--cookies", "restricted-cookie-file.txt"],
            yt_dlp="yt-dlp.exe",
        )
        self.assertEqual(ingest.selectors, ["312", "234"])
        command = ingest.command("312")
        self.assertIn("--no-live-from-start", command)
        self.assertIn("--fragment-retries", command)
        self.assertIn("infinite", command)
        self.assertIn("312", command)
        self.assertNotIn("312+234", command)
        self.assertEqual(command[-3:], ["-o", "-", "https://www.youtube.com/watch?v=test"])
        self.assertNotIn("Cookie:", " ".join(command))

    def test_command_forwards_environment_proxy_to_hls_downloaders(self) -> None:
        previous = os.environ.get("ALL_PROXY")
        os.environ["ALL_PROXY"] = "http://127.0.0.1:7890"
        try:
            ingest = MODULE.YtDlpLiveIngest(
                "https://www.youtube.com/watch?v=test", "301", [], yt_dlp="yt-dlp.exe"
            )
            command = ingest.command()
        finally:
            if previous is None:
                os.environ.pop("ALL_PROXY", None)
            else:
                os.environ["ALL_PROXY"] = previous
        self.assertEqual(command[command.index("--proxy") + 1], "http://127.0.0.1:7890")
        external_args = command[command.index("--downloader-args") + 1]
        self.assertIn("ffmpeg_i:-http_proxy http://127.0.0.1:7890", external_args)

    def test_live_hls_uses_the_native_downloader_so_fragments_run_concurrently(self) -> None:
        # yt-dlp hands live HLS to its internal ffmpeg by default, which fetches
        # segments one at a time and ignores --concurrent-fragments entirely.
        # A single connection to googlevideo is bandwidth-capped well below a
        # 1080p rendition on this link, so the concurrency has to be real.
        ingest = MODULE.YtDlpLiveIngest("https://www.youtube.com/watch?v=test", "96", [], yt_dlp="yt-dlp.exe")
        command = ingest.command()
        self.assertEqual(command[command.index("--downloader") + 1], "m3u8:native")
        self.assertGreater(int(command[command.index("--concurrent-fragments") + 1]), 1)

    def test_probe_info_json_replaces_the_page_url_so_extraction_runs_once(self) -> None:
        ingest = MODULE.YtDlpLiveIngest(
            "https://www.youtube.com/watch?v=test",
            "96",
            [],
            yt_dlp="yt-dlp.exe",
            info_json_path="restricted-info.json",
        )
        command = ingest.command()
        self.assertEqual(command[-4:], ["-o", "-", "--load-info-json", "restricted-info.json"])
        self.assertNotIn("https://www.youtube.com/watch?v=test", command)

    def test_muxed_selector_spawns_a_single_leg(self) -> None:
        ingest = MODULE.YtDlpLiveIngest(
            "https://www.youtube.com/watch?v=test",
            "96",
            [],
            yt_dlp="yt-dlp.exe",
        )
        self.assertEqual(ingest.selectors, ["96"])

    def test_redacts_signed_urls_from_status_log(self) -> None:
        redacted = MODULE.YtDlpLiveIngest._redact("Opening https://rr.example.googlevideo.com/path?sig=secret for reading")
        self.assertNotIn("secret", redacted)
        self.assertIn("<REDACTED_URL>", redacted)

    def test_tcp_pump_tee_receives_bytes_and_isolates_sink_failure(self) -> None:
        payload = b"audio-transport-stream"
        pump = MODULE._TcpPump("test")
        received: list[bytes] = []

        def sink(chunk: bytes) -> None:
            received.append(chunk)
            raise RuntimeError("subtitle consumer failed")

        pump.set_tee(sink)
        pump.start(io.BytesIO(payload))
        client = socket.create_connection(("127.0.0.1", pump.port), timeout=2)
        try:
            client.settimeout(2)
            self.assertEqual(client.recv(len(payload)), payload)
            deadline = time.monotonic() + 1
            while not received and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(b"".join(received), payload)
            self.assertEqual(pump.tee_dropped, 1)
        finally:
            client.close()
            pump.stop()

    def test_audio_tee_selects_audio_or_muxed_leg(self) -> None:
        ingest = MODULE.YtDlpLiveIngest("https://www.youtube.com/watch?v=test", "312+234", [], yt_dlp="yt-dlp.exe")
        ingest.pumps = [MODULE._TcpPump("video"), MODULE._TcpPump("audio")]
        sink = lambda _: None
        try:
            ingest.attach_audio_tee(sink)
            self.assertIsNone(ingest.pumps[0]._tee)
            self.assertIs(ingest.pumps[1]._tee, sink)
            ingest.detach_audio_tee()
            self.assertIsNone(ingest.pumps[1]._tee)
        finally:
            for pump in ingest.pumps:
                pump.stop()

    def test_credentials_are_released_once_every_leg_produces_media(self) -> None:
        # yt-dlp block-buffers its own stderr on a pipe, so "[info] Downloading"
        # can sit unflushed for the whole session. Forwarded bytes are the only
        # timely proof that a leg is past extraction; until then the cookie and
        # probe-info files have to stay on disk for the other legs.
        calls: list[str] = []
        ingest = MODULE.YtDlpLiveIngest(
            "https://www.youtube.com/watch?v=test",
            "312+234",
            [],
            yt_dlp="yt-dlp.exe",
            auth_cleanup=lambda: calls.append("closed"),
        )
        ingest._leg_reached_download(0)()
        self.assertEqual(calls, [], "one leg must not strip credentials from the other")
        ingest._leg_reached_download(0)()
        self.assertEqual(calls, [], "the same leg reporting twice must not count twice")
        ingest._leg_reached_download(1)()
        self.assertEqual(calls, ["closed"])

    def test_pump_reports_the_first_forwarded_byte(self) -> None:
        seen: list[str] = []
        pump = MODULE._TcpPump("test", on_first_byte=lambda: seen.append("live"))
        pump.start(io.BytesIO(b"transport-stream-bytes"))
        client = socket.create_connection(("127.0.0.1", pump.port), timeout=2)
        try:
            client.settimeout(2)
            client.recv(64)
            deadline = time.monotonic() + 1
            while not seen and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(seen, ["live"])
        finally:
            client.close()
            pump.stop()

    def test_auth_cleanup_runs_once(self) -> None:
        calls: list[str] = []
        ingest = MODULE.YtDlpLiveIngest(
            "https://www.youtube.com/watch?v=test",
            "312+234",
            [],
            yt_dlp="yt-dlp.exe",
            auth_cleanup=lambda: calls.append("closed"),
        )
        ingest._cleanup_auth()
        ingest._cleanup_auth()
        self.assertEqual(calls, ["closed"])


if __name__ == "__main__":
    unittest.main()
