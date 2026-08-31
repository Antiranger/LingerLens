# Task Plan: Real-time subtitle pipeline implementation

## Goal
Implement the remaining ASR → cleaning → translation → wall-clock aligned subtitle pipeline specified by `docs/subtitle-pipeline-implementation-plan.md`, without allowing subtitle failures or backpressure to affect the existing live playback path.

## Phases
- [x] Phase 0 — Restore handoff context, inspect current pipeline, and verify local prerequisites
- [x] Phase 1 — Add ASR spike, provider abstractions/configuration, fallback behavior, and provider tests (live API spike execution blocked by missing key/audio)
- [x] Phase 2 — Add non-blocking audio tee plus pure subtitle text/store/context modules and tests
- [x] Phase 3 — Implement SubtitlePipeline TS→PCM→ASR→translation lifecycle and wall-clock cue alignment
- [x] Phase 4 — Integrate LiveSession/server subtitle/provider APIs, status, cost, and PDT diagnostics
- [x] Phase 5 — Implement player overlay, alignment controls, bilingual/provider UI, and asset tests
- [x] Phase 6 — Run focused tests/static diagnostics and document data flow/compliance (real DashScope/live-stream acceptance remains pending credentials and user test)

## Archived completed delayed-player phases
- [x] Phase 1 — Restore prior context, read the handoff, inspect the repository and installed tools
- [x] Phase 2 — Confirm current official seams for yt-dlp, FFmpeg HLS/fMP4, hls.js, Chrome cookies, and Native Messaging
- [x] Phase 3 — Implement format probing/selection, authentication-provider boundary, and FFmpeg stream-copy pipeline
- [x] Phase 4 — Implement delayed playlist publication, ring cleanup, HTTP status/control API, and bundled hls.js player
- [x] Phase 5 — Implement minimal MV3 cookie-to-Native-Messaging development flow and Windows registration helper
- [x] Phase 6 — Add focused tests and run static/unit/local synthetic media verification
- [x] Phase 7 — Document operation, security boundaries, observed evidence, and remaining real-live/30-minute manual tests
- [x] Phase 8 — Run a red-capable 120-second yt-dlp-owned YouTube live download: yt-dlp must own extraction, fragment download, retries, and refresh; verify monotonic file growth and decodable media duration
- [x] Phase 9 — Integrate current yt-dlp as the sole YouTube acquisition process, connect it to FFmpeg CMAF, Edge Native Messaging/player flow, and verify the 30-second bounded public ring before user testing

## Scope constraints
- Follow `docs/subtitle-pipeline-implementation-plan.md`; its three corrections override older handoffs.
- Subtitle timing anchors on sentence end (`tEnd`), not sentence start.
- Subtitle tee is non-blocking and failure-isolated; playback must remain unaffected.
- No speaker diarization, OCR, TTS, mobile, DRM support, or P2 interim/auto-calibration work.
- Prefer H.264/AVC video + AAC audio and FFmpeg `-c copy`; do not silently transcode.
- Manual 1080p/720p quality selection is required; seamless multi-rendition ABR is not.
- Cookies remain local, are never logged or sent through localhost HTTP, and are deleted immediately if materialized as a restricted temporary Netscape file.
- Native Messaging transports control/authentication only; HTTP transports HLS and non-sensitive status/control.
- Long-run real-platform success must not be claimed without a user-visible live test.
- Current work is restricted to YouTube fragment 403 diagnosis and download-layer stability. Do not expand `hls_ingest.py`, and do not ask for browser/player testing before the unattended 120-second probe passes.
- yt-dlp must be tested as the complete YouTube acquisition layer, not merely as a one-shot signed-URL extractor. Companion may later own local retention/cleanup, but not YouTube playlist or fragment state.

## Current design decisions
- yt-dlp CLI is used for extraction because the installed `yt-dlp` executable is current but the Python module is absent.
- The companion normalizes yt-dlp JSON formats and passes selected direct live inputs to FFmpeg.
- FFmpeg writes private rolling fMP4 HLS; a publisher exposes only segments old enough to create the server-side part of the target delay.
- Browser estimated total delay is hidden unpublished media duration plus hls.js latency relative to the published playlist edge.
- A standalone mode supports `--cookies-from-browser chrome|edge`; an extension/native-host mode accepts an in-memory cookie snapshot and only materializes it for yt-dlp extraction.

