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
