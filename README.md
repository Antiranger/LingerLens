# LagLingo Live Delay Platform Spike

This repository currently contains a browser-only experiment for validating whether YouTube Live and Bilibili Live can remain intentionally **5 or 10 seconds behind their current playable live edge**.

It does **not** include ASR, translation, subtitles, audio capture, a backend, or custom stream parsing.

## Current Prototype 2: localhost HLS/CMAF browser player

`prototype/hls-companion/` is now the active prototype. It probes yt-dlp qualities, supports manually selected browser-compatible 1080p/720p, remuxes with FFmpeg stream copy, delays publication of local fMP4 HLS segments, and plays them in bundled hls.js on Chrome/Edge. It also includes a minimal Native Messaging cookie bridge for the current logged-in tab.

Run it from the repository root:

```bash
npm run prototype:hls
```

Then open `http://127.0.0.1:8765/`. See [`prototype/hls-companion/README.md`](prototype/hls-companion/README.md) for login setup, validation commands, security boundaries, and remaining manual tests.

The earlier `prototype/delayed_live_player.py` ffplay relay is retained only as the first delay feasibility proof; see [`prototype/README.md`](prototype/README.md).

## Load the extension

1. Open Chrome or Edge.
2. Visit `chrome://extensions` or `edge://extensions`.
3. Enable **Developer mode**.
4. Choose **Load unpacked**.
5. Select the repository's `extension/` directory.
6. Open a supported live room:
   - `https://www.youtube.com/watch?v=...`
   - `https://live.bilibili.com/<room-id>`
7. Reload an already-open live page once after installing the extension.

A **Live Delay Spike** panel appears at the top-right of the page.

## Recommended two-tab test

Open the same live room in two tabs:

- **Source tab**: select `Source / observe`, keep the player at the live edge, and click **Start observation**.
- **Viewer tab**: select `Viewer / delayed`, select `5 seconds` or `10 seconds`, then click **Set delay & start**.

The Source tab is only a playback/background-tab reference. This spike does not capture its audio.

## What the panel measures

Once monitoring starts, the extension logs one observation per second to the page console and stores it for export:

- `currentTime`, `paused`, `playbackRate`, `readyState`, `networkState`;
- all seekable ranges and the final range's start/end;
- `liveEdge` and `actualDelay = liveEdge - currentTime`;
- seekable-window length and target error;
- page visibility and video-element generation.

It also counts:

- drift corrections;
- likely platform jumps back to the live edge;
- video replacements/reloads;
- `waiting`/`stalled` buffering events;
- likely Source-tab timeline stops.

## Control policy

Viewer mode uses deliberately simple hard-seek control:

- target: 5s or 10s;
- stable deadband: target ±1s, with no intervention;
- correction threshold: absolute target error greater than 2s;
- correction: one seek to `liveEdge - targetDelay`;
- no playback-rate control and no per-frame seeking.

If no DVR/seekable range is exposed, or the seekable window is shorter than the target, the panel records an explicit failure reason instead of attempting a more complex controller.

## Exporting evidence

- **Export JSON** includes session metadata, counters, computed summary, event log, and all samples.
- **Export CSV** includes the per-second samples for spreadsheet analysis.

Keep the JSON for each run because it preserves behavioral events and counters that the CSV does not flatten.

## Required manual matrix

Run at least 30 minutes per combination:

| Platform | Target |
|---|---:|
| YouTube Live | 5s |
| YouTube Live | 10s |
| Bilibili Live | 5s |
| Bilibili Live | 10s |

For each run, perform and timestamp these disturbances:

1. uninterrupted playback;
2. pause for 10 seconds, then resume;
3. manually click the platform's Live button or drag to the front, then let the extension recover;
4. change quality once;
5. leave the Source tab in the background;
6. inspect `chrome://discards` or `edge://discards` and browser Memory Saver behavior.

Use `docs/live-delay-test-protocol.md` and copy `docs/test-records/result-template.md` for every run.

## Choosing test live rooms

### YouTube

Prefer an actual current livestream rather than a premiere or completed archive. Confirm the player exposes rewindable history by manually dragging backward. Test both:

- an ordinary live stream with DVR enabled;
- a low-latency or ultra-low-latency stream if its mode is known.

If the broadcaster disabled DVR, keep the exported failure evidence; do not replace it with assumptions about other streams.

### Bilibili

Choose a currently live room with continuous motion/audio. First verify whether the web player exposes any rewindable range. Many room/player modes may expose little or no DVR history; that is itself a result. If possible, test rooms using different quality/latency options.

## Automated verification

From the repository root:

```bash
npm test
```

These tests validate manifest shape, extension JavaScript syntax, summary statistics, and CSV export behavior. They cannot establish real platform support; the four 30-minute live runs remain required.
