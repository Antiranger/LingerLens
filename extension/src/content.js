(() => {
  if (window.top !== window || window.__lingerLensDelaySpikeLoaded) return;
  window.__lingerLensDelaySpikeLoaded = true;

  const { getAdapter, metrics } = window.LiveDelaySpike;
  const adapter = getAdapter();
  const SAMPLE_INTERVAL_MS = 1000;
  const CORRECTION_THRESHOLD_SECONDS = 2;
  const AUTO_JUMP_NEAR_EDGE_SECONDS = 1.25;
  const AUTO_JUMP_MIN_CHANGE_SECONDS = 2;

  const state = {
    targetDelay: 5,
    mode: "viewer",
    monitoring: false,
    controlEnabled: false,
    video: null,
    videoGeneration: 0,
    samples: [],
    events: [],
    startedAt: null,
    initialSeekSucceeded: null,
    initialSeekPending: false,
    initialSeekTarget: null,
    driftCorrectionCount: 0,
    autoJumpToLiveCount: 0,
    videoReloadCount: 0,
    bufferingCount: 0,
    sourceTabStoppedCount: 0,
    manualRecoveryRequired: false,
    failureReason: "",
    lastSample: null,
    sourceStallSeconds: 0,
    correctionInFlightUntil: 0,
    userSeekUntil: 0,
    lastCorrectionAt: 0
  };

  const panel = createPanel();
  document.documentElement.append(panel.root);
  wirePanel();
  setInterval(tick, SAMPLE_INTERVAL_MS);
  tick();

  function createPanel() {
    const root = document.createElement("section");
    root.id = "lingerlens-delay-spike";
    root.setAttribute("aria-label", "LingerLens live delay test panel");
    root.innerHTML = `
      <header class="llds-header">
        <div>
          <strong>Live Delay Spike</strong>
          <span data-field="platform">${adapter.label}</span>
        </div>
        <button type="button" data-action="collapse" aria-label="Collapse panel">−</button>
      </header>
      <div class="llds-body">
        <div class="llds-status-row">
          <span class="llds-dot" data-field="status-dot"></span>
          <span data-field="status">Waiting for video</span>
          <span class="llds-role" data-field="role">Viewer</span>
        </div>
        <div class="llds-controls">
          <label>Role
            <select data-control="mode">
              <option value="viewer">Viewer / delayed</option>
              <option value="source">Source / observe</option>
            </select>
          </label>
          <label>Target
            <select data-control="target">
              <option value="5">5 seconds</option>
              <option value="10">10 seconds</option>
            </select>
          </label>
        </div>
        <div class="llds-actions">
          <button type="button" class="llds-primary" data-action="start">Set delay & start</button>
          <button type="button" data-action="stop" disabled>Stop</button>
        </div>
        <dl class="llds-metrics">
          <div><dt>Actual delay</dt><dd data-field="delay">—</dd></div>
          <div><dt>Seekable window</dt><dd data-field="window">—</dd></div>
          <div><dt>Media time</dt><dd data-field="media-time">—</dd></div>
          <div><dt>Live edge</dt><dd data-field="live-edge">—</dd></div>
          <div><dt>State</dt><dd data-field="media-state">—</dd></div>
          <div><dt>Stable band</dt><dd data-field="stable-ratio">—</dd></div>
        </dl>
        <div class="llds-counters">
          <span>Corrections <b data-field="corrections">0</b></span>
          <span>Live jumps <b data-field="jumps">0</b></span>
          <span>Reloads <b data-field="reloads">0</b></span>
          <span>Buffering <b data-field="buffering">0</b></span>
          <span>Source stops <b data-field="source-stops">0</b></span>
        </div>
        <div class="llds-note" data-field="note">Open the same live room in two tabs. Use Source in one and Viewer in the other.</div>
        <div class="llds-export">
          <button type="button" data-action="json">Export JSON</button>
          <button type="button" data-action="csv">Export CSV</button>
          <button type="button" data-action="reset">Reset data</button>
        </div>
      </div>`;

    const field = (name) => root.querySelector(`[data-field="${name}"]`);
    const control = (name) => root.querySelector(`[data-control="${name}"]`);
    const action = (name) => root.querySelector(`[data-action="${name}"]`);
    return { root, field, control, action };
  }

  function wirePanel() {
    panel.control("target").addEventListener("change", (event) => {
      state.targetDelay = Number(event.target.value);
      addEvent("target_changed", { targetDelay: state.targetDelay });
      render(state.lastSample);
    });

    panel.control("mode").addEventListener("change", (event) => {
      state.mode = event.target.value;
      state.controlEnabled = false;
      panel.field("role").textContent = state.mode === "viewer" ? "Viewer" : "Source";
      panel.action("start").textContent = state.mode === "viewer" ? "Set delay & start" : "Start observation";
      addEvent("role_changed", { mode: state.mode });
      render(state.lastSample);
    });

    panel.action("start").addEventListener("click", () => {
      state.monitoring = true;
      state.startedAt ||= new Date().toISOString();
      state.failureReason = "";
      state.manualRecoveryRequired = false;
      if (state.mode === "viewer") {
        state.controlEnabled = true;
        state.initialSeekPending = true;
        attemptSeek("initial");
      } else {
        state.controlEnabled = false;
        addEvent("source_observation_started");
      }
      updateButtons();
    });

    panel.action("stop").addEventListener("click", () => {
      state.monitoring = false;
      state.controlEnabled = false;
      addEvent("monitoring_stopped");
      updateButtons();
      render(state.lastSample);
    });

    panel.action("collapse").addEventListener("click", () => {
      const collapsed = panel.root.classList.toggle("is-collapsed");
      panel.action("collapse").textContent = collapsed ? "+" : "−";
      panel.action("collapse").setAttribute("aria-label", collapsed ? "Expand panel" : "Collapse panel");
    });

    panel.action("json").addEventListener("click", () => download("json"));
    panel.action("csv").addEventListener("click", () => download("csv"));
    panel.action("reset").addEventListener("click", resetData);
  }

  function updateButtons() {
    panel.action("start").disabled = state.monitoring;
    panel.action("stop").disabled = !state.monitoring;
    panel.control("mode").disabled = state.monitoring;
  }

  function tick() {
    attachVideo(adapter.findVideo());
    const sample = sampleVideo();
    state.lastSample = sample;

    if (state.monitoring) {
      state.samples.push(sample);
      detectBehavior(sample);
      if (state.mode === "viewer" && state.controlEnabled) maintainDelay(sample);
      console.info("[LingerLens Live Delay]", sample);
    }

    render(sample);
  }

  function attachVideo(nextVideo) {
    if (nextVideo === state.video) return;
    if (state.video && nextVideo) {
      state.videoReloadCount += 1;
      addEvent("video_element_replaced", { videoReloadCount: state.videoReloadCount });
      if (state.monitoring && state.mode === "viewer") state.initialSeekPending = true;
    }
    state.video = nextVideo;
    state.videoGeneration += 1;
    state.lastSample = null;
    state.sourceStallSeconds = 0;

    if (!nextVideo) return;
    const generation = state.videoGeneration;
    const on = (eventName, handler) => nextVideo.addEventListener(eventName, (event) => {
      if (state.videoGeneration !== generation || state.video !== nextVideo) return;
      handler(event);
    }, { passive: true });

    on("waiting", () => {
      state.bufferingCount += 1;
      addEvent("buffering", { event: "waiting", bufferingCount: state.bufferingCount });
    });
    on("stalled", () => {
      state.bufferingCount += 1;
      addEvent("buffering", { event: "stalled", bufferingCount: state.bufferingCount });
    });
    on("emptied", () => {
      state.videoReloadCount += 1;
      addEvent("video_emptied", { videoReloadCount: state.videoReloadCount });
      if (state.monitoring && state.mode === "viewer") state.initialSeekPending = true;
    });
    on("seeking", () => {
      if (performance.now() > state.correctionInFlightUntil) state.userSeekUntil = performance.now() + 3500;
    });
    on("play", () => addEvent("play"));
    on("pause", () => addEvent("pause"));
  }

  function sampleVideo() {
    const timestamp = new Date().toISOString();
    const video = state.video;
    if (!video) {
      return baseSample(timestamp, { videoFound: false, failureReason: "video_not_found" });
    }

    const ranges = [];
    for (let index = 0; index < video.seekable.length; index += 1) {
      ranges.push({ start: video.seekable.start(index), end: video.seekable.end(index) });
    }
    const lastRange = ranges.at(-1) || null;
    const liveEdge = lastRange?.end ?? null;
    const actualDelay = Number.isFinite(liveEdge) ? liveEdge - video.currentTime : null;
    const seekableWindowLength = lastRange ? lastRange.end - lastRange.start : 0;

    return baseSample(timestamp, {
      videoFound: true,
      pageVisibility: document.visibilityState,
      videoGeneration: state.videoGeneration,
      currentTime: video.currentTime,
      paused: video.paused,
      playbackRate: video.playbackRate,
      readyState: video.readyState,
      networkState: video.networkState,
      seeking: video.seeking,
      ended: video.ended,
      seekableLength: video.seekable.length,
      seekableRanges: ranges,
      seekableStart: lastRange?.start ?? null,
      seekableEnd: liveEdge,
      liveEdge,
      actualDelay,
      seekableWindowLength,
      targetError: Number.isFinite(actualDelay) ? actualDelay - state.targetDelay : null
    });
  }

  function baseSample(timestamp, values) {
    return {
      timestamp,
      elapsedSeconds: state.startedAt ? (Date.now() - Date.parse(state.startedAt)) / 1000 : 0,
      platform: adapter.id,
      platformLabel: adapter.label,
      pageId: adapter.pageId(),
      url: location.href,
      mode: state.mode,
      targetDelay: state.targetDelay,
      monitoring: state.monitoring,
      ...values
    };
  }

  function detectBehavior(sample) {
    const previous = state.samples.at(-2);
    if (!previous || !sample.videoFound || !previous.videoFound) return;

    if (state.mode === "source" && !sample.paused && sample.readyState >= 2) {
      const advanced = sample.currentTime - previous.currentTime;
      state.sourceStallSeconds = advanced < 0.2 ? state.sourceStallSeconds + 1 : 0;
      if (state.sourceStallSeconds === 3) {
        state.sourceTabStoppedCount += 1;
        addEvent("source_tab_stopped", {
          pageVisibility: sample.pageVisibility,
          sourceTabStoppedCount: state.sourceTabStoppedCount
        });
      }
    }

    const delayDrop = previous.actualDelay - sample.actualDelay;
    const nearEdge = sample.actualDelay <= AUTO_JUMP_NEAR_EDGE_SECONDS;
    const wasMeaningfullyDelayed = previous.actualDelay >= Math.max(3, state.targetDelay - 1);
    const notOurSeek = performance.now() > state.correctionInFlightUntil;
    const notUserSeek = performance.now() > state.userSeekUntil;
    if (nearEdge && wasMeaningfullyDelayed && delayDrop >= AUTO_JUMP_MIN_CHANGE_SECONDS && notOurSeek && notUserSeek) {
      state.autoJumpToLiveCount += 1;
      addEvent("likely_auto_jump_to_live", {
        previousDelay: previous.actualDelay,
        actualDelay: sample.actualDelay,
        autoJumpToLiveCount: state.autoJumpToLiveCount
      });
    }
  }

  function maintainDelay(sample) {
    if (!sample.videoFound) {
      state.failureReason = "video_not_found";
      return;
    }
    if (sample.seekableLength === 0 || !Number.isFinite(sample.liveEdge)) {
      state.failureReason = "seekable_unavailable";
      return;
    }
    if (sample.seekableWindowLength < state.targetDelay) {
      state.failureReason = "seekable_window_too_short";
      return;
    }
    state.failureReason = "";

    if (state.initialSeekPending) {
      attemptSeek("initial");
      return;
    }

    if (!Number.isFinite(sample.actualDelay) || sample.seeking) return;
    const error = Math.abs(sample.actualDelay - state.targetDelay);
    if (error > CORRECTION_THRESHOLD_SECONDS && performance.now() - state.lastCorrectionAt > 2500) {
      addEvent("drift", { actualDelay: sample.actualDelay, targetError: sample.targetError });
      attemptSeek("drift_correction");
    }
  }

  function attemptSeek(reason) {
    const video = state.video;
    if (!video || video.seekable.length === 0) return false;
    const rangeIndex = video.seekable.length - 1;
    const start = video.seekable.start(rangeIndex);
    const liveEdge = video.seekable.end(rangeIndex);
    const target = liveEdge - state.targetDelay;
    if (target < start) {
      state.failureReason = "seekable_window_too_short";
      if (reason === "initial") state.initialSeekSucceeded = false;
      addEvent("seek_failed", { reason, failureReason: state.failureReason, start, liveEdge, target });
      return false;
    }

    state.correctionInFlightUntil = performance.now() + 3500;
    state.lastCorrectionAt = performance.now();
    state.initialSeekTarget = target;
    try {
      video.currentTime = target;
      if (reason === "initial") {
        state.initialSeekPending = false;
        window.setTimeout(() => verifyInitialSeek(target), 1400);
      } else {
        state.driftCorrectionCount += 1;
      }
      addEvent("seek_requested", { reason, target, liveEdge, targetDelay: state.targetDelay });
      return true;
    } catch (error) {
      state.failureReason = "current_time_assignment_failed";
      if (reason === "initial") state.initialSeekSucceeded = false;
      addEvent("seek_failed", { reason, failureReason: state.failureReason, message: error.message });
      return false;
    }
  }

  function verifyInitialSeek(target) {
    if (!state.video || state.initialSeekTarget !== target) return;
    const sample = sampleVideo();
    const succeeded = Number.isFinite(sample.actualDelay) && Math.abs(sample.actualDelay - state.targetDelay) <= 2;
    state.initialSeekSucceeded = succeeded;
    if (!succeeded) {
      state.failureReason ||= "initial_seek_not_observed";
      state.manualRecoveryRequired = true;
    }
    addEvent("initial_seek_verified", {
      succeeded,
      requestedCurrentTime: target,
      observedCurrentTime: sample.currentTime,
      actualDelay: sample.actualDelay
    });
    render(sample);
  }

  function addEvent(type, details = {}) {
    const event = { timestamp: new Date().toISOString(), type, ...details };
    state.events.push(event);
    console.info("[LingerLens Live Delay event]", event);
  }

  function render(sample) {
    const hasVideo = sample?.videoFound;
    const delay = hasVideo && Number.isFinite(sample.actualDelay) ? sample.actualDelay : null;
    const inBand = delay != null && Math.abs(delay - state.targetDelay) <= 1;
    const summary = metrics.summarize(state.samples, state.targetDelay);

    panel.field("delay").textContent = formatSeconds(delay);
    panel.field("window").textContent = formatSeconds(sample?.seekableWindowLength);
    panel.field("media-time").textContent = formatSeconds(sample?.currentTime);
    panel.field("live-edge").textContent = formatSeconds(sample?.liveEdge);
    panel.field("media-state").textContent = !hasVideo
      ? "No video"
      : `${sample.paused ? "Paused" : "Playing"} · ${sample.playbackRate.toFixed(2)}× · RS${sample.readyState}`;
    panel.field("stable-ratio").textContent = summary.targetBandRatio == null
      ? "—"
      : `${(summary.targetBandRatio * 100).toFixed(1)}%`;
    panel.field("corrections").textContent = state.driftCorrectionCount;
    panel.field("jumps").textContent = state.autoJumpToLiveCount;
    panel.field("reloads").textContent = state.videoReloadCount;
    panel.field("buffering").textContent = state.bufferingCount;
    panel.field("source-stops").textContent = state.sourceTabStoppedCount;

    let status = "Ready to observe";
    let tone = "ready";
    if (!hasVideo) {
      status = "Waiting for primary video";
      tone = "waiting";
    } else if (state.failureReason) {
      status = humanFailure(state.failureReason);
      tone = "error";
    } else if (state.monitoring && state.mode === "source") {
      status = "Observing source timeline";
      tone = state.sourceStallSeconds >= 3 ? "error" : "active";
    } else if (state.monitoring && delay == null) {
      status = "Seekable live edge unavailable";
      tone = "waiting";
    } else if (state.monitoring && inBand) {
      status = `Stable at ${state.targetDelay}s target`;
      tone = "stable";
    } else if (state.monitoring) {
      status = "Monitoring delay drift";
      tone = "active";
    }
    panel.field("status").textContent = status;
    panel.field("status-dot").dataset.tone = tone;

    panel.field("note").textContent = state.failureReason
      ? `Failure reason: ${state.failureReason}. Export the log before reloading.`
      : state.monitoring
        ? `${state.samples.length} samples · ${state.events.length} events · ${document.visibilityState} tab`
        : "Open the same live room in two tabs. Use Source in one and Viewer in the other.";
  }

  function humanFailure(reason) {
    const labels = {
      video_not_found: "Primary video not found",
      seekable_unavailable: "No seekable DVR range",
      seekable_window_too_short: "DVR window is shorter than target",
      current_time_assignment_failed: "Browser rejected the seek",
      initial_seek_not_observed: "Platform did not keep the initial seek"
    };
    return labels[reason] || reason;
  }

  function formatSeconds(value) {
    return Number.isFinite(value) ? `${value.toFixed(2)}s` : "—";
  }

  function buildReport() {
    return {
      schemaVersion: 1,
      exportedAt: new Date().toISOString(),
      session: {
        platform: adapter.id,
        platformLabel: adapter.label,
        pageId: adapter.pageId(),
        url: location.href,
        mode: state.mode,
        targetDelay: state.targetDelay,
        startedAt: state.startedAt,
        durationSeconds: state.startedAt ? (Date.now() - Date.parse(state.startedAt)) / 1000 : 0,
        initialSeekSucceeded: state.initialSeekSucceeded,
        driftCorrectionCount: state.driftCorrectionCount,
        autoJumpToLiveCount: state.autoJumpToLiveCount,
        videoReloadCount: state.videoReloadCount,
        bufferingCount: state.bufferingCount,
        sourceTabStoppedCount: state.sourceTabStoppedCount,
        manualRecoveryRequired: state.manualRecoveryRequired,
        failureReason: state.failureReason || null
      },
      summary: metrics.summarize(state.samples, state.targetDelay),
      events: state.events,
      samples: state.samples
    };
  }

  function download(format) {
    const report = buildReport();
    const contents = format === "json"
      ? JSON.stringify(report, null, 2)
      : metrics.samplesToCsv(report.samples);
    const mime = format === "json" ? "application/json" : "text/csv";
    const blob = new Blob([contents], { type: `${mime};charset=utf-8` });
    const link = document.createElement("a");
    const page = String(adapter.pageId()).replace(/[^a-z0-9_-]+/gi, "-").slice(0, 48);
    link.href = URL.createObjectURL(blob);
    link.download = `live-delay-${adapter.id}-${page}-${state.mode}-${state.targetDelay}s-${Date.now()}.${format}`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  function resetData() {
    state.samples = [];
    state.events = [];
    state.startedAt = state.monitoring ? new Date().toISOString() : null;
    state.initialSeekSucceeded = null;
    state.driftCorrectionCount = 0;
    state.autoJumpToLiveCount = 0;
    state.videoReloadCount = 0;
    state.bufferingCount = 0;
    state.sourceTabStoppedCount = 0;
    state.manualRecoveryRequired = false;
    state.failureReason = "";
    state.sourceStallSeconds = 0;
    addEvent("data_reset");
    render(state.lastSample);
  }
})();
