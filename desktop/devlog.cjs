'use strict';
const fs = require('node:fs');
const path = require('node:path');

/*
 * The development log file.
 *
 * Two streams land in one file so that a test session leaves exactly one
 * artifact to hand over:
 *
 *   1. the backend's stderr, verbatim (startBackend writes it here)
 *   2. the renderer's diagnostic timeline, one record per line
 *
 * They are deliberately not merged into one format. Backend stderr is whatever
 * Python and FFmpeg printed; diagnostic lines always look like
 * `2026-09-15 14:31:27  [error] ui  <message>` and can be grepped as such.
 *
 * This was previously `backendLogSink` in backend.cjs. It moved because a
 * failed file open used to degrade silently to the terminal -- and Electron is
 * a GUI-subsystem binary on Windows, so "the terminal" is a void. A run could
 * spend an hour believing it was logging and produce nothing. Now the failure
 * is recorded on the object and shown in the diagnostics bar.
 */
const MAX_LINES_PER_POST = 200;
const MAX_LINE_CHARS = 1000;

// unset / 0 / off => null (nothing anywhere); 1 / on / true => terminal only;
// anything else => that path, opened for append, with the terminal as a tee.
function openDevLog(env = process.env) {
  const value = String(env.LINGERLENS_BACKEND_LOG ?? '').trim();
  if (!value || value === '0' || /^off$/i.test(value)) return null;

  const log = {
    path: null,        // absolute path that was asked for, or null for terminal-only
    writable: false,
    error: null,       // why the file is not being written, or null
    lines: 0,          // diagnostic records appended so far
    terminalOnly: false,
    write(chunk) {
      if (process.stderr) process.stderr.write(chunk);
      if (!log.writable) return false;
      // Synchronous on purpose: a buffered stream loses its tail when the app
      // is killed, and this log exists precisely for the run that dies.
      try {
        fs.appendFileSync(log.path, chunk);
        return true;
      } catch (problem) {
        // Mid-run failure -- disk full, file removed, permissions changed.
        log.error = describe(problem);
        log.writable = false;
        return false;
      }
    },
    writeLines(lines) {
      if (!log.writable) return 0;
      const accepted = [];
      for (const raw of Array.isArray(lines) ? lines : []) {
        const text = String(raw ?? '').replace(/[\r\n]+/g, ' ').trim();
        if (!text) continue;
        accepted.push(text.length > MAX_LINE_CHARS ? `${text.slice(0, MAX_LINE_CHARS - 1)}…` : text);
        if (accepted.length >= MAX_LINES_PER_POST) break;
      }
      if (!accepted.length) return 0;
      if (!log.write(`${accepted.join('\n')}\n`)) return 0;
      log.lines += accepted.length;
      return accepted.length;
    },
    state() {
      return { enabled: log.writable, file: log.path, error: log.error,
        lines: log.lines, terminalOnly: log.terminalOnly };
    },
  };

  if (/^(1|on|true|yes|stdout|stderr)$/i.test(value)) {
    log.terminalOnly = true;
    return log;
  }

  log.path = path.resolve(value);
  try {
    // Not creating this was the trap: a typo'd or not-yet-existing directory
    // turned the whole switch into a no-op with no signal anywhere.
    fs.mkdirSync(path.dirname(log.path), { recursive: true });
    // Windows opens a *directory* handle happily for both `openSync(p, 'a')` and
    // `appendFileSync(p, '')` -- the zero-byte append never reaches a write, so
    // neither can be used as a writability probe here. Only the real append
    // fails, with EISDIR. Check the one case the OS will not report.
    if (fs.statSync(log.path, { throwIfNoEntry: false })?.isDirectory()) {
      // No "EISDIR:" prefix in the message: describe() adds the code, and
      // spelling it twice reads like a bug in the error itself.
      throw Object.assign(new Error(`${log.path} is a directory`), { code: 'EISDIR' });
    }
    fs.appendFileSync(log.path, ''); // create it now rather than one hour into a run
    log.writable = true;
  } catch (problem) {
    log.error = describe(problem);
  }
  return log;
}

function describe(problem) {
  const code = problem?.code || 'ERROR';
  return `${code}: ${problem?.message || String(problem)}`;
}

module.exports = { openDevLog, MAX_LINES_PER_POST, MAX_LINE_CHARS };
