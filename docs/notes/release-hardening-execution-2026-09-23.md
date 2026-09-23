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

Long-lived AssemblyAI/Speechmatics/Tencent ledgers are bounded; Volcano full-history tombstones retire a numeric frontier. Soniox refuses an unended utterance beyond 8192 tokens instead of unlimited retention. AssemblyAI onset fallback converts milliseconds to seconds; continuous partials are requested explicitly; Speechmatics EndOfUtterance is no longer ignored. ASR sends and close handshakes have deadlines.

Selected real-media, clock, recovery, provider and pipeline tests passed. Remaining: real platform discontinuities and stop/reconnect soak; exact ASR-vs-player offsets with B-frame composition offsets; common-audio fan-out architecture is not implemented by this patch.

Primary media reference: https://ffmpeg.org/ffmpeg.html#Advanced-options ; https://ffmpeg.org/ffmpeg-formats.html
