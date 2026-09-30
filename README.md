<p align="center"><img src="desktop/assets/icon.png" width="96" alt="LingerLens"></p>
<h1 align="center">LingerLens</h1>
<p align="center"><strong>About 15 seconds of delay. More stable, complete and readable live translations.</strong></p>

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [Deutsch](README.de.md) · [Русский](README.ru.md)

LingerLens is a **bilingual live player for YouTube, Bilibili and Twitch**, with speech translation, live chat translation and translated chat overlays to make streams in other languages easier to follow.

Real-time translation often prioritizes showing text immediately. But while a speaker is still talking, recognition and translation models work with incomplete sentences. New audio can cause the subtitles to grow, change or be rewritten entirely. This **subtitle flickering / revision churn** makes viewers reread the same line and interrupts the viewing experience.

LingerLens buffers video for about **15 seconds**, giving speech recognition, sentence segmentation and translation time to settle before displaying bilingual subtitles along the playback timeline. The buffer reduces revisions of temporary results and helps deliver more stable translations with fuller context when the corresponding video plays. **The delay is adjustable; translated live chat can also appear as an overlay on the video.**

![LingerLens — English interface](docs/assets/player.en.png)

## Watch it in action

English interface: bilingual captions, delayed playback and live chat.

![LingerLens — English demo](docs/assets/demo.en.gif)

## Download

[**Download from GitHub Releases →**](https://github.com/Antiranger/LingerLens/releases)

| System | Package |
| --- | --- |
| Windows x64 | `LingerLens-<version>-windows-x64-setup.exe` |
| macOS · Apple Silicon | `LingerLens-<version>-macos-arm64.dmg` |
| macOS · Intel | `LingerLens-<version>-macos-x64.dmg` |

Only files attached to a published release are available downloads. Windows builds are unsigned. macOS builds have an ad-hoc signature for integrity, but no Apple Developer ID signature or notarization. On macOS, open the matching DMG and drag LingerLens to Applications. Compare the download with the release's `SHA256SUMS.txt`.

## Why delay the video?

Recognition and translation run ahead of delayed playback. The default target is 15 seconds, adjustable from 11–60 seconds. This is not a fixed end-to-end latency: the stream, network and provider still affect the result. Long speech may be split into captions, and providers without word timestamps use approximate timing. The buffer reduces revision churn but cannot guarantee that every translation arrives on time or is accurate.

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

Browser mode requires Windows, Python **3.11+**, Node.js **22.12+**, FFmpeg/ffprobe on PATH, and Chrome or Edge.

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

## Model setup

Choose a speech protocol, then select a recommended model to fill its ID or enter a custom ID. Tencent selections also update the speech engine. Provider-native bilingual modes can recognize and translate in one session; other modes require a separate translation service. See [provider settings](docs/PROVIDERS.md) and [ASR timing compatibility](docs/ASR-COMPATIBILITY.md).

## Updates

Windows installed builds check GitHub Releases for updates. Choose to download the newer installer; the app verifies its size and SHA-256 before running it. Source mode hides the update button. On macOS, download the new DMG and replace the application. Existing local settings are preserved; a fresh profile contains no personal models, API keys or cookies.

## Privacy and limitations

Cloud recognition sends audio to the selected service; translation sends text and context. Provider-native bilingual modes can send audio and translation instructions to the same service. A local Whisper-compatible server is optional and must be installed separately. Local recognition does not make cloud translation local.

Keys and Cookies are stored in local files, not an encrypted credential vault. **Connections & keys displays saved keys**: keep it closed when sharing your screen. Never attach runtime data or unreviewed diagnostics to an issue. Desktop data normally lives in `%APPDATA%/LingerLens`; uninstalling preserves it by default.

LingerLens does not bypass DRM, paid access, account restrictions or anti-bot controls. Provider charges, site changes and translation quality require individual verification. Offline tests do not establish compatibility with every real account or stream.

## Development and community

Run `npm run ci` and `git diff --check` before contributing. CI checks repository hygiene, documentation, licences, syntax and local fixtures. Optional browser/Streamlink tests can be skipped without their dependencies; see [verification](docs/DEVELOPMENT.md).

[Documentation](docs/README.md) · [Contributing](CONTRIBUTING.md) · [Code of conduct](CODE_OF_CONDUCT.md) · [Support](SUPPORT.md) · [Security](SECURITY.md) · [Changelog](CHANGELOG.md) · [Release checklist](docs/RELEASING.md)

Application code is under the [MIT License](LICENSE). Bundled components retain their own licences; redistribution requires the notices and source obligations in [Third-party notices](THIRD_PARTY_NOTICES.md).
