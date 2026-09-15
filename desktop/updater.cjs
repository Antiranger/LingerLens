"use strict";

/*
 * Update checking for the packaged desktop app.
 *
 * No npm dependencies, on purpose: desktop/audit-package.cjs allows an explicit
 * handful of files inside app.asar, so electron-updater and its transitive tree
 * would either fail the build or force that allowlist open. What this app
 * actually needs is small -- fetch one JSON, compare two version strings,
 * download a file, check its SHA-256, run it -- and all of it is in Node's
 * standard library.
 *
 * The trust anchor is the manifest's SHA-256, fetched over HTTPS from a public
 * release channel. A tampered installer fails the hash check and is deleted
 * rather than run. That is not as strong as code signing (nothing here verifies
 * the publisher), so the manifest URL must stay on a host the project controls.
 *
 * Everything here is pure enough to unit test without Electron: `fetch` is
 * injected, and no module reads `app` or `process.versions` at load time.
 */

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

/* Public release channel. The source repository is private; this one only ever
   holds installers and this manifest. `latest/download` is a GitHub redirect to
   the newest release's asset, so the URL does not change per version. */
const DEFAULT_MANIFEST_URL = "https://github.com/Antiranger/LagLingo-releases/releases/latest/download/manifest.json";
const MANIFEST_ENV = "LAGLINGO_UPDATE_URL";
const REQUEST_TIMEOUT_MS = 20000;

/* Only ever follow https. A file:// or http:// manifest would let a local
   attacker point the updater at their own build. */
function isHttps(value) {
  try {
    return new URL(String(value)).protocol === "https:";
  } catch {
    return false;
  }
}

/*
 * The manifest URL is trusted if it is https, or http on loopback.
 *
 * Loopback is not a network position: anything able to serve on 127.0.0.1
 * already runs code on this machine, so it can replace the app directly rather
 * than trick the updater into doing it. Allowing it is what makes the updater
 * testable against a local server instead of only against a published release.
 */
function isTrustedManifestUrl(value) {
  let parsed;
  try {
    parsed = new URL(String(value));
  } catch {
    return false;
  }
  if (parsed.protocol === "https:") return true;
  if (parsed.protocol !== "http:") return false;
  return ["127.0.0.1", "localhost", "[::1]", "::1"].includes(parsed.hostname);
}

/*
 * Numeric-segment comparison, enough for `1.2.3` and `1.2.3-rc.1`.
 *
 * A pre-release sorts BELOW the release it leads to (1.2.3-rc.1 < 1.2.3), which
 * is the semver rule that matters here: shipping an rc must not look like a
 * downgrade to someone already on the final build.
 */
function parseVersion(value) {
  const text = String(value || "").trim().replace(/^v/i, "");
  if (!/^\d+(\.\d+)*([-+].*)?$/.test(text)) return null;
  const [core, ...rest] = text.split(/[-+]/);
  return {
    numbers: core.split(".").map((part) => Number(part)),
    prerelease: rest.length ? rest.join("-") : "",
  };
}

function compareVersions(left, right) {
  const a = parseVersion(left);
  const b = parseVersion(right);
  if (!a || !b) return 0;
  const length = Math.max(a.numbers.length, b.numbers.length);
  for (let index = 0; index < length; index += 1) {
    const diff = (a.numbers[index] || 0) - (b.numbers[index] || 0);
    if (diff) return diff > 0 ? 1 : -1;
  }
  if (a.prerelease === b.prerelease) return 0;
  if (!a.prerelease) return 1;
  if (!b.prerelease) return -1;
  return a.prerelease > b.prerelease ? 1 : -1;
}

function isNewer(candidate, current) {
  return compareVersions(candidate, current) > 0;
}

/*
 * Validate the manifest before anything acts on it. An update offer is the one
 * piece of remote input that leads to code execution, so every field the
 * installer step depends on is checked here rather than at the call site.
 */
function readManifest(payload) {
  if (!payload || typeof payload !== "object") return null;
  const version = parseVersion(payload.version);
  const installer = payload.installer;
  if (!version || !installer || typeof installer !== "object") return null;
  if (!isTrustedManifestUrl(installer.url)) return null;
  if (!/^[a-f0-9]{64}$/i.test(String(installer.sha256 || ""))) return null;
  const size = Number(installer.size);
  if (!Number.isFinite(size) || size <= 0) return null;
  return {
    version: String(payload.version).trim().replace(/^v/i, ""),
    notes: typeof payload.notes === "string" ? payload.notes.slice(0, 2000) : "",
    publishedAt: typeof payload.publishedAt === "string" ? payload.publishedAt : "",
    installer: { url: installer.url, sha256: String(installer.sha256).toLowerCase(), size },
  };
}

