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

## Soniox realtime ASR support
- Researched the current official Soniox WebSocket API and official examples: `stt-rt-v5`, binary 16 kHz PCM, token-level finality/timestamps/language, `<end>`/`<fin>` boundaries, lowercase finalize/keepalive controls, and empty-frame graceful stop.
- Added the native aiohttp `soniox-realtime` adapter, built-in/example catalog preset, Provider Catalog UI kind, capability metadata, protocol regression tests, aggregate test wiring, and README configuration notes.
- Verification: 4 Soniox fake-WebSocket tests, 17 provider tests, 7 web-asset tests, Python compile/JS syntax checks, and the complete `npm run test:hls-companion` suite all passed. Targeted LSP diagnostics for both new files are clean. Live Soniox API acceptance remains pending a user API key.
- Live Soniox timing diagnosis: `/api/subtitles` showed all 37 retained cues had `tStart == tEnd` despite `timingSource=asr`; playback/ASR health had zero drops and zero reconnects. A red-capable adapter replay proved a `<end>` control token carrying `end_ms=0` overrode the final lexical token's real `end_ms=700`, producing a negative span that the generic pipeline clamped to zero. Fixed `_finish_utterance` to use lexical token timing as authoritative and consult the boundary token only when no lexical end exists. The focused Soniox suite and all 35 subtitle pipeline tests pass; running Companion must be restarted for the Python adapter fix to take effect.

## Realtime STT provider protocol audit and expansion
- Completed two serial tickets. Ticket 01 produced the current official protocol audit for 11 table entries and corrected stale Soniox/OpenAI/AssemblyAI/Deepgram assumptions. Ticket 02 implemented audited changes only.
- Existing adapters revised: DashScope Qwen-Audio-3.0 preset/candidates/vocabulary/context; Soniox speaker/confidence and bounded tail-final drain; Deepgram diarize/utterance-end options; OpenAI current transcription session retained.
- Added independent native adapters and fake-protocol suites for AssemblyAI Streaming v3, Volcengine SAUC v3 binary framing, ElevenLabs Scribe v2 Realtime, Speechmatics Realtime v2, and Tencent Cloud signed realtime ASR. Gemini 3.5 Transcribe Live and Mistral Voxtral Realtime remain documented defer items rather than false UI options.
- Extended backend ASR/Cue metadata with optional speaker/confidence and speaker-revision events; no speaker UI was added. Provider Catalog gained provider-specific configuration fields and example profiles. Added `docs/realtime-stt-provider-api-modification-plan.md` and official protocol notices.
- GLM 5.3 Flash Ticket 02 Agent hit its provider 5-hour limit during verification; the main agent inspected the actual changes, fixed incomplete registration/UI/docs/lifecycle/test integration, and ran the full `npm run ci` successfully. Targeted LSP diagnostics are clean for the registry and all seven new/revised realtime adapter files. Cloud live spikes remain pending credentials.

## Live subtitle timeline, live-message and wall-clock planning
- User fixed the scope to ongoing YouTube/Bilibili livestreams only: left bilingual subtitle timeline, center video with real wall-clock controls, right live-message timeline, plus an independent live-message translation toggle targeting the persisted Target Language.
- Inspected the current MediaAnchor/CueStore/server/player/fullscreen/layout/provider/auth lifecycle and prior subtitle timing redesign; established one HLS PDT Media Wall Clock as the sole alignment contract.
- Produced `.scratch/laglingo-live-timelines/spec.md`; after user feedback, consolidated the original six small tickets into three subagent-sized vertical work packages: complete `web-player/` frontend, complete `companion/` live-message backend, then integration/hardening/live acceptance.
- Ran an independent fresh-agent review and corrected the plan for session-scoped Cookie leases, YouTube initial-history filtering, runtime translation settings, atomic capture cursor snapshots, subtitle readiness/offset semantics, Store reset/generation cleanup, server-level translation factory/metering, terminology, file-ownership boundaries, wide-screen breakout, and upcoming/post-live handling.
- Updated `CONTEXT.md` with Media Wall Clock, Playback Wall Time, Capture Media Cursor, Subtitle Cue, Live Message, Live Message Source, and Follow Mode terminology. No product implementation was performed.

