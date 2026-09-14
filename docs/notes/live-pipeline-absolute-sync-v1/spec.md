# Fixed-media absolute subtitle sync experiment

## Question

Does the `MediaAnchor` residual observed in a live Twitch run correspond to a
real, user-visible subtitle offset, and do one-leg stalls leave a persistent
sync error?

## Fixture

- 60-second 1280x720/30 H.264 + AAC clip, looped as a live MPEG-TS source.
- A short English phrase begins at media times 5, 15, 25, 35, 45 and 55
  seconds.
- The whole video frame flashes white for 300 ms at the same media times.
- The phrase WAV is trimmed to its first non-silent sample before it is mixed,
  so the scheduled audio and visual marker share a known onset.

The fixture contains synthetic public text and no user content.

## Production path exercised

The same MPEG-TS bytes are sent to both production legs:

1. `LiveSession` and `DelayedPlaylistPublisher` create the delayed HLS stream.
2. `SubtitlePipeline` decodes PCM, sends it to the active Soniox realtime
   Provider, chunks captions, translates them, stores Cues, and maps PCM time
   to PDT with `MediaAnchor`.
3. A real headless Chromium runs the production player, HLS clock, subtitle
   poller, scheduler, and DOM renderer.

The lab source replaces only the network acquisition boundary. It uses an
isolated HTTP port and media directory and never touches the normal port 8765.

## Schedule

- 0-80 s: baseline and Anchor freeze.
- 80-82 s: packaging leg is paused while the ASR leg continues; buffered TS is
  then released as a burst.
- 130-132 s: ASR leg is paused while packaging continues; buffered TS is then
  released as a burst.
- 180-190 s: add +2 s only to the counter presented to `MediaAnchor` sampling.
- 190-200 s: add -2 s only to the sampled counter.
- 200-245 s: recovery observation.

The counter-only injection is deliberately excluded from Cue PCM timestamps.
It tests whether the residual diagnostic can move without moving real subtitle
sync.

## Measurements

- Known marker PCM onset.
- Cue begin PCM, tStart/tEnd and terminal state.
- `MediaAnchor` C, drift, sample count and packaging media counter.
- Browser Playback Wall Time and video `currentTime`.
- First white-flash video frame.
- Cue first source, terminal, DOM render, and animation frame.
- Player wait/stall/seek events and dropped frames.

No Provider raw payload, credentials, cookies, or signed URLs are persisted.
Subtitle and translation text are not persisted.

## Primary verdict

For each marker Cue, match its first DOM render to the nearest detected flash.
The signed error is:

`DOM first render monotonic - flash frame monotonic`

- Pass: absolute P95 <= 250 ms, maximum <= 500 ms, and no persistent post-stall
  step larger than 250 ms.
- Fail: thresholds are exceeded by at least two markers, or the post-stall
  median remains shifted by more than 250 ms.

The server mapping cross-check is `cue.tStart - flash Playback Wall Time`.
Anchor residual is reported beside these values but is never used as proof of
semantic misalignment.

