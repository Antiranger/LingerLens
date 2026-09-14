# Translation continuity concurrency experiment

## Question

Which scheduling policy preserves the immediately preceding translated Caption
Chunk without creating an unacceptable translation queue?

## Compared policies

1. `current_4_workers`: production `SubtitlePipeline._translation_worker` with
   four workers and the current shared FIFO queue.
2. `single_worker`: the same production worker with one worker.
3. `predecessor_dependency`: four production workers remain available, but a
   Cue from one generation is admitted to the queue only after its immediate
   predecessor has completed and entered `RollingContext`.

The third policy is implemented only in the experiment's admission harness; no
production code is changed.

## Workload

- One fixed generation of 30 ordered Caption Chunks.
- Nominal source interval: 4 simulated seconds.
- Every fifth Chunk arrives simultaneously with its predecessor to model an ASR
  catch-up burst.
- Provider latency: 1.2 simulated seconds normally, 3.2 seconds at four fixed
  orders, and 7.0 seconds at one fixed order.
- Wall time is scaled by 0.01, so the full experiment is deterministic and fast
  while preserving latency/interarrival ratios.

The deterministic Provider records the production `TranslationRequest`,
including `history`, generation and chunk order. It does not call an external
model and does not claim translation-quality results.

## Measurements

- Requests missing the immediate predecessor in `history`.
- Translation queue delay P50/P95/max.
- Arrival-to-terminal-ready P50/P95/max.
- Maximum concurrent Provider calls.
- Translation failures, drops, and terminal completion count.

## Decision rule

A policy satisfies continuity only when every order after 1 receives its
immediate predecessor. Among satisfying policies, prefer the one whose queue
P95 remains below the measured live budget and whose concurrency contract does
not serialize unrelated generations by design.