## Bilibili / Twitch live media implementation
- Read `.scratch/bilibili-twitch-live-support/spec.md` and Tickets 01/02 completely.
- Confirmed the mandated seams already exist: `build_quality_options`/`select_quality`/`selected_inputs`, `YtDlpLiveIngest` selector splitting, single-Pump `_audio_pump()`, packaging FFmpeg muxed mapping, construction-time subtitle tee, auth lease, and explicit live-message sources.
- Current red causes: BiliLive formats without height/acodec are discarded; Twitch URL/cookie/platform routing is absent; probe currently permits upcoming streams; message routing treats every non-YouTube platform as Bilibili.
- Began Phase 9. Added Ticket 01 red tests at the existing public seams. `test_core.py` was red exactly for the intended gaps: BiliLive null metadata yielded no qualities, CDN/codec normalization was absent, Twitch channel URL was rejected, and Twitch Source lost to a lower rendition. Existing `test_ytdlp_ingest.py` remained green, confirming the single-selector/single-Pump seam already worked before production changes.
- Implemented narrow platform handling without new policy abstractions: strict live-room/channel URL boundaries, BiliLive/Twitch muxed quality normalization, CDN mirror dedupe, AVC HLS preference, Twitch Source ordering, ongoing-live-only probe semantics, Twitch cookies, and explicit Twitch unsupported message state.
- Reused `YtDlpLiveIngest` unchanged at its lifecycle boundary. YouTube remains dual selector/two Pump; Bilibili/Twitch use one selector/one Pump. Added only a selected-protocol flag so BiliLive FLV fallback does not incorrectly force the HLS downloader.
- Implemented the BiliLive fMP4 narrow fix in `ProbeInfoSnapshot`: only the selected trusted BiliLive HLS format's temporary `ext` is changed to `mp4`; yt-dlp still uses `--hls-use-mpegts`, unsafe-extension checks remain enabled, and non-BiliLive/non-HLS metadata is unchanged.
- Deterministic verification passed: full `npm run test:hls-companion`, Python compile, Node syntax, scoped tests, and final full `npm run ci`.
- Ticket 02 real checks: Bilibili room 6 and Twitch channels `twitchplayspokemon`/`eslcs` reached local production fMP4 HLS with PDT through a single ingest leg and cleaned to idle/not-running on Stop. A second live Bilibili room (`21452505`) verified AVC/HEVC + HLS/FLV/CDN selection. User room `22637261` returned no formats at test time. Full 10-minute playback, subtitle cloud PCM, Twitch ad discontinuity, Bilibili danmaku, and real YouTube regression were not run and are recorded honestly in `.scratch/bilibili-twitch-live-support/acceptance-results.md`.

## Live Twitch subtitle diagnosis (`hello_kiko`)
- Read the running Companion's `/api/status` and `/api/subtitles` directly. Media/PCM alignment is healthy: no tee/PCM drops, no ASR reconnects, MediaAnchor frozen with 0.1s spread and 0.016s drift.
- Built and ran a red-capable live diagnostic: long cues exceed the 15s target while 38 failed translations are under 5s; translation queue is empty, no cues were dropped, all four workers remain alive, and the terminal error is `TimeoutError`.
- Timing root cause: a cue is not displayable until translation succeeds or fails. A final-only utterance needs `utterance duration + post-end readiness lag <= playback delay` to be visible at its true start. Observed cue durations reached 33.9s; ready lag was p50 3.873s / p95 9.76s, so 15s cannot cover several long utterances.
- Existing Soniox prefix splitting is only punctuation-driven. It emitted 40 prefix cues, but 62 overlong cues remained; without an early stable strong boundary, a long utterance still waits for the endpoint/final. `maxUtteranceSeconds` is configured as 0 and forced commits are 0.
- Translation root cause is separate: 49/127 attempts timed out. Failures include 38 cues shorter than 5s, proving sentence length/delay is not the cause. The UI's ~2s average measures successful calls only. Current 8045 endpoint passed fresh sequential and 4-way concurrent probes (1.3–3.6s), so the earlier timeout burst is intermittent gateway/upstream latency or availability, not sustained queue overload.

