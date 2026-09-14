# Translation continuity concurrency result

| Policy | Context coverage | Missing predecessor | Queue P95 | Ready P95 | Max concurrency |
|---|---:|---:|---:|---:|---:|
| current_4_workers | 100.0% | 0 / 29 | 2.421s | 4.554s | 1 |
| single_worker | 100.0% | 0 / 29 | 2.441s | 4.574s | 1 |
| predecessor_dependency | 100.0% | 0 / 29 | 2.121s | 4.389s | 1 |

All 30 Cues reached `done` in every policy. Times are scaled back to simulated seconds.
The controlled Provider measures the context contract and latency tradeoff only; it does not score natural-language translation quality.
