import io
import socket
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from companion.ytdlp_ingest import YtDlpLiveIngest, _TcpPump


class IngestStabilityTests(unittest.TestCase):
    def test_a_leg_that_never_produced_bytes_is_not_hidden_by_healthy_audio(self):
        ingest = YtDlpLiveIngest('https://example.test/live', 'video+audio', [], yt_dlp='unused')
        ingest.started_at = 100
        ingest.pumps = [SimpleNamespace(last_byte_at=None), SimpleNamespace(last_byte_at=129.9)]
        with patch('companion.ytdlp_ingest.time.monotonic', return_value=130):
            self.assertEqual(ingest._source_idle_seconds(), 30)

    def test_stop_unblocks_a_pump_when_the_decoder_stops_reading(self):
        # A real loopback socket with a non-reading peer reaches sendall
        # backpressure. Closing only the listener cannot release this thread.
        entered = threading.Event()
        class Source:
            def read1(self, _size):
                entered.set()
                return b'x' * (8 * 1024 * 1024)
        pump = _TcpPump('blocked')
        pump.start(Source())
        client = socket.socket()
        client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        client.connect(('127.0.0.1', pump.port))
        try:
            self.assertTrue(entered.wait(1))
            time.sleep(.03)
            pump.stop(timeout=.5)
            self.assertFalse(pump._thread.is_alive(), 'a blocked send must not survive Stop')
        finally:
            client.close()
            pump.stop(timeout=1)

    def test_start_failure_cleans_up_already_spawned_legs(self):
        class Process:
            stdout = io.BytesIO(b'')
            stderr = io.BytesIO(b'')
            returncode = None
            def poll(self): return self.returncode
            def kill(self): self.returncode = -1
            def wait(self, timeout=None): return self.returncode
        process = Process()
        cleaned = []
        ingest = YtDlpLiveIngest('https://example.test/live', 'video+audio', [], yt_dlp='unused', auth_cleanup=lambda: cleaned.append(True))
        try:
            with patch.object(ingest, '_spawn', side_effect=[process, OSError('second leg refused')]):
                with self.assertRaises(OSError):
                    ingest.start()
            self.assertIsNotNone(process.poll(), 'partial startup must not leave a downloader alive')
            self.assertEqual(ingest.pumps, [])
            self.assertEqual(cleaned, [True])
        finally:
            ingest.stop()


if __name__ == '__main__':
    unittest.main()