## Streaming caption segmentation research
- Researched papers, standards, official vendor contracts and open-source implementations; an independent researcher saved the cited note at `docs/research/streaming-caption-segmentation.md` and a second reviewer challenged the proposed thresholds/state machine.
- Converged recommendation: keep Soniox semantic endpointing for natural turn ends, but add an application-owned Caption Chunker over immutable final lexical tokens. Do not wait for `<end>/<fin>` and do not normally call `finalize` on a timer.
- Evidence base includes Soniox final/non-final token guarantees and endpoint/manual-finalization docs; AWS/Azure stable partials; Speechmatics max-delay; Deepgram VAD/word-gap limits; Whisper-Streaming LocalAgreement-2 and 15s buffer trimming; streaming ST local-agreement/hold-n papers; punctuation+acoustic segmentation research; and Netflix/DCMP/BBC caption block constraints.
- For the currently measured 15s delay and 9.76s p95 boundary-to-ready lag, the research note recommends a conservative 3s preferred / 4s hard source chunk initially, relaxing toward 6s only after measured downstream readiness improves. The independent reviewer recommends the same architecture but notes 4.5–6.5s is a reasonable product target once translation readiness is fixed.

## Provider-neutral 6-second Caption Chunking plan
- User fixed the product target to 6 seconds and required the solution to cover every ASR Provider rather than Soniox only.
- Audited current Adapter capabilities and designed one Provider-neutral Recognition Evidence seam: immutable token deltas, token snapshots, stable-prefix snapshots, mutable text snapshots, utterance finals and endpoints all feed one `CaptionChunker`; `advance_audio()` supplies the 6-second clock even when ASR emits no event.
- Distinguished Provider Stable Tokens from LocalAgreement Policy-Committed Tokens and strict guarantee paths from best-effort paths. Exact/timestamped tokens, <=6s audio windows and verified safe commits can guarantee the cap; untimestamped/no-commit Providers report evidence-limited overshoot rather than fabricate timing.
- Defined three internal language policies (whitespace, CJK, universal fallback), deterministic candidate ranking, source-order translation context, mid-sentence metadata and a stronger continuity prompt.
- Wrote `.scratch/provider-neutral-caption-chunking/spec.md` plus three dependency-ordered tickets. A fresh reader review initially found observation/timer/guarantee/ordering/lifecycle ambiguities; revised all artifacts and reran review. Final reader verdict: ready, no remaining P0/P1 blockers.

## Provider-neutral Caption Chunking implementation — Ticket 01
- Added normalized `RecognitionToken`, discriminated `CaptionObservation`, capability observation-kind sets, and backward-compatible `ASREvent.caption_observation`.
- Added pure `companion/caption_chunker.py`: immutable deltas, snapshot LocalAgreement-2, stable-prefix/text/final reconciliation, deterministic punctuation/gap/language/hard-deadline boundaries, `advance_audio()`, strict/best-effort telemetry, and idempotent lifecycle behavior.
- Normalized all 11 current ASR Adapters conservatively. Soniox emits exactly-once stable lexical deltas; Qwen emits stable-prefix snapshots; Deepgram/AssemblyAI use proven token snapshots; unproven word-wire paths remain text/final rather than inventing contracts. OpenAI transcription windows now reject invalid or >6s configuration.
- Added contract/chunker and focused Adapter tests. Main-agent spot verification passed recognition (1), chunker (9), and Soniox (9) suites; the implementation agent also ran all focused Adapter/provider suites and py_compile/diff-check successfully. Two verification command-shape mistakes were logged in `task_plan.md`; they were harness invocation errors, not product failures.

