# Third-party notices

LagLingo is MIT-licensed, but it redistributes or interoperates with third-party software under each project's own terms.

## Redistributed files

- **yt-dlp** — `prototype/hls-companion/vendor/yt-dlp/yt-dlp.exe`, version `2026.08.19`. yt-dlp is licensed under The Unlicense. Source and notices: <https://github.com/yt-dlp/yt-dlp>.
- **hls.js** — `prototype/hls-companion/web-player/vendor/hls.min.js`, version `1.7.1`. hls.js is licensed under Apache License 2.0. Source and license: <https://github.com/video-dev/hls.js>.

The yt-dlp executable is integrity-pinned by the adjacent SHA-256 file and verified by `bootstrap.ps1`.

## Installed/runtime dependencies

- **aiohttp** and its transitive Python dependencies are installed from PyPI using `prototype/hls-companion/companion/requirements.txt`; see <https://github.com/aio-libs/aiohttp>.
- **FFmpeg/ffprobe**, Python, Node.js/npm, and Chrome or Edge are system dependencies and are not redistributed by this repository. Their respective licenses and installation terms apply.

## External services and sites

LagLingo can connect to YouTube, Bilibili, and user-configured ASR/translation providers. Those services are not part of LagLingo. Users are responsible for their accounts, credentials, content rights, service terms, regional restrictions, and provider charges. LagLingo does not bypass DRM or paid entitlements.
