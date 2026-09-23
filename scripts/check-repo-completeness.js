#!/usr/bin/env node
"use strict";

// Does a fresh clone actually contain everything the build and the app use?
//
// The failure this exists for: the working tree ran fine for months while
// `git ls-files` was missing desktop/, most of web-player/, the design
// prototype and half the test suite. Nothing caught it because every local
// command reads the working tree, never the index. A clean `git clone` then
// got an app whose own CI could not pass.
//
// So: gather every file path the project *references* (package.json scripts and
// main, the CI workflow, the player's script/link tags, CSS url() assets), and
// require each one to be either tracked or deliberately git-ignored. Untracked
// and unignored means a fresh clone breaks.

const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const REPO_ROOT = path.resolve(__dirname, "..");
const WEB_PLAYER = "prototype/hls-companion/web-player";
const EXTENSIONS = "js|cjs|mjs|ts|py|json|css|html|txt|exe|sha256|ps1|cmd|woff2";
const PATH_TOKEN = new RegExp(`[A-Za-z0-9_.\\-/\\\\]+\\.(?:${EXTENSIONS})`, "g");

function git(args, input) {
  return execFileSync("git", args, {
    cwd: REPO_ROOT,
    encoding: "utf8",
    input,
    maxBuffer: 64 * 1024 * 1024,
  });
}

