/*
 * S1 (page half): which session the stop barrier belongs to.
 *
 * A Stop is a local action, so the backend keeps describing the OLD session for
 * as long as its teardown takes, and the poll must not act on that. The previous
 * rule was "one flag, cleared only by a successful Start", which fails in two
 * directions at once:
 *
 *   - one Start that fails (a language check, a rejected request) left the flag
 *     set forever, so the poll skipped `attach` for the rest of the page's life;
 *   - a status response already in flight when the user pressed Stop was applied
 *     on arrival, which re-attached the dismissed playlist and reverted the
 *     stopped controls.
 *
 * These tests run the SHIPPED start/stop/refreshStatus/resetStoppedUi (sliced out
 * of player.js and executed in a vm) against a request() whose every call the
 * test resolves by hand, so the ORDER of the answers is the subject rather than
 * something the test has to hope for.
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  pendingRequests,
  playerContext,
  sessionGlobals,
} = require("./player-harness.js");

const TEST_TIMEOUT_MS = 5000;

function session({ request, state = {}, overrides = {} } = {}) {
  const globals = sessionGlobals({ request, ...overrides });
  const harness = playerContext({
    state: {
      stopRequested: false,
      pendingStop: null,
      lastSessionState: "idle",
      observedMediaSessionId: null,
      stoppedMediaSessionId: null,
      uiGeneration: 0,
      stopUnconfirmedLogged: false,
      sessionAction: null,
      controlsBusy: false,
      browserLatency: null,
      ...state,
    },
    globals,
    functions: ["start", "stop", "refreshStatus", "resetStoppedUi"],
  });
  return { ...harness, globals };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

function unconfirmedWarnings(globals) {
  return globals.diagnosticsBar.push.calls.filter(([, , message]) =>
    String(message).includes("停止状态未确认"),
  );
}

test(
  "a Start that fails does not latch the poll out of attaching (D)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const request = pendingRequests();
    const h = session({
      request,
      state: { lastSessionState: "running", observedMediaSessionId: "A" },
      overrides: { validateLanguageSettingsClient: () => "请先选择目标语言" },
    });
    h.context.el("subtitlesEnabled").checked = true;

    h.call("stop");
    const stopCall = request.forPath("/api/stop")[0];
    assert.ok(stopCall, "Stop must ask the server to clean up");

    // Teardown still reports session A as running.
    const firstPoll = h.call("refreshStatus");
    request.forPath("/api/status")[0].resolve({
      state: "running",
      mediaSessionId: "A",
      playlistUrl: "/hls/live.m3u8",
    });
    await firstPoll;
    assert.equal(h.context.attach.count(), 0, "the stopped session was re-attached");

    // The server confirms the stop, so the barrier comes down here -- not only
    // on a later successful Start.
    stopCall.resolve({ ok: true });
    await settle();
    assert.equal(h.read().stopRequested, false, "a confirmed stop must release the barrier");

    // This page's next Start fails at the language check and never reaches the
    // server. That failure used to leave the barrier up forever.
    await h.call("start");
    assert.equal(h.globals.showError.count(), 1, "the start failure must still be reported");
    assert.equal(request.forPath("/api/start").length, 0, "start must not reach the server");

    // Another tab starts session B.
    const secondPoll = h.call("refreshStatus");
    request.forPath("/api/status")[1].resolve({
      state: "running",
      mediaSessionId: "B",
      playlistUrl: "/hls/live.m3u8",
    });
    await secondPoll;

    assert.equal(h.context.attach.count(), 1, "a new session was never attached: the latch");
    assert.deepEqual(h.context.attach.calls[0], ["/hls/live.m3u8"]);
  },
);

test(
  "a status response that predates a local Stop changes nothing (D)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const request = pendingRequests();
    const h = session({
      request,
      state: { lastSessionState: "running", observedMediaSessionId: "A" },
    });

    const inFlight = h.call("refreshStatus");
    h.call("stop");
    request.forPath("/api/status")[0].resolve({
      state: "running",
      mediaSessionId: "A",
      playlistUrl: "/hls/live.m3u8",
    });
    await inFlight;

    assert.equal(h.context.attach.count(), 0, "a pre-Stop response re-attached the stopped playlist");
    assert.equal(
      h.read().lastSessionState,
      "idle",
      "a pre-Stop response reverted the stopped controls",
    );
  },
);

test(
  "a Stop the server never confirms still releases the barrier for a DIFFERENT session (D)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const request = pendingRequests();
    const h = session({
      request,
      state: { lastSessionState: "running", observedMediaSessionId: "A" },
    });

    h.call("stop");
    request.forPath("/api/stop")[0].reject(new Error("本地后台响应超时，请重试。"));
    await settle();
    assert.equal(h.read().stopRequested, true, "a timeout is not a confirmation");
    assert.equal(h.context.diagnosticsBar.push.count(), 1, "the unconfirmed stop must be recorded");

    // No idle sample ever arrives, and the same session must not come back.
    const sameSession = h.call("refreshStatus");
    request.forPath("/api/status")[0].resolve({
      state: "running",
      mediaSessionId: "A",
      playlistUrl: "/hls/live.m3u8",
    });
    await sameSession;
    assert.equal(h.context.attach.count(), 0, "the session the user stopped came back");

    // A genuinely different session, still with no idle sample in between.
    const newSession = h.call("refreshStatus");
    request.forPath("/api/status")[1].resolve({
      state: "running",
      mediaSessionId: "B",
      playlistUrl: "/hls/live.m3u8",
    });
    await newSession;
    assert.equal(h.context.attach.count(), 1, "a new session could not be attached");
    assert.equal(h.read().stopRequested, false);
    assert.equal(h.read().observedMediaSessionId, "B");
  },
);

test(
  "an unknown identity is never treated as a different session (D)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const request = pendingRequests();
    // Nothing observed an identity before the Stop, so nothing can be compared.
    const h = session({ request, state: { lastSessionState: "running" } });

    h.call("stop");
    request.forPath("/api/stop")[0].reject(new Error("timeout"));
    await settle();

    for (const index of [0, 1]) {
      const poll = h.call("refreshStatus");
      request.forPath("/api/status")[index].resolve({
        state: "running",
        mediaSessionId: "Z",
        playlistUrl: "/hls/live.m3u8",
      });
      await poll;
    }

    assert.equal(h.context.attach.count(), 0, "an unconfirmable session was guessed to be new");
    assert.equal(
      unconfirmedWarnings(h.globals).length,
      1,
      "the unconfirmable stop must be recorded exactly once, not once per poll",
    );
    assert.equal(h.read().stopRequested, true, "the barrier must hold");
  },
);

test(
  "a Start that completes after a later Stop does not clear the newer barrier (D)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const request = pendingRequests();
    const h = session({ request });

    const starting = h.call("start");
    await settle();
    const startCall = request.forPath("/api/start")[0];
    assert.ok(startCall, "start must reach the server");

    h.call("stop");
    assert.equal(h.read().stopRequested, true, "Stop must raise the barrier immediately");

    startCall.resolve({
      ok: true,
      quality: { width: 1920, height: 1080 },
      status: { state: "running", mediaSessionId: "C" },
    });
    await starting;

    assert.equal(h.read().stopRequested, true, "a stale Start cleared the newest Stop's barrier");
    assert.equal(
      h.read().observedMediaSessionId,
      null,
      "a stale Start adopted a session it no longer owns",
    );
    assert.equal(h.read().lastSessionState, "idle", "a stale Start reverted the stopped controls");
    assert.equal(
      h.context.setSessionAction.count(),
      2,
      "the stale Start's finally block cleared the newer Stop's session action",
    );
  },
);

test(
  "a poll during this page's own Start does not attach the previous session (D)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const request = pendingRequests();
    const h = session({
      request,
      state: { sessionAction: "starting", observedMediaSessionId: "A" },
    });

    const during = h.call("refreshStatus");
    request.forPath("/api/status")[0].resolve({
      state: "running",
      mediaSessionId: "A",
      playlistUrl: "/hls/live.m3u8",
    });
    await during;
    assert.equal(h.context.attach.count(), 0, "the session being replaced was attached");
    assert.equal(h.read().observedMediaSessionId, "A", "server truth is still recorded");

    h.write({ sessionAction: null });
    const after = h.call("refreshStatus");
    request.forPath("/api/status")[1].resolve({
      state: "running",
      mediaSessionId: "A",
      playlistUrl: "/hls/live.m3u8",
    });
    await after;
    assert.equal(h.context.attach.count(), 1, "the poll must attach once the Start has settled");
  },
);

test(
  "a successful Start hands the poll its identity (D)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const request = pendingRequests();
    const h = session({ request });

    const starting = h.call("start");
    await settle();
    request.forPath("/api/start")[0].resolve({
      ok: true,
      quality: { width: 1920, height: 1080 },
      status: { state: "running", mediaSessionId: "D" },
    });
    await starting;

    assert.equal(h.read().observedMediaSessionId, "D", "the Start response carries the identity");
    assert.equal(h.read().stopRequested, false);
    assert.equal(h.context.showError.count(), 0);

    const poll = h.call("refreshStatus");
    request.forPath("/api/status")[0].resolve({
      state: "running",
      mediaSessionId: "D",
      playlistUrl: "/hls/live.m3u8",
    });
    await poll;
    assert.equal(h.context.attach.count(), 1);
  },
);

test(
  "repeated Stop clicks issue one request, and the controls are never left busy (D/G)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const request = pendingRequests();
    const h = session({
      request,
      state: { lastSessionState: "running", observedMediaSessionId: "A" },
    });

    h.call("stop");
    h.call("stop");
    h.call("stop");

    assert.equal(request.forPath("/api/stop").length, 1, "each click asked the server again");
    assert.equal(
      h.globals.setBusy.calls.at(-1)[0],
      false,
      "the controls were left busy after a local stop",
    );
    assert.equal(h.read().stopRequested, true);

    // While the stop request is still in flight it owns the barrier, so even an
    // idle sample does not release it. That is the plan's ordering: "the server
    // is still running the stop" is checked before "this sample says idle", and
    // the stale UI is already correct either way.
    const during = h.call("refreshStatus");
    request.forPath("/api/status")[0].resolve({ state: "idle", mediaSessionId: null });
    await during;
    assert.equal(h.read().stopRequested, true, "the in-flight stop is still the owner");

    request.forPath("/api/stop")[0].resolve({ ok: true });
    await settle();
    assert.equal(h.read().stopRequested, false, "a confirmed stop must release the barrier");

    const after = h.call("refreshStatus");
    request.forPath("/api/status")[1].resolve({ state: "idle", mediaSessionId: null });
    await after;
    assert.equal(h.context.showError.count(), 0);
    assert.equal(h.context.attach.count(), 0, "an idle session has nothing to attach");
  },
);

test(
  "an idle sample releases the barrier when no stop is in flight (G)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const request = pendingRequests();
    const h = session({
      request,
      state: {
        stopRequested: true,
        lastSessionState: "idle",
        observedMediaSessionId: "A",
        stoppedMediaSessionId: "A",
      },
    });

    const poll = h.call("refreshStatus");
    request.forPath("/api/status")[0].resolve({ state: "idle", mediaSessionId: null });
    await poll;

    assert.equal(h.read().stopRequested, false, "an idle sample must still release the barrier");
    assert.equal(h.read().stoppedMediaSessionId, null);
  },
);

test(
  "a stalled status call is not an exception path (G)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const request = pendingRequests();
    const h = session({ request });
    const poll = h.call("refreshStatus");
    request.forPath("/api/status")[0].reject(new Error("本地后台响应超时，请重试。"));
    await poll;
    // request() surfaces a timeout as an ordinary rejection, so the poll reports
    // it rather than throwing out of the poller's cycle.
    assert.equal(h.globals.showError.count(), 1);
    assert.equal(h.context.attach.count(), 0);
  },
);

test(
  "the status poll states the playhead, and omits it when the clock is unavailable",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    // The backend feeds a realtime ASR and must know how far ahead of the viewer
    // it is; it cannot get that from HLS requests, which hls.js issues 6-15s
    // ahead of the playhead. So the page states it, on the poll it already sends.
    const withClock = pendingRequests();
    const h = session({
      request: withClock,
      overrides: { mediaClock: { playingWallTime: () => 1789724606.141 } },
    });
    const poll = h.call("refreshStatus");
    const call = withClock.forPath("/api/status")[0];
    assert.ok(call, "the poll must still be a plain status request");
    assert.equal(call.path, "/api/status?playhead=1789724606.141");
    call.resolve({ state: "idle", mediaSessionId: null });
    await poll;

    // No PDT yet (the clock returns null): report nothing rather than invent one.
    const withoutClock = pendingRequests();
    const other = session({
      request: withoutClock,
      overrides: { mediaClock: { playingWallTime: () => null } },
    });
    const secondPoll = other.call("refreshStatus");
    const secondCall = withoutClock.forPath("/api/status")[0];
    assert.equal(secondCall.path, "/api/status");
    secondCall.resolve({ state: "idle", mediaSessionId: null });
    await secondPoll;
  },
);

// A request begun AFTER Stop has the new generation, but its server sample
// can still describe the stopped session while cleanup is running.
test('a post-Stop status sample cannot restore controls before cleanup confirms', async () => {
  const request = pendingRequests();
  const h = session({request, state:{lastSessionState:'running',observedMediaSessionId:'A'}});
  h.call('stop');
  const poll = h.call('refreshStatus');
  request.forPath('/api/status')[0].resolve({state:'running',mediaSessionId:'A',playlistUrl:'/hls/live.m3u8'});
  await poll;
  assert.equal(h.read().lastSessionState,'idle','controls must retain local stop, not pending server state');
  assert.equal(h.context.attach.count(),0);
  request.forPath('/api/stop')[0].resolve({ok:true});
  await settle();
});
