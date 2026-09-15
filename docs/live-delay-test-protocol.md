# Live Delay Test Protocol

## Before each run

1. Disable unrelated extensions that alter the player or media speed.
2. Record browser/version, OS, network type, and whether Memory Saver is enabled.
3. Open the same live URL in Source and Viewer tabs.
4. Ensure both players are playing and initially at their platform live edge.
5. Open DevTools console in both tabs if practical; filter for `LingerLens Live Delay`.
6. Select the correct role in each panel.
7. In Viewer, select the target delay and click **Set delay & start**.
8. In Source, click **Start observation**.

Do not compare wall-clock broadcaster latency. Only use the exported `actualDelay` relative to each player's final seekable range end.

## Run timeline (30 minutes minimum)

Suggested schedule:

| Time | Action |
|---:|---|
| 00:00 | Start Source and Viewer monitoring |
| 00:00–10:00 | Uninterrupted playback |
| 10:00 | Pause Viewer for 10 seconds, then resume |
| 15:00 | Click Live / drag Viewer to the front |
| 20:00 | Change Viewer quality once |
| 25:00 | Inspect Source playback while it remains backgrounded |
| 30:00+ | Export JSON and CSV from both tabs |

Record any unplanned buffering, player reset, navigation, error screen, or manual recovery.

## Failure interpretation

- `seekable_unavailable`: the page did not expose a playable DVR range through `HTMLMediaElement.seekable`.
- `seekable_window_too_short`: the final seekable range existed but was shorter than the selected target.
- `current_time_assignment_failed`: setting `currentTime` threw.
- `initial_seek_not_observed`: the assignment did not produce a delay within ±2s after verification; the platform may have ignored or corrected it.
- Rising `autoJumpToLiveCount`: likely platform/player correction to near-live, excluding extension and recent user seeks.
- Rising `driftCorrectionCount`: the target is not naturally stable and requires hard seeks.
- Rising `sourceTabStoppedCount`: the Source timeline advanced less than 0.2s for three consecutive samples while nominally playing and ready; inspect background throttling/discarding.

Heuristics are evidence prompts, not definitive platform internals. Confirm suspicious events against console timestamps and visible player behavior.

## Classification guidance

### Directly supported

- DVR history is consistently sufficient.
- Initial seek succeeds.
- Most valid samples remain in target ±1s.
- Corrections are rare and do not visibly disrupt viewing.
- Pause/resume and quality changes recover automatically.

### Conditionally supported

- Only some streams/player modes expose DVR.
- One target works while the other is frequently corrected.
- Quality changes or reloads require rebuilding delay.
- Background Source playback is unreliable without browser settings changes.

State the exact conditions and recovery steps.

### Not directly supported

- Seekable history is consistently unavailable/too short.
- `currentTime` assignment is ineffective.
- The player repeatedly forces the Viewer to live.
- Stable delay would require stream parsing or a custom media buffer.

Do not respond to this outcome by adding a complex controller in this spike.
