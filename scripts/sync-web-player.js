#!/usr/bin/env node
"use strict";

// Copy the web UI into an already-unpacked desktop build.
//
// The packaged backend is a PyInstaller COLLECT tree, so
// resources/backend/_internal/web-player is a plain directory of real files and
// the Electron window loads the UI over laglingo://app/ from that backend. Pure
// HTML/CSS/JS edits therefore need no freeze and no electron-builder run.
//
// What this CANNOT do: add or remove backend HTTP routes. companion/server.py
// is compiled into the frozen archive, so a new asset URL (say, a new script tag
// next to a new add_get) still needs `npm run desktop:refresh`. The script
// detects that case and says so instead of shipping a 404.

const fs = require("node:fs");
const path = require("node:path");

const REPO_ROOT = path.resolve(__dirname, "..");
const SOURCE = path.join(REPO_ROOT, "prototype", "hls-companion", "web-player");
const TARGET = path.join(REPO_ROOT, "release", "win-unpacked", "resources", "backend", "_internal", "web-player");
const BACKEND_EXE = path.join(REPO_ROOT, "release", "win-unpacked", "resources", "backend", "laglingo-backend.exe");
// Sources that get compiled into the frozen backend rather than copied.
const FROZEN_SOURCES = [
  path.join(REPO_ROOT, "prototype", "hls-companion", "companion"),
  path.join(REPO_ROOT, "desktop", "companion_entry.py"),
];

function walk(directory, base = directory) {
  const out = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    // Compiled bytecode is rewritten every time anything imports the package,
    // so counting it would make the staleness check fire on every test run.
    if (entry.isDirectory()) {
      if (entry.name === "__pycache__" || entry.name === "node_modules") continue;
      out.push(...walk(path.join(directory, entry.name), base));
    } else if (entry.isFile() && !entry.name.endsWith(".pyc")) {
      out.push(path.relative(base, path.join(directory, entry.name)));
    }
  }
  return out;
}

function newestMtime(targets) {
  let newest = 0;
  for (const target of targets) {
    const stat = fs.statSync(target);
    if (stat.isFile()) { newest = Math.max(newest, stat.mtimeMs); continue; }
    for (const relative of walk(target)) {
      newest = Math.max(newest, fs.statSync(path.join(target, relative)).mtimeMs);
    }
  }
  return newest;
}

function main() {
  const dryRun = process.argv.includes("--dry-run");
  if (!fs.existsSync(SOURCE)) throw new Error(`Missing UI source: ${SOURCE}`);
  // Refuse to scatter files into anything that is not an unpacked backend.
  if (!fs.existsSync(path.join(TARGET, "index.html"))) {
    console.error(`Not an unpacked build (no index.html in ${TARGET}).`);
    console.error("Run: npm run desktop:backend && npm run desktop:pack");
    return 2;
  }

  const sources = walk(SOURCE);
  const existing = new Set(walk(TARGET));
  const wanted = new Set(sources);
  let copied = 0;
  let unchanged = 0;
  let removed = 0;

  for (const relative of sources) {
    const from = path.join(SOURCE, relative);
    const to = path.join(TARGET, relative);
    const same = fs.existsSync(to) && fs.statSync(to).size === fs.statSync(from).size
      && fs.readFileSync(to).equals(fs.readFileSync(from));
    if (same) { unchanged += 1; continue; }
    if (!dryRun) {
      fs.mkdirSync(path.dirname(to), { recursive: true });
      fs.copyFileSync(from, to);
    }
    copied += 1;
  }
  // Mirror, so an asset deleted in the source cannot linger and keep being served.
  for (const relative of existing) {
    if (wanted.has(relative)) continue;
    if (!dryRun) fs.rmSync(path.join(TARGET, relative), { force: true });
    removed += 1;
  }

  console.log(`${dryRun ? "[dry-run] would sync" : "synced"} web-player -> ${path.relative(REPO_ROOT, TARGET)}`);
  console.log(`  ${dryRun ? "would copy" : "copied"} ${copied} | unchanged ${unchanged} | ${dryRun ? "would remove" : "removed"} ${removed} | total ${sources.length}`);

  const frozenNewer = fs.existsSync(BACKEND_EXE) && newestMtime(FROZEN_SOURCES) > fs.statSync(BACKEND_EXE).mtimeMs;
  if (frozenNewer) {
    console.error("");
    console.error("WARNING: backend Python changed after the frozen build was made.");
    console.error("Routes and API behaviour in the packaged app are still the old ones.");
    console.error("A brand-new asset URL will 404 until you run: npm run desktop:refresh");
    return 1;
  }
  console.log("backend freeze is up to date with the Python sources");
  return 0;
}

try {
  process.exit(main());
} catch (error) {
  console.error(`sync-web-player failed: ${error.message}`);
  process.exit(2);
}
