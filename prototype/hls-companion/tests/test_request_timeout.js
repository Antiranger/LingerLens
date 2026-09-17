/*
 * A2-T: the request deadline must cover the response BODY, and the real serial
 * poller must survive a body that stalls.
 *
 * `fetch()` resolves as soon as the response headers arrive. The pre-A2 code
 * raced only the fetch against a timer and then cleared that timer in the
 * `finally`, so the abort it had armed was cancelled, and `await response.text()`
 * ran outside the race with no deadline and no abort at all: a local service
 * that sent headers and then stalled left the call pending forever.
 * `createSerialPoller` waits for `run()` to finish before arming the next cycle,
 * so one such call stopped that whole poll chain -- status and subtitles
 * silently stopped updating.
 *
 * These tests execute the SHIPPED `request()` (extracted from player.js and run
 * in a vm context) against a real loopback HTTP server, and drive its timer
 * directly so the 20s/30s budgets never have to elapse. They also drive the
 * SHIPPED `createSerialPoller`, because "the next poll still runs" is a property
 * of that poller and not of a second hand-written request.
 *
 * Red/green against the pre-A2 source, without switching the product worktree:
 * copy this file to `<tmp>/tests/`, put `git show 3cc2093^:<path>/player.js` at
 * `<tmp>/web-player/player.js` next to a copy of `poll-loop.js`, then run
 * `node --test <tmp>/tests/test_request_timeout.js`. The body cases must FAIL
 * inside their bounds instead of hanging the runner.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const vm = require("node:vm");

const { createSerialPoller } = require("../web-player/poll-loop.js");

const PLAYER = path.resolve(__dirname, "../web-player/player.js");

/* Bounds. None of these replaces a product budget: they exist so that a defect
 * that never settles fails a test instead of hanging the runner, and so that a
 * stuck socket cannot outlive its own test. */
const TEST_TIMEOUT_MS = 5000; // outer bound for one test
const READY_MS = 3000; // loopback readiness
const SETTLE_MS = 1500; // bounded settle after the deadline fires
const CLEANUP_MS = 1000; // sockets, listeners, pollers

/*
 * Extract the shipped request() by its two boundaries and refuse to guess.
 *
 * The bounds are string literals, so a refactor can move them. That is
 * acceptable as long as it fails loudly: an extraction that silently returns
 * the wrong slice would make every assertion below meaningless. Both the
 * uniqueness of the start and the identity of the function that immediately
 * follows are asserted, and the caller additionally fails if the slice does not
 * compile in the vm.
 */
function requestSource() {
  const source = fs.readFileSync(PLAYER, "utf8");
  const startMarker = "  async function request(path, body) {";
  const start = source.indexOf(startMarker);
  assert.notEqual(start, -1, "request() was not found in player.js");
  assert.equal(
    source.indexOf(startMarker, start + 1),
    -1,
    "request() is no longer defined exactly once: the extraction start is ambiguous",
  );
  const end = source.indexOf("\n  function ", start + 1);
  assert.notEqual(end, -1, "the end of request() was not found in player.js");
  assert.ok(
    source.startsWith("\n  function commonBody(", end),
    "the function immediately after request() is no longer commonBody(): the extraction bound moved",
  );
  return source.slice(start, end);
}

/* Two independent timer registries, on purpose.
 *
 * The shipped request() owns its 20s/30s deadline; createSerialPoller owns its
 * 1s cycle. If they shared one registry, firing the 1s cycle timer could be
 * mistaken for firing the deadline, or the reverse, and the test would prove
 * something about the wrong clock. Keeping them apart is what makes
 * `fireRequestDeadline()` and firing only the poll cycle different operations.
 */