## Provider-neutral Caption Chunking implementation — Ticket 02
- Integrated the shared Chunker into `SubtitlePipeline`, including Provider-session-to-pipeline timestamp mapping, chunk materialization through existing MediaAnchor/CueStore/translation queues, and legacy-path suppression/fallback.
- PCM sending now calls `advance_audio()` after every successful push, so six-second decisions work during ASR event silence. Manual commit is capability-gated, cooldown-controlled, session-disabled after rejection, and never used when local evidence can be cut.
- Added generation-local monotonic `chunk_order`, continuation/cut metadata propagation, residual/idempotent lifecycle handling, reconnect isolation, and complete chunk telemetry in subtitle status.
- Fixed a discovered chunker edge so a hard cut resets the next deadline budget to the residual lexical boundary rather than the original utterance start.
- Verification passed: CaptionChunker 9 tests, SubtitlePipeline 46 tests, server/provider 22 tests, targeted syntax diagnostics clean, and scoped diff-check clean.

## Provider-neutral Caption Chunking implementation — Ticket 03 and final review
- Made `RollingContext` generation/chunk-order/media-time aware; parallel completion is reordered by source, CURRENT/future/live-message entries are excluded, duplicate orders reject, and missing immediate predecessors do not serialize workers.
- Strengthened generic LLM continuity Prompt and passed unknown-aware `starts_mid_sentence/ends_mid_sentence/cut_reason`; Qwen-MT receives the same chronological history through supported `tm_list` only.
- Made Cue source/timing/order/cut metadata immutable with one terminal translation outcome; added source-ready, translation-success-ready and terminal-outcome latency telemetry plus English/Spanish/Chinese/Japanese/Korean/mixed deterministic fixtures.
- Independent reviewer reported no P0/P1 issues, but direct hybrid-evidence replay exposed a real duplicate (`hello hello again`) when a snapshot-promoted token later arrived as a stable delta. Added a red regression and deduped timestamped tokens across evidence kinds.
- Direct Deepgram replay exposed a second contract bug: `is_final=true, speech_final=false` was incorrectly mapped as an utterance final and closed the chunker item. Remapped Deepgram finalized intervals to `stable_token_delta`; only `speech_final`/`UtteranceEnd` closes the utterance, with new protocol regression coverage.
- Propagated observation language/speaker into text-only Chunker units so CJK safe-prefix policy and Cue language do not fall back to unknown.
- Final verification passed: complete `npm run ci`, production-routing Playwright browser smoke, CaptionChunker 13 tests, Deepgram 17 tests, SubtitlePipeline 50 tests, targeted LSP diagnostics clean, and scoped `git diff --check` clean except line-ending notices.
- Real-live multilingual/provider matrix remains blocked/not claimed as recorded in `.scratch/provider-neutral-caption-chunking/ticket03-acceptance-results.md`.

