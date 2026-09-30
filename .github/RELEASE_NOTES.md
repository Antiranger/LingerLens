# LingerLens 0.1.1 preview

Watch YouTube Live, Bilibili Live and Twitch with locally delayed video, original
and translated subtitles, and live chat. Includes English, 简体中文, 日本語,
Deutsch and Русский interfaces and documentation.

## Choose your download

- **Windows x64:** `LingerLens-0.1.1-windows-x64-setup.exe`.
- **Mac with Apple Silicon:** `LingerLens-0.1.1-macos-arm64.dmg`.
- **Mac with Intel:** `LingerLens-0.1.1-macos-x64.dmg`.

The Windows installer lets you choose the installation folder. A fresh desktop
profile has no configured models, API keys or imported cookies. Add your own
connections in model settings. Upgrading on a computer that already used
LingerLens keeps that computer's local settings and cookies; reinstalling does
not reset the local profile. These user files are outside the installer.

The repository remains private. Downloads require repository access; anonymous
in-app update checks cannot access private release assets. Use manual downloads.

Electron, Python, FFmpeg/ffprobe, yt-dlp, fonts and the Japanese dictionary are
included. Users do not install development tools. Cloud speech/translation
requires internet access and the user's own provider account/API keys; provider
charges are separate. Optional local Whisper servers and models are not bundled.

Preview packages are **unsigned** and macOS packages are **not notarized**.
Check `SHA256SUMS.txt` before installing. Mac updates use a new DMG; the in-app
installer updater is Windows-only. Stream access and provider language support
vary by account, region and service. This release does not bypass DRM or paid access.

See the repository README for five-language guides, privacy details and
troubleshooting. Bundled components retain their own licenses and corresponding
source obligations; see `THIRD_PARTY_NOTICES.md` in the tagged source.
