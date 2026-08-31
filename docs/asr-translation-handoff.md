# Handoff：下一阶段 —— 实时字幕管线（百炼 ASR → 翻译 → 播放器内嵌中文字幕）

> 日期：2026-08-30。承接 `bailian_live_translation_plugin_handoff.md`（产品愿景与供应商调研，2617 行，**先读它**）与 `docs/live-delay-solution.md`（延迟管线方案）。
> 本文只做三件事：①记录延迟管线**已完成**的最终状态；②给出字幕管线的架构决策（含关键陷阱）；③列出行动清单。
>
> **⚠️ 落地请以 `docs/subtitle-pipeline-implementation-plan.md` 为准**（2026-08-30 新增可执行规格）。该文对本文有 3 处修正：字幕时间锚点改为句尾、ASR 默认模型定为 `qwen3-asr-flash-realtime`、播放器墙钟映射改用 hls.js 内建的 `hls.playingDate`。

---

## 1. 现状：延迟管线已完成并实测通过

**端到端数据流（当前生产路径）：**

```
YouTube Live
  ├─ yt-dlp 进程 A（视频腿, -f 312）── stdout(MPEG-TS) ──► _TcpPump:PORT_V ─┐
  └─ yt-dlp 进程 B（音频腿, -f 234）── stdout(MPEG-TS) ──► _TcpPump:PORT_A ─┤
                                                                            ▼
                              封装 FFmpeg（-i tcp://... ×2, -c copy, 零转码）
                                ├─ setts 时间戳连续性过滤（bsf, 见 §1.3）
                                └─ HLS fMP4, 1 秒分片（split_by_time）→ private/
                                                                            ▼
                              DelayedPlaylistPublisher（持有 ~3s, 30s 窗口）→ public/
                                                                            ▼
                              hls.js 播放器（落后公开边缘 3 个分片 ≈ 3s）
```

**用户看到的画面落后直播边缘 ≈ 5–7 秒**（③分片 1s + ④发布 ~3s + ⑤浏览器 ~3s），全程流拷贝、零转码，磁盘占用恒定 ~136MB。

### 1.1 关键文件

| 文件 | 职责 |
|---|---|
| `prototype/hls-companion/companion/ytdlp_ingest.py` | **双腿采集**：每个格式一个 yt-dlp 进程，stdout TS 经 `_TcpPump`（localhost TCP）送给封装 FFmpeg |
| `prototype/hls-companion/companion/core.py` | `build_ffmpeg_command()`（含 setts bsf、tcp 输入分支）、`DelayedPlaylistPublisher`、`LiveSession` |
| `prototype/hls-companion/companion/server.py` | aiohttp API：`/api/probe|start|stop|status`、`/hls/{name}` |
| `prototype/hls-companion/web-player/` | `index.html` + `player.js`（hls.js，`liveSyncDurationCount:3`） |

### 1.2 为什么是两个 yt-dlp 进程（不要合并回去）

单进程 `-f 312+234` 时 yt-dlp 内部用一个 ffmpeg 串行读两个 HLS 播放列表，在低延迟直播（1s 分片、短窗口）上**慢性跟不上**，日志持续 `skipping N segments ahead, expired from playlists`，把 3–5 秒的内容空洞打进媒体流。实测：合并模式 8 分钟 23 次断裂；拆双腿后 3+ 分钟零跳过（仅启动时 1 次无害跳过）。`--downloader-args "ffmpeg_i:-http_multiple 1"` 已验证**无效**。

### 1.3 setts 时间戳连续性过滤（不要移除）

`-c copy` 会把源 TS 的 PTS 断裂（跳片空洞 +5s、重抓取回退 -24s、长直播重基 ±47000s）原样写进 fMP4 `tfdt`，Chrome MSE 时间轴瞬移 → hls.js 死循环"饿死→强拖"。`core.py` 顶部 `_VIDEO_SETTS/_AUDIO_SETTS` 用 `setts` bsf 重建连续时间线：正常增量保留，断裂坍缩为一帧时长。真实抓包回放验证：输出 tfdt 跳跃 **139 → 0**。代价：若采集层仍有内容空洞，音画会按空洞大小漂移——所以双腿采集（§1.2）是前提，setts 是兜底。

### 1.4 已知的可接受瑕疵

- 冷启动最多 ~4s 黑屏（1/5 分片以 IDR 起始，`docs/live-delay-solution.md` §5"R1 的真实代价"；如明显，实施该文 R2）。
- `server.py` `/api/status` 的 `sourceDelaySeconds` 硬编码 0.0，UI"估计总延迟"不含源侧延迟。
- 测试基建：`tests/test_control_ipc.py` 在服务器进程运行时因命名管道冲突失败（环境问题，非代码问题）。

