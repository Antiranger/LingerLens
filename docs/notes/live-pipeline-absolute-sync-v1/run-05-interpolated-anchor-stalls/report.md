# Fixed-media absolute subtitle sync result

- Outcome: `completed`
- Matched visible markers: 13 / detected flashes 13
- DOM minus flash absolute P50/P95/max: 0.09 / 0.222 / 0.272 s
- Visible errors >250 ms / >500 ms: 1 / 0
- ASR begin minus known marker absolute P50/P95/max: 0.06 / 0.172 / 0.22 s
- Cue tStart minus flash Playback Wall Time absolute P50/P95/max: 0.064 / 0.164 / 0.197 s
- Anchor residual absolute P50/P95/max: 0.205 / 0.581 / 0.91 s
- Primary verdict: `PASS`

## Phase breakdown

| Phase | n | signed median | abs P95 | abs max |
|---|---:|---:|---:|---:|
| warmup | 3 | 0.187 | 0.264 | 0.272 |
| frozen_baseline | 5 | 0.171 | 0.188 | 0.188 |
| after_package_stall | 5 | 0.09 | 0.09 | 0.09 |
| after_asr_stall | 0 | None | None | None |
| counter_injection | 0 | None | None | None |
| recovery | 0 | None | None | None |

The visible metric compares browser monotonic timestamps for the first subtitle DOM render and the first decoded white-flash frame. It therefore does not assume that browser and server monotonic clocks are comparable.
