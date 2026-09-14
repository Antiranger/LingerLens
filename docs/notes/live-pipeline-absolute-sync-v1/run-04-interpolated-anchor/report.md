# Fixed-media absolute subtitle sync result

- Outcome: `completed`
- Matched visible markers: 6 / detected flashes 6
- DOM minus flash absolute P50/P95/max: 0.175 / 0.177 / 0.177 s
- Visible errors >250 ms / >500 ms: 0 / 0
- ASR begin minus known marker absolute P50/P95/max: 0.11 / 0.195 / 0.22 s
- Cue tStart minus flash Playback Wall Time absolute P50/P95/max: 0.13 / 0.155 / 0.157 s
- Anchor residual absolute P50/P95/max: 0.181 / 0.588 / 0.815 s
- Primary verdict: `FAIL`

## Phase breakdown

| Phase | n | signed median | abs P95 | abs max |
|---|---:|---:|---:|---:|
| warmup | 3 | 0.176 | 0.177 | 0.177 |
| frozen_baseline | 3 | 0.174 | 0.176 | 0.176 |
| after_package_stall | 0 | None | None | None |
| after_asr_stall | 0 | None | None | None |
| counter_injection | 0 | None | None | None |
| recovery | 0 | None | None | None |

The visible metric compares browser monotonic timestamps for the first subtitle DOM render and the first decoded white-flash frame. It therefore does not assume that browser and server monotonic clocks are comparable.