---

## 2. 下一阶段目标

> **采集延迟已就绪。现在实现：音频 → 百炼实时 ASR → 翻译模型 → 中文字幕实时嵌入 LagLingo 播放界面。**

产品原则（来自 bailian handoff §0，仍然成立）：**不追求最低字幕延迟，而是把管线已有的 5–7 秒观看延迟当作 ASR + 翻译的计算预算，让画面与中文字幕在延迟后的时间轴上同步。**

---

## 3. 架构决策（三个关键选择）

### 决策 1：音频在【音频腿 TCP 泵】处 tee 出一路 —— 不要从分片目录取

候选对比：

| 取音点 | 字幕可用的计算预算 | 评价 |
|---|---|---|
| **A. `_TcpPump` tee（音频腿）** | **全部 5–7s** | ✅ 推荐。音频到达 ingest 即分出一路，ASR/翻译吃满整条管线的人为延迟 |
| B. `private/` 分片文件 | ~4–6s | 晚 1 个分片周期，还要自己解 fMP4，无收益 |
| C. `public/` 分片文件 | ~0–2s | ❌ 人为延迟已被消耗殆尽，字幕必然迟到 |

具体做法：给 `_TcpPump`（`ytdlp_ingest.py`）加一个可选的字节订阅者（`tee: Callable[[bytes], None]` 或 `queue.Queue`），仅音频腿挂接。订阅者把 TS 字节喂给字幕 worker（新模块，见 §4）。泵已有背压语义，tee 必须**非阻塞**（有界队列，满了丢最旧的并计数——字幕可以缺，播放不能卡）。

### 决策 2：字幕与画面对齐用【墙钟 / PROGRAM-DATE-TIME】—— 不要碰 PTS/tfdt

这是本阶段**最容易做砸**的地方。媒体时间线里有太多陷阱：双腿各自的首包 PTS 不同、setts 在断裂时坍缩、tfdt 与浏览器 `currentTime` 的关系依赖 hls.js 细节。绕开全部：

- 公开播放列表每个分片带 `#EXT-X-PROGRAM-DATE-TIME`（封装 FFmpeg 写入的墙钟）。
- 播放器已知 `edge`（hls.js `LEVEL_UPDATED`）和 `video.currentTime`，从播放列表解析 edge 处分片的 PDT，即可算出 **currentTime 对应的墙钟 ≈ PDT_edge − (edge − currentTime)**。
- 字幕 worker 给每条 cue 打墙钟时间戳（音频进入泵的时刻，按字节速率换算更精）。
- 播放器按"currentTime 的墙钟"查 cue 队列显示。误差 <1s，对字幕足够；且**天然免疫一切时间戳断裂**。

精化路径（可选，后续再做）：worker 解析音频 TS 的 PTS，发布器在 `/api/status` 提供"音频 PTS ↔ 输出 tfdt"的会话偏移，对齐精度到帧。不要在第一版做。

### 决策 3：字幕渲染用【overlay div】—— 不要走 MSE/WebVTT 注入

`index.html` 的 `.player-stage` 里叠一个绝对定位字幕层，`player.js` 每 ~250ms 用 `video.currentTime` 查 cue。改动最小、零播放风险、样式自由（双语、字号）。WebVTT `<track>` 要求 cue 提前就绪且与 seek 语义耦合，留作后续打磨项。

---

## 4. 字幕管线模块设计

新模块 `prototype/hls-companion/companion/subtitle_pipeline.py`（生命周期跟随 `LiveSession`：`handle_start` 启动、`handle_stop` 停止）：

```
音频腿 TS 字节（tee）
  └─ ffmpeg 子进程：-f mpegts -i pipe:0 -vn -ac 1 -ar 16000 -f s16le pipe:1   （唯一新增的转码，仅音频重采样，CPU 可忽略）
       └─ PCM 16k mono 流
            └─ DashScope 实时 ASR WebSocket（选型见 §5）
                 ├─ interim 结果 → 占位字幕（灰色/可抖动）
                 └─ final 句子 → 翻译队列（带最近 N 句上下文 + 术语表）
                      └─ 翻译 LLM（§5）→ 中文 cue 入队 {t_wall, src, zh}
                           └─ GET /api/subtitles?since=<wall> → 播放器 overlay
```

要点：

