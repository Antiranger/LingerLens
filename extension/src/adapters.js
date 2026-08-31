(() => {
  const MIN_VIDEO_AREA = 160 * 90;

  function isUsableVideo(video) {
    const rect = video.getBoundingClientRect();
    return rect.width * rect.height >= MIN_VIDEO_AREA && rect.width > 0 && rect.height > 0;
  }

  function visibleArea(video) {
    const rect = video.getBoundingClientRect();
    const viewportWidth = document.documentElement.clientWidth;
    const viewportHeight = document.documentElement.clientHeight;
    const width = Math.max(0, Math.min(rect.right, viewportWidth) - Math.max(rect.left, 0));
    const height = Math.max(0, Math.min(rect.bottom, viewportHeight) - Math.max(rect.top, 0));
    return width * height;
  }

  function mediaReadinessScore(video) {
    let score = visibleArea(video);
    if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) score += 1_000_000;
    if (video.seekable.length > 0) score += 2_000_000;
    if (!video.paused) score += 500_000;
    if (!video.muted) score += 50_000;
    return score;
  }

  function chooseBest(candidates) {
    return candidates
      .filter(isUsableVideo)
      .sort((a, b) => mediaReadinessScore(b) - mediaReadinessScore(a))[0] ?? null;
  }

  const adapters = [
    {
      id: "youtube",
      label: "YouTube Live",
      matches: (location) => /(^|\.)youtube\.com$/i.test(location.hostname),
      findVideo() {
        const preferred = document.querySelector("#movie_player video.html5-main-video, ytd-player video.html5-main-video");
        return preferred && isUsableVideo(preferred)
          ? preferred
          : chooseBest([...document.querySelectorAll("video")]);
      },
      pageId() {
        return new URL(location.href).searchParams.get("v") || location.pathname;
      }
    },
    {
      id: "bilibili",
      label: "Bilibili Live",
      matches: (location) => location.hostname.toLowerCase() === "live.bilibili.com",
      findVideo() {
        const selectors = [
          "#live-player video",
          ".live-player-mounter video",
          ".web-player-module-area-mask video",
          "video"
        ];
        const candidates = selectors.flatMap((selector) => [...document.querySelectorAll(selector)]);
        return chooseBest([...new Set(candidates)]);
      },
      pageId() {
        return location.pathname.split("/").filter(Boolean)[0] || location.pathname;
      }
    }
  ];

  const unknownAdapter = {
    id: "unknown",
    label: "Unsupported platform",
    matches: () => true,
    findVideo: () => chooseBest([...document.querySelectorAll("video")]),
    pageId: () => location.pathname
  };

  window.LiveDelaySpike = window.LiveDelaySpike || {};
  window.LiveDelaySpike.getAdapter = () => adapters.find((adapter) => adapter.matches(location)) || unknownAdapter;
})();
