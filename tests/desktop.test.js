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

test('dev logging accepts explicit terminal mode and asynchronous file mode', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(),'lingerlens-log-'));
  const original=process.stderr.write; const seen=[];
  process.stderr.write=chunk=>{seen.push(chunk);return true;};
  try {
    const terminal=createDevLog({LINGERLENS_BACKEND_LOG:'1'});
    assert.equal(terminal.state().active,true);
    assert.equal(terminal.state().enabled,false);
    terminal.write('terminal');
    const target=path.join(dir,'run.log');
    const log=createDevLog({LINGERLENS_BACKEND_LOG:target});
    assert.equal(log.state().pending,true);
    log.write('file');
    assert.equal(await log.flush(),true);
    assert.equal(log.state().enabled,true);
    assert.equal(log.state().file,target);
    assert.equal(fs.readFileSync(target,'utf8'),'file');
    assert.deepEqual(seen,['terminal'],'file mode never blocks on redirected stderr');
    await log.close();
  } finally {process.stderr.write=original;fs.rmSync(dir,{recursive:true,force:true});}
});

test('logging can be armed and disarmed without cross-file contamination',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'lingerlens-log-'));
  const log=createDevLog({});
  try {
    const first=path.join(dir,'first.log'),second=path.join(dir,'second.log');
    log.arm(first,'header1'); log.write('before');
    log.disarm(); assert.equal(log.write('discarded'),false);
    log.arm(second,'header2'); log.write('after');
    assert.equal(await log.flush(),true);
    assert.equal(fs.readFileSync(first,'utf8'),'header1before');
    assert.equal(fs.readFileSync(second,'utf8'),'header2after');
    assert.equal(log.state().lines,0);
    await log.close(); assert.equal(log.state().active,false);
  } finally {await log.close();fs.rmSync(dir,{recursive:true,force:true});}
});

test('a user-requested log lands next to the app data',()=>{
  const file=defaultLogPath('user-data',new Date(2026,8,15,16,24,1));
  assert.equal(file,path.join('user-data','logs','lingerlens-20260915-162401.log'));
  assert.doesNotMatch(path.basename(file),/[:*?"<>|]/);
});

test('a dev log creates parent directories asynchronously',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'lingerlens-log-'));
  const target=path.join(dir,'nested','deeper','run.log');
  const log=createDevLog({LINGERLENS_BACKEND_LOG:target});
  try {
    log.write('created'); assert.equal(await log.flush(),true);
    assert.equal(log.state().error,null);
    assert.equal(fs.readFileSync(target,'utf8'),'created');
  } finally {await log.close();fs.rmSync(dir,{recursive:true,force:true});}
});

test('an unwritable path reports failure after initialization without throwing',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'lingerlens-log-'));
  const log=createDevLog({LINGERLENS_BACKEND_LOG:dir});
  try {
    assert.equal(await log.flush(),false);
    const state=log.state();
    assert.equal(state.enabled,false); assert.equal(state.pending,false);
    assert.equal(state.file,dir); assert.match(state.error,/^EISDIR:/);
    assert.doesNotThrow(()=>log.write('still alive'));
    assert.equal(log.writeLines(['nope']),0);
  } finally {await log.close();fs.rmSync(dir,{recursive:true,force:true});}
});

test('diagnostic lines are flattened, capped, accepted and eventually persisted',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'lingerlens-log-'));
  const target=path.join(dir,'run.log');
  const log=createDevLog({LINGERLENS_BACKEND_LOG:target});
  try {
    assert.equal(log.writeLines([]),0); assert.equal(log.writeLines([' ']),0);
    assert.equal(log.writeLines(['a'+String.fromCharCode(13,10)+'b']),1);
    log.writeLines(Array.from({length:500},(_,i)=>`line ${i}`));
    assert.equal(log.state().lines,1+MAX_LINES_PER_POST);
    assert.equal(await log.flush(),true);
    const written=fs.readFileSync(target,'utf8').split(String.fromCharCode(10)).filter(Boolean);
    assert.equal(written.length,1+MAX_LINES_PER_POST);
    assert.deepEqual(written.slice(0,2),['a b','line 0']);
    assert.equal(log.state().queuedBytes,0);
  } finally {await log.close();fs.rmSync(dir,{recursive:true,force:true});}
});

