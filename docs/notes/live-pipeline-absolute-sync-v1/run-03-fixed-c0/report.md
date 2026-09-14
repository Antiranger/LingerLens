# Fixed-media absolute subtitle sync result

- Outcome: `completed`
- Matched visible markers: 6 / detected flashes 6
- DOM minus flash absolute P50/P95/max: 0.217 / 0.294 / 0.319 s
- Visible errors >250 ms / >500 ms: 1 / 0
- ASR begin minus known marker absolute P50/P95/max: 0.12 / 0.2 / 0.22 s
- Cue tStart minus flash Playback Wall Time absolute P50/P95/max: 0.142 / 0.222 / 0.242 s
- Anchor residual absolute P50/P95/max: 0.2 / 0.721 / 0.921 s
- Primary verdict: `FAIL`

## Phase breakdown

| Phase | n | signed median | abs P95 | abs max |
|---|---:|---:|---:|---:|
| warmup | 3 | 0.215 | 0.309 | 0.319 |
| frozen_baseline | 3 | 0.217 | 0.218 | 0.218 |
| after_package_stall | 0 | None | None | None |
| after_asr_stall | 0 | None | None | None |
| counter_injection | 0 | None | None | None |
| recovery | 0 | None | None | None |

The visible metric compares browser monotonic timestamps for the first subtitle DOM render and the first decoded white-flash frame. It therefore does not assume that browser and server monotonic clocks are comparable.