function manifestUrl(env = process.env) {
  const override = String(env[MANIFEST_ENV] || "").trim();
  return isTrustedManifestUrl(override) ? override : DEFAULT_MANIFEST_URL;
}

async function fetchJson(url, fetchImpl, timeoutMs = REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(url, {
      signal: controller.signal,
      redirect: "follow",
      // GitHub's release redirect serves a cached asset; a stale manifest would
      // hide a release for as long as the cache lives.
      cache: "no-store",
      headers: { Accept: "application/json", "User-Agent": "LagLingo-updater" },
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}

/*
 * Download to `<destination>.part` and rename only after the hash matches, so a
 * truncated or tampered download can never be run: the installer path either
 * does not exist or is a file whose bytes were verified.
 */
async function downloadVerified({ url, sha256, size, destination, fetchImpl, onProgress, timeoutMs = 600000 }) {
  const partial = `${destination}.part`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let handle;
  try {
    const response = await fetchImpl(url, {
      signal: controller.signal, redirect: "follow", cache: "no-store",
      headers: { "User-Agent": "LagLingo-updater" },
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const expected = Number(size);
    const declared = Number(response.headers?.get?.("content-length"));
    if (Number.isFinite(declared) && declared > 0 && declared !== expected) {
      throw new Error(`size mismatch: manifest ${expected}, server ${declared}`);
    }
    await fs.promises.mkdir(path.dirname(destination), { recursive: true });
    await fs.promises.rm(partial, { force: true });
    handle = await fs.promises.open(partial, "w");
    const hash = crypto.createHash("sha256");
    let written = 0;
    for await (const chunk of response.body) {
      const buffer = Buffer.from(chunk);
      hash.update(buffer);
      written += buffer.length;
      await handle.write(buffer);
      if (onProgress) onProgress(written, expected);
    }
    await handle.close();
    handle = null;
    if (written !== expected) throw new Error(`short download: ${written} of ${expected} bytes`);
    const actual = hash.digest("hex");
    if (actual !== sha256) throw new Error(`checksum mismatch: expected ${sha256}, got ${actual}`);
    await fs.promises.rm(destination, { force: true });
    await fs.promises.rename(partial, destination);
    return { path: destination, bytes: written };
  } catch (error) {
    if (handle) await handle.close().catch(() => {});
    await fs.promises.rm(partial, { force: true }).catch(() => {});
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

/*
 * State machine for one update attempt. The caller supplies the side effects
 * (fetch, download, install) so this stays testable and so main.cjs owns
 * anything that touches Electron.
 *
 * States: idle -> checking -> current | available -> downloading -> ready
 *                                            \-> failed (from any step)
 */
function createUpdater(options = {}) {
  const currentVersion = String(options.currentVersion || "0.0.0");
  const fetchImpl = options.fetch || globalThis.fetch;
  const download = options.download || downloadVerified;
  const onChange = options.onChange || (() => {});
  const now = options.now || (() => Date.now());
  let state = { status: "idle", currentVersion, checkedAt: null, error: null, update: null, progress: null };

  function set(patch) {
    state = { ...state, ...patch };
    onChange(state);
    return state;
  }

  return {
    getState: () => state,

    async check() {
      if (!fetchImpl) return set({ status: "failed", error: "no fetch available" });
      set({ status: "checking", error: null });
      try {
        const payload = await fetchJson(manifestUrl(options.env), fetchImpl);
        const manifest = readManifest(payload);
        if (!manifest) return set({ status: "failed", error: "malformed manifest", checkedAt: now() });
        if (!isNewer(manifest.version, currentVersion)) {
          return set({ status: "current", checkedAt: now(), update: null });
        }
        return set({ status: "available", checkedAt: now(), update: manifest });
      } catch (error) {
        return set({ status: "failed", error: error.message || String(error), checkedAt: now() });
      }
    },

    /* Download to `destination` and leave the verified installer path in state. */
    async download(destination) {
      const update = state.update;
      if (!update) return set({ status: "failed", error: "nothing to download" });
      set({ status: "downloading", error: null, progress: { received: 0, total: update.installer.size } });
      try {
        const result = await download({
          ...update.installer,
          destination,
          fetchImpl,
          onProgress: (received, total) => set({ progress: { received, total } }),
        });
        return set({ status: "ready", progress: { received: result.bytes, total: update.installer.size }, installerPath: result.path });
      } catch (error) {
        return set({ status: "failed", error: error.message || String(error) });
      }
    },
  };
}

module.exports = {
  DEFAULT_MANIFEST_URL,
  MANIFEST_ENV,
  compareVersions,
  createUpdater,
  downloadVerified,
  isHttps,
  isTrustedManifestUrl,
  isNewer,
  manifestUrl,
  parseVersion,
  readManifest,
};
