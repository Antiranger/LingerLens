# Release hardening execution — 2026-09-23

Worktree: `F:/Projects/LagLingo-hardening-20260923`. Branch: `hardening/pre-release-20260923`.
Original worktree is preserved. Baseline `3b5a54c` contains pre-existing source/docs only; no runtime credentials or installers.

## Phase 1 — contracts and configuration

- Registered provider classes own the ASR/MT role; all 15 built-in records validate.
- ElevenLabs: unique session-local commit identities, bounded one-use metadata-copy matching, VAD defaults and URL encoding. Explicit manual mode is blocked at production preflight until a commit controller is available.
- Tencent: resolve AppID in the URL before signing.
- Native translations: arrival-order prefix pairs are diagnostic only. Consume only completed whole source/translation segments; early local cuts stay source-only.
- OpenAI Live: `turn_detection=null`; disabled at production preflight until client VAD and commit timing are implemented and validated.
- Provider readiness is distinct from language capability. Saved settings are reported explicitly as next-start configuration, with model identities, mode, timing and configuration path, never credentials.
- i18n excludes transcript, chat and live-title content. Legacy cross-provider credential inheritance is restricted to DashScope.

New regression suite first reproduced 5 failures and 1 error on the baseline, then all six passed. Selected inherited tests (native ledger, ElevenLabs, Tencent, OpenAI) passed. Full regression pending later phases.

### Primary protocol references checked during implementation
- https://developers.openai.com/api/docs/guides/realtime-transcription
- https://elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime
- https://cloud.tencent.com/document/product/1093/131127

## Remaining execution
Phase 2: timestamp probes, bounded muxing, source-clock failures and long-lived ledgers.
Phase 3: move log writes off the Electron main thread; bounded diagnostics and rendering fallback controls.
Phase 4: clean-checkout import guard, replay/acceptance tooling, full regression and evidence-based release status.

## Phase 2 — media clocks and bounded ownership

PTS parsing now accepts PTS+DTS, rejects malformed markers and metadata PIDs, handles adaptation-only discontinuities, and compacts the read buffer once per feed. Source-clock failures reach the existing whole-session supervisor. Successful-but-immediately-failing restarts no longer renew the retry budget; three attempts span one manual viewing session.

The packager preserves both PTS and DTS rather than independently deleting track gaps. Independent local inputs are synchronized to input 0 (`-isync 0`). Interleave tolerance is finite (1 second), with explicit mux-queue thresholds. These bounds do not guarantee end-to-end latency.

A real local FFmpeg 9.0 test uses two real TCP pumps, B-frame H.264 and AAC with independent origins, and a two-second video-only gap. The first attempted duration-only filter caused an 80 ms residual: FFmpeg setts defaults also affected timestamp identity. Explicit `pts=PTS:dts=DTS` fixed it without relaxing the test. The observed input/output origin difference is 0.678667 s; the video gap remains and the audio stays continuous. This is an offline media-contract test, not live-network or screen-level latency certification.

Long-lived AssemblyAI/Speechmatics/Tencent ledgers are bounded; Volcano full-history tombstones retire a numeric frontier. Soniox refuses an unended utterance beyond 8192 tokens instead of unlimited retention. AssemblyAI onset fallback converts milliseconds to seconds; continuous partials are requested explicitly; Speechmatics EndOfUtterance is no longer ignored. ASR sends have a five-second deadline. Close/drain remains owned by each adapter and application teardown; cancelling a close midway would risk orphaning its ClientSession.

Selected real-media, clock, recovery, provider and pipeline tests passed. Remaining: real platform discontinuities and stop/reconnect soak; exact ASR-vs-player offsets with B-frame composition offsets; common-audio fan-out architecture is not implemented by this patch.

Primary media reference: https://ffmpeg.org/ffmpeg.html#Advanced-options ; https://ffmpeg.org/ffmpeg-formats.html

## Phase 3 — responsive diagnostics

Electron file logging now uses one serialized asynchronous filesystem queue, including initialization and rotation. There are no synchronous file operations on this logging path. File-mode output is not echoed to potentially blocking redirected stderr. Explicit terminal logging remains separate. Queued and in-flight data share a 1 MiB / 128-operation bound; oversized chunks are rejected and counted. Each log is capped at 8 MiB plus three backups. These bounds apply per armed file, not to all historical sessions combined.

`write`/`writeLines` return queue acceptance, not durability. Diagnostics exposes pending, queued, dropped and persisted counts; callers can await a bounded flush. Already accepted records retain their destination across disarm/rearm. Normal exit attempts a two-second drain; forced process termination or a timed-out disk write may still lose records.

A five-second opt-in main-loop summary records timer lateness, process CPU time, RSS and log queue/drop counters alongside existing renderer playback summaries. The ordinary Chromium path is the default; ignore-gpu-blocklist/rasterization overrides now require `LINGERLENS_GPU_EXPERIMENTAL=1`. Explicit software fallback remains available.

30 desktop/logging/probe tests pass, including injected stuck asynchronous I/O, event-loop liveness, queue overflow, file rotation, destination isolation, failure reporting and flush deadlines. The original whole-system freeze was not reproduced and is not declared fixed.

Primary reference: https://www.electronjs.org/docs/latest/tutorial/performance ; https://nodejs.org/api/fs.html

## Phase 4 — acceptance and reproducibility gates

