const test = require('node:test');
const assert = require('node:assert/strict');
const config = require('../desktop/builder.cjs');
const manifest = require('../desktop/dependencies.json');

test('installer keeps user data and installs per user without starting itself', () => {
  assert.equal(config.nsis.perMachine, false);
  assert.equal(config.nsis.deleteAppDataOnUninstall, false);
  assert.equal(config.nsis.runAfterFinish, false);
});

test('desktop package inputs exclude private working directories', () => {
  const sources = [...config.files, ...config.extraResources.map(resource => resource.from)];
  for (const source of sources) {
    assert.ok(!source.includes('**'), 'Top-level input must be explicit');
    assert.doesNotMatch(source, /(^|\/)(docs|prototype|runtime|\.scratch|output)(\/|$)/);
  }
  assert.ok(config.extraResources.some(r => r.from === 'build-desktop/backend/laglingo-backend'));
});

test('desktop dependency download pins HTTPS and a full SHA-256', () => {
  assert.equal(new URL(manifest.ffmpeg.url).protocol, 'https:');
  assert.match(manifest.ffmpeg.sha256, /^[a-f0-9]{64}$/);
  assert.ok(manifest.ffmpeg.url.includes(manifest.ffmpeg.version));
});
