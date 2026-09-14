"""Kill an isolated dummy parent; its job must reap only its dummy child."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import unittest


@unittest.skipUnless(os.name == 'nt', 'Windows process ownership')
class WindowsJobTests(unittest.TestCase):
    def test_parent_crash_reaps_child(self):
        script = (
            'import sys,subprocess,time; sys.path.insert(0,sys.argv[1]); '
            'from windows_job import own_process_tree; job=own_process_tree(); '
            'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"], '
            'creationflags=subprocess.CREATE_NO_WINDOW); '
            'print(child.pid,flush=True); time.sleep(60)'
        )
        parent = subprocess.Popen([sys._base_executable, '-c', script, str(Path(__file__).resolve().parents[1])],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        child_handle = None
        try:
            lines = queue.Queue()
            threading.Thread(target=lambda: lines.put(parent.stdout.readline()), daemon=True).start()
            child_pid = int(lines.get(timeout=10))
            child_handle = kernel.OpenProcess(0x100001, False, child_pid)  # SYNCHRONIZE | TERMINATE
            self.assertTrue(child_handle)
            parent.kill()
            parent.wait(timeout=5)
            self.assertEqual(kernel.WaitForSingleObject(child_handle, 5000), 0)
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=5)
            if child_handle:
                if kernel.WaitForSingleObject(child_handle, 0) != 0:
                    kernel.TerminateProcess(child_handle, 1)
                kernel.CloseHandle(child_handle)
            parent.stdout.close()
            parent.stderr.close()
