# Clause segmentation experiment, 2026-09-06

Status: isolated experiment, not a production policy. The user's current
clause-ending requirement supersedes the old six-second hard-cap design.

Question: Can increasing the cap, punctuation gating, or a small local Japanese
morphology gate prevent mid-phrase cuts without hiding extra latency?

Evidence is deliberately separated:

* Authored Japanese/English/Chinese fixtures have **synthetic** token timestamps
  and delivery schedules. Allowed cuts are manually specified before running.
  They are adversarial examples, not a representative accuracy benchmark.
* The existing 159.9855-second local Japanese audio is sent to the configured
  Soniox in real time, preserving allowlisted raw token arrivals and normalized
  observations. This is a new capture, not a replay of the user's screenshots.
* An oracle policy uses fixture labels. It is a feasibility reference, not an
  implementable detector and not evidence that a model can find these cuts.

Compare the production snapshot at 6/8/10 seconds with immediate terminal
punctuation, a conservative local morphology candidate, and oracle labels.
The morphology experiment uses already-installed fugashi/UniDic; it adds no
application dependency. Any undetected clause remains buffered; time alone
does not create a cut. Flush at audio/session end preserves residual fragments
and must not be counted as a linguistically validated boundary.

Measure forbidden internal cuts, accepted clause opportunities, exact text
conservation, emission times, start-to-emission delay, and readiness at a
15-second playback delay under **assumed** 2/4-second translation durations.
The translation calculation excludes queue/network/player overhead and is
not live end-to-end latency. Also measure CPU per replay, excluding imports,
file I/O and provider calls. Inspect real outputs separately; do not infer
linguistic correctness from the absence of a hard_deadline reason.

Production gate: candidate must not break any explicit protected phrase,
must release useful clauses during continuous speech, preserve text/times,
and show acceptable cost. A failed gate means retain an experimental artifact
and report the concrete failure, not quietly deploy a larger timeout or a
weaker definition of a clause.