## Live Soniox spacing/micro-chunk root-cause correction
- Inspected the retained localhost live Cue set: 68/104 Cues contained obvious Latin word-internal spaces and 20/104 were <=1.2s. Active ASR was Soniox `stt-rt-v5`; representative source included `Ch at, list en to m e.` and 0.18s/0.30s `hard_deadline` Cues.
- Built a deterministic failing replay from Soniox tokenizer pieces. Confirmed the Adapter exposed subword pieces as `RecognitionToken`, while the shared Chunker correctly assumed every token was a cut-safe lexical unit; endpoint then published the malformed text before authoritative final reconciliation.
- Fixed the owning boundary rather than adding text regexes or UI/translation patches: Soniox now merges whitespace-delimited tokenizer pieces into lexical units, holds back the final open lexical unit across WebSocket messages, keeps CJK token-granular, and emits exactly-once lexical deltas plus final residual tokens.
- Soniox `speech_stopped` now supplies VAD timing/lifecycle only; authoritative `utterance_final` owns final reconciliation/publication, preventing premature immutable Cue publication and duplicate final tails.
- CaptionChunker now distinguishes audio deadline from sufficient committable evidence: subminimum lexical evidence is not emitted as a LagLingo-initiated hard-deadline Cue; safe manual commit is requested or pending evidence is reported, while real short endpoint/final utterances remain publishable.
- Added Soniox subword/cross-message tests, a Chunker micro-hard-cut regression, and a Pipeline stop-before-final integration test. Updated the recognition contract/spec to require cut-safe lexical units.
- Verification passed: Soniox 11 tests, CaptionChunker 14 tests, SubtitlePipeline 51 tests, full `npm run ci`, production browser smoke, targeted LSP diagnostics, and scoped `git diff --check`.
- A fresh real Soniox live run is still required before Phase 18 can be marked complete; historical Cue rows remain intentionally immutable and will continue showing the old defect until the Companion is restarted and a new session is recorded.

## Full-pipeline latency and alignment-recovery experiment design
- Traced current timing ownership across yt-dlp/Pump, packaging publisher/CaptureClock, TS→PCM, Provider events, CaptionChunker, MediaAnchor, CueStore, translation workers, HTTP polling, browser MediaClock and scheduler.
- Confirmed readiness latency and timestamp alignment are separate: one late translation does not shift later Cue timestamps. However, MediaAnchor freezes its startup offset C after 20 samples and only reports later drift, while CaptureClock keeps the earliest PDT; a bad startup estimate or true media discontinuity can leave all later Cues at a persistent fixed offset until restart.
- Initially designed a broader trace/epoch system, then reduced it after user review to the true minimum: six monotonic stage markers inside `SubtitlePipeline`, 60-sample percentile windows, existing MediaAnchor drift sampling, focused fault injection, and a 10-minute `ironmouse` baseline. Browser tracing and automatic alignment epochs are explicitly deferred until measurements prove they are needed.
- Independent Plan subagent could not run because the provider returned usage-limit/auth-unavailable; completed the evidence-based design directly from the code and recorded the failure rather than retrying the exhausted route.
- Implemented the first minimal-observability slice in `subtitle_pipeline.py`: successful ASR PCM push frontiers, normalized evidence frontiers, Chunk emission, translation worker start, Provider terminal time (including failure/timeout), and Cue terminal update now yield six bounded 60-sample stage distributions without storing source text in telemetry.
- `/api/status` subtitle stats now expose `asrAdapterDelay`, `chunkerPolicyDelay`, `translationQueueDelay`, `translationProviderDelay`, `storeUpdateDelay`, and `totalReadyDelay` P50/P95 plus completed/unknown sample counts. Existing aggregate readiness fields remain backward compatible.
- MediaAnchor now retains only 60 drift numbers and exposes drift P50/P95/max-absolute/sample count; no automatic correction or alignment framework was added.
- Added three focused regressions covering exact stage decomposition, incomplete/unknown samples, sensitive-text absence from status, and a persistent +2s packaging-counter jump in anchor drift.
- Verification: `tests/test_subtitle_pipeline.py` 54/54 passed; full `npm run ci` passed; LSP diagnostics clean for implementation and tests; scoped `git diff --check` clean except existing LF/CRLF conversion notices.
- Continued with the smallest next measured-risk fix while the real baseline runs: Soniox lexical aggregation now treats either previous-piece trailing whitespace or current-piece leading whitespace as a word boundary. Added focused `"Hello " + "world"` and `"you " + "and " + "I"` regressions; Soniox + Pipeline focused suite passes 67/67 and LSP diagnostics are clean.
- Started a fresh Companion and a real Twitch `ironmouse` session at 720p60, 15s target delay, Soniox subtitles and translation enabled. A bounded sampler is recording one redacted `/api/status` snapshot per minute for 10 minutes to `.scratch/live-pipeline-latency-observability/ironmouse-status-samples.json`; analysis is deferred until the sampler completes rather than polling it.

