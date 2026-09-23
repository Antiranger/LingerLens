const test = require('node:test');
const assert = require('node:assert/strict');
const { playerContext, sessionGlobals } = require('./player-harness.js');
const recovery = require('../web-player/playback-recovery.js');

function statusHarness(data, overrides = {}, functions = []) {
  const globals = sessionGlobals({ request: async () => data, ...overrides });
  const h = playerContext({
    state: { uiGeneration: 1, lastSessionState: 'running', observedMediaSessionId: 'A',
      stopRequested: false, sessionAction: null, browserLatency: 20, latestLevelDetails: { edge: 100 },
      sourceWasStalled: false, lastRecoverySeekAt: 0 },
    globals,
    functions: ['refreshStatus', 'estimateVideoLatency', ...functions],
  });
  return { ...h, ...globals };
}

test('playback lag follows the current frame between playlist updates', async () => {
  const h = statusHarness({ state: 'running', mediaSessionId: 'A', hiddenMediaSeconds: 3 });
  h.video.currentTime = 88;
  h.video.seekable = { length: 1, end: () => 100 };
  await h.call('refreshStatus');
  assert.equal(h.showError.count(), 0);
  assert.equal(h.el('playerDelay').textContent, '12s');
  assert.equal(h.updatePlaybackRecovery.calls[0][1], 12);
  h.video.currentTime = 90;
  await h.call('refreshStatus');
  assert.equal(h.el('playerDelay').textContent, '10s');
});

test('local delay compares one media clock even when playlists update at different times', async () => {
  const h = statusHarness({ state: 'running', mediaSessionId: 'A', hiddenMediaSeconds: 3,
    privateEdgeWallTime: 1120 }, { mediaClock: { playingWallTime: () => 1100 } });
  h.video.currentTime = 88;
  await h.call('refreshStatus');
  assert.equal(h.el('totalDelay').textContent, '20s');
});

test('missing playhead or translation samples are unknown rather than zero', async () => {
  const h = statusHarness({ state: 'running', mediaSessionId: 'A', hiddenMediaSeconds: 3,
    subtitles: { avgTranslationLatencyMs: null } });
  await h.call('refreshStatus');
  assert.equal(h.el('totalDelay').textContent, 'nulls');
  assert.equal(h.el('translationLatency').textContent, '—');
});

test('failed status polling clears cached measurements and recommendations', async () => {
  const h = statusHarness({}, { request: async () => { throw new Error('Failed to fetch'); } });
  h.el('totalDelay').textContent = '20s';
  await h.call('refreshStatus');
  assert.equal(h.el('totalDelay').textContent, '—');
  assert.equal(h.resetSubtitleBudget.count(), 1);
});

test('catch-up does not speed up playback based on mismatched playlist snapshots', async () => {
  const h = statusHarness({ state: 'running', mediaSessionId: 'A', hiddenMediaSeconds: 8,
    targetDelaySeconds: 15, privateEdgeWallTime: 1115, playlistReady: true, sourceStallSeconds: 0 },
  { mediaClock: { playingWallTime: () => 1100 }, window: recovery }, ['updatePlaybackRecovery']);
  h.video.currentTime = 88;
  await h.call('refreshStatus');
  assert.equal(h.showError.count(), 0);
  assert.equal(h.video.playbackRate, 1, 'the measured local lag is already at its target');
});
