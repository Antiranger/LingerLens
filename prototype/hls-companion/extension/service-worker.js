const NATIVE_HOST = "com.laglingo.hls_companion";
const PLATFORMS = [
  {
    matches: (url) => /(^|\.)youtube\.com$/.test(url.hostname) || url.hostname === "youtu.be",
    cookieUrls: ["https://www.youtube.com/", "https://accounts.google.com/"],
  },
  {
    matches: (url) => url.hostname === "live.bilibili.com" && /^\/\d+/.test(url.pathname),
    cookieUrls: ["https://www.bilibili.com/", "https://live.bilibili.com/"],
  },
  {
    matches: (url) => /^(www\.)?twitch\.tv$/.test(url.hostname) && /^\/[A-Za-z0-9_]+\/?$/.test(url.pathname),
    cookieUrls: ["https://www.twitch.tv/"],
  },
];

chrome.action.onClicked.addListener(async (tab) => {
  try {
    if (!tab.id || !tab.url) throw new Error("No active page URL");
    const pageUrl = new URL(tab.url);
    const platform = PLATFORMS.find((candidate) => candidate.matches(pageUrl));
    if (!platform) throw new Error("Open a YouTube, Bilibili, or Twitch live page first");

    const store = await cookieStoreForTab(tab.id);
    const cookies = await collectCookies(platform.cookieUrls, store?.id);
    const response = await chrome.runtime.sendNativeMessage(NATIVE_HOST, {
      action: "authSnapshot",
      pageUrl: tab.url,
      cookies,
    });
    if (!response?.ok) throw new Error(response?.error || "Native host did not accept the cookie snapshot");

    const player = new URL(response.playerUrl);
    player.searchParams.set("url", tab.url);
    player.searchParams.set("authToken", response.authToken);
    await chrome.tabs.create({ url: player.toString() });
    await setBadge("OK", "#2f9e66", "Cookie snapshot sent locally; open player created");
  } catch (error) {
    console.error("[LagLingo]", error);
    await setBadge("!", "#b83a32", error.message || String(error));
  }
});

async function cookieStoreForTab(tabId) {
  const stores = await chrome.cookies.getAllCookieStores();
  return stores.find((store) => store.tabIds.includes(tabId));
}

async function collectCookies(urls, storeId) {
  const seen = new Map();
  for (const url of urls) {
    const query = storeId ? { url, storeId } : { url };
    const cookies = await chrome.cookies.getAll(query);
    for (const cookie of cookies) {
      const partition = cookie.partitionKey?.topLevelSite || "";
      seen.set(`${cookie.domain}\n${cookie.path}\n${cookie.name}\n${partition}`, sanitize(cookie));
    }
  }
  return [...seen.values()];
}

function sanitize(cookie) {
  return {
    name: cookie.name,
    value: cookie.value,
    domain: cookie.domain,
    path: cookie.path,
    secure: cookie.secure,
    httpOnly: cookie.httpOnly,
    sameSite: cookie.sameSite,
    session: cookie.session,
    expirationDate: cookie.expirationDate,
    storeId: cookie.storeId,
    partitionKey: cookie.partitionKey,
  };
}

async function setBadge(text, color, title) {
  await chrome.action.setBadgeText({ text });
  await chrome.action.setBadgeBackgroundColor({ color });
  await chrome.action.setTitle({ title });
  setTimeout(() => chrome.action.setBadgeText({ text: "" }), 5000);
}
