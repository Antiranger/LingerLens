# Ticket 03 Acceptance Results

## Deterministic acceptance

Implemented and automated:

- source-order continuity when translation completion order is `3,1,2`;
- exclusion of CURRENT, future order, media-future, other generation, and metadata-free live-message calls;
- duplicate `(generation, chunk_order)` rejection;
- `contextSeconds` filtering by Caption Chunk media `tEnd`, not translation completion wall time;
- generic LLM CURRENT-only continuity prompt and unknown-aware chunk-position metadata;
- chronological Qwen-MT `tm_list` without unsupported free-text protocol fields;
- terminal Cue publication (`done|failed` once) with immutable source/time/order/cut metadata;
- deterministic English, Spanish, Chinese, Japanese, Korean, mixed/unknown, URL, decimal, proper-name and emoji fixtures;
- terminal punctuation / weak cut / endpoint continuation metadata.

Fixture: `prototype/hls-companion/tests/fixtures/ticket03_translation_continuity.json`.

## Real-live acceptance

Status: **blocked, not passed**.

This checkout has no supplied credentials or controlled multilingual live samples for the required matrix. The following must not be inferred from fixtures:

1. English live speech with an original ASR Utterance longer than 20 seconds.
2. Spanish or another whitespace-language live sample.
3. At least two of Chinese/Japanese/Korean live samples.
4. Three real evidence paths: immutable tokens, native stable prefix, mutable LocalAgreement or final-only/window.
5. Background music/lyrics where VAD does not end.
6. Manual review of at least 50 boundaries as natural / acceptable hard / bad, target bad `<10%`.
7. Separate source-ready, translation-success-ready and terminal-outcome latency measurements.
8. Strict-path source Cue reaching `done|failed` before playback `tStart` at least 99%, with Provider/platform outages reported separately.
9. Adjacent translation review for subject/reference/register/terminology/repetition/omission/future invention.

Required inputs: live/sample URLs or recordings that satisfy the language/content matrix, plus credentials for the selected cloud ASR/translation Providers. No credentials were used or requested by automated tests.
