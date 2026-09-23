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
