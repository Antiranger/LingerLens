# LagLingo subtitle problem investigation — 2026-09-05

## What is and is not broken

The 600-second Twitch run at a 15-second target delay did **not** reproduce
subtitles missing their sentence start because generation was too slow. All 133
terminal Cues shown before the cutoff rendered within 96 ms after their
scheduled tStart, and the worst ready margin was still 1.241 seconds.

The follow-up fixed-media experiments did reproduce three separate subtitle
problems:

1. A stable, user-visible synchronization offset of a few hundred milliseconds.
2. Translation requests that start without their immediately preceding context.
3. Long or overlapping subtitle blocks that occupy too much of the video.

These have different causes and should not be fixed with one global delay
setting.

## P1 — MediaAnchor adds a stable late offset

### Evidence

The fixed 60-second fixture speaks a synthetic English marker and flashes the
whole video frame at exactly 5, 15, 25, 35, 45 and 55 seconds. It is looped
through the same MPEG-TS tee, production SubtitlePipeline, Soniox, translation,
HLS publisher, player clock, scheduler and DOM renderer.

The corrected 175-second run matched 16 visible markers:

| Metric | P50 | P95 | Maximum |
|---|---:|---:|---:|
| DOM first render minus flash | 268 ms | 367 ms | 368 ms |
| Cue tStart minus flash Playback Wall Time | 208 ms | 298 ms | 334 ms |
| Soniox begin_pcm minus known marker | 50 ms | 145 ms | 220 ms |
| Internal Anchor residual, absolute | 359 ms | 859 ms | 950 ms |

Twelve of 16 markers appeared more than 250 ms after the synchronized
audio/visual marker. None exceeded 500 ms. The Anchor froze at `C=+150 ms`, so
it shifted every Cue later by 150 ms in that run.

An experimental run fixed only the Anchor constant at `C=0`, without changing
production code. Its three frozen-baseline markers had a 217 ms median and 218
ms P95 DOM-to-flash error; server mapping P95 was 222 ms. The sample is small
and Soniox timestamp variation prevents treating it as a strict performance
A/B, but the direction matches the exact 150 ms timestamp shift.

### Stall and residual findings

- Packaging paused from 80.625 to 82.125 seconds and released a 16 KiB burst.
- ASR paused from 130.515 to 132.750 seconds and released a 24 KiB burst.
- Neither produced a persistent step in visible synchronization.
- Counter-only `+2/-2 s` injection moved the residual maximum to 2.809 seconds
  without a corresponding change in Cue timestamps or visible offset.

The residual is therefore a health signal for counter agreement, not a direct
measurement of semantic subtitle synchronization. Concurrent samples can fold
decoder/segment observation lag into the frozen constant.

### Fix direction

Keep the fixed-media marker experiment as the acceptance test. Replace or
redefine C using an origin-alignment measurement that cannot include decoder
processing lag. Do not hardcode C=0 globally until the same test covers
separate audio/video acquisition legs and non-zero source start timestamps.

## P1 — Translation continuity is broken by concurrent request start

### Live evidence

The Twitch run had 29 of 134 translation attempts without the immediately
preceding translated Caption Chunk: 21.6%.

### Deterministic experiment

A fixed 30-Chunk workload used the production queue, worker and RollingContext
with controlled Provider latency and six ASR-style bursts:

| Policy | Context coverage | Missing predecessor | Queue P95 | Ready P95 |
|---|---:|---:|---:|---:|
| Current 4 workers | 75.9% | 7 / 29 | 0.266 s | 3.706 s |
| Single worker | 100% | 0 / 29 | 2.441 s | 4.398 s |
| Same-generation predecessor dependency | 100% | 0 / 29 | 2.110 s | 4.213 s |

All 30 Cues reached `done`; there were no failures or drops. The current policy
missed every modeled simultaneous predecessor and one Cue following the fixed
7-second Provider outlier. Its 24.1% gap rate closely reproduces the live 21.6%
rate.

The controlled Provider proves request-context availability and latency tradeoff
only. It does not prove that the 29 live translations were linguistically
wrong.

### Fix direction

Do not add workers and do not globally set workers to one. Admit a Chunk only
after its same-generation predecessor reaches a terminal context decision,
while bounding the wait by the Cue's remaining playback/translation deadline.
Different generations can retain independent chains. Add real translated-text
blind review before claiming a quality improvement.

## P1 — Sequential Cue tails already stack in the live player

### Live evidence

The Twitch browser trace contained 34 consecutive Caption-Chunk pairs whose DOM
intervals overlapped. Median overlap was 598 ms, maximum 1.0 second, and the
player spent 18.559 seconds with at least two subtitle rows visible. Twelve
overlaps contained at least one Cue longer than 40 source characters; the
largest pair combined 141 source characters.

This follows directly from the scheduler contract: every Cue remains active
until `tEnd + min(hold, 1.5s)`, while `active()` also admits its chronological
successor. The renderer displays every active Cue.

### Geometry experiment

Production HTML/CSS was rendered in Chromium for fixed bilingual strings at
40, 56, 70 and 85 source characters:

- Current 85-character Cues occupied 18.1%-32.0% of player height and used up
  to three visual lines.
- Splitting near 42 characters reduced an individual part to 11.9%-22.3% and
  one or two lines.
- At real scheduler boundaries, the split parts stacked into three active rows
  and occupied 39.9%-64.2% of player height.
- Even 56-character two-part splits occupied 25.9%-39.5% at the transition.

The geometry uses fixed public text; exact wrapping will vary with language and
glyph width. The scheduling overlap is production behavior and was independently
observed in the real Twitch trace.

### Fix direction

Fix tail ownership before lowering the character cap. When a later Cue begins
at or after the earlier Cue's tEnd, truncate the earlier tail at the successor's
tStart. Preserve both only for real audio overlap (`next.tStart < prior.tEnd`).
After that, compare raw-character, display-width and line-budget chunking. A
42-character cap by itself makes the current overlay worse.

## Priority order

1. Add scheduler regression coverage for non-overlapping consecutive Cues and
   stop predecessor-tail stacking. This is already present in the real run and
   has a narrow deterministic seam.
2. Add same-generation predecessor admission with a deadline bound, then replay
   the deterministic context workload and a fixed-text Provider evaluation.
3. Replace the Anchor estimator only after adding separate-leg fixed-media cases;
   retain DOM-to-flash as the acceptance metric.
4. Tune Caption Chunk size/width after the scheduler fix, then rerun geometry at
   medium and large sizes and narrow viewports.

Increasing translation workers is not supported by the data. Lowering the
15-second playback target is also premature: the live run's worst ready margin
was only 1.241 seconds.

## Artifacts

- Absolute synchronization spec and runner:
  `.scratch/live-pipeline-absolute-sync-v1/`
- Corrected real-provider fault run:
  `.scratch/live-pipeline-absolute-sync-v1/run-02/`
- Experimental fixed-C run:
  `.scratch/live-pipeline-absolute-sync-v1/run-03-fixed-c0/`
- Translation continuity spec and formal run:
  `.scratch/translation-context-concurrency-v1/` and `run-04/`
- Readability geometry spec and corrected run:
  `.scratch/subtitle-readability-v1/` and `run-02/`
- Original 600-second live report:
  `.scratch/live-pipeline-latency-v2/run-05/report.md`

