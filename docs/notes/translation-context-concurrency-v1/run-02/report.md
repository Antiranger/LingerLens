# Translation continuity concurrency result

| Policy | Context coverage | Missing predecessor | Queue P95 | Ready P95 | Max concurrency |
|---|---:|---:|---:|---:|---:|
| current_4_workers | 79.3% | 6 / 29 | 1.4s | 5.25s | 2 |
| single_worker | 100.0% | 0 / 29 | 3.495s | 6.68s | 1 |
| predecessor_dependency | 100.0% | 0 / 29 | 1.655s | 5.295s | 1 |

All 30 Cues reached `done` in every policy. Times are scaled back to simulated seconds.
The controlled Provider measures the context contract and latency tradeoff only; it does not score natural-language translation quality.
