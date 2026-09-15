/* LingerLens 原型 · UI Locale 层
   机制：BINDINGS 把 DOM 绑定到语义 key；zh-CN 默认文案在初始化时从 DOM 捕获，
   其余语言查 DICT。原型演示 5 种语言（zh-CN/en/ja/de/ru），机制上覆盖全部 10 种。 */
const I18N = (() => {
  "use strict";

  /* kind: text(默认) | placeholder | html | fieldLabel | popSliderLabel | tcardLabel | tcardDesc | textAfter */
  const BINDINGS = [
    ["#stateText", "state.idle"],
    ["#openCookieImport", "action.cookie"],
    ["#openModelSettings", "action.connections"],
    [".hero-title", "hero.title", "html"],
    [".hero-sub", "hero.sub"],
    [".hero-right .chip-lime", "chip.trans"],
    [".hero-right .chip-yellow", "chip.dm"],
    [".hero-right .chip-ink", "chip.replay"],
    ['section[aria-label="实时工作台"] .deck-title', "deck1.title"],
    ['.tab[data-view="split"]', "tab.split"],
    ['.tab[data-view="subtitles"]', "tab.subs"],
    ['.tab[data-view="chat"]', "tab.chat"],
    ["#clearSubtitlesBtn", "clear.subs"],
    ["#clearChatBtn", "clear.chat"],
    ["#paneSubtitles .pane-title h3", "pane.subs"],
    ["#paneChat .pane-title h3", "pane.chat"],
    ["#followSubtitlesBtn", "follow"],
    ["#followChatBtn", "follow"],
    ["#subtitlesTimeline .timeline-empty", "empty.subs"],
    ["#chatTimeline .timeline-empty", "empty.chat"],
    [".setup-title", "setup.title"],
    [".setup-sub", "setup.sub"],
    ["#url", "field.url", "fieldLabel"],
    ["#url", "ph.url", "placeholder"],
    ["#proxy", "field.proxy", "fieldLabel"],
    ["#proxy", "ph.proxy", "placeholder"],
    ["#probe", "action.probe"],
    ["#setupPlayback .field-label", "field.quality"],
    ["#start", "action.start"],
    ["#setupFeedback", "setup.feedback"],
    ["#mediaLoadingText", "loading.start"],
    ["#subtitleLayer .sub-src", "sub.ph.src"],
    ["#subtitleLayer .sub-tgt", "sub.ph.tgt"],
    ["#stallBanner b", "stall.title"],
    ["#stallBanner span", "stall.text"],
    [".now-label", "now.label", "html"],
    ["#streamTitle", "now.title.idle"],
    ["#message", "now.msg.idle"],
    [".delay-field .field-label", "delay.target"],
    [".delay-input i", "delay.unit"],
    [".delay-est .field-label", "delay.est"],
    ["#stop", "action.stop"],
    ['section[aria-label="模型分工"] .deck-title', "deck2.title"],
    ["#manageModels", "models.manage"],
    ["#roleAsr", "role.asr", "fieldLabel"],
    ["#roleSubtitle", "role.subtitle", "fieldLabel"],
    ["#roleFallback", "role.fallback", "fieldLabel"],
    ["#roleChat", "role.chat", "fieldLabel"],
    ["#roleFallback option:first-child", "opt.disabled"],
    ["#roleChat option:first-child", "opt.disabled"],
    ['section[aria-label="字幕与弹幕设置"] .deck-title', "deck3.title"],
    [".settings-grid .sgroup:nth-child(1) .sgroup-title", "group.a", "textAfter"],
    [".settings-grid .sgroup:nth-child(2) .sgroup-title", "group.b", "textAfter"],
    [".settings-grid .sgroup:nth-child(3) .sgroup-title", "group.c", "textAfter"],
    ["#subtitlesEnabled ~ .check-text", "check.cloud"],
    ["#chatOverlayToggle ~ .check-text", "check.danmaku"],
    ["#hidePureEmojiToggle ~ .check-text", "check.emoji"],
    ["#chatTranslateToggle ~ .check-text", "check.chattrans"],
    ["#allowCodeSwitching ~ .check-text", "check.codeswitch"],
    ["#popDmToggle ~ .check-text", "check.danmaku"],
    ["#subtitleMode", "label.mode", "fieldLabel"],
    ['#subtitleMode option[value="bilingual"]', "opt.bilingual"],
    ['#subtitleMode option[value="zh"]', "opt.zh"],
    ['#subtitleMode option[value="src"]', "opt.src"],
    ["#subtitleSize", "label.size", "fieldLabel"],
    ['#subtitleSize option[value="small"]', "opt.small"],
    ['#subtitleSize option[value="medium"]', "opt.medium"],
    ['#subtitleSize option[value="large"]', "opt.large"],
    ["#subtitleOpacity", "label.opacity", "fieldLabel"],
    ["#subtitleOffset", "label.offset", "fieldLabel"],
    ["#subtitleSourceColor", "label.srccolor", "fieldLabel"],
    ["#subtitleTranslationColor", "label.tgtcolor", "fieldLabel"],
    ["#resetSubtitlePosition", "reset.pos"],
    ["#chatOverlayOpacity", "label.opacity", "fieldLabel"],
    ["#chatOverlaySize", "label.dmsize", "fieldLabel"],
    ["#sourceLanguageMode", "label.srclang", "fieldLabel"],
    ['#sourceLanguageMode option[value="specified"]', "opt.specified"],
    ['#sourceLanguageMode option[value="detect"]', "opt.detect"],
    ["#sourceLanguage", "label.specifiedsrc", "fieldLabel"],
    ["#targetLanguage", "label.tgtlang", "fieldLabel"],
    ['section[aria-label="运行数据"] .deck-title', "deck4.title"],
    ["#hiddenDelay", "t.hidden", "tcardLabel"],
    ["#hiddenDelay", "t.hidden.d", "tcardDesc"],
    ["#playerDelay", "t.behind", "tcardLabel"],
    ["#playerDelay", "t.behind.d", "tcardDesc"],
    ["#buffer", "t.buffer", "tcardLabel"],
    ["#buffer", "t.buffer.d", "tcardDesc"],
    ["#resolution", "t.output", "tcardLabel"],
    ["#resolution", "t.output.d", "tcardDesc"],
    ["#asrUsage", "t.asr", "tcardLabel"],
    ["#asrUsage", "t.asr.d", "tcardDesc"],
    ["#translationLatency", "t.latency", "tcardLabel"],
    ["#translationLatency", "t.latency.d", "tcardDesc"],
    ["#subtitleReadyLag", "t.ready", "tcardLabel"],
    ["#subtitleReadyLag", "t.ready.d", "tcardDesc"],
    ["#budgetMargin", "t.budget", "tcardLabel"],
    ["#budgetMargin", "t.budget.d", "tcardDesc"],
    ["#volumePop .pop-label", "vol"],
    ["#qualityPop .pop-label", "field.quality"],
    ['#subtitlePop > .pop-label:nth-of-type(1)', "group.a"],
    ['#subtitlePop > .pop-label:nth-of-type(2)', "label.size"],
    ["#popSubOpacity", "label.opacity", "popSliderLabel"],
    ["#popDmOpacity", "label.opacity", "popSliderLabel"],
    ["#popDmSize", "label.dmsize", "popSliderLabel"],
    ['#popSubMode button[data-value="bilingual"]', "seg.bilingual"],
    ['#popSubMode button[data-value="zh"]', "seg.zh"],
    ['#popSubMode button[data-value="src"]', "seg.src"],
    ['#popSubSize button[data-value="small"]', "opt.small"],
    ['#popSubSize button[data-value="medium"]', "opt.medium"],
    ['#popSubSize button[data-value="large"]', "opt.large"],
    ['#quality option[value="auto"]', "opt.auto"],
    ['#qualityPop .pop-item[data-quality="auto"]', "opt.auto"],
    ['#qualityPop .pop-item[data-quality="1080p60"]', "q.1080"],
    ["#modelSettingsTitle", "dlg.model.title"],
    ["#modelSettingsDialog .dialog-tabs .tab:nth-child(1)", "dlg.model.tab.asr"],
    ["#modelSettingsDialog .dialog-tabs .tab:nth-child(2)", "dlg.model.tab.trans"],
    ["#modelSettingsDialog .dialog-body > .btn", "dlg.model.add"],
    ["#modelSettingsDialog .dialog-foot .btn-ghost", "action.cancel"],
    ["#modelSettingsDialog .dialog-foot .btn-go", "action.savemodels"],
    ["#cookieImportTitle", "dlg.cookie.title"],
    ["#cookieImportDialog .dialog-intro", "dlg.cookie.intro"],
    ["#cookiePlatform", "field.platform", "fieldLabel"],
    ["#cookieFormat", "field.format", "fieldLabel"],
    ["#cookiePayload", "field.payload", "fieldLabel"],
    ["#cookieImportDialog .dialog-foot .btn-ghost", "action.cancel"],
    ["#cookieImportDialog .dialog-foot .btn-primary", "action.import"],
  ];

  const DICT = {
    en: {
      "state.idle": "Not started",
      "state.live": "Live",
      "action.cookie": "Import Cookie", "action.connections": "Connections & Keys",
      "hero.title": 'Local Delay <span class="hero-x">×</span> Bilingual Live Subtitles',
      "hero.sub": "YouTube · Bilibili · Twitch streams — saved locally first, then played with recognition and translation.",
      "chip.trans": "TRANS", "chip.dm": "DANMAKU", "chip.replay": "REPLAY",
      "deck1.title": "Watch Live",
      "tab.split": "Split View", "tab.subs": "Subtitles", "tab.chat": "Live Chat",
      "clear.subs": "Clear", "clear.chat": "Clear",
      "pane.subs": "Live Subtitles", "pane.chat": "Live Chat",
      "follow": "Follow",
      "empty.subs": "Waiting for subtitles…", "empty.chat": "Waiting for chat messages…",
      "setup.title": "Open a Live Stream",
      "setup.sub": "Paste link → Probe → Pick quality → Start",
      "field.url": "Stream URL",
      "action.probe": "Probe",
      "field.proxy": "Proxy (optional)", "ph.proxy": "System proxy; e.g. http://127.0.0.1:7890",
      "field.quality": "Quality", "action.start": "Start ▶",
      "setup.feedback": "Supports YouTube, Bilibili and Twitch live streams.",
      "feedback.needurl": "Paste a stream link first.",
      "feedback.probing": "Probing available qualities…",
      "feedback.probed": "Found 4 browser-compatible formats. Pick a quality and start.",
      "loading.start": "Starting stream…",
      "sub.ph.src": "Subtitles appear here", "sub.ph.tgt": "Drag me to move or resize",
      "stall.title": "Buffering", "stall.text": "Local stream is catching up…",
      "now.label": '<i class="now-dot"></i>NOW · STATUS',
      "now.title.idle": "Waiting for stream URL",
      "now.msg.idle": "Enter a stream link in the player to begin.",
      "now.msg.live": "Local pipeline running; subtitles and chat syncing.",
      "delay.target": "Local target", "delay.unit": "s", "delay.est": "Local estimate",
      "action.stop": "Stop ■",
      "deck2.title": "Model Roles", "models.manage": "Model Settings",
      "role.asr": "Speech Recognition", "role.subtitle": "Subtitle Translation",
      "role.fallback": "Fallback Translation", "role.chat": "Chat Translation",
      "deck3.title": "Subtitles & Chat",
      "group.a": "Subtitle Display", "group.b": "Danmaku Overlay", "group.c": "Languages",
      "check.cloud": "Enable cloud subtitles", "check.danmaku": "Danmaku overlay",
      "check.emoji": "Hide pure-emoji", "check.chattrans": "Translate chat",
      "check.codeswitch": "Allow code-switching",
      "label.mode": "Display", "opt.bilingual": "Original+Translation",
      "opt.zh": "Translation only", "opt.src": "Original only",
      "label.size": "Size", "opt.small": "Small", "opt.medium": "Medium", "opt.large": "Large",
      "label.opacity": "Opacity", "label.offset": "Offset",
      "label.srccolor": "Source", "label.tgtcolor": "Translation",
      "reset.pos": "Reset position", "label.dmsize": "Size",
      "label.srclang": "Source language", "opt.specified": "Specified", "opt.detect": "Auto-detect",
      "label.specifiedsrc": "Specified source", "label.tgtlang": "Target language",
      "deck4.title": "Telemetry",
      "t.hidden": "Locally staged media", "t.hidden.d": "Downloaded but withheld for the local target",
      "t.behind": "Player local lag", "t.behind.d": "Intentional distance behind the newest position",
      "t.buffer": "Player buffer", "t.buffer.d": "Playable media ahead of the playhead",
      "t.output": "Current output", "t.output.d": "Resolution and frame rate",
      "t.asr": "ASR usage / cost", "t.asr.d": "Recognized audio and estimated cost",
      "t.latency": "Translation latency", "t.latency.d": "Avg. request-to-response per sentence",
      "t.ready": "Subtitle ready p50/p95", "t.ready.d": "Speech end to translation ready",
      "t.budget": "Subtitle budget margin", "t.budget.d": "Sustained negative = subtitles can't keep up",
      "vol": "Volume",
      "seg.bilingual": "Bilingual", "seg.zh": "Trans.", "seg.src": "Orig.",
      "opt.auto": "Auto (best compatible)", "q.1080": "1080P High bitrate",
      "chat.count": "{n} msgs",
      "empty.subs.cleared": "Cleared. Waiting for new subtitles…",
      "empty.chat.cleared": "Cleared. Waiting for new messages…",
      "dlg.model.title": "Model Settings", "dlg.model.tab.asr": "ASR Profiles",
      "dlg.model.tab.trans": "Translation Profiles", "dlg.model.add": "＋ Add Profile",
      "action.cancel": "Cancel", "action.savemodels": "Save Model Settings",
      "dlg.cookie.title": "Import Login Cookie",
      "dlg.cookie.intro": "Pick a platform and paste its cookies. Stored locally only.",
      "field.platform": "Platform", "field.format": "Format", "field.payload": "Paste here",
      "action.import": "Import & Authorize",
      "opt.disabled": "Disabled",
    },
    ja: {
      "state.idle": "未開始",
      "state.live": "配信中",
      "action.cookie": "Cookie をインポート", "action.connections": "接続とキー",
      "hero.title": 'ローカル遅延<span class="hero-x">×</span>リアルタイム二言語字幕',
      "hero.sub": "YouTube · Bilibili · Twitch の配信を、まず本機に保存し、認識と翻訳を添えて再生します。",
      "chip.trans": "翻訳", "chip.dm": "弾幕", "chip.replay": "再生",
      "deck1.title": "ライブ視聴",
      "tab.split": "左右分割", "tab.subs": "字幕", "tab.chat": "チャット",
      "clear.subs": "クリア", "clear.chat": "クリア",
      "pane.subs": "リアルタイム字幕", "pane.chat": "ライブチャット",
      "follow": "追従",
      "empty.subs": "字幕を待っています…", "empty.chat": "チャットを待っています…",
      "setup.title": "配信を開く",
      "setup.sub": "リンクを貼る → 準備 → 画質選択 → 開始",
      "field.url": "配信リンク",
      "action.probe": "準備",
      "field.proxy": "プロキシ（任意）", "ph.proxy": "システムプロキシ；例 http://127.0.0.1:7890",
      "field.quality": "画質", "action.start": "開始 ▶",
      "setup.feedback": "YouTube・Bilibili・Twitch の配信に対応。",
      "feedback.needurl": "先に配信リンクを貼ってください。",
      "feedback.probing": "利用可能な画質を検出中…",
      "feedback.probed": "ブラウザ互換フォーマットを 4 件検出。画質を選んで開始してください。",
      "loading.start": "配信を開始しています…",
      "sub.ph.src": "字幕はここに表示されます", "sub.ph.tgt": "ドラッグで移動・リサイズ",
      "stall.title": "バッファ中", "stall.text": "ローカルストリームが追いついています…",
      "now.label": '<i class="now-dot"></i>NOW · 現在の状態',
      "now.title.idle": "配信URLを待っています",
      "now.msg.idle": "画面に配信リンクを入力して開始。",
      "now.msg.live": "ローカルパイプライン実行中。字幕とチャットを同期表示。",
      "delay.target": "ローカル目標", "delay.unit": "秒", "delay.est": "ローカル推定",
      "action.stop": "停止 ■",
      "deck2.title": "モデル分担", "models.manage": "モデル設定",
      "role.asr": "音声認識", "role.subtitle": "字幕翻訳",
      "role.fallback": "字幕翻訳フォールバック", "role.chat": "コメント翻訳",
      "deck3.title": "字幕と弾幕の設定",
      "group.a": "字幕表示", "group.b": "画面弾幕", "group.c": "翻訳言語",
      "check.cloud": "クラウド字幕を有効化", "check.danmaku": "画面弾幕",
      "check.emoji": "絵文字のみを隠す", "check.chattrans": "チャットを翻訳",
      "check.codeswitch": "言語混合を許可",
      "label.mode": "表示", "opt.bilingual": "原文+訳文",
      "opt.zh": "訳文のみ", "opt.src": "原文のみ",
      "label.size": "サイズ", "opt.small": "小", "opt.medium": "中", "opt.large": "大",
      "label.opacity": "不透明度", "label.offset": "オフセット",
      "label.srccolor": "原文", "label.tgtcolor": "訳文",
      "reset.pos": "位置をリセット", "label.dmsize": "サイズ",
      "label.srclang": "ソース言語", "opt.specified": "言語を指定", "opt.detect": "自動認識",
      "label.specifiedsrc": "指定ソース言語", "label.tgtlang": "ターゲット言語",
      "deck4.title": "実行データ",
      "t.hidden": "ローカル保持メディア", "t.hidden.d": "取得済みだが目標遅延のため未再生",
      "t.behind": "プレイヤー遅延", "t.behind.d": "最新位置から意図的に遅れる時間",
      "t.buffer": "プレイヤーバッファ", "t.buffer.d": "再生ヘッド前方の再生可能な長さ",
      "t.output": "現在の出力", "t.output.d": "解像度とフレームレート",
      "t.asr": "ASR 使用量 / 費用", "t.asr.d": "認識した音声時間と推定費用",
      "t.latency": "翻訳レイテンシ", "t.latency.d": "1文あたりの平均応答時間",
      "t.ready": "字幕準備 p50/p95", "t.ready.d": "発話終了から訳文準備まで",
      "t.budget": "字幕予算余裕", "t.budget.d": "継続的な負値は字幕が追いつかない状態",
      "vol": "音量",
      "seg.bilingual": "二言語", "seg.zh": "訳文", "seg.src": "原文",
      "opt.auto": "自動（最高互換）", "q.1080": "1080P 高ビットレート",
      "chat.count": "{n} 件",
      "empty.subs.cleared": "クリアしました。新しい字幕を待機…",
      "empty.chat.cleared": "クリアしました。新しいメッセージを待機…",
      "dlg.model.title": "モデル設定", "dlg.model.tab.asr": "音声認識プロファイル",
      "dlg.model.tab.trans": "翻訳プロファイル", "dlg.model.add": "＋ プロファイル追加",
      "action.cancel": "キャンセル", "action.savemodels": "モデル設定を保存",
      "dlg.cookie.title": "ログイン Cookie をインポート",
      "dlg.cookie.intro": "プラットフォームを選び Cookie を貼り付け。本機にのみ保存。",
      "field.platform": "プラットフォーム", "field.format": "形式", "field.payload": "貼り付け内容",
      "action.import": "インポートして認証",
      "opt.disabled": "無効",
    },
    de: {
      "state.idle": "Nicht gestartet",
      "state.live": "Live",
      "action.cookie": "Cookie importieren", "action.connections": "Verbindungen & Schlüssel",
      "hero.title": 'Lokale Verzögerung <span class="hero-x">×</span> zweisprachige Live-Untertitel',
      "hero.sub": "YouTube- · Bilibili- · Twitch-Streams — erst lokal speichern, dann mit Erkennung und Übersetzung abspielen.",
      "chip.trans": "ÜBERS.", "chip.dm": "DANMAKU", "chip.replay": "REPLAY",
      "deck1.title": "Live ansehen",
      "tab.split": "Geteilte Ansicht", "tab.subs": "Live-Untertitel", "tab.chat": "Live-Chat",
      "clear.subs": "Leeren", "clear.chat": "Leeren",
      "pane.subs": "Live-Untertitel", "pane.chat": "Live-Chat",
      "follow": "Folgen",
      "empty.subs": "Warte auf Untertitel…", "empty.chat": "Warte auf Chat-Nachrichten…",
      "setup.title": "Livestream öffnen",
      "setup.sub": "Link einfügen → Prüfen → Qualität wählen → Start",
      "field.url": "Stream-Link",
      "action.probe": "Prüfen",
      "field.proxy": "Proxy (optional)", "ph.proxy": "Systemproxy; z. B. http://127.0.0.1:7890",
      "field.quality": "Qualität", "action.start": "Start ▶",
      "setup.feedback": "Unterstützt YouTube-, Bilibili- und Twitch-Streams.",
      "feedback.needurl": "Füge zuerst einen Stream-Link ein.",
      "feedback.probing": "Verfügbare Qualitäten werden geprüft…",
      "feedback.probed": "4 browserkompatible Formate gefunden. Qualität wählen und starten.",
      "loading.start": "Stream wird gestartet…",
      "sub.ph.src": "Untertitel erscheinen hier", "sub.ph.tgt": "Ziehen zum Verschieben und Skalieren",
      "stall.title": "Pufferung", "stall.text": "Lokaler Stream holt auf…",
      "now.label": '<i class="now-dot"></i>NOW · STATUS',
      "now.title.idle": "Warte auf Stream-Link",
      "now.msg.idle": "Gib einen Stream-Link im Player ein, um zu beginnen.",
      "now.msg.live": "Lokale Pipeline läuft; Untertitel und Chat werden synchronisiert.",
      "delay.target": "Lokales Ziel", "delay.unit": "s", "delay.est": "Lokale Schätzung",
      "action.stop": "Stopp ■",
      "deck2.title": "Modellrollen", "models.manage": "Modelleinstellungen",
      "role.asr": "Spracherkennung", "role.subtitle": "Untertitelübersetzung",
      "role.fallback": "Fallback-Übersetzung", "role.chat": "Chat-Übersetzung",
      "deck3.title": "Untertitel & Danmaku",
      "group.a": "Untertitelanzeige", "group.b": "Danmaku im Bild", "group.c": "Sprachen",
      "check.cloud": "Cloud-Untertitel aktivieren", "check.danmaku": "Danmaku im Bild",
      "check.emoji": "Reine Emojis ausblenden", "check.chattrans": "Chat übersetzen",
      "check.codeswitch": "Sprachwechsel erlauben",
      "label.mode": "Anzeige", "opt.bilingual": "Original+Übersetzung",
      "opt.zh": "Nur Übersetzung", "opt.src": "Nur Original",
      "label.size": "Größe", "opt.small": "Klein", "opt.medium": "Mittel", "opt.large": "Groß",
      "label.opacity": "Deckkraft", "label.offset": "Versatz",
      "label.srccolor": "Original", "label.tgtcolor": "Übersetzung",
      "reset.pos": "Position zurücksetzen", "label.dmsize": "Größe",
      "label.srclang": "Quellsprache", "opt.specified": "Festgelegt", "opt.detect": "Automatisch",
      "label.specifiedsrc": "Festgelegte Quellsprache", "label.tgtlang": "Zielsprache",
      "deck4.title": "Laufzeitdaten",
      "t.hidden": "Lokal zwischengespeichert", "t.hidden.d": "Geladen, aber für das lokale Ziel zurückgehalten",
      "t.behind": "Lokaler Rückstand", "t.behind.d": "Absichtlicher Abstand zur neuesten Position",
      "t.buffer": "Player-Puffer", "t.buffer.d": "Abspielbare Medien vor dem Abspielkopf",
      "t.output": "Aktuelle Ausgabe", "t.output.d": "Auflösung und Bildrate",
      "t.asr": "ASR-Nutzung / Kosten", "t.asr.d": "Erkannte Audiodauer und geschätzte Kosten",
      "t.latency": "Übersetzungslatenz", "t.latency.d": "Ø Anfrage bis Antwort pro Satz",
      "t.ready": "Untertitel p50/p95", "t.ready.d": "Vom Satzende bis zur fertigen Übersetzung",
      "t.budget": "Budgetreserve Untertitel", "t.budget.d": "Dauerhaft negativ = Untertitel hinken hinterher",
      "vol": "Lautstärke",
      "seg.bilingual": "Zweispr.", "seg.zh": "Übers.", "seg.src": "Orig.",
      "opt.auto": "Automatisch (beste Qualität)", "q.1080": "1080P hohe Bitrate",
      "chat.count": "{n} Nachr.",
      "empty.subs.cleared": "Geleert. Warte auf neue Untertitel…",
      "empty.chat.cleared": "Geleert. Warte auf neue Nachrichten…",
      "dlg.model.title": "Modelleinstellungen", "dlg.model.tab.asr": "ASR-Profile",
      "dlg.model.tab.trans": "Übersetzungsprofile", "dlg.model.add": "＋ Profil hinzufügen",
      "action.cancel": "Abbrechen", "action.savemodels": "Einstellungen speichern",
      "dlg.cookie.title": "Login-Cookie importieren",
      "dlg.cookie.intro": "Plattform wählen und Cookie einfügen. Wird nur lokal gespeichert.",
      "field.platform": "Plattform", "field.format": "Format", "field.payload": "Inhalt einfügen",
      "action.import": "Importieren & autorisieren",
      "opt.disabled": "Deaktiviert",
    },
    ru: {
      "state.idle": "Не запущено",
      "state.live": "В эфире",
      "action.cookie": "Импорт Cookie", "action.connections": "Подключения и ключи",
      "hero.title": 'Локальная задержка <span class="hero-x">×</span> двуязычные субтитры',
      "hero.sub": "Трансляции YouTube · Bilibili · Twitch — сначала сохраняются локально, затем играются с распознаванием и переводом.",
      "chip.trans": "ПЕРЕВОД", "chip.dm": "ДАНМАКУ", "chip.replay": "ПОВТОР",
      "deck1.title": "Просмотр трансляции",
      "tab.split": "Раздельный вид", "tab.subs": "Субтитры", "tab.chat": "Чат",
      "clear.subs": "Очистить", "clear.chat": "Очистить",
      "pane.subs": "Субтитры", "pane.chat": "Чат трансляции",
      "follow": "Следить",
      "empty.subs": "Ожидание субтитров…", "empty.chat": "Ожидание сообщений чата…",
      "setup.title": "Открыть трансляцию",
      "setup.sub": "Вставьте ссылку → Проба → Качество → Старт",
      "field.url": "Ссылка на трансляцию",
      "action.probe": "Проба",
      "field.proxy": "Прокси (необязательно)", "ph.proxy": "Системный прокси; напр. http://127.0.0.1:7890",
      "field.quality": "Качество", "action.start": "Старт ▶",
      "setup.feedback": "Поддерживаются трансляции YouTube, Bilibili и Twitch.",
      "feedback.needurl": "Сначала вставьте ссылку на трансляцию.",
      "feedback.probing": "Определяем доступные качества…",
      "feedback.probed": "Найдено 4 совместимых формата. Выберите качество и начните.",
      "loading.start": "Запуск трансляции…",
      "sub.ph.src": "Субтитры появятся здесь", "sub.ph.tgt": "Перетащите, чтобы переместить",
      "stall.title": "Буферизация", "stall.text": "Локальный поток догоняет…",
      "now.label": '<i class="now-dot"></i>NOW · СТАТУС',
      "now.title.idle": "Ожидание ссылки на трансляцию",
      "now.msg.idle": "Введите ссылку в плеере, чтобы начать.",
      "now.msg.live": "Локальный конвейер работает; субтитры и чат синхронизируются.",
      "delay.target": "Локальная цель", "delay.unit": "с", "delay.est": "Локальная оценка",
      "action.stop": "Стоп ■",
      "deck2.title": "Роли моделей", "models.manage": "Настройки моделей",
      "role.asr": "Распознавание речи", "role.subtitle": "Перевод субтитров",
      "role.fallback": "Резервный перевод", "role.chat": "Перевод чата",
      "deck3.title": "Субтитры и данмаку",
      "group.a": "Отображение субтитров", "group.b": "Данмаку на экране", "group.c": "Языки перевода",
      "check.cloud": "Облачные субтитры", "check.danmaku": "Данмаку на экране",
      "check.emoji": "Скрывать чистые эмодзи", "check.chattrans": "Переводить чат",
      "check.codeswitch": "Смешение языков",
      "label.mode": "Режим", "opt.bilingual": "Оригинал+перевод",
      "opt.zh": "Только перевод", "opt.src": "Только оригинал",
      "label.size": "Размер", "opt.small": "Мал.", "opt.medium": "Сред.", "opt.large": "Бол.",
      "label.opacity": "Прозрачность", "label.offset": "Сдвиг",
      "label.srccolor": "Оригинал", "label.tgtcolor": "Перевод",
      "reset.pos": "Сбросить позицию", "label.dmsize": "Размер",
      "label.srclang": "Исходный язык", "opt.specified": "Указанный", "opt.detect": "Автоопределение",
      "label.specifiedsrc": "Указанный исходный", "label.tgtlang": "Целевой язык",
      "deck4.title": "Телеметрия",
      "t.hidden": "Локально сохранено", "t.hidden.d": "Загружено, но удерживается ради локальной цели",
      "t.behind": "Локальное отставание", "t.behind.d": "Насколько плеер отстаёт от новейшей позиции",
      "t.buffer": "Буфер плеера", "t.buffer.d": "Готовые к воспроизведению данные впереди",
      "t.output": "Текущий вывод", "t.output.d": "Разрешение и частота кадров",
      "t.asr": "ASR: объём / расход", "t.asr.d": "Длительность распознанного аудио и оценка расхода",
      "t.latency": "Задержка перевода", "t.latency.d": "Среднее время от запроса до ответа",
      "t.ready": "Готовность p50/p95", "t.ready.d": "От конца фразы до готового перевода",
      "t.budget": "Запас бюджета субтитров", "t.budget.d": "Устойчивый минус = субтитры не поспевают",
      "vol": "Громкость",
      "seg.bilingual": "Обе", "seg.zh": "Перевод", "seg.src": "Ориг.",
      "opt.auto": "Авто (лучшее качество)", "q.1080": "1080P высокий битрейт",
      "chat.count": "{n} сообщ.",
      "empty.subs.cleared": "Очищено. Ожидание новых субтитров…",
      "empty.chat.cleared": "Очищено. Ожидание новых сообщений…",
      "dlg.model.title": "Настройки моделей", "dlg.model.tab.asr": "Профили ASR",
      "dlg.model.tab.trans": "Профили перевода", "dlg.model.add": "＋ Добавить профиль",
      "action.cancel": "Отмена", "action.savemodels": "Сохранить настройки",
      "dlg.cookie.title": "Импорт Cookie для входа",
      "dlg.cookie.intro": "Выберите платформу и вставьте Cookie. Хранятся только локально.",
      "field.platform": "Платформа", "field.format": "Формат", "field.payload": "Вставьте содержимое",
      "action.import": "Импорт и авторизация",
      "opt.disabled": "Отключено",
    },
  };

  /* zh-CN 仅需要 DOM 里捕获不到的动态文案 */
  const ZH_DYNAMIC = {
    "state.live": "直播中",
    "feedback.needurl": "先粘贴一个直播链接。",
    "feedback.probing": "正在探测可用清晰度…",
    "feedback.probed": "已找到 4 个浏览器兼容格式，选择清晰度后启动。",
    "now.msg.live": "本地管线运行中，字幕与聊天同步上屏。",
    "empty.subs.cleared": "已清空，等待新字幕…",
    "empty.chat.cleared": "已清空，等待新消息…",
    "chat.count": "{n} 条",
  };

  const DEFAULT_LOCALE = "zh-CN";
  const zhDefaults = new Map();
  let current = DEFAULT_LOCALE;

  const firstText = (el) => {
    for (const node of el.childNodes) if (node.nodeType === 3) return node.textContent;
    return "";
  };
  const lastText = (el) => {
    for (let i = el.childNodes.length - 1; i >= 0; i--) {
      if (el.childNodes[i].nodeType === 3) return el.childNodes[i].textContent;
    }
    return "";
  };

  const getContent = (el, kind) => {
    switch (kind) {
      case "placeholder": return el.placeholder;
      case "html": return el.innerHTML;
      case "fieldLabel": return firstText(el.closest(".field, .color-field")?.querySelector(".field-label") || el);
      case "popSliderLabel": return (el.closest(".pop-slider")?.querySelector(".pop-label") || el).textContent;
      case "tcardLabel": return (el.closest(".tcard")?.querySelector(".tcard-label") || el).textContent;
      case "tcardDesc": return (el.closest(".tcard")?.querySelector(".tcard-desc") || el).textContent;
      case "textAfter": return lastText(el);
      default: return el.textContent;
    }
  };

  const setFirstText = (el, text) => {
    for (const node of el.childNodes) {
      if (node.nodeType === 3) {
        const keepSpace = /\s$/.test(node.textContent) && !/\s$/.test(text);
        node.textContent = keepSpace ? text + " " : text;
        return;
      }
    }
    el.prepend(document.createTextNode(text));
  };

  const setContent = (el, kind, text) => {
    switch (kind) {
      case "placeholder": el.placeholder = text; return;
      case "html": el.innerHTML = text; return;
      case "fieldLabel": {
        const lab = el.closest(".field, .color-field")?.querySelector(".field-label");
        if (lab) setFirstText(lab, text);
        return;
      }
      case "popSliderLabel": {
        const lab = el.closest(".pop-slider")?.querySelector(".pop-label");
        if (lab) lab.textContent = text;
        return;
      }
      case "tcardLabel": {
        const lab = el.closest(".tcard")?.querySelector(".tcard-label");
        if (lab) lab.textContent = text;
        return;
      }
      case "tcardDesc": {
        const lab = el.closest(".tcard")?.querySelector(".tcard-desc");
        if (lab) lab.textContent = text;
        return;
      }
      case "textAfter": {
        for (let i = el.childNodes.length - 1; i >= 0; i--) {
          if (el.childNodes[i].nodeType === 3) { el.childNodes[i].textContent = text; return; }
        }
        el.append(document.createTextNode(text));
        return;
      }
      default: el.textContent = text;
    }
  };

  const resolve = (sel) => document.querySelector(sel);

  function captureDefaults() {
    for (const [sel, key, kind] of BINDINGS) {
      if (zhDefaults.has(key)) continue;
      const el = resolve(sel);
      if (el) zhDefaults.set(key, getContent(el, kind || "text"));
    }
  }

  function t(key, vars) {
    let text;
    if (current === DEFAULT_LOCALE) {
      text = ZH_DYNAMIC[key] ?? zhDefaults.get(key) ?? DICT.en[key] ?? key;
    } else {
      text = DICT[current]?.[key] ?? DICT.en[key] ?? ZH_DYNAMIC[key] ?? zhDefaults.get(key) ?? key;
    }
    if (vars) for (const [k, v] of Object.entries(vars)) text = text.replace(`{${k}}`, v);
    return text;
  }

  /* 直播中不覆盖 NOW 面板的动态标题/消息 */
  const LIVE_GUARDED = new Set(["#streamTitle", "#message"]);

  function applyLocale(locale) {
    current = DICT[locale] ? locale : DEFAULT_LOCALE;
    document.documentElement.lang = current;
    const isLive = document.getElementById("stateBadge")?.dataset.tone === "live";
    for (const [sel, key, kind] of BINDINGS) {
      if (isLive && LIVE_GUARDED.has(sel)) continue;
      const el = resolve(sel);
      if (el) setContent(el, kind || "text", t(key));
    }
  }

  captureDefaults();
  const switcher = document.getElementById("localeSwitch");
  if (switcher) switcher.addEventListener("change", (e) => applyLocale(e.target.value));

  return { t, applyLocale, get current() { return current; } };
})();
window.I18N = I18N;
