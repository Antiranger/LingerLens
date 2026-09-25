# Development and verification

## Environment

Windows development uses Python 3.11+, Node.js 22.12+, FFmpeg/ffprobe and Chrome or Edge. Use a project virtual environment and run commands from the repository root. `bootstrap.ps1 -CheckOnly` checks tools and the yt-dlp SHA-256 without installing dependencies. `yt-dlp.exe` is not committed: `python scripts/fetch-yt-dlp.py` (also run by `bootstrap.ps1` and the desktop build) downloads the version pinned in `desktop/dependencies.json` and verifies it.

## Checks

```powershell
npm ci --ignore-scripts
npm run ci
npm run desktop:test
git diff --check
```

`npm run ci` runs the release guard, tracked-file completeness, third-party notice check, documentation/link coverage, Python compilation, JavaScript syntax checks, all discovered Companion tests and root Node tests. Browser and Streamlink files are reported as skips when their optional packages are absent. A real stream, credentialed account, billing result or translation quality is never inferred from these offline checks.

## Source boundaries

The Companion API and lifecycle are in `prototype/hls-companion/companion/`; the browser player is in `web-player/`; desktop process ownership and packaging are in `desktop/`. Runtime files stay under the ignored runtime directory. Do not revive `.archive` snapshots or `.scratch` experiments as source.

Changes that affect cookies, provider protocols, data flow, packaging or commands must update the user guide and relevant security/licence documentation. Add a focused regression for a confirmed bug; do not add tests that only mirror a trivial implementation.

## Browser verification

The optional Playwright smoke starts a real local server and checks rendered controls, language switching, settings, fullscreen ownership and playback fixtures. Source inspection and an XML/HTML parse are not visual acceptance. Record the browser, viewport, stream fixture and limitations when a smoke is skipped.