## Ticket 02 resume — SubtitlePipeline CaptionChunk integration
- Re-inspected the disconnected run's actual diff and files before editing. Ticket 01's normalized observations/chunker and five new pipeline tests were present, but `SubtitlePipeline` had no chunker integration methods/imports; focused pipeline tests failed with 3 failures and 1 missing-method error exactly at the new Ticket 02 seams.
- Integrated one generation-local `CaptionChunker` into `SubtitlePipeline`: Provider-session observations are mapped through `_server_to_pipeline`, PCM sends drive `advance_audio()`, decisions materialize through the existing `_PendingFinal` → MediaAnchor → CueStore → translation queue path, and normalized events suppress the overlapping legacy prefix/final path while malformed/unmigrated events retain final-only fallback.
- Added VAD-open timer registration, manual commit orchestration/degradation, 4-second chunker cooldown, redundant legacy timer suppression, stable local-cut budget reset, idempotent session flush/reset, queue/pending cleanup on Stop, generation-local monotonic `chunk_order`, continuation/cut metadata propagation into `TranslationRequest`, and the complete Ticket 02 chunk telemetry surface.
- Fixed a resumed Ticket 01 edge discovered by integration: after a local hard cut, the next deadline budget now starts at the residual lexical start/emitted boundary; after commit rejection, the same frontier can degrade to pending-evidence telemetry rather than staying suppressed.
- Expanded focused integration coverage for long exact-token utterances, silent PCM timer commit, commit failure, stable-prefix timing/legacy suppression, idempotent flush/restart, malformed timestamp fallback, legacy max-utterance timer suppression, and translation metadata propagation.
- Focused verification passed: recognition contract (1), CaptionChunker (9), SubtitlePipeline (46), server/provider API (22); targeted LSP diagnostics clean; scoped diff check clean at that checkpoint.

## Streaming caption segmentation research
- Completed a primary-source review of semantic versus acoustic endpointing, stable/committed prefixes, time/word/width caps, punctuation restoration, incremental translation revisions, word-timestamp scheduling, forced-finalization tradeoffs, and live caption standards.
- Mapped Soniox's immutable final-token/timestamp contract and LagLingo's measured 15s budget to a concrete two-layer policy: natural Soniox endpointing plus local committed-token caption cuts; initial 3s preferred/4s hard span, adaptive up to 6s only when p95 readiness permits; finalization only for stalled non-final tails.
- Wrote `docs/research/streaming-caption-segmentation.md` with 34 cited official/paper/source references, algorithms, thresholds, telemetry, limitations, and source links. No product code was changed.
- Verification: inspected the completed Markdown (390+ lines, 43 source URLs), checked for unresolved placeholders, and reviewed Git status/diff. External arXiv API searches failed at the network layer; canonical arXiv/ACL/ISCA paper pages/PDFs were fetched through the web gateway instead.

