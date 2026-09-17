// Drive the REAL app page through a real Start -> Stop -> Start, over CDP.
//
// This is the page-side counterpart to live-backend-smoke.py. The backend script
// proves the server's contract; this one proves the page can actually be driven
// through it -- which is where a stop barrier that never comes down would show up
// as the UI never leaving "已停止" again.
//
// It attaches to an ALREADY RUNNING dev app. Do not start one from here:
//
//   npm run desktop:dev  (or the user's own launch, with --remote-debugging-port=9222)
//
// Safety: it only clicks the page's own buttons and changes nothing else except
// muting the video element so the test makes no noise -- the ASR leg downloads its
// own audio server-side, so muting changes nothing the test cares about. It does
// NOT unmute afterwards. It leaves the session stopped.
//
// Two things this learned the hard way, both kept in the code:
//   * 开始播放 is DISABLED after a stop; the page requires a fresh 解析 first. A test
//     that clicks the disabled button proves nothing.
//   * a startable state is `startDisabled === false && startHidden === false`.
//
// Node 22+ only, for the built-in WebSocket. Usage:
//
//   node prototype/hls-companion/scripts/live-page-smoke.js
//   LL_PAGE_STREAM=https://www.youtube.com/@tbsnewsdig/live node .../live-page-smoke.js
// The plan's S1 acceptance test, for real: one Start -> Stop -> Start in the live
// app, driven through its own buttons. Muted first so it makes no noise; the ASR
// leg downloads its own audio server-side, so muting changes nothing that matters.
//
// Everything is read back from the page itself. Console errors and page exceptions
// are captured, because the failure this test exists to catch (the stop barrier
// latching shut) would show up as the UI never leaving "已停止" again.
const STREAM = process.env.LL_PAGE_STREAM || 'https://www.youtube.com/@ANNnewsCH/live';
const DEADLINE = Date.now() + 7 * 60 * 1000;

const list = await (await fetch('http://127.0.0.1:9222/json')).json();
const page = list.filter((t) => t.type === 'page')[0];
const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
const events = [];
const send = (method, params) =>
  new Promise((resolve) => { const n = ++id; pending.set(n, resolve); ws.send(JSON.stringify({ id: n, method, params })); });
ws.addEventListener('message', (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); return; }
  if (msg.method === 'Runtime.consoleAPICalled') {
    events.push({ kind: 'console', level: msg.params.type,
      text: (msg.params.args || []).map((a) => a.value ?? a.description ?? '').join(' ').slice(0, 300) });
  }
  if (msg.method === 'Runtime.exceptionThrown') {
    events.push({ kind: 'exception',
      text: (msg.params.exceptionDetails?.exception?.description || msg.params.exceptionDetails?.text || '').slice(0, 300) });
  }
  if (msg.method === 'Log.entryAdded') {
    events.push({ kind: 'log', level: msg.params.entry.level, text: String(msg.params.entry.text).slice(0, 300) });
  }
});
await new Promise((resolve) => ws.addEventListener('open', resolve));
await send('Runtime.enable', {});
await send('Log.enable', {});

const evaluate = async (expression) => {
  const reply = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (reply.result?.exceptionDetails) return { __error: reply.result.exceptionDetails.text };
  return reply.result?.result?.value;
};

const snapshot = () => evaluate(`JSON.stringify({
  badge: document.getElementById('stateBadge')?.textContent?.trim(),
  state: document.getElementById('stateText')?.textContent?.trim(),
  message: document.getElementById('message')?.textContent?.trim(),
  feedback: document.getElementById('setupFeedback')?.textContent?.trim(),
  hiddenDelay: document.getElementById('hiddenDelay')?.textContent?.trim(),
  buffer: document.getElementById('buffer')?.textContent?.trim(),
  uptime: document.getElementById('uptime')?.textContent?.trim(),
  resolution: document.getElementById('resolution')?.textContent?.trim(),
  startDisabled: document.getElementById('start')?.disabled,
  stopDisabled: document.getElementById('stop')?.disabled,
  startHidden: document.getElementById('start')?.hidden,
  videoReadyState: document.getElementById('video')?.readyState,
  videoPaused: document.getElementById('video')?.paused,
  videoSrc: (document.getElementById('video')?.currentSrc || '').replace(/\\?.*$/, '').slice(-42),
  subtitlesEnabled: document.getElementById('subtitlesEnabled')?.checked,
  startLabel: document.getElementById('start')?.textContent?.trim(),
})`);

const timeline = [];
const record = async (label) => {
  const raw = await snapshot();
  let parsed; try { parsed = JSON.parse(raw); } catch { parsed = { raw }; }
  timeline.push({ at: new Date().toISOString().slice(11, 19), label, ...parsed });
  console.log(label, JSON.stringify(parsed));
  return parsed;
};
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

console.log('--- before: is the page idle? ---');
await record('idle');

// The real flow needs a fresh 解析 before EACH start: after a stop the page puts
// itself back in "needs probing" state and disables 开始播放 (its own feedback says
// "已停止。可以重新解析…"). The first attempt at this test clicked the disabled
// button and proved nothing -- that was the harness's mistake, not the page's.
const probeUntilStartable = async (round) => {
  console.log(`--- round ${round}: paste the url and press 解析 ---`);
  await evaluate(`(() => {
    const input = document.getElementById('url');
    input.value = ${JSON.stringify(STREAM)};
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return input.value;
  })()`);
  await evaluate(`document.getElementById('probe').click()`);
  for (let i = 0; i < 40 && Date.now() < DEADLINE; i += 1) {
    await wait(2000);
    const state = await record(`round${round}-probing`);
    if (state.startDisabled === false && state.startHidden === false) return true;
    if (state.feedback && /失败|错误|无法/.test(state.feedback)) {
      console.log('!! probe reported a problem:', state.feedback);
      return false;
    }
  }
  return false;
};

for (const round of [1, 2]) {
  const startable = await probeUntilStartable(round);
  console.log(`round ${round} reached a startable state =`, startable);
  if (!startable) { console.log(`STOPPING: round ${round} could not reach a startable state.`); break; }

  await evaluate(`document.getElementById('video').muted = true`);
  console.log(`--- round ${round}: click 开始播放 ---`);
  await evaluate(`document.getElementById('start').click()`);
  const until = Date.now() + (round === 1 ? 80000 : 60000);
  while (Date.now() < until && Date.now() < DEADLINE) {
    await wait(5000);
    await record(`round${round}-running`);
  }
  console.log(`--- round ${round}: click 停止 ---`);
  await evaluate(`document.getElementById('stop').click()`);
  let stopped = false;
  for (let i = 0; i < 25 && Date.now() < DEADLINE; i += 1) {
    await wait(2000);
    const state = await record(`round${round}-stopping`);
    if (state.state && state.state.includes('停止') && state.stopDisabled === true) { stopped = true; break; }
  }
  console.log(`round ${round} stopped cleanly =`, stopped);
  if (!stopped) console.log(`!! round ${round}: the UI never returned to a stopped state`);
  await wait(3000);
}

console.log('\n=== console / exception / log entries seen during the test ===');
const noisy = events.filter((e) => e.kind !== 'console' || e.level === 'error' || e.level === 'warning');
console.log(noisy.length ? JSON.stringify(noisy, null, 1) : '(none)');
console.log('\n=== timeline ===');
console.log(JSON.stringify(timeline, null, 1));
ws.close();
process.exit(0);

