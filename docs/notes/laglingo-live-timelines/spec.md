# LagLingo 实时字幕时间轴、直播弹幕与画面时钟 Spec

**Status:** ready-for-agent  
**Scope:** 仅正在进行的 YouTube / Bilibili 直播；不处理普通视频、直播回放或开播前历史消息

## 1. Problem Statement

LagLingo 已经把直播媒体发布为带 `EXT-X-PROGRAM-DATE-TIME` 的本地延迟 HLS，并用同一媒体时间轴调度画面字幕；但当前播放器仍有三个断裂：

1. 字幕只作为画面浮层出现，没有可回看的双语时间列表。
2. YouTube Live Chat 和 Bilibili 直播弹幕没有进入 LagLingo，更没有补偿 LagLingo 自身的观看延迟。
3. 浏览器原生视频进度条从媒体秒数 `0` 开始，用户看到的是 `00:23`，而不是当前画面对应的 `21:30:30`。

如果字幕、弹幕和进度条分别使用“收到消息的系统时间”“HTMLVideoElement.currentTime”和“ASR PCM 时间”，它们会持续产生不同偏差。第一版必须先收敛为一条时间合同，再增加列表和翻译能力。

## 2. MVP Outcome

在宽屏工作台中，视频位于中央：左侧是随当前画面滚动的双语字幕时间轴，右侧是随当前画面滚动的直播弹幕表。中央播放器使用 LagLingo 自己的控制条，以本机时区的 `HH:mm:ss` 显示当前画面时间和可回看窗口两端时间。

字幕 cue、直播消息和播放器都只认同一个 **媒体墙钟（Media Wall Clock）**：当前播放头由 hls.js `playingDate` 映射为 epoch seconds；服务端产生的字幕和弹幕事件也映射到同一条 PDT 媒体时间轴。事件可以提前获取和翻译，但只有播放头到达它的媒体时间后，才进入用户可见列表。

弹幕翻译是独立开关，默认关闭；开启后复用当前 active Translation Provider，并翻译到字幕设置里的 Target Language。翻译拥堵或失败只能让该条弹幕退化为原文，不能延迟、丢失弹幕原文，更不能影响媒体播放和字幕管线。

## 3. Canonical Terms

- **Media Wall Clock / 媒体墙钟**：由本地 HLS `PROGRAM-DATE-TIME` 定义、以 epoch seconds 表示的连续媒体时间轴。UI 按本机时区格式化为 `HH:mm:ss`。
- **Playback Wall Time / 播放头时间**：当前正在显示的视频帧在 Media Wall Clock 上的时间。优先来自 `hls.playingDate`，回退到当前 fragment 的 `programDateTime + fragment offset`。
- **Capture Media Cursor / 采集媒体游标**：Companion 当前已采集到的媒体位置在 Media Wall Clock 上的估计值，用于给刚收到的直播消息打媒体时间戳。
- **Subtitle Cue / 字幕条目**：ASR 产生的一段源文及其可选译文，具有 `tStart`、`tEnd` 和状态。
- **Live Message / 直播消息**：YouTube Live Chat 或 Bilibili `DANMU_MSG` 归一化后的只读事件。
- **Live Message Source / 直播消息源**：连接一个直播平台并产出原始 Live Message 的平台协议边界；它不是模型 Provider Adapter。
- **Follow Mode / 跟随模式**：列表自动把当前字幕或最新到达播放头的弹幕保持在可视区域；用户手动滚动后暂停，点击“回到当前”恢复。

“真实时间”在本 Spec 中指 **LagLingo 媒体墙钟的本机时区显示**，不是主播演播室时钟，也不承诺等于平台页面内部未公开的源时间。它能够保证 LagLingo 自己的画面、字幕和弹幕使用同一把钟。

## 4. Timing Contract

### 4.1 单一时钟

前端不得再直接把 `video.currentTime` 当作用户时间。新增一个纯逻辑 `MediaClock`：

```text
playingWallTime()
  = hls.playingDate
  fallback = fragment.programDateTime + (video.currentTime - fragment.start)

wallTimeForMediaPosition(position)
  = current Playback Wall Time + (position - video.currentTime)
```

服务端 `DelayedPlaylistPublisher` 暴露一个**原子、线程安全**的采集快照；同一把锁内读取/更新 `pdtEpoch`、`completedPrivateMediaSeconds`、`targetDuration` 和 `lastMediaAdvanceMonotonic`：

