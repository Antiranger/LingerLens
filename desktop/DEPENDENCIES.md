# LagLingo desktop dependencies

Windows x64 desktop builds include Chromium and Node.js through Electron, a frozen
Python 3.11 interpreter, the Companion's Python packages, Japanese dictionary,
hls.js, yt-dlp, and FFmpeg/ffprobe. End users do not install Python, Node, npm,
FFmpeg, Chrome, Edge, or WebView2 to run the desktop player.

The installer installs these files privately for the current user. It does not
change system PATH or existing Python installations. Provider credentials are
configured by the user. A local Whisper-compatible server and model weights are
optional external services and are not installed or downloaded automatically.

## Third-party redistribution

- Electron includes its LICENSE and LICENSES.chromium.html in the application.
- Python package metadata/licenses, fugashi native libraries, and unidic-lite
  dictionary files are collected into the backend's `_internal` directory.
- **FFmpeg 9.0.1 is the BtbN `win64-lgpl` Windows build** (build
  `n9.0.1-29-gad500d59cb`, autobuild `2026-09-14-13-17`). It is an **LGPL v3**
  build: `ffmpeg -version` reports `--enable-version3` and no `--enable-gpl`,
  no `--enable-nonfree`, and `--disable-libx264` / `--disable-libx265` /
  `--disable-libxvid`. LagLingo only ever stream-copies (`-c copy`), so the
  encoders an LGPL build omits were never used. Its `LICENSE.txt` is copied to
  `backend/_internal/third-party/ffmpeg/LICENSE`, and the binary's own
  `configuration:` line is written to `BUILD-CONFIGURATION.txt` beside it, so the
  LGPL claim is verifiable from what shipped rather than from this document.
  Exact download provenance and SHA-256 are in `SOURCE.json` and
  `desktop/dependencies.json`.
- yt-dlp and hls.js remain subject to the upstream terms described in the root
  THIRD_PARTY_NOTICES.md.

Superseded: an earlier build used the Gyan `essentials` distribution, which is
GPL v3 and carries the whole-program source obligation that comes with it. The
switch was made because nothing in the product encodes.

The generated unsigned installer is a local pre-release build. Code signing and
public release publishing are separate release steps.
