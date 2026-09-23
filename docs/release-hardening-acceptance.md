# Release hardening acceptance

The hardening branch is not a live-performance certification. Adapter contract tests,
real local media tests, browser tests, cloud fixture tests and soak runs are separate evidence levels.

## Repeatable local gate

Run `npm run ci` on a clean checkout, using the documented Python environment.
`test_provider_pipeline_matrix.py` passes the same three utterances through ten
real adapters, the real SubtitlePipeline and CueStore, including an intentional repeated phrase.
`test_media_clock_integration.py` runs real FFmpeg/TCP packaging with B frames,
independent starts and an asymmetric gap. `tests/logging-hardening.test.js` injects
slow storage and verifies bounded queues, responsive timers and rotation.

## Controlled provider acceptance

Generate or supply a non-sensitive mono PCM16 WAV at the provider's negotiated rate
(16 or 24 kHz). Use the SAME fixture for comparisons. The following tool refuses
cloud calls without explicit cost consent and caps audio at 120 seconds:

```powershell
python scripts/provider-live-acceptance.py --config <local-providers.json> --provider <profile-id> --audio <fixture.wav> --language en --max-seconds 30 --output <new-result.json> --confirm-paid
```

The default report contains model identity, fixture hash, event counts and paced
audio-end-to-final lag, not transcript text, keys or endpoints. `--events-output` is
an explicit transcript-containing JSONL recording for private replay; never commit it.
This probe does not measure source acquisition or the rendered video playhead, and
a successful run does not establish semantic translation alignment. Blocked profiles
stay blocked until their required commit controller is implemented.

For each model/region, cover Japanese, English and Chinese separately where supported,
long speech without pauses, genuine repeated phrases, code switching, music, silence,
reconnect and cancellation. Preserve the fixture hash and exact model/options privately.
Do not upgrade a profile from experimental on synthetic protocol fixtures alone.

## Live media and stutter acceptance

Use a fresh test profile and the exact built revision. Keep the user's running app unchanged.
Start with the default Chromium path; change one variable at a time. Correlate
`main-perf`, `playback-perf`, media-byte progress, private segment progress and source
clock validity. A late timer is not a dropped-video-frame counter.

Fault matrix: stop audio only and video only for 2/5/15 seconds; reset timestamps;
expire the upstream URL; pause the viewer; change quality; stop during startup;
restore connectivity. Verify at most three whole-session recoveries per manual
viewing session, no stale cues on the next media identity, and no owned process
left after stop. Run the existing `soak-live-monitor.py`, `soak-onscreen-sampler.py`
and `soak-analyze.py` for an extended real session only after short fault tests pass.

## Release gates still requiring live evidence

- Actual ASR/translation-to-screen readiness with a known sound/visual reference.
- Original severe stutter reproduction and same-build before/after comparison.
- Provider-specific long-turn behavior, response cancellation and bilingual semantic joins.
- Shared-audio fan-out redesign is not implemented; independent legs still require
  compatible source clocks. Script-specific native targets (for example zh-Hant)
  must not be claimed from a language label alone.
- Real source discontinuities across YouTube/Bilibili/Twitch; local FFmpeg fixtures
  do not establish a global audiovisual synchronization guarantee.
