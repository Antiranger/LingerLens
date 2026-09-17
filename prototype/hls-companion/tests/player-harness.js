/*
 * Shared harness for testing the SHIPPED player.js functions.
 *
 * Why extraction rather than a model: four of the remaining work items have to
 * test functions that live inside player.js's single IIFE (the stop barrier, the
 * playback-recovery caller, the stall overlay, the provider-option renderer). A
 * hand-written model of any of them would pass while the shipped code stayed
 * wrong, which is exactly the failure mode the work plan names. So the function
 * source is sliced out of the real file and executed in a vm, and the test
 * supplies the collaborators it touches as spies.
 *
 * The free variables are a deliberate, explicit part of each test: `state` lists
 * the `let` bindings the function reads or writes, and `globals` lists the
 * collaborators. Anything the function needs and the test did not supply is a
 * ReferenceError at call time, not a silently undefined value.
 *
 * Bounds fail closed. Both boundaries are asserted, the slice must end on a
 * top-level closing brace, and the whole thing must compile in the vm; a
 * refactor that moves a boundary breaks the test loudly instead of testing the
 * wrong text.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const PLAYER = path.resolve(__dirname, "../web-player/player.js");

function playerSource() {
  return fs.readFileSync(PLAYER, "utf8");
}

/*
 * Drop trailing whitespace and any block comments that sit after the last real
 * statement, so the end-of-function check below is about the code rather than
 * about the comment a developer left between two declarations.
 */
function trimTrailingComments(text) {
  let out = text.replace(/\s+$/, "");
  for (;;) {
    const start = out.lastIndexOf("/*");
    if (start === -1) return out;
    const end = out.indexOf("*/", start);
    if (end === -1 || out.slice(end + 2).trim() !== "") return out;
    out = out.slice(0, start).replace(/\s+$/, "");
  }
}

function extractFunction(source, name) {
  const markers = [`\n  async function ${name}(`, `\n  function ${name}(`];
  const hits = markers
    .map((marker) => source.indexOf(marker))
    .filter((index) => index !== -1);
  assert.equal(
    hits.length,
    1,
    `${name}() is not declared exactly once at the top level of player.js`,
  );
  const start = hits[0] + 1;
  const boundaries = [
    source.indexOf("\n  async function ", start),
    source.indexOf("\n  function ", start),
  ].filter((index) => index !== -1);
  const end = boundaries.length ? Math.min(...boundaries) : source.length;
  const text = source.slice(start, end);
  // A slice that stops early can still compile if the cut lands on a statement
  // boundary, so also require the braces to have closed again inside the slice.
  assert.ok(
    trimTrailingComments(text).endsWith("}"),
    `${name}() does not end on a top-level closing brace: the extraction bound moved`,
  );
  return text;
}

/*
 * A DOM node that accepts everything and stores nothing the test cares about.
 * `el()` is called for a couple of dozen ids across these functions, and giving
 * each one a distinct object would add noise without adding evidence.
 */
function stubElement() {
  return {
    textContent: "",
    innerHTML: "",
    title: "",
    value: "",
    checked: false,
    hidden: false,
    disabled: false,
    src: "",
    dataset: {},
    style: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    setAttribute() {},
    removeAttribute() {},
    getAttribute: () => null,
    addEventListener() {},
    removeEventListener() {},
    querySelector: () => stubElement(),
    querySelectorAll: () => [],
    appendChild() {},
    remove() {},
    closest: () => null,
    focus() {},
    blur() {},
    click() {},
    load() {},
    play: async () => {},
    pause() {},
  };
}

