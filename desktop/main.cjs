const { app, BrowserWindow, protocol, net, session, Menu, dialog, shell, screen } = require('electron');
const path = require('node:path');
const fs = require('node:fs/promises');
const fsSync = require('node:fs');
const { spawn } = require('node:child_process');
const { startBackend, createDevLog, localStamp } = require('./backend.cjs');
const { defaultLogPath } = require('./devlog.cjs');
const { createUpdater } = require('./updater.cjs');

protocol.registerSchemesAsPrivileged([{ scheme: 'lingerlens', privileges: {
  standard: true, secure: true, supportFetchAPI: true, stream: true, corsEnabled: true,
} }]);
const smoke = process.argv.includes('--smoke-test') && process.env.LINGERLENS_SMOKE_OUTPUT;
// Some Windows environments lack a usable Chromium GPU DLL. The player is
// video-streaming work for FFmpeg, so software rendering is a safe fallback.
app.commandLine.appendSwitch('disable-gpu');
if (smoke) app.setPath('userData', path.resolve(smoke, 'user-data'));
let window;
let backend;
let devLog;
let quitting = false;
let endpoint;
let updater;

/*
 * Build identity. `app.getVersion()` alone cannot tell two builds of 0.1.0
 * apart, which is exactly the confusion that made a week-old install look like
 * a release without the diagnostics feature. The executable's own mtime is the
 * cheapest thing that actually distinguishes them.
 */
function buildInfo() {
  let builtAt = null;
  try {
    builtAt = fsSync.statSync(process.execPath).mtime.toISOString();
  } catch { /* stat can fail on an unusual install layout; not worth failing over */ }
  return { version: app.getVersion(), packaged: app.isPackaged, builtAt, electron: process.versions.electron };
}

/*
 * Relaunch after an update.
 *
 * The installer cannot replace a running executable, and `runAfterFinish` is
 * deliberately false so a fresh install does not launch itself. That leaves
 * nobody to start the app again, so wait for the installer to finish and start
 * it detached. `ping` delays without needing a console, unlike `timeout`.
 */
function scheduleRelaunch(target, seconds = 12) {
  if (process.platform !== 'win32' || !target) return;
  try {
    spawn('cmd.exe', ['/c', `ping -n ${seconds + 1} 127.0.0.1 >nul & start "" "${target}"`],
      { detached: true, stdio: 'ignore', windowsHide: true }).unref();
  } catch { /* If the relaunch cannot be scheduled the user still has a fresh install. */ }
}

async function handleAppUpdate(request, url) {
  const json = (body, status = 200) => new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
  if (url.pathname === '/api/app-update' && request.method === 'GET') {
    return json({ ...buildInfo(), update: updater.getState() });
  }
  if (url.pathname === '/api/app-update/check' && request.method === 'POST') {
    return json(await updater.check());
  }
  if (url.pathname === '/api/app-update/install' && request.method === 'POST') {
    const destination = path.join(app.getPath('temp'), `LingerLens-${updater.getState().update?.version || 'update'}-setup.exe`);
    const state = await updater.download(destination);
    if (state.status !== 'ready') return json(state, 502);
    try {
      spawn(state.installerPath, [], { detached: true, stdio: 'ignore', windowsHide: false }).unref();
    } catch (error) {
      return json({ ...state, status: 'failed', error: `无法运行安装程序：${error.message}` }, 502);
    }
    // Answer the renderer first, then step aside so the installer can replace
    // this executable, and arrange for the new build to come back up.
    setTimeout(() => {
      scheduleRelaunch(process.execPath);
      quitting = true;
      void (async () => { try { await backend?.stop(); } finally { app.exit(); } })();
    }, 750);
    return json({ ...state, status: 'installing', relaunchInSeconds: 12 });
  }
  return json({ error: 'not found' }, 404);
}

/*
 * The renderer's diagnostic timeline, and the controls that decide whether it
 * is being written down at all.
 *
 * The renderer is sandboxed with no filesystem access, and the main process is
 * the only side that owns the log file. So this rides the existing
 * `lingerlens://` protocol intercept rather than adding a preload script or an
 * IPC channel -- the renderer's privilege set stays exactly as it was.
 *
 * A GET doubles as the feature query: the renderer reads it once at start-up
 * and only posts records when `enabled` is true.
 */
async function handleDiagnostics(request, url) {
  const json = (body, status = 200) => new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
  const state = devLog.state();

  if (url.pathname === '/api/diagnostics/start') {
    if (request.method !== 'POST') return json({ error: 'method not allowed' }, 405);
    // A fresh file per arming: one session, one artifact to hand over.
    const file = defaultLogPath(app.getPath('userData'));
    const stamp = localStamp();
    // The start-up header only reaches somewhere if the environment variable
    // armed this before the window existed. Say plainly when it did not, so a
    // reader never assumes the file is a complete record of the session.
    return json(devLog.arm(file,
      `\n[LingerLens ${stamp}] 从这一刻开始记录（此前的输出没有保存）\n`
      + `[LingerLens ${stamp}] backend: ${backend?.label || 'unknown'}\n`));
  }
  if (url.pathname === '/api/diagnostics/stop') {
    if (request.method !== 'POST') return json({ error: 'method not allowed' }, 405);
    return json(devLog.disarm());
  }
  if (url.pathname === '/api/diagnostics/reveal') {
    if (request.method !== 'POST') return json({ error: 'method not allowed' }, 405);
    const target = state.file ? path.dirname(state.file) : app.getPath('userData');
    // shell.openPath resolves with a message on failure rather than rejecting.
    return json({ ...state, opened: await shell.openPath(target) });
  }

  if (request.method === 'GET') return json(state);
  if (request.method !== 'POST') return json({ error: 'method not allowed' }, 405);
  let payload;
  try {
    payload = await request.json();
  } catch {
    return json({ ...state, written: 0, error: state.error || 'invalid JSON body' }, 400);
  }
  return json({ ...state, written: devLog.writeLines(payload?.lines) });
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => {
    if (window) { if (window.isMinimized()) window.restore(); window.show(); window.focus(); }
  });
  app.on('before-quit', event => {
    if (quitting) return;
    event.preventDefault(); quitting = true;
    void (async () => { try { await backend?.stop(); } finally { app.exit(process.exitCode ?? 0); } })();
  });
  app.on('window-all-closed', () => app.quit());
  app.whenReady().then(start).catch(fail);
}

