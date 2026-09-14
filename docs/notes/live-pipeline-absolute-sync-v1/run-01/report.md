# Fixed-media absolute subtitle sync result

- Outcome: `completed`
- Matched visible markers: 23 / detected flashes 23
- DOM minus flash absolute P50/P95/max: 0.416 / 0.677 / 0.68 s
- Visible errors >250 ms / >500 ms: 19 / 10
- Anchor residual absolute P50/P95/max: 0.309 / 2.002 / 2.809 s
- Primary verdict: `FAIL`

## Phase breakdown

| Phase | n | signed median | abs P95 | abs max |
|---|---:|---:|---:|---:|
| baseline | 8 | 0.251 | 0.496 | 0.595 |
| after_package_stall | 3 | 0.412 | 0.416 | 0.416 |
| after_asr_stall | 5 | 0.462 | 0.581 | 0.581 |
| pre_counter_injection | 2 | 0.68 | 0.68 | 0.68 |
| counter_injection | 2 | 0.648 | 0.648 | 0.648 |
| recovery | 3 | 0.648 | 0.649 | 0.649 |

The visible metric compares browser monotonic timestamps for the first subtitle DOM render and the first decoded white-flash frame. It therefore does not assume that browser and server monotonic clocks are comparable.