function elementLookup() {
  const cache = new Map();
  return (id) => {
    if (!cache.has(id)) cache.set(id, stubElement());
    return cache.get(id);
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
}

/*
 * A `request()` stand-in whose every call is captured unresolved, so a test can
 * decide the ORDER in which the network answers -- which is the whole subject of
 * the stop/start interleavings.
 */
function pendingRequests() {
  const calls = [];
  const request = (requestPath, body) => {
    const call = { path: requestPath, body, ...deferred() };
    calls.push(call);
    return call.promise;
  };
  request.calls = calls;
  request.forPath = (requestPath) => calls.filter((call) => call.path === requestPath);
  return request;
}

function spy(implementation) {
  const calls = [];
  const wrapper = (...args) => {
    calls.push(args);
    return implementation ? implementation(...args) : undefined;
  };
  wrapper.calls = calls;
  wrapper.count = () => calls.length;
  return wrapper;
}

/*
 * Build a vm context containing the named shipped functions plus the state and
 * collaborators the caller supplies.
 *
 * `state` values must be JSON-representable primitives: they become `let`
 * bindings inside the vm, and `read()`/`write()` bridge them back out so a test
 * can observe what the shipped function did to module-level state.
 */
function playerContext({ state = {}, globals = {}, functions = [] } = {}) {
  const source = playerSource();
  const names = Object.keys(state);
  const declarations = names
    .map((name) => `let ${name} = ${JSON.stringify(state[name])};`)
    .join("\n");
  const body = functions.map((name) => extractFunction(source, name)).join("\n");
  const readFields = names.map((name) => `    ${JSON.stringify(name)}: ${name},`).join("\n");
  const writeFields = names
    .map(
      (name) =>
        `    if (Object.prototype.hasOwnProperty.call(patch, ${JSON.stringify(name)})) ` +
        `${name} = patch[${JSON.stringify(name)}];`,
    )
    .join("\n");
  const prelude = [
    declarations,
    body,
    "  this.__read = () => ({",
    readFields,
    "  });",
    "  this.__write = (patch) => {",
    writeFields,
    "  };",
  ].join("\n");

  const context = { ...globals };
  vm.createContext(context);
  try {
    vm.runInContext(prelude, context);
  } catch (error) {
    assert.fail(`the extracted player functions do not compile: ${error.message}`);
  }
  for (const name of functions) {
    assert.equal(typeof context[name], "function", `${name}() was not extracted`);
  }
  return {
    context,
    call: (name, ...args) => context[name](...args),
    read: () => context.__read(),
    write: (patch) => context.__write(patch),
  };
}

/*
 * Everything start()/stop()/refreshStatus()/resetStoppedUi() touch apart from
 * each other. Returned as a bag so a test can add or override single entries.
 */
function sessionGlobals(overrides = {}) {
  const stubs = {
    el: elementLookup(),
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    stage: stubElement(),
    video: stubElement(),
    hls: null,
    targetSelector: { value: "zh-Hans" },
    diagnosticsBar: { push: spy() },
    attach: spy(),
    destroyPlayer: spy(),
    setSessionAction: spy(),
    setBusy: spy(),
    setMediaLoading: spy(),
    setState: spy(),
    showError: spy(),
    renderSessionControls: spy(),
    updateStallOverlay: spy(),
    updatePlaybackRecovery: spy(),
    updateSubtitleBudget: spy(),
    commonBody: () => ({ qualityId: "auto" }),
    sourcePolicyFromUi: () => ({ mode: "auto" }),
    validateLanguageSettingsClient: () => null,
    persistLanguageSettings: async () => {},
    bufferAhead: () => 7.5,
    estimateVideoLatency: () => 1.25,
    updateLabel: (key, fallback) => fallback || key,
    escapeHtml: (value) => String(value),
    seconds: (value) => `${value}s`,
    integer: (value) => String(value),
    costText: () => "—",
    costsText: () => "—",
    authToken: null,
  };
  return { ...stubs, ...overrides };
}

module.exports = {
  PLAYER,
  deferred,
  elementLookup,
  extractFunction,
  pendingRequests,
  playerContext,
  playerSource,
  sessionGlobals,
  spy,
  stubElement,
  trimTrailingComments,
};
