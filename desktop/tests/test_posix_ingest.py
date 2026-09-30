"""A stopped Mac download must release pipes inherited by FFmpeg grandchildren."""
import os
from pathlib import Path
import queue
import signal
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'prototype/hls-companion'))
from companion.ytdlp_ingest import YtDlpLiveIngest


@unittest.skipIf(os.name == 'nt', 'POSIX process groups')
class PosixIngestTests(unittest.TestCase):
    def test_stop_reaps_descendants_even_after_their_parent_exits(self):
        for parent_exits in (False, True):
            with self.subTest(parent_exits=parent_exits):
                ingest = YtDlpLiveIngest('https://example.test/live', 'video', [], yt_dlp='unused')
                child = 'import time; print("ready", flush=True); time.sleep(60)'
                parent = ('import subprocess, sys, time; '
                          f'subprocess.Popen([sys.executable, "-c", {child!r}]); '
                          + ('time.sleep(0.1)' if parent_exits else 'time.sleep(60)'))
                process = ingest._spawn([sys.executable, '-c', parent])
                ingest.processes.append(process)
                lines = queue.Queue()
                reader = threading.Thread(target=lambda: (lines.put(process.stdout.readline()),
                                                           lines.put(process.stdout.read())), daemon=True)
                reader.start()
                try:
                    self.assertEqual(lines.get(timeout=5).strip(), b'ready')
                    if parent_exits:
                        process.wait(timeout=5)
                    stopped = threading.Thread(target=ingest.stop, daemon=True)
                    stopped.start()
                    stopped.join(timeout=5)
                    self.assertFalse(stopped.is_alive(), 'Stop blocked on a surviving descendant')
                    self.assertEqual(lines.get(timeout=2), b'', 'grandchild still owns the output pipe')
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)
                    reader.join(timeout=2)
                    for stream in (process.stdout, process.stderr):
                        if stream:
                            stream.close()


if __name__ == '__main__':
    unittest.main()
