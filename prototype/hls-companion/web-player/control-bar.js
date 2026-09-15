/* LingerLens 播放条交互层
 * 只负责播放条上的弹窗与「弹窗 ↔ 设置区」的双向同步。
 * 不接管播放/音量/字幕业务逻辑：所有改动都通过派发原生
 * input/change 事件交回 player.js 现有监听器处理。 */
(() => {
  "use strict";
  const el = (id) => document.getElementById(id);
  const fire = (node, type) => node.dispatchEvent(new Event(type, { bubbles: true }));

  /* ── 弹窗开关 ── */
  const POPS = [
    ["qualityBtn", "qualityPop"],
    ["subtitleCtlBtn", "subtitlePop"],
    ["danmakuCtlBtn", "danmakuPop"],
  ];
  const closeAllPops = () => document.querySelectorAll(".ctl-pop.open").forEach((p) => p.classList.remove("open"));

  for (const [btnId, popId] of POPS) {
    const btn = el(btnId);
    const pop = el(popId);
    if (!btn || !pop) continue;
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      const wasOpen = pop.classList.contains("open");
      closeAllPops();
      pop.classList.toggle("open", !wasOpen);
    });
    pop.addEventListener("click", (event) => event.stopPropagation());
  }
  document.addEventListener("click", closeAllPops);
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeAllPops(); });

  /* ── 音量：悬停展开（B 站式），静音键仍由 player.js 控制 ── */
  const volumeWrap = el("volumeWrap");
  const volumePop = el("volumePop");
  if (volumeWrap && volumePop) {
    volumeWrap.addEventListener("mouseenter", () => volumePop.classList.add("open"));
    volumeWrap.addEventListener("mouseleave", () => volumePop.classList.remove("open"));
  }
  const volume = el("volume");
  const volumeValue = el("volumeValue");
  const syncVolumeLabel = () => {
    if (volume && volumeValue) volumeValue.textContent = `${Math.round(Number(volume.value) * 100)}%`;
  };
  volume?.addEventListener("input", syncVolumeLabel);
  syncVolumeLabel();

  /* ── 通用双向同步 ── */
  const syncRange = (sourceId, mirrorId, labelId, format) => {
    const source = el(sourceId);
    const mirror = el(mirrorId);
    if (!source || !mirror) return;
    const label = labelId ? el(labelId) : null;
    const paint = () => { if (label) label.textContent = format(mirror.value); };
    mirror.value = source.value;
    paint();
    source.addEventListener("input", () => { mirror.value = source.value; paint(); });
    mirror.addEventListener("input", () => {
      source.value = mirror.value;
      paint();
      fire(source, "input");
    });
  };
  const asPercent = (value) => `${Math.round(Number(value) * 100)}%`;
  syncRange("subtitleOpacity", "popSubOpacity", "popSubOpacityValue", asPercent);
  syncRange("chatOverlayOpacity", "popDmOpacity", "popDmOpacityValue", asPercent);
  syncRange("chatOverlaySize", "popDmSize", "popDmSizeValue", (v) => `${Number(v).toFixed(2)}×`);

  const syncSegmented = (segId, selectId) => {
    const seg = el(segId);
    const select = el(selectId);
    if (!seg || !select) return;
    const paint = () => {
      seg.querySelectorAll("button").forEach((button) => {
        button.classList.toggle("active", button.dataset.value === select.value);
      });
    };
    seg.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => {
        select.value = button.dataset.value;
        paint();
        fire(select, "change");
      });
    });
    select.addEventListener("change", paint);
    paint();
  };
  syncSegmented("popSubMode", "subtitleMode");
  syncSegmented("popSubSize", "subtitleSize");

  const syncCheckbox = (aId, bId) => {
    const a = el(aId);
    const b = el(bId);
    if (!a || !b) return;
    b.checked = a.checked;
    a.addEventListener("change", () => { b.checked = a.checked; });
    b.addEventListener("change", () => { a.checked = b.checked; fire(a, "change"); });
  };
  syncCheckbox("chatOverlayToggle", "popDmToggle");

  /* ── 清晰度：弹窗从真实 select 动态生成，保证与探测结果一致 ── */
  const qualitySelect = el("quality");
  const qualityPop = el("qualityPop");
  const qualityBtn = el("qualityBtn");
  const shortQuality = (text) => {
    const value = (text || "").trim();
    if (!value) return "清晰度";
    if (value.includes("自动")) return "自动";
    const match = value.match(/\d{3,4}\s*p?\d*/i);
    return match ? match[0].replace(/\s+/g, "").toUpperCase() : value.slice(0, 8);
  };
  const paintQualityButton = () => {
    if (!qualitySelect || !qualityBtn) return;
    const option = qualitySelect.selectedOptions?.[0];
    qualityBtn.textContent = shortQuality(option?.textContent);
  };
  const renderQualityPop = () => {
    if (!qualitySelect || !qualityPop) return;
    const label = qualityPop.querySelector(".pop-label");
    qualityPop.replaceChildren();
    if (label) qualityPop.append(label);
    const options = [...qualitySelect.options];
    if (!options.length || qualitySelect.disabled) {
      const empty = document.createElement("span");
      empty.className = "pop-label";
      empty.textContent = "先点「准备」获取清晰度";
      qualityPop.append(empty);
      return;
    }
    options.forEach((option, index) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "pop-item";
      item.textContent = option.textContent;
      if (index === qualitySelect.selectedIndex) item.classList.add("active");
      item.addEventListener("click", () => {
        qualitySelect.selectedIndex = index;
        paintQualityButton();
        fire(qualitySelect, "change");
        renderQualityPop();
        closeAllPops();
      });
      qualityPop.append(item);
    });
  };
  qualitySelect?.addEventListener("change", () => { paintQualityButton(); renderQualityPop(); });
  new MutationObserver(() => { paintQualityButton(); renderQualityPop(); })
    .observe(qualitySelect || document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled"] });
  paintQualityButton();
  renderQualityPop();
})();
