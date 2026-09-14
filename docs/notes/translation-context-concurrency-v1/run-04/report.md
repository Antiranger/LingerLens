# Translation continuity concurrency result

| Policy | Context coverage | Missing predecessor | Queue P95 | Ready P95 | Max concurrency |
|---|---:|---:|---:|---:|---:|
| current_4_workers | 75.9% | 7 / 29 | 0.266s | 3.706s | 2 |
| single_worker | 100.0% | 0 / 29 | 2.441s | 4.398s | 1 |
| predecessor_dependency | 100.0% | 0 / 29 | 2.11s | 4.213s | 1 |

All 30 Cues reached `done` in every policy. Times are scaled back to simulated seconds.
The controlled Provider measures the context contract and latency tradeoff only; it does not score natural-language translation quality.
