# LingerLens user guide

[Documentation index](../README.md) · Baseline 0.1.2 · Updated 2026-09-30

## Run

The default Windows installation folder is `%LOCALAPPDATA%/Programs/lingerlens`; user data is stored separately in `%APPDATA%/LingerLens`. Starting with 0.1.1, the installation wizard lets you choose the folder.

A fresh profile has no configured models, API keys or imported cookies. Add your own connections in model settings. Upgrades and reinstalls preserve this computer's existing user data, so old settings on a development computer do not indicate that they are bundled. Use a separate fresh data directory for clean recordings; never add user data or its backups to release packages.

Desktop build targets are Windows x64 and macOS arm64/x64. Download available packages from [Releases](https://github.com/Antiranger/LingerLens/releases). Windows uses an EXE installer; on Mac open the DMG for your chip and drag LingerLens to Applications. Preview packages are unsigned and macOS packages are not notarized. Verify the download against `SHA256SUMS.txt`.

The installer includes Electron, Python, FFmpeg/ffprobe, yt-dlp, fonts and the Japanese dictionary. Cloud accounts, API charges and optional local Whisper servers/model weights are separate. No developer tools are needed. Mac data lives in `~/Library/Application Support/LingerLens`. Mac updates are installed by downloading a new DMG; the in-app installer updater is Windows-only.

For browser development install Python 3.11+, Node.js 22.12+, FFmpeg/ffprobe and Chrome or Edge, then run from the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1 -Python .\.venv\Scripts\python.exe
.\start-lingerlens.cmd -Prototype -Python .\.venv\Scripts\python.exe
```

Open `http://127.0.0.1:8765/`. Add `-CheckOnly` to bootstrap for checks without installation. Without `-Prototype`, the launcher opens an existing `release/win-unpacked/LingerLens.exe`; bootstrap does not create an EXE. Do not expose the Companion to a LAN or reverse proxy.

## Configure providers

In **Connections & keys**, add a profile, save it and select it. Use the provider's exact model ID, endpoint and authentication. Recognition and translation are separate unless provider-native bilingual mode is enabled. Implemented translation protocols include OpenAI-compatible Chat Completions, Qwen-MT, Anthropic Messages and Google Gemini. Recognition adapters include DashScope, Soniox, Deepgram, OpenAI, AssemblyAI, Volcano Engine, ElevenLabs, Speechmatics and Tencent. This list describes protocol support, not real-account success for every model or language.

The protocol selector uses short vendor names. After selecting an ASR protocol, use the model dropdown to fill a supported model ID, or edit the ID manually for your own gateway. Tencent model selection also updates its engine. New Soniox profiles work without a separate translator; enable native translation fallback only after configuring one. The seven-second limit releases confirmed caption evidence, not mutable hypotheses or a result the service has not returned. See the [ASR compatibility audit](../ASR-COMPATIBILITY.md) for timing and native-translation limitations.

Local Whisper-compatible recognition requires a service that you operate. A common base URL is `http://127.0.0.1:8000/v1`; the adapter sends `/audio/transcriptions`. A blank key works only when that service allows it. ASR language detection, code switching and target-language coverage are provider-specific; the five UI locales do not imply five-language recognition for every provider. Costs are estimates only and unavailable data is not zero.

## Play a stream

Paste an HTTPS YouTube, Bilibili room or Twitch channel URL, probe it, choose a compatible H.264/AVC plus AAC quality and start. Set the target delay between 11 and 60 seconds (15 seconds by default). Actual camera-to-screen latency depends on the source and network. Use the player fullscreen control so captions remain in the fullscreen stage. Stop before switching streams or accounts. Chat reception and translation depend on the platform.

Try public streams without Cookies first. The Cookie dialog accepts the supported platform header, table or Netscape export; Bilibili login requires `SESSDATA`. Platform snapshots remain separate. Cookies do not bypass DRM, paid access, region restrictions or anti-bot controls.

## Reading delay figures

**Local segment lag** measures the distance from the current picture to the latest complete local video segment. It excludes platform latency and incomplete segments; missing measurements appear as **—**. **Translation arrival margin** is positive when a translation arrives early and negative when it arrives late. It uses recent observations during normal foreground playback; pauses, seeks, catch-up playback and stale data invalidate recommendations. A button such as **Set target delay to 19 seconds** sets the total target to 19 seconds. It is a suggestion based on recent results, not a guarantee that every subtitle will be on time.

## Data and privacy

Cloud ASR receives live audio. Translation receives caption text and bounded context. Native bilingual routes can send audio and translation instructions to one service. Settings, Cookies, media and cache stay locally (`%APPDATA%/LingerLens` in desktop mode; the ignored runtime directory in browser mode). Saved keys are visible in **Connections & keys**; do not open it while sharing your screen. Never publish runtime files, signed stream URLs or raw diagnostics.

The desktop updater fetches a JSON manifest and verifies installer size and SHA-256 before running it. This is not publisher-signature verification. A missing/private/draft-only update channel can fail without stopping playback. Uninstall preserves data by default.

## Troubleshooting

- Missing EXE: use `-Prototype` or build desktop first.
- Missing module: install requirements with the same Python used to start the Companion.
- Port busy: close the identified Companion or use another port; never kill all Python processes.
- 403/login/no formats: verify the URL, account/region access and the matching Cookie snapshot.
- No captions: check enabled state, active ASR, endpoint, key, model and spoken language.
- No translation: check quota, target language and the selected translation route.
- No playback: try AVC/AAC, another public stream, and inspect network/proxy diagnostics.
- Update failure: check network and whether a published manifest exists; playback can continue.

## Build and verify

```powershell
py -3.11 -m venv .venv-desktop
.\.venv-desktop\Scripts\python.exe -m pip install -r desktop/requirements-build.txt
npm ci --ignore-scripts
npm run ci
npm run desktop:test
git diff --check
```

These checks use local fixtures and do not prove a real stream, provider account, billing result or translation quality. Read [Development](../DEVELOPMENT.md), [Contributing](../../CONTRIBUTING.md) and [Releasing](../RELEASING.md).
