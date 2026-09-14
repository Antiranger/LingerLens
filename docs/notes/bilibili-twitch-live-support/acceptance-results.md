# Bilibili / Twitch live acceptance results

Date: 2026-09-03 (local session clock)

## Environment

- Downloader: vendored `prototype/hls-companion/vendor/yt-dlp/yt-dlp.exe` (`vendored-2026.08.19` label)
- Media path: production `YtDlpLiveIngest` → localhost TCP Pump → production packaging FFmpeg → delayed fMP4 HLS
- Authentication: anonymous for all completed media smokes
- Runtime FFprobe: not used
- Secrets: signed URLs and probe JSON contents are not reproduced here

## Matrix

| Platform/sample | Probe | Auto / legs | Production media smoke | PDT | Stop cleanup | 10-minute playback | Subtitles / PCM | Messages | Result |
|---|---|---|---|---|---|---|---|---|---|
| Bilibili room `22637261` | Extractor returned `No video formats found` at test time | n/a | not run | n/a | n/a | not run | not run | not run | **blocked** (room unavailable/no formats at acceptance time; fixture remains covered by automation) |
| Bilibili room `6` | `BiliLive`, live, 8 formats | AVC HLS `ultra_high_res-2`, 1 leg | Local playlist became ready through production path | present | session idle; ingest not running | not run | not run | danmaku not exercised | **short smoke pass** |
| Bilibili room `21452505` | `BiliLive`, live, 8 formats with AVC/HEVC, HLS/FLV and CDN mirrors | AVC HLS `ultra_high_res-2`, 1 leg | not started (second probe/selection sample) | not observed | n/a | not run | not run | not run | **probe/selection pass only** |
| Twitch `twitchplayspokemon` | `TwitchStream`, live, 6 formats | Source `1080p60__source_`, 1 leg | Local playlist became ready through production path | present | session idle; ingest not running | not run | not run | unsupported by design | **short smoke pass** |
| Twitch `eslcs` | `TwitchStream`, live | Source `1080p60__source_`, 1 leg | Local playlist became ready through production path | present | session idle; ingest not running | not run | not run | unsupported by design | **short smoke pass** |
| Twitch `lck`, `tarik` | network probe failed with TLS EOF in this environment | n/a | not run | n/a | n/a | not run | not run | n/a | **blocked by transient/network error** |

## Observed constraints / honest gaps

- No sample was played continuously for 10 minutes in this implementation session; short production-path startup smokes are not promoted to long-run acceptance.
- Subtitle-enabled ASR PCM was not exercised because it would invoke the user's configured cloud Provider and credentials. Deterministic tests continue to cover construction-time byte-zero tee and single-Pump audio extraction.
- No Twitch pre-roll/mid-roll or `EXT-X-DISCONTINUITY` boundary was observed. No Twitch-only downloader argument was added.
- Bilibili danmaku was not part of the completed media smoke and remains a best-effort sidecar.
- A current YouTube live regression was not run in this session; the complete deterministic suite preserves the dual-selector/two-Pump tests, but that is not a real-platform verdict.
- Process cleanup checks covered the launched production media smokes: after Stop, `LiveSession` reported idle and `YtDlpLiveIngest` reported not running. A full OS-wide process/thread/resource census was not run.
