# Fixed-media absolute subtitle sync result

- Outcome: `completed`
- Matched visible markers: 16 / detected flashes 16
- DOM minus flash absolute P50/P95/max: 0.268 / 0.367 / 0.368 s
- Visible errors >250 ms / >500 ms: 12 / 0
- ASR begin minus known marker absolute P50/P95/max: 0.05 / 0.145 / 0.22 s
- Cue tStart minus flash Playback Wall Time absolute P50/P95/max: 0.208 / 0.298 / 0.334 s
- Anchor residual absolute P50/P95/max: 0.359 / 0.859 / 0.95 s
- Primary verdict: `FAIL`

## Phase breakdown

| Phase | n | signed median | abs P95 | abs max |
|---|---:|---:|---:|---:|
| warmup | 3 | 0.268 | 0.358 | 0.368 |
| frozen_baseline | 5 | 0.267 | 0.347 | 0.367 |
| after_package_stall | 5 | 0.269 | 0.269 | 0.269 |
| after_asr_stall | 3 | 0.137 | 0.137 | 0.137 |
| counter_injection | 0 | None | None | None |
| recovery | 0 | None | None | None |

The visible metric compares browser monotonic timestamps for the first subtitle DOM render and the first decoded white-flash frame. It therefore does not assume that browser and server monotonic clocks are comparable.
