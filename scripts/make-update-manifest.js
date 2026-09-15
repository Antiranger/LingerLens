#!/usr/bin/env node
"use strict";

// Produce the update manifest that the installed app polls.
//
// The app trusts this file's SHA-256 absolutely: a manifest that names a
// installer and a hash is the only thing standing between a user and whatever
// is on the other end of the URL. Generating it from the artifact that was just
// built -- rather than typing a hash -- is what keeps the two in step.
//
// Usage:
//   node scripts/make-update-manifest.js <installer.exe> <download-url> [notes-file]
// Writes release/manifest.json next to it.

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const REPO_ROOT = path.resolve(__dirname, "..");

function sha256(file) {
  const hash = crypto.createHash("sha256");
  const handle = fs.openSync(file, "r");
  const buffer = Buffer.allocUnsafe(1 << 20);
  try {
    let read;
    while ((read = fs.readSync(handle, buffer, 0, buffer.length, null)) > 0) {
      hash.update(buffer.subarray(0, read));
    }
  } finally {
    fs.closeSync(handle);
  }
  return hash.digest("hex");
}

function main() {
  const [installer, url, notesFile] = process.argv.slice(2);
  if (!installer || !url) {
    console.error("usage: node scripts/make-update-manifest.js <installer.exe> <download-url> [notes-file]");
    return 2;
  }
  const absolute = path.resolve(installer);
  if (!fs.existsSync(absolute)) {
    console.error(`no such installer: ${absolute}`);
    return 2;
  }
  if (new URL(url).protocol !== "https:") {
    console.error(`the download url must be https, got ${url}`);
    return 2;
  }
  const version = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, "package.json"), "utf8")).version;
  const manifest = {
    version,
    notes: notesFile && fs.existsSync(notesFile) ? fs.readFileSync(notesFile, "utf8").trim().slice(0, 2000) : "",
    publishedAt: new Date().toISOString(),
    installer: {
      url,
      sha256: sha256(absolute),
      size: fs.statSync(absolute).size,
    },
  };
  const output = path.join(REPO_ROOT, "release", "manifest.json");
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, `${JSON.stringify(manifest, null, 2)}\n`);
  console.log(`wrote ${path.relative(REPO_ROOT, output)}`);
  console.log(`  version ${manifest.version}`);
  console.log(`  sha256  ${manifest.installer.sha256}`);
  console.log(`  size    ${manifest.installer.size}`);
  console.log(`  url     ${manifest.installer.url}`);
  return 0;
}

try {
  process.exit(main());
} catch (error) {
  console.error(`make-update-manifest failed: ${error.message}`);
  process.exit(2);
}
