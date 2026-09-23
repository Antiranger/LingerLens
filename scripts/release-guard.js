#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const args = process.argv.slice(2);
function option(name) {
  const index = args.indexOf(name);
  return index === -1 ? null : args[index + 1];
}

const root = path.resolve(option("--root") || path.join(__dirname, ".."));
const filesFrom = option("--files-from");
let tracked;
if (filesFrom) {
  tracked = fs.readFileSync(filesFrom, "utf8").split(/\r?\n/).filter(Boolean);
} else {
  const git = spawnSync("git", ["ls-files", "-z"], { cwd: root, encoding: "buffer" });
  if (git.status !== 0) {
    console.error("Release guard could not enumerate tracked files.");
    process.exit(2);
  }
  tracked = git.stdout.toString("utf8").split("\0").filter(Boolean);
}

const forbiddenPaths = [
  /^(?:build-desktop|release|\.scratch)(\/|$)/i,
  /^\.archive\/(?!README\.md$)/i,
  /(^|\/)(?:\.venv(?:[-_][^/]*)?|venv|\.agents?|\.pytest_cache)(\/|$)/i,
  /(^|\/)runtime\/(?!providers\.example\.json$)/i,
  /(^|\/)(?:output|stream|logs?|cache|captures?)(\/|$)/i,
  /(^|\/)\.playwright-cli(\/|$)/i,
  /(^|\/)\.planning(\/|$)/i,
  /(^|\/)\.benchmark-data(\/|$)/i,
  /(^|\/)benchmark-results(\/|$)/i,
  /(^|\/)node_modules(\/|$)/i,
  /(^|\/)__pycache__(\/|$)/i,
  /(^|\/)\.env(?:\.|$)/i,
  /(?:^|\/)(?:(?:auth-snapshot|providers)\.json|control\.secret|cookies\.txt)$/i,
  /\.(?:log|m3u8|m4s|ts|mp4|webm|wav|pcm|cookie|cookies)$/i,
];

const textExtensions = new Set([
  ".cmd", ".cjs", ".css", ".html", ".ini", ".js", ".json", ".md", ".ps1", ".py", ".spec", ".sh", ".toml", ".txt", ".xml", ".yaml", ".yml",
]);
const credentialPatterns = [
  { name: "credential material", regex: /["']?(?:api[_-]?key|access[_-]?token|client[_-]?secret|control[_-]?secret|password)["']?\s*[:=]\s*["'][^"'\r\n]{8,}["']/i },
  { name: "private key material", regex: /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/ },
  { name: "provider token", regex: /\b(?:sk|xox[baprs]|gh[opsu])[-_][A-Za-z0-9_-]{20,}\b/ },
];

const findings = [];
for (const relativeRaw of tracked) {
  const relative = relativeRaw.replaceAll("\\", "/");
  const allowExample = /(?:^|\/)(?:providers\.example\.json|\.env\.example)$/i.test(relative);
  if (!allowExample && forbiddenPaths.some((pattern) => pattern.test(relative))) {
    findings.push(`${relative}: forbidden release path`);
    continue;
  }
  const absolute = path.join(root, relativeRaw);
  if (!fs.existsSync(absolute) || (!allowExample && !textExtensions.has(path.extname(relative).toLowerCase()))) continue;
  const contents = fs.readFileSync(absolute, "utf8");
  for (const pattern of credentialPatterns) {
    const isFixtureOrDocumentation = /(^|\/)(?:tests?|docs?)(\/|$)/i.test(relative);
    // Generic assignments in prose/tests are often intentional fixtures, but
    // recognizable provider tokens and private keys must be checked everywhere.
    if (isFixtureOrDocumentation && pattern.name === "credential material") continue;
    if (pattern.regex.test(contents)) findings.push(`${relative}: possible ${pattern.name}`);
  }
}

if (findings.length) {
  console.error(`Release guard failed with ${findings.length} finding(s):`);
  for (const finding of findings) console.error(`- ${finding}`);
  console.error("Secret values are intentionally not displayed.");
  process.exit(1);
}
console.log(`Release guard passed for ${tracked.length} tracked file(s).`);
