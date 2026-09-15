# Security boundaries

LingerLens is intended for one user on one Windows machine.

1. **Network:** the Companion refuses non-loopback bind addresses. Do not proxy or expose port 8765.
2. **Platform authentication:** YouTube and Bilibili Cookies remain local to authentication, yt-dlp extraction, and optional per-platform runtime persistence. Bilibili login validation treats `SESSDATA` as critical; YouTube uses its own platform guidance.
3. **Provider data:** cloud ASR receives live audio; translation receives recognized text and selected context. Choose providers and prices deliberately. A localhost Whisper-compatible endpoint can keep ASR audio local, but LingerLens neither installs nor secures that service.
4. **Credentials:** provider keys, auth snapshots, and the IPC control secret are runtime-private. Model settings intentionally displays raw saved keys through a same-origin, loopback-only, `no-store` route; this creates a screen-sharing/screenshot risk.
5. **Media:** private/public HLS segments, captures, logs, caches, and benchmark output are generated data and excluded from Git.
6. **Release:** `npm run guard:release` checks the tracked file set without echoing matched values. Review changes and use host secret scanning as additional controls.

See the repository [SECURITY.md](../SECURITY.md) for vulnerability reporting and the complete policy.
