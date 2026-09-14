# Continuous live-caption onset follow-up

Date: 2026-09-05

## Reproduction

The earlier absolute-sync fixture left ten seconds between short phrases. This
follow-up uses a phrase every three seconds for 90 seconds while exercising the
real Soniox Adapter, active translation Provider, production HLS pipeline,
production Chromium player, subtitle poller, scheduler, and DOM renderer on an
isolated dynamic port.

The captured Twitch session on port 8765 also contained eight adjacent Cue
pairs from the same speaker whose audio ranges overlapped by 0.78-2.00 seconds.
All eight were mid-sentence hard-deadline continuations. They are impossible as
simultaneous speech from one speaker and explain why an old row could visually
remain over its continuation.

## Minimal fixes

- Different-speaker Cues remain concurrent only for the positive intersection
  of their spoken ranges. When the earlier speaker stops, the later continuing
  Cue owns the display and the old reading tail ends.
- A newer Cue from the same non-empty speaker label replaces the older Cue even
  if malformed recognition timing makes their ranges overlap.
- The 100 ms renderer uses exactly one render tick of lookahead. This removes
  the timer's systematic 0-100 ms late quantization without increasing timer or
  HTTP polling frequency.
- Subtitle opacity no longer fades in for 120 ms. Hover/focus transitions are
  unchanged.

## A/B result

| Metric | Before | After |
|---|---:|---:|
| Matched visible markers | 25 | 25 |
| DOM minus speech-flash absolute P50 | 109 ms | 45 ms |
| DOM minus speech-flash absolute P95 | 274 ms | 147 ms |
| DOM minus speech-flash maximum | 518 ms | 446 ms |
| Errors over 250 ms | 2 | 1 |
| Errors over 500 ms | 1 | 0 |
| Verdict | FAIL | PASS |

The ASR onset distribution remained at P50/P95 40/136 ms, so the improvement
came from presentation timing rather than rewriting recognition timestamps.

## Performance

With 500 retained Cues carrying ten alternating speaker labels over 2,500
ticks, the corrected scheduler took 70.012 ms total, or 0.028 ms per tick. The
player runs one tick every 100 ms. The change does not add polling, timers, DOM
passes, Provider calls, or server work.
