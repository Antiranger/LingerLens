# Live-caption drift diagnosis — 2026-09-05

## User symptom

The reported symptom is a subtitle offset that begins at a few hundred
milliseconds and can become visibly late by roughly 2–3 seconds after several
minutes. The previous subtitle may then appear to remain too long because its
successor is not yet visible.

This report separates four possible clocks: subtitle readiness, Cue media
timestamps, the browser playback clock, and the final DOM render.

## Experiments

All runs used the production SubtitlePipeline, Soniox `stt-rt-v5`, the active
translation Provider, production HLS publishing, Chromium, the production
poller/scheduler/renderer, and an isolated dynamic port. Port 8765 was not
stopped or restarted.

### Controlled synchronized fixture, six minutes

Artifact: `run-01-clean/`

The fixture speaks and flashes every three seconds, giving a semantic
audio/video marker rather than relying on internal timestamps.

| Measurement | Result |
|---|---:|
| Matched markers | 114 |
| Minute 1 to final stable minute median growth | -0.115 s |
| DOM-minus-flash slope | -0.0225 s/min |
| Cue mapping-minus-flash slope | -0.0165 s/min |
| Anchor drift slope | +0.0037 s/min |
| Player live-edge-distance slope | -0.0158 s/min |
| Translation backlog maximum | 1 |
| Verdict | PASS |

The sampling completed, but the first runner version incorrectly applied the
older fault-injection experiment's end assertion and marked metadata as failed
because no package pause had been injected. All 360 seconds and final browser
trace were already written. The wrapper now explicitly disables that obsolete
assertion.

### Current real Twitch path, six minutes

Artifact: `../live-pipeline-latency-v2/run-06-drift-current/`

| Measurement | Result |
|---|---:|
| Cues / rendered | 81 / 69 |
| DOM lateness slope | -0.0052 s/min |
| Player buffer slope | +0.0102 s/min |
| Anchor drift slope | -0.0319 s/min |
| Translation backlog maximum | 3 |
| Translation queue P95 | 1.656 s |
| Total ready P95 | 9.172 s |
| Scheduler late / dropped | 0 / 0 |

Strict same-generation translation ordering increases queue latency relative to
the earlier concurrent run, but this input did not accumulate a browser-visible
delay. The backlog recovered to zero.

### Current Japanese YouTube path, six minutes

Artifact: `../live-pipeline-latency-v2/run-07-youtube-ja-current/`

The tested public ANN stream selected one muxed 720p60 rendition. This run
therefore exercises Japanese continuous speech and YouTube HLS behavior, but
does not exercise the repository's optional separate video/audio acquisition
topology.

| Measurement | Result |
|---|---:|
| Cues / rendered | 88 / 68 |
| Complete T0–T5 samples | 64 (72.7%) |
| ASR + Adapter P50 / P95 | 3.422 / 11.500 s |
| CaptionChunker P50 / P95 | 0.000 / 5.921 s |
| Translation queue P50 / P95 | 0.000 / 1.703 s |
| Translation Provider P50 / P95 | 1.109 / 2.406 s |
| Total ready P50 / P95 / max | 6.765 / 13.313 / 18.406 s |
| Translation deadline failures | 9 / 88 |
| Terminal headroom minimum | -0.718 s |
| DOM render maximum lateness | +0.756 s |
| Anchor absolute drift P50 / P95 / max | 0.396 / 2.605 / 3.804 s |
| Anchor observations over 500 ms | 105 / 280 |
| Player dropped frames | 0 |

The DOM-to-Cue slope remained stable (-0.0163 s/min), which rules out an
ever-growing browser render queue. It does not prove semantic audio alignment:
the renderer follows `cue.tStart`, so a wrong Cue timestamp still renders
"on time" according to the wrong coordinate.

## Located failure paths

### 1. Cue mapping freezes a constant while the real input counters diverge

`MediaAnchor` freezes the median after 20 valid samples. Later samples only set
the `drift` telemetry field. `epoch()` always maps a Cue as:

```text
pdt0 + frozen_offset + pcm_offset
```

In the Japanese YouTube run the raw completed-HLS-versus-sent-PCM difference
was about -0.4 seconds near startup, reached roughly -1.5 to -1.8 seconds around
165–175 seconds, and later partially recovered. The Anchor residual reached
-3.804 seconds during packaging bursts. This is the only observed production
clock path whose magnitude matches the reported 2–3 second semantic offset.

The residual is sampled across asynchronous PCM decode and completed HLS
segments, so it includes burst/sawtooth observation error. Applying every raw
drift value directly would make Cue timing jump. The result supports replacing
the frozen constant with a rate-aware or same-audio-timestamp mapping, after a
controlled A/B proves the filter does not follow packaging jitter.

### 2. Cues remain invisible until translation reaches a terminal state

The scheduler displays only `done` and `failed`. A source Cue in `src` or
`translating` remains hidden. On Japanese continuous speech, ASR/Adapter and
CaptionChunker latency consumed most of the 15-second playback budget before
translation began. Nine Cues hit their deadline and became source-only; the
minimum terminal headroom was -718 ms and one Cue rendered 756 ms after its
scheduled start.

This is a proven independent onset problem. Translation serialization is a
bounded amplifier here, not the dominant stage: its P95 was 1.703 seconds and
backlog never exceeded one on this run, while ASR/Adapter P95 was 11.5 seconds.

### 3. The previous row is a symptom, not a growing scheduler queue

The Japanese run produced long spoken spans (P95 7.5 seconds) and visible
intervals (P95 8.499 seconds, maximum 9.405 seconds). The current scheduler
already replaces a same-speaker predecessor when a successor becomes active.
When the successor is formed late or its Cue timestamp is late, the predecessor
is the only displayable row and remains through its own spoken window/tail.

No scheduler queue growth was measured: scheduler late count was zero in the
controlled and Twitch runs, and one deadline-related late Cue occurred in the
Japanese run.

## Evidence boundary

The synchronized fixture proves that the base player and Cue mapper are stable
for a regular one-stream clock. The Japanese live run proves large internal
counter disagreement and a real readiness miss, but has no synchronized visual
flash identifying the exact spoken word. Therefore the Anchor path is the
highest-probability explanation for the reported growing semantic offset, not
yet a safe signal to feed back unfiltered.

The next minimal implementation experiment should keep production polling and
render frequency unchanged and vary only the Cue mapping estimator. It must:

1. replay a known audio/flash marker through bursty HLS delivery;
2. add a controlled 0.4–0.5% PCM/HLS rate mismatch and short one-leg stalls;
3. compare the current frozen offset with a bounded, smoothed affine mapping;
4. require semantic DOM-minus-flash P95 below 250 ms with no persistent slope;
5. reject any candidate that follows segment sawtooth or increases Cue jitter.

Separately, a readiness experiment should render source text at its Cue start
when translation is still pending, then replace it in place when translation
arrives. It should not add Provider calls, workers, polling, or timers.