## Provider-neutral Caption Chunking implementation — Ticket 03
- Replaced completion-time RollingContext with generation/chunk-order/media-time context. Successful translations may be added out of order, duplicate `(generation, chunk_order)` is rejected, and history queries exclude current/future/media-future/other-generation pairs.
- Wired SubtitlePipeline translation requests to query at `cue.t_end`, retain four-worker concurrency, count missing immediate predecessors, and add only successful Caption Chunk translations. Metadata-free live-message calls never enter subtitle history.
- Strengthened the shared generic LLM prompt: chronological previous context, CURRENT-only output, no repeat/summary/future invention, natural unfinished clauses, continuity of subject/reference/tense/register/names/terminology, and unknown-aware position/cut metadata. Qwen-MT remains structured and chronological through `tm_list` only.
- Centralized `CaptionCutReason` in the provider contract and fixed first/residual chunk starts-mid-sentence propagation. CueStore now freezes source/timing/order/cut metadata and permits one terminal translation outcome only; no-translation mode publishes terminal source-only `failed` cues.
- Added deterministic English, Spanish, Chinese, Japanese, Korean, mixed/unknown/URL/decimal/proper-name/emoji and completion-order acceptance fixtures, plus the blocked real-live acceptance matrix.
- Added separate `sourceReadyLagP50/P95`, `translationSuccessReadyLagP50/P95`, and `terminalOutcomeLagP50/P95`; legacy `readyLagP50/P95` remains a terminal-outcome alias so failures never inflate successful-translation readiness.
- Verification passed: focused Ticket 03 suites, complete `npm run test:hls-companion`, complete `npm run ci` (rerun after final telemetry changes), Python compile, Node syntax, scoped production-file LSP diagnostics, and `git diff --check` (line-ending warnings only). Workspace-wide LSP still reports unrelated/pre-existing optional-access diagnostics in existing test files; Ticket 03 production files are clean.

## 2026-09-09 handoff takeover session
- Restored context from laglingo-handoff-2026-09-09.md; companion alive (PID 39736), session running but wall−pdt−priv=+2582s (P1 chronic lag ongoing).
- P2 CLOSED: analyzed completed run4 (674 samples/30min) + watch4 + run4-gpu.csv. Conclusions written to docs/perf-mouse-lag-analysis-2026-09-09.md: 1080p60 was software-decoded (all GPU decode engines 0.0 on both GPUs), corr(browser cpu, dwm)=0.73, sys CPU not exhausted, run3's 11.7GB browser RSS was an aggregation artifact not a player leak, stall overlay worked live (2 stalls, banner+auto-pause+resume aligned monitor/watch timelines).
- P1 code: new web-player/quality-preference.js (pickPreferredQuality: ≤1080p, height desc, separate v+a before muxed within tier) wired into player.js probe + index.html + server.py route; 7 node tests.
- P1 observability: ytdlp_ingest snapshot() now exposes legThroughput (per-leg forwardedBytes + bytesPerSecond between polls, zero-elapsed clock guarded) and redacted logTail (last 8 lines); _TcpPump counts forwarded_bytes. 1 new python test (fake clock, deterministic).
- P4: stall-sim error branch — fake_server /control?error=1 + 2 new checks (error banner appears, hides after clear); closed-loop 10/10 PASS. Soniox adapter maps maxNonFinalTokensDurationMs → max_non_final_tokens_duration_ms (name verified against official WebSocket API docs); test_endpoint_tuning_options_map_to_official_wire_names added, 17/17 OK.
- Full suite: npm run test:hls-companion all green (python files all OK; node 62/62).
- NOT done: P3 (needs user decision C vs B), companion restart (kills live session), P4 diarization toggle (user pref), asrReconnects startup diagnosis (needs fresh instrumented session).

