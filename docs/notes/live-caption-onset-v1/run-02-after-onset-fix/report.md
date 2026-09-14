# Fixed-media absolute subtitle sync result

- Outcome: `completed`
- Matched visible markers: 25 / detected flashes 25
- DOM minus flash absolute P50/P95/max: 0.045 / 0.147 / 0.446 s
- Visible errors >250 ms / >500 ms: 1 / 0
- ASR begin minus known marker absolute P50/P95/max: 0.04 / 0.136 / 0.46 s
- Cue tStart minus flash Playback Wall Time absolute P50/P95/max: 0.095 / 0.207 / 0.516 s
- Anchor residual absolute P50/P95/max: 0.142 / 0.371 / 0.533 s
- Primary verdict: `PASS`

## Phase breakdown

| Phase | n | signed median | abs P95 | abs max |
|---|---:|---:|---:|---:|
| warmup | 10 | 0.044 | 0.146 | 0.148 |
| frozen_baseline | 15 | 0.045 | 0.167 | 0.446 |
| after_package_stall | 0 | None | None | None |
| after_asr_stall | 0 | None | None | None |
| counter_injection | 0 | None | None | None |
| recovery | 0 | None | None | None |

The visible metric compares browser monotonic timestamps for the first subtitle DOM render and the first decoded white-flash frame. It therefore does not assume that browser and server monotonic clocks are comparable.
