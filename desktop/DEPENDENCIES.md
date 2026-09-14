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
- FFmpeg 9.0.1 is the Gyan essentials Windows build, with its original LICENSE
  and README in `backend/_internal/third-party/ffmpeg`. This build has GPL-enabled
  components; it must not be described as an LGPL-only binary. Exact download
  provenance and SHA-256 are in SOURCE.json and desktop/dependencies.json.
- yt-dlp and hls.js remain subject to the upstream terms described in the root
  THIRD_PARTY_NOTICES.md; bundled executable dependencies need their own notices.

The generated unsigned installer is a local pre-release build. Before publishing,
review the complete bundled notices and arrange the corresponding source and
build information required by the exact FFmpeg/yt-dlp binary distributions.
A download URL or SHA-256 alone does not fulfill all source-distribution duties.
Code signing and public release publishing are separate release steps.
