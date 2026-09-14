# 03 — 契约集成、加固与真实直播验收

**What to build:** 合并 Ticket 01/02 后完成最后收口：解决前后端契约差异，接好测试入口，验证生命周期和性能，更新正式文档，并在 YouTube/Bilibili 当前直播上记录真实验收结果。本 Ticket 不新增功能或新架构。

**Blocked by:** 01, 02.

**Status:** ready

**Primary ownership:**
- `package.json`
- 正式 docs/README/测试记录
- 前后端集成冲突和最小修复
- 完整自动化与真实平台验收

## Contract integration

- [ ] 对照 Spec 验证 `/api/status.mediaClock`、`/api/live-messages`、`/api/live-messages/settings`、`POST /api/start.liveMessages` 的字段、状态和错误完全匹配前端；删除 fixture 与真实响应差异。
- [ ] 验证当前画面、滑轨两端、左侧当前 cue、右侧刚公开 message 使用同一 MediaClock；subtitle offset 在浮层和左侧列表中语义一致。
- [ ] 验证弹幕翻译开关的启动初值和运行中切换一致；语音字幕关闭时弹幕翻译仍可工作；Target Language/active Provider 无第二份状态。

## Lifecycle and performance

- [ ] 集成测试 start A → 收字幕/弹幕 → stop → 所有服务端 Store、前端 Map/游标、进程、WSS、worker、Cookie 文件和认证租约清空 → start B；旧 generation callback 无效，重复 Stop/application cleanup 幂等。
- [ ] 持续高消息 fixture 运行 10 分钟：Map/Store/DOM 有界、翻译队列有界、滚动不抖动、控制条不触发布局风暴；不得出现每消息 timer；媒体和字幕延迟不因弹幕洪峰恶化。
- [ ] Chromium smoke：回看约 5 秒时真实时间、字幕高亮和弹幕可见状态共同倒退；回到直播后共同恢复；检查三种响应式布局、全屏、键盘、200% zoom、RTL 和 reduced-motion。

## Real platform acceptance

- [ ] YouTube 当前直播至少 10 分钟：live_chat 收取、负 offset 历史过滤、消息按本地延迟公开、翻译即时开/关、聊天关闭/无轨状态、字幕关闭但弹幕翻译开启、Stop 后无残留 yt-dlp chat/Cookie 文件。
- [ ] Bilibili 当前直播至少 10 分钟：DANMU_MSG 收取、心跳、至少一次受控断线重连、消息按本地延迟公开、翻译即时开/关、字幕关闭但弹幕翻译开启、Stop 后 WSS 关闭；记录匿名/Cookie 结果。
- [ ] 验证 upcoming/post-live 不启动 Live Message Source，并显示“仅支持正在直播”；媒体的既有处理不被意外破坏。
- [ ] 记录 subtitle alignment、live-message mapping p50/p95、Source reconnects、translation failures/skips、DOM/Store 峰值、CPU/RSS、播放 stall/jump、残留资源。没有完成的真实验收不得标通过。

## Documentation and final verification

- [ ] 更新 `docs/architecture.md`、HLS Companion README、安全/第三方说明和测试记录：统一时钟、只读实时范围、无归档、yt-dlp live_chat、Bilibili 网页协议稳定性和 Cookie 生命周期。
- [ ] 将新 Python/Node suites 接入 `package.json`，运行 focused tests、`npm run test:hls-companion`、`npm run ci`、Python compile、Node syntax、LSP diagnostics 和 `git diff --check`。
- [ ] 只修复集成和验收中发现的本功能回归；不得顺手重构现有 Provider、字幕调度、媒体 ingest 或设计系统。