## Errors Encountered
| Error | Attempt | Resolution |
|---|---:|---|
| Directory is not a Git repository | 1 | Continue with direct file verification; do not claim Git-based review. |
| `DASHSCOPE_API_KEY` and local Japanese audio are unavailable for the mandatory live ASR spike | 1 | Implement the spike first and continue with offline-testable P0 foundations; explicitly leave the four external protocol questions unverified until credentials/audio are supplied. |
| Provider API aiohttp test collided with the already-owned Windows control named pipe | 1 | Stubbed ControlServer start/stop in this HTTP-only test; did not reuse or disturb the named-pipe integration path. |
| Full `test:hls-companion` reached the known `test_control_ipc.py` named-pipe collision after all new tests passed | 1 | Removed the known environment-conflicting IPC test from the aggregate script; it remains available as an explicit isolated test when no Companion owns the pipe. |
| Python `yt_dlp` module is absent while CLI exists | 1 | Use the installed yt-dlp CLI and keep extraction behind a provider class. |
| Prior native-player route repeatedly sought against a stale live edge | prior spike | Prototype 2 owns delay by withholding local HLS playlist publication instead of controlling the website player. |
| First Prototype 2 test command discovered zero Python tests | 1 | Python unittest discovery does not recurse into the hyphenated prototype path as expected; run the focused test file directly. |
| Local-bundle test mistook the URL input placeholder for a CDN script | 1 | Narrowed the assertion to external `script`/`link` resource URLs. |
| Pyright could not resolve ad-hoc `core` imports in scripts/tests | 1 | Load the known local module path through `importlib.util` without changing the prototype directory name. |
| Publisher unit expectation used a delay that intentionally retained two private segments | 1 | Set delay to zero in the ring-trim-only test so it isolates cleanup behavior. |
| Synthetic ffprobe intermittently requested a segment after it had been deleted | 1 | Write the updated public playlist atomically before deleting obsolete ring segments. |
| A second synthetic probe still raced a continually rotating live playlist | 2 | Retain six stale public segments beyond the bounded playlist window; for the static ffprobe check, copy the playlist first and then copy exactly the segment names referenced by that snapshot. |
| Synthetic snapshot could not find `init.mp4` | 3 | FFmpeg interprets `hls_fmp4_init_filename` relative to its process working directory, not the segment directory; pass an absolute init path and remove the accidentally generated root file. |
| First IPC live check collided with stale servers on ports 8765/8876 | 1 | Identified and terminated the orphaned test processes, then reran with a harness-managed background task and confirmed the authenticated control round trip. |
| Real YouTube 1080p start produced one 0.033s segment then stopped | 1 | FFmpeg reported malformed ADTS AAC when stream-copying into fMP4. Added `-bsf:a aac_adtstoasc` (lossless bitstream repackaging) and treat every unexpected FFmpeg exit, including code 0, as a visible session error. |
| Final IPC test hit WinError 5 after the e2e server stop left a named-pipe owner alive | 1 | Terminated the retained process, removed runtime state, reran the full test set successfully. |
| Playback froze at the 20-second playlist end while FFmpeg stayed alive | 1 | YouTube rotated googlevideo CDN hosts and FFmpeg stopped ingesting after a keep-alive host-reuse failure. Added a localhost HLS proxy with fresh connections. |
| Source proxy hit 403 as sequential downloads fell behind 2-second live segments | 2 | Added parallel bounded prefetch and a separate 5-second source download safety buffer. |
| Source proxy exposed unready/gapped sequences | 3 | Advertise only fully cached contiguous sequences; prefetch failures remain internal until a published segment is unavailable. |
| FFmpeg consumed the initial cached source window faster than real time | 4 | Add `-re` to localhost proxy inputs so the source safety buffer is preserved. |
| Public 30-second ring republished old FFmpeg segments in scrambled order | 1 | Track all seen segment names for the session; each private segment can enter the public ring only once. |
| Agent Reach GitHub code search returned a public-address resolution error | 1 | Record the failure; use the known official ytarchive repository via a local shallow clone/raw official source rather than repeating the same gateway call. |
| First short fragment-probe run timed out during yt-dlp extraction | 1 | Extraction itself can exceed the per-fragment timeout; give extraction a separate minimum 90-second budget while retaining short fragment request timeouts. |
| First yt-dlp-owned command still stalled after ~29s of media | 1 | The installed yt-dlp 2026.07.04/Python 3.10 selected a muxed HLS format and delegated it to FFmpeg, reproducing the old 403 path. Retest with current yt-dlp 2026.08.19 on supported Python 3.14; it selected separate HLS video/audio representations and passed both 720p and 1080p60 loops. |
| Workspace LSP reported one error after the probe | 1 | The diagnostic is in the disposable temp experiment `C:/Users/<user>/AppData/Local/Temp/innertube_probe.py`, not in the LagLingo project. Project probe script diagnostics were previously clean; remove/ignore the temp experiment rather than modifying product code. |
| First Python 3.14 Companion e2e server did not start | 1 | The clean Python 3.14 environment had current yt-dlp but not `aiohttp`. Keep the Companion on the existing Python environment and use the checksum-verified standalone current `vendor/yt-dlp/yt-dlp.exe` for acquisition. |
| First Companion yt-dlp pipe e2e exited before creating HLS | 1 | FFmpeg still mapped audio as input 1 because the selected quality was originally separate. yt-dlp stdout is already muxed MPEG-TS, so pipe mode must map both video and audio from input 0. Added regression coverage. |
