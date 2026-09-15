#!/usr/bin/env node
"use strict";

// Copy the web UI into an already-built desktop tree.
//
// The packaged backend is a PyInstaller COLLECT tree, so
// resources/backend/_internal/web-player is a plain directory of real files and
// the Electron window loads the UI over laglingo://app/ from that backend. Pure
// HTML/CSS/JS edits therefore need no freeze and no electron-builder run.
//
// It has to write BOTH copies. `npm run desktop:pack` (and `desktop:dist`) take
// web-player from the freeze output, not from the source tree, and overwrite
// the unpacked build with it. Syncing only the unpacked build therefore looked
// like it worked and was silently reverted by the next repack -- which is how a
// build was shipped with an older UI than the tree it came from.
//
// What this CANNOT do: add or remove backend HTTP routes. companion/server.py
// is compiled into the frozen archive, so a new asset URL (say, a new script tag
// next to a new add_get) still needs `npm run desktop:refresh`. The script
// detects that case and says so instead of shipping a 404.

const fs = require("node:fs");
const path = require("node:path");

const REPO_ROOT = path.resolve(__dirname, "..");
const SOURCE = path.join(REPO_ROOT, "prototype", "hls-companion", "web-player");
const RELATIVE_UI = path.join("backend", "_internal", "web-player");
// The freeze output is what a repack copies from; the unpacked build is what is
// running right now. Both have to agree with the source.
const TARGETS = [
  path.join(REPO_ROOT, "build-desktop", "backend", "laglingo-backend", "_internal", "web-player"),
  path.join(REPO_ROOT, "release", "win-unpacked", "resources", RELATIVE_UI),
];
const BACKEND_EXE = path.join(REPO_ROOT, "release", "win-unpacked", "resources", "backend", "laglingo-backend.exe");
// Sources that get compiled into the frozen backend rather than copied.
const FROZEN_SOURCES = [
  path.join(REPO_ROOT, "prototype", "hls-companion", "companion"),
  path.join(REPO_ROOT, "desktop", "companion_entry.py"),
];
// Editor and OS leftovers. Windows' ReplaceFileW drops `<name>~RF<hex>.TMP`
// next to the file it rewrites; git ignores them, but this script mirrors a
// directory tree, so without this they would be copied into the packaged app.
const JUNK = [/(^|[\\/])~\$/, /~RF[0-9a-f]+\.TMP$/i, /\.(tmp|swp|bak|orig)$/i, /(^|[\\/])\.DS_Store$/];

function isJunk(relative) {
  return JUNK.some((pattern) => pattern.test(relative));
}

function walk(directory, base = directory) {
  const out = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    // Compiled bytecode is rewritten every time anything imports the package,
    // so counting it would make the staleness check fire on every test run.
    if (entry.isDirectory()) {
      if (entry.name === "__pycache__" || entry.name === "node_modules") continue;
      out.push(...walk(path.join(directory, entry.name), base));
    } else if (entry.isFile() && !entry.name.endsWith(".pyc")) {
      const relative = path.relative(base, path.join(directory, entry.name));
      if (!isJunk(relative)) out.push(relative);
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

function syncInto(target, sources, dryRun) {
  const existing = new Set(walk(target));
  const wanted = new Set(sources);
  const counts = { copied: 0, unchanged: 0, removed: 0 };
  for (const relative of sources) {
    const from = path.join(SOURCE, relative);
    const to = path.join(target, relative);
    const same = fs.existsSync(to) && fs.statSync(to).size === fs.statSync(from).size
      && fs.readFileSync(to).equals(fs.readFileSync(from));
    if (same) { counts.unchanged += 1; continue; }
    if (!dryRun) {
      fs.mkdirSync(path.dirname(to), { recursive: true });
      fs.copyFileSync(from, to);
    }
    counts.copied += 1;
  }
  // Mirror, so an asset deleted in the source cannot linger and keep being served.
  for (const relative of existing) {
    if (wanted.has(relative)) continue;
    if (!dryRun) fs.rmSync(path.join(target, relative), { force: true });
    counts.removed += 1;
  }
  return counts;
}

function main() {
  const dryRun = process.argv.includes("--dry-run");
  if (!fs.existsSync(SOURCE)) throw new Error(`Missing UI source: ${SOURCE}`);
  // Refuse to scatter files into anything that is not an unpacked backend.
  const targets = TARGETS.filter((target) => fs.existsSync(path.join(target, "index.html")));
  if (!targets.length) {
    console.error(`No unpacked build found. Looked in:\n  ${TARGETS.join("\n  ")}`);
    console.error("Run: npm run desktop:backend && npm run desktop:pack");
    return 2;
  }

  const sources = walk(SOURCE);
  console.log(`${dryRun ? "[dry-run] would sync" : "synced"} web-player (${sources.length} files)`);
  for (const target of targets) {
    const counts = syncInto(target, sources, dryRun);
    const verb = dryRun ? "would copy" : "copied";
    const drop = dryRun ? "would remove" : "removed";
    console.log(`  ${path.relative(REPO_ROOT, target)}: ${verb} ${counts.copied} | unchanged ${counts.unchanged} | ${drop} ${counts.removed}`);
  }
  if (targets.length < TARGETS.length) {
    const missing = TARGETS.filter((target) => !targets.includes(target));
    console.log(`  (skipped, not built: ${missing.map((t) => path.relative(REPO_ROOT, t)).join(", ")})`);
  }

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