## 2026-09-09 P3-B implementation (audio-leg root fix)
- Design: docs/subtitle-audio-leg-design-2026-09-09.md (continuous MediaAnchor C=V−A, rate-gated 2.5s cadence, rolling median, jump-reset on video skips).
- New: companion/media_anchor.py + tests/test_media_anchor.py (9 tests incl. segment-quantization regression from live finding).
- subtitle_pipeline: start(audio_url, media_epoch, input_format), _anchor_sampler task, anchor-gated cue materialization (hold until ready, requeue on reseed), status exposes captionSource/mediaAnchor/pendingFinals. 5 golden integration tests in test_subtitle_pipeline.py (71 total).
- server.py: _asr_audio_leg_selector (self-resolving "234/233/ba[protocol^=m3u8]/worst[protocol^=m3u8]", None for bilibili/twitch), second YtDlpLiveIngest with own auth consumer, late start, status sourceIngest gains role=asr-audio entry. Selector tests in test_server_providers.py (30 total).
- Live debugging findings: (1) probe formats fluctuate between extractions — 233/234 transiently absent; (2) 1s anchor sampling starved by 1s segment quantization; (3) Windows bg_start PID is a shell wrapper — kill the LISTENING pid from netstat, not the wrapper.
- Live acceptance (iwhhsBMAIZQ 720p60-muxed + audio leg): anchor ready after ~13s, cues map within <2s of packaged edge, subs survive independently of video leg. Full suite green (python all OK, node 62/62).
- Session left RUNNING on companion10 (port 8765) for user acceptance at http://127.0.0.1:8765/

## 2026-09-09 P1 root cause found & fixed (proxy'd ffmpeg) + P3-B live acceptance
- ROOT CAUSE P1: yt-dlp hard-delegates is_live HLS to external ffmpeg FD (HlsFD.can_download refuses is_live) — --downloader m3u8:native/--concurrent-fragments are no-ops for live. The real ceiling was ffmpeg HLS demuxer THROUGH THE PROXY (broken connection reuse): 0.55x@1080p60 / 0.8x@720p60. Direct googlevideo sustains 1.0x@1080p60 (measured 20.8MB/30s).
- Fix: ytdlp_ingest command() no longer forwards proxy to ffmpeg downloader by default (LAGLINGO_FFMPEG_PROXY=1 opt-in restores); added -reconnect hardening args. Tests updated (3 changed/new in test_ytdlp_ingest.py, 15 total OK). Full suite green.
- Live acceptance on companion11 (iwhhsBMAIZQ 1080p60-muxed-301 + audio leg 234, ja→zh-Hans): first 6 min wall−pdt−priv pinned at −3s (was +2582s pre-fix). Network blips at ~9-16min caused video skips (~35s) and one live 7.9s stall — subtitles sailed through ALL of it (pcmDropped=0, asrReconnects=0, pendingFinals=0), anchor jump-reset re-converged each time (offset 13.3→7.9→5.7→4.2, spread ~1.5).
- Residual: video leg averages ~0.95x on 1080p60 over 21min (+50s accumulated) — direct connection has no catch-up margin at 5.5Mbps; 720p has margin. YouTube live manifest flaps between muxed-only and separate-only renditions between extractions (observed 3 flips) — quality list varies per probe.
- Session RUNNING on port 8765 for user visual acceptance (sync slider for final calibration; anchor spread ~1.5s).

## 2026-09-09 late session: parallel subtitle startup + proxy exoneration
- User concern: subtitle startup latency. Root cause: serial chain (private-HLS wait → audio-leg re-extraction → anchor convergence). Fix: parallelized — audio leg + caption pipeline start immediately (epoch=None), set_media_epoch() late-binds when first private segment lands. Test: test_epoch_late_binding_releases_held_finals_with_anchor_mapping. All suites green (72 pipeline tests).
- Live verify on Weather News (674lr89Qfj4, 1080p muxed 96): pipeline consuming audio at t+13s, first cues ~t+60-65s from probe (~35s from start).
- CORRECTION to earlier P1 conclusion: "Cannot reuse HTTP connection for different host" persists in DIRECT mode — ffmpeg 9.0 HLS demuxer does not reuse connections across googlevideo's per-segment host rotation. Proxy was not the culprit (or not the only one). ffmpeg demuxer tops out ~0.8x at 1080p under evening load; audio leg (130kbps) holds 1.01x. Media-leg root fix = custom concurrent HLS downloader or yt-dlp patch for is_live native — queued as P5.
- First stream (iwhhsBMAIZQ) ended mid-session; acceptance moved to Weather News live.