```text
Capture Media Cursor
  = pdtEpoch
  + completedPrivateMediaSeconds
  + bounded interpolation since last private-media advance
```

游标在一次会话中必须单调不减。插值最多为一个 `targetDuration`，只用于消除播放列表刷新形成的整秒阶梯；封装停滞后不再继续外推，多个分片在一次刷新中出现时允许媒体游标一次跳进但不回退。PDT 未就绪时，直播消息先进入有界 pending 队列，时钟就绪后按各自接收 monotonic 时间回算，不使用 `time.time()` 猜造媒体时间。

### 4.2 字幕对齐

- 继续使用现有 `Cue.tStart` / `Cue.tEnd`，不建立第二份服务端字幕时间。
- cue 只有 `state ∈ {done, failed}` 且播放头到达后才公开；`src/translating` 不得因为列表存在而提前泄露。
- 现有 `subtitlePrefs.offset` 是用户对字幕事件时间的校准：列表、浮层、高亮、行时间和点击 seek 都使用 `scheduledTime = cueTime - offset`；服务端 cue epoch 保持不变。这样两个字幕视图不会因偏移设置互相矛盾。
- 当前行按校准后的 cue span 高亮。翻译失败的 `failed` cue 显示原文并标记“仅原文”。
- 画面字幕浮层继续使用现有调度器；本工作不得重新定义其排队/驻留语义。列表与浮层共享 cue 数据、offset 和 MediaClock，但各自拥有展示策略。

### 4.3 弹幕对齐

消息到达 Live Message Source 时记录 `receivedMonotonic`，随后通过 Capture Media Cursor 映射为 `mediaTime`。右侧列表只在：

```text
message.mediaTime <= Playback Wall Time + 0.25s
```

时公开该消息。这样补偿的是 **LagLingo 自身的本地延迟**。观众在 YouTube/Bilibili 原生播放器上看到画面后再打字所产生的平台/人为反应延迟无法统一消除，不应伪装成逐帧语义对齐。

YouTube 批量返回消息时应保留批内的源时间间隔；Bilibili 单条实时消息优先使用接收 monotonic 映射。平台时间戳只作为诊断字段保留，不能直接与本地 PDT 混算。

### 4.4 精度目标

- 播放头时间标签：每 250ms 刷新，显示到秒。
- 字幕列表当前行与画面：沿用字幕链路验收，目视误差 `< 1.5s`。
- 弹幕进入列表相对 Capture Media Cursor：p95 误差 `< 1.5s`；不承诺弹幕文字与其所评论画面的语义反应延迟。
- 所有 epoch 传输使用浮点秒；格式化只发生在浏览器，统一使用本机时区和 24 小时制。

## 5. Normalized Data Contracts

### 5.1 Subtitle API

保留 `GET /api/subtitles?afterSeq=N` 和现有 Cue 模型。为列表增加的字段只能是向后兼容字段；第一版不复制 Store。

```json
{
  "id": 41,
  "seq": 52,
  "tStart": 1787996410.3,
  "tEnd": 1787996414.1,
  "src": "源文",
  "zh": "目标语言译文",
  "state": "done",
  "lang": "ja",
  "timingSource": "asr"
}
```

### 5.2 Live Message

```json
{
  "id": "youtube:ChwK...",
  "seq": 104,
  "platform": "youtube",
  "mediaTime": 1787996414.42,
  "receivedAt": 1787996429.18,
  "platformSentAt": 1787996428.91,
  "author": { "id": "...", "name": "viewer", "badges": ["member"] },
  "kind": "text",
  "text": "原始聊天文本",
  "translation": "目标语言文本",
  "translationState": "done",
  "revision": 2
}
```

MVP 对外展示和翻译只处理 `kind="text"`。Super Chat、礼物、入场、点赞、系统通知可以在 Source 统计中计数，但不进入右侧正文列表；不得因此阻塞后续 text 消息。

`LiveMessageStore` 使用与 `CueStore` 相同的单调 `seq`/revision 轮询语义，按最新 `mediaTime` 保留最近 120 秒，另设 `max_messages=5000` 硬上限。

## 6. Platform Sources

### 6.1 Shared interface

新增 `companion/live_messages/`，平台差异必须停在 Live Message Source 内：

