<p align="center"><img src="desktop/assets/icon.png" width="96" alt="LingerLens"></p>
<h1 align="center">LingerLens</h1>
<p align="center"><strong>Live video. Subtitles that keep up.</strong></p>

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [Deutsch](README.de.md) · [Русский](README.ru.md)

Watch live video with time for the subtitles to catch up. LingerLens plays a locally delayed stream from **YouTube Live, Bilibili Live or Twitch**, adds speech recognition and translated subtitles, and keeps live chat alongside the player.

**Windows and macOS preview · MIT application code · Bring your own provider credentials.** Playback and language coverage depend on the stream, account and selected provider.

[![CI](https://github.com/Antiranger/LingerLens/actions/workflows/ci.yml/badge.svg)](https://github.com/Antiranger/LingerLens/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-4B3FE0.svg)](LICENSE)

![Application preview — fresh profile, no credentials](docs/assets/player.png)

## Download

[**Download from GitHub Releases →**](https://github.com/Antiranger/LingerLens/releases)

| System | Package |
| --- | --- |
| Windows x64 | `LingerLens-<version>-windows-x64-setup.exe` |
| macOS · Apple Silicon | `LingerLens-<version>-macos-arm64.dmg` |
| macOS · Intel | `LingerLens-<version>-macos-x64.dmg` |

Only files attached to a published release are available downloads. Preview builds are unsigned; macOS builds are not notarized. On macOS, open the matching DMG and drag LingerLens to Applications. Compare the download with the release's `SHA256SUMS.txt`.

## Why delay the video?

Recognition and translation take time. LingerLens holds the picture briefly so speech and translated captions can arrive together. Start with the default 15-second target, then adjust it to your provider and network.

## Features

- Local HLS playback with a default target delay of 15 seconds, adjustable from 11–60 seconds. Actual latency also depends on the source and network.
- Source and translated captions, including overlapping speaker captions when the ASR provider supplies speaker information.
- Configurable recognition, translation and fallback profiles; optional chat translation; usage/cost estimates when the required data is available.
- A movable subtitle window with persistent styling, stage fullscreen, diagnostics and desktop update checks.
- Simplified Chinese, English, Japanese, German and Russian interfaces. Interface language and subtitle languages are independent.

## Start watching

Install the package for your system and open **LingerLens**. Electron, Python, FFmpeg/ffprobe, yt-dlp, fonts and the Japanese dictionary are included; no terminal or developer tools are needed. Cloud recognition and translation still need internet access, your own API keys and any provider fees. Optional local Whisper servers and model weights are not included.

The [complete English guide](docs/en/guide.md) explains configuration and troubleshooting. See [desktop builds](desktop/README.md) to build Windows or macOS from source.

1. Open **Connections & keys** and configure your recognition and translation services.
2. Choose the spoken and target subtitle languages, then enable subtitles if needed.
3. Paste a supported live URL, probe qualities, choose a compatible format and start.
4. Import platform Cookies only when login is necessary. Stop playback before changing accounts or updating.

The [complete English guide](docs/en/guide.md) covers installation, providers, Cookies, privacy, troubleshooting and development.

## Run from source

Browser mode requires Windows, Python **3.11+**, Node.js **22.12+**, FFmpeg/ffprobe on PATH, and Chrome or Edge. Cloning requires GitHub access while this repository remains private.

```powershell
git clone https://github.com/Antiranger/LingerLens.git
cd LingerLens
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1 -Python .\.venv\Scripts\python.exe
.\start-lingerlens.cmd -Prototype -Python .\.venv\Scripts\python.exe
```

Open <http://127.0.0.1:8765/>. Bootstrap validates tools and the yt-dlp checksum, then installs dependencies; `-CheckOnly` checks without installing. It does not build the desktop application.

Without `-Prototype`, `start-lingerlens.cmd` launches an existing `release/win-unpacked/LingerLens.exe`. See the [desktop build instructions](desktop/README.md) to create it.

## Privacy and limitations

Cloud recognition sends audio to the selected service; translation sends text and context. Provider-native bilingual modes can send audio and translation instructions to the same service. A local Whisper-compatible server is optional and must be installed separately. Local recognition does not make cloud translation local.

Keys and Cookies are stored in local files, not an encrypted credential vault. **Connections & keys displays saved keys**: keep it closed when sharing your screen. Never attach runtime data or unreviewed diagnostics to an issue. Desktop data normally lives in `%APPDATA%/LingerLens`; uninstalling preserves it by default.

LingerLens does not bypass DRM, paid access, account restrictions or anti-bot controls. Provider charges, site changes and translation quality require individual verification. Offline tests do not establish compatibility with every real account or stream.

## Development and community

Run `npm run ci` and `git diff --check` before contributing. CI checks repository hygiene, documentation, licences, syntax and local fixtures. Optional browser/Streamlink tests can be skipped without their dependencies; see [verification](docs/DEVELOPMENT.md).

[Documentation](docs/README.md) · [Contributing](CONTRIBUTING.md) · [Code of conduct](CODE_OF_CONDUCT.md) · [Support](SUPPORT.md) · [Security](SECURITY.md) · [Changelog](CHANGELOG.md) · [Release checklist](docs/RELEASING.md)

Application code is under the [MIT License](LICENSE). Bundled components retain their own licences; redistribution requires the notices and source obligations in [Third-party notices](THIRD_PARTY_NOTICES.md).
