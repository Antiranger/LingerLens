const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const config = require('../desktop/builder.cjs');
const manifest = require('../desktop/dependencies.json');
const { localStamp } = require('../desktop/backend.cjs');
const { createDevLog, defaultLogPath, MAX_LINES_PER_POST } = require('../desktop/devlog.cjs');
const updater = require('../desktop/updater.cjs');

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
  assert.ok(config.extraResources.some(r => r.from === 'build-desktop/backend/lingerlens-backend'));
});

test('desktop dependency download pins HTTPS and a full SHA-256', () => {
  assert.equal(new URL(manifest.ffmpeg.url).protocol, 'https:');
  assert.match(manifest.ffmpeg.sha256, /^[a-f0-9]{64}$/);
  assert.ok(manifest.ffmpeg.url.includes(manifest.ffmpeg.version));
});

// The backend's stderr can carry private stream URLs, so silence is the
// default and anything else has to be asked for by name.
test('dev logging stays off unless something asks for it', () => {
  const original = process.stderr.write;
  const seen = [];
  process.stderr.write = chunk => { seen.push(chunk); return true; };
  try {
    for (const value of [undefined, '', '  ', '0', 'off', 'OFF', 'false', 'no']) {
      const log = createDevLog({ LINGERLENS_BACKEND_LOG: value });
      assert.equal(log.state().active, false, `${value} must not arm`);
      assert.equal(log.state().enabled, false, `${value} must not write a file`);
      log.write('dropped\n');
    }
    assert.deepEqual(seen, [], 'silence means stderr too, not just no file');
  } finally {
    process.stderr.write = original;
  }
});

