const { app, BrowserWindow, protocol, net, session, Menu, dialog, shell, screen } = require('electron');
const path = require('node:path');
const fs = require('node:fs/promises');
const fsSync = require('node:fs');
const { performance } = require('node:perf_hooks');
const { spawn } = require('node:child_process');
const { startBackend, createDevLog, localStamp } = require('./backend.cjs');
const { defaultLogPath } = require('./devlog.cjs');
const { createUpdater } = require('./updater.cjs');

if (process.platform === 'win32') app.setAppUserModelId('io.github.antiranger.lingerlens');
const { startMainHealthProbe } = require('./health.cjs');

protocol.registerSchemesAsPrivileged([{ scheme: 'lingerlens', privileges: {
  standard: true, secure: true, supportFetchAPI: true, stream: true, corsEnabled: true,
} }]);
const smoke = process.argv.includes('--smoke-test') && process.env.LINGERLENS_SMOKE_OUTPUT;
/*
 * 默认走 Chromium 的正常渲染路径，软件渲染只做兜底。
 *
 * 这里原本无条件 appendSwitch('disable-gpu')。同机、同一直播、同一应用、各 10
 * 分钟的实测对照（只差这一个开关）：
 *
 *   不传 --disable-gpu： p95 8.3ms    最慢 64.8ms    >500ms 0 次     丢帧 0.06%
 *   传   --disable-gpu： p95 17784ms  最慢 43407ms   >500ms 249 次   丢帧 26.46%
 *
 * 代价不止在画面上：连本机 3 毫秒就能答完的 /api/status 都被拖成 43 秒，整个系统
 * 的输入响应一起变钝。
 *
 * 别把结论说成"打开硬件加速"：实测这台机器两条路都不做硬件解码
 * （gpu_compositing 与 video_decode 都是 disabled_software）。差别在于
 * --disable-gpu 会把 Chromium 赶进一条降级的纯软渲染路径；不传它时走的是正常
 * 路径（GPU 进程 + 多线程光栅），底层即便仍是软件 GL 也快得多。
 *
 * LINGERLENS_DISABLE_GPU=1 手动强制软解；LINGERLENS_GPU_FALLBACK=1 是自动兜底
 * 重启时给自己打的标记，保证只退一次。
 */
const gpuForcedOff = process.env.LINGERLENS_DISABLE_GPU === '1';
const gpuAlreadyFellBack = process.env.LINGERLENS_GPU_FALLBACK === '1';
if (gpuForcedOff || gpuAlreadyFellBack) {
  app.commandLine.appendSwitch('disable-gpu');
} else if (process.env.LINGERLENS_GPU_EXPERIMENTAL === '1') {
  // Driver overrides are opt-in experiments, never the release default.
  app.commandLine.appendSwitch('ignore-gpu-blocklist');
  app.commandLine.appendSwitch('enable-gpu-rasterization');
}

/*
 * 关掉 Chromium 的无障碍树。
 *
 * 划词/截图翻译、输入法一类 UI Automation 客户端一碰窗口，Chromium 就会打开
 * 无障碍树：每条弹幕、每句字幕都要在浏览器主线程里同步更新树并向外广播事件。
 * 弹幕密集的直播下主线程被原生代码吃满（V8 画像只看到 (idle)），整个桌面的
 * 鼠标键盘跟着发钝。同机、同一直播（zackrawrr）、只差这一个开关的实测：
 *
 *   开着： Browser cpuMs 最高 4706/5s   timerLate 最高 3952ms   约 2 分钟卡死
 *   关掉： Browser cpuMs 最高  483/5s   timerLate 最高   15ms   5 分钟无卡顿
 *
 * 本应用是直播播放器，不面向屏幕阅读器；LINGERLENS_ACCESSIBILITY=1 可恢复。
 */
if (process.env.LINGERLENS_ACCESSIBILITY !== '1') {
  app.commandLine.appendSwitch('disable-renderer-accessibility');
}

function fallBackToSoftwareRendering(reason) {
  console.log(`[main] falling back to software rendering (${reason}); relaunching once`);
  process.env.LINGERLENS_GPU_FALLBACK = '1';
  // 单实例锁必须先释放，否则新进程会因为拿不到锁而立刻退出。
  try { app.releaseSingleInstanceLock(); } catch { /* older Electron builds */ }
  app.relaunch();
  app.exit(0);
}

