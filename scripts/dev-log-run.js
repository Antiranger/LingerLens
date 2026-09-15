#!/usr/bin/env node
'use strict';
/*
 * `npm run desktop:dev:log` -- the development build with the dev log switched on.
 *
 * This exists because the two-step version is easy to get wrong in a way that
 * produces nothing:
 *
 *   $env:LINGERLENS_BACKEND_LOG = "F:\...\logs\run.log"   # directory must exist
 *   npm run desktop:dev
 *
 * The directory not existing used to be silent (the sink fell back to the
 * terminal, and Electron is a GUI binary with nothing attached to it), and the
 * `=1` terminal form is useless for the same reason. Here the directory is
 * created, the filename is timestamped, and the path is printed before and
 * after the run so there is never a question of where the log went.
 *
 * Extra arguments are forwarded, so this still works for the CDP probes:
 *   npm run desktop:dev:log -- --remote-debugging-port=9222
 */
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const now = new Date();
const pad = (value) => String(value).padStart(2, '0');
const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`
  + `-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
const directory = path.join(root, '.scratch', 'dev-logs');
const file = path.join(directory, `dev-${stamp}.log`);

const electron = path.join(root, 'build-desktop', 'electron', 'electron.exe');
if (!fs.existsSync(electron)) {
  console.error(`找不到 Electron：${electron}\n先跑一次 npm install。`);
  process.exit(1);
}

// The launcher owns this directory rather than trusting the caller to have made it.
fs.mkdirSync(directory, { recursive: true });
console.log(`[dev:log] 本次日志 -> ${file}`);

const child = spawn(electron, ['.', ...process.argv.slice(2)], {
  cwd: root,
  stdio: 'inherit',
  env: { ...process.env, LINGERLENS_BACKEND_LOG: file },
});
child.on('error', (error) => {
  console.error(`[dev:log] 启动失败：${error.message}`);
  process.exit(1);
});
child.on('exit', (code) => {
  let size = 0;
  try { size = fs.statSync(file).size; } catch { /* never written: size stays 0 */ }
  console.log(`[dev:log] 已退出。日志 ${file}（${size} 字节）`);
  process.exit(code ?? 0);
});
