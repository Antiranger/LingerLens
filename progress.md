# Progress

## Session log
- Read `live_delay_platform_spike_handoff.md` completely.
- Inspected repository: no implementation exists yet.
- Read prior product handoff sufficiently to confirm this spike must remain isolated from ASR/translation architecture.
- Selected a minimal MV3 content-script architecture with platform adapters and a compact product-style debug panel.
- Created planning files.

- Implemented `extension/manifest.json` with narrow YouTube/Bilibili host permissions.
- Implemented platform-isolated primary-video adapters.
- Implemented per-second sampling, initial seek, drift logging/correction, auto-live-jump heuristics, reload/buffering/source-stall counters, summary metrics, console logging, and JSON/CSV exports.
- Implemented the on-page debug panel.

- Added installation/operation guidance, a 30-minute disturbance protocol, and a reusable result template.
- Added Node tests for manifest scope, script syntax, metrics, and CSV export.
- Verification: `npm test` passed all 5 tests.
- LSP verification was unavailable because no LSP server is configured; Git status was unavailable because the directory is not a Git repository.
- Removed the unused `storage` permission to keep the extension minimal.

## Current phase
The native-player extension spike is retained as evidence, but its first YouTube control strategy is no longer the active direction.

## yt-dlp delayed-player prototype session
- Inspected the exported YouTube run and confirmed repeated hard seeks caused the observed stalls.
- Confirmed pausing/resuming the tested website player returns it to latest live position.
- Verified local availability of yt-dlp, ffmpeg, ffplay, Node, and Python.
- Verified yt-dlp can extract the tested current YouTube livestream as a muxed 1280x720 HLS format.
- Implemented `prototype/delayed_live_player.py`: yt-dlp MPEG-TS stdout → timestamped fixed-delay relay → ffplay.
- Added a headless decode mode, dry-run mode, max-height selection, optional browser cookies, status output, and child-process cleanup.
- Added `prototype/README.md`, a package script, and a root README pointer.
- Verification completed: Python bytecode compilation passed, LSP diagnostics are clean, dry-run command construction passed, and the existing 5 Node tests passed.
- A real headless YouTube live run confirmed yt-dlp selected muxed 720p HLS, produced MPEG-TS, the relay released it after the configured test delay, and FFmpeg decoded it. The run also showed intermittent YouTube CDN retry/403 messages, so long-run smoothness remains a user-visible test rather than a claimed result.
- Adjusted the relay to accumulate the entire startup delay window before releasing bytes, avoiding the first version's one-fragment-at-a-time startup starvation.
- Current state: prototype implementation complete; user should run the visible 10-second ffplay test and report smoothness/offset.

## Prototype 2 session
- Read `C:/Users/<user>/AppData/Local/Temp/laglingo_browser_hls_companion_handoff.md` completely and replaced the completed prior plan with a scoped Prototype 2 plan.
- Re-inspected the repository and local toolchain. Confirmed current yt-dlp CLI/FFmpeg/Node availability, local `aiohttp`, absent Python `yt_dlp` module, and current hls.js npm release.
- Reviewed current official yt-dlp/Chrome Native Messaging/Cookies guidance needed for the extraction and login seams.
- Selected an isolated `prototype/hls-companion/` implementation with private FFmpeg HLS output, delayed public playlist publication, bundled hls.js, and a minimal MV3 Native Messaging cookie bridge.
- Implemented the core probe/selection/authentication boundaries, FFmpeg stream-copy command, delayed playlist publisher, localhost API/server, browser player, MV3 cookie bridge, and Windows host registration helper.
- First focused verification exposed three test/tooling issues (Python discovery, overly broad CDN assertion, Pyright imports); recorded them in the plan and changed the verification path rather than repeating the failing commands unchanged.
- Fixed HLS publication races and an FFmpeg init-segment path error found by the synthetic verification loop. Public media cleanup is bounded while retaining six stale segments beyond the advertised playlist to tolerate in-flight requests.
- Verification passed: 5 Python core tests, 1 authenticated IPC test, 3 Prototype 2 JavaScript/asset tests, 5 existing spike tests, Python compilation, and clean LSP diagnostics.
- Synthetic smoke passed with generated 1920×1080 H.264 video + AAC audio, three seconds of private delayed media, published fMP4 HLS, and ffprobe codec/resolution confirmation.
- Started the real localhost server and probed the current YouTube test live URL through the API; yt-dlp returned a compatible 1920×1080@30 AVC/AAC format plus 720p/480p choices.
- Opened the local player in a real Chrome browser with Playwright, inspected the accessible UI and captured `output/playwright/hls-companion-initial.png`. The only browser console issue was a missing favicon, then a 204 favicon route was added.
- Added complete Prototype 2 run/login/registration/security/test documentation and updated the root README pointer.
- Hardened the login path after review: sensitive cookie snapshots no longer traverse localhost HTTP. Native Messaging now hands them to the running Companion through an authenticated Windows named pipe / user AF_UNIX socket. Added and passed an IPC round-trip test and a live Companion control-channel check.

