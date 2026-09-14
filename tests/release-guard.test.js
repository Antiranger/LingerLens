const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const root = path.resolve(__dirname, "..");
const guard = path.join(root, "scripts", "release-guard.js");

function runGuard(files) {
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), "laglingo-release-guard-"));
  for (const [relative, contents] of Object.entries(files)) {
    const target = path.join(fixture, relative);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, contents);
  }
  const list = path.join(fixture, "tracked.txt");
  fs.writeFileSync(list, Object.keys(files).join("\n") + "\n");
  return spawnSync(process.execPath, [guard, "--root", fixture, "--files-from", list], {
    cwd: root,
    encoding: "utf8",
  });
}

test("release guard accepts sanitized examples", () => {
  const result = runGuard({
    "prototype/hls-companion/runtime/providers.example.json": '{"apiKeyEnv":"DASHSCOPE_API_KEY","apiKey":""}',
    "docs/security.md": "Never commit private provider catalogs or Cookie headers.\n",
  });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /release guard passed/i);
});

test("release guard rejects private runtime paths without printing contents", () => {
  const secret = "this-value-must-never-be-printed";
  const result = runGuard({ "prototype/hls-companion/runtime/providers.json": secret });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /runtime\/providers\.json/);
  assert.doesNotMatch(result.stderr + result.stdout, new RegExp(secret));
});

test("release guard rejects credential assignments without printing values", () => {
  const secret = "sk-proj-abcdefghijklmnopqrstuvwxyz012345";
  const result = runGuard({ "config/local.json": `{"apiKey":"${secret}"}` });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /credential material/i);
  assert.doesNotMatch(result.stderr + result.stdout, new RegExp(secret));
});

test("release guard rejects media, logs, caches, and agent state", () => {
  for (const relative of [
    "capture/segment.m4s",
    "logs/companion.log",
    ".playwright-cli/session.json",
    ".planning/state.json",
    "prototype/hls-companion/.benchmark-data/result.json",
    "release/LagLingo-setup.exe",
    "build-desktop/backend/runtime.json",
    ".venv-desktop/pyvenv.cfg",
  ]) {
    const result = runGuard({ [relative]: "fixture" });
    assert.notEqual(result.status, 0, relative);
    assert.doesNotMatch(result.stderr + result.stdout, /fixture/);
  }
});

test("repository tracked files pass the release guard", () => {
  const result = spawnSync(process.execPath, [guard], { cwd: root, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
});