/*
 * 兜底的判据是 GPU 进程异常退出，不是 app.getGPUFeatureStatus()。
 *
 * 那个状态是"当前快照"，在 whenReady 之后立刻读往往还没稳定：实测本机在完全
 * 没传 --disable-gpu 的情况下，它照样把 compositing / rasterization /
 * video_decode 全报成 disabled_software。拿它当判据，会把本来能走正常渲染路径
 * 的机器误判成必须软解——那正是这个性能问题本身。
 *
 * 真正说明"这台机器起不来 GPU"的信号，是 GPU 进程非正常退出。
 */
/*
 * `app.getAppMetrics()` is the only per-process CPU breakdown Electron exposes.
 * `[main-perf]` alone said the whole main process burned 1.4 cores; this says
 * which of Browser / GPU / Utility / Tab spent it.
 *
 * `percentCPUUsage` is not usable here -- measured on Electron 44 it reports 0
 * for every process even after seconds of work -- so the delta is taken from
 * `cumulativeCPUUsage` (seconds) against the previous window. That also makes
 * the numbers directly comparable with the `cpuMs` in the same summary, which
 * is what makes "7031ms total, 6800ms of it the Browser process" readable.
 *
 * Compacted here rather than in health.cjs so that the probe stays a dumb
 * recorder and keeps its Electron-free unit test.
 */
let previousAppMetrics = null;
function compactAppMetrics() {
  try {
    const previous = previousAppMetrics || new Map();
    const next = new Map();
    const rows = app.getAppMetrics().map(entry => {
      const memory = entry.memory || {};
      const cpu = entry.cpu || {};
      const cumulative = Number(cpu.cumulativeCPUUsage) || 0;
      const before = previous.get(entry.pid);
      next.set(entry.pid, cumulative);
      const row = { pid: entry.pid,
        type: entry.serviceName && entry.serviceName !== entry.type
          ? `${entry.type}:${entry.serviceName}` : entry.type,
        cpuMs: before === undefined ? null : Math.round((cumulative - before) * 1000),
        wakeups: Math.round(Number(cpu.idleWakeupsPerSecond) || 0),
        wsMB: Math.round((Number(memory.workingSetSize) || 0) / 1024) };
      if (Number.isFinite(memory.privateBytes)) row.privMB = Math.round(memory.privateBytes / 1024);
      return row;
    });
    previousAppMetrics = next;
    return rows;
  } catch { return null; }
}

/*
 * 代理流量画像。
 *
 * 渲染进程的每个请求都要经过 protocol.handle（CSP 是 connect-src 'self'），
 * 而响应体是原生流泵在推——读代码看不出它被调用了多少次。所以这里记四样：
 *
 *   n   请求数
 *   kb  content-length 报出的字节。流式响应常常没有这个头，所以它经常是 0，
 *       不能拿它当流量看。
 *   ch  响应体被切成了多少块。这一项才是重点：如果原生泵每块只递几十字节，
 *       一个 8 Mbps 的流就变成每秒上万次跨进程搬运，而字节数看上去完全正常。
 *   ms  从 net.fetch 返回到流读完的总耗时。
 *
 * 用 TransformStream 直通计数，不用 tee：tee 会把内存流量翻倍，等于给正在被
 * 观察的那条路径加负担。
 */
const NET_TOP = 4;
/*
 * LINGERLENS_PROXY_BUFFER=1 让代理把响应体整体读进内存再交给渲染进程，而不是
 * 流式回传。这是给卡顿定位做的实验开关：卡顿现场主线程在原生代码里烧满一个核，
 * 而主进程唯一随播放伸缩的工作就是这条"把原生流交给自定义协议"的路。如果改成
 * 缓冲卡顿就消失，那答案就在这一步上，不需要再去读 ETW 的原生栈。
 */
