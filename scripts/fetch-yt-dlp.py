#!/usr/bin/env python3
"""Download the pinned yt-dlp.exe into prototype/hls-companion/vendor/yt-dlp/.

The 17 MB executable used to be committed, so every yt-dlp upgrade added
another copy to the repository history. Now the pin lives in
desktop/dependencies.json (like Electron and FFmpeg) and the binary is fetched
on demand. Two records name the same hash on purpose: ``yt-dlp.exe.sha256``
beside the binary is what bootstrap, CI and the packager verify, and this
script refuses to run if the two ever disagree.

Exit status: 0 when the binary is present and verified, 1 otherwise.
Proxies from HTTP(S)_PROXY are honoured (urllib reads the environment).
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "prototype" / "hls-companion" / "vendor" / "yt-dlp" / "yt-dlp.exe"
CHECKSUM = TARGET.with_name("yt-dlp.exe.sha256")
DEPENDENCIES = ROOT / "desktop" / "dependencies.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    pin = json.loads(DEPENDENCIES.read_text(encoding="utf-8"))["yt-dlp"]
    expected = pin["sha256"].lower()
    recorded = CHECKSUM.read_text(encoding="utf-8").split()[0].lower()
    if recorded != expected:
        print(f"yt-dlp pin disagrees: dependencies.json {expected} vs {CHECKSUM.name} {recorded}", file=sys.stderr)
        return 1
    if TARGET.exists() and sha256(TARGET) == expected:
        print(f"[ok] yt-dlp {pin['version']} already present and verified")
        return 0
    print(f"Downloading yt-dlp {pin['version']} ...", flush=True)
    partial = TARGET.with_suffix(".download")
    try:
        with urllib.request.urlopen(pin["url"], timeout=180) as response, partial.open("wb") as output:
            while block := response.read(1 << 20):
                output.write(block)
        actual = sha256(partial)
        if actual != expected:
            print(f"yt-dlp SHA-256 mismatch: expected {expected}, got {actual}; refusing to install it", file=sys.stderr)
            return 1
        partial.replace(TARGET)
    finally:
        partial.unlink(missing_ok=True)
    print(f"[ok] yt-dlp {pin['version']} downloaded and verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
