# Subtitle geometry and readability experiment

## Question

Do the 56-85 character Cues observed in the live run occupy too many lines or
too much of the video, and does splitting them near 42 source characters improve
the production overlay without causing stacked tail windows?

## Method

- Real Chromium renders the production player HTML, CSS, subtitle scheduler,
  and DOM renderer on an isolated Companion port.
- Synthetic public bilingual strings are fitted to source lengths 40, 56, 70,
  and 85, covering the live median-to-maximum range.
- Two viewport sizes (1600x1000 and 1100x760) and medium/large subtitle settings
  are measured.
- `current_single_cue` renders the full string as one Cue.
- `split_42` splits the source at word boundaries near 42 characters and
  distributes the same audio span proportionally. Translation slices are
  distributed by the same proportions.
- Each split part is measured alone, then all parts are placed on the production
  timeline and sampled just after each boundary to detect overlapping tail
  windows.

Only lengths and DOM geometry are persisted in results. The fixed public test
strings are part of the experiment source, not copied from a live stream.

## Measurements

- source and translation visual line counts;
- row height and percentage of player-stage height;
- simultaneous active rows and combined height at split boundaries;
- source/translation characters per audio second;
- calculated hold and audio-span duration.

## Interpretation

Splitting is useful only if it reduces per-row line count and total occupied
height without creating larger multi-row stacks at consecutive boundaries.
Character splitting cannot improve characters per audio second by itself; that
requires paraphrasing, earlier partial publication, or a longer display window.

