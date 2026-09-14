#!/usr/bin/env node
"use strict";

// Discover and run every browser-side test, mirroring scripts/run-hls-tests.py.
//
// The previous npm script hand-listed 10 of the 11 files that exist, so
// test_chat_overlay.js never ran. Discovery removes that failure mode.

const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const REPO_ROOT = path.resolve(__dirname, "..");
const TESTS_DIR = path.join(REPO_ROOT, "prototype", "hls-companion", "tests");

const files = fs
  .readdirSync(TESTS_DIR)
  .filter((name) => /^test_.*\.js$/.test(name))
  .sort();

if (files.length === 0) {
  console.error(`No browser test files found in ${TESTS_DIR}`);
  process.exit(2);
}

const relative = files.map((name) => path.join("prototype", "hls-companion", "tests", name));
console.log(`Running ${files.length} browser test files`);
console.log("=".repeat(78));

const result = spawnSync(process.execPath, ["--test", ...relative], {
  cwd: REPO_ROOT,
  stdio: "inherit",
});

process.exit(result.status === null ? 1 : result.status);