const PROXY_BUFFER = process.env.LINGERLENS_PROXY_BUFFER === '1';
let netWindow = new Map();
function netLabel(pathname) {
  const folded = String(pathname).replace(/[0-9a-f]{8,}/gi, '#').replace(/\d+/g, '#').replace(/#+/g, '#');
  return folded.length > 30 ? `${folded.slice(0, 29)}…` : folded;
}
function netRowFor(pathname, startedAt, headersAt = performance.now()) {
  const key = netLabel(pathname);
  const row = netWindow.get(key) || { n: 0, kb: 0, ch: 0, fms: 0, ms: 0, max: 0 };
  netWindow.set(key, row);
  row.n += 1;
  // net.fetch 返回到这里的时间，和 ms（到流读完）分开记。卡顿现场这两个数差了
  // 三个数量级——不分开，就分不清是"后端答得慢"还是"我们把响应体交出去这一段慢"。
  row.fms += Math.max(0, headersAt - startedAt);
  return row;
}
function finishNetRow(row, startedAt) {
  const ms = performance.now() - startedAt;
  row.ms += ms;
  if (ms > row.max) row.max = ms;
}
function countProxyStream(pathname, body, startedAt) {
  const row = netRowFor(pathname, startedAt);
  if (!body) { finishNetRow(row, startedAt); return body; }
  // flush 和 cancel 都可能到达（正常读完，或渲染进程切台取消），只记一次。
  let finished = false;
  const done = () => { if (finished) return; finished = true; finishNetRow(row, startedAt); };
  return body.pipeThrough(new TransformStream({
    transform(chunk, controller) {
      row.ch += 1;
      row.kb += (chunk && chunk.byteLength ? chunk.byteLength : 0) / 1024;
      controller.enqueue(chunk);
    },
    flush: done,
    cancel: done,
  }));
}
function noteProxyBuffer(pathname, bytes, startedAt, headersAt) {
  const row = netRowFor(pathname, startedAt, headersAt);
  row.kb += (Number(bytes) || 0) / 1024;
  finishNetRow(row, startedAt);
}
function compactNetMetrics() {
  const total = { n: 0, kb: 0, ch: 0, fms: 0, ms: 0 };
  for (const row of netWindow.values()) {
    total.n += row.n; total.kb += row.kb; total.ch += row.ch;
    total.fms += row.fms; total.ms += row.ms;
  }
  const top = [...netWindow.entries()]
    .sort((a, b) => b[1].ms - a[1].ms || b[1].ch - a[1].ch)
    .slice(0, NET_TOP)
    .map(([path, row]) => [path, row.n, Math.round(row.kb), row.ch,
      Math.round(row.fms), Math.round(row.ms), Math.round(row.max)]);
  netWindow = new Map();
  const summary = { n: total.n, kb: Math.round(total.kb), ch: total.ch,
    fms: Math.round(total.fms), ms: Math.round(total.ms), paths: top.length, top };
  if (PROXY_BUFFER) summary.buffered = 1;
  return summary;
}

function watchGpuProcessHealth() {
  app.on('child-process-gone', (_event, details) => {
    if (details?.type !== 'GPU' || details.reason === 'clean-exit') return;
    if (gpuForcedOff || gpuAlreadyFellBack) return;
    if (window) {
      // 会话已经跑起来了，重启会打断用户；GPU 进程会被 Chromium 自己拉起。
      console.log(`[main] GPU process gone after startup (${details.reason}); keeping this session`);
      return;
    }
    fallBackToSoftwareRendering(`gpu process gone: ${details.reason}`);
  });
}
// Explicit local validation profiles never replace the installed app's settings.
if (!smoke && process.env.LINGERLENS_DATA_DIR) {
  const dataDir = process.env.LINGERLENS_DATA_DIR;
  if (!path.isAbsolute(dataDir)) throw new Error('LINGERLENS_DATA_DIR must be absolute');
  fsSync.mkdirSync(dataDir, { recursive: true });
  app.setPath('userData', dataDir);
}
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
  // The checksum-verified NSIS updater is Windows-only. macOS uses the release DMG.
  if (process.platform !== 'win32') {
    const update = { status: 'unsupported', error: 'Download macOS updates from GitHub Releases.' };
    return json(url.pathname === '/api/app-update' ? { ...buildInfo(), update } : update);
  }
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
      void (async () => { try { await backend?.stop(); } finally { await devLog?.close(2000); app.exit(); } })();
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
    devLog.arm(file,
      `\n[LingerLens ${stamp}] 从这一刻开始记录（此前的输出没有保存）\n`
      + `[LingerLens ${stamp}] backend: ${backend?.label || 'unknown'}\n`);
    const flushed = await devLog.flush(2000);
    return json({ ...devLog.state(), flushed });
  }
  if (url.pathname === '/api/diagnostics/stop') {
    if (request.method !== 'POST') return json({ error: 'method not allowed' }, 405);
    devLog.disarm();
    const flushed = await devLog.flush(2000);
    return json({ ...devLog.state(), flushed });
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
  const accepted = devLog.writeLines(payload?.lines);
  // `written` is a legacy accepted-record count, not a disk durability promise.
  return json({ ...devLog.state(), accepted, written: accepted });
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => {
    if (window) { if (window.isMinimized()) window.restore(); window.show(); window.focus(); }
  });
  app.on('before-quit', event => {
    if (quitting) return;
    event.preventDefault(); quitting = true;
    void (async () => { try { await backend?.stop(); } finally { await devLog?.close(2000); app.exit(process.exitCode ?? 0); } })();
  });
  app.on('window-all-closed', () => app.quit());
  watchGpuProcessHealth();
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
  // 只记录，不做判据：这个状态在启动初期还不稳定，见 watchGpuProcessHealth 的说明。
  const mode = gpuForcedOff ? 'software (requested)' : gpuAlreadyFellBack ? 'software (fallback)' : 'normal';
  console.log('[main] render path:', mode, JSON.stringify(app.getGPUFeatureStatus()));
  Menu.setApplicationMenu(process.platform === 'darwin'
    ? Menu.buildFromTemplate([{ role: 'appMenu' }, { role: 'editMenu' }, { role: 'windowMenu' }])
    : null);
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
    icon: path.join(__dirname, 'assets', 'icon.png'),
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
  // Deny everything except fullscreen. Electron lists 'fullscreen' among the
  // requestable permissions, so a blanket deny blocked the board's own
  // fullscreen button -- and requestFullscreen() never settled, neither
  // resolving nor rejecting, which is why the button looked dead rather than
  // refused. Nothing else on this page needs a permission.
  session.defaultSession.setPermissionRequestHandler((_contents, permission, callback) => {
    callback(permission === 'fullscreen');
  });
  // Permanent, not conditional: the destination can be switched on later from
  // the diagnostics bar, which is the only way a packaged build can log at all.
  devLog = createDevLog();
  startMainHealthProbe(devLog, { appMetrics: compactAppMetrics, netMetrics: compactNetMetrics });
  backend = startBackend({ packaged: app.isPackaged, resources: process.resourcesPath,
    root: path.join(__dirname, '..'), dataDir: app.getPath('userData'),
    proxy: await desktopProxy(), log: devLog,
    onExit: () => { if (endpoint && !quitting) void fail(); } });
  endpoint = await backend.ready;
  if (quitting) return;
  // Use Chromium's network stack so release downloads follow the desktop's
  // system proxy, just like the player's other desktop requests.
  updater = createUpdater({ currentVersion: app.getVersion(), fetch: net.fetch.bind(net) });
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
    const startedAt = performance.now();
    try {
      const response = await net.fetch(target, { method: request.method, headers,
        body: ['GET', 'HEAD'].includes(request.method) ? undefined : await request.arrayBuffer(), redirect: 'error' });
      const headersAt = performance.now();
      const tracing = devLog.state().active;
      const resultHeaders = new Headers(response.headers);
      resultHeaders.set('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; frame-src 'none'; base-uri 'none'");
      if (PROXY_BUFFER) {
        const buffer = await response.arrayBuffer();
        if (tracing) noteProxyBuffer(url.pathname, buffer.byteLength, startedAt, headersAt);
        return new Response(buffer, { status: response.status, headers: resultHeaders });
      }
      return new Response(tracing ? countProxyStream(url.pathname, response.body, startedAt) : response.body,
        { status: response.status, headers: resultHeaders });
    } catch { return new Response('Backend unavailable', { status: 503 }); }
  });
  await window.loadURL('lingerlens://app/');
  // Check once per run, well after startup so it never competes with the first
  // stream a user opens. Failures are reported, never retried in a loop: an
  // update check that hammers a dead host is worse than no update check.
  if (process.platform === 'win32' && !smoke) setTimeout(() => { void updater?.check(); }, 10000);
  if (smoke) {
    const { runSmoke } = require('./smoke.cjs');
    await runSmoke({ window, dataDir: app.getPath('userData'), outputDir: smoke, backendPid: backend.pid,
      ffmpeg: app.isPackaged ? path.join(process.resourcesPath, 'backend', '_internal', 'bin', process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg') : 'ffmpeg' });
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
