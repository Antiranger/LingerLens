const { spawn } = require('node:child_process');
const { createInterface } = require('node:readline');
const { randomBytes } = require('node:crypto');
const path = require('node:path');
const { createDevLog } = require('./devlog.cjs');

// Local wall-clock rather than UTC: a long-run log gets read against what the
// clock on the wall said when something went wrong.
function localStamp(date = new Date()) {
  const pad = value => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function startBackend({ packaged, resources, root, dataDir, proxy = null, log = createDevLog(),
  onExit = () => {} }) {
  const command = packaged
    ? path.join(resources, 'backend', process.platform === 'win32' ? 'lingerlens-backend.exe' : 'lingerlens-backend')
    : (process.env.LINGERLENS_PYTHON || path.join(root, '.venv-desktop',
      ...(process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python'])));
  const args = packaged ? [] : [path.join(root, 'desktop', 'companion_entry.py')];
  args.push('--data-dir', dataDir);
  const env = { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' };
  // PyInstaller and the development venv must not import another Python install.
  delete env.PYTHONHOME;
  delete env.PYTHONPATH;
  const child = spawn(command, args, { cwd: packaged ? resources : root, env,
    windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
  const token = randomBytes(32).toString('hex');
  let stopped = false;
  let stopPromise;
  // The listener stays attached whether or not anything is armed: the user can
  // switch logging on from the UI long after start-up, and in 'off' mode write()
  // drops the chunk, which is exactly what resume() used to do. Attaching it
  // unconditionally is also what keeps a full pipe from stalling the child.
  log.write(`\n[LingerLens ${localStamp()}] backend: ${command} ${args.join(' ')}\n`);
  if (log.error) {
    // Someone asked for a file and did not get one. Say so in the log's own
    // place, because the UI reads this back through /api/diagnostics.
    log.write(`[LingerLens] log file unavailable -> ${log.error}\n`);
  }
  child.stderr.on('data', chunk => log.write(chunk));
  child.stdin.on('error', () => {});
  const exited = new Promise(resolve => child.once('exit', (code) => { resolve(); if (!stopped) onExit(code); }));
  const ready = new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('后台启动超时，请重试。')), 45000);
    const lines = createInterface({ input: child.stdout });
    const done = () => { clearTimeout(timeout); lines.close(); };
    child.once('error', () => { done(); reject(new Error('无法启动后台，请检查安装是否完整。')); });
    child.once('exit', () => { done(); reject(new Error('后台已退出，请检查安装是否完整。')); });
    lines.on('line', line => {
      try {
        const message = JSON.parse(line);
        // Windows venv python.exe is a redirector; the actual interpreter can
        // have another PID. The private stdout pipe establishes ownership.
        if (message.event === 'ready' && Number.isInteger(message.pid) && message.pid > 0 && Number.isInteger(message.port)
            && message.port > 0 && message.port <= 65535) {
          done();
          resolve({ origin: `http://127.0.0.1:${message.port}`, token });
        }
      } catch {
        // Ignore all non-protocol output on stdout. The backend redirects its
        // own prints to stderr, so anything landing here is stray; surface it
        // only when logging is on rather than dropping it silently.
        if (log) log.write(`${line}\n`);
      }
    });
  });
  child.stdin.write(JSON.stringify({ token, proxy }) + '\n');
  async function stop() {
    if (stopPromise) return stopPromise;
    stopped = true;
    stopPromise = (async () => {
      if (child.exitCode !== null || !child.pid) return;
      child.stdin.end('stop\n');
      let timer;
      const graceful = await Promise.race([exited.then(() => true), new Promise(resolve => {
        timer = setTimeout(() => resolve(false), 12000);
      })]);
      clearTimeout(timer);
      if (!graceful && child.exitCode === null) {
        // Only this owned PID tree, never all python/ffmpeg processes.
        if (process.platform === 'win32') {
          await new Promise(resolve => {
            const killer = spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
            killer.once('exit', resolve); killer.once('error', resolve);
          });
        } else child.kill('SIGKILL');
      }
    })();
    return stopPromise;
  }
  // `label` lets the main process write a truthful header when logging is armed
  // mid-session and the start-up header never reached anywhere.
  return { ready, stop, pid: child.pid, label: `${command} ${args.join(' ')}` };
}
module.exports = { startBackend, createDevLog, localStamp };
