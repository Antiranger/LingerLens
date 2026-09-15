const { spawn } = require('node:child_process');
const { createInterface } = require('node:readline');
const { randomBytes } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

// The backend writes everything to stderr: `companion_entry.py` redirects
// stdout there and keeps stdout for the one-line ready handshake. That stderr
// stays drained by default, because media and provider output can carry private
// stream URLs. `LAGLINGO_BACKEND_LOG` unlocks it, opt-in and per run:
//
//   unset / 0 / off   discard it (default, nothing is written anywhere)
//   1 / on / true     write it to this process's stderr
//   anything else     treat it as a file path and tee it there as well
//
// Electron is a GUI-subsystem binary on Windows, so `process.stderr` is not
// always attached to a console. When it is missing, use the file form for a run
// long enough that you cannot watch the terminal.
function backendLogSink(env = process.env) {
  const value = String(env.LAGLINGO_BACKEND_LOG ?? '').trim();
  if (!value || value === '0' || /^off$/i.test(value)) return null;
  let file = null;
  if (!/^(1|on|true|yes|stdout|stderr)$/i.test(value)) {
    file = path.resolve(value);
    // Fail here rather than mid-run, and fall back to the terminal.
    try { fs.appendFileSync(file, ''); } catch { file = null; }
  }
  // Appends are synchronous on purpose: this log is low-volume (unexpected
  // request failures), and a buffered stream can lose its tail when the app is
  // killed. A broken sink must never take the player down with it.
  return chunk => {
    if (process.stderr) process.stderr.write(chunk);
    if (file) { try { fs.appendFileSync(file, chunk); } catch { /* ignore */ } }
  };
}

// Local wall-clock rather than UTC: a long-run log gets read against what the
// clock on the wall said when something went wrong.
function localStamp(date = new Date()) {
  const pad = value => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function startBackend({ packaged, resources, root, dataDir, proxy = null, onExit = () => {} }) {
  const command = packaged
    ? path.join(resources, 'backend', 'laglingo-backend.exe')
    : (process.env.LAGLINGO_PYTHON || path.join(root, '.venv-desktop', 'Scripts', 'python.exe'));
  const args = packaged ? [] : [path.join(root, 'desktop', 'companion_entry.py')];
  args.push('--data-dir', dataDir);
  const env = { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' };
  // PyInstaller and the development venv must not import another Python install.
  delete env.PYTHONHOME;
  delete env.PYTHONPATH;
  const child = spawn(command, args, { cwd: packaged ? resources : root, env,
    windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
  const token = randomBytes(32).toString('hex');
  const log = backendLogSink();
  let stopped = false;
  let stopPromise;
  if (log) {
    // A timestamped header keeps an eight-hour log navigable.
    log(`\n[LagLingo ${localStamp()}] backend: ${command} ${args.join(' ')}\n`);
    child.stderr.on('data', log);
  } else {
    child.stderr.resume(); // Do not persist media/provider output containing private URLs.
  }
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
        if (log) log(`${line}\n`);
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
  return { ready, stop, pid: child.pid };
}
module.exports = { startBackend, backendLogSink, localStamp };