```python
class LiveMessageSource(Protocol):
    async def run(self, emit: Callable[[RawLiveMessage], None]) -> None: ...
    async def aclose(self) -> None: ...
    def status(self) -> dict[str, object]: ...
```

`LiveMessagePipeline` 拥有时间映射、Store、翻译队列、去重、统计和生命周期；Source 只拥有平台连接、协议解析和原始消息归一化。弹幕是播放 sidecar：启动、断线或翻译失败都不得让 `/api/start` 失败，也不得给音视频泵施加背压。

### 6.2 YouTube

MVP 复用项目已经 vendored 的 yt-dlp，启动独立 `live_chat` sidecar，而不是引入 Google OAuth：

- 使用本次 `/api/probe` 的同一 URL 和**会话级认证租约**。一次性 auth token 只消费一次，但租约在内存里保留规范化 Cookie，允许媒体腿、聊天初连和聊天重连分别生成短命临时文件；每个文件独立清理，会话 Stop 时释放内存。`--cookies-from-browser` fallback 也由租约按子进程重新生成参数。
- 以 `--skip-download --write-subs --sub-langs live_chat --sub-format json` 获取当前直播聊天。
- Source 增量读取 JSON Lines/fragment 输出，不等待文件完成；解析 text renderer、稳定消息 ID、作者、badge、源发布时间。
- 当前直播的 `videoOffsetTimeMsec < 0` 视为连接前历史并丢弃；以 Source 建连时的 Capture Media Cursor 为 offset 0，非负 offset 用于保留同批和后续消息间隔。若该字段缺失，再用 `platformSentAt >= sourceStartedAt - 2s` 的水位过滤。
- yt-dlp 没有 `live_chat`、聊天被关闭或子进程退出时，只把 `liveMessages.state` 标为 `unavailable|error`；视频和字幕照常运行。
- 不接 Google Live Streaming API，不新增 OAuth/Cloud Console/配额配置。

实现时不得与媒体 ingest 共享 stdout、进程监督或临时输出文件；只允许共享 URL、会话认证租约和清理策略。

### 6.3 Bilibili

MVP 使用网页直播弹幕 WebSocket：

1. 房间 URL 解析短号/真实 `room_id`。
2. 获取弹幕服务器和 token。
3. WSS 鉴权、心跳、zlib/brotli 包解压。
4. 只将 `DANMU_MSG` 归一化为 `kind="text"`。
5. 指数退避重连；断线期间不补造历史弹幕。

优先匿名连接；已有 Bilibili Cookie 可用于受限房间，但 `SESSDATA` 缺失不应阻止公开直播弹幕。网页协议不是稳定官方合同，因此端点、包头和命令解析必须集中在 `BilibiliLiveMessageSource`，不能散落进 server 或 UI。

## 7. Translation Contract

- UI 在右侧面板头部提供独立开关：`翻译弹幕`，默认 `false`，使用 `localStorage` 保存偏好。
- 目标语言直接复用 Provider 配置中持久化的 Subtitle Target Language；因此即使语音字幕关闭，弹幕翻译仍有服务端权威目标语言。不新增第二个目标语言选择器。
- 开关可在直播中即时切换。新增 `POST /api/live-messages/settings {"translate": boolean}`；开关仅控制请求成功后 **新收到的消息** 是否入翻译队列。关闭后取消排队但尚未开始的任务，不补翻历史；重新开启只翻译之后的消息。
- 使用当前 active Translation Provider 和既有 fallback 顺序，但由独立 `LiveMessageTranslationWorker` 串行执行；不能复用字幕 worker 队列，避免高流量聊天拖慢字幕。翻译 Provider 的构建必须从 server 级共享 factory 提取，使语音字幕关闭时也能独立创建翻译链。
- 默认队列上限 30：满时丢弃最旧的未翻译任务，该条仍显示原文并标记 `skipped`。
- 单条 deadline 默认 3 秒；失败/超时显示原文，状态为 `failed`，不重试旧消息。
- 不携带字幕的滚动 HISTORY，避免把聊天噪声污染语音字幕上下文。MVP 请求只含直播标题/频道、源语言自动、目标语言和当前消息。
- 仅翻译有文本的 `kind="text"`；URL、纯 emoji、纯符号和明显重复刷屏可由纯函数过滤为 `skipped`。
- 用量和费用按实际 Translation Provider 进入 server 级会话用量聚合器：分别保存 `subtitle` 与 `liveMessage` 分项，再由总计相加。SubtitlePipeline 现有累计值迁入同一聚合器或作为单一快照合并；同一个 `TranslationResult.usage` 只能被消费一次。
- active Provider 缺 Key、语言不受支持或 fallback 全失败时，弹幕翻译进入 `degraded` 并保留原文；不得让 live-message Source 或媒体 state 变成 error。

