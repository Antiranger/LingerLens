#!/usr/bin/env node
"use strict";

// Fail when something is redistributed that THIRD_PARTY_NOTICES.md does not name.
//
// The notices drifted silently before: the file claimed fugashi 1.4.0 while
// requirements-build.txt pinned 1.5.2, and yt-dlp.exe had grown a copyleft
// dependency nobody had listed. Nothing checked, so nothing caught it. A notice
// file that nobody re-reads is not compliance, it is a hope.
//
// Two levels, because they have different prerequisites:
//
//   (default)  the vendored components tracked in git. Always runnable, and
//              these are the ones a source clone redistributes immediately.
//   --frozen   additionally walk the PyInstaller freeze output, so a new
//              dependency in requirements-build.txt cannot ship unnamed.
//
// Usage: node scripts/check-licence-coverage.js [--frozen]

const fs = require("node:fs");
const path = require("node:path");

const REPO_ROOT = path.resolve(__dirname, "..");
const NOTICES = path.join(REPO_ROOT, "THIRD_PARTY_NOTICES.md");
const LICENCE_DIR = path.join(REPO_ROOT, "licenses");
const WEB_PLAYER = path.join(REPO_ROOT, "prototype", "hls-companion", "web-player");
const VENDOR = path.join(REPO_ROOT, "prototype", "hls-companion", "vendor");
const FREEZE = path.join(REPO_ROOT, "build-desktop", "backend", "lingerlens-backend", "_internal");

/*
 * Each entry: something redistributed, the notice text that must name it, and
 * the licence files that must sit beside it. `beside` is checked because
 * several licences (OFL, Apache-2.0) require the text to travel WITH the copy,
 * not merely to exist somewhere in the repository.
 */
const COMPONENTS = [
  {
    what: "the vendored yt-dlp executable",
    path: path.join(VENDOR, "yt-dlp", "yt-dlp.exe"),
    named: [/yt-dlp/i],
    beside: [path.join(VENDOR, "yt-dlp")],
  },
  {
    what: "the vendored hls.js bundle",
    path: path.join(WEB_PLAYER, "vendor", "hls.min.js"),
    named: [/hls\.js/i, /regenerator-runtime/i],
    beside: [path.join(WEB_PLAYER, "vendor")],
  },
  {
    what: "the bundled web fonts",
    path: path.join(WEB_PLAYER, "fonts"),
    named: [/Noto Sans SC/i, /Archivo Black/i, /JetBrains Mono/i, /Open Font License/i],
    beside: [path.join(WEB_PLAYER, "fonts")],
  },
];

// Licence files that must exist and be non-trivial. A zero-byte or stub file is
// worse than a missing one because it looks like compliance.
const REQUIRED_TEXTS = [
  { file: "OFL-1.1-fonts.txt", marker: /SIL OPEN FONT LICENSE/i },
  { file: "Apache-2.0.txt", marker: /Apache License/i },
  { file: "GPL-2.0.txt", marker: /GNU GENERAL PUBLIC LICENSE/i },
  { file: "GPL-3.0.txt", marker: /GNU GENERAL PUBLIC LICENSE/i },
  { file: "MIT-libffi.txt", marker: /Permission is hereby granted/i },
  { file: "Unlicense.txt", marker: /This is free and unencumbered software/i },
];

// Binary components in the freeze that are not Python distributions and would
// otherwise be invisible to the dist-info walk.
const NATIVE_COMPONENTS = [
  { file: /libcrypto-3-x64\.dll$/i, named: /OpenSSL/i },
  { file: /libssl-3-x64\.dll$/i, named: /OpenSSL/i },
  { file: /libffi-8\.dll$/i, named: /libffi/i },
  { file: [/^ffmpeg\.exe$/i, /^ffprobe\.exe$/i], named: /FFmpeg/i },
  { file: /^python3\d+\.dll$/i, named: /Python/i },
];

function readNotices() {
  if (!fs.existsSync(NOTICES)) throw new Error(`missing ${path.relative(REPO_ROOT, NOTICES)}`);
  return fs.readFileSync(NOTICES, "utf8");
}

function licenceTextsBeside(directory) {
  if (!fs.existsSync(directory)) return [];
  return fs.readdirSync(directory, { withFileTypes: true })
    .filter((entry) => entry.isFile() && /^(LICENSE|LICENCE|COPYING|OFL)/i.test(entry.name))
    .map((entry) => entry.name)
    .filter((name) => fs.statSync(path.join(directory, name)).size > 200);
}

