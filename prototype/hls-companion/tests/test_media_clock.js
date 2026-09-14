const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const {
  createMediaClock,
  formatWallClockTime,
} = require(path.resolve(__dirname, "../web-player/media-clock.js"));

test("MediaClock uses hls.playingDate first, falls back to fragment programDateTime + offset, returns unavailable when PDT absent", () => {
  // 1. hls.playingDate preferred
  const hlsWithPlayingDate = {
    playingDate: new Date("2026-04-18T12:00:10.500Z"),
  };
  const videoMock = { currentTime: 15.0 };
  const clock1 = createMediaClock({
    getHls: () => hlsWithPlayingDate,
    getVideo: () => videoMock,
  });

  assert.equal(clock1.getPlayingWallTime(), 1776513610.5);
  assert.equal(clock1.isWallTimeAvailable(), true);

  // 2. Fragment PDT fallback
  const hlsWithFragment = {
    playingDate: null,
    currentLevel: 0,
    levels: [
      {
        details: {
          fragments: [
            { start: 10.0, duration: 4.0, programDateTime: Date.parse("2026-04-18T12:00:10.000Z") },
            { start: 14.0, duration: 4.0, programDateTime: Date.parse("2026-04-18T12:00:14.000Z") },
          ],
        },
      },
    ],
  };
  const videoMock2 = { currentTime: 15.5 };
  const clock2 = createMediaClock({
    getHls: () => hlsWithFragment,
    getVideo: () => videoMock2,
  });

  // start = 14.0, currentTime = 15.5 => offset = 1.5s. PDT = 12:00:14.000Z => 1776513615.5
  assert.equal(clock2.getPlayingWallTime(), 1776513615.5);
  assert.equal(clock2.isWallTimeAvailable(), true);

  // 3. When PDT unavailable, must return null (NEVER fallback to Date.now())
  const hlsNoPdt = {
    playingDate: null,
    levels: [{ details: { fragments: [{ start: 0, duration: 4.0 }] } }],
  };
  const clock3 = createMediaClock({
    getHls: () => hlsNoPdt,
    getVideo: () => ({ currentTime: 2.0 }),
  });
  assert.equal(clock3.getPlayingWallTime(), null);
  assert.equal(clock3.isWallTimeAvailable(), false);
  assert.equal(clock3.formatTime(null), "--:--:--");

  // wallTimeForMediaPosition(pos) = playingWallTime + (pos - video.currentTime)
  assert.equal(clock1.wallTimeForMediaPosition(17.0), 1776513612.5);
});

test("formatWallClockTime formats epoch seconds into 24-hour HH:mm:ss in local time", () => {
  const epoch = 1776513610.5; // April 18, 2026 12:00:10.500 UTC
  const formatted = formatWallClockTime(epoch);
  assert.match(formatted, /^\d{2}:\d{2}:\d{2}$/);
});
