const { app, BrowserWindow, protocol, net, session, Menu, dialog, shell } = require('electron');
const path = require('node:path');
const fs = require('node:fs/promises');
const { startBackend } = require('./backend.cjs');

protocol.registerSchemesAsPrivileged([{ scheme: 'laglingo', privileges: {
  standard: true, secure: true, supportFetchAPI: true, stream: true, corsEnabled: true,
} }]);
const smoke = process.argv.includes('--smoke-test') && process.env.LAGLINGO_SMOKE_OUTPUT;
// Some Windows environments lack a usable Chromium GPU DLL. The player is
// video-streaming work for FFmpeg, so software rendering is a safe fallback.
app.commandLine.appendSwitch('disable-gpu');
if (smoke) app.setPath('userData', path.resolve(smoke, 'user-data'));
let window;
let backend;
let quitting = false;
let endpoint;

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => {
    if (window) { if (window.isMinimized()) window.restore(); window.show(); window.focus(); }
  });
  app.on('before-quit', event => {
    if (quitting) return;
    event.preventDefault(); quitting = true;
    void (async () => { try { await backend?.stop(); } finally { app.exit(); } })();
  });
  app.on('window-all-closed', () => app.quit());
  app.whenReady().then(start).catch(fail);
}

async function fail(error) {
  if (quitting) return;
  if (smoke) {
    await fs.mkdir(smoke, { recursive: true });
    await fs.writeFile(path.join(smoke, 'failure.json'), JSON.stringify({ error: error?.message || 'desktop startup or smoke failed' }));
    app.quit(); return;
  }
  await dialog.showMessageBox({ type: 'error', title: 'LagLingo',
    message: '播放器启动失败', detail: '请退出后重新打开。若仍然失败，请重新安装完整的 LagLingo 安装包。', buttons: ['退出'] });
  app.quit();
}

async function start() {
  Menu.setApplicationMenu(null);
  window = new BrowserWindow({ width: 1440, height: 920, minWidth: 960, minHeight: 640,
    backgroundColor: '#151719', title: 'LagLingo', show: false,
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true,
      webSecurity: true, spellcheck: false } });
  window.once('ready-to-show', () => window.show());
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https:\/\//i.test(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });
  window.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith('laglingo://app/')) event.preventDefault();
  });
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  backend = startBackend({ packaged: app.isPackaged, resources: process.resourcesPath,
    root: path.join(__dirname, '..'), dataDir: app.getPath('userData'),
    proxy: await desktopProxy(),
    onExit: () => { if (endpoint && !quitting) void fail(); } });
  endpoint = await backend.ready;
  if (quitting) return;
  // Stable origin preserves localStorage even though the private backend port
  // changes. HLS stays streaming HTTP internally; no renderer Node privileges.
  protocol.handle('laglingo', async request => {
    const url = new URL(request.url);
    if (url.hostname !== 'app' || url.port || url.username || url.password) return new Response('', { status: 403 });
    const target = endpoint.origin + url.pathname + url.search;
    const headers = new Headers(request.headers);
    headers.set('X-LagLingo-Session', endpoint.token);
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
  await window.loadURL('laglingo://app/');
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