function fakeTimers() {
  const timers = new Map();
  let next = 0;
  return {
    set(fn, delay) {
      const id = ++next;
      timers.set(id, { fn, delay });
      return id;
    },
    clear(id) {
      timers.delete(id);
    },
    delays() {
      return [...timers.values()].map((timer) => timer.delay).sort((a, b) => a - b);
    },
    fire() {
      for (const [id, timer] of [...timers]) {
        timers.delete(id);
        timer.fn();
      }
    },
  };
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

/*
 * The fetch wrapper only WITNESSES the phase; it does not replace the body
 * stream. `text()` is called by the shipped request() when it reaches
 * `await response.text()`, which is exactly the moment the deadline has to
 * cover. The server having called `res.write()` proves nothing about the
 * client -- the bytes may sit in a socket buffer the client never reads.
 */
function harness(origin) {
  const requestTimers = fakeTimers();
  const signals = [];
  const controllers = [];
  let bodyReads = 0;
  const bodyReadStarted = deferred();
  const context = {
    AbortController: class extends AbortController {
      constructor() {
        super();
        controllers.push(this);
      }
    },
    setTimeout: (fn, delay) => requestTimers.set(fn, delay),
    clearTimeout: (id) => requestTimers.clear(id),
    async fetch(url, options) {
      signals.push(options.signal);
      const response = await fetch(origin + url, options);
      return {
        ok: response.ok,
        status: response.status,
        headers: response.headers,
        text() {
          bodyReads += 1;
          bodyReadStarted.resolve();
          return response.text();
        },
      };
    },
  };
  vm.createContext(context);
  try {
    vm.runInContext(`${requestSource()}\nthis.request = request;`, context);
  } catch (error) {
    assert.fail(`the extracted request() does not compile: ${error.message}`);
  }
  return {
    request: (requestPath) => context.request(requestPath),
    requestTimers,
    signals,
    controllers,
    bodyReads: () => bodyReads,
    bodyReadStarted: bodyReadStarted.promise,
    fireRequestDeadline() {
      requestTimers.fire();
    },
  };
}

function collectRejections() {
  const seen = [];
  const handler = (reason) => seen.push(reason);
  process.on("unhandledRejection", handler);
  return { seen, stop: () => process.off("unhandledRejection", handler) };
}

function listen(server) {
  return new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
}

function originOf(server) {
  return `http://127.0.0.1:${server.address().port}`;
}

async function close(server) {
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}

function tick() {
  return new Promise((resolve) => setImmediate(resolve));
}

/* Bounded wait. A bare `await` on the defect would hang the whole runner rather
 * than fail one test, so every wait on a request goes through this. The guard
 * timer is cleared on both outcomes, so a fast settle leaves nothing behind. */
function bounded(promise, ms, what) {
  let timer;
  const guard = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`timed out after ${ms}ms waiting for ${what}`)), ms);
  });
  return Promise.race([promise, guard]).finally(() => clearTimeout(timer));
}

async function outcomeWithin(promise, ms = SETTLE_MS) {
  const outcome = bounded(
    promise.then(
      () => "success",
      () => "error",
    ),
    ms,
    "the request to settle",
  );
  return outcome.catch(() => "pending");
}

async function until(predicate, what, ms = READY_MS) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.fail(`timed out waiting for ${what}`);
}

/* Rejections come from inside the vm realm, so `instanceof Error` compares
 * prototypes across realms and is always false. Assert on the fields instead,
 * and never await a rejection unbounded: on the pre-A2 code the call that was
 * supposed to reject never settles at all, and an unbounded await would hold the
 * server open past the test instead of failing it. */
async function rejectionWithin(promise, ms, what) {
  const settled = await Promise.race([
    promise.then(
      (value) => ({ kind: "success", value }),
      (error) => ({ kind: "error", error }),
    ),
    new Promise((resolve) => setTimeout(() => resolve({ kind: "pending" }), ms)),
  ]);
  assert.equal(settled.kind, "error", `${what}: expected a rejection, got ${settled.kind}`);
  assert.equal(typeof settled.error?.message, "string", `${what}: the rejection carries no message`);
  return settled.error.message;
}

function stallAfterHeaders(res, status = 200) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.write(status === 200 ? '{"partial":' : '{"error":"backend is unhappy');
  // Deliberately never ends: the deadline is what has to finish this call.
}

test(
  "a body that stalls after the client starts reading it is bounded and aborted",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const server = http.createServer((_req, res) => stallAfterHeaders(res, 200));
    await listen(server);
    const h = harness(originOf(server));
    // Keep the RAW promise for the bounded wait: a `.then(ok, err)` wrapper
    // settles either way, so racing it would always report "success".
    const raw = h.request("/api/status");
    let settled = "pending";
    raw.then(
      () => {
        settled = "success";
      },
      () => {
        settled = "error";
      },
    );
    const rejections = collectRejections();
    try {
      await bounded(h.bodyReadStarted, READY_MS, "the client to start reading the body");
      assert.equal(h.bodyReads(), 1, "the shipped request() must read the body itself");
      assert.deepEqual(h.requestTimers.delays(), [30000], "the shipped status budget");
      assert.equal(settled, "pending", "the call settled before its deadline");

      h.fireRequestDeadline();
      assert.equal(
        await outcomeWithin(raw),
        "error",
        "a stalled body left the call pending past its deadline: this is the defect",
      );
      assert.equal(h.signals.at(-1).aborted, true, "reaching the deadline must abort the request");
      assert.deepEqual(h.requestTimers.delays(), [], "the deadline timer was left armed");
      await tick();
      assert.deepEqual(rejections.seen, [], "aborting produced an unhandled rejection");
    } finally {
      rejections.stop();
      await bounded(close(server), CLEANUP_MS, "the server to close");
      await outcomeWithin(raw);
    }
  },
);

