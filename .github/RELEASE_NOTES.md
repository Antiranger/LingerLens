# LingerLens 0.1.3

Watch YouTube Live, Bilibili Live and Twitch with locally delayed video, original
and translated subtitles, and live chat. Includes English, 简体中文, 日本語,
Deutsch and Русский interfaces and documentation.

## Choose your download

- **Windows x64:** `LingerLens-0.1.3-windows-x64-setup.exe`.
- **Mac with Apple Silicon:** `LingerLens-0.1.3-macos-arm64.dmg`.
- **Mac with Intel:** `LingerLens-0.1.3-macos-x64.dmg`.

## What's fixed

- Fix the invalid application signature in the macOS 0.1.2 packages. Re-sign the
  modified Electron application and all nested executable code before packaging.
- Verify the application, extracted ZIP and mounted DMG using strict recursive
  macOS signature checks on both Apple Silicon and Intel before uploading.

**Mac users upgrading from 0.1.2:** replace the old application with the matching
0.1.3 DMG. Local model settings and cookies remain in your user data directory.

The Windows installer lets you choose the installation folder. A fresh desktop
profile has no configured models, API keys or imported cookies. Add your own
connections in model settings. Upgrading on a computer that already used
LingerLens keeps that computer's local settings and cookies; reinstalling does
not reset the local profile. These user files are outside the installer.

Windows installed builds can check the public GitHub release channel, download
the next update on request, and verify its size and SHA-256 before installation.
Source builds hide the update button. You can also update manually from Releases.

Electron, Python, FFmpeg/ffprobe, yt-dlp, fonts and the Japanese dictionary are
included. Users do not install development tools. Cloud speech/translation
requires internet access and the user's own provider account/API keys; provider
charges are separate. Optional local Whisper servers and models are not bundled.

Windows packages are **unsigned**. macOS packages are **ad-hoc signed** for
integrity, without Apple Developer ID signing or notarization. If macOS blocks
first launch because the developer is unidentified, follow [Apple’s per-app
Open Anyway instructions](https://support.apple.com/en-us/102445) after verifying
the download.
Check `SHA256SUMS.txt` before installing. Mac updates use a new DMG; the in-app
installer updater is Windows-only. Stream access and provider language support
vary by account, region and service. This release does not bypass DRM or paid access.

See the repository README for five-language guides, privacy details and
troubleshooting. Bundled components retain their own licenses and corresponding
source obligations; see `THIRD_PARTY_NOTICES.md` in the tagged source.