## Prototype 2 current status
- Implementation and automated/local verification are complete.
- Not completed or claimed: 30-minute Chrome/Edge playback, account-restricted live verification, a real YouTube separate-video+audio 1080p source, Bilibili live validation, and final calibration of total delay to approximately 10 seconds.

## Real playback startup bug fix
- Reproduced the user's exact symptom from the running Companion: selected 1080p, then status returned idle with no public playlist; disk contained only one 0.033s segment and an ended private playlist.
- The exact FFmpeg command reported malformed AAC for fMP4 and requested `aac_adtstoasc`. Added the lossless AAC bitstream filter and added regression coverage for the command.
- Fixed status handling so a clean/zero FFmpeg exit before user stop is still reported as an error instead of silently appearing idle.
- Real YouTube end-to-end API regression passed: the fixed 1080p process stayed running, accumulated four 2s private segments, then exposed a public delayed playlist after about 9 seconds.

## Long-play stall and delay-budget fix
- Reproduced the 20-second freeze: after 125 seconds runtime the private input had stopped at 28 seconds and the public playlist at exactly 20 seconds, while FFmpeg remained alive.
- Captured FFmpeg's `Cannot reuse HTTP connection for different host` behavior as YouTube rotated googlevideo CDN hosts.
- Added a localhost HLS source proxy with fresh remote HTTP connections, parallel bounded prefetch, a 5-second source safety buffer, and contiguous cached-sequence publication.
- Added FFmpeg `-re` for local cached inputs so it consumes the source safety buffer at media rate instead of racing to the edge.
- Changed the default post-download publication delay from 7 seconds to 2 seconds; the total target is now 5s source safety + 2s post-download + roughly 2–3s browser buffering.
- Fixed the public ring to approximately 30 seconds and prevented old private segments from being re-added after public eviction.
- Verified the public playlist contains 15×2-second segments (30 seconds), disk retention remains bounded, and playlist segment order is monotonic.
- A 130-second real-live run completed without FFmpeg/403 failure after the contiguous source cache changes. A second currently-live source remained running for 130 seconds with a monotonic public timeline; its source advanced in larger bursts, so longer 30-minute visual testing remains required.
- Subsequent 1080p60 testing disproved the generic proxy/Streamlink/N_m3u8DL-RE approaches for this YouTube stream: all eventually stopped around a fragment 403 or playlist-consumption mismatch.
- Inspected ytarchive source and identified its YouTube-specific recovery model: build `googlevideo base + &sq=N`, set Host/Referer/Origin headers, track `X-Head-Seqnum`, refresh VideoInfo/download URLs on 403, download concurrently, and write strictly in sequence.
- Created focused next-session handoff: `C:/Users/<user>/AppData/Local/Temp/laglingo_youtube_403_ingest_handoff.md`. Do not ask the user to retest the current experimental player until a 120-second 403 recovery loop passes.