test(
  "an incomplete error body is bounded by the same deadline",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const server = http.createServer((_req, res) => stallAfterHeaders(res, 500));
    await listen(server);
    const h = harness(originOf(server));
    const raw = h.request("/api/status");
    try {
      await bounded(h.bodyReadStarted, READY_MS, "the client to start reading the error body");
      assert.deepEqual(h.requestTimers.delays(), [30000]);
      h.fireRequestDeadline();
      assert.equal(
        await outcomeWithin(raw),
        "error",
        "an incomplete error body escaped the deadline",
      );
      assert.deepEqual(h.requestTimers.delays(), [], "the deadline timer was left armed");
    } finally {
      await bounded(close(server), CLEANUP_MS, "the server to close");
      await outcomeWithin(raw);
    }
  },
);

test(
  "the real serial poller survives a stalled cycle and polls again",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    let requests = 0;
    const server = http.createServer((_req, res) => {
      requests += 1;
      if (requests === 1) return stallAfterHeaders(res, 200);
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ state: "running" }));
    });
    await listen(server);
    const h = harness(originOf(server));
    const pollTimers = fakeTimers();
    const failures = [];
    const payloads = [];
    let inFlight = 0;
    let maxInFlight = 0;
    const poller = createSerialPoller({
      run: async () => {
        inFlight += 1;
        maxInFlight = Math.max(maxInFlight, inFlight);
        try {
          payloads.push(await h.request("/api/status"));
        } catch (error) {
          failures.push(error);
        } finally {
          inFlight -= 1;
        }
      },
      intervalMs: 1000,
      hiddenIntervalMs: 1000,
      isHidden: () => false,
      setTimer: (fn, delay) => pollTimers.set(fn, delay),
      clearTimer: (id) => pollTimers.clear(id),
    });
    try {
      poller.start();
      await bounded(h.bodyReadStarted, READY_MS, "the first cycle to start reading");
      assert.equal(h.controllers.length, 1, "more than one request was issued at once");
      assert.equal(poller.armedChains(), 0, "a cycle is in flight; no cycle timer may be armed");

      // Only the request's own deadline. The poller's 1s cycle timer must NOT be
      // what settles this cycle -- that would be a different clock entirely.
      h.fireRequestDeadline();
      await until(
        () => failures.length === 1 && pollTimers.delays().length === 1,
        "the stalled cycle to settle and the next cycle to arm",
      );
      assert.equal(poller.isRunning(), true, "the poller gave up after one stalled cycle");
      assert.match(failures[0].message, /超时/, "the stalled cycle must fail as a timeout");
      assert.deepEqual(pollTimers.delays(), [1000], "exactly one next cycle must be armed");

      // Only the poller's 1s cycle timer.
      pollTimers.fire();
      await until(
        () => payloads.length === 1 && pollTimers.delays().length === 1,
        "the second cycle to complete and arm the next",
      );
      assert.equal(requests, 2, "the second cycle did not reach the server");
      assert.equal(payloads[0].state, "running");
      assert.equal(maxInFlight, 1, "two cycles ran at the same time");
      assert.equal(h.controllers.length, 2, "each cycle must own its own AbortController");
      assert.deepEqual(h.requestTimers.delays(), [], "a completed request left a timer armed");
    } finally {
      poller.stop();
      assert.equal(poller.armedChains(), 0, "stop() left a cycle timer armed");
      assert.deepEqual(pollTimers.delays(), [], "stop() left a cycle timer armed");
      await bounded(close(server), CLEANUP_MS, "the server to close");
    }
  },
);

test("complete, failing, non-JSON and refused responses all settle cleanly", { timeout: TEST_TIMEOUT_MS }, async () => {
  const server = http.createServer((req, res) => {
    if (req.url === "/api/plain") {
      res.writeHead(200, { "Content-Type": "text/plain" });
      res.end("not json at all");
      return;
    }
    if (req.url === "/api/failing") {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "backend is unhappy" }));
      return;
    }
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ state: "running" }));
  });
  await listen(server);
  const h = harness(originOf(server));
  const rejections = collectRejections();

  const refused = http.createServer();
  await listen(refused);
  const refusedOrigin = originOf(refused);
  await close(refused);
  const dead = harness(refusedOrigin);

  try {
    const payload = await bounded(h.request("/api/status"), READY_MS, "a complete response");
    assert.equal(payload.state, "running");

    const failing = await rejectionWithin(h.request("/api/failing"), SETTLE_MS, "an HTTP 500");
    assert.equal(failing, "backend is unhappy", "the JSON error detail is the message");

    const plain = await rejectionWithin(h.request("/api/plain"), SETTLE_MS, "a non-JSON body");
    assert.match(plain, /非 JSON/);

    assert.ok(
      (await outcomeWithin(dead.request("/api/status"), READY_MS)) === "error",
      "a refused connection must reject rather than hang",
    );

    assert.deepEqual(h.requestTimers.delays(), [], "a settled request left a timer armed");
    assert.deepEqual(dead.requestTimers.delays(), [], "a refused request left a timer armed");
    await tick();
    assert.deepEqual(rejections.seen, [], "a settled request produced an unhandled rejection");
  } finally {
    rejections.stop();
    await bounded(close(server), CLEANUP_MS, "the server to close");
  }
});

