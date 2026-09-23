
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const MAX_LINES_PER_POST = 200;
const MAX_LINE_CHARS = 1000;
const MAX_QUEUED_BYTES = 1024 * 1024;
const MAX_QUEUED_OPERATIONS = 128;
const MAX_CHUNK_BYTES = 256 * 1024;
const MAX_FILE_BYTES = 8 * 1024 * 1024;
const BACKUP_FILES = 3;
const TERMINAL_VALUES = /^(1|on|true|yes|stdout|stderr)$/i;
const OFF_VALUES = /^(0|off|false|no|none)$/i;

/** One serialized async writer owns all files and rotations.
 * write() means accepted into a bounded queue, not durably persisted.
 * Every queued operation captures its destination: re-arming cannot send old
 * output into the new log. File mode deliberately does not tee to stderr,
 * whose writes can themselves be synchronous when redirected.
 */
function createDevLog(env = process.env, { io = fs.promises,
  maxQueuedBytes = MAX_QUEUED_BYTES, maxFileBytes = MAX_FILE_BYTES } = {}) {
  if (!Number.isSafeInteger(maxQueuedBytes) || maxQueuedBytes < 1
      || !Number.isSafeInteger(maxFileBytes) || maxFileBytes < 1) {
    throw new RangeError('Log byte limits must be positive safe integers');
  }
  const queue = [];
  const idleWaiters = new Set();
  let current = null;
  let draining = false;
  let scheduled = false;
  let queuedBytes = 0;
  let pendingOperations = 0;
  let failedOperations = 0;
  let droppedChunks = 0;
  let droppedBytes = 0;

  function drop(bytes, context) {
    droppedChunks += 1;
    droppedBytes += bytes;
    if (context) context.dropped += 1;
    return false;
  }
  function enqueue(operation) {
    const bytes = operation.bytes || 0;
    if (pendingOperations >= MAX_QUEUED_OPERATIONS || queuedBytes + bytes > maxQueuedBytes) {
      return drop(bytes, operation.context);
    }
    queue.push(operation);
    pendingOperations += 1;
    queuedBytes += bytes;  // Includes the in-flight filesystem operation.
    if (!draining && !scheduled) {
      scheduled = true;
      queueMicrotask(() => { scheduled = false; void drain(); });
    }
    return true;
  }
  async function rotate(context) {
    await io.rm(`${context.path}.${BACKUP_FILES}`, { force: true });
    for (let n = BACKUP_FILES - 1; n >= 1; n -= 1) {
      try { await io.rename(`${context.path}.${n}`, `${context.path}.${n + 1}`); }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    try { await io.rename(context.path, `${context.path}.1`); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    context.size = 0;
  }
  async function drain() {
    if (draining) return;
    draining = true;
    try {
      while (queue.length) {
        const operation = queue.shift();
        const context = operation.context;
        try {
          if (context.error) {
            if (operation.kind === 'write') drop(operation.bytes, context);
            continue;
          }
          if (operation.kind === 'open') {
            await io.mkdir(path.dirname(context.path), { recursive: true });
            let stat = null;
            try { stat = await io.stat(context.path); }
            catch (error) { if (error.code !== 'ENOENT') throw error; }
            if (stat?.isDirectory()) throw Object.assign(new Error('Log path is a directory'), { code: 'EISDIR' });
            context.size = stat?.size || 0;
            if (context.size > maxFileBytes) await rotate(context);
            await io.appendFile(context.path, '');
            context.ready = true;
          } else {
            // Rotating before the batch keeps each owned file bounded.
            if (context.size + operation.bytes > maxFileBytes) await rotate(context);
            await io.appendFile(context.path, operation.chunk);
            context.size += operation.bytes;
            context.persistedBytes += operation.bytes;
          }
        } catch (error) {
          context.error = describe(error);
          context.ready = false;
          failedOperations += 1;
          if (operation.kind === 'write') drop(operation.bytes, context);
        } finally {
          pendingOperations -= 1;
          queuedBytes -= operation.bytes || 0;
        }
      }
    } finally {
      draining = false;
      for (const finish of [...idleWaiters]) finish(true);
    }
  }
  const log = {
    mode: 'off',
    path: null,
    terminalOnly: false,
    get writable() { return log.mode === 'file' && current?.ready === true && !current.error; },
    get error() { return current?.error || null; },
    get lines() { return current?.lines || 0; },

    arm(target, header = '') {
      log.disarm();
      if (target === true || target === undefined || TERMINAL_VALUES.test(String(target))) {
        log.mode = 'terminal';
        log.terminalOnly = true;
      } else {
        log.mode = 'file';
        log.path = path.resolve(String(target));
        current = { path: log.path, ready: false, error: null, size: 0,
          lines: 0, persistedBytes: 0, dropped: 0 };
        if (!enqueue({ kind: 'open', context: current })) {
          current.error = 'EQUEUEFULL: log queue is full';
        }
      }
      if (header) log.write(header);
      return log.state();
    },
    disarm() {
      log.mode = 'off';
      log.path = null;
      log.terminalOnly = false;
      current = null;
      return log.state();
    },
    write(chunk) {
      if (log.mode === 'off') return false;
      const bytes = Buffer.isBuffer(chunk) ? chunk.length : Buffer.byteLength(String(chunk));
      if (bytes > Math.min(MAX_CHUNK_BYTES, maxFileBytes) || bytes > maxQueuedBytes) return drop(bytes, current);
      if (log.mode === 'terminal') {
        // Explicit terminal mode is not used by normal desktop diagnostics.
        if (process.stderr && !process.stderr.writableNeedDrain) process.stderr.write(chunk);
        else drop(bytes, null);
        return false;
      }
      if (!current || current.error) return false;
      if (!bytes) return true;
      return enqueue({ kind: 'write', context: current, bytes,
        chunk: Buffer.isBuffer(chunk) ? Buffer.from(chunk) : String(chunk) });
    },
    writeLines(lines) {
      if (log.mode !== 'file' || !current || current.error) return 0;
      const accepted = [];
      for (const raw of Array.isArray(lines) ? lines : []) {
        const text = String(raw ?? '').replace(/[\r\n]+/g, ' ').trim();
        if (!text) continue;
        accepted.push(text.length > MAX_LINE_CHARS ? `${text.slice(0, MAX_LINE_CHARS - 1)}…` : text);
        if (accepted.length >= MAX_LINES_PER_POST) break;
      }
      if (!accepted.length || !log.write(`${accepted.join('\n')}
`)) return 0;
      current.lines += accepted.length;
      return accepted.length;
    },
    async flush(timeoutMs = 2000) {
      if (!pendingOperations) return !current?.error;
      if (idleWaiters.size >= 32) return false;
      const failuresAtStart = failedOperations;
      const completed = await new Promise(resolve => {
        let timer;
        const finish = value => { clearTimeout(timer); idleWaiters.delete(finish); resolve(value); };
        idleWaiters.add(finish);
        timer = setTimeout(() => finish(false), Math.max(1, Math.min(10000, Number(timeoutMs) || 2000)));
      });
      return completed && failuresAtStart === failedOperations && !current?.error;
    },
    async close(timeoutMs = 2000) {
      log.disarm();
      return log.flush(timeoutMs);
    },
    state() {
      return { enabled: log.writable, file: log.path, error: log.error, lines: log.lines,
        terminalOnly: log.terminalOnly, active: log.mode !== 'off',
        pending: log.mode === 'file' && !!current && !current.ready && !current.error,
        queuedBytes, queuedOperations: pendingOperations, droppedChunks, droppedBytes,
        persistedBytes: current?.persistedBytes || 0 };
    },
  };
  const value = String(env?.LINGERLENS_BACKEND_LOG ?? '').trim();
  if (value && !OFF_VALUES.test(value)) log.arm(TERMINAL_VALUES.test(value) ? true : value);
  return log;
}
function describe(problem) {
  return `${problem?.code || 'ERROR'}: ${problem?.message || String(problem)}`;
}
function defaultLogPath(userDataDir, at = new Date()) {
  const pad = value => String(value).padStart(2, '0');
  const stamp = `${at.getFullYear()}${pad(at.getMonth() + 1)}${pad(at.getDate())}`
    + `-${pad(at.getHours())}${pad(at.getMinutes())}${pad(at.getSeconds())}`;
  return path.join(userDataDir, 'logs', `lingerlens-${stamp}.log`);
}
module.exports = { createDevLog, defaultLogPath, MAX_LINES_PER_POST, MAX_LINE_CHARS,
  MAX_QUEUED_BYTES, MAX_QUEUED_OPERATIONS, MAX_FILE_BYTES };
