# 02 — 实时弹幕后端、对齐与翻译

**What to build:** 一次完成 `companion/` 范围内的完整直播消息能力：原子 Capture Media Cursor、LiveMessage model/store/pipeline、YouTube yt-dlp live_chat、Bilibili WebSocket、会话级认证租约、独立弹幕翻译 worker、API/status、用量和完整生命周期。所有失败必须与媒体播放和语音字幕隔离。

**Primary ownership:**
- `prototype/hls-companion/companion/core.py`
- `prototype/hls-companion/companion/server.py`
- `prototype/hls-companion/companion/ytdlp_ingest.py`（仅认证租约接线所需的最小改动）
- 新的 `companion/live_messages/`
- 相关 Python tests

**Blocked by:** None — 与 Ticket 01 按 Spec 中固定的 HTTP/JSON 合同并行开发。

**Status:** ready

## 采集媒体时钟与 Store

- [ ] 先写失败测试：`DelayedPlaylistPublisher` 在同一锁内维护/快照 `pdtEpoch`、`completedPrivateMediaSeconds`、`targetDuration`、`lastMediaAdvanceMonotonic`；Capture Media Cursor 单调不减，插值最多一个 target duration，停滞后不继续外推，PDT 未就绪返回 null。
- [ ] 实现 `LiveMessage`、有界 `LiveMessageStore` 和 `LiveMessagePipeline`：稳定 ID 去重、add/update 递增单调 seq、`afterSeq` 查询、revision 更新、按最新 mediaTime 保留 120 秒、5000 条硬上限。
- [ ] PDT 未就绪的消息进入有界 pending；时钟就绪后按各自接收 monotonic 间隔回算。不得使用 `time.time()` 猜造媒体时间；pending 溢出丢最旧并计数。
- [ ] `/api/status.mediaClock` 暴露 `captureWallTime`、`pdtEpoch`、`targetDuration` 和 availability，不含敏感字段。

## YouTube Live Chat

- [ ] 建立 `LiveMessageSource` 协议与 YouTube Source，复用 vendored yt-dlp，独立运行 `--skip-download --write-subs --sub-langs live_chat --sub-format json`，增量读取 JSONL/fragment，不等待直播结束。
- [ ] 把一次性 auth token 改为会话级认证租约：内存保存规范化 Cookie，媒体、chat 初连和 chat 重连分别创建/清理短命 Cookie 文件；Stop/启动失败释放租约。不得回显或记录 Cookie。
- [ ] 解析 text、stable ID、作者、badge、platformSentAt；`videoOffsetTimeMsec < 0` 作为连接前历史丢弃，以建连时 Capture Media Cursor 为 offset 0，非负 offset 保留批内间隔；字段缺失时使用 Source start timestamp 的 2 秒容差水位。
- [ ] 无 live_chat、聊天关闭、yt-dlp 退出或坏 JSON 行只更新 Source `unavailable|error`，跳过坏行；不能使媒体/字幕 start 失败。Stop 后无残留进程和临时文件。

## Bilibili 直播弹幕

- [ ] 用 aiohttp 实现 Bilibili Source：短号→真实 room ID、getConf/token、WSS auth、heartbeat、zlib/brotli 解压、多包、`DANMU_MSG` 和指数退避重连。
- [ ] 优先匿名连接，已有 Bilibili Cookie 可选；公开房间不得因缺少 `SESSDATA` 被阻止。断线期间不补造历史；非 text 命令只统计或忽略。
- [ ] 平台协议只存在 Source 内，server 不解析原始包；不要把它命名为 Provider Adapter。

## 弹幕翻译

- [ ] 翻译开关默认关闭。`POST /api/start.liveMessages {enabled, translate}` 设置会话初值；`POST /api/live-messages/settings {translate}` 可即时切换，只影响请求成功后的新消息，关闭时取消排队但未开始任务，不追溯旧消息。
- [ ] 从 `_prepare_subtitles()` 提取 server 级 Translation Provider/fallback factory，使语音字幕关闭时弹幕仍能使用持久化 Target Language 和 active Translation Provider。
- [ ] 新增独立串行 `LiveMessageTranslationWorker`，不共用 SubtitlePipeline 队列或 HISTORY；上限 30、deadline 3 秒。队满丢最旧未开始任务并将消息标记 skipped，原文必须保留。
- [ ] 缺 Key、语言不支持、超时或 fallback 全失败只标记 degraded/failed；不能改变 Source、媒体或字幕状态。纯 URL、纯 emoji/符号和重复刷屏可跳过翻译但不删除原文。
- [ ] 完成后以同一 message ID revision 回填译文，不改变 mediaTime/顺序。建立 server 级会话用量聚合：subtitle/liveMessage 分项和总计，同一个 TranslationResult usage 只能消费一次，缺 usage/price 保持 unavailable。

## API、直播范围与生命周期

- [ ] 新增 `GET /api/live-messages?afterSeq=N`、`POST /api/live-messages/settings` 和 `/api/status.liveMessages`；响应 `Cache-Control: no-store`，不含 Cookie、WSS token、原始包或敏感 URL。
- [ ] `/api/probe`/`start` 对 upcoming/post-live 明确给出 Live Message “仅支持正在直播”，且不能意外改变媒体已有行为。
- [ ] start B 先完整停止 A；每场创建 generation、空 CueStore、空 LiveMessageStore 和认证租约。媒体启动失败时终止已部分启动 Source/worker并回 clean idle；Source 后续失败不回滚成功媒体。
- [ ] Stop/application cleanup 共用幂等路径：停 Source/翻译、清 Message Store，停/替换 CueStore，释放租约；旧 generation 的迟到 callback 无效；重复 Stop 安全。

## Verification

- [ ] 用 fake yt-dlp JSONL、fake Bilibili HTTP/WSS 和 fake Translation Provider 覆盖：时钟、store、连接水位、auth lease/reconnect、压缩/多包/坏包、无聊天、translation on/off、字幕关闭时翻译、queue overflow、fallback、usage、部分启动 rollback、切流和重复 Stop。
- [ ] 运行 Python focused tests、compile 和相关 LSP diagnostics；现有 core/server/provider/subtitle/ingest tests 保持绿色。不要修改 `package.json` 聚合脚本或前端文件，留给 Ticket 03。