test('dev logging accepts a boolean switch and a file path', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lingerlens-log-'));
  const original = process.stderr.write;
  const seen = [];
  process.stderr.write = chunk => { seen.push(chunk); return true; };
  try {
    const terminal = createDevLog({ LINGERLENS_BACKEND_LOG: '1' });
    assert.equal(terminal.state().enabled, false, 'terminal-only writes no file');
    assert.equal(terminal.state().active, true);
    terminal.write('to terminal\n');
    assert.deepEqual(seen, ['to terminal\n']);

    const target = path.join(dir, 'backend.log');
    const tee = createDevLog({ LINGERLENS_BACKEND_LOG: target });
    assert.deepEqual(tee.state(),
      { enabled: true, file: target, error: null, lines: 0, terminalOnly: false, active: true });
    tee.write('to both\n');
    assert.deepEqual(seen, ['to terminal\n', 'to both\n']);
    assert.equal(fs.readFileSync(target, 'utf8'), 'to both\n');
  } finally {
    process.stderr.write = original;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

/*
 * The packaged build has no launcher script to set an environment variable
 * before start-up, so this is the only route a normal user has: arm it from
 * the diagnostics bar while the app is already running.
 */
test('logging can be armed and disarmed while the app is running', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lingerlens-log-'));
  const original = process.stderr.write;
  process.stderr.write = () => true;
  try {
    const log = createDevLog({});
    assert.equal(log.state().active, false, 'a bare app records nothing');

    const first = path.join(dir, 'first.log');
    log.arm(first, 'header one\n');
    assert.deepEqual(log.state(),
      { enabled: true, file: first, error: null, lines: 0, terminalOnly: false, active: true });
    assert.equal(fs.readFileSync(first, 'utf8'), 'header one\n', 'the header lands immediately');
    log.write('backend said something\n');

    // Disarming must stop the writes rather than just hide the badge.
    log.disarm();
    assert.equal(log.state().active, false);
    assert.equal(log.state().file, null);
    log.write('after disarm\n');
    assert.equal(fs.readFileSync(first, 'utf8'), 'header one\nbackend said something\n');

    // Re-arming starts a new file with its own line count.
    const second = path.join(dir, 'second.log');
    log.arm(second, 'header two\n');
    assert.equal(log.state().lines, 0);
    assert.equal(fs.readFileSync(second, 'utf8'), 'header two\n');
    assert.equal(fs.readFileSync(first, 'utf8'), 'header one\nbackend said something\n',
      'the earlier file is left alone');
  } finally {
    process.stderr.write = original;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('a user-requested log lands next to the app data', () => {
  const file = defaultLogPath('C:\\Users\\someone\\AppData\\Roaming\\lingerlens',
    new Date(2026, 8, 15, 16, 24, 1));
  assert.equal(file,
    path.join('C:\\Users\\someone\\AppData\\Roaming\\lingerlens', 'logs', 'lingerlens-20260915-162401.log'));
  // No colons anywhere: the stamp goes into a filename on Windows.
  assert.doesNotMatch(path.basename(file), /[:*?"<>|]/);
});

/*
 * The whole point of the switch is to leave evidence behind. It used to fail
 * open: a path whose directory did not exist fell back to the terminal, and
 * Electron is a GUI-subsystem binary with no terminal attached -- so a run
 * could log nothing for an hour and never say so.
 */
test('a dev log path creates its parent directory instead of silently doing nothing', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lingerlens-log-'));
  const original = process.stderr.write;
  process.stderr.write = () => true;
  try {
    const target = path.join(dir, 'nested', 'deeper', 'run.log');
    const log = createDevLog({ LINGERLENS_BACKEND_LOG: target });
    assert.equal(log.state().error, null);
    assert.equal(log.state().enabled, true);
    log.write('created\n');
    assert.equal(fs.readFileSync(target, 'utf8'), 'created\n');
  } finally {
    process.stderr.write = original;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('an unwritable dev log path keeps the player alive but reports the failure', () => {
  const original = process.stderr.write;
  process.stderr.write = () => true;
  try {
    // A directory cannot be opened for appending on Windows, which makes this a
    // portable way to force the failure without touching permissions.
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lingerlens-log-'));
    try {
      const log = createDevLog({ LINGERLENS_BACKEND_LOG: dir });
      const state = log.state();
      assert.equal(state.enabled, false);
      assert.equal(state.file, dir, 'the path that failed is still reportable');
      assert.match(state.error, /^[A-Z]+:/, 'the error keeps its code');
      assert.doesNotThrow(() => log.write('still alive\n'));
      assert.equal(log.writeLines(['nope']), 0);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  } finally {
    process.stderr.write = original;
  }
});

test('diagnostic lines are capped, flattened and counted', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lingerlens-log-'));
  const original = process.stderr.write;
  process.stderr.write = () => true;
  try {
    const target = path.join(dir, 'run.log');
    const log = createDevLog({ LINGERLENS_BACKEND_LOG: target });
    assert.equal(log.writeLines([]), 0, 'nothing to write is not a write');
    assert.equal(log.writeLines(['  ', '\n']), 0, 'blank entries are not records');
    assert.equal(log.writeLines(['a\r\nb']), 1, 'an embedded newline must not forge a line');
    assert.equal(log.state().lines, 1);
    log.writeLines(Array.from({ length: 500 }, (_, index) => `line ${index}`));
    assert.equal(log.state().lines, 1 + MAX_LINES_PER_POST, 'a single post is bounded');
    const written = fs.readFileSync(target, 'utf8').split('\n').filter(Boolean);
    assert.equal(written.length, 1 + MAX_LINES_PER_POST);
    assert.deepEqual(written.slice(0, 2), ['a b', 'line 0']);
  } finally {
    process.stderr.write = original;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('a dev log that disappears mid-run stops writing but never throws', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lingerlens-log-'));
  const original = process.stderr.write;
  process.stderr.write = () => true;
  try {
    const target = path.join(dir, 'run.log');
    const log = createDevLog({ LINGERLENS_BACKEND_LOG: target });
    log.write('before\n');
    // An entire directory removed underneath a long run, e.g. someone tidying up.
    fs.rmSync(dir, { recursive: true, force: true });
    assert.doesNotThrow(() => log.write('after\n'));
    const state = log.state();
    assert.equal(state.enabled, false, 'a broken file must not keep claiming to record');
    assert.match(state.error, /^[A-Z]+:/);
    assert.equal(log.writeLines(['nope']), 0);
  } finally {
    process.stderr.write = original;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('log headers carry local wall-clock time, not UTC', () => {
  const stamp = localStamp(new Date(2026, 8, 15, 11, 37, 50));
  assert.equal(stamp, '2026-09-15 11:37:50');
  assert.equal(localStamp(new Date(2026, 0, 2, 3, 4, 5)), '2026-01-02 03:04:05');
});

/* ── 更新器 ──
   An update offer is the one piece of remote input that ends in code execution,
   so the validation and the hash check are the parts worth pinning. */

test('version comparison treats a pre-release as older than its release', () => {
  assert.equal(updater.isNewer('0.2.0', '0.1.0'), true);
  assert.equal(updater.isNewer('0.1.0', '0.1.0'), false);
  assert.equal(updater.isNewer('0.1.0', '0.2.0'), false);
  assert.equal(updater.isNewer('1.0.0', '0.99.99'), true);
  assert.equal(updater.isNewer('0.1.1', '0.1'), true);
  assert.equal(updater.isNewer('v0.2.0', '0.1.0'), true, 'a leading v is not a version');
  assert.equal(updater.isNewer('0.2.0-rc.1', '0.2.0'), false, 'an rc must not look newer than the release');
  assert.equal(updater.isNewer('0.2.0', '0.2.0-rc.1'), true);
  assert.equal(updater.isNewer('garbage', '0.1.0'), false, 'an unparseable version is never newer');
  assert.equal(updater.parseVersion('not-a-version'), null);
});

test('a manifest is rejected unless every field the installer step needs is sound', () => {
  const good = {
    version: '0.2.0',
    notes: 'fixes',
    installer: { url: 'https://example.test/a.exe', sha256: 'a'.repeat(64), size: 1024 },
  };
  assert.ok(updater.readManifest(good));
  const broken = [
    ['http url', { ...good, installer: { ...good.installer, url: 'http://example.test/a.exe' } }],
    ['file url', { ...good, installer: { ...good.installer, url: 'file:///C:/a.exe' } }],
    ['no url', { ...good, installer: { ...good.installer, url: undefined } }],
    ['short sha', { ...good, installer: { ...good.installer, sha256: 'abc' } }],
    ['non-hex sha', { ...good, installer: { ...good.installer, sha256: 'z'.repeat(64) } }],
    ['zero size', { ...good, installer: { ...good.installer, size: 0 } }],
    ['string size', { ...good, installer: { ...good.installer, size: 'big' } }],
    ['no installer', { version: '0.2.0' }],
    ['bad version', { ...good, version: 'latest' }],
    ['not an object', 'nope'],
    ['null', null],
  ];
  for (const [label, payload] of broken) {
    assert.equal(updater.readManifest(payload), null, `${label} must be rejected`);
  }
});

test('the manifest url is fixed unless a trusted override is given', () => {
  assert.equal(updater.manifestUrl({}), updater.DEFAULT_MANIFEST_URL);
  assert.equal(updater.manifestUrl({ LINGERLENS_UPDATE_URL: 'http://evil.test/m.json' }), updater.DEFAULT_MANIFEST_URL,
    'a plain-http override must not be honoured');
  assert.equal(updater.manifestUrl({ LINGERLENS_UPDATE_URL: 'file:///C:/m.json' }), updater.DEFAULT_MANIFEST_URL);
  assert.equal(updater.manifestUrl({ LINGERLENS_UPDATE_URL: 'https://local.test/m.json' }), 'https://local.test/m.json');
  assert.equal(updater.manifestUrl({ LINGERLENS_UPDATE_URL: 'http://127.0.0.1:8080/m.json' }), 'http://127.0.0.1:8080/m.json',
    'loopback is how the updater gets tested without publishing a release');
  assert.equal(updater.manifestUrl({ LINGERLENS_UPDATE_URL: 'http://localhost:8080/m.json' }), 'http://localhost:8080/m.json');
  assert.equal(updater.isTrustedManifestUrl('http://192.168.1.5/m.json'), false, 'a LAN address is not loopback');
});

function manifestFor(version, url, bytes, sha) {
  return { version, installer: { url, sha256: sha, size: bytes.length } };
}

function fetchJsonOnce(payload, status = 200) {
  return async () => ({ ok: status === 200, status, json: async () => payload });
}

function fetchBytes(bytes, { status = 200 } = {}) {
  return async () => ({
    ok: status === 200, status,
    headers: { get: () => String(bytes.length) },
    body: (async function* chunks() { yield bytes; })(),
  });
}

test('checking reports current, available and failure without throwing', async () => {
  const digest = crypto.createHash('sha256').update('installer-bytes').digest('hex');
  const url = 'https://example.test/LingerLens.exe';

  const current = updater.createUpdater({ currentVersion: '0.2.0', fetch: fetchJsonOnce(manifestFor('0.2.0', url, Buffer.from('installer-bytes'), digest)) });
  assert.equal((await current.check()).status, 'current');

  const available = updater.createUpdater({ currentVersion: '0.1.0', fetch: fetchJsonOnce(manifestFor('0.2.0', url, Buffer.from('installer-bytes'), digest)) });
  const state = await available.check();
  assert.equal(state.status, 'available');
  assert.equal(state.update.version, '0.2.0');

  const offline = updater.createUpdater({ currentVersion: '0.1.0', fetch: async () => { throw new Error('getaddrinfo ENOTFOUND'); } });
  const failed = await offline.check();
  assert.equal(failed.status, 'failed');
  assert.match(failed.error, /ENOTFOUND/);

  const junk = updater.createUpdater({ currentVersion: '0.1.0', fetch: async () => ({ ok: true, status: 200, json: async () => { throw new Error('not json'); } }) });
  assert.equal((await junk.check()).status, 'failed');
});

test('a download whose bytes match the manifest is renamed into place', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lingerlens-update-'));
  const bytes = Buffer.from('installer-bytes'.repeat(100));
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  const destination = path.join(dir, 'setup.exe');
  try {
    const result = await updater.downloadVerified({
      url: 'https://example.test/a.exe', sha256: digest, size: bytes.length, destination,
      fetchImpl: fetchBytes(bytes),
    });
    assert.equal(result.bytes, bytes.length);
    assert.equal(fs.readFileSync(destination).toString(), bytes.toString());
    assert.ok(!fs.existsSync(`${destination}.part`), 'no partial file is left behind');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('a download that fails verification leaves no runnable file behind', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lingerlens-update-'));
  const bytes = Buffer.from('the real bytes');
  const destination = path.join(dir, 'setup.exe');
  const cases = [
    ['checksum mismatch', { sha256: 'b'.repeat(64), size: bytes.length }],
    ['short download', { sha256: crypto.createHash('sha256').update(bytes).digest('hex'), size: bytes.length + 5 }],
  ];
  try {
    for (const [label, spec] of cases) {
      await assert.rejects(
        updater.downloadVerified({
          url: 'https://example.test/a.exe', destination, fetchImpl: fetchBytes(bytes), ...spec,
        }),
        (error) => /mismatch|short download/.test(error.message),
        label);
      assert.ok(!fs.existsSync(destination), `${label}: the installer must not exist`);
      assert.ok(!fs.existsSync(`${destination}.part`), `${label}: the partial file must be cleaned up`);
    }
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('a server that lies about the size is rejected before anything is written', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lingerlens-update-'));
  const destination = path.join(dir, 'setup.exe');
  const bytes = Buffer.from('x');
  try {
    await assert.rejects(
      updater.downloadVerified({
        url: 'https://example.test/a.exe', sha256: 'a'.repeat(64), size: 999,
        destination, fetchImpl: fetchBytes(bytes),
      }),
      /size mismatch/);
    assert.ok(!fs.existsSync(destination));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('the full check-then-download path ends in ready with a verified installer', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lingerlens-update-'));
  const bytes = Buffer.from('a real installer, honest');
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  const manifest = manifestFor('9.9.9', 'https://example.test/a.exe', bytes, digest);
  const seen = [];
  const instance = updater.createUpdater({
    currentVersion: '0.1.0',
    fetch: fetchJsonOnce(manifest),
    download: (options) => updater.downloadVerified({ ...options, fetchImpl: fetchBytes(bytes) }),
    onChange: (state) => seen.push(state.status),
  });
  try {
    assert.equal((await instance.check()).status, 'available');
    const ready = await instance.download(path.join(dir, 'setup.exe'));
    assert.equal(ready.status, 'ready');
    assert.equal(fs.existsSync(ready.installerPath), true);
    /* Progress re-emits `downloading`, so compare the transitions, not every emit. */
    assert.deepEqual(seen.filter((status, index) => status !== seen[index - 1]),
      ['checking', 'available', 'downloading', 'ready']);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('downloading without an offered update fails rather than guessing', async () => {
  const instance = updater.createUpdater({ currentVersion: '0.1.0', fetch: fetchJsonOnce({ version: '0.0.1', installer: {} }) });
  await instance.check();
  const state = await instance.download(path.join(os.tmpdir(), 'never.exe'));
  assert.equal(state.status, 'failed');
  assert.match(state.error, /nothing to download/);
});