## 8. API and Lifecycle

### 8.1 Start payload

`POST /api/start` 增加：

```json
{
  "liveMessages": {
    "enabled": true,
    "translate": false
  }
}
```

`enabled` MVP 默认 `true`；`translate` 由浏览器偏好填入。平台由已经校验的 URL 决定，客户端不得提交任意 Source kind。`/api/probe` 或 `/api/start` 若确认 URL 不是 `is_live=true`（包括 upcoming/post-live），必须拒绝 live-message 启动并给出“仅支持正在直播”的明确错误；媒体现有对非直播的策略不得被静默改变。

### 8.2 Polling

新增：

```text
GET /api/live-messages?afterSeq=N
```

响应：

```json
{
  "messages": [],
  "maxSeq": 104,
  "stats": {
    "state": "running",
    "platform": "youtube",
    "received": 213,
    "textMessages": 180,
    "translated": 42,
    "translationFailed": 1,
    "translationSkipped": 3,
    "reconnects": 0,
    "pendingClock": 0,
    "lastError": null
  }
}
```

轮询频率 500ms，与字幕相同但使用独立请求和游标。第一版不增加浏览器 WebSocket/SSE。

直播中设置：

```text
POST /api/live-messages/settings
{ "translate": true }
```

响应返回生效后的 `translate` 和 live-message stats；无 active session 时返回明确错误，不暗中修改下一场的服务端状态。下一场默认值仍由浏览器 localStorage 随 `/api/start` 传入。

### 8.3 Status

`GET /api/status` 增加：

- `mediaClock.captureWallTime`
- `mediaClock.pdtEpoch`
- `mediaClock.targetDuration`
- `liveMessages` 上述统计

敏感 token、Cookie、WebSocket 鉴权 key、完整平台原始包不得进入响应或日志。

### 8.4 Lifecycle

- `/api/start` 开始时先完整停止旧会话，并创建新的 session generation、空 Subtitle Store、空 LiveMessage Store 和会话认证租约。任何新媒体启动失败都必须停止已部分启动的 Source/worker、释放租约并回到 clean idle。
- 媒体仍是主成功合同；媒体启动成功后再启动直播消息 sidecar。Source 部分启动或重连失败只记 sidecar 状态，不回滚正在播放的媒体/字幕。
- `/api/stop`：先停止 Source/翻译 worker，再清空 LiveMessage Store；停止并替换 Subtitle Store；随后沿用现有字幕/媒体清理。旧 generation 的迟到 callback 必须被忽略。
- application cleanup 与显式 Stop 使用同一幂等清理路径；切换直播后所有服务端 Store、前端 Map 和轮询游标从零开始，Provider/语言/浏览器翻译开关偏好保留。
- 不得遗留 yt-dlp live_chat、WebSocket、翻译任务、Cookie 临时文件或内存认证租约。

## 9. Player UX and Visual Design

### 9.1 Desktop topology

遵循当前纸张底色、山茶红/青色、细线、日文字体和 mono 数据标签，不引入新的视觉语言。顶层工作台改为：

```text
┌──────────── 300px 字幕时间轴 ────────────┐ ┌──── 16:9 视频舞台 ────┐ ┌──────────── 300px 直播弹幕 ────────────┐
│ SUBTITLES / 当前时间                     │ │                       │ │ LIVE CHAT / 平台状态 / 翻译开关          │
│ 21:30:24 原文 / 译文                     │ │   LagLingo overlay     │ │ 21:30:26 用户名  原文                    │
│ 21:30:29 当前高亮                         │ │                       │ │          译文（若开启且成功）             │
└──────────────────────────────────────────┘ └── 自定义墙钟控制条 ───┘ └──────────────────────────────────────────┘
```

