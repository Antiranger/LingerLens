# Subtitle problem fixes and verification

Date: 2026-09-05

## Scope

This change set fixes the four failure paths confirmed by the preceding experiments:

1. a previous Caption Chunk's display tail stacked with a later non-overlapping chunk;
2. concurrent translation workers constructed requests before the immediately preceding context was terminal;
3. long Caption Chunks could occupy up to three bilingual lines and as much as 32% of the player stage;
4. `MediaAnchor` added segment-midpoint compensation to a sampled phase that left subtitles consistently late.

The fixes preserve the existing four-worker setting, queue limit, Provider timeout, HLS stream-copy path, Caption Chunk minimum span, six-second hard cap, and genuine overlapping-speech display.

## Production changes

### Scheduler tail ownership

When a displayable successor starts at or after the previous Cue's audio end, the successor owns the display from that boundary. The previous Cue's natural tail ends there. If `next.tStart < previous.tEnd`, both Cues remain visible because the audio really overlaps. Late catch-up admissions keep their existing independent hold window and late/drop counters.

### Translation continuity

Caption Chunks now enter a bounded per-generation admission chain before the existing translation queue:

- only the earliest unfinished chunk of a generation is admitted to a Provider call;
- success and failure are both terminal and release the next chunk;
- different generations can still use separate workers concurrently;
- waiting chunks count toward the existing backlog, degradation thresholds, and queue cap;
- completion removes admission/deadline state, and stop clears all pending state;
- every chunk keeps the absolute deadline assigned when it was enqueued;
- the Provider deadline is also capped by `target playback delay - Cue audio span - readiness time already consumed`.

This prevents waiting behind context from silently granting a fresh Provider timeout or issuing a translation that can no longer be ready when its Cue starts playing.

### Display-width chunking

The shared `CaptionChunker` now has a default 42-column readability budget. Narrow characters count as one column, East Asian full-width characters count as two, and combining marks add no column. It cuts only at normalized recognition-unit boundaries after the existing 1.2-second minimum span. The six-second hard cap has priority over the width budget.

Width cuts are reported as `display_width`, and their sentence-continuity metadata is passed through the existing translation request. The multilingual deterministic fixture was updated to the new boundary without changing or losing source content.

### Media anchor

The production `media_clock` now supplies `MediaAnchor` with the existing `CaptureClock` interpolated packaging-media cursor. Because this cursor already removes the completed-segment sawtooth, it explicitly supplies zero midpoint compensation. `MediaAnchor` still measures and freezes a real offset `C`; it is not hard-coded to zero, and drift telemetry remains active.

The packaging command, per-track `setts` timestamp normalization, PDT generation, stream-copy behavior, and delayed playlist publication were not changed.

## Results

### Tail and geometry

The corrected sequential Chromium experiment is in `subtitle-readability-v1/run-04-fixed-sequence`.

- All 16 viewport/font/length scenarios had one active row at every non-overlapping split boundary.
- An 85-character Cue split into at most two-line chunks rather than one three-line Cue.
- At 1600x1000 large, the largest boundary occupied 20.6% of the stage instead of the old stacked 59.5%.
- At 1100x760 large, the largest boundary occupied 22.3% instead of the old stacked 64.2%.
- Genuine audio overlap remains covered by a separate scheduler regression test.

Scheduler microbenchmark (`scheduler-benchmark.json`): 500 retained Cues over 2,500 ticks took 23.998 ms before and 29.417 ms after in the median round. The added cost is about 0.0022 ms per tick, far below the player's 100 ms scheduler interval.

### Translation context and latency

The post-fix deterministic run is in `translation-context-concurrency-v1/run-05-fixed`.

| Metric | Before, current 4 workers | Fixed, current 4 workers |
|---|---:|---:|
| Missing immediate predecessor | 7 / 29 | 0 / 29 |
| Context coverage | 75.9% | 100.0% |
| Queue P95 | 0.266 s | 2.421 s |
| Arrival-to-ready P95 | 3.706 s | 4.554 s |
| Terminal done | 30 / 30 | 30 / 30 |
| Translation failures/drops | 0 / 0 | 0 / 0 |

The controlled workload contains one generation, so its expected Provider concurrency after the fix is one. A separate regression test proves two generations can call the Provider concurrently. The 0.848-second P95 readiness cost is bounded by the original per-Cue deadline and the playback budget.

### Absolute subtitle sync

The final real Soniox/Chromium run is in `live-pipeline-absolute-sync-v1/run-06-interpolated-anchor-both-stalls`. It used 170 seconds of synthetic English media and an isolated dynamic port. It did not touch the normal Companion port.

| Metric | Previous production anchor run | Fixed anchor run |
|---|---:|---:|
| Matched visible markers | 16 | 15 |
| DOM minus flash absolute P50 | 268 ms | 81 ms |
| DOM minus flash absolute P95 | 367 ms | 158 ms |
| DOM minus flash maximum | 368 ms | 183 ms |
| Errors over 250 ms | 12 | 0 |
| Cue `tStart` minus flash P95 | 298 ms | 156 ms |
| Primary verdict | FAIL | PASS |

Phase results in the fixed run:

- frozen baseline: absolute P95 84 ms;
- after the 1.5-second packaging-leg pause and burst: absolute P95 78 ms;
- after the 2.2-second ASR-leg pause and burst: two visible markers, absolute P95 142 ms and maximum 147 ms;
- browser errors: none, and decoded video frames advanced throughout the run;
- no persistent synchronization step followed either catch-up event.

The internal Anchor residual still reached 836 ms while visible P95 remained 158 ms. It remains a counter-consistency diagnostic and is not treated as semantic subtitle sync.

Two earlier post-fix confirmation runs are retained:

- `run-04-interpolated-anchor`: six markers, P95/max 177/177 ms; the analyzer's overall verdict is false only because the specification requires at least twelve markers.
- `run-05-interpolated-anchor-stalls`: thirteen markers, P95/max 222/272 ms, primary verdict PASS.

This turn sent 394 seconds of synthetic English audio to Soniox across the three confirmation runs. Translation and geometry experiments did not call external Providers.

## Verification

- Red/green scheduler tests: the old implementation returned both the previous and successor Cue at a non-overlapping boundary; 16 scheduler tests now pass.
- Red/green translation test: the old implementation started chunk 2 before chunk 1 was terminal; eight continuity/deadline tests now pass inside 63 pipeline tests.
- Red/green readability tests: the old chunker did not accept a display-width budget; 17 chunker tests now pass.
- Multilingual acceptance fixture: 2 tests pass.
- Server/provider tests: 22 pass.
- Full `npm run ci`: PASS, including Python compilation, JavaScript parsing, all Companion Python/Node suites, repository Node tests, and release guard.
- `git diff --check`: no whitespace errors; Git only reports the repository's existing Windows LF-to-CRLF warnings.

## Runtime state and remaining coverage

The normal Companion at `http://127.0.0.1:8765` remains `idle`, with subtitles stopped. It was not restarted, so the active process has not loaded these disk changes. They take effect after the user's next normal Companion restart.

The absolute experiment exercised independently pausable packaging and ASR paths, including burst catch-up. It did not create a second physical audio input with deliberately different starting PTS, nor inject a true byte-loss discontinuity. Existing `build_ffmpeg_command` tests still verify separate per-track `setts` filters, and the fix does not alter that path. A future timestamp-normalization change should add those fixture variants before changing the anchor contract again.