test(
  "the shipped budgets are 20s for probe and 30s for everything else",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const server = http.createServer((req, res) => {
      if (req.url === "/api/probe") return; // never even sends headers
      stallAfterHeaders(res, 200);
    });
    await listen(server);
    const origin = originOf(server);
    const probe = harness(origin);
    const status = harness(origin);
    const rejections = collectRejections();
    const probeCall = probe.request("/api/probe");
    const statusCall = status.request("/api/status");
    try {
      // Phase 1: headers have NOT arrived. This only proves which budget the
      // timer carries; it says nothing about coverage of the body.
      await until(() => probe.requestTimers.delays().length === 1, "the probe deadline to arm");
      assert.deepEqual(probe.requestTimers.delays(), [20000]);
      probe.fireRequestDeadline();
      assert.equal(
        await outcomeWithin(probeCall),
        "error",
        "a probe that never answers must time out, not succeed",
      );

      // Phase 2: the client has entered the body read before the clock moves.
      await bounded(status.bodyReadStarted, READY_MS, "the status body read to start");
      assert.deepEqual(status.requestTimers.delays(), [30000]);
      status.fireRequestDeadline();
      assert.equal(
        await outcomeWithin(statusCall),
        "error",
        "a stalled body must time out, not succeed",
      );
      assert.deepEqual(status.requestTimers.delays(), []);
      await tick();
      assert.deepEqual(rejections.seen, [], "a timeout produced an unhandled rejection");
    } finally {
      rejections.stop();
      await bounded(close(server), CLEANUP_MS, "the server to close");
    }
  },
);

/*
 * Timeout classification, both ways it can be reached.
 *
 * The implementation has two probe texts -- one written by the timer, one by the
 * AbortError branch -- and which of them wins the race is an environment detail.
 * The contract tested here is the CATEGORY, not the wording: both paths must
 * fail as a timeout, neither may succeed, and neither may extend the budget.
 */
test("a timeout stays a timeout whether the timer or an abort gets there first", { timeout: TEST_TIMEOUT_MS }, async () => {
  const server = http.createServer((_req, res) => stallAfterHeaders(res, 200));
  await listen(server);
  const origin = originOf(server);
  const rejections = collectRejections();

  const timerFirst = harness(origin);
  const abortFirst = harness(origin);
  const timerCall = timerFirst.request("/api/probe");
  const abortCall = abortFirst.request("/api/probe");
  try {
    await bounded(timerFirst.bodyReadStarted, READY_MS, "the body read to start (timer path)");
    timerFirst.fireRequestDeadline();
    const timedOut = await rejectionWithin(timerCall, SETTLE_MS, "the timer path");
    assert.match(timedOut, /超时/, "the timer path must read as a timeout");
    assert.deepEqual(timerFirst.requestTimers.delays(), [], "the timer path left a timer armed");

    await bounded(abortFirst.bodyReadStarted, READY_MS, "the body read to start (abort path)");
    assert.equal(abortFirst.controllers.length, 1);
    abortFirst.controllers[0].abort();
    const aborted = await rejectionWithin(abortCall, SETTLE_MS, "the abort path");
    assert.match(aborted, /超时/, "an abort during the body read must read as a timeout");
    assert.equal(abortFirst.signals.at(-1).aborted, true);
    assert.deepEqual(abortFirst.requestTimers.delays(), [], "the abort path left a timer armed");

    // Firing the clock after the fact must be a no-op: there is nothing left to
    // fire, and the already-cleared deadline must not produce a second failure.
    timerFirst.fireRequestDeadline();
    abortFirst.fireRequestDeadline();
    await tick();
    assert.deepEqual(rejections.seen, [], "a classified timeout produced an unhandled rejection");
  } finally {
    rejections.stop();
    await bounded(close(server), CLEANUP_MS, "the server to close");
    await outcomeWithin(timerCall);
    await outcomeWithin(abortCall);
  }
});