- 主工作台从当前 1560px 内容列中横向 breakout：`width: min(1920px, calc(100vw - 40px))`；下方 Source/Subtitles/Telemetry decks 仍限制在当前 1560px 内容宽度，避免整个设置区无意义拉伸。
- 三栏使用 `grid-template-columns: minmax(240px, 280px) minmax(800px, 1fr) minmax(240px, 280px)`，目标是在常见 1920px 宽屏上保留接近当前尺寸的中央视频，而不是拿侧栏挤小视频。
- 保持中央视频为唯一视觉主角；侧栏背景继续用 `var(--card)`、1px `var(--line)`，当前项使用低饱和 `camellia-soft`/`rose-soft` 底色，不做大色块。
- 现有 `now-panel` 从视频右边移出主舞台，放到播放器下方状态区或后续 telemetry；不能与新弹幕栏抢占右侧。
- 行内时间使用 `font-mono`、tabular numerals；正文使用现有 sans，源文可用现有 JP font。
- 不加头像，避免密度、网络请求和视觉噪声；作者 badge 仅做小号文本标记。

### 9.2 Subtitle timeline behavior

- 每行显示校准后的 `HH:mm:ss`、原文、译文；译文在上或更高权重，原文次级，跟随当前 overlay 的语言方向和显示模式。
- 只有 `done/failed` 且播放头已经到达的 cue 才渲染。当前播放 span 内的 cue 使用山茶红标记/浅底；已播放行正常，未来 cue 不渲染。
- 新 cue 到达且 Follow Mode 开启时滚到当前行；动画 150–200ms，并尊重 `prefers-reduced-motion`。
- 用户滚轮、触摸或键盘滚动离开底部后暂停自动跟随，显示 `回到当前`；不得在用户阅读旧内容时抢滚动。
- 点击已播放字幕行：如果该时间仍在当前 `video.seekable` 范围内，则 seek 到对应媒体位置；否则按钮不可用并说明“已超出本地回看窗口”。这是已有 30 秒 HLS 窗口内的本地跳转，不扩展存储。

### 9.3 Live-message timeline behavior

- 每行显示 `HH:mm:ss`、作者、原文；翻译开启且完成时在下一行显示译文。
- 时间取 `mediaTime`，不是浏览器收到消息的时间。
- 消息只在播放头到达时追加，所以“当前”天然位于列表底部；Follow Mode 与字幕栏相同。
- 翻译中不显示占位闪烁；完成后原地 revision 更新。失败/跳过保留原文，不弹 toast。
- 高流量时仍显示所有保留窗口内的 text 消息，但 DOM 只保留可见窗口附近或硬上限 500 行；Store 的 5000 条上限不等于全部挂 DOM。

### 9.4 Real-time player controls

隐藏原生 `<video controls>`，实现最小 LagLingo 控制条：

- 播放/暂停
- 静音与音量
- 当前画面时间 `HH:mm:ss`
- 30 秒本地 seekable window 滑轨
- 左端/右端真实时间 `HH:mm:ss`
- `回到直播` 按钮（seek 到当前公开边缘后按现有 live-sync 安全距离定位）
- 全屏按钮，继续让 `.player-stage` 包含字幕浮层

滑轨数值仍使用媒体秒 `seekable.start/end`，但所有标签通过 MediaClock 转为真实墙钟。用户拖动时预览目标墙钟；释放后才 seek。直播窗口移动时，thumb 按实际 currentTime 更新，不把右端伪装成平台零延迟 live edge。

### 9.5 Responsive behavior

- `>= 1500px`：横向 breakout 的左右双栏 + 中央视频。
- `900–1499px`：中央视频在上，字幕/弹幕在下方两列；不把视频压到不可观看。
- `< 900px`：字幕/弹幕使用两个 tabs，共享一个列表区；视频仍在上。MVP 不要求同时看到三列。
- 全屏：只显示视频、字幕浮层和自定义控制条；左右列表不进入全屏。

## 10. Key States

每个侧栏必须覆盖：

- `idle`：尚未启动直播，说明启动后自动出现。
- `connecting`：使用 3–5 行固定高度 skeleton，避免布局跳动。
- `running-empty`：连接正常但当前尚无条目。
- `running`：跟随当前画面。
- `follow-paused`：用户正在查看旧内容，可一键回到当前。
- `unavailable`：平台关闭聊天/弹幕或无 live_chat；视频和字幕仍正常。
- `reconnecting`：保留已有列表，头部显示重连状态，不清空。
- `error`：仅侧栏内联错误，不提升整个播放器为 media error。
- `translation-disabled|running|degraded`：翻译开关状态与失败计数可见，但单条失败不打断浏览。

