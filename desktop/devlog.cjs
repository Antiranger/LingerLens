'use strict';
const fs = require('node:fs');
const path = require('node:path');

/*
 * The diagnostic log file.
 *
 * Two streams land in one file so that one test session, or one bug report,
 * leaves exactly one artifact to hand over:
 *
 *   1. the backend's stderr, verbatim (startBackend writes it here)
 *   2. the renderer's diagnostic timeline, one record per line
 *
 * They are deliberately not merged into one format. Backend stderr is whatever
 * Python and FFmpeg printed; diagnostic lines always look like
 * `2026-09-15 14:31:27  [error] ui  <message>` and can be grepped as such.
 *
 * The object is permanent and the *destination* is switchable. That matters:
 * the packaged build has no launcher script to set an environment variable
 * before start-up, so a user has to be able to arm this from the UI while the
 * app is already running -- which means the backend's stderr listener must
 * already be attached when that happens.
 *
 * History worth keeping: this used to degrade silently. A path whose directory
 * did not exist fell back to "the terminal", and Electron is a GUI-subsystem
 * binary on Windows, so a run could log nothing for an hour and never say so.
 * The failure now lives on the object, reaches the UI, and shows up in the
 * diagnostics bar.
 */
const MAX_LINES_PER_POST = 200;
const MAX_LINE_CHARS = 1000;

const TERMINAL_VALUES = /^(1|on|true|yes|stdout|stderr)$/i;
const OFF_VALUES = /^(0|off|false|no|none)$/i;

/*
 * `mode` is the single source of truth for whether anything is written:
 *
 *   'off'       nothing, anywhere (the default; stderr is consumed and dropped)
 *   'terminal'  process.stderr only
 *   'file'      process.stderr and the file
 *
 * A failed file open lands in 'terminal' with `error` set: the file is not
 * there, but nothing else about the app changes and the reason is reportable.
 */
function createDevLog(env = process.env) {
  const log = {
    mode: 'off',
    path: null,        // absolute path in play, or null
    writable: false,
    error: null,       // why the file is not being written, or null
    lines: 0,          // diagnostic records appended since the last arm()
    terminalOnly: false,

    arm(target, header = '') {
      log.error = null;
      log.lines = 0;
      log.path = null;
      log.writable = false;
      log.terminalOnly = false;
      log.mode = 'off';

      if (target === true || target === undefined || TERMINAL_VALUES.test(String(target))) {
        log.mode = 'terminal';
        log.terminalOnly = true;
      } else {
        log.path = path.resolve(String(target));
        try {
          // A typo'd or not-yet-existing directory used to turn the whole
          // switch into a no-op with no signal anywhere. Create it.
          fs.mkdirSync(path.dirname(log.path), { recursive: true });
          // Windows opens a *directory* handle happily for both
          // `openSync(p, 'a')` and `appendFileSync(p, '')` -- a zero-byte
          // append never reaches a write, so neither works as a writability
          // probe. Only a real write fails, with EISDIR. Check that one case
          // the OS will not report.
          if (fs.statSync(log.path, { throwIfNoEntry: false })?.isDirectory()) {
            // No "EISDIR:" prefix: describe() adds the code, and spelling it
            // twice reads like a bug in the error itself.
            throw Object.assign(new Error(`${log.path} is a directory`), { code: 'EISDIR' });
          }
          fs.appendFileSync(log.path, ''); // create it now, not an hour into a run
          log.writable = true;
          log.mode = 'file';
        } catch (problem) {
          log.error = describe(problem);
          log.mode = 'terminal';
        }
      }
      if (header) log.write(header);
      return log.state();
    },

    disarm() {
      log.mode = 'off';
      log.path = null;
      log.writable = false;
      log.error = null;
      log.terminalOnly = false;
      return log.state();
    },

    write(chunk) {
      if (log.mode === 'off') return false;
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
        lines: log.lines, terminalOnly: log.terminalOnly, active: log.mode !== 'off' };
    },
  };

  // The environment variable still wins at construction: the dev launcher sets
  // it so a run starts recording before any window exists.
  const value = String(env?.LINGERLENS_BACKEND_LOG ?? '').trim();
  if (value && !OFF_VALUES.test(value)) log.arm(TERMINAL_VALUES.test(value) ? true : value);
  return log;
}

function describe(problem) {
  const code = problem?.code || 'ERROR';
  return `${code}: ${problem?.message || String(problem)}`;
}

/* Default location for a log the user asked for from inside the running app. */
function defaultLogPath(userDataDir, at = new Date()) {
  const pad = (value) => String(value).padStart(2, '0');
  const stamp = `${at.getFullYear()}${pad(at.getMonth() + 1)}${pad(at.getDate())}`
    + `-${pad(at.getHours())}${pad(at.getMinutes())}${pad(at.getSeconds())}`;
  return path.join(userDataDir, 'logs', `lingerlens-${stamp}.log`);
}

module.exports = { createDevLog, defaultLogPath, MAX_LINES_PER_POST, MAX_LINE_CHARS };
