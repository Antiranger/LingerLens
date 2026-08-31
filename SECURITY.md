# Security policy

## Reporting a vulnerability

Do not open a public issue containing exploit details, credentials, Cookie data, or private stream URLs. Use GitHub's private vulnerability reporting for `Antiranger/LagLingo` when available, or contact the repository owner privately through GitHub. Include affected revision, reproduction steps, impact, and a minimal sanitized proof. Allow reasonable time for investigation before disclosure.

## Security model

- The Companion accepts only loopback hosts (`127.0.0.1`, `localhost`, or `::1`). It is not designed for LAN or Internet exposure.
- Browser Cookies are sensitive. Native Messaging sends snapshots through an authenticated local IPC channel. Manual import uses a loopback, same-origin endpoint. Persisted platform snapshots, temporary Cookie files, and the IPC control secret are runtime-private and must never be committed.
- Provider API keys live in the private provider catalog or environment. General status/provider endpoints, logs, errors, and extension messages mask them.
- Product requirement exception: the loopback, same-origin model-settings route returns stored raw API keys with `Cache-Control: no-store` so the local user can inspect and replace them. Opening model settings exposes keys to the screen, screenshots, screen sharing, remote desktop software, browser extensions, and any other process already able to inspect the local browser. Do not open it while sharing your screen.
- Enabling cloud subtitles sends audio to the selected ASR provider and source/context text to the selected translation provider. Local Whisper is not bundled; its operator controls that service's network exposure and authentication.
- yt-dlp and FFmpeg process untrusted remote media. Keep system dependencies current and verify the vendored yt-dlp checksum with `bootstrap.ps1`.

## Repository release gate

Run `npm run guard:release` before sharing a revision. It examines `git ls-files` deterministically, rejects known private/generated paths and common credential material, and prints only file paths plus finding categories. It complements review and GitHub secret scanning; it is not a guarantee that every possible secret format is detected.
