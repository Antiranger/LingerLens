# Fixed-media absolute subtitle sync result

- Outcome: `completed`
- Matched visible markers: 3 / detected flashes 3
- DOM minus flash absolute P50/P95/max: 0.373 / 0.557 / 0.577 s
- Visible errors >250 ms / >500 ms: 2 / 1
- Anchor residual absolute P50/P95/max: None / None / None s
- Primary verdict: `FAIL`

## Phase breakdown

| Phase | n | signed median | abs P95 | abs max |
|---|---:|---:|---:|---:|
| baseline | 3 | 0.373 | 0.557 | 0.577 |
| after_package_stall | 0 | None | None | None |
| after_asr_stall | 0 | None | None | None |
| pre_counter_injection | 0 | None | None | None |
| counter_injection | 0 | None | None | None |
| recovery | 0 | None | None | None |

The visible metric compares browser monotonic timestamps for the first subtitle DOM render and the first decoded white-flash frame. It therefore does not assume that browser and server monotonic clocks are comparable.