## YouTube fragment 403 diagnosis session
- Restored both handoffs, the project planning files, and the current ingest code. Confirmed `server.py` is still on the disproved Streamlink experiment and `hls_ingest.py` must not be expanded.
- Started Phase 8: build one unattended command that compares playlist segment requests with ytarchive-style `base + sq=N`, records only redacted metadata, refreshes extraction on 403/stall, and requires 120 seconds of monotonic sequence progress.
- Agent Reach GitHub code search failed with a public-address resolution error; the next source inspection will use the known official ytarchive repository without exposing signed media URLs.
- Researched the standard acquisition options. Confirmed there is no official YouTube viewer-download API; yt-dlp is the actively maintained authoritative extractor/downloader and officially supports recording current live content. ytarchive is specialized but its stable binary and documented 30-second `pot`/403 issue make it unsuitable as an unmodified dependency for this stream. Streamlink/N_m3u8DL-RE remain generic HLS engines and are already disproved locally.
- Revised the intended direction: first run a clean, proxy-pinned 120-second yt-dlp-owned download using an explicit YouTube client/PO-token policy, rather than extracting an HLS URL and handing it to FFmpeg/Streamlink. Do not integrate any player until that acquisition command passes.
- User approved the revised ownership boundary: trust yt-dlp with the complete live-download task; LagLingo will only own local cache retention/cleanup after this download layer is proven.
- First full command using the installed yt-dlp 2026.07.04/Python 3.10 was a valid red loop: despite being launched through yt-dlp, muxed HLS was delegated to FFmpeg, which froze after 26–38 seconds with cross-host reuse errors and repeated 403s.
- Created an isolated Python 3.14 environment with current yt-dlp 2026.08.19 and reran the acquisition-only loop. A 720p H.264/AAC live stream grew throughout 128 seconds with zero 403/failed segments; ffprobe reported about 155 seconds of media.
- Repeated the gate on an active 1080p60 stream using separate HLS video 312 plus audio 234. The output grew throughout 126 seconds with zero 403, zero cross-host reuse errors, and zero failed segments; ffprobe confirmed 1920×1080@60 H.264 + AAC and about 145 seconds of media.
- Phase 8 is complete. No player or generic proxy was tested or extended. Next work is the narrow integration: current yt-dlp process owns YouTube acquisition; LagLingo owns only bounded local retention and the existing FFmpeg CMAF/public-window output.

## Edge integration and bounded-cache verification
- Added checksum-verified current `vendor/yt-dlp/yt-dlp.exe` version 2026.08.19 so the Companion does not depend on the machine's deprecated Python 3.10 yt-dlp installation.
- Added `companion/ytdlp_ingest.py`: yt-dlp owns YouTube extraction, separate live video/audio download, retries, and MPEG-TS stdout. Signed URLs are redacted from status logs.
- Replaced the active Streamlink path in `server.py` with yt-dlp stdout → FFmpeg local-only input. FFmpeg maps both video and audio from the single muxed pipe and performs only stream-copy CMAF packaging.
- Updated current yt-dlp format normalization to accept HLS audio representations 233/234 whose probe codec is unknown, while still verifying actual AAC at the FFmpeg seam.
- Updated the Edge extension identity/copy and the local player text to describe yt-dlp-owned downloading. Native Messaging still transports the current tab's cookie snapshot; the player retains the URL/authToken flow.
- Real API e2e on `fbVkrk-mWqo` selected 1920×1080@60 format 312 + audio 234, generated public CMAF after startup, stayed running for 90 seconds, and exposed downloader=`yt-dlp` without source errors.
- At 90 seconds the public playlist was exactly 30 one-second segments; public disk retained 36 segment files (30 advertised + six race-safety stale files), so deletion was active and bounded. Private FFmpeg retention was 60 files and bounded by its HLS flags/list/delete thresholds.
- Verified `/api/stop` terminates yt-dlp and FFmpeg; no `yt-dlp.exe` process remained after stop.
- Kept the Native Messaging cookie temp file alive until yt-dlp reaches its download stage, then deletes it exactly once; this fixes the previous extraction-only lifetime assumption while preserving restricted temporary storage and cleanup.
- Final smoke after auth-lifetime and stop-order fixes reached a public playlist using downloader=`yt-dlp`, then returned cleanly to idle on stop.

