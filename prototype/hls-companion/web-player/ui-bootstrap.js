/* Critical shell chrome that must work even if the main player script fails.
 *
 * Two jobs:
 *  1. Dialog open/close controls stay independent from player.js, so a stale or
 *     partially-loaded playback bundle cannot make model settings or Cookie
 *     import unreachable. The full handlers in player.js may also run; every
 *     operation is idempotent and checks the dialog state first.
 *  2. The shell chrome from the design prototype: the top-bar clock and the
 *     scroll-reveal for the page sections. Both are independent of playback, and
 *     the reveal in particular must run as early as possible: the CSS starts
 *     `.reveal` at `opacity: 0`, so a script that never runs would leave every
 *     section invisible.
 */
(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const openDialog = (id) => {
    const dialog = byId(id);
    if (dialog && !dialog.open && typeof dialog.showModal === "function") dialog.showModal();
  };
  const closeDialog = (id) => {
    const dialog = byId(id);
    if (dialog?.open && typeof dialog.close === "function") dialog.close();
  };

  byId("openCookieImport")?.addEventListener("click", () => openDialog("cookieImportDialog"));
  byId("openModelSettings")?.addEventListener("click", () => openDialog("modelSettingsDialog"));
  byId("closeCookieImport")?.addEventListener("click", () => closeDialog("cookieImportDialog"));
  byId("cancelCookieImport")?.addEventListener("click", () => closeDialog("cookieImportDialog"));
  byId("closeModelSettings")?.addEventListener("click", () => closeDialog("modelSettingsDialog"));
  byId("cancelModelSettings")?.addEventListener("click", () => closeDialog("modelSettingsDialog"));

  /* ── 顶栏本地时钟 ── */
  const clock = byId("topClock");
  if (clock) {
    const pad = (value) => String(value).padStart(2, "0");
    const paint = () => {
      const now = new Date();
      clock.textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
    };
    paint();
    setInterval(paint, 1000);
  }

  /* ── 滚动揭示：进入视口时依次浮现 ── */
  const sections = document.querySelectorAll(".reveal");
  const revealAll = () => sections.forEach((node) => node.classList.add("revealed"));
  if (typeof IntersectionObserver !== "function") {
    revealAll();
  } else {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry, index) => {
        if (!entry.isIntersecting) return;
        entry.target.style.transitionDelay = `${index * 70}ms`;
        entry.target.classList.add("revealed");
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.08 });
    sections.forEach((node) => observer.observe(node));
    // 兜底：观察器在首帧之前就该命中首屏板块；若浏览器迟迟不回调（节流、
    // 离屏渲染、hidden 窗口），2 秒后强制显示，绝不让内容永久隐身。
    setTimeout(() => {
      for (const node of sections) {
        if (!node.classList.contains("revealed") && node.getBoundingClientRect().top < innerHeight) {
          node.classList.add("revealed");
        }
      }
    }, 2000);
  }
})();
