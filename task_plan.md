# Task Plan: Ticket 01 — Provider Catalog and local OpenAI-compatible ASR

## Goal
Deliver Ticket 01 using strict vertical-slice TDD at the documented public seams: loopback model-settings HTTP API, provider/ASRStream interface, and browser-observable player assets.

## Confirmed public seams
- Loopback `/api/model-settings` and general `/api/providers`/status HTTP behavior.
- `ASRProvider` / `ASRStream` events with a fake transcription HTTP service.
- Browser player DOM/request behavior through web asset tests.

## Phases
- [ ] Phase 1 — Inspect full current server/provider/player/test call paths and record compatibility constraints
- [ ] Phase 2 — Red→green catalog v2 migration, persistence, CRUD, active selection, deletion invariants, and local route security
- [ ] Phase 3 — Red→green bounded OpenAI-compatible transcription adapter and failure-isolated stream behavior
- [ ] Phase 4 — Red→green active-catalog subtitle startup and removal of outer provider selectors/request fields
- [ ] Phase 5 — Update example configuration and local Whisper documentation
- [ ] Phase 6 — Run focused tests, aggregate suite, syntax/LSP diagnostics, inspect diff, and commit

## Scope constraints
- Preserve DashScope Qwen realtime/task ASR, Qwen-MT/openai-compatible translation fallback, subtitle preferences, and playback failure isolation.
- Model settings is the only CRUD/active selection UI and the only raw-key response.
- Raw keys require loopback Host plus same-origin Origin and `Cache-Control: no-store`; other APIs/status/errors/logs must not expose them.
- OpenAI-compatible ASR targets an already-running service, sends bounded PCM windows as WAV multipart to `/audio/transcriptions`, sends Authorization only when configured, and declares only real capabilities.
- Work one failing behavior test → minimal implementation at a time; do not weaken tests.

## Errors Encountered
| Error | Attempt | Resolution |
|---|---:|---|