## Real browser playback fixes (first true end-to-end playback verification)
- First-ever real browser playback test (previous sessions verified only API/segment generation). Two defects found and fixed in `web-player`:
  1. Autoplay blocked: `<video>` was not muted, async `video.play()` in `MANIFEST_PARSED` was rejected with `NotAllowedError` even after user interaction, and `.catch(() => {})` swallowed it silently. The status line showed "延迟播放中" from server state regardless of `video.paused`. Fixed with muted autoplay, a visible fallback message, click-to-retry, and paused-aware status text.
  2. Repeated forward jumps ("7s→13s→18s"): FFmpeg stream-copy segments follow source keyframe cadence (observed up to 5s), but hls.js was configured with time-based `liveSyncDuration: 2` / `liveMaxLatencyDuration: 5`. The playhead rode the live edge, starved (lag decayed to ~0.01s), then hls.js force-seeked +6s. Reproduced with 1s sampling over 60s (2 jumps, 4 stall-seconds). Fixed with count-based chasing: `liveSyncDurationCount: 3`, `liveMaxLatencyDurationCount: 8`, `maxBufferLength: 30`.
- Post-fix 45s verification on the same live source: zero jumps, zero stalls, 1.01 s/s advance, playhead stable 11–22s behind the public edge (within the 30s public window).
- Consequence: total end-to-end delay is now ~18–29s (source safety + publish delay + 3-segment browser chase). Approaching ~10s would require true 1–2s segments, i.e. re-encoding to force keyframes, which the project's no-silent-transcode policy forbids. 3-segment chasing is the correct floor for stream copy.

## Timestamp-continuity and dual-leg ingest fixes (real 1080p60 low-latency stream playable)
- Symptom after the 1s-segment change: on stream RQOx_fdVDzA playback stalled 83 of 100 sampled seconds with 12 forced seeks; the browser media timeline teleported +47733s (twice), then reset.
- Root cause layer 1: yt-dlp's merged "312+234" mode uses one internal ffmpeg reading two HLS playlists serially; on this 1s-fragment low-latency stream it chronically logged "skipping N segments ahead, expired from playlists" (23 discontinuities in 8 minutes of capture: ~5s audio holes, one -24s video rewind). `--downloader-args ffmpeg_i:-http_multiple 1` tested and does NOT fix it.
- Root cause layer 2: `-c copy` passed those PTS discontinuities into fMP4 tfdt; replaying the capture through the packaging command produced 139 output timeline jumps.
- Fix A: `_VIDEO_SETTS`/`_AUDIO_SETTS` bitstream filters in `core.py` rebuild a continuous per-track timeline (sane deltas preserved, discontinuities collapse to one frame duration) while remaining pure stream copy. Capture replay verified: 139 -> 0 output jumps; synthetic B-frame input decodes 1200/1200 frames clean.
- Fix B: `ytdlp_ingest.py` rewritten to spawn one yt-dlp process per format leg with localhost TCP pumps feeding the packaging ffmpeg (two stdins are not possible on Windows). Single-leg acquisition measured zero mid-stream skips.
- Verified end-to-end on the same live stream: 30-segment public window, hiddenMediaSeconds stable at 3.0, user confirmed playback works ("确实成功了"). 18 pytest + web-asset tests pass (test_control_ipc fails only while a server instance holds the named pipe).
- Disk behavior re-verified bounded: private 210 segments (~116MB), public 36 (~20MB), stable across time.
- Next phase handoff written: `docs/asr-translation-handoff.md` (Bailian ASR -> translation -> embedded Chinese subtitles).