async function fail(error) {
  if (quitting) return;
  if (smoke) {
    await fs.mkdir(smoke, { recursive: true });
    await fs.writeFile(path.join(smoke, 'failure.json'), JSON.stringify({ error: error?.message || 'desktop startup or smoke failed' }));
    // A runner reads this exit code; quitting with zero made a failed smoke run
    // look exactly like a passing one.
    process.exitCode = 1;
    app.quit(); return;
  }
  await dialog.showMessageBox({ type: 'error', title: 'LingerLens',
    message: '播放器启动失败', detail: '请退出后重新打开。若仍然失败，请重新安装完整的 LingerLens 安装包。', buttons: ['退出'] });
  app.quit();
}

async function start() {
  Menu.setApplicationMenu(null);
  // The layout is a three-column desktop board with `body { min-width: 1080px }`
  // and its own 1240px breakpoint below which the live chat column is hidden.
  // The previous fixed 1440x920 came out at roughly 960 CSS px on a 150%-scaled
  // display, so a fresh install could not show all three columns and `minWidth`
  // let the window shrink far below the layout's own floor.
  //
  // Size from the work area (Electron reports it in DIPs, the same unit
  // BrowserWindow bounds use) and maximise whenever the screen is comfortably
  // wide enough, which also sidesteps any DPI-unit ambiguity.
  const { workAreaSize } = screen.getPrimaryDisplay();
  const fitsBoard = workAreaSize.width >= 1280;
  window = new BrowserWindow({
    width: Math.min(workAreaSize.width, 1680),
    height: Math.min(workAreaSize.height, 1000),
    minWidth: 1080, minHeight: 700,
    backgroundColor: '#151719', title: 'LingerLens', show: false,
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true,
      webSecurity: true, spellcheck: false } });
  window.once('ready-to-show', () => {
    if (fitsBoard) window.maximize();
    window.show();
  });
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https:\/\//i.test(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });
  window.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith('lingerlens://app/')) event.preventDefault();
  });
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  // Permanent, not conditional: the destination can be switched on later from
  // the diagnostics bar, which is the only way a packaged build can log at all.
  devLog = createDevLog();
  backend = startBackend({ packaged: app.isPackaged, resources: process.resourcesPath,
    root: path.join(__dirname, '..'), dataDir: app.getPath('userData'),
    proxy: await desktopProxy(), log: devLog,
    onExit: () => { if (endpoint && !quitting) void fail(); } });
  endpoint = await backend.ready;
  if (quitting) return;
  updater = createUpdater({ currentVersion: app.getVersion() });
  // Stable origin preserves localStorage even though the private backend port
  // changes. HLS stays streaming HTTP internally; no renderer Node privileges.
  protocol.handle('lingerlens', async request => {
    const url = new URL(request.url);
    if (url.hostname !== 'app' || url.port || url.username || url.password) return new Response('', { status: 403 });
    // Update control belongs to the main process: it is the only side that can
    // replace the executable, and nothing it does belongs in the backend.
    if (url.pathname.startsWith('/api/app-update')) return handleAppUpdate(request, url);
    // Diagnostics persistence is a main-process concern for the same reason:
    // only this side owns the log file.
    if (url.pathname.startsWith('/api/diagnostics')) return handleDiagnostics(request, url);
    const target = endpoint.origin + url.pathname + url.search;
    const headers = new Headers(request.headers);
    headers.set('X-LingerLens-Session', endpoint.token);
    headers.set('Origin', endpoint.origin);
    headers.delete('Host');
    try {
      const response = await net.fetch(target, { method: request.method, headers,
        body: ['GET', 'HEAD'].includes(request.method) ? undefined : await request.arrayBuffer(), redirect: 'error' });
      const resultHeaders = new Headers(response.headers);
      resultHeaders.set('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; frame-src 'none'; base-uri 'none'");
      return new Response(response.body, { status: response.status, headers: resultHeaders });
    } catch { return new Response('Backend unavailable', { status: 503 }); }
  });
  await window.loadURL('lingerlens://app/');
  // Check once per run, well after startup so it never competes with the first
  // stream a user opens. Failures are reported, never retried in a loop: an
  // update check that hammers a dead host is worse than no update check.
  setTimeout(() => { void updater?.check(); }, 10000);
  if (smoke) {
    const { runSmoke } = require('./smoke.cjs');
    await runSmoke({ window, dataDir: app.getPath('userData'), outputDir: smoke, backendPid: backend.pid,
      ffmpeg: app.isPackaged ? path.join(process.resourcesPath, 'backend', '_internal', 'bin', 'ffmpeg.exe') : 'ffmpeg' });
    app.quit();
  }
}

async function desktopProxy() {
  try {
    const result = await session.defaultSession.resolveProxy('https://www.youtube.com/');
    const match = String(result || '').match(/(?:PROXY|HTTPS|HTTP)\s+([^;\s]+)/i);
    if (match && match[1]) return `http://${match[1]}`;
  } catch { /* System proxy discovery is best effort; environment proxy still applies. */ }
  return null;
}
