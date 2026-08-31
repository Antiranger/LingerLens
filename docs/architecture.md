# Architecture

LagLingo is a Windows-first local application composed of a loopback Companion, a self-contained browser player, and optional browser-extension bridges.

```text
YouTube/Bilibili URL
  -> vendored yt-dlp (probe/download; optional platform Cookie snapshot)
  -> FFmpeg stream-copy/remux
  -> private rolling fMP4 HLS + delayed publisher
  -> loopback HTTP playlist/player (bundled hls.js)

Audio tee -> FFmpeg PCM -> active ASR provider -> translation provider/fallback
           -> timed subtitle store -> browser subtitle scheduler/overlay
```

## Ownership boundaries

- `prototype/hls-companion/companion/server.py` owns the loopback HTTP API, lifecycle, validation, and static player serving.
- `core.py` and `ytdlp_ingest.py` own local media processes and delayed HLS publication. Remote extraction remains yt-dlp's responsibility.
- `providers/` owns the versioned Provider Catalog and protocol adapters. Model settings is the user-facing CRUD/selection boundary.
- `subtitle_pipeline.py` owns PCM ingestion, ASR/translation results, usage attribution, and cost estimates.
- `web-player/` owns playback, target total delay controls, stage fullscreen, subtitle scheduling, and draggable/style-persistent overlay behavior.
- `extension/`, `native_host.py`, and `control_ipc.py` own browser-to-local authentication transfer; media and ordinary status use loopback HTTP.

Runtime state belongs under `prototype/hls-companion/runtime/` and is deliberately absent from releases except for the sanitized example. See [security.md](security.md) and the detailed [HLS Companion README](../prototype/hls-companion/README.md).
