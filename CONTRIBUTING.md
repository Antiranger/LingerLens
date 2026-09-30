# Contributing to LingerLens

LingerLens ships desktop packages for Windows x64 and macOS Intel/Apple Silicon. The browser-mode development bootstrap uses Windows PowerShell. Keep changes narrow, test observable behavior, and do not add credentials or captured media.

## Setup

From a Windows PowerShell prompt at the repository root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1
```

The browser-mode bootstrap checks Python 3.11+, Node.js 22.12+/npm, FFmpeg/ffprobe, Chrome or Edge, and the vendored yt-dlp checksum before installing declared dependencies. It never installs system software or credentials. For the self-contained desktop build, follow [desktop/README.md](desktop/README.md).

## Before opening a change

```powershell
npm run ci
git diff --check
```

`npm run ci` runs the tracked-file secret/release guard, Python compilation, JavaScript syntax checks, HLS Companion tests, and root Node tests. Real livestreams, cloud credentials, browser profiles, and live model endpoints are intentionally excluded from automated CI.

## Security and test data

- Never commit `runtime/providers.json`, auth/Cookie snapshots, `.env` files, control secrets, logs, media, benchmarks, caches, or agent/browser state.
- Use `prototype/hls-companion/runtime/providers.example.json` for sanitized configuration examples.
- Tests may use unmistakably fake fixture values. The release guard reports paths and finding categories, never matched secret values.
- Do not weaken security boundaries merely to make a test easier. Companion HTTP must remain loopback-only; sensitive Cookie transfer remains limited to the documented local flows.

## Pull requests

Explain the behavior changed, tests run, manual limitations, and any external provider assumptions. Update user documentation when configuration, security boundaries, dependencies, or commands change. See [SECURITY.md](SECURITY.md) for vulnerability reporting.
