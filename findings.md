# Findings

## Repository
- Repository initially contained only two handoff documents; no existing extension code, package manifest, tests, or design system.

## Required behavior extracted from handoff
- Poll primary video once per second.
- Record media time, paused, playback rate, ready state, seekable ranges, live edge, relative actual delay, and seekable-window length.
- User-selectable 5s/10s target.
- Initial hard seek to `liveEdge - target`.
- No intervention inside target ±1s; record/correct only when absolute target error exceeds 2s.
- Detect likely auto-jumps to live edge, media reloads, buffering, and video replacement.
- On-page panel plus JSON/CSV export.
- YouTube/Bilibili differences must live in adapters.
- Source/viewer two-tab behavior is an observation workflow, not audio capture.

## Implementation direction
- Use a content-script-only MV3 extension with narrow YouTube and Bilibili host permissions.
- A generic controller owns sampling, counters, event records, corrections, summary metrics, and exports.
- Platform adapters own host matching and primary-video selection/scoring.
- Keep state in the page session; export observations for later analysis.

## Evidence limitations
- Automated code verification cannot establish 30-minute live-platform support classifications.
- Final platform conclusions require user-run live sessions for all four platform/target combinations and disturbance cases.
- No live-room credentials/session were available in this implementation run, so YouTube/Bilibili 5s/10s outcomes remain deliberately unclassified.

## Verification findings
- All extension scripts parse under Node's JavaScript parser.
- Manifest is MV3, limited to YouTube and Bilibili hosts, and requests no extension permissions beyond host access.
- Unit checks cover summary percentiles/error/stability and CSV escaping.

## Native YouTube player test result
- The exported YouTube run was invalid as a platform verdict because the spike treated a static `seekable.end()` value as the live edge.
- Over roughly 72 seconds it issued 24 seek requests and recorded 28 buffering events, causing the visible repeated stalls.
- Pausing the website player and resuming also returned the tested stream to the latest position, so the simple native-player delay path is not a dependable product foundation.

## yt-dlp prototype direction
- Local environment has `yt-dlp`, FFmpeg, ffplay, Node, and Python installed.
- yt-dlp successfully identified the tested YouTube URL as a live `m3u8_native` muxed 1280x720 MP4 format.
- For a minimal delay proof, selecting a single muxed live format avoids synchronizing separate video and audio inputs.
- The prototype can preserve encoded media and avoid browser screen re-encoding by piping yt-dlp's MPEG-TS output through a timestamped 10-second byte-delay relay into ffplay.
- The configured delay is relative to local media-byte arrival; platform/CDN latency and player probe latency are additional.
- yt-dlp `--live-from-start` is experimental and unnecessary here; the prototype explicitly uses the current live position with `--no-live-from-start`.

## Product architecture research: login, 1080p, and browser playback
- yt-dlp authentication for YouTube now relies on cookies rather than OAuth; some YouTube clients/formats may additionally require a PO Token, and this is a moving compatibility boundary.
- A Chrome MV3 extension with `cookies` plus domain host permissions can read allowlisted YouTube/Bilibili cookies, including metadata for HttpOnly and partitioned cookies. This is more deterministic than guessing the user's Chromium profile from a native companion, but it is a sensitive permission and must be opt-in and local-only.
- YouTube 1080p commonly exposes separate video-only and audio-only formats. The companion must probe formats, expose quality choices, acquire both inputs, and let FFmpeg align/remux them; the existing single muxed 720p prototype is insufficient for the product requirement.
- For the browser playback seam, localhost HLS/CMAF with bundled hls.js is the recommended product baseline over a custom WebSocket+MediaSource protocol. Hls.js already owns MSE append queues, buffering, stall recovery, and playlist/quality behavior; the custom route would reimplement these failure-prone responsibilities.
- Manual quality selection can fetch/remux only one selected rendition, minimizing bandwidth. Seamless automatic bitrate switching requires multiple simultaneous renditions or a more complex source-switch pipeline and should not be conflated with merely supporting selectable 1080p.
- The preferred output is H.264/AAC fragmented MP4 HLS (CMAF-style) when the selected source codecs are browser-compatible, using FFmpeg stream copy. Transcoding is a compatibility fallback, not the default, because it increases CPU load and quality loss.

