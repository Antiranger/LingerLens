"""Authenticated local IPC for sensitive Native Messaging control data."""

from __future__ import annotations

import json
import os
import secrets
import stat
import tempfile
import threading
from multiprocessing.connection import Client, Listener
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
SECRET_FILE = RUNTIME / "control.secret"
ADDRESS = r"\\.\pipe\lingerlens_hls_companion_v2" if os.name == "nt" else str(Path(tempfile.gettempdir()) / f"lingerlens-hls-{os.getuid()}.sock")
FAMILY = "AF_PIPE" if os.name == "nt" else "AF_UNIX"


def write_secret() -> bytes:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(32)
    SECRET_FILE.write_bytes(secret)
    os.chmod(SECRET_FILE, stat.S_IRUSR | stat.S_IWUSR)
    return secret


def read_secret() -> bytes:
    secret = SECRET_FILE.read_bytes()
    if len(secret) != 32:
        raise RuntimeError("Invalid Companion control secret")
    return secret


def send_control(message: dict[str, Any]) -> dict[str, Any]:
    connection = Client(ADDRESS, family=FAMILY, authkey=read_secret())
    try:
        connection.send_bytes(json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        response = json.loads(connection.recv_bytes(4 * 1024 * 1024).decode("utf-8"))
        if not isinstance(response, dict):
            raise RuntimeError("Invalid Companion control response")
        return response
    finally:
        connection.close()


class ControlServer:
    def __init__(self, handler: Callable[[dict[str, Any]], dict[str, Any]]):
        self.handler = handler
        self.secret = write_secret()
        self.listener: Listener | None = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()

    def start(self) -> None:
        if FAMILY == "AF_UNIX":
            Path(ADDRESS).unlink(missing_ok=True)
        self.listener = Listener(ADDRESS, family=FAMILY, authkey=self.secret)
        self.thread = threading.Thread(target=self._run, name="native-control-ipc", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        assert self.listener
        while not self.stop_event.is_set():
            try:
                connection = self.listener.accept()
            except (OSError, EOFError):
                return
            try:
                raw = connection.recv_bytes(4 * 1024 * 1024)
                message = json.loads(raw.decode("utf-8"))
                if not isinstance(message, dict):
                    raise ValueError("Control message must be an object")
                response = self.handler(message)
            except Exception as error:
                response = {"ok": False, "error": str(error)}
            try:
                connection.send_bytes(json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            finally:
                connection.close()

    def stop(self) -> None:
        self.stop_event.set()
        if self.listener:
            self.listener.close()
            self.listener = None
        SECRET_FILE.unlink(missing_ok=True)
        if FAMILY == "AF_UNIX":
            Path(ADDRESS).unlink(missing_ok=True)