## Real-time subtitle implementation session
- Read `docs/subtitle-pipeline-implementation-plan.md` and `docs/asr-translation-handoff.md`; inspected the current dual-leg yt-dlp, LiveSession/server, player, tests, and planning state.
- Environment check: Python 3.10.11 and aiohttp 3.14.3 are available. `DASHSCOPE_API_KEY` and a local Japanese audio sample are unavailable, so the required external ASR spike cannot be executed in this session yet; implementation proceeds with the spike tool and offline-testable foundations first.
- Replaced the completed delayed-player plan with a scoped subtitle-pipeline implementation plan while preserving archived phases/errors.
- Implemented the first playback-safety seam in `companion/ytdlp_ingest.py`: best-effort `_TcpPump` tee after `sendall`, exception isolation/drop counting, audio/muxed-leg attach/detach, and tee stats.
- Added tee regression tests verifying identical bytes reach the subscriber, subscriber exceptions do not break socket delivery, and the correct audio leg is selected. Verification: 6 tests passed; LSP diagnostics clean.
- Exposed the first private HLS segment `PROGRAM-DATE-TIME` as `DelayedPlaylistPublisher.snapshot().pdtEpoch`, with ISO-8601 parsing and regression coverage. Verification: 6 core tests passed; LSP diagnostics clean.
- Added the complete provider foundation: shared ASR/translation interfaces, four registered protocol kinds, provider config creation/validation/masking/restricted atomic updates, translation fallback cooldown, `runtime/providers.example.json`, and `scripts/asr-spike.py`. Focused verification: 9 tests passed; the live spike remains blocked by missing credentials/audio.
- Added pure subtitle modules: cleaning/dedup/splitting/hold logic, bounded revision-aware CueStore, rolling translation context, and deterministic prompts. Focused verification: 15 tests passed; LSP diagnostics clean for new modules.
- Added `/api/providers` GET/POST integration, default providers path/config loading, masked output, restricted updates, and probe channel metadata. The first HTTP test hit the known active named-pipe collision; the HTTP-only test now stubs ControlServer lifecycle and passes without touching IPC. LSP diagnostics clean.
- Implemented `SubtitlePipeline`: bounded cross-thread tee sink, FFmpeg TS→PCM, continuous PCM timing, reconnecting ASR event consumption, VAD/asr/approx timing, sentence-end wall-clock cues, error-window suppression, serial translation/context/fallback/degradation/recovery, lifecycle cleanup, cost and health stats. Focused tests passed.
- Integrated subtitle startup as a best-effort sidecar after playback starts, audio tee attach/detach ordering, `/api/subtitles` revision polling, subtitle status/cost in `/api/status`, and first-segment PDT diagnostics. Subtitle startup failure is recorded without failing playback.
- Added player overlay and controls: hls.js `playingDate` wall-clock mapping with fragment fallback, 500ms polling, 100ms rendering, bilingual/Chinese/source modes, size and ±3s offset persisted in localStorage, provider/target-language settings, and cost/provider telemetry.
- Updated README with the audio-to-cloud data boundary, secret handling, provider configuration, and ASR spike command. Updated `test:hls-companion` with the new suites while excluding the already documented environment-conflicting named-pipe test from the aggregate command.
- Final automated verification: aggregate HLS Companion suite passed (46 Python tests plus 4 web asset tests); Python bytecode compilation and Node syntax check passed. Workspace LSP still reports only pre-existing `core.py` escape warnings plus an intermittent package-resolution false positive for `subtitle_pipeline.py`; targeted file diagnostics are clean.
- Not yet verified: live DashScope event shape/idle timeout/workspace endpoint/hotword support (no key/audio), real 5-minute Japanese live subtitles, visual <1.5s alignment, and long-run memory/cost behavior.

## Model settings UI follow-up
- Replaced the cramped provider-only controls with a dedicated “模型设置” dialog in the player header.
- ASR is intentionally fixed to Alibaba Bailian `qwen3-asr-flash-realtime`; the UI configures its Realtime WebSocket endpoint and DashScope API key.
- Translation now exposes a generic OpenAI-compatible Chat Completions configuration: Base URL, model, API key, temperature, max tokens, timeout, and rolling context pairs.
- Added loopback-only `/api/model-settings` GET/POST. GET never returns secrets; POST persists entered keys to the private providers file through the existing atomic writer. Blank key fields preserve the current key.
- Corrected the generic OpenAI-compatible request shape: DashScope receives its vendor `extra_body`, while ordinary OpenAI-compatible endpoints receive standard Chat Completions JSON only.
- Verification: 10 provider tests, 3 model/provider API tests, and 4 web asset tests passed; targeted LSP diagnostics are clean.