Added an AST/Git-index guard for every runtime Python module and local import. A disposable Git repository test demonstrates that even a git-ignored runtime import fails, then passes only after the dependency is tracked. Added the guard to `npm run ci`.

Ten actual adapters now replay the same three utterances (including an intentional repeated sentence) through the real SubtitlePipeline and CueStore. All ten local integration cases pass.

A duration-capped cloud fixture tool is available in `scripts/provider-live-acceptance.py`. It requires explicit paid-test consent, refuses evidence overwrite, stores no transcript by default, and labels its measurement as fixture-to-ASR events, not video-screen latency. No paid provider request was made in this implementation session. The acceptance/fault/soak protocol is in `docs/release-hardening-acceptance.md`.

Test-only Playwright 1.56.0 and its dependencies were installed under this worktree's ignored `.scratch/hardening-20260923/browser-deps`, not into the original development environment. Both actual-browser suites pass against a fresh headless Chrome profile (metrics: 4.2 s; smoke: 22.4 s). Node frontend: 228 passing; root suite: 41 passing.

The first detached Python full run showed unusual child-process startup delays and one timeout. Its processes were stopped; an isolated replay passed all 54 caption cases in 3.7 s. A fresh serialized full run is required before closing the gate; no root cause for the detached-run delay is asserted.

### Full-suite follow-up
The first serialized full Python run executed 854 counted tests across 74 files: 72 files passed, one optional Streamlink file was skipped, and the provider selection test failed because its saved fake ASR profile had no credential. Production now correctly rejects missing DashScope credentials before startup. The fixture supplies a clearly fake credential; a new test explicitly verifies that missing keys for both DashScope task/realtime profiles fail before pipeline start. All 51 server provider tests pass. No production validation was relaxed.

GitHub CI now invokes the Python import guard directly (it uses individual npm commands rather than the aggregate `ci` script), and installs a pinned test-only Playwright plus Chromium so actual browser tests cannot silently skip for a missing dependency. Runtime and installer requirements remain unchanged. Optional Streamlink is not bundled and is reported separately.

### Stop-control race found by clean checkout
The first clean-checkout `npm run ci` passed 855 counted Python tests except the script-style browser smoke, which exposed a real Stop control race. A status request started after Stop has the current UI generation but may still describe the old running session. `refreshStatus` updated `lastSessionState` and controls before checking the local stop barrier. Moving both writes after admission fixes the race. A new deterministic Node test fails on the previous implementation and passes after the fix; all 12 handoff tests and both actual-browser suites pass. The browser fixture still asserts immediate local Stop, then drains its owned slow request before closing. No arbitrary sleep was added to hide the failure.

## Packaging verification

The first package audit correctly rejected the new `desktop/health.cjs` because the builder and audit held duplicate file lists. They now use one immutable manifest; the builder receives a copy because electron-builder normalizes its file array in place. Real-ASAR regression tests cover the expected probe, unknown code, private runtime data and mutation isolation.

Backend freeze succeeded, frozen third-party notice coverage passed, and an unpacked Windows desktop build succeeded. The actual executable was started with a fresh smoke-only user-data directory and file diagnostics enabled. `/api/status` and `/api/languages` returned 200, model settings loaded, and the bundled FFmpeg/HLS path played synthetic media to 0.343053 seconds. Exit code was zero and the owned backend exited. The startup GPU snapshot reported software features even on the normal Chromium path; this is not evidence of hardware decoding or a stutter improvement.

The executable is `release/win-unpacked/LingerLens.exe` in the hardening worktree. No installer was published, the user's running app was not replaced, and smoke credentials/configuration were isolated from the user's desktop profile. Normal manual startup still uses the application's normal desktop user-data directory.

## Final local verification — supersedes pending checks above

The complete `npm run ci` was rerun on a clean detached checkout of `a12bff9c9bc9654326fbc352022db657bdfce0ec`, including the final package-manifest correction, and exited with code **0**. The verification checkout's tracked working tree is clean.

| Gate | Observed result |
| --- | --- |
| Python discovery | 74 files: 73 passed; one optional Streamlink file skipped because `streamlink` is not installed. 855 counted tests passed. |
| Actual browser | Both script-style browser suites passed; these are additional to the counted Python unit tests. |
| Frontend Node | 229 passed; zero failures. |
| Root Node | 45 passed; zero failures, including the four real-ASAR/package-isolation cases. |
| Source/release checks | Secret/release, tracked paths, Python imports, notices, docs, Python compile and JavaScript syntax checks passed. |

Total counted passing tests: **1,129**, plus the two actual-browser scripts. Local evidence is `.scratch/hardening-20260923/clean-npm-ci-release.log`; intermediate failures remain in separate logs. This final run replaces neither real paid-provider acceptance nor extended live soak testing.

The branch was pushed to the existing private repository. Draft PR #1 targets `wip/subtitle-anchor-correction`; nothing was merged, tagged, published or installed over the existing application. The subsequent execution-record commit changes documentation only; the code validated and packaged remains `a12bff9`.

Outstanding implementation and release gates remain explicit: client VAD/commit for blocked modes, native response-ID/cancellation and script-specific output, common-audio fan-out, live provider/region/language acceptance, original severe-freeze reproduction, and real platform outage/discontinuity soak. No unconditional latency or audiovisual synchronization guarantee is made.