## 11. Testing Decisions

采用现有稳定 seams，所有 ticket 使用红 → 绿纵向切片：

1. **纯时间逻辑**：MediaClock、Capture Media Cursor、epoch↔media position、时区格式化和 seekable-window 映射。
2. **Source fake protocol**：YouTube JSONL 增量解析；Bilibili fake HTTP/WSS 的 auth、heartbeat、压缩包、DANMU_MSG 和重连。
3. **Loopback HTTP**：start/stop、`/api/live-messages` seq/revision、status、无聊天降级、翻译开关。
4. **Translation Provider/Pipeline**：独立队列、deadline、skip、usage attribution、失败隔离。
5. **Browser-observable behavior**：三栏 DOM、Follow Mode、当前行、未来事件不可见、墙钟控制条、seek、回到直播、响应式 tabs。
6. **真实 Chromium smoke**：hls.js 当前画面时间与公开 playlist PDT 相符；拖动 5 秒后标签同步倒退；恢复 live 后更新正确。
7. **真实平台 smoke**：YouTube 与 Bilibili 各至少 10 分钟；记录消息采集、重连、p95 映射误差、进程清理和播放稳定性。

## 12. Acceptance Criteria

- 宽屏下明确形成“字幕列表 / 视频 / 弹幕列表”三栏，视觉沿用现有 LagLingo 风格。
- 当前画面的时间、滑轨两端、字幕行和弹幕行都以本机时区 `HH:mm:ss` 显示，并来自同一 PDT 媒体墙钟；字幕行额外统一应用用户 subtitle offset。
- 字幕和弹幕都不会在对应画面播放前出现在列表中；`src/translating` 字幕不会因列表存在而提前泄露。
- 手动回看当前 HLS 窗口时，两个列表的当前高亮随画面倒退/前进；返回直播后恢复跟随。
- YouTube 当前直播 Live Chat 与 Bilibili 当前直播 `DANMU_MSG` 都能进入统一 API；任一 Source 失败不影响播放和字幕。
- 弹幕翻译默认关闭；直播中可即时开关，开启后即使语音字幕关闭也使用持久化 Target Language/active Translation Provider，新消息可原地出现译文；失败时保留原文。
- 高消息量不会阻塞媒体、字幕或浏览器主线程；队列、Store 和 DOM 都有硬上限。
- Stop 后没有残留 live_chat yt-dlp、Bilibili WSS 或翻译任务，且可以立即启动另一直播。
- 全量 `npm run ci` 保持绿色，并完成 YouTube/Bilibili 各一轮真实直播验收记录。

## 13. Out of Scope

- 普通视频、直播回放、开播前聊天历史、直播结束后的弹幕归档与导出。
- 发送、点赞、回复、屏蔽、管理或登录身份互动。
- 把弹幕绘制为横向飞过视频画面的 overlay；MVP 只有右侧滚动列表。
- 翻译 Super Chat、礼物、系统事件、用户名、badge 或 emoji。
- 让弹幕的文字语义自动匹配它评论的具体画面；MVP 只补偿 LagLingo 本地播放延迟。
- 无限 DVR、扩大现有 30 秒公开 HLS 窗口或持久化完整直播。
- 替换字幕 Provider/语言领域模型、重写字幕 overlay 调度器，或引入 Google OAuth。
- 移动端三栏同时显示。

## 14. Implementation Order

按一个 Ticket 对应一个 subagent，压缩为三个纵向工作包：

```text
01 Realtime Workbench Frontend ─┐
                                ├──> 03 Integration + Live Acceptance
02 Live Messages Backend ───────┘
```

- **Ticket 01** 完整拥有 `web-player/`：MediaClock、真实时间控制条、左字幕、右弹幕、翻译开关、三栏和响应式；使用 Spec 固定的 API fixtures，因此可与后端并行。
- **Ticket 02** 完整拥有 `companion/`：Capture Media Cursor、YouTube/Bilibili Source、Store/API、认证、弹幕翻译、用量和生命周期；不修改前端或 `package.json`。
- **Ticket 03** 在前两个完成后负责契约联调、冲突修复、测试入口、性能、文档和真实平台验收；不得引入新功能或重做前两票架构。

这种边界优先按文件所有权减少并行 subagent 冲突，而不是把每个小模块单独开票。