function distributionName(directoryName) {
  return directoryName.replace(/\.dist-info$/, "").replace(/-\d[^-]*$/, "");
}

function checkTracked(notices, findings) {
  for (const component of COMPONENTS) {
    if (!fs.existsSync(component.path)) continue; // not vendored in this checkout
    for (const pattern of component.named) {
      if (!pattern.test(notices)) {
        findings.push(`${component.what}: THIRD_PARTY_NOTICES.md never mentions ${pattern}`);
      }
    }
    for (const directory of component.beside) {
      if (!fs.existsSync(directory)) continue;
      if (!licenceTextsBeside(directory).length) {
        findings.push(`${component.what}: no licence file sits beside it in ${path.relative(REPO_ROOT, directory)}`);
      }
    }
  }
  for (const required of REQUIRED_TEXTS) {
    const file = path.join(LICENCE_DIR, required.file);
    if (!fs.existsSync(file)) { findings.push(`licenses/${required.file} is missing`); continue; }
    const text = fs.readFileSync(file, "utf8");
    if (!required.marker.test(text)) {
      findings.push(`licenses/${required.file} does not look like that licence (no match for ${required.marker})`);
    }
  }
}

/*
 * Components that must have their licence text BESIDE them inside the freeze,
 * not just somewhere in the repository. This is the check that catches a spec
 * file shipping a binary without its notices: `companion.spec` copied only
 * yt-dlp.exe for a while, so the texts added to the source tree never reached
 * the build, which is precisely the case the licences care about.
 */
const FROZEN_BESIDE = [
  { directory: /(^|[\\/])vendor[\\/]yt-dlp$/i, what: "the bundled yt-dlp executable", named: /yt-dlp/i },
];

function checkFrozen(notices, findings) {
  if (!fs.existsSync(FREEZE)) {
    findings.push(`frozen inventory requested but not built: ${path.relative(REPO_ROOT, FREEZE)}`);
    return false;
  }
  let seen = 0;
  for (const entry of fs.readdirSync(FREEZE, { withFileTypes: true })) {
    if (entry.isDirectory() && entry.name.endsWith(".dist-info")) {
      seen += 1;
      const name = distributionName(entry.name);
      // A distribution is declared if its import name appears in the notices.
      if (!notices.includes(name)) {
        findings.push(`frozen distribution "${name}" (${entry.name}) is not named in THIRD_PARTY_NOTICES.md`);
      }
    }
  }
  for (const entry of fs.readdirSync(FREEZE, { withFileTypes: true })) {
    if (!entry.isFile()) continue;
    for (const native of NATIVE_COMPONENTS) {
      const patterns = Array.isArray(native.file) ? native.file : [native.file];
      if (patterns.some((pattern) => pattern.test(entry.name)) && !native.named.test(notices)) {
        findings.push(`frozen binary "${entry.name}" implies ${native.named}, which THIRD_PARTY_NOTICES.md does not name`);
      }
    }
  }
  for (const directory of directoriesUnder(FREEZE, 6)) {
    const beside = FROZEN_BESIDE.find((candidate) => candidate.directory.test(directory));
    if (!beside) continue;
    if (!licenceTextsBeside(directory).length) {
      findings.push(`${beside.what}: nothing in the freeze carries its licence text (${path.relative(REPO_ROOT, directory)})`);
    }
  }
  console.log(`licence: checked ${seen} frozen distribution(s), the native DLLs, and the licence files beside them`);
  return true;
}

/* Bounded walk: the freeze is deep and mostly irrelevant, so cap the depth. */
function directoriesUnder(root, maxDepth, depth = 0, found = []) {
  if (depth > maxDepth) return found;
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const full = path.join(root, entry.name);
    found.push(full);
    directoriesUnder(full, maxDepth, depth + 1, found);
  }
  return found;
}

function main() {
  const findings = [];
  const notices = readNotices();
  checkTracked(notices, findings);
  if (process.argv.includes("--frozen")) checkFrozen(notices, findings);

  if (findings.length) {
    console.error(`\nFAIL: ${findings.length} licence coverage problem(s):`);
    for (const finding of findings) console.error(`  - ${finding}`);
    console.error("\nRedistributing something the notices do not name is a licence violation, not a tidiness issue.");
    console.error("Add the component and its licence text, then re-run.");
    return 1;
  }
  console.log("PASS every redistributed component is named and every required licence text is present");
  return 0;
}

try {
  process.exit(main());
} catch (error) {
  console.error(`check-licence-coverage failed: ${error.message}`);
  process.exit(2);
}
