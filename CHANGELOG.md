# Changelog

## 0.1.2

- Reduce overlapping caption intervals for ASR results without word timestamps by preserving each utterance's estimated start and advancing the finalized frontier.
- Audit all configured ASR protocol paths for timing, translation routing, duplicate results, reconnects and stable-text release deadlines. Provider timing precision still varies.
- Shorten speech protocol labels and add recommended model selections while preserving custom model IDs and credentials; Tencent selections update its engine type.
- New Soniox profiles do not require a separate translation model unless native-translation fallback is enabled.
- Rewrite the five README introductions around subtitle flickering / revision churn and delayed playback, with Chinese screenshots/GIF for Simplified Chinese and English media for the other four languages.
- Publish a public GitHub release channel with verified Windows update metadata; retain user data during upgrades and keep fresh packages free of personal configuration.
- Use Electron's network stack for update requests so system proxy settings apply to GitHub release checks and downloads.

## 0.1.1 — preview

- Windows installation now uses a wizard with an installation folder selector.
- Fresh desktop profiles start without configured speech or translation models, API keys or imported cookies. Users add their own connections; upgrades preserve their existing local profile.
- Remove the provider example file from frozen packages and reject configuration backups and browser profile files during package auditing.
- Verify blank model settings and absence of saved authentication in every native packaged smoke test.

## 0.1.0 — preview

- Use the purple LL mark for application, installer and shortcut icons.
- Add native macOS Intel and Apple Silicon packaging alongside Windows x64.
- Refresh all five README pages and installation guides, with bundled dependency,
  provider credential, unsigned build and privacy details.
- Prepare same-repository GitHub Releases with checksums and a Windows update manifest.
- Omit the unnecessary Windows elevation helper from per-user installers.

- Soniox bilingual subtitles now consume trusted source/translation chunk boundaries from the unified token stream, prefer one-way translation for live captions, fall back only for missing/unaligned native text, keep source-only cues repairable by late translations, and briefly revive just-late translated revisions instead of losing Chinese permanently.
- Subtitle diagnostics now expose native-translation waiting, unaligned/missing segments, fallback use, and late-patch counts; the hard-deadline chunker prefers trusted Provider translation boundaries before making a local cut.
- Fix desktop-wide mouse/keyboard lag during chat-heavy streams: Chromium's accessibility tree is now off by default (`LINGERLENS_ACCESSIBILITY=1` restores it). UI Automation clients such as translation or IME tools had switched it on, and every chat and caption update then ran through the browser main thread.
- Speech-recognition and translation connections now follow the network setting (system proxy / direct / manual). Previously they always connected directly, so Soniox and other overseas services failed intermittently on networks that need a proxy. Local and private addresses always stay direct; SOCKS proxies fall back to direct with a log warning.
- Model settings: each speech-recognition protocol links straight to its vendor's API-key page and official site (DashScope gets both the mainland-China and international consoles).
- Player: single-click the video to pause/resume, double-click to toggle fullscreen.
- Cookie import: marked as optional, with an explanation of cookies and `cookies.txt`, a step-by-step guide using the open-source Get cookies.txt LOCALLY extension, and automatic Netscape-format detection on paste.
- Model settings: the saved-configuration panel is a readable summary instead of raw JSON (raw data stays available for troubleshooting); internal release-gate labels such as "experimental" and "awaiting acceptance" no longer appear in provider lists.

- Electron desktop packages for Windows x64, macOS Intel x64 and Apple Silicon arm64, with bundled Python backend, FFmpeg/ffprobe, yt-dlp and language data.
- Delayed YouTube, Bilibili and Twitch playback with HLS, ASR, translated captions, speaker-aware caption rows and optional live chat.
- Five interface locales: Simplified Chinese, English, Japanese, German and Russian.
- Provider profiles, native bilingual recognition routes, usage/cost telemetry, local diagnostics and verified-manifest update checks.
- Preview packages are unsigned and macOS packages are not notarized. Real-provider coverage and stream stability depend on the stream, account and service; optional local Whisper services and model weights are not bundled.