## Prototype 2 implementation findings
- Current local tools: Python 3.10.11, yt-dlp 2026.07.04 CLI, FFmpeg 9.0, Node 24.12.0, npm 11.6.2. The Python `yt_dlp` module is not installed; `aiohttp` is installed.
- hls.js current npm release observed during implementation is 1.7.1; it will be copied into the prototype so the browser player has no CDN dependency.
- Official yt-dlp documentation supports embedding through Python but also confirms FFmpeg/ffprobe are central for merging separate video/audio. For this machine, the narrower prototype seam is the installed CLI plus JSON output.
- Chrome's official Cookies API documentation supports partition keys, and Native Messaging on Windows uses a per-host manifest registered under the Chrome/Edge NativeMessagingHosts registry key with an extension origin allowlist.
- Sensitive extension cookies cannot be placed on a command line or sent to localhost HTTP. The implemented Native Host uses an authenticated local named pipe/AF_UNIX control channel; because the CLI cannot consume an in-memory CookieJar directly, the companion creates a user-only temporary Netscape cookie file only around yt-dlp extraction and deletes it immediately.
- FFmpeg can produce rolling HLS with fragmented MP4 segments using stream copy. Delayed publication must copy/rewrite only completed segments into a public directory; directly serving FFmpeg's live playlist would eliminate the controlled delay.
- `hls_fmp4_init_filename` must be an absolute path (or otherwise deliberately rooted): FFmpeg interprets a bare `init.mp4` relative to its process working directory rather than `hls_segment_filename`'s directory.
- A public rolling HLS implementation should retain a few stale segment files after removing them from the advertised playlist. Immediate deletion creates races with clients or probes that fetched the preceding playlist revision.
- The current public YouTube test live stream exposes a directly muxed 1920×1080@30 AVC/AAC HLS format. This verifies real 1080p probing but does not validate the separate-video+audio path against a live platform source.
- The synthetic local run verified 1920×1080 H.264 + AAC fMP4 HLS generation and delayed publication. It used encoding only to manufacture a deterministic test input; the actual platform pipeline command remains stream copy.
- Real YouTube HLS supplies AAC as ADTS. Stream-copying that audio into fMP4/CMAF requires FFmpeg's `aac_adtstoasc` bitstream filter. This changes AAC container framing without decoding/re-encoding or quality loss; omitting it caused a single 0.033s segment followed by FFmpeg termination.
- YouTube media playlists rotate segment requests across googlevideo CDN hosts. FFmpeg 9 can stop producing output after `Cannot reuse HTTP connection for different host`; a localhost HLS reverse proxy is required to normalize those URLs to one origin.
- Segment download time is part of total latency. A source safety buffer (5s) must be modeled separately from post-download publication delay (2s default) and hls.js playback latency. Simply stacking the old 7s delay would overshoot the 10s target.
- The source proxy must publish only fully cached, contiguous media sequences; exposing an unready segment or jumping across a cache gap causes FFmpeg to terminate or freeze. FFmpeg also needs `-re` against the local cached playlist so it does not consume the safety buffer faster than real time.
- The public browser playlist is now limited to approximately 30 seconds. A seen-segment set prevents FFmpeg's retained private playlist entries from being republished after they slide out of the public ring.

## YouTube 403 network-path audit
- The current process environment contains an `ALL_PROXY` setting, and yt-dlp verbose output confirms it automatically builds an `all` proxy map from that environment. No explicit `--proxy` argument was passed, but yt-dlp did use the inherited proxy configuration.
- Windows Internet Settings also has a proxy enabled/configured, while WinHTTP reports direct access. Different download tools may therefore choose different network paths unless the probe explicitly pins proxy or direct mode.
- A redacted public-egress fingerprint check showed the default and proxy-bypassed requests currently exit through the same observed public IP. This makes a simple global IP ban unlikely, but does not rule out proxy routing/connection/DNS behavior or signed-URL IP binding as a contributor.
- HTTP 403 means the Googlevideo CDN rejected a particular fragment request. Existing evidence does not prove an IP ban: requests from the same environment have alternated between 403 and 200, and `X-Head-Seqnum`/sequence freshness plus signed URL refresh remain material variables.

## Standard YouTube live-download path research
- YouTube's official Live Streaming API is a broadcaster-management/ingestion API: it creates/manages broadcasts and gives creators RTMP(S)/HLS/DASH upload endpoints. It does not expose an official viewer-side media-download API for arbitrary live pages.
- yt-dlp officially supports downloading active livestreams. Current-time recording is the default (`--no-live-from-start`); downloading from the beginning is explicitly experimental. Its standard downloader already provides fragment retries, retry backoff, concurrent fragments, live MPEG-TS output, cookies, client selection, proxy pinning, and extractor refresh behavior.
- The yt-dlp PO Token guide currently recommends a PO Token Provider plugin with the `mweb` client for GVS formats. It also states that ordinary YouTube HLS live streams generally do not require a GVS PO Token, while some direct/adaptive GVS clients do. This makes the client/format choice a first-class part of stability, not merely a retry setting.
- ytarchive is purpose-built for YouTube livestream archival and has the right sequence downloader model, but its latest stable release is v0.5.0 (2024-09-20), and its own issue #221 documents fragment URLs expiring/403ing after about 30 seconds without the correct `pot`; suggested mitigations include suitable clients/PO tokens or external extraction through yt-dlp. The current public binary also failed the tested stream by starting at sequence 0.
- Streamlink and N_m3u8DL-RE are mature generic stream/HLS downloaders, but they do not own YouTube's current extraction/PO-token contract. Both already failed the target stream in local tests, so switching to another generic HLS engine is not the solution.
- Recommended architecture change: stop extracting a signed HLS URL and handing it to a separate long-lived downloader. Make current yt-dlp the authoritative YouTube acquisition process, with pinned proxy/direct mode, explicit client/token policy, and a supervised restart/resume boundary. Only consider a thin YouTube-specific sequence downloader if yt-dlp's own 120-second file/pipe download still cannot recover.

