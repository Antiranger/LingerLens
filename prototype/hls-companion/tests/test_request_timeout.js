/*
 * A2: the request deadline must cover the response BODY, not just the headers.
 *
 * `fetch()` resolves as soon as the response headers arrive, so racing only the
 * fetch left `await response.text()` outside any deadline: a local service or a
 * desktop-protocol forward that sent headers and then stalled left the call
 * pending forever. `createSerialPoller` waits for `run()` to finish before
 * scheduling the next cycle, so one such call stopped that whole poll chain --
 * status and subtitles silently stopped updating.
 *
 * These tests execute the SHIPPED `request()` (extracted from player.js and run
 * in a vm context), not a copy of it, against a real loopback HTTP server, and
 * drive its timer directly so the 20s/30s budgets never have to elapse.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const vm = require("node:vm");

const PLAYER = path.resolve(__dirname, "../web-player/player.js");

function requestSource() {
  const source = fs.readFileSync(PLAYER, "utf8");
  const start = source.indexOf("  async function request(path, body) {");
  assert.notEqual(start, -1, "request() was not found in player.js");
  const end = source.indexOf("\n  function ", start + 1);
  assert.notEqual(end, -1, "the end of request() was not found in player.js");
  const helper = source.slice(start, end);
  // The body read has to be INSIDE the deadline window. If it moves back out,
  // the deadline covers headers only again and every assertion below is moot.
  assert.ok(
    helper.includes("await response.text()"),
    "the response body is read outside request()'s deadline again",
  );
  return helper;
}

/* The shipped budgets, asserted rather than assumed: this change must not move
 * them. A 20s probe and a 30s everything-else are what the call sites expect. */
function armedDelays(timers) {
  return [...timers.values()].map((timer) => timer.delay).sort((a, b) => a - b);
}

function harness(origin) {
  const timers = new Map();
  let next = 0;
  let lastSignal = null;
  const context = {
    AbortController,
    setTimeout(fn, delay) {
      const id = ++next;
      timers.set(id, { fn, delay });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    fetch(url, options) {
      lastSignal = options.signal;
      return fetch(origin + url, options);
    },
  };
  vm.createContext(context);
  vm.runInContext(`${requestSource()}\nthis.request = request;`, context);
  return {
    context,
    timers,
    signal: () => lastSignal,
    fireTimers() {
      for (const [id, timer] of [...timers]) {
        timers.delete(id);
        timer.fn();
      }
    },
  };
}

function listen(server) {
  return new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
}

async function close(server) {
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}

function tick() {
  return new Promise((resolve) => setImmediate(resolve));
}

/*
 * Bounded settle: "success" | "error" | "pending".
 *
 * On the code this guards against, the call NEVER settles -- that is the whole
 * defect -- so a bare `await` would hang the test runner instead of failing a
 * test. Every wait on a stalled request goes through this.
 */
async function outcomeWithin(promise, ms = 1500) {
  return Promise.race([
    promise.then(() => "success", () => "error"),
    new Promise((resolve) => setTimeout(resolve, ms)).then(() => "pending"),
  ]);
}

async function until(predicate, what) {
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.fail(`timed out waiting for ${what}`);
}

test("a completed request leaves no timer armed and keeps its budget", async () => {
  const server = http.createServer((_req, res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ state: "running" }));
  });
  await listen(server);
  const { context, timers } = harness(`http://127.0.0.1:${server.address().port}`);
  try {
    const payload = await context.request("/api/status");
    // Field-wise on purpose: objects parsed inside the vm context carry that
    // realm's Object.prototype, so a strict deep-equal would compare prototypes.
    assert.equal(payload.state, "running");
    assert.deepEqual(armedDelays(timers), [], "a finished request left a timer armed");
  } finally {
    await close(server);
  }
});

test("the shipped budgets are still 20s for probe and 30s for everything else", async () => {
  const server = http.createServer((_req, res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.write('{"stalled":');
    // Deliberately never ends: the timer is what has to finish this call.
  });
  await listen(server);
  const origin = `http://127.0.0.1:${server.address().port}`;
  try {
    const probe = harness(origin);
    const status = harness(origin);
    probe.context.request("/api/probe").catch(() => {});
    status.context.request("/api/status").catch(() => {});
    await until(() => probe.timers.size > 0 && status.timers.size > 0, "both deadlines to arm");
    assert.deepEqual(armedDelays(probe.timers), [20000]);
    assert.deepEqual(armedDelays(status.timers), [30000]);
    probe.fireTimers();
    status.fireTimers();
    await tick();
  } finally {
    await close(server);
  }
});

test("a body that stalls after its headers is bounded and aborted", async () => {
  let bodyFlushed = false;
  const server = http.createServer((_req, res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.write('{"partial":');
    bodyFlushed = true;
    // never ends
  });
  await listen(server);
  const { context, timers, signal, fireTimers } = harness(
    `http://127.0.0.1:${server.address().port}`,
  );
  const rejections = [];
  const onRejection = (reason) => rejections.push(reason);
  process.on("unhandledRejection", onRejection);

  let settled = "pending";
  // Keep the RAW promise for the bounded wait below: a `.then(ok, err)` wrapper
  // resolves either way, so racing it would always report "success".
  const raw = context.request("/api/status");
  raw.then(() => { settled = "success"; }, () => { settled = "error"; });
  try {
    // Headers and the first body bytes have arrived; the read is now waiting.
    await until(() => bodyFlushed, "the partial body to be flushed");
    await tick();
    assert.equal(settled, "pending", "the stall should still be pending before the deadline");

    fireTimers();
    const outcome = await outcomeWithin(raw);
    assert.equal(
      outcome,
      "error",
      "a stalled body left the call pending past its deadline: this is the defect",
    );
    assert.equal(signal().aborted, true, "reaching the deadline must abort the request");
    assert.deepEqual(armedDelays(timers), [], "the deadline timer was left armed");
    await tick();
    assert.deepEqual(rejections, [], "aborting produced an unhandled rejection");
  } finally {
    process.off("unhandledRejection", onRejection);
    await close(server);
    await outcomeWithin(raw); // now settles: the socket is gone
  }
});

test("an incomplete error body is bounded too, and the next poll still runs", async () => {
  let requests = 0;
  const server = http.createServer((_req, res) => {
    requests += 1;
    if (requests === 1) {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.write('{"error":"backend is unhappy');
      return; // never ends: the failure body itself stalls
    }
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ state: "running" }));
  });
  await listen(server);
  const { context, timers, fireTimers } = harness(
    `http://127.0.0.1:${server.address().port}`,
  );
  try {
    const stalled = context.request("/api/status");
    await until(() => requests === 1, "the stalled error response");
    await tick();
    fireTimers();
    const outcome = await outcomeWithin(stalled);
    assert.equal(
      outcome,
      "error",
      "an incomplete error body left the call pending past its deadline",
    );

    // This is what createSerialPoller does next: it only schedules the following
    // cycle once run() settles, so the chain must not be stuck.
    const second = await context.request("/api/status");
    assert.equal(second.state, "running");
    assert.deepEqual(armedDelays(timers), [], "the second request left a timer armed");
  } finally {
    await close(server);
  }
});
