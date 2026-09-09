from __future__ import annotations

import importlib.util
import io
import os
import socket
import sys
import threading
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
        # ffmpeg (the actual live HLS downloader) must NOT be proxied by
        # default: measured on this link the proxied demuxer loses connection
        # reuse and sags below realtime; direct googlevideo sustains 1080p60.
        downloader_args = [command[i + 1] for i, a in enumerate(command) if a == "--downloader-args"]
        self.assertFalse(any("http_proxy" in args for args in downloader_args))

    def test_ffmpeg_proxy_forwarding_is_opt_in_via_env(self) -> None:
        previous_proxy = os.environ.get("ALL_PROXY")
        previous_flag = os.environ.get("LAGLINGO_FFMPEG_PROXY")
        os.environ["ALL_PROXY"] = "http://127.0.0.1:7890"
        os.environ["LAGLINGO_FFMPEG_PROXY"] = "1"
        try:
            ingest = MODULE.YtDlpLiveIngest(
                "https://www.youtube.com/watch?v=test", "301", [], yt_dlp="yt-dlp.exe"
            )
            command = ingest.command()
        finally:
            if previous_proxy is None:
                os.environ.pop("ALL_PROXY", None)
            else:
                os.environ["ALL_PROXY"] = previous_proxy
            if previous_flag is None:
                os.environ.pop("LAGLINGO_FFMPEG_PROXY", None)
            else:
                os.environ["LAGLINGO_FFMPEG_PROXY"] = previous_flag
        downloader_args = [command[i + 1] for i, a in enumerate(command) if a == "--downloader-args"]
        self.assertTrue(any("ffmpeg_i:-http_proxy http://127.0.0.1:7890" in args for args in downloader_args))

    def test_ffmpeg_live_input_reconnects_instead_of_dying(self) -> None:
        ingest = MODULE.YtDlpLiveIngest("https://www.youtube.com/watch?v=test", "301", [], yt_dlp="yt-dlp.exe")
        command = ingest.command()
        downloader_args = [command[i + 1] for i, a in enumerate(command) if a == "--downloader-args"]
        self.assertTrue(any("-reconnect 1" in args and "-reconnect_delay_max 2" in args for args in downloader_args))

    def test_live_hls_requests_native_downloader_and_concurrent_fragments(self) -> None:
        # 2026-09-09: yt-dlp delegates is_live HLS to ffmpeg regardless, so
        # these flags only cover yt-dlp's own HTTP and non-live HLS paths.
        # They stay because they are harmless there and protective off-live.
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

    def test_non_hls_single_leg_does_not_force_the_hls_downloader(self) -> None:
        ingest = MODULE.YtDlpLiveIngest(
            "https://live.bilibili.com/1", "flv-avc", [], yt_dlp="yt-dlp.exe", selected_protocol="http"
        )
        command = ingest.command()
        self.assertNotIn("--downloader", command)
        self.assertNotIn("--hls-use-mpegts", command)

    def test_redacts_signed_urls_from_status_log(self) -> None:
        redacted = MODULE.YtDlpLiveIngest._redact("Opening https://rr.example.googlevideo.com/path?sig=secret for reading")
        self.assertNotIn("secret", redacted)
        self.assertIn("<REDACTED_URL>", redacted)

    def test_snapshot_exposes_leg_throughput_and_log_tail(self) -> None:
        ingest = MODULE.YtDlpLiveIngest(
            "https://www.youtube.com/watch?v=test",
            "312+234",
            [],
            yt_dlp="yt-dlp.exe",
        )
        pump_a = MODULE._TcpPump("video")
        pump_b = MODULE._TcpPump("audio")
        pump_a.forwarded_bytes = 1000
        pump_b.forwarded_bytes = 100
        ingest.pumps = [pump_a, pump_b]
        ingest.log_tail.append("first diagnostic")
        # Redaction happens at append time in _read_log; simulate that here.
        ingest.log_tail.append(MODULE.YtDlpLiveIngest._redact("Opening https://rr.example.googlevideo.com/path?sig=secret for reading"))
        # Controlled clock: snapshot() measures rates between polls, and a
        # same-tick poll pair must not divide by zero (Windows monotonic
        # granularity observed returning elapsed == 0).
        clock = iter([1000.0, 1002.0])
        original_monotonic = MODULE.time.monotonic
        MODULE.time.monotonic = lambda: next(clock)
        try:
            first = ingest.snapshot()
            self.assertEqual(first["legThroughput"][0]["forwardedBytes"], 1000)
            self.assertIsNone(first["legThroughput"][0]["bytesPerSecond"])
            pump_a.forwarded_bytes += 2000
            pump_b.forwarded_bytes += 50
            second = ingest.snapshot()
            self.assertEqual(second["legThroughput"][0]["forwardedBytes"], 3000)
            self.assertEqual(second["legThroughput"][0]["bytesPerSecond"], 1000.0)
            self.assertEqual(second["legThroughput"][1]["bytesPerSecond"], 25.0)
            self.assertEqual(second["legThroughput"][0]["label"], "video")
            self.assertIn("first diagnostic", second["logTail"])
            joined = "\n".join(second["logTail"])
            self.assertNotIn("secret", joined)
            self.assertIn("<REDACTED_URL>", joined)
        finally:
            MODULE.time.monotonic = original_monotonic
            pump_a.stop()
            pump_b.stop()

    def test_tcp_pump_forwards_bytes(self) -> None:
        payload = b"audio-transport-stream"
        pump = MODULE._TcpPump("test")
        pump.start(io.BytesIO(payload))
        client = socket.create_connection(("127.0.0.1", pump.port), timeout=2)
        try:
            client.settimeout(2)
            self.assertEqual(client.recv(len(payload)), payload)
        finally:
            client.close()
            pump.stop()

    def test_stop_claims_and_joins_readers_before_closing_process_streams(self) -> None:
        class BlockingReader:
            def __init__(self) -> None:
                self.entered = threading.Event()
                self.released = threading.Event()
                self.closed = False
                self.closed_while_reading = False

            def read(self, _size: int) -> bytes:
                self.entered.set()
                self.released.wait(2)
                if self.closed:
                    raise ValueError("read of closed file")
                return b""

            def __iter__(self):
                self.entered.set()
                self.released.wait(2)
                if self.closed:
                    raise ValueError("read of closed file")
                return iter(())

            def close(self) -> None:
                if self.entered.is_set() and not self.released.is_set():
                    self.closed_while_reading = True
                self.closed = True
                self.released.set()

        class FakeProcess:
            def __init__(self) -> None:
                self.stdout = BlockingReader()
                self.stderr = BlockingReader()
                self.returncode = None

            def poll(self):
                return self.returncode

            def terminate(self) -> None:
                self.returncode = 0
                self.stdout.released.set()
                self.stderr.released.set()

            def wait(self, timeout=None):
                del timeout
                self.returncode = 0
                return 0

            def kill(self) -> None:
                self.returncode = -9
                self.stdout.released.set()
                self.stderr.released.set()

        ingest = MODULE.YtDlpLiveIngest(
            "https://www.youtube.com/watch?v=test", "96", [], yt_dlp="yt-dlp.exe"
        )
        process = FakeProcess()
        pump = MODULE._TcpPump("video")
        pump.start(process.stdout)
        log_thread = threading.Thread(target=ingest._read_log, args=(process, 0), daemon=True)
        log_thread.start()
        client = socket.create_connection(("127.0.0.1", pump.port), timeout=2)
        try:
            self.assertTrue(process.stdout.entered.wait(1))
            self.assertTrue(process.stderr.entered.wait(1))
            ingest.processes = [process]
            ingest.pumps = [pump]
            ingest._log_threads = [log_thread]
            ingest.stop()
            self.assertFalse(process.stdout.closed_while_reading)
            self.assertFalse(process.stderr.closed_while_reading)
            self.assertFalse(log_thread.is_alive())
        finally:
            client.close()
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

    def test_tcp_pump_uses_available_read_without_waiting_for_full_buffer(self) -> None:
        class PartialReader:
            def __init__(self) -> None:
                self.read1_calls = 0

            def read1(self, _size: int) -> bytes:
                self.read1_calls += 1
                return b"one-live-fragment"

            def read(self, _size: int) -> bytes:
                raise AssertionError("live pump must not wait for BufferedReader.read(size)")

        source = PartialReader()
        pump = MODULE._TcpPump("partial", on_first_byte=None)
        pump.start(source)
        client = socket.create_connection(("127.0.0.1", pump.port), timeout=2)
        try:
            client.settimeout(2)
            received = client.recv(64)
            self.assertTrue(received.startswith(b"one-live-fragment"))
            self.assertGreaterEqual(source.read1_calls, 1)
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
