# LagLingo

LagLingo is a Windows-first local delayed live player for YouTube Live and Bilibili Live. It uses yt-dlp and FFmpeg to publish a controlled local HLS stream, then adds real-time ASR, translated bilingual subtitles, provider usage/cost estimates, stage fullscreen, and a draggable, persistent subtitle window.

The repository is still a developer-oriented prototype: it does not bypass DRM, paid access, regional restrictions, or platform anti-bot controls, and real-stream compatibility depends on the source and user authentication.

## Quick start (Windows)

Requirements are Python **3.11+**, Node.js **18+** with npm, FFmpeg/ffprobe, and Chrome or Edge. From the repository root run the one bootstrap command:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1
```

Or double-click/run `bootstrap.cmd`. Bootstrap checks system tools and official install links, verifies the vendored yt-dlp SHA-256, installs declared Python/Node dependencies, and runs the tracked-file release guard. It is repeatable and does **not** install system browsers/tools or modify credentials. Use `-CheckOnly` for verification without package installation.

Start LagLingo:

```powershell
.\start-laglingo.cmd
```

Then open <http://127.0.0.1:8765/>. Enter a supported live URL, probe qualities, choose a browser-compatible format, and start playback.

## Model providers and local Whisper

The player's **Model settings** dialog owns saved ASR/translation profiles and active selection. The sanitized example is [`prototype/hls-companion/runtime/providers.example.json`](prototype/hls-companion/runtime/providers.example.json); the real `runtime/providers.json` is generated locally and ignored by Git.

Existing Bailian realtime/task ASR and OpenAI-compatible/Qwen translation are supported. For local ASR, first run your own Speaches, faster-whisper-server, Xinference, LocalAI, or compatible service, then configure an **OpenAI Audio Transcriptions** profile (commonly `http://127.0.0.1:8000/v1`, endpoint `/audio/transcriptions`). LagLingo does not download, start, authenticate, or expose that service. A key may be blank when the local endpoint permits it.

If cloud subtitles are enabled, audio leaves the machine for the selected ASR provider and recognized text/context goes to the translation provider. Provider-returned usage is accumulated. Costs are shown only when both usage and required profile prices are available; unavailable data is not reported as zero.

## Cookies and supported platforms

- **Manual import:** choose YouTube or Bilibili in the player and paste a supported Cookie header/table/export. Bilibili requires the login-critical `SESSDATA`; other valid Bilibili Cookies are retained without being falsely marked mandatory.
- **Native Messaging:** load `prototype/hls-companion/extension/` unpacked and use the registration helper documented in the [Companion README](prototype/hls-companion/README.md).
- **Development fallback:** `npm run prototype:hls -- --cookies-from-browser chrome` (or `edge`) may work but can fail while Chromium locks/encrypts its Cookie database.

Platform snapshots are kept separately so importing one platform does not overwrite the other. They are private runtime files and must not be committed.

## Player behavior

- **Target total delay** defaults to 15 seconds and must be greater than 10 seconds. Measured delay is reported separately; source/network behavior can prevent exact equality.
- Use LagLingo's **stage fullscreen** button so video and subtitles remain in the same fullscreen element.
- Drag or keyboard-nudge the subtitle window; position, opacity, size, and source/translation colors persist locally.
- Stop is idempotent and clears the current media/subtitle session so another URL can be probed immediately.

## Security boundary

The Companion binds only to loopback. Cookies are not logged or passed on ordinary command lines. API keys are masked from normal status/provider APIs and logs. By explicit product design, the loopback, same-origin model-settings route returns saved raw API keys with `Cache-Control: no-store` so the local user can inspect them. Opening that dialog exposes keys to nearby viewers, screenshots, screen sharing, remote desktop tools, and browser inspection; do not open it while sharing your screen.

Runtime providers, Cookies/auth snapshots, control secrets, media, logs, caches, benchmark output, and agent/browser state are excluded by `.gitignore` and the deterministic tracked-file guard. See [SECURITY.md](SECURITY.md) and [`docs/security.md`](docs/security.md).

## Tests and release checks

```powershell
npm run test:hls-companion
npm test
npm run check:python
npm run check:js
npm run guard:release
# or all established CI-safe checks:
npm run ci
```

These checks use fake/local fixtures and do not call real livestreams or credentialed provider endpoints. Native fullscreen ownership, long-running stream stability, authenticated/restricted streams, provider billing, and local Whisper deployment remain manual/environment-specific validation.

Architecture: [`docs/architecture.md`](docs/architecture.md). Detailed Companion setup: [`prototype/hls-companion/README.md`](prototype/hls-companion/README.md). Contributions: [CONTRIBUTING.md](CONTRIBUTING.md). Third-party licensing: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). LagLingo itself is available under the [MIT License](LICENSE).