- **Cue 队列**：服务端保留最近 ~120s 的 cue（有界 deque），`server.py` 加 `GET /api/subtitles?since=` 轮询接口（250–500ms 轮询即可，不必 WebSocket，本地 127.0.0.1 开销可忽略；想省轮询再上 WS）。
- **DashScope API Key**：只能留在服务端（环境变量 / 本地配置文件），**绝不进 web-player 或 extension**。浏览器只跟 127.0.0.1 说话。
- **interim 字幕**第一版可以不做，先只显示 final+译文，体验已是"稳定字幕"；interim 原文是 P2 打磨。
- **无语音段**：ASR 不出句就没有 cue，overlay 自然消隐，不需要特殊处理。

## 5. 供应商选型（直接沿用 bailian handoff 的结论，不要重新调研）

详见 `bailian_live_translation_plugin_handoff.md` §4–§6，结论摘要：

- **ASR 默认**：`qwen3-asr-flash-realtime`（Realtime WebSocket，中日韩英；热词/上下文增强适合直播专有名词）；**成本基线**：`paraformer-realtime-v2`（0.00024 元/秒）；**对照/fallback**：`qwen3.5-livetranslate-flash-realtime`（端到端同传，不做主链路——我们要自己掌控上下文策略）。
- **翻译默认**：通用 Qwen LLM 高质量模式（带历史上下文 + 术语表，符合"用预算换质量"的产品定位）；**极速模式**：`qwen-mt-plus/flash`。
- 第一版默认链路：`qwen3-asr-flash-realtime` final 句 → Qwen 翻译 → 中文 cue。A/B 对比留到调优阶段。

## 6. 延迟预算核对（为什么字幕能"免费"同步）

| 事件 | 距直播边缘 |
|---|---|
| 音频到达 ingest（tee 点） | ~0s（②yt-dlp 取完整分片，结构性，见 live-delay-solution §2） |
| ASR final + 翻译完成 | ~+2–4s |
| **用户看到对应画面** | **~+5–7s** |
| 余量 | **+2–3s** ✅ |

预算不够时，用户可在 UI 的 `publishDelay` 下拉加大发布延迟（控件已存在），每 +1s 都是纯字幕预算。这正是当初设计"可调延迟"的意义——**不要再为压延迟而优化③④⑤，字幕体验依赖这段预算。**

## 7. 行动清单

| # | 动作 | 验收 |
|---|---|---|
| P0-1 | `_TcpPump` 加 tee 订阅（有界队列、丢旧计数） | 单测：订阅者收到与 ffmpeg 相同的字节流 |
| P0-2 | `subtitle_pipeline.py`：TS→PCM 16k 子进程 + DashScope ASR WebSocket 客户端（自动重连、句边界、墙钟打戳） | 真实直播 5 分钟：原文 cue 连续、无内存增长 |
| P0-3 | `/api/subtitles?since=` + 播放器 overlay（按 currentTime↔墙钟映射查 cue） | 浏览器实测：原文字幕与口型误差 <1.5s |
| P1-1 | 翻译队列（上下文窗口 + 术语表）→ 中文 cue | 同屏中日/中英双语对照目测 |
| P1-2 | 双语 overlay UI（可开关、字号可调） | 截图评审 |
| P2 | interim 占位字幕、WebVTT/seek 精化、ASR A/B、成本统计 | — |

**明确不做**（沿用 bailian handoff §1.2）：声纹分离、OCR、TTS 配音、移动端、DRM 平台。

## 8. 风险与注意事项

1. **tee 绝不能阻塞播放**：订阅队列满 → 丢旧 + 计数 + status 上报，不许抛异常进泵线程。
2. **ASR WebSocket 长会话保活**：百炼 realtime 连接有空闲超时与服务端轮换，worker 必须指数退避重连并在重连间隙丢弃 cue（不要补打时间戳造假）。
3. **断裂期间的字幕**：采集跳片/重抓时音频内容缺失，ASR 会出乱句——worker 监听 ingest 的 `log_tail`/`sourceError`，异常窗口内的 ASR 结果直接丢弃。
4. **多语言**：源语言由用户启动时选择（不要自动检测，直播场景检测不稳）；`isLive` 探测结果已在 `/api/probe` 返回。
5. **成本**：ASR 按秒计费，在 `/api/status` 暴露累计 ASR 秒数与估算费用，方便用户感知。
6. **合规**：音频只在本机与阿里云之间流转，与现有"Cookie 不出本机"口径一致；README 需要补一句数据流说明。
