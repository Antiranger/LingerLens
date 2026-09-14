# Fixed-media absolute subtitle sync result

- Outcome: `completed`
- Matched visible markers: 15 / detected flashes 15
- DOM minus flash absolute P50/P95/max: 0.081 / 0.158 / 0.183 s
- Visible errors >250 ms / >500 ms: 0 / 0
- ASR begin minus known marker absolute P50/P95/max: 0.06 / 0.164 / 0.22 s
- Cue tStart minus flash Playback Wall Time absolute P50/P95/max: 0.043 / 0.156 / 0.161 s
- Anchor residual absolute P50/P95/max: 0.186 / 0.559 / 0.836 s
- Primary verdict: `PASS`

## Phase breakdown

| Phase | n | signed median | abs P95 | abs max |
|---|---:|---:|---:|---:|
| warmup | 3 | 0.081 | 0.173 | 0.183 |
| frozen_baseline | 5 | 0.083 | 0.084 | 0.084 |
| after_package_stall | 5 | -0.03 | 0.078 | 0.086 |
| after_asr_stall | 2 | -0.097 | 0.142 | 0.147 |
| counter_injection | 0 | None | None | None |
| recovery | 0 | None | None | None |

The visible metric compares browser monotonic timestamps for the first subtitle DOM render and the first decoded white-flash frame. It therefore does not assume that browser and server monotonic clocks are comparable.
