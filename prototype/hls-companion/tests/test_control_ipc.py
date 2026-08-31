from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("laglingo_control_ipc", ROOT / "companion" / "control_ipc.py")
assert SPEC and SPEC.loader
IPC = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = IPC
SPEC.loader.exec_module(IPC)


class ControlIpcTests(unittest.TestCase):
    def test_authenticated_control_round_trip(self) -> None:
        server = IPC.ControlServer(lambda message: {"ok": True, "echo": message.get("value")})
        server.start()
        try:
            self.assertEqual(IPC.send_control({"value": "local-only"}), {"ok": True, "echo": "local-only"})
        finally:
            server.stop()
            if server.thread:
                server.thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
