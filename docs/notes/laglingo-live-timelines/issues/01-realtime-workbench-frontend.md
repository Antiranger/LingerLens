# 01 — 实时时间轴工作台前端

**What to build:** 一次完成 `web-player/` 范围内的全部用户界面：统一 MediaClock、真实时间直播控制条、左侧双语字幕时间轴、右侧直播弹幕时间轴、弹幕翻译开关、三栏布局、Follow Mode、回看和响应式。严格复用当前 LagLingo 风格，不改后端协议实现。

**Primary ownership:**
- `prototype/hls-companion/web-player/index.html`
- `prototype/hls-companion/web-player/player.js`
- `prototype/hls-companion/web-player/style.css`
- 新的 DOM-free 前端模块及其 Node 测试
- `tests/test_web_assets.js`

**Blocked by:** None — 与 Ticket 02 按 Spec 中固定的 HTTP/JSON 合同并行开发，可使用 deterministic fixtures。

**Status:** ready

## MediaClock 与控制条

- [ ] 先写纯逻辑失败测试：`hls.playingDate` 优先、fragment `programDateTime + offset` 回退；PDT 不可用时返回 unavailable，禁止退回 `Date.now()`。
- [ ] 新增 DOM-free `media-clock.js`，统一 media position ↔ epoch、seekable start/current/end 和本机时区 24 小时制 `HH:mm:ss`；字幕、弹幕和控制条不得各写一份时钟算法。
- [ ] 隐藏原生 `<video controls>`，实现播放/暂停、静音/音量、当前画面时间、seekable 滑轨、左右端真实时间、回到直播和 LagLingo 全屏。
- [ ] 滑轨内部仍使用 media seconds；拖动时预览墙钟、释放后 seek，并在移动的 HLS window 内 clamp。回到直播必须落到现有 hls.js 安全 live-sync 距离，不能骑到边缘造成卡顿。
- [ ] 保持 `.player-stage` 全屏所有权、字幕浮层和现有播放稳定性；控制条支持键盘、ARIA、可见 focus 和 reduced-motion。

## 左侧字幕时间轴

- [ ] 复用现有 `/api/subtitles?afterSeq=N`、cue Map 和 subtitle offset；不建立第二个 Store/游标/时间模型。
- [ ] cue 只有 `state ∈ {done, failed}` 且播放头达到校准后的时间才公开；`src/translating` 和未来字幕不可提前出现。
- [ ] 每行显示校准后的 `HH:mm:ss`、译文和原文；显示模式复用“原文+译文 / 仅译文 / 仅原文”，保留 bidi isolation；`failed` 标记“仅原文”。
- [ ] 当前 cue 随回看高亮，而不是只追加不回算。subtitle offset 必须统一影响浮层、列表公开、高亮、行时间和点击 seek。
- [ ] 点击仍在 `video.seekable` 内的字幕行可回看；超出约 30 秒本地窗口时禁用并说明。

## 右侧直播弹幕时间轴

- [ ] 按固定合同轮询 `/api/live-messages?afterSeq=N`，维护独立 afterSeq/Map；只公开 `message.mediaTime <= playbackWallTime + 0.25s` 的消息。
- [ ] 每行显示 `HH:mm:ss`、作者、原文和可选译文；revision 更新原地补译文，不改变时间和顺序。翻译失败/跳过只保留原文，不弹 toast。
- [ ] 面板头部提供默认关闭的“翻译弹幕”开关，启动时写入 `liveMessages.translate`，直播中调用 `POST /api/live-messages/settings`；显示当前 Target Language 和 degraded 状态。
- [ ] Stop/切流清空弹幕 Map/游标；高流量下 DOM 硬上限 500 行，不为每条消息创建 timer。

## Follow Mode、布局和状态

- [ ] 左右列表共用一个 DOM-free Follow Mode 模块：自动保持当前项可见；用户 wheel/touch/键盘滚动后暂停，点击“回到当前”恢复；150–200ms 且尊重 reduced-motion。
- [ ] 主工作台 breakout 到 `min(1920px, 100vw - 40px)`，下方 decks 维持当前 1560px；宽屏三栏为 `240–280px / minmax(800px,1fr) / 240–280px`，不能因为加侧栏明显缩小中央视频。
- [ ] 迁移现有 `now-panel` 到播放器下方状态摘要或 telemetry 位置，保留全部延迟/状态功能，不做无关重设计。
- [ ] `>=1500px` 三栏；`900–1499px` 视频在上、两列表在下两列；`<900px` 用字幕/弹幕 tabs；全屏只含视频、字幕浮层和控制条。
- [ ] 两侧覆盖 idle、connecting skeleton、running-empty、running、follow-paused、unavailable、reconnecting、error、translation degraded，错误只在所属面板内联。
- [ ] 样式照抄现有 paper/card/line/camellia/rose/mono/JP-font；不加头像、横飞弹幕、新主题或大色块。核对长用户名、RTL、多语言、200% zoom 和对比度。

## Verification

- [ ] 增加 MediaClock、visibility、Follow Mode、offset、seek、revision 的 Node 测试，以及 web asset 测试。
- [ ] 用 deterministic API fixtures 做 Chromium smoke：当前画面、滑轨端点、左 cue、右 message 使用同一墙钟；回看约 5 秒和返回直播时共同移动。
- [ ] 运行前端 focused tests、Node syntax check；现有 subtitle scheduler/window/fullscreen/player tests 保持绿色。不要修改 `package.json` 聚合脚本，留给 Ticket 03。
