/* LingerLens 新野兽派原型 —— 模拟交互（无真实后端） */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);

  /* ── 顶栏时钟 ── */
  const pad = (n) => String(n).padStart(2, "0");
  const fmt = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  setInterval(() => { $("topClock").textContent = fmt(new Date()); }, 1000);

  /* ── 滚动揭示 ── */
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e, i) => {
      if (e.isIntersecting) {
        e.target.style.transitionDelay = `${i * 70}ms`;
        e.target.classList.add("revealed");
        io.unobserve(e.target);
      }
    });
  }, { threshold: 0.08 });
  document.querySelectorAll(".reveal").forEach((el) => io.observe(el));

  /* ── 工作台选项卡 ── */
  const workbench = $("workbench");
  document.querySelectorAll(".tabs .tab[data-view]").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tabs .tab[data-view]").forEach((t) => {
        t.classList.toggle("active", t === tab);
        t.setAttribute("aria-selected", t === tab ? "true" : "false");
      });
      workbench.className = `workbench view-${tab.dataset.view}`;
    });
  });

  /* ── 跟随开关 ── */
  const followSubs = $("followSubtitlesBtn");
  const followChat = $("followChatBtn");
  [followSubs, followChat].forEach((btn) =>
    btn.addEventListener("click", () => btn.classList.toggle("active"))
  );

  /* ── 清空时间轴 ── */
  const emptyMsg = (text) => {
    const d = document.createElement("div");
    d.className = "timeline-empty";
    d.textContent = text;
    return d;
  };
  const subsTimeline = $("subtitlesTimeline");
  const chatTimeline = $("chatTimeline");
  $("clearSubtitlesBtn").addEventListener("click", () => {
    subsTimeline.replaceChildren(emptyMsg(I18N.t("empty.subs.cleared")));
  });
  $("clearChatBtn").addEventListener("click", () => {
    chatTimeline.replaceChildren(emptyMsg(I18N.t("empty.chat.cleared")));
    chatCount = 0;
    $("chatStats").textContent = I18N.t("chat.count", { n: 0 });
  });

  /* ── 对话框 ── */
  const bindDialog = (openBtn, dialog) => {
    openBtn.addEventListener("click", () => dialog.showModal());
    dialog.querySelectorAll("[data-close]").forEach((b) =>
      b.addEventListener("click", () => dialog.close())
    );
    dialog.addEventListener("click", (e) => {
      if (e.target === dialog) dialog.close();
    });
  };
  bindDialog($("openModelSettings"), $("modelSettingsDialog"));
  bindDialog($("manageModels"), $("modelSettingsDialog"));
  bindDialog($("openCookieImport"), $("cookieImportDialog"));
  document.querySelectorAll(".dialog-tabs .tab").forEach((tab) =>
    tab.addEventListener("click", () => {
      tab.parentElement.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    })
  );

  /* ── 模拟数据 ── */
  const SUBS = [
    ["みなさん、こんばんは！今日も配信に来てくれてありがとう！", "大家晚上好！谢谢你们今天也来直播！"],
    ["今日は新しい曲を練習したいと思います。", "今天想练习一首新曲子。"],
    ["ちょっとマイクの音量大きいかな？大丈夫そう？", "麦克风声音会不会有点大？没问题吧？"],
    ["コメントいっぱいありがとう！読んでいくね。", "谢谢大家的评论！我来看看。"],
    ["このゲーム、実は三週間前から練習してたんだよね。", "这个游戏，其实我三周前就开始练了。"],
    ["えっ、今の見た？すごくない？", "诶，刚才那个看到了吗？很厉害吧？"],
    ["次の配信は金曜日の夜八時からです。", "下次直播是周五晚上八点开始。"],
    ["みんなのおかげで登録者が十万人を超えました！", "托大家的福，订阅超过十万人了！"],
  ];
  const CHATS = [
    ["猫又おかゆ", "こんばんは〜！", "晚上好〜！"],
    ["夜空メル", "今日も可愛い！", "今天也很可爱！"],
    ["白上フブキ", "新曲楽しみ！", "期待新曲子！"],
    ["大神ミオ", "音量ちょうどいいよ", "音量刚刚好哦"],
    ["大空スバル", "きたああああ", "来了啊啊啊啊"],
    ["桃鈴ねね", "十万登録おめでとう！", "恭喜十万订阅！"],
    ["獅白ぼたん", "今日のサムネ良き", "今天的封面真不错"],
    ["雪花ラミィ", "待機してた！", "一直在待机！"],
  ];

  /* ── 开播流程 ── */
  const stage = document.querySelector(".stage");
  const emptyState = $("emptyState");
  const setupPlayback = $("setupPlayback");
  const setupFeedback = $("setupFeedback");
  const stateBadge = $("stateBadge");
  const stateText = $("stateText");
  let live = false;
  let timers = [];
  let chatCount = 0;

  $("probe").addEventListener("click", () => {
    const url = $("url").value.trim();
    if (!url) {
      setupFeedback.textContent = I18N.t("feedback.needurl");
      setupFeedback.classList.remove("ok");
      $("url").focus();
      return;
    }
    setupFeedback.textContent = I18N.t("feedback.probing");
    setTimeout(() => {
      setupPlayback.hidden = false;
      setupFeedback.textContent = I18N.t("feedback.probed");
      setupFeedback.classList.add("ok");
    }, 700);
  });

  const spawnSubtitle = () => {
    const [src, tgt] = SUBS[Math.floor(Math.random() * SUBS.length)];
    subsTimeline.querySelector(".timeline-empty")?.remove();
    const cue = document.createElement("div");
    cue.className = "cue cue-fresh";
    const now = new Date();
    cue.innerHTML = `<span class="cue-time">${fmt(now)}</span><p class="cue-src"></p><p class="cue-tgt"></p>`;
    cue.querySelector(".cue-src").textContent = src;
    cue.querySelector(".cue-tgt").textContent = tgt;
    subsTimeline.appendChild(cue);
    setTimeout(() => cue.classList.remove("cue-fresh"), 1600);
    if (followSubs.classList.contains("active")) {
      subsTimeline.scrollTop = subsTimeline.scrollHeight;
    }
    // 舞台字幕
    const layer = $("subtitleLayer");
    layer.querySelector(".sub-src").textContent = src;
    layer.querySelector(".sub-tgt").textContent = tgt;
  };

  const spawnChat = () => {
    const [user, text, trans] = CHATS[Math.floor(Math.random() * CHATS.length)];
    chatTimeline.querySelector(".timeline-empty")?.remove();
    const msg = document.createElement("div");
    msg.className = "chat-msg";
    msg.innerHTML = `<div class="chat-head"><span class="chat-user"></span><span class="chat-time">${fmt(new Date())}</span></div><p class="chat-text"></p><p class="chat-trans"></p>`;
    msg.querySelector(".chat-user").textContent = user;
    msg.querySelector(".chat-text").textContent = text;
    msg.querySelector(".chat-trans").textContent = trans;
    chatTimeline.appendChild(msg);
    chatCount += 1;
    $("chatStats").textContent = I18N.t("chat.count", { n: chatCount });
    if (followChat.classList.contains("active")) {
      chatTimeline.scrollTop = chatTimeline.scrollHeight;
    }
    if (msg.parentElement.children.length > 60) msg.parentElement.firstElementChild.remove();
  };

  const spawnDanmaku = () => {
    if (!$("chatOverlayToggle").checked) return;
    const [, text] = CHATS[Math.floor(Math.random() * CHATS.length)];
    if ($("hidePureEmojiToggle").checked && /^[^\w\u4e00-\u9fff]+$/.test(text)) return;
    const item = document.createElement("span");
    item.className = "danmaku-item";
    const roll = Math.random();
    if (roll > 0.8) item.classList.add("dm-lime");
    else if (roll > 0.65) item.classList.add("dm-yellow");
    item.textContent = text;
    const lane = Math.floor(Math.random() * 7);
    item.style.top = `${4 + lane * 12}%`;
    item.style.animationDuration = `${6 + Math.random() * 4}s`;
    item.addEventListener("animationend", () => item.remove());
    $("danmakuLayer").appendChild(item);
  };

  const tickClock = () => {
    const now = fmt(new Date());
    $("subtitleClock").textContent = now;
    $("currentWallTime").textContent = now;
    $("seekEnd").textContent = now;
  };

  const tickTelemetry = () => {
    const r = (a, b, f = 1) => (a + Math.random() * (b - a)).toFixed(f);
    $("hiddenDelay").textContent = `${r(10, 18)}s`;
    $("playerDelay").textContent = `${r(2, 5)}s`;
    $("buffer").textContent = `${r(14, 30)}s`;
    $("resolution").textContent = "1920×1080 · 60";
    $("asrUsage").textContent = `${r(3, 8, 2)} 分钟`;
    $("translationLatency").textContent = `${r(0.6, 1.4, 2)}s`;
    $("subtitleReadyLag").textContent = `${r(1.2, 2)}s / ${r(2.5, 4)}s`;
    $("budgetMargin").textContent = `+${r(4, 9)}s`;
    $("totalDelay").textContent = `${r(14, 17, 0)}s`;
  };

  $("start").addEventListener("click", () => {
    if (live) return;
    $("mediaLoading").hidden = false;
    setTimeout(() => {
      $("mediaLoading").hidden = true;
      live = true;
      stage.classList.add("playing");
      emptyState.classList.add("hidden");
      stateBadge.dataset.tone = "live";
      stateText.textContent = I18N.t("state.live");
      $("liveChip").hidden = false;
      $("stop").disabled = false;
      $("streamTitle").textContent = "【歌枠】深夜のリクエスト歌配信！";
      $("message").textContent = I18N.t("now.msg.live");
      tickClock(); tickTelemetry(); spawnSubtitle(); spawnChat();
      timers = [
        setInterval(tickClock, 1000),
        setInterval(spawnSubtitle, 3600),
        setInterval(spawnChat, 1700),
        setInterval(spawnDanmaku, 900),
        setInterval(tickTelemetry, 4000),
      ];
    }, 1100);
  });

  $("stop").addEventListener("click", () => {
    live = false;
    timers.forEach(clearInterval);
    timers = [];
    stage.classList.remove("playing");
    emptyState.classList.remove("hidden");
    stateBadge.dataset.tone = "idle";
    stateText.textContent = I18N.t("state.idle");
    $("liveChip").hidden = true;
    $("stop").disabled = true;
    $("streamTitle").textContent = I18N.t("now.title.idle");
    $("message").textContent = I18N.t("now.msg.idle");
    $("subtitleClock").textContent = "--:--:--";
    $("currentWallTime").textContent = "--:--:--";
    $("danmakuLayer").replaceChildren();
  });

  /* ── 播放控制（原型态视觉反馈） ── */
  const playPause = $("playPause");
  let paused = false;
  const syncPlayIcon = () => {
    playPause.querySelector(".ic-play").style.display = paused ? "" : "none";
    playPause.querySelector(".ic-pause").style.display = paused ? "none" : "";
  };
  playPause.addEventListener("click", () => { paused = !paused; syncPlayIcon(); });
  syncPlayIcon();

  /* ── 字幕层拖拽 ── */
  const subLayer = $("subtitleLayer");
  let drag = null;
  subLayer.addEventListener("pointerdown", (e) => {
    drag = { x: e.clientX, y: e.clientY, l: subLayer.offsetLeft, t: subLayer.offsetTop };
    subLayer.setPointerCapture(e.pointerId);
  });
  subLayer.addEventListener("pointermove", (e) => {
    if (!drag) return;
    subLayer.style.left = `${drag.l + e.clientX - drag.x}px`;
    subLayer.style.top = `${drag.t + e.clientY - drag.y}px`;
    subLayer.style.bottom = "auto";
    subLayer.style.transform = "none";
  });
  subLayer.addEventListener("pointerup", () => { drag = null; });
  $("resetSubtitlePosition").addEventListener("click", () => {
    subLayer.style.left = "50%";
    subLayer.style.top = "auto";
    subLayer.style.bottom = "12%";
    subLayer.style.transform = "translateX(-50%)";
  });

  /* ── 设置联动 ── */
  $("subtitleMode").addEventListener("change", (e) => { subLayer.dataset.mode = e.target.value; });
  $("subtitleSize").addEventListener("change", (e) => { subLayer.dataset.size = e.target.value; });
  $("subtitleOpacity").addEventListener("input", (e) => {
    subLayer.style.setProperty("--sub-opacity", e.target.value);
  });
  $("subtitleSourceColor").addEventListener("input", (e) => {
    subLayer.style.setProperty("--sub-src-color", e.target.value);
  });
  $("subtitleTranslationColor").addEventListener("input", (e) => {
    subLayer.style.setProperty("--sub-tgt-color", e.target.value);
  });
  $("subtitleOffset").addEventListener("input", (e) => {
    $("subtitleOffsetValue").textContent = `${Number(e.target.value).toFixed(1)}s`;
  });
  $("chatOverlayOpacity").addEventListener("input", (e) => {
    $("danmakuLayer").style.setProperty("--dm-opacity", e.target.value);
  });
  $("chatOverlaySize").addEventListener("input", (e) => {
    $("danmakuLayer").style.setProperty("--dm-scale", e.target.value);
  });

  /* ── 播放条弹窗体系 ── */
  const fire = (el, type) => el.dispatchEvent(new Event(type, { bubbles: true }));
  const pops = [
    ["qualityBtn", "qualityPop"],
    ["subtitleCtlBtn", "subtitlePop"],
    ["danmakuCtlBtn", "danmakuPop"],
  ];
  const closePops = () => document.querySelectorAll(".ctl-pop.open").forEach((p) => p.classList.remove("open"));
  pops.forEach(([btnId, popId]) => {
    $(btnId).addEventListener("click", (e) => {
      e.stopPropagation();
      const pop = $(popId);
      const wasOpen = pop.classList.contains("open");
      closePops();
      pop.classList.toggle("open", !wasOpen);
    });
    $(popId).addEventListener("click", (e) => e.stopPropagation());
  });
  document.addEventListener("click", closePops);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePops(); });

  /* 音量：悬停弹窗（参考 B 站）+ 点击静音 */
  const volumeWrap = $("volumeWrap");
  const volumePop = $("volumePop");
  volumeWrap.addEventListener("mouseenter", () => volumePop.classList.add("open"));
  volumeWrap.addEventListener("mouseleave", () => volumePop.classList.remove("open"));
  $("muteToggle").addEventListener("click", (e) => {
    e.currentTarget.classList.toggle("muted");
  });
  $("volume").addEventListener("input", (e) => {
    $("volumeValue").textContent = `${Math.round(e.target.value * 100)}%`;
  });

  /* ── 弹窗 ↔ 03 板块设置：双向同步 ── */
  const syncRange = (aId, bId, labelId, fmtLabel) => {
    const a = $(aId), b = $(bId), label = labelId ? $(labelId) : null;
    const update = (from, to) => {
      to.value = from.value;
      if (label) label.textContent = fmtLabel(from.value);
    };
    a.addEventListener("input", () => { update(a, b); fire(b, "input"); });
    b.addEventListener("input", () => { update(b, a); fire(a, "input"); });
  };
  const pct = (v) => `${Math.round(v * 100)}%`;
  syncRange("subtitleOpacity", "popSubOpacity", "popSubOpacityValue", pct);
  syncRange("chatOverlayOpacity", "popDmOpacity", "popDmOpacityValue", pct);
  syncRange("chatOverlaySize", "popDmSize", "popDmSizeValue", (v) => `${Number(v).toFixed(2)}×`);

  const syncSeg = (segId, selectId) => {
    const seg = $(segId), select = $(selectId);
    seg.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => {
        seg.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
        select.value = btn.dataset.value;
        fire(select, "change");
      });
    });
    select.addEventListener("change", () => {
      seg.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.value === select.value));
    });
  };
  syncSeg("popSubMode", "subtitleMode");
  syncSeg("popSubSize", "subtitleSize");

  const syncCheck = (aId, bId) => {
    const a = $(aId), b = $(bId);
    a.addEventListener("change", () => { b.checked = a.checked; fire(b, "change"); });
    b.addEventListener("change", () => { a.checked = b.checked; fire(a, "change"); });
  };
  syncCheck("chatOverlayToggle", "popDmToggle");

  /* 清晰度弹窗 ↔ 开播设置 select + 按钮标签 */
  const QUALITY_LABELS = { auto: "自动", "1080p60": "1080P", "720p60": "720P60", "480p": "480P" };
  const qualitySelect = $("quality");
  document.querySelectorAll("#qualityPop .pop-item").forEach((item) => {
    item.addEventListener("click", () => {
      document.querySelectorAll("#qualityPop .pop-item").forEach((i) => i.classList.toggle("active", i === item));
      const q = item.dataset.quality;
      $("qualityBtn").textContent = QUALITY_LABELS[q] || q;
      [...qualitySelect.options].forEach((opt, i) => {
        if (opt.value === q || opt.textContent.startsWith(QUALITY_LABELS[q])) qualitySelect.selectedIndex = i;
      });
      fire(qualitySelect, "change");
    });
  });
})();
