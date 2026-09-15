const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const config = require('../desktop/builder.cjs');
const manifest = require('../desktop/dependencies.json');
const { backendLogSink, localStamp } = require('../desktop/backend.cjs');

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

// The backend's stderr can carry private stream URLs, so silence is the
// default and anything else has to be asked for by name.
test('backend logging stays off unless LAGLINGO_BACKEND_LOG asks for it', () => {
  for (const value of [undefined, '', '  ', '0', 'off', 'OFF']) {
    assert.equal(backendLogSink({ LAGLINGO_BACKEND_LOG: value }), null, `${value} must stay silent`);
  }
});

test('backend logging accepts a boolean switch and a file path', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'laglingo-log-'));
  const original = process.stderr.write;
  const seen = [];
  process.stderr.write = chunk => { seen.push(chunk); return true; };
  try {
    const terminal = backendLogSink({ LAGLINGO_BACKEND_LOG: '1' });
    terminal('to terminal\n');
    assert.deepEqual(seen, ['to terminal\n']);

    const target = path.join(dir, 'backend.log');
    const tee = backendLogSink({ LAGLINGO_BACKEND_LOG: target });
    tee('to both\n');
    assert.deepEqual(seen, ['to terminal\n', 'to both\n']);
    assert.equal(fs.readFileSync(target, 'utf8'), 'to both\n');
  } finally {
    process.stderr.write = original;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('an unwritable backend log path degrades instead of failing the player', () => {
  const sink = backendLogSink({ LAGLINGO_BACKEND_LOG: path.join(os.tmpdir(), 'laglingo-missing-dir', 'x.log') });
  assert.equal(typeof sink, 'function');
  assert.doesNotThrow(() => sink('still alive\n'));
});

test('log headers carry local wall-clock time, not UTC', () => {
  const stamp = localStamp(new Date(2026, 8, 15, 11, 37, 50));
  assert.equal(stamp, '2026-09-15 11:37:50');
  assert.equal(localStamp(new Date(2026, 0, 2, 3, 4, 5)), '2026-01-02 03:04:05');
});