## yt-dlp-owned 120-second results
- The original `YxWNvY8XNIQ` source ended during testing and became `post_live`, so it could no longer provide a valid live-duration loop.
- Installed yt-dlp 2026.07.04 under deprecated Python 3.10 selected muxed HLS format 95 and still delegated media I/O to FFmpeg. That path reproduced `Cannot reuse HTTP connection for different host`, then repeated 403s and froze at 26–38 seconds. Passing a downloader selector did not change this legacy path.
- A clean supported environment was created with Python 3.14 and current yt-dlp 2026.08.19. Its YouTube extraction selected separate HLS video/audio representations instead of the legacy muxed HLS format.
- Current yt-dlp 720p validation on live `EEM7a3mHMR4` ran for 128 seconds after first file growth. Every 10-second interval grew; no 403, cross-host reuse error, or failed segment was observed. `ffprobe` read approximately 155 seconds of 1280×720 H.264/AAC MPEG-TS.
- Current yt-dlp 1080p60 validation on live `fbVkrk-mWqo` used video format 312 plus audio format 234. It ran for 126 seconds after first growth; all 10-second samples grew; no 403, cross-host reuse error, or failed segment was observed. `ffprobe` read approximately 145 seconds of 1920×1080@60 H.264/AAC MPEG-TS.
- The successful outputs prove the revised ownership boundary for the 120-second gate: current yt-dlp can own extraction plus separate live video/audio acquisition and produce a continuously growing muxed transport stream. The nonzero process code was caused by the harness terminating the still-running process after the duration gate, not by a download failure.
- The standalone current yt-dlp Windows executable can be vendored independently of the Companion's Python runtime. This lets aiohttp continue on the existing environment while acquisition uses a supported/current yt-dlp build.
- In current yt-dlp live JSON, YouTube HLS video renditions (e.g. 312) are video-only and audio renditions 233/234 may report an unknown codec. Selecting 312+234 produced actual H.264/AAC MPEG-TS, verified downstream by FFmpeg/ffprobe; the normalization must treat those known HLS audio IDs as provisional AAC and verify rather than reject them.
- yt-dlp stdout for separate video+audio is already one muxed MPEG-TS input. FFmpeg must map `0:v:0` and `0:a:0`; retaining the old two-remote-input mapping (`1:a:0`) prevents HLS output creation.
- The integrated 1080p60 API run verified cleanup behavior: the advertised public ring held exactly 30 seconds, disk retained only six additional stale public segments for request-race safety, and private FFmpeg retention remained bounded. Public byte totals can decrease when large stale files are deleted, so file-count/window invariants are the correct cleanup signal rather than monotonic public-directory bytes.

## Productization ticket research (2026-08-31)
- The current version-1 provider config already stores lists plus active IDs, but the model-settings route edits only a built-in ASR choice and the first OpenAI-compatible translation record; the UI cannot perform general provider CRUD. Model-settings GET currently masks secrets, contrary to the newly requested loopback-visible-key behavior.
- Existing provider abstractions are deep enough to extend: ASR is an `ASRStream` event interface, translation returns `TranslationResult.usage`, and registry kinds are centralized. The highest stable TDD seams are loopback HTTP API, provider/pipeline public interfaces, and browser-observable player behavior.
- Official OpenAI Audio Transcriptions uses multipart `POST /audio/transcriptions` with file/model/language and optional timestamp-capable response formats. Xinference and the faster-whisper ecosystem expose compatible endpoints. Since those servers do not share the current DashScope WebSocket protocol, live support should use bounded PCM-to-WAV/VAD requests rather than claim native streaming capabilities.
- The OpenAI-compatible translation provider already returns response `usage`, but SubtitlePipeline records latency and discards usage. This is the narrow insertion point for token accumulation and cost estimation.
- Subtitle DOM is a sibling overlay of `<video>`. Browser native video fullscreen therefore excludes it; the supported fix is stage/container fullscreen plus a dedicated draggable subtitle-window controller.
- The UI exposes an internal `publishDelaySeconds` selector, while hls.js separately adds about 12 seconds of live-sync distance. A user-facing total-delay contract must coordinate both and report measured actual delay separately.
- `LiveSession.stop()` clears processes and publisher but leaves `page_url`, quality, timestamps, and error identity. Player status explicitly treats idle + prior page URL + no playlist as an error, matching the reported inability to cleanly start another live after Stop.
- Current yt-dlp Bilibili extractor defines login as the presence of `SESSDATA` from `api.bilibili.com`. Other valid Bilibili cookies should be preserved, but evidence does not justify making `bili_jct` or `DedeUserID` mandatory.
- The working directory is not a Git repository. GitHub CLI is authenticated as `Antiranger` with `repo` and `workflow` scopes, so a private repository can be created after secrets/runtime artifacts are excluded and clean-clone verification passes.