test('a removed log directory stops recording and reports an asynchronous error',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'lingerlens-log-'));
  const log=createDevLog({LINGERLENS_BACKEND_LOG:path.join(dir,'run.log')});
  try {
    log.write('before'); await log.flush();
    fs.rmSync(dir,{recursive:true,force:true});
    assert.doesNotThrow(()=>log.write('after'));
    assert.equal(await log.flush(),false);
    assert.equal(log.state().enabled,false);
    assert.match(log.state().error,/^[A-Z]+:/);
    assert.equal(log.writeLines(['nope']),0);
  } finally {await log.close();fs.rmSync(dir,{recursive:true,force:true});}
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
    ['fractional size', { ...good, installer: { ...good.installer, size: 1.5 } }],
    ['coerced size', { ...good, installer: { ...good.installer, size: '1024' } }],
    ['unsafe size', { ...good, installer: { ...good.installer, size: Number.MAX_SAFE_INTEGER + 1 } }],
    ['url credentials', { ...good, installer: { ...good.installer, url: 'https://user:password@example.test/a.exe' } }],
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

test('version identifiers cannot escape the installer directory and follow prerelease ordering', () => {
  for (const version of ['1.2.3-../../outside', '1.2.3-..\\outside', '1.2.3-x/y',
    '1.2.3-', '1.2.3-rc..1', '1.2.3-01', '01.2.3', '1.2.3.4', '9007199254740992.0.0']) {
    assert.equal(updater.parseVersion(version), null, version);
  }
  assert.equal(updater.compareVersions('1.2.3+build.7', '1.2.3'), 0);
  assert.equal(updater.compareVersions('1.2.3-rc.10', '1.2.3-rc.9'), 1);
  assert.equal(updater.compareVersions('1.2.3-rc.1', '1.2.3-rc'), 1);
  assert.equal(updater.compareVersions('1.2.3-1', '1.2.3-alpha'), -1);
  assert.equal(updater.compareVersions('1.2.3-rc.9+build', '1.2.3-rc.10'), -1);
});

test('a chunked download stops as soon as it exceeds the declared size', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lingerlens-overflow-'));
  const destination = path.join(dir, 'setup.exe');
  let chunksRead = 0;
  try {
    await assert.rejects(updater.downloadVerified({
      url: 'https://example.test/a.exe', sha256: 'a'.repeat(64), size: 4, destination,
      fetchImpl: async () => ({ ok: true, headers: { get: () => null },
        body: (async function* () { for (let i = 0; i < 10; i++) { chunksRead++; yield Buffer.from('1234'); } })() }),
    }), /exceeds manifest size/);
    assert.equal(chunksRead, 2);
    assert.equal(fs.existsSync(destination), false);
    assert.equal(fs.existsSync(`${destination}.part`), false);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('a failed refresh cannot reuse a previous update offer', async () => {
  let calls = 0;
  const instance = updater.createUpdater({ currentVersion: '0.1.0', fetch: async () => {
    if (++calls > 1) throw new Error('offline');
    return { ok: true, json: async () => manifestFor('0.2.0', 'https://example.test/a.exe', Buffer.from('x'), 'a'.repeat(64)) };
  } });
  assert.equal((await instance.check()).status, 'available');
  assert.equal((await instance.check()).status, 'failed');
  assert.equal(instance.getState().update, null);
  assert.match((await instance.download('unused.exe')).error, /nothing to download/);
});

test('repeated download and check requests do not race an in-progress download', async () => {
  let finish;
  let downloads = 0;
  const instance = updater.createUpdater({ currentVersion: '0.1.0',
    fetch: fetchJsonOnce(manifestFor('0.2.0', 'https://example.test/a.exe', Buffer.from('x'), 'a'.repeat(64))),
    download: async () => { downloads++; return new Promise(resolve => { finish = resolve; }); },
  });
  await instance.check();
  const pending = instance.download('unused.exe');
  assert.equal((await instance.download('unused.exe')).status, 'downloading');
  assert.equal((await instance.check()).status, 'downloading');
  assert.equal(downloads, 1);
  finish({ path: 'unused.exe', bytes: 1 });
  assert.equal((await pending).status, 'ready');
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
