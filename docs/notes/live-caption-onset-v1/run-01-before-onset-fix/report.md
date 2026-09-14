# Fixed-media absolute subtitle sync result

- Outcome: `completed`
- Matched visible markers: 25 / detected flashes 25
- DOM minus flash absolute P50/P95/max: 0.109 / 0.274 / 0.518 s
- Visible errors >250 ms / >500 ms: 2 / 1
- ASR begin minus known marker absolute P50/P95/max: 0.04 / 0.136 / 0.46 s
- Cue tStart minus flash Playback Wall Time absolute P50/P95/max: 0.072 / 0.243 / 0.492 s
- Anchor residual absolute P50/P95/max: 0.143 / 0.408 / 0.712 s
- Primary verdict: `FAIL`

## Phase breakdown

| Phase | n | signed median | abs P95 | abs max |
|---|---:|---:|---:|---:|
| warmup | 10 | 0.109 | 0.223 | 0.315 |
| frozen_baseline | 15 | 0.11 | 0.234 | 0.518 |
| after_package_stall | 0 | None | None | None |
| after_asr_stall | 0 | None | None | None |
| counter_injection | 0 | None | None | None |
| recovery | 0 | None | None | None |

The visible metric compares browser monotonic timestamps for the first subtitle DOM render and the first decoded white-flash frame. It therefore does not assume that browser and server monotonic clocks are comparable.