function normalize(token) {
  return token.replace(/\\/g, "/").replace(/^\.\//, "");
}

function read(relative) {
  const full = path.join(REPO_ROOT, relative);
  return fs.existsSync(full) ? fs.readFileSync(full, "utf8") : "";
}

// Reference sources. Each returns [rawPath, where-it-is-mentioned] pairs.
function collectReferences() {
  const found = [];

  // Path-looking tokens from free text. Skip anything glued to a shell variable
  // (`"$PWD/build-desktop/..."` is a runtime path, not a repo file) and anything
  // inside a template placeholder.
  const pushTokens = (text, source) => {
    for (const match of text.matchAll(PATH_TOKEN)) {
      const before = text.slice(Math.max(0, match.index - 1), match.index);
      if (before === "$" || before === "{" || before === "%") continue;
      if (text.includes(`{${match[0]}}`)) continue;
      found.push([normalize(match[0]), source]);
    }
  };

  const pkg = JSON.parse(read("package.json"));
  for (const [name, command] of Object.entries(pkg.scripts || {})) {
    pushTokens(command, `package.json script "${name}"`);
  }
  if (pkg.main) found.push([normalize(pkg.main), "package.json main"]);

  // Every workflow, not just ci.yml: the desktop build workflow references files
  // that no other source mentions.
  const workflowDir = path.join(REPO_ROOT, ".github", "workflows");
  for (const name of fs.existsSync(workflowDir) ? fs.readdirSync(workflowDir) : []) {
    if (!/\.ya?ml$/.test(name)) continue;
    const relative = `.github/workflows/${name}`;
    pushTokens(read(relative), relative);
  }

  // Everything the browser loads. A missing <script> is a silently dead feature.
  const html = read(`${WEB_PLAYER}/index.html`);
  for (const match of html.matchAll(/<(?:script|link)[^>]*?(?:src|href)="([^"]+)"/g)) {
    const url = match[1].split("?")[0];
    if (!url.startsWith("/") || url.includes("{")) continue;
    found.push([`${WEB_PLAYER}${url}`, `${WEB_PLAYER}/index.html asset tag`]);
  }

  // url() assets, including the 396 sliced font files declared by fonts.css.
  for (const sheet of ["style.css", "fonts.css"]) {
    const css = read(`${WEB_PLAYER}/${sheet}`);
    for (const match of css.matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/g)) {
      const url = match[1].split("?")[0].trim();
      if (/^(?:data:|https?:|#)/.test(url)) continue;
      // Resolve relative to the stylesheet, the way the browser does.
      const resolved = normalize(path.posix.join(WEB_PLAYER, url));
      found.push([resolved, `${WEB_PLAYER}/${sheet} url()`]);
    }
  }

  return found;
}

function main() {
  if (process.argv.includes("--docs")) return checkDocs();
  let tracked;
  try {
    tracked = new Set(git(["ls-files", "-z"]).split("\0").filter(Boolean));
  } catch {
    console.error("check-repo-completeness: `git ls-files` failed; skipping.");
    return 0;
  }

  const references = collectReferences();
  const candidates = new Map();
  for (const [raw, source] of references) {
    if (!raw || raw.includes("{") || raw.startsWith("/")) continue;
    // A bare name with no directory ("powershell.exe") is a command from a
    // script line, not a repo path. Only keep it if it really sits at the root.
    if (!raw.includes("/") && !fs.existsSync(path.join(REPO_ROOT, raw))) continue;
    if (!candidates.has(raw)) candidates.set(raw, new Set());
    candidates.get(raw).add(source);
  }

  const missing = [];
  for (const [relative, sources] of candidates) {
    if (tracked.has(relative)) continue;
    // Deliberately ignored build inputs (the venv, the unpacked Electron
    // distribution, downloaded archives) are expected to be absent from git.
    // `check-ignore` tells us the difference between "ignored on purpose" and
    // "somebody forgot to add it".
    missing.push({ relative, sources: [...sources] });
  }

  let ignored = new Set();
  if (missing.length) {
    try {
      const output = git(["check-ignore", "--stdin"], missing.map((m) => m.relative).join("\n"));
      ignored = new Set(output.split("\n").map((line) => normalize(line.trim())).filter(Boolean));
    } catch (error) {
      // Exit code 1 simply means "none of them are ignored".
      if (error.status !== 1) throw error;
    }
  }

  const untracked = missing.filter((m) => !ignored.has(m.relative));
  const absent = untracked.filter((m) => !fs.existsSync(path.join(REPO_ROOT, m.relative)));

  console.log(`referenced paths: ${candidates.size}   tracked: ${candidates.size - missing.length}`
    + `   ignored by design: ${ignored.size}   untracked: ${untracked.length}`);

  for (const item of untracked) {
    const state = absent.includes(item) ? "MISSING ON DISK" : "NOT TRACKED BY GIT";
    console.error(`\n${state}: ${item.relative}`);
    for (const source of item.sources) console.error(`  referenced by ${source}`);
  }

  if (untracked.length) {
    console.error(`\nFAIL: ${untracked.length} referenced path(s) are neither tracked nor git-ignored.`);
    console.error("A fresh clone would not contain them, so the app or its CI would break.");
    console.error("Fix with `git add <path>`, or add an explicit ignore rule if it is generated.");
    return 1;
  }
  console.log("PASS every referenced path is tracked or deliberately ignored");
  return 0;
}

function checkDocs() {
  const required = [
    "README.md", "README.zh-CN.md", "README.ja.md", "README.de.md", "README.ru.md",
    "docs/README.md", "docs/en/guide.md", "docs/zh-CN/guide.md", "docs/ja/guide.md", "docs/de/guide.md", "docs/ru/guide.md",
    "docs/DEVELOPMENT.md", "docs/PROVIDERS.md", "docs/RELEASING.md", "docs/OPEN_SOURCE_READINESS.md", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "SUPPORT.md", "CHANGELOG.md",
  ];
  const findings = required.filter((relative) => !fs.existsSync(path.join(REPO_ROOT, relative))).map((relative) => `missing required document: ${relative}`);
  const tracked = new Set(git(["ls-files"]).split(/\r?\n/).filter(Boolean));
  const files = [...tracked].filter((file) => /^README(?:\.[^/]+)?\.md$|^docs\/(?:README|DEVELOPMENT|PROVIDERS|RELEASING|OPEN_SOURCE_READINESS)\.md$|^docs\/(?:en|zh-CN|ja|de|ru)\/guide\.md$|^(?:CONTRIBUTING|SECURITY|CODE_OF_CONDUCT|SUPPORT|CHANGELOG)\.md$/.test(file));
  const links = /\[[^\]]*\]\(([^)#]+)(?:#[^)]+)?\)/g;
  for (const relative of files) {
    const text = fs.readFileSync(path.join(REPO_ROOT, relative), "utf8");
    for (const match of text.matchAll(links)) {
      const target = match[1].trim();
      if (/^(?:https?:|mailto:|#)/i.test(target)) continue;
      const clean = target.replace(/^<|>$/g, "");
      const resolved = path.resolve(REPO_ROOT, path.dirname(relative), clean);
      if (!resolved.startsWith(REPO_ROOT + path.sep) || !fs.existsSync(resolved)) findings.push(`${relative}: broken local link ${target}`);
    }
  }
  if (findings.length) { for (const finding of findings) console.error(finding); return 1; }
  console.log(`Documentation check passed: ${required.length} required files and ${files.length} Markdown files scanned.`);
  return 0;
}

try {
  process.exit(main());
} catch (error) {
  console.error(`check-repo-completeness failed to run: ${error.message}`);
  process.exit(2);
}
