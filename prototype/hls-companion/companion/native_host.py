#!/usr/bin/env python3
"""Chrome/Edge Native Messaging bridge for Prototype 2 authentication snapshots."""

from __future__ import annotations

import json
import struct
import sys
from typing import Any, BinaryIO

try:
    from .control_ipc import send_control  # type: ignore[import-not-found]
except ImportError:  # Direct script execution.
    from control_ipc import send_control  # type: ignore[import-not-found]

DEFAULT_COMPANION = "http://127.0.0.1:8765"


def read_message(stream: BinaryIO) -> dict[str, Any] | None:
    raw_length = stream.read(4)
    if not raw_length:
        return None
    if len(raw_length) != 4:
        raise EOFError("Incomplete native-message header")
    length = struct.unpack("=I", raw_length)[0]
    if length > 4 * 1024 * 1024:
        raise ValueError("Native message exceeds 4 MiB")
    payload = stream.read(length)
    if len(payload) != length:
        raise EOFError("Incomplete native-message payload")
    message = json.loads(payload.decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("Native message must be an object")
    return message


def write_message(stream: BinaryIO, message: dict[str, Any]) -> None:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    stream.write(struct.pack("=I", len(payload)))
    stream.write(payload)
    stream.flush()


def handle(message: dict[str, Any]) -> dict[str, Any]:
    action = message.get("action")
    if action == "ping":
        return {"ok": True, "companion": DEFAULT_COMPANION}
    if action == "authSnapshot":
        cookies = message.get("cookies")
        if not isinstance(cookies, list):
            raise ValueError("cookies must be an array")
        response = send_control({"action": "authSnapshot", "cookies": cookies})
        if not response.get("ok"):
            raise RuntimeError(response.get("error") or "Companion rejected the authentication snapshot")
        return {"ok": True, "authToken": response["authToken"], "playerUrl": DEFAULT_COMPANION + "/"}
    raise ValueError(f"Unsupported action: {action}")


def main() -> int:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        try:
            message = read_message(stdin)
            if message is None:
                return 0
            write_message(stdout, handle(message))
        except (FileNotFoundError, ConnectionError, OSError):
            write_message(stdout, {"ok": False, "error": "LagLingo companion control channel is not running"})
        except Exception as error:
            write_message(stdout, {"ok": False, "error": str(error)})


if __name__ == "__main__":
    raise SystemExit(main())
