const { spawn } = require('node:child_process');
const { createInterface } = require('node:readline');
const { randomBytes } = require('node:crypto');
const path = require('node:path');

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
  let stopped = false;
  let stopPromise;
  child.stderr.resume(); // Do not persist media/provider output containing private URLs.
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
      } catch { /* Ignore all non-protocol output. */ }
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
module.exports = { startBackend };
