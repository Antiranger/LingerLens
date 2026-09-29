# Changelog

## Unreleased

- Soniox bilingual subtitles now consume trusted source/translation chunk boundaries from the unified token stream, prefer one-way translation for live captions, fall back only for missing/unaligned native text, keep source-only cues repairable by late translations, and briefly revive just-late translated revisions instead of losing Chinese permanently.
- Subtitle diagnostics now expose native-translation waiting, unaligned/missing segments, fallback use, and late-patch counts; the hard-deadline chunker prefers trusted Provider translation boundaries before making a local cut.
- Fix desktop-wide mouse/keyboard lag during chat-heavy streams: Chromium's accessibility tree is now off by default (`LINGERLENS_ACCESSIBILITY=1` restores it). UI Automation clients such as translation or IME tools had switched it on, and every chat and caption update then ran through the browser main thread.
- Speech-recognition and translation connections now follow the network setting (system proxy / direct / manual). Previously they always connected directly, so Soniox and other overseas services failed intermittently on networks that need a proxy. Local and private addresses always stay direct; SOCKS proxies fall back to direct with a log warning.
- Model settings: each speech-recognition protocol links straight to its vendor's API-key page and official site (DashScope gets both the mainland-China and international consoles).
- Player: single-click the video to pause/resume, double-click to toggle fullscreen.
- Cookie import: marked as optional, with an explanation of cookies and `cookies.txt`, a step-by-step guide using the open-source Get cookies.txt LOCALLY extension, and automatic Netscape-format detection on paste.
- Model settings: the saved-configuration panel is a readable summary instead of raw JSON (raw data stays available for troubleshooting); internal release-gate labels such as "experimental" and "awaiting acceptance" no longer appear in provider lists.

## 0.1.0 — preview

- Windows-first Electron desktop entry with bundled Python backend, FFmpeg/ffprobe, yt-dlp and language data.
- Delayed YouTube, Bilibili and Twitch playback with HLS, ASR, translated captions, speaker-aware caption rows and optional live chat.
- Five interface locales: Simplified Chinese, English, Japanese, German and Russian.
- Provider profiles, native bilingual recognition routes, usage/cost telemetry, local diagnostics and verified-manifest update checks.
- This is a pre-release. Real-provider coverage, stream stability, code signing, updater publishing, macOS and Android remain separately gated.