## Cookie import UI (manual, no browser DB access)
- `--cookies-from-browser edge` failed with "Could not copy Chrome cookie database" because 128 Edge processes were holding the DB (known yt-dlp#7271). No cookie code was changed; diagnosis confirmed the auth path was untouched.
- Added user-facing manual import instead of touching the browser DB: `POST /api/auth-cookies` accepts a Netscape cookies.txt paste or a raw `name=value` header string plus domain, reuses `BrowserCookieSnapshot`-shaped dicts, filters to YouTube/Google/Bilibili domains, dedupes, and returns a one-shot `authToken` consumed by the existing probe/start flow.
- Cross-site hardening: loopback Host check plus same-origin Origin check; JSON body means browser cross-origin POSTs are also preflight-blocked. Cookies stay in memory only.
- New parsers `parse_netscape_cookies` / `parse_header_cookies` / `normalize_imported_cookies` in `core.py` (unit-tested), new player dialog "导入 Cookie" with format/domain/paste fields.
- Verification: 5 server tests (incl. 2 new import tests), 6 core tests, 4 web asset tests passed; py_compile and node --check clean.
- User note: the running server must be restarted to register the new route (HTML/JS hot-reloads from disk, Python routes do not).
- First real import attempt failed: DevTools copies the full cookie-table row (13+ tab columns); the multi-line parser took everything after the first tab as the value, so the generated Netscape file had 17-field lines and yt-dlp rejected every entry ("invalid length 17"), reproducing the bot check. Fixed: `parse_name_value_lines` now keeps only the first two tab fields (name, value) and drops trailing columns; `_netscape_row` and `normalize_imported_cookies` now also reject tab-containing values as defense in depth. Added a regression test that pastes a full DevTools row and asserts the emitted Netscape line has exactly 7 fields. 5 server + 6 core tests pass.
- Import now also reports accepted cookie names and flags missing login-critical cookies (SID/HSID/SSID/APISID/SAPISID/LOGIN_INFO/__Secure-*PSID family); the dialog shows the missing list in red. The user's earlier paste had only 7 non-critical cookies because the DevTools row selection was partial.

## Subtitle timing rework (sentence-span display + latency telemetry)
- User feedback: cues appeared only at the END of long passages, and vanished after a fixed hold instead of spanning the whole sentence; translation measured 3-5s.
- Player visibility window changed from `[tEnd, tEnd+hold]` to `[tStart, tEnd+hold]` (`cueStart` helper, falls back to tEnd when tStart is null). Viable because playback trails the live edge 5-7s while cues enter the store with original text ~1s after ASR final; long-sentence openings first show the source text with 翻译中… until the translation lands in place.
- Pipeline now records per-translation latency: `lastTranslationLatencyMs` plus a 0.7/0.3 rolling `avgTranslationLatencyMs` in `/api/status` subtitle stats; telemetry panel gained a 翻译延迟 row.
- Verification: 6 pipeline tests (incl. new latency stats test), 4 web asset tests, node --check all pass.

## Cookie snapshot persistence
- Root cause of the recurring bot check: imported snapshots lived only in server memory, so every Companion restart (needed to pick up Python-side changes) silently dropped the login.
- `handle_import_cookies` now also writes the normalized snapshot to `runtime/auth-snapshot.json` (atomic tmp+replace, 0600 on POSIX) next to providers.json; startup reloads it, and `_authentication` falls back to it whenever no in-memory token is supplied. Explicit tokens still win; browser fallback is now last.
- Import response gains `persisted`, and the dialog explains restart persistence / disk-failure degradation. Verification: new restart-cycle test (fresh CompanionApplication over the same providers dir authenticates from the saved file); 6 server tests pass.

## Session outcome
- User verdict: cookie import/persistence works, but subtitles remain broken overall (timing/duration/latency complaints persisted after the sentence-span rework). A debugging handoff was written for the next session: `docs/subtitle-debugging-handoff.md` (implementation map, symptoms, prioritized suspects S1–S7, evidence-first investigation order, iron rules).
- Honest open assumptions flagged there: `hls.playingDate` existence in hls.js 1.7.1 never verified in a browser (S1); ASR VAD event names/`completed` timestamp fields never tested live (S2) — the spike can now finally run since providers.json has a real DashScope key; structural pcm-vs-playback timeline drift measurable via `subtitles.epochDeltaSeconds` (S3).

## Productization spec/tickets session
- User requested planning only, using TDD plus `to-spec`/`to-tickets`, before distributing implementation to other models.
- Read the current provider, pipeline, player, cookie/auth, stop lifecycle, dependency, tests, and repository state; fixed the public TDD seams at loopback HTTP API, provider/pipeline interfaces, and browser-observable behavior.
- Researched official OpenAI-compatible transcription compatibility and yt-dlp Bilibili login behavior. Confirmed `/audio/transcriptions` as the local Whisper compatibility seam and `SESSDATA` as yt-dlp's Bilibili login cookie.
- Explore subagents could not start because Anthropic OAuth refresh returned 403; completed the bounded investigation directly without repeating the failed delegation path.
- Produced `.scratch/laglingo-productization/spec.md` and five minimal dependency-ordered tickets. Frontier tickets 01/02/03 can run in parallel; ticket 04 is blocked by 01; release ticket 05 is blocked by all feature tickets.
- No product implementation or GitHub repository creation was performed in this session.

## Productization implementation and private release
- Initialized Git only after excluding provider keys, Cookie/auth snapshots, control secrets, media captures, logs, agent state, caches, and approximately 2GB of runtime recordings. Created and pushed the private repository `https://github.com/Antiranger/LagLingo`.
- Ran five GPT-5.6 Sol / medium-thinking implementation agents in isolated worktrees, one per productization ticket. Tickets 01/02/03 ran in parallel; Ticket 04 followed Provider Catalog integration; Ticket 05 hardened release/bootstrap after feature integration.
- Independently rejected the first Ticket 01 result because the full suite failed and the player still referenced removed model-settings fields. A repair agent completed the Provider Catalog controller, local OpenAI-compatible transcription adapter, migration, documentation, and tests before integration.
- Integrated Ticket 03 platform Cookie/session cleanup and Ticket 02 subtitle floating-window/target-delay changes in a dedicated integration worktree, resolving only the shared server test conflict and preserving both behavior sets.
- Integrated Ticket 01 after repair, resolved shared package/player initialization conflicts, and restored the project task plan after an agent-local planning rewrite leaked into the first commit.
- Integrated Ticket 04 usage metering: ASR seconds, OpenAI-compatible prompt/cached/output token usage, fallback attribution, provider pricing, null-on-unknown estimates, and separate runtime costs.
- Integrated Ticket 05: Windows bootstrap, checksum verification, secret/release guard, current README, MIT/license/security/contribution/third-party docs, package lock, and Windows GitHub Actions CI.
- First remote CI failed because `YtDlpProbe` required a global PATH yt-dlp while the download path used the vendored binary. Added a red→green product regression and made probing prefer the vendored verified executable.
- Second remote CI exposed three server tests that accidentally depended on development-machine FFmpeg. Isolated those tests at their intended media-process seam and verified them under a PATH containing neither FFmpeg nor global yt-dlp.
- Final GitHub Actions Windows CI passed: `https://github.com/Antiranger/LagLingo/actions/runs/33430078496`.
- Final real Chromium smoke against a clean temporary Companion configuration verified Provider Catalog rendering and raw-key warning, and verified `document.fullscreenElement` is `.player-stage` containing `#subtitleLayer`.
- Repository remains PRIVATE. Final bootstrap command: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1` (requires Python 3.11+; this machine's default Python 3.10 is intentionally rejected, while installed Python 3.14 passes check-only validation).
