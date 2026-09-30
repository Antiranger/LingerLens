# Source layout and early playback experiment

This directory contains both the current application code and an earlier playback experiment:

- `hls-companion/companion/`: the backend used by the desktop application.
- `hls-companion/web-player/`: the current player interface.
- `delayed_live_player.py`: an early command-line experiment using yt-dlp and ffplay.

For the current application, start with the [project README](../README.md), [user guide](../docs/en/guide.md) or [development instructions](../docs/DEVELOPMENT.md). Desktop packaging is documented in [desktop/README.md](../desktop/README.md).

## Early fixed-delay playback experiment

The instructions below describe only `delayed_live_player.py`. It was built to explore one question:

> Can a local player stay approximately 10 seconds behind a YouTube/Bilibili livestream without relying on the website player's DVR behavior?

This early experiment does not include ASR, translation or subtitles. Those features belong to the current application under `hls-companion/`.

## How it works

```text
live page URL
  → yt-dlp selects a muxed live format (default: up to 720p)
  → yt-dlp writes MPEG-TS bytes to stdout
  → the Python relay accumulates a 10-second startup window
  → ffplay receives the accumulated window and then the continuing live stream
```

The relay delay is based on when media bytes arrive locally. Platform/CDN latency still exists before this buffer, so the window title's “10s delayed” means roughly 10 seconds behind what yt-dlp received—not necessarily exactly 10 seconds behind the camera.

## Requirements

These executables must be on `PATH`:

- `yt-dlp`
- `ffplay` and `ffmpeg`
- Python 3.10+ for this prototype

Install these tools before running the experiment. The desktop application's installers provide their own bundled runtime dependencies.

## Run

From the repository root:

```bash
python prototype/delayed_live_player.py "https://www.youtube.com/watch?v=LIVE_ID"
```

For Bilibili:

```bash
python prototype/delayed_live_player.py "https://live.bilibili.com/ROOM_ID"
```

The player window opens immediately, but media begins only after approximately 10 seconds have been accumulated.

Stop by closing ffplay or pressing `Ctrl+C` in the terminal.

## Useful options

Test another delay:

```bash
python prototype/delayed_live_player.py --delay 5 "URL"
```

Lower capture bandwidth/decoder load:

```bash
python prototype/delayed_live_player.py --max-height 480 "URL"
```

Use browser cookies for login/age/region-sensitive streams:

```bash
python prototype/delayed_live_player.py --cookies-from-browser chrome "URL"
```

Decode without opening a window (diagnostic only):

```bash
python prototype/delayed_live_player.py --headless "URL"
```

Print the generated commands:

```bash
python prototype/delayed_live_player.py --dry-run "URL"
```

## What to observe

Run the website player and the local player side by side. The prototype passes its first test if:

1. ffplay starts after the initial buffer period;
2. playback remains smooth for at least 10 minutes;
3. local audio and video remain synchronized;
4. the visible offset from the website player stays approximately stable;
5. memory does not climb without bound;
6. closing the player also stops yt-dlp.

Test YouTube first. Bilibili support depends on whether the installed yt-dlp can extract a usable muxed live format for that room.

## Expected limitations

- This intentionally selects a single format containing both audio and video. On YouTube that generally limits the prototype to 720p or lower. Separate high-quality video/audio formats require a more involved dual-input remux pipeline.
- yt-dlp extraction can break when platforms change. Keep yt-dlp current.
- Some streams need cookies or are unavailable by region/account.
- Network retries are mostly delegated to yt-dlp's downloader.
- The prototype buffers encoded bytes in memory. Ten seconds at ordinary 720p bitrates is small, but this is not a production ring-buffer implementation.
- The exact delay may include demux/player probing time in addition to the configured byte-release delay. Measure the visible result rather than assuming frame-accurate 10.000 seconds.
