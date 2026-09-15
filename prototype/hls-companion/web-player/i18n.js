/* LingerLens · UI Locale 层
 *
 * 设计：静态 UI 通过 BINDINGS（DOM ↔ 语义 key）本地化；zh-CN 默认文案在
 * 初始化时从 DOM 捕获，其余语言查 DICT。
 *
 * 动态文案（player.js 写入的状态/反馈）目前通过 DYNAMIC 短语映射在写入后
 * 翻译，属于过渡桥接；彻底方案是让 player.js 直接调用 I18N.t(key)。
 * UI Locale 独立于 Source Language Policy 与 Target Language（见 CONTEXT.md）。
 */
const I18N = (() => {
  "use strict";

  /* kind: text | placeholder | html | fieldLabel | checkText | segText | summaryText | cardLabel | cardDesc | optionText */
  const BINDINGS = [
    /* 顶栏 */
    ["#openCookieImport", "action.cookie"],
    ["#openModelSettings", "action.connections"],
    /* 诊断栏 */
    ["#diagnosticsBar .diag-title", "diag.title"],
    ["#diagSummary", "diag.ok"],
    ["#diagCopy", "diag.copy"],
    ["#diagClear", "diag.clear"],
    ["#diagEmpty", "diag.empty"],
    ["#diagnosticsBar", "diag.aria", "ariaLabel"],
    ["#diagToggle", "diag.toggle", "titleAttr"],
    /* 宣言带 */
    [".hero-title", "hero.title", "html"],
    [".hero-sub", "hero.sub"],
    [".hero-right .chip-lime", "chip.trans"],
    [".hero-right .chip-yellow", "chip.dm"],
    [".hero-right .chip-ink", "chip.replay"],
    /* 01 观看直播 */
    ['section[aria-label="实时工作台"] .deck-title', "deck1.title"],
    ["#tabWorkbenchSplit", "tab.split"],
    ["#tabWorkbenchSubtitles", "tab.subs"],
    ["#tabWorkbenchChat", "tab.chat"],
    ["#clearSubtitlesTimelineBtn", "clear.subs"],
    ["#clearChatTimelineBtn", "clear.chat"],
    ["#paneSubtitles .pane-heading", "pane.subs"],
    ["#paneChat .pane-heading", "pane.chat"],
    /* 侧栏宽度手柄：可见文案是 title 与 aria-label，两者都要跟着语言走 */
    ["#paneSubtitles .pane-resizer", "resize.subs", "ariaLabel"],
    ["#paneChat .pane-resizer", "resize.chat", "ariaLabel"],
    ["#paneSubtitles .pane-resizer", "resize.hint", "titleAttr"],
    ["#paneChat .pane-resizer", "resize.hint", "titleAttr"],
    ["#subtitlesTimelineList .timeline-empty", "empty.subs"],
    ["#chatTimelineList .timeline-empty", "empty.chat"],
    /* 开播卡 */
    [".player-setup h3", "setup.title"],
    [".player-setup > p", "setup.sub"],
    ["#url", "field.url", "fieldLabel"],
    ["#url", "ph.url", "placeholder"],
    ["#probe", "action.probe"],
    ["#proxy", "field.proxy", "fieldLabel"],
    ["#proxy", "ph.proxy", "placeholder"],
    ["#setupPlayback .field-label", "field.quality"],
    ["#start .button-label", "action.start"],
    ["#setupFeedback", "setup.feedback"],
    ["#mediaLoadingText", "loading.start"],
    /* NOW 面板 */
    [".now-label", "now.label", "html"],
    ["#streamTitle", "now.title.idle"],
    ["#message", "now.msg.idle"],
    ["#applyDelayButton", "action.raiseDelay"],
    [".delay-target-compact .field-label", "delay.target"],
    [".delay-input i", "delay.unit"],
    [".delay-estimate .field-label", "delay.est"],
    ["#stop .button-label", "action.stop"],
    /* 02 模型分工 */
    ['section[aria-label="当前模型"] .deck-title', "deck2.title"],
    ["#manageModelConnections", "models.manage"],
    ["#roleAsr", "role.asr", "fieldLabel"],
    ["#roleSubtitle", "role.subtitle", "fieldLabel"],
    ["#roleFallback", "role.fallback", "fieldLabel"],
    ["#roleChat", "role.chat", "fieldLabel"],
    /* 03 字幕与弹幕设置 */
    [".deck-settings .deck-title", "deck3.title"],
    [".deck-settings .sgroup:nth-of-type(1) .sgroup-title", "group.a", "summaryText"],
    [".deck-settings .sgroup:nth-of-type(2) .sgroup-title", "group.b", "summaryText"],
    [".deck-settings .sgroup:nth-of-type(3) .sgroup-title", "group.c", "summaryText"],
    ["#subtitlesEnabled ~ .check-text", "check.cloud"],
    ["#subtitleMode", "label.mode", "fieldLabel"],
    ['#subtitleMode option[value="bilingual"]', "opt.bilingual", "optionText"],
    ['#subtitleMode option[value="zh"]', "opt.zh", "optionText"],
    ['#subtitleMode option[value="src"]', "opt.src", "optionText"],
    ["#subtitleSize", "label.size", "fieldLabel"],
    ['#subtitleSize option[value="small"]', "opt.small", "optionText"],
    ['#subtitleSize option[value="medium"]', "opt.medium", "optionText"],
    ['#subtitleSize option[value="large"]', "opt.large", "optionText"],
    ["#subtitleOpacity", "label.opacity", "fieldLabel"],
    [".subtitle-direct-tip span", "tip.position"],
    [".subtitle-direct-tip small", "tip.position.desc"],
    ["#subtitleSourceColor", "label.srccolor", "fieldLabel"],
    ["#subtitleTranslationColor", "label.tgtcolor", "fieldLabel"],
    ["#resetSubtitlePosition", "reset.pos"],
    ["#subtitleOffset", "label.offset", "fieldLabel"],
    ["#chatOverlayToggle ~ .check-text", "check.danmaku"],
    ["#chatOverlayOpacity", "label.dmopacity", "fieldLabel"],
    ["#chatOverlaySize", "label.dmsize", "fieldLabel"],
    ["#hidePureEmojiToggle ~ .check-text", "check.emoji"],
    ["#chatTranslateToggle ~ .check-text", "check.chattrans"],
    ["#sourceLanguageMode", "label.srclang", "fieldLabel"],
    ['#sourceLanguageMode option[value="specified"]', "opt.specified", "optionText"],
    ['#sourceLanguageMode option[value="detect"]', "opt.detect", "optionText"],
    ["#sourceSpecifiedField > .field-label", "label.specifiedsrc"],
    ["#sourceDetectField > .field-label", "label.candidates"],
    ["#allowCodeSwitching ~ .check-text", "check.codeswitch"],
    [".language-controls .language-field:last-child > .field-label", "label.tgtlang"],
    /* 04 运行数据 */
    ['section[aria-label="运行数据"] .deck-title', "deck4.title"],
    ["#hiddenDelay", "t.hidden", "cardLabel"], ["#hiddenDelay", "t.hidden.d", "cardDesc"],
    ["#playerDelay", "t.behind", "cardLabel"], ["#playerDelay", "t.behind.d", "cardDesc"],
    ["#buffer", "t.buffer", "cardLabel"], ["#buffer", "t.buffer.d", "cardDesc"],
    ["#resolution", "t.output", "cardLabel"], ["#resolution", "t.output.d", "cardDesc"],
    ["#uptime", "t.uptime", "cardLabel"], ["#uptime", "t.uptime.d", "cardDesc"],
    ["#subtitleProviderStatus", "t.provider", "cardLabel"], ["#subtitleProviderStatus", "t.provider.d", "cardDesc"],
    ["#asrUsageCost", "t.asr", "cardLabel"], ["#asrUsageCost", "t.asr.d", "cardDesc"],
    ["#translationUsageCost", "t.translation", "cardLabel"], ["#translationUsageCost", "t.translation.d", "cardDesc"],
    ["#totalUsageCost", "t.total", "cardLabel"], ["#totalUsageCost", "t.total.d", "cardDesc"],
    ["#translationLatency", "t.latency", "cardLabel"], ["#translationLatency", "t.latency.d", "cardDesc"],
    ["#subtitleReadyLag", "t.ready", "cardLabel"], ["#subtitleReadyLag", "t.ready.d", "cardDesc"],
    ["#budgetMargin", "t.budget", "cardLabel"], ["#budgetMargin", "t.budget.d", "cardDesc"],
    ["#cueDuration", "t.cue", "cardLabel"], ["#cueDuration", "t.cue.d", "cardDesc"],
    ["#timingSources", "t.timing", "cardLabel"], ["#timingSources", "t.timing.d", "cardDesc"],
    ["#schedulerDrops", "t.drops", "cardLabel"], ["#schedulerDrops", "t.drops.d", "cardDesc"],
    /* 弹窗 */
    ["#modelSettingsTitle", "dlg.model.title"],
    ["#editAsrConnections", "dlg.model.tab.asr"],
    ["#editTranslationConnections", "dlg.model.tab.trans"],
    [".asr-section .settings-section-heading h3", "dlg.model.sec.asr"],
    [".translation-section .settings-section-heading h3", "dlg.model.sec.trans"],
    ["#addAsrProfile", "dlg.model.addasr"],
    ["#addTranslationProfile", "dlg.model.addtrans"],
    ["#cancelModelSettings", "action.cancel"],
    ["#saveModelSettings", "action.save"],
    ["#cookieImportTitle", "dlg.cookie.title"],
    ["#cookieImportIntro", "dlg.cookie.intro"],
    ["#cookiePlatform", "field.platform", "fieldLabel"],
    ["#cookieFormat", "field.format", "fieldLabel"],
    ["#cookiePayload", "field.payload", "fieldLabel"],
    ["#cookieImportDialog .cookie-hint", "dlg.cookie.hint"],
    ["#cancelCookieImport", "action.cancel"],
    ["#submitCookieImport", "action.import"],
  ];

  const DICT = {
    en: {
      "state.idle": "Not started", "state.notRunning": "Not running",
      "follow.current": "Following", "follow.back": "Back to live",
      "empty.subs.ready": "Waiting for ready subtitles…", "empty.chat.none": "No live messages on screen yet",
      "chat.status.idle": "Idle", "chat.status.connecting": "Connecting chat",
      "chat.status.authenticating": "Authenticating chat", "chat.status.reconnecting": "Reconnecting chat",
      "chat.status.ready": "Chat ready", "chat.status.receiving": "Receiving chat",
      "chat.status.unavailable": "Chat unavailable", "chat.status.failed": "Chat receive failed",
      "chat.received": "Received: {n}", "chat.translated": "Translated: {n}",
      "action.cookie": "Import Cookie", "action.connections": "Connections & Keys",
      "action.probe": "Probe", "action.start": "Start", "action.stop": "Stop",
      "action.raiseDelay": "Subtitles lagging: raise delay", "action.cancel": "Cancel",
      "action.save": "Save Model Settings", "action.import": "Import & Authorize",
      "hero.title": 'Local Delay <span class="hero-x">×</span> Bilingual Live Subtitles',
      "hero.sub": "YouTube · Bilibili · Twitch streams — saved locally first, then played with recognition and translation.",
      "chip.trans": "TRANS", "chip.dm": "DANMAKU", "chip.replay": "REPLAY",
      "deck1.title": "Watch Live",
      "tab.split": "Split View", "tab.subs": "Subtitles", "tab.chat": "Live Chat",
      "clear.subs": "Clear", "clear.chat": "Clear",
      "pane.subs": "Subtitle Timeline", "pane.chat": "Live Chat Timeline",
      "resize.subs": "Resize the subtitle timeline", "resize.chat": "Resize the live chat timeline",
      "resize.hint": "Drag to resize · double-click to reset",
      "empty.subs": "Waiting for subtitles…", "empty.chat": "Waiting for chat messages…",
      "setup.title": "Open a Live Stream",
      "setup.sub": "Paste a stream link, probe, then pick a quality and start.",
      "field.url": "Stream URL", "ph.url": "Paste a YouTube, Bilibili or Twitch live link",
      "field.proxy": "Network proxy (optional)", "ph.proxy": "Uses the system proxy; e.g. http://127.0.0.1:7890",
      "field.quality": "Quality",
      "setup.feedback": "Supports YouTube, Bilibili and Twitch live streams.",
      "loading.start": "Starting stream…",
      "now.label": '<i class="now-dot"></i>NOW · STATUS',
      "now.title.idle": "Waiting for stream URL",
      "now.msg.idle": "Enter a stream link in the player to begin.",
      "delay.target": "Local target", "delay.unit": "s", "delay.est": "Local estimate",
      "deck2.title": "Model Roles", "models.manage": "Model Settings",
      "role.asr": "Speech Recognition", "role.subtitle": "Subtitle Translation",
      "role.fallback": "Subtitle Fallback", "role.chat": "Chat Translation",
      "deck3.title": "Subtitles & Chat",
      "group.a": "Subtitle Display",
      "group.b": "On-screen Danmaku",
      "group.c": "Translation Languages",
      "check.cloud": "Enable cloud subtitles",
      "label.mode": "Display", "opt.bilingual": "Original+Translation", "opt.zh": "Translation only", "opt.src": "Original only",
      "label.size": "Size", "opt.small": "Small", "opt.medium": "Medium", "opt.large": "Large",
      "label.opacity": "Opacity",
      "tip.position": "Position & scale", "tip.position.desc": "Drag the subtitles to move them; drag the corner handle to scale",
      "label.srccolor": "Source color", "label.tgtcolor": "Translation color",
      "reset.pos": "Reset subtitle position", "label.offset": "Subtitle offset",
      "check.danmaku": "On-screen danmaku", "label.dmopacity": "Danmaku opacity", "label.dmsize": "Danmaku size",
      "check.emoji": "Hide pure-emoji", "check.chattrans": "Translate chat",
      "label.srclang": "Source language", "opt.specified": "Specified", "opt.detect": "Auto-detect",
      "label.specifiedsrc": "Specified source language",
      "label.candidates": "Candidate languages (empty = any; first = preferred)",
      "check.codeswitch": "Allow code-switching", "label.tgtlang": "Target language",
      "deck4.title": "Runtime Data & Cost",
      "t.hidden": "Locally staged media", "t.hidden.d": "Downloaded but withheld to keep the local target",
      "t.behind": "Player local lag", "t.behind.d": "How far the player intentionally trails the newest local position",
      "t.buffer": "Player buffer", "t.buffer.d": "Media already downloaded and playable ahead of the playhead",
      "t.output": "Current output", "t.output.d": "Resolution and frame rate being played",
      "t.uptime": "Uptime", "t.uptime.d": "How long the current download pipeline has been running",
      "t.provider": "Subtitle providers", "t.provider.d": "Which provider handles recognition and translation",
      "t.asr": "ASR usage / cost", "t.asr.d": "Recognized audio duration and estimated cost",
      "t.translation": "Translation usage / cost", "t.translation.d": "Input, cache hits, output and estimated cost",
      "t.total": "Estimated total", "t.total.d": "Shown only when both ASR and translation can be estimated",
      "t.latency": "Translation latency", "t.latency.d": "Average request-to-response time per sentence",
      "t.ready": "Subtitle ready p50/p95", "t.ready.d": "Speech end to translation ready; decides whether subtitles make it",
      "t.budget": "Subtitle budget margin", "t.budget.d": "Local delay − p95 ready − p95 cue; sustained negative means subtitles lag",
      "t.cue": "Cue duration p50/p95", "t.cue.d": "Percentiles of subtitle sentence length; long cues need more delay budget",
      "t.timing": "Timestamp source", "t.timing.d": "Share of ASR-exact timestamps versus local estimates",
      "t.drops": "Client drops", "t.drops.d": "Cues that arrived late or timed out before display",
      "dlg.model.title": "Model Settings",
      "dlg.model.tab.asr": "ASR Profiles", "dlg.model.tab.trans": "Translation Profiles",
      "dlg.model.sec.asr": "Speech recognition profiles", "dlg.model.sec.trans": "Translation profiles",
      "dlg.model.addasr": "Add ASR", "dlg.model.addtrans": "Add translation",
      "dlg.cookie.title": "Import Login Cookie",
      "dlg.cookie.intro": "Pick a platform and paste that site's cookies. They are stored locally per platform; probe/start only writes a temporary file for the target URL and deletes it immediately.",
      "field.platform": "Platform", "field.format": "Format", "field.payload": "Paste content",
      "dlg.cookie.hint": "One cookie per line: name + tab/space/equals + value; cookies.txt and header strings also work.",
      "state.preparing": "Preparing", "state.ready": "Ready", "state.joining": "Joining", "state.buffering": "Building delay buffer",
      "state.waiting": "Waiting to play", "state.delayed": "Delayed playback", "state.readyToPlay": "Ready to play",
      "state.stopped": "Stopped", "state.stopping": "Stopping", "state.error": "Error", "state.playerBuffering": "Player buffering",
      "msg.needUrl": "Paste a complete stream link first.",
      "msg.probeHint": "Click Probe to read this stream's qualities.",
      "msg.ready": "Ready. Pick a quality and press Start.",
      "msg.stopped": "Stopped. You can probe again or paste another stream link.",
      "chat.count": "{n} msgs",
      "diag.title": "Diagnostics", "diag.ok": "All clear",
      "diag.copy": "Copy diagnostics", "diag.clear": "Clear",
      "diag.empty": "Nothing to report yet.",
      "diag.aria": "Runtime diagnostics", "diag.toggle": "Show or hide runtime diagnostics",
      "diag.level.info": "info", "diag.level.warn": "warn", "diag.level.error": "error",
      "diag.hint.media": "The stream download stopped. Usually the source ended or the platform blocked it — stop and start the session again.",
      "diag.hint.network": "The local backend did not answer. Check that LingerLens is still running, then retry.",
      "diag.hint.source": "No stream is coming from this link. Usually the link has expired, the broadcast has ended, or the platform wants a login or is blocking the request — try another live link.",
      "diag.hint.decode": "The player could not decode this stream. Try a lower quality.",
      "diag.hint.translation": "Translation is failing. Check the provider key and quota under Connections & Keys.",
      "diag.hint.auth": "This stream needs a login. Import a cookie for the platform and try again.",
      "diag.ctx.locale": "UI language", "diag.ctx.state": "Session state", "diag.ctx.target": "Local target",
      "diag.devlog.on": "recording {file}", "diag.devlog.failed": "log write failed",
      "diag.log.record": "Record log to file", "diag.log.stop": "Stop recording", "diag.log.open": "Open log folder",
      "diag.log.private": "Writes the diagnostic log to a file you can send. It can contain private stream URLs — check it before sharing.",
      "diag.ctx.devlog": "Dev log",
      "diag.ctx.page": "Page", "diag.ctx.useragent": "User agent",
      "diag.report.title": "LingerLens diagnostics", "diag.report.time": "Generated", "diag.report.count": "Records",
      "diag.copied": "Copied", "diag.copyFailed": "Copy failed",
      "diag.update.check": "Check for updates", "diag.update.current": "Up to date",
      "diag.update.download": "Download {version}", "diag.update.downloading": "Downloading {percent}%",
      "diag.update.restarting": "Restarting to install…", "diag.update.retry": "Retry update check",
      "diag.update.found": "Version {version} is available (you have {current})",
      "diag.update.ready": "Update downloaded and verified; restarting to install",
      "diag.update.failed": "Update check failed: {error}",
      "diag.ctx.build": "Build",
    },
    ja: {
      "state.idle": "未開始", "state.notRunning": "未実行",
      "follow.current": "追従中", "follow.back": "現在地へ戻る",
      "empty.subs.ready": "準備済み字幕を待機…", "empty.chat.none": "この画面にはまだコメントがありません",
      "chat.status.idle": "待機", "chat.status.connecting": "チャット接続中",
      "chat.status.authenticating": "チャット認証中", "chat.status.reconnecting": "チャット再接続中",
      "chat.status.ready": "チャット準備完了", "chat.status.receiving": "チャット受信中",
      "chat.status.unavailable": "チャット利用不可", "chat.status.failed": "チャット受信失敗",
      "chat.received": "受信: {n}", "chat.translated": "翻訳: {n}",
      "action.cookie": "Cookie をインポート", "action.connections": "接続とキー",
      "action.probe": "準備", "action.start": "開始", "action.stop": "停止",
      "action.raiseDelay": "字幕が間に合わない：遅延を増やす", "action.cancel": "キャンセル",
      "action.save": "モデル設定を保存", "action.import": "インポートして認証",
      "hero.title": 'ローカル遅延<span class="hero-x">×</span>リアルタイム二言語字幕',
      "hero.sub": "YouTube · Bilibili · Twitch の配信を、まず本機に保存し、認識と翻訳を添えて再生します。",
      "chip.trans": "翻訳", "chip.dm": "弾幕", "chip.replay": "再生",
      "deck1.title": "ライブ視聴",
      "tab.split": "左右分割", "tab.subs": "字幕", "tab.chat": "チャット",
      "clear.subs": "クリア", "clear.chat": "クリア",
      "pane.subs": "リアルタイム字幕", "pane.chat": "ライブチャット",
      "resize.subs": "字幕タイムラインの幅を調整", "resize.chat": "ライブチャットの幅を調整",
      "resize.hint": "ドラッグで幅を調整 · ダブルクリックでリセット",
      "empty.subs": "字幕を待っています…", "empty.chat": "チャットを待っています…",
      "setup.title": "配信を開く",
      "setup.sub": "リンクを貼り、準備してから画質を選んで開始します。",
      "field.url": "配信リンク", "ph.url": "YouTube・Bilibili・Twitch の配信リンクを貼り付け",
      "field.proxy": "外部プロキシ（任意）", "ph.proxy": "システムプロキシを使用；例 http://127.0.0.1:7890",
      "field.quality": "画質",
      "setup.feedback": "YouTube・Bilibili・Twitch の配信に対応。",
      "loading.start": "配信を開始しています…",
      "now.label": '<i class="now-dot"></i>NOW · 現在の状態',
      "now.title.idle": "配信URLを待っています",
      "now.msg.idle": "画面に配信リンクを入力すると開始できます。",
      "delay.target": "ローカル目標", "delay.unit": "秒", "delay.est": "ローカル推定",
      "deck2.title": "モデル分担", "models.manage": "モデル設定",
      "role.asr": "音声認識", "role.subtitle": "字幕翻訳",
      "role.fallback": "字幕翻訳フォールバック", "role.chat": "コメント翻訳",
      "deck3.title": "字幕と弾幕の設定",
      "group.a": "字幕表示",
      "group.b": "画面弾幕",
      "group.c": "翻訳言語",
      "check.cloud": "クラウド字幕を有効化",
      "label.mode": "表示", "opt.bilingual": "原文+訳文", "opt.zh": "訳文のみ", "opt.src": "原文のみ",
      "label.size": "サイズ", "opt.small": "小", "opt.medium": "中", "opt.large": "大",
      "label.opacity": "不透明度",
      "tip.position": "位置と拡大縮小", "tip.position.desc": "字幕をドラッグして移動、右下のハンドルで拡大縮小",
      "label.srccolor": "原文の色", "label.tgtcolor": "訳文の色",
      "reset.pos": "字幕位置を戻す", "label.offset": "字幕オフセット",
      "check.danmaku": "画面弾幕", "label.dmopacity": "弾幕の不透明度", "label.dmsize": "弾幕サイズ",
      "check.emoji": "絵文字のみを隠す", "check.chattrans": "チャットを翻訳",
      "label.srclang": "ソース言語", "opt.specified": "言語を指定", "opt.detect": "自動認識",
      "label.specifiedsrc": "指定ソース言語",
      "label.candidates": "候補言語（空 = 制限なし；先頭 = 優先）",
      "check.codeswitch": "言語混合を許可", "label.tgtlang": "ターゲット言語",
      "deck4.title": "実行データと費用",
      "t.hidden": "ローカル保持メディア", "t.hidden.d": "取得済みだが、目標遅延を保つため未再生の長さ",
      "t.behind": "プレイヤー遅延", "t.behind.d": "最新のローカル位置から意図的に遅れている時間",
      "t.buffer": "プレイヤーバッファ", "t.buffer.d": "再生ヘッド前方で即再生できる長さ",
      "t.output": "現在の出力", "t.output.d": "再生中の解像度とフレームレート",
      "t.uptime": "稼働時間", "t.uptime.d": "現在のダウンロードパイプラインの連続稼働時間",
      "t.provider": "字幕プロバイダー", "t.provider.d": "認識と翻訳をそれぞれ誰が処理しているか",
      "t.asr": "ASR 使用量 / 費用", "t.asr.d": "認識した音声時間と推定費用",
      "t.translation": "翻訳 使用量 / 費用", "t.translation.d": "入力・キャッシュ・出力と推定費用",
      "t.total": "推定合計", "t.total.d": "ASR と翻訳の両方を推定できる場合のみ表示",
      "t.latency": "翻訳レイテンシ", "t.latency.d": "1文あたりの平均応答時間",
      "t.ready": "字幕準備 p50/p95", "t.ready.d": "発話終了から訳文準備まで；字幕が間に合うかを左右",
      "t.budget": "字幕予算余裕", "t.budget.d": "ローカル遅延 − p95 準備 − p95 句長；負が続くと字幕が追いつかない",
      "t.cue": "句長 p50/p95", "t.cue.d": "字幕1文の長さの分位数；長い文ほど遅延予算が必要",
      "t.timing": "タイムスタンプ来源", "t.timing.d": "ASR の正確な時刻とローカル推定の比率",
      "t.drops": "クライアント破棄", "t.drops.d": "遅延やタイムアウトで表示できなかった字幕数",
      "dlg.model.title": "モデル設定",
      "dlg.model.tab.asr": "音声認識設定", "dlg.model.tab.trans": "翻訳設定",
      "dlg.model.sec.asr": "音声認識プロファイル", "dlg.model.sec.trans": "翻訳プロファイル",
      "dlg.model.addasr": "ASR を追加", "dlg.model.addtrans": "翻訳を追加",
      "dlg.cookie.title": "ログイン Cookie をインポート",
      "dlg.cookie.intro": "プラットフォームを選び、そのサイトの Cookie を貼り付けます。内容は本機に保存され、探索/開始時のみ対象 URL 用の一時ファイルを作成し即削除します。",
      "field.platform": "プラットフォーム", "field.format": "形式", "field.payload": "貼り付け内容",
      "dlg.cookie.hint": "1行1 Cookie：名前 + タブ/空白/等号 + 値；cookies.txt とヘッダー形式にも対応。",
      "state.preparing": "準備中", "state.ready": "準備完了", "state.joining": "合流中", "state.buffering": "遅延バッファ構築",
      "state.waiting": "再生待ち", "state.delayed": "遅延再生中", "state.readyToPlay": "再生可能",
      "state.stopped": "停止しました", "state.stopping": "停止中", "state.error": "エラー", "state.playerBuffering": "プレイヤーがバッファ中",
      "msg.needUrl": "まず完全な配信リンクを貼ってください。",
      "msg.probeHint": "「準備」を押すと、この配信の画質を読み取ります。",
      "msg.ready": "準備できました。画質を選んで開始してください。",
      "msg.stopped": "停止しました。再度準備するか、別の配信リンクを貼れます。",
      "chat.count": "{n} 件",
      "diag.title": "診断", "diag.ok": "問題ありません",
      "diag.copy": "診断情報をコピー", "diag.clear": "クリア",
      "diag.empty": "まだ報告する情報はありません。",
      "diag.aria": "実行時の診断", "diag.toggle": "実行時の診断を表示／非表示",
      "diag.level.info": "情報", "diag.level.warn": "警告", "diag.level.error": "エラー",
      "diag.hint.media": "配信のダウンロードが止まりました。配信終了かプラットフォーム側の制限が原因のことが多いです。セッションを停止して再開してください。",
      "diag.hint.network": "ローカルバックエンドが応答していません。LingerLens が起動しているか確認して再試行してください。",
      "diag.hint.source": "このリンクから配信を取得できません。リンク切れ、配信終了、ログイン要求、アクセス制限のいずれかがほとんどです。別の配信リンクで試してください。",
      "diag.hint.decode": "プレイヤーがこの配信をデコードできませんでした。より低い画質を試してください。",
      "diag.hint.translation": "翻訳に失敗しています。「接続とキー」で翻訳プロバイダーのキーと残量を確認してください。",
      "diag.hint.auth": "この配信にはログインが必要です。プラットフォームの Cookie をインポートして再試行してください。",
      "diag.ctx.locale": "UI 言語", "diag.ctx.state": "セッション状態", "diag.ctx.target": "ローカル目標",
      "diag.devlog.on": "{file} に記録中", "diag.devlog.failed": "ログの書き込みに失敗",
      "diag.log.record": "ログをファイルに記録", "diag.log.stop": "記録を停止", "diag.log.open": "ログフォルダーを開く",
      "diag.log.private": "診断ログを送付できるファイルに書き出します。非公開の配信 URL が含まれることがあるため、共有前に確認してください。",
      "diag.ctx.devlog": "開発ログ",
      "diag.ctx.page": "ページ", "diag.ctx.useragent": "ユーザーエージェント",
      "diag.report.title": "LingerLens 診断情報", "diag.report.time": "生成日時", "diag.report.count": "記録",
      "diag.copied": "コピーしました", "diag.copyFailed": "コピーに失敗しました",
      "diag.update.check": "更新を確認", "diag.update.current": "最新です",
      "diag.update.download": "更新 {version} をダウンロード", "diag.update.downloading": "ダウンロード中 {percent}%",
      "diag.update.restarting": "再起動してインストール中…", "diag.update.retry": "更新確認を再試行",
      "diag.update.found": "新しいバージョン {version} があります（現在 {current}）",
      "diag.update.ready": "更新をダウンロードして検証しました。まもなく再起動してインストールします",
      "diag.update.failed": "更新の確認に失敗しました: {error}",
      "diag.ctx.build": "ビルド",
    },
    de: {
      "state.idle": "Nicht gestartet", "state.notRunning": "Nicht aktiv",
      "follow.current": "Folgt", "follow.back": "Zurück zum Livepunkt",
      "empty.subs.ready": "Warte auf fertige Untertitel…", "empty.chat.none": "Noch keine Live-Nachrichten im Bild",
      "chat.status.idle": "Inaktiv", "chat.status.connecting": "Chat wird verbunden",
      "chat.status.authenticating": "Chat wird authentifiziert", "chat.status.reconnecting": "Chat wird neu verbunden",
      "chat.status.ready": "Chat bereit", "chat.status.receiving": "Chat empfängt",
      "chat.status.unavailable": "Chat nicht verfügbar", "chat.status.failed": "Chat-Empfang fehlgeschlagen",
      "chat.received": "Empfangen: {n}", "chat.translated": "Übersetzt: {n}",
      "action.cookie": "Cookie importieren", "action.connections": "Verbindungen & Schlüssel",
      "action.probe": "Prüfen", "action.start": "Start", "action.stop": "Stopp",
      "action.raiseDelay": "Untertitel zu spät: Verzögerung erhöhen", "action.cancel": "Abbrechen",
      "action.save": "Modelleinstellungen speichern", "action.import": "Importieren & autorisieren",
      "hero.title": 'Lokale Verzögerung <span class="hero-x">×</span> zweisprachige Live-Untertitel',
      "hero.sub": "YouTube- · Bilibili- · Twitch-Streams — erst lokal speichern, dann mit Erkennung und Übersetzung abspielen.",
      "chip.trans": "ÜBERS.", "chip.dm": "DANMAKU", "chip.replay": "REPLAY",
      "deck1.title": "Live ansehen",
      "tab.split": "Geteilte Ansicht", "tab.subs": "Untertitel", "tab.chat": "Live-Chat",
      "clear.subs": "Leeren", "clear.chat": "Leeren",
      "pane.subs": "Untertitel-Zeitleiste", "pane.chat": "Live-Chat-Zeitleiste",
      "resize.subs": "Breite der Untertitel-Zeitleiste ändern", "resize.chat": "Breite des Live-Chats ändern",
      "resize.hint": "Ziehen zum Anpassen · Doppelklick zum Zurücksetzen",
      "empty.subs": "Warte auf Untertitel…", "empty.chat": "Warte auf Chat-Nachrichten…",
      "setup.title": "Livestream öffnen",
      "setup.sub": "Stream-Link einfügen, prüfen, Qualität wählen und starten.",
      "field.url": "Stream-Link", "ph.url": "YouTube-, Bilibili- oder Twitch-Livelink einfügen",
      "field.proxy": "Netzwerk-Proxy (optional)", "ph.proxy": "Nutzt den Systemproxy; z. B. http://127.0.0.1:7890",
      "field.quality": "Qualität",
      "setup.feedback": "Unterstützt YouTube-, Bilibili- und Twitch-Streams.",
      "loading.start": "Stream wird gestartet…",
      "now.label": '<i class="now-dot"></i>NOW · STATUS',
      "now.title.idle": "Warte auf Stream-Link",
      "now.msg.idle": "Gib einen Stream-Link im Player ein, um zu beginnen.",
      "delay.target": "Lokales Ziel", "delay.unit": "s", "delay.est": "Lokale Schätzung",
      "deck2.title": "Modellrollen", "models.manage": "Modelleinstellungen",
      "role.asr": "Spracherkennung", "role.subtitle": "Untertitelübersetzung",
      "role.fallback": "Untertitel-Fallback", "role.chat": "Chat-Übersetzung",
      "deck3.title": "Untertitel & Danmaku",
      "group.a": "Untertitelanzeige",
      "group.b": "Danmaku im Bild",
      "group.c": "Übersetzungssprachen",
      "check.cloud": "Cloud-Untertitel aktivieren",
      "label.mode": "Anzeige", "opt.bilingual": "Original+Übersetzung", "opt.zh": "Nur Übersetzung", "opt.src": "Nur Original",
      "label.size": "Größe", "opt.small": "Klein", "opt.medium": "Mittel", "opt.large": "Groß",
      "label.opacity": "Deckkraft",
      "tip.position": "Position & Skalierung", "tip.position.desc": "Untertitel zum Verschieben ziehen; am Eckgriff skalieren",
      "label.srccolor": "Originalfarbe", "label.tgtcolor": "Übersetzungsfarbe",
      "reset.pos": "Untertitelposition zurücksetzen", "label.offset": "Untertitel-Versatz",
      "check.danmaku": "Danmaku im Bild", "label.dmopacity": "Danmaku-Deckkraft", "label.dmsize": "Danmaku-Größe",
      "check.emoji": "Reine Emojis ausblenden", "check.chattrans": "Chat übersetzen",
      "label.srclang": "Quellsprache", "opt.specified": "Festgelegt", "opt.detect": "Automatisch",
      "label.specifiedsrc": "Festgelegte Quellsprache",
      "label.candidates": "Kandidatensprachen (leer = alle; erste = bevorzugt)",
      "check.codeswitch": "Sprachwechsel erlauben", "label.tgtlang": "Zielsprache",
      "deck4.title": "Laufzeitdaten & Kosten",
      "t.hidden": "Lokal zwischengespeichert", "t.hidden.d": "Geladen, aber für das lokale Ziel zurückgehalten",
      "t.behind": "Lokaler Rückstand", "t.behind.d": "Wie weit der Player bewusst hinter der neuesten Position liegt",
      "t.buffer": "Player-Puffer", "t.buffer.d": "Bereits geladene, sofort abspielbare Medien vor dem Abspielkopf",
      "t.output": "Aktuelle Ausgabe", "t.output.d": "Wiedergegebene Auflösung und Bildrate",
      "t.uptime": "Laufzeit", "t.uptime.d": "Wie lange die aktuelle Download-Pipeline läuft",
      "t.provider": "Untertitel-Anbieter", "t.provider.d": "Wer Erkennung und Übersetzung übernimmt",
      "t.asr": "ASR-Nutzung / Kosten", "t.asr.d": "Erkannte Audiodauer und geschätzte Kosten",
      "t.translation": "Übersetzungsnutzung / Kosten", "t.translation.d": "Eingabe, Cache-Treffer, Ausgabe und geschätzte Kosten",
      "t.total": "Geschätzte Summe", "t.total.d": "Nur wenn ASR und Übersetzung beide schätzbar sind",
      "t.latency": "Übersetzungslatenz", "t.latency.d": "Durchschnittliche Anfrage-bis-Antwort-Zeit pro Satz",
      "t.ready": "Untertitel bereit p50/p95", "t.ready.d": "Satzende bis Übersetzung fertig; entscheidet, ob Untertitel rechtzeitig kommen",
      "t.budget": "Budgetreserve Untertitel", "t.budget.d": "Lokale Verzögerung − p95 bereit − p95 Satzlänge; dauerhaft negativ = Untertitel hinken",
      "t.cue": "Satzlänge p50/p95", "t.cue.d": "Perzentile der Untertitellänge; lange Sätze brauchen mehr Budget",
      "t.timing": "Zeitstempelquelle", "t.timing.d": "Anteil exakter ASR-Zeitstempel gegenüber lokaler Schätzung",
      "t.drops": "Client-Verwürfe", "t.drops.d": "Untertitel, die zu spät kamen oder vor der Anzeige ausliefen",
      "dlg.model.title": "Modelleinstellungen",
      "dlg.model.tab.asr": "ASR-Konfiguration", "dlg.model.tab.trans": "Übersetzungskonfiguration",
      "dlg.model.sec.asr": "Spracherkennungsprofile", "dlg.model.sec.trans": "Übersetzungsprofile",
      "dlg.model.addasr": "ASR hinzufügen", "dlg.model.addtrans": "Übersetzung hinzufügen",
      "dlg.cookie.title": "Login-Cookie importieren",
      "dlg.cookie.intro": "Plattform wählen und die Cookies der Seite einfügen. Inhalte bleiben lokal; beim Prüfen/Starten wird nur eine temporäre Datei für die Ziel-URL erzeugt und sofort gelöscht.",
      "field.platform": "Plattform", "field.format": "Format", "field.payload": "Inhalt einfügen",
      "dlg.cookie.hint": "Ein Cookie pro Zeile: Name + Tab/Leerzeichen/Gleichheitszeichen + Wert; cookies.txt und Header-Format ebenfalls möglich.",
      "state.preparing": "Wird vorbereitet", "state.ready": "Bereit", "state.joining": "Verbindung", "state.buffering": "Verzögerungspuffer aufbauen",
      "state.waiting": "Wartet auf Wiedergabe", "state.delayed": "Verzögerte Wiedergabe", "state.readyToPlay": "Abspielbereit",
      "state.stopped": "Gestoppt", "state.stopping": "Wird gestoppt", "state.error": "Fehler", "state.playerBuffering": "Player puffert",
      "msg.needUrl": "Füge zuerst einen vollständigen Stream-Link ein.",
      "msg.probeHint": "Auf „Prüfen“ klicken, um die Qualitäten zu lesen.",
      "msg.ready": "Bereit. Qualität wählen und Start drücken.",
      "msg.stopped": "Gestoppt. Du kannst erneut prüfen oder einen anderen Link einfügen.",
      "chat.count": "{n} Nachr.",
      "diag.title": "Diagnose", "diag.ok": "Alles in Ordnung",
      "diag.copy": "Diagnose kopieren", "diag.clear": "Leeren",
      "diag.empty": "Noch nichts zu melden.",
      "diag.aria": "Laufzeitdiagnose", "diag.toggle": "Laufzeitdiagnose ein- oder ausblenden",
      "diag.level.info": "Info", "diag.level.warn": "Warnung", "diag.level.error": "Fehler",
      "diag.hint.media": "Der Stream-Download wurde beendet. Meist ist die Übertragung zu Ende oder die Plattform blockiert sie — Sitzung stoppen und neu starten.",
      "diag.hint.network": "Das lokale Backend antwortet nicht. Prüfen Sie, ob LingerLens noch läuft, und versuchen Sie es erneut.",
      "diag.hint.source": "Über diesen Link kommt kein Stream. Meist ist der Link abgelaufen, die Übertragung beendet, oder die Plattform verlangt eine Anmeldung bzw. blockiert die Anfrage — versuchen Sie einen anderen Live-Link.",
      "diag.hint.decode": "Der Player kann diesen Stream nicht dekodieren. Versuchen Sie eine niedrigere Qualität.",
      "diag.hint.translation": "Die Übersetzung schlägt fehl. Prüfen Sie Schlüssel und Kontingent des Anbieters unter „Verbindungen & Schlüssel“.",
      "diag.hint.auth": "Dieser Stream erfordert eine Anmeldung. Importieren Sie ein Cookie für die Plattform und versuchen Sie es erneut.",
      "diag.ctx.locale": "UI-Sprache", "diag.ctx.state": "Sitzungsstatus", "diag.ctx.target": "Lokales Ziel",
      "diag.devlog.on": "Aufzeichnung in {file}", "diag.devlog.failed": "Log-Schreiben fehlgeschlagen",
      "diag.log.record": "Log in Datei aufzeichnen", "diag.log.stop": "Aufzeichnung stoppen", "diag.log.open": "Log-Ordner öffnen",
      "diag.log.private": "Schreibt das Diagnoseprotokoll in eine Datei zum Versenden. Sie kann private Stream-URLs enthalten — vor dem Teilen prüfen.",
      "diag.ctx.devlog": "Entwicklungslog",
      "diag.ctx.page": "Seite", "diag.ctx.useragent": "User-Agent",
      "diag.report.title": "LingerLens-Diagnose", "diag.report.time": "Erstellt", "diag.report.count": "Einträge",
      "diag.copied": "Kopiert", "diag.copyFailed": "Kopieren fehlgeschlagen",
      "diag.update.check": "Nach Updates suchen", "diag.update.current": "Aktuell",
      "diag.update.download": "Update {version} laden", "diag.update.downloading": "Lädt {percent}%",
      "diag.update.restarting": "Neustart zum Installieren…", "diag.update.retry": "Suche erneut versuchen",
      "diag.update.found": "Version {version} ist verfügbar (Sie haben {current})",
      "diag.update.ready": "Update geladen und geprüft; Neustart zur Installation",
      "diag.update.failed": "Update-Suche fehlgeschlagen: {error}",
      "diag.ctx.build": "Build",
    },
    ru: {
      "state.idle": "Не запущено", "state.notRunning": "Не активно",
      "follow.current": "Слежение", "follow.back": "К текущему моменту",
      "empty.subs.ready": "Ожидание готовых субтитров…", "empty.chat.none": "На экране пока нет сообщений",
      "chat.status.idle": "Не слушается", "chat.status.connecting": "Подключение чата",
      "chat.status.authenticating": "Проверка чата", "chat.status.reconnecting": "Переподключение чата",
      "chat.status.ready": "Чат готов", "chat.status.receiving": "Приём чата",
      "chat.status.unavailable": "Чат недоступен", "chat.status.failed": "Ошибка приёма чата",
      "chat.received": "Получено: {n}", "chat.translated": "Переведено: {n}",
      "action.cookie": "Импорт Cookie", "action.connections": "Подключения и ключи",
      "action.probe": "Проба", "action.start": "Старт", "action.stop": "Стоп",
      "action.raiseDelay": "Субтитры отстают: увеличить задержку", "action.cancel": "Отмена",
      "action.save": "Сохранить настройки", "action.import": "Импорт и авторизация",
      "hero.title": 'Локальная задержка <span class="hero-x">×</span> двуязычные субтитры',
      "hero.sub": "Трансляции YouTube · Bilibili · Twitch — сначала сохраняются локально, затем играются с распознаванием и переводом.",
      "chip.trans": "ПЕРЕВОД", "chip.dm": "ДАНМАКУ", "chip.replay": "ПОВТОР",
      "deck1.title": "Просмотр трансляции",
      "tab.split": "Раздельный вид", "tab.subs": "Субтитры", "tab.chat": "Чат",
      "clear.subs": "Очистить", "clear.chat": "Очистить",
      "pane.subs": "Лента субтитров", "pane.chat": "Лента чата",
      "resize.subs": "Изменить ширину ленты субтитров", "resize.chat": "Изменить ширину ленты чата",
      "resize.hint": "Перетащите для изменения · двойной щелчок для сброса",
      "empty.subs": "Ожидание субтитров…", "empty.chat": "Ожидание сообщений чата…",
      "setup.title": "Открыть трансляцию",
      "setup.sub": "Вставьте ссылку, выполните пробу, выберите качество и запустите.",
      "field.url": "Ссылка на трансляцию", "ph.url": "Вставьте ссылку YouTube, Bilibili или Twitch",
      "field.proxy": "Сетевой прокси (необязательно)", "ph.proxy": "Используется системный прокси; напр. http://127.0.0.1:7890",
      "field.quality": "Качество",
      "setup.feedback": "Поддерживаются трансляции YouTube, Bilibili и Twitch.",
      "loading.start": "Запуск трансляции…",
      "now.label": '<i class="now-dot"></i>NOW · СТАТУС',
      "now.title.idle": "Ожидание ссылки на трансляцию",
      "now.msg.idle": "Введите ссылку в плеере, чтобы начать.",
      "delay.target": "Локальная цель", "delay.unit": "с", "delay.est": "Локальная оценка",
      "deck2.title": "Роли моделей", "models.manage": "Настройки моделей",
      "role.asr": "Распознавание речи", "role.subtitle": "Перевод субтитров",
      "role.fallback": "Резервный перевод", "role.chat": "Перевод чата",
      "deck3.title": "Субтитры и данмаку",
      "group.a": "Отображение субтитров",
      "group.b": "Данмаку на экране",
      "group.c": "Языки перевода",
      "check.cloud": "Облачные субтитры",
      "label.mode": "Режим", "opt.bilingual": "Оригинал+перевод", "opt.zh": "Только перевод", "opt.src": "Только оригинал",
      "label.size": "Размер", "opt.small": "Мал.", "opt.medium": "Сред.", "opt.large": "Бол.",
      "label.opacity": "Прозрачность",
      "tip.position": "Позиция и масштаб", "tip.position.desc": "Перетаскивайте субтитры для перемещения; угол — для масштаба",
      "label.srccolor": "Цвет оригинала", "label.tgtcolor": "Цвет перевода",
      "reset.pos": "Сбросить позицию субтитров", "label.offset": "Сдвиг субтитров",
      "check.danmaku": "Данмаку на экране", "label.dmopacity": "Прозрачность данмаку", "label.dmsize": "Размер данмаку",
      "check.emoji": "Скрывать чистые эмодзи", "check.chattrans": "Переводить чат",
      "label.srclang": "Исходный язык", "opt.specified": "Указанный", "opt.detect": "Автоопределение",
      "label.specifiedsrc": "Указанный исходный язык",
      "label.candidates": "Кандидаты языков (пусто = любые; первый = предпочтительный)",
      "check.codeswitch": "Смешение языков", "label.tgtlang": "Целевой язык",
      "deck4.title": "Данные и расходы",
      "t.hidden": "Локально сохранено", "t.hidden.d": "Загружено, но удерживается ради локальной цели",
      "t.behind": "Локальное отставание", "t.behind.d": "Насколько плеер намеренно отстаёт от новейшей позиции",
      "t.buffer": "Буфер плеера", "t.buffer.d": "Уже загруженные и готовые к игре данные впереди",
      "t.output": "Текущий вывод", "t.output.d": "Разрешение и частота кадров воспроизведения",
      "t.uptime": "Время работы", "t.uptime.d": "Сколько непрерывно работает текущий конвейер загрузки",
      "t.provider": "Провайдеры субтитров", "t.provider.d": "Кто выполняет распознавание и перевод",
      "t.asr": "ASR: объём / расход", "t.asr.d": "Длительность распознанного аудио и оценка расхода",
      "t.translation": "Перевод: объём / расход", "t.translation.d": "Ввод, попадания в кэш, вывод и оценка расхода",
      "t.total": "Итоговая оценка", "t.total.d": "Показывается, только если оценимы и ASR, и перевод",
      "t.latency": "Задержка перевода", "t.latency.d": "Среднее время от запроса до ответа на фразу",
      "t.ready": "Готовность субтитров p50/p95", "t.ready.d": "От конца фразы до готового перевода; определяет, успевают ли субтитры",
      "t.budget": "Запас бюджета субтитров", "t.budget.d": "Локальная задержка − p95 готовности − p95 длины фразы; устойчивый минус = субтитры не успевают",
      "t.cue": "Длина фраз p50/p95", "t.cue.d": "Перцентили длины субтитров; длинным фразам нужен больший бюджет",
      "t.timing": "Источник меток времени", "t.timing.d": "Доля точных меток ASR против локальных оценок",
      "t.drops": "Отброшено клиентом", "t.drops.d": "Субтитры, опоздавшие или истёкшие до показа",
      "dlg.model.title": "Настройки моделей",
      "dlg.model.tab.asr": "Конфигурация ASR", "dlg.model.tab.trans": "Конфигурация перевода",
      "dlg.model.sec.asr": "Профили распознавания речи", "dlg.model.sec.trans": "Профили перевода",
      "dlg.model.addasr": "Добавить ASR", "dlg.model.addtrans": "Добавить перевод",
      "dlg.cookie.title": "Импорт Cookie для входа",
      "dlg.cookie.intro": "Выберите платформу и вставьте Cookie сайта. Данные хранятся только локально; при пробе/запуске создаётся временный файл для целевого URL и сразу удаляется.",
      "field.platform": "Платформа", "field.format": "Формат", "field.payload": "Вставьте содержимое",
      "dlg.cookie.hint": "Один Cookie на строку: имя + табуляция/пробел/равно + значение; поддерживаются cookies.txt и формат заголовка.",
      "state.preparing": "Подготовка", "state.ready": "Готово", "state.joining": "Подключение", "state.buffering": "Набор буфера задержки",
      "state.waiting": "Ожидание воспроизведения", "state.delayed": "Воспроизведение с задержкой", "state.readyToPlay": "Готово к игре",
      "state.stopped": "Остановлено", "state.stopping": "Остановка", "state.error": "Ошибка", "state.playerBuffering": "Плеер буферизует",
      "msg.needUrl": "Сначала вставьте полную ссылку на трансляцию.",
      "msg.probeHint": "Нажмите «Проба», чтобы узнать доступные качества.",
      "msg.ready": "Готово. Выберите качество и нажмите «Старт».",
      "msg.stopped": "Остановлено. Можно снова выполнить пробу или вставить другую ссылку.",
      "chat.count": "{n} сообщ.",
      "diag.title": "Диагностика", "diag.ok": "Всё в порядке",
      "diag.copy": "Скопировать диагностику", "diag.clear": "Очистить",
      "diag.empty": "Пока сообщать нечего.",
      "diag.aria": "Диагностика во время работы", "diag.toggle": "Показать или скрыть диагностику",
      "diag.level.info": "инфо", "diag.level.warn": "предупр.", "diag.level.error": "ошибка",
      "diag.hint.media": "Загрузка трансляции остановилась. Обычно это конец эфира или блокировка платформой — остановите и запустите сеанс заново.",
      "diag.hint.network": "Локальный бэкенд не отвечает. Проверьте, что LingerLens ещё запущен, и повторите.",
      "diag.hint.source": "По этой ссылке трансляция не приходит. Обычно ссылка устарела, эфир закончился, либо платформа требует вход или блокирует запрос — попробуйте другую ссылку на трансляцию.",
      "diag.hint.decode": "Плеер не смог декодировать эту трансляцию. Попробуйте более низкое качество.",
      "diag.hint.translation": "Перевод не работает. Проверьте ключ и лимит провайдера в разделе «Подключения и ключи».",
      "diag.hint.auth": "Для этой трансляции нужен вход. Импортируйте cookie платформы и повторите.",
      "diag.ctx.locale": "Язык интерфейса", "diag.ctx.state": "Состояние сеанса", "diag.ctx.target": "Локальная цель",
      "diag.devlog.on": "запись в {file}", "diag.devlog.failed": "не удалось записать журнал",
      "diag.log.record": "Записать журнал в файл", "diag.log.stop": "Остановить запись", "diag.log.open": "Открыть папку журнала",
      "diag.log.private": "Записывает журнал диагностики в файл, который можно отправить. Он может содержать приватные ссылки на трансляции — проверьте перед отправкой.",
      "diag.ctx.devlog": "Журнал разработки",
      "diag.ctx.page": "Страница", "diag.ctx.useragent": "User-Agent",
      "diag.report.title": "Диагностика LingerLens", "diag.report.time": "Создано", "diag.report.count": "Записей",
      "diag.copied": "Скопировано", "diag.copyFailed": "Не удалось скопировать",
      "diag.update.check": "Проверить обновления", "diag.update.current": "Актуальная версия",
      "diag.update.download": "Скачать {version}", "diag.update.downloading": "Загрузка {percent}%",
      "diag.update.restarting": "Перезапуск для установки…", "diag.update.retry": "Повторить проверку",
      "diag.update.found": "Доступна версия {version} (у вас {current})",
      "diag.update.ready": "Обновление загружено и проверено; скоро перезапуск для установки",
      "diag.update.failed": "Не удалось проверить обновления: {error}",
      "diag.ctx.build": "Сборка",
    },
  };

  /* zh-CN 需要写死的动态文案（DOM 捕获不到） */
  const ZH_DYNAMIC = {
    "state.idle": "未启动", "state.notRunning": "未运行",
    "follow.current": "跟随当前", "follow.back": "回到当前",
    "empty.subs.ready": "等待就绪字幕...", "empty.chat.none": "当前画面尚无直播消息",
    "chat.status.idle": "未监听", "chat.status.connecting": "正在连接聊天",
    "chat.status.authenticating": "正在验证聊天连接", "chat.status.reconnecting": "正在重连聊天",
    "chat.status.ready": "聊天室就绪", "chat.status.receiving": "聊天接收中",
    "chat.status.unavailable": "聊天不可用", "chat.status.failed": "聊天接收失败",
    "chat.received": "收到: {n}", "chat.translated": "翻译: {n}",
    /* 诊断栏的动态文案：这些节点由 diagnostics-log.js 写入，DOM 捕获不到，
       缺了就会落到 DICT.en 而给中文用户显示英文。 */
    "diag.level.info": "信息", "diag.level.warn": "警告", "diag.level.error": "错误",
    "diag.hint.media": "直播源下载中断。多数是直播已结束，或平台限制了这次拉流——停止后重新启动会话即可。",
    "diag.hint.network": "本地后台没有响应。确认 LingerLens 仍在运行，然后重试。",
    "diag.hint.source": "这个链接拉不到直播。多半是链接已失效、直播已经结束，或者平台要求登录、限制了访问——换一个正在直播的链接再试。",
    "diag.hint.decode": "播放器无法解码这路直播。试一下更低的清晰度。",
    "diag.hint.translation": "翻译正在失败。到「连接与密钥」检查翻译厂商的密钥和额度。",
    "diag.hint.auth": "这路直播需要登录。导入对应平台的 Cookie 后重试。",
    "diag.ctx.locale": "界面语言", "diag.ctx.state": "会话状态", "diag.ctx.target": "本地目标",
    "diag.devlog.on": "记录中 {file}", "diag.devlog.failed": "日志写入失败",
    "diag.log.record": "记录日志到文件", "diag.log.stop": "停止记录", "diag.log.open": "打开日志文件夹",
    "diag.log.private": "把诊断日志写进一个可以发送的文件。它可能包含私有直播地址——分享前先看一眼。",
    "diag.ctx.devlog": "开发日志",
    "diag.ctx.page": "页面", "diag.ctx.useragent": "用户代理",
    "diag.ctx.build": "构建",
    "diag.report.title": "LingerLens 诊断信息", "diag.report.time": "生成时间", "diag.report.count": "记录",
    "diag.copied": "已复制", "diag.copyFailed": "复制失败",
    "diag.update.check": "检查更新", "diag.update.current": "已是最新版本",
    "diag.update.download": "下载更新 {version}", "diag.update.downloading": "下载中 {percent}%",
    "diag.update.restarting": "正在重启并安装…", "diag.update.retry": "重试检查更新",
    "diag.update.found": "发现新版本 {version}（当前 {current}）",
    "diag.update.ready": "更新已下载并校验通过，即将重启安装",
    "diag.update.failed": "检查更新失败：{error}",
    "state.preparing": "正在准备", "state.ready": "准备就绪", "state.joining": "启动合流",
    "state.buffering": "建立延迟缓冲", "state.waiting": "待播放", "state.delayed": "延迟播放中",
    "state.readyToPlay": "就绪待播放", "state.stopped": "已停止", "state.stopping": "正在停止",
    "state.error": "错误", "state.playerBuffering": "播放器缓冲",
    "msg.needUrl": "请先粘贴完整的直播链接。",
    "msg.probeHint": "点击准备，读取这场直播的清晰度。",
    "msg.ready": "准备好了，选择清晰度后点击启动。",
    "msg.stopped": "已停止。可以重新准备，或粘贴另一场直播的链接。",
    "chat.count": "{n} 条",
  };

  /* player.js 写入的动态中文短语 → key（过渡桥接，最终应由 player.js 直接调用 t()） */
  const DYNAMIC_PHRASES = {
    "未启动": "state.idle", "未运行": "state.notRunning",
    "正在准备": "state.preparing", "准备就绪": "state.ready", "启动合流": "state.joining",
    "建立延迟缓冲": "state.buffering", "待播放": "state.waiting", "延迟播放中": "state.delayed",
    "就绪待播放": "state.readyToPlay", "已停止": "state.stopped", "正在停止": "state.stopping",
    "错误": "state.error", "播放器缓冲": "state.playerBuffering",
    "跟随当前": "follow.current", "回到当前": "follow.back",
    "请先粘贴完整的直播链接。": "msg.needUrl",
    "点击准备，读取这场直播的清晰度。": "msg.probeHint",
    "准备好了，选择清晰度后点击启动。": "msg.ready",
    "已停止。可以重新准备，或粘贴另一场直播的链接。": "msg.stopped",
    "等待字幕就绪…": "empty.subs", "等待直播聊天消息…": "empty.chat",
    "等待就绪字幕...": "empty.subs.ready", "当前画面尚无直播消息": "empty.chat.none",
    "未监听": "chat.status.idle", "正在连接聊天": "chat.status.connecting",
    "正在验证聊天连接": "chat.status.authenticating", "正在重连聊天": "chat.status.reconnecting",
    "聊天室就绪": "chat.status.ready", "聊天接收中": "chat.status.receiving",
    "聊天不可用": "chat.status.unavailable", "聊天接收失败": "chat.status.failed",
  };
  const DYNAMIC_SINKS = [
    "stateText", "setupFeedback", "message", "streamTitle", "roleFeedback",
    "subtitlesTimelineList", "chatTimelineList", "chatStatsIndicator", "applyDelayButton",
    "followSubtitlesBtn", "followChatBtn", "mediaLoadingText",
  ];

  const DEFAULT_LOCALE = "zh-CN";
  const zhDefaults = new Map();
  let current = DEFAULT_LOCALE;

  const firstText = (node) => {
    for (const child of node.childNodes) if (child.nodeType === 3 && child.nodeValue.trim()) return child.nodeValue;
    return "";
  };
  const setFirstText = (node, text) => {
    for (const child of node.childNodes) {
      if (child.nodeType === 3 && child.nodeValue.trim()) {
        const keepSpace = /\s$/.test(child.nodeValue) && !/\s$/.test(text);
        child.nodeValue = keepSpace ? `${text} ` : text;
        return;
      }
    }
    node.prepend(document.createTextNode(text));
  };

  const getContent = (node, kind) => {
    switch (kind) {
      case "placeholder": return node.placeholder;
      case "html": return node.innerHTML;
      case "fieldLabel": return firstText(node.closest(".field, .color-field")?.querySelector(".field-label") || node);
      case "summaryText": return firstText(node);
      /* 遥测卡片：<div class="tcard"><span class="tcard-label">…</span>
         <b id="…">值</b><span class="tcard-desc">…</span></div> */
      case "cardLabel": return firstText(node.closest(".tcard")?.querySelector(".tcard-label") || node);
      case "cardDesc": return (node.closest(".tcard")?.querySelector(".tcard-desc") || node).textContent;
      case "titleAttr": return node.title || "";
      case "ariaLabel": return node.getAttribute("aria-label") || "";
      default: return node.textContent;
    }
  };

  const setContent = (node, kind, text) => {
    switch (kind) {
      case "placeholder": node.placeholder = text; return;
      case "html": node.innerHTML = text; return;
      case "fieldLabel": {
        const target = node.closest(".field, .color-field")?.querySelector(".field-label");
        if (target) setFirstText(target, text);
        return;
      }
      case "summaryText": setFirstText(node, text); return;
      case "cardLabel": {
        const target = node.closest(".tcard")?.querySelector(".tcard-label");
        if (target) setFirstText(target, text);
        return;
      }
      case "cardDesc": {
        const target = node.closest(".tcard")?.querySelector(".tcard-desc");
        if (target) target.textContent = text;
        return;
      }
      case "titleAttr": node.title = text; return;
      case "ariaLabel": node.setAttribute("aria-label", text); return;
      default: node.textContent = text;
    }
  };

  function captureDefaults() {
    for (const [selector, key, kind] of BINDINGS) {
      if (zhDefaults.has(key)) continue;
      const node = document.querySelector(selector);
      if (node) zhDefaults.set(key, getContent(node, kind || "text"));
    }
  }

  function t(key, vars, fallback) {
    let text;
    if (current === DEFAULT_LOCALE) {
      text = ZH_DYNAMIC[key] ?? zhDefaults.get(key) ?? DICT.en[key] ?? fallback ?? key;
    } else {
      text = DICT[current]?.[key] ?? DICT.en[key] ?? ZH_DYNAMIC[key] ?? zhDefaults.get(key) ?? fallback ?? key;
    }
    if (vars) for (const [name, value] of Object.entries(vars)) text = text.replace(`{${name}}`, value);
    return text;
  }

  /* 动态 sink 双向映射：先把任意语言的文案归一化为 key，再按当前语言渲染。
     这样 zh → de → ja → zh 来回切换都不会残留上一种语言。 */
  const DYNAMIC_KEYS = new Set([
    ...Object.values(DYNAMIC_PHRASES),
    "chat.count", "chat.received", "chat.translated",
  ]);
  const VARIABLE_KEYS = ["chat.count", "chat.received", "chat.translated"];
  const MATCHERS = [];
  (function buildMatchers() {
    const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const templatesFor = (key) => {
      const list = [ZH_DYNAMIC[key], zhDefaults.get(key)];
      for (const lang of Object.keys(DICT)) list.push(DICT[lang][key]);
      return [...new Set(list.filter((value) => typeof value === "string" && value))];
    };
    for (const key of DYNAMIC_KEYS) {
      if (!VARIABLE_KEYS.includes(key)) continue;
      for (const template of templatesFor(key)) {
        MATCHERS.push({ key, re: new RegExp(`^${escape(template).replace("\\{n\\}", "(\\d+)")}$`) });
      }
    }
  })();
  const EXACT = new Map();
  (function buildExact() {
    for (const [zh, key] of Object.entries(DYNAMIC_PHRASES)) EXACT.set(zh, key);
    for (const key of DYNAMIC_KEYS) {
      if (VARIABLE_KEYS.includes(key)) continue;
      const zh = ZH_DYNAMIC[key] ?? zhDefaults.get(key);
      if (typeof zh === "string") EXACT.set(zh, key);
      for (const lang of Object.keys(DICT)) {
        const value = DICT[lang][key];
        if (typeof value === "string") EXACT.set(value, key);
      }
    }
  })();

  const keyFor = (text) => {
    const exact = EXACT.get(text);
    if (exact) return { key: exact, vars: null };
    for (const { key, re } of MATCHERS) {
      const match = text.match(re);
      if (match) return { key, vars: { n: match[1] } };
    }
    return null;
  };

  const renderPart = (part) => {
    const found = keyFor(part);
    return found ? t(found.key, found.vars) : part;
  };

  const translateDynamicNode = (node) => {
    if (node.nodeType !== 3) return;
    const raw = node.nodeValue?.trim();
    if (!raw) return;
    const single = keyFor(raw);
    if (single) {
      const next = t(single.key, single.vars);
      if (next !== raw) node.nodeValue = node.nodeValue.replace(raw, next);
      return;
    }
    // 组合串（例如聊天统计）：逐段映射后用统一分隔符重排
    if (raw.includes(" · ")) {
      const parts = raw.split(" · ").map((part) => renderPart(part.trim()));
      const next = parts.join(" · ");
      if (next !== raw) node.nodeValue = node.nodeValue.replace(raw, next);
    }
  };
  const walkDynamic = (root) => {
    for (const child of root.childNodes) {
      translateDynamicNode(child);
      if (child.childNodes?.length) walkDynamic(child);
    }
  };
  const dynamicObserver = new MutationObserver((records) => {
    for (const record of records) {
      if (record.type === "characterData") translateDynamicNode(record.target);
      else for (const node of record.addedNodes) {
        if (node.nodeType === 3) translateDynamicNode(node);
        else if (node.nodeType === 1) walkDynamic(node);
      }
    }
  });
  function observeDynamicSinks() {
    for (const id of DYNAMIC_SINKS) {
      const node = document.getElementById(id);
      if (!node) continue;
      walkDynamic(node);
      dynamicObserver.observe(node, { childList: true, subtree: true, characterData: true });
    }
  }

  /* 直播中的 NOW 面板由 player.js 维护，语言切换不覆盖 */
  const LIVE_GUARDED = new Set(["#streamTitle", "#message"]);

  function applyLocale(locale) {
    current = DICT[locale] ? locale : DEFAULT_LOCALE;
    document.documentElement.lang = current;
    const isLive = document.querySelector(".state-badge")?.dataset.tone && document.querySelector(".state-badge").dataset.tone !== "idle";
    for (const [selector, key, kind] of BINDINGS) {
      if (isLive && LIVE_GUARDED.has(selector)) continue;
      const node = document.querySelector(selector);
      if (node) setContent(node, kind || "text", t(key));
    }
    observeDynamicSinks();
    /*
     * 有些文案是 JS 按状态拼出来的（诊断栏的按钮标签、开发日志徽标），既不在
     * 静态 BINDINGS 里，也不适合塞进反向查表的 DYNAMIC_SINKS。它们自己监听
     * 这个事件重画，比让 i18n 去猜哪段文字对应哪个 key 可靠。
     */
    document.dispatchEvent(new CustomEvent("i18n:changed", { detail: { locale: current } }));
    return current;
  }

  captureDefaults();
  observeDynamicSinks();
  const switcher = document.getElementById("localeSwitch");
  if (switcher) switcher.addEventListener("change", (event) => applyLocale(event.target.value));

  return { t, applyLocale, get current() { return current; } };
})();
window.I18N = I18N;
