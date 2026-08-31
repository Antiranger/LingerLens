# Live Delay Test Record

## Environment

- Date/time:
- Tester:
- Platform: YouTube Live / Bilibili Live
- Live URL or room ID:
- Stream type: ordinary / low latency / ultra-low latency / unknown
- Target delay: 5s / 10s
- Browser and version:
- OS:
- Network:
- Memory Saver enabled: yes / no
- Source tab discarded during run: yes / no
- Run duration:
- Viewer JSON file:
- Viewer CSV file:
- Source JSON file:

## Stream capability

- `seekable.length` became greater than zero: yes / no
- Observed seekable-window range:
- Manual rewind worked before extension control: yes / no
- Initial seek succeeded: yes / no
- Initial failure reason, if any:

## Disturbance observations

| Scenario | Timestamp | Visible result | Automatic recovery? | Manual action required? |
|---|---|---|---|---|
| Continuous playback | | | | |
| Pause 10s and resume | | | | |
| Manual jump to Live | | | | |
| Quality switch | | | | |
| Source in background | | | | |
| Memory Saver/discard check | | | | |

## Exported metrics

- Actual delay P50:
- Actual delay P95:
- Mean absolute target error:
- Ratio in target ±1s:
- Longest continuous stable period:
- Drift correction count:
- Auto-jump-to-live count:
- Video reload count:
- Buffering count:
- Source-tab stopped count:
- Manual recovery required:
- Failure reason:

## Qualitative notes

- Were hard corrections visible or audible?
- Did the platform's own latency controls change during the run?
- Did quality switching replace the video element or clear seekable history?
- Did either tab stop while backgrounded?
- Other anomalies:

## Classification

- Result: directly supported / conditionally supported / not directly supported
- Conditions:
- Required recovery strategy:
- Confidence and remaining gaps:
