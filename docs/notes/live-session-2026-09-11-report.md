# LagLingo Electron 直播会话取证报告（2026-09-11）

## 范围与限制

- 会话 URL：已从进程命令行确认（报告不重复写入完整 URL，避免把临时签名信息带入报告）。
- 采集窗口：`2026-09-11 19:01:12` 至 `2026-09-11 22:10:15`，约 **3 小时 09 分 03 秒**。
- 结束动作：已验证目标为 `laglingo-backend.exe`（PID 261996，父进程为 LagLingo Electron），用树终止结束 backend 及其 yt-dlp/ffmpeg 子进程；Electron 外壳仍在运行。
- 这版运行时没有把每条 ASR/翻译 cue 持久化到磁盘；HLS 只保留滚动窗口。因此无法从磁盘恢复 3 小时逐句字幕正文和每句 provider latency，只能恢复媒体分片、时间线、配置和进程拓扑。

## 已收集的可复核证据

### 媒体时间线

| 项目 | 结果 |
|---|---:|
| private 分片序号 | `10844..11153`（当前目录保留 310 个） |
| private 播放列表 | 250 × 1 秒，媒体序列 `10904..11153` |
| public 播放列表 | 180 × 1 秒，媒体序列 `10971..11150` |
| private 当前最后 PDT | `2026-09-11 22:07:05.706 +08:00` |
| public 当前最后 PDT | `2026-09-11 22:07:02.706 +08:00` |
| 最后分片文件写入时间 | `2026-09-11 22:10:15.475 +08:00`（private） |
| 写入时间 − PDT | **约 189.8 秒**（private），**约 193.0 秒**（public） |
| private 分片大小 | 平均约 459.9 KiB，范围约 105.4–858.3 KiB |
| public 分片大小 | 平均约 460.1 KiB，范围约 251.6–744.8 KiB |

结束后再次观察 3 秒，private `live.m3u8` 修改时间没有推进；backend、yt-dlp、ffmpeg 进程均已不存在。

### 运行配置（已脱敏）

- ASR：Soniox `stt-rt-v5`，endpoint detection，`maxEndpointDelayMs=700`。
- 翻译：本机兼容代理 `127.0.0.1:8045`，模型 `gemini-3.7-flash-low`，单请求超时 6 秒，4 个 translation workers。
- subtitle target delay：15 秒；播放器没有配置 60 秒目标延迟。
- 本次确实启用了独立视频 leg 与独立 ASR 音频 leg。

## 结论

这次首先能被证实的是：**LagLingo 本地 HLS 输出的媒体时间线落后于本机墙钟约 3 分钟**。这不等于播放过程中卡住了 3 分钟，也不单凭本地证据证明“字幕相对播放器画面落后了 3 分钟”。结束时最新分片已经写入磁盘，但它携带的 PDT 仍停留在约 190 秒之前；播放器和字幕都以 PDT 为权威时钟，所以它们会在同一条本地时间线上连续播放，只是这条时间线可能落后于外部直播边缘。若要把这 190 秒归因到 YouTube/CDN、yt-dlp/FFmpeg 消费速率，还是本地 PTS 重基准，必须同时采集源端 playlist PDT 或第二个独立直播参考；本次没有这条对照证据。

从当前滚动窗口看，PDT 每秒递增、分片仍持续写入，说明结束前最后几分钟不是简单的“完全停流”。更符合的机制是：

1. yt-dlp/FFmpeg 取得的 live HLS 边缘本身已经落后，或媒体 leg 在某段时间以低于 1× 的速率消费，形成约 190 秒的固定 backlog；
2. 现有 publisher 只按“保留多少媒体秒数”发布，不比较 `latest PDT` 与本机墙钟，因此不会发现并跳过这段 backlog；
3. ASR 管线在重连时会丢弃没有 stream 的 PCM，但没有把丢弃量、输入时间线落后量作为可观测指标；一旦发生恢复突发，旧音频可能以远高于 1× 的速率灌入 ASR，造成更多无效延迟或 cue 空洞。

## 追赶机制（建议的产品语义）

目标应定义为“字幕服务尽快恢复到最新 live frontier”，而不是把历史欠账逐秒补完：

1. **实时落后探测**：每 1–2 秒计算 `wall_now - latest_private_PDT`，并同时记录 source idle、音频/视频 leg 的字节速率和 PTS 速率。超过 10 秒进入 `DEGRADED`，超过 30 秒进入 `CATCHING_UP`。
2. **跳过旧 backlog，保住最新语义**：进入 `CATCHING_UP` 时，停止向 ASR 继续灌入旧 PCM；丢弃到“当前 live frontier − 2~3 秒”的音频，并累计 `catchupDroppedSeconds`。这比让 ASR 以 10×/25× 追历史更快恢复字幕。
3. **重新建立 ASR 会话**：清空旧 session 的 VAD/breadcrumb，建立新 session，只送最近 2–3 秒尾部，再以不超过约 1.1–1.2× 的速率恢复；超过上限仍直接丢旧数据并计数。
4. **时间线恢复条件**：只有当 `wall_now - latest_PDT <= 5 秒` 且音频 PTS 速率连续 3 个采样窗在 `0.9–1.2×` 内，才退出 `CATCHING_UP`。恢复后优先显示新的 source cue，翻译可异步替换，避免 provider 慢导致“看起来完全没字幕”。
5. **低质量但可用的降级**：若视频 leg 继续低于 1×，保留独立音频 leg 服务字幕，同时自动把画面 leg 切到更低码率/更稳的 HLS rendition；不要让 1080p60 的下载 backlog 拖垮字幕恢复。

## 下一步实现顺序

1. 先给 ingest/publisher 加三项持久化指标：`latestPdtEpoch`、`sourceLagSeconds`、`catchupDroppedSeconds`，并把每次状态转换写入按会话滚动 JSONL。
2. 在 ASR PCM sender 加“frontier-aware drop + bounded catch-up”状态机；补一个模拟 60 秒 backlog 的回归测试，断言恢复时间和丢弃秒数，而不是只断言进程没有崩溃。
3. 在发布器加入 source lag 熔断：超过阈值自动重启/重新提取媒体 leg，或切换到较低码率；禁止静默地继续累积分钟级 backlog。
4. 下一场测试至少保留：每秒 PDT、分片写入时间、每 leg PTS/bytes、ASR reconnect、PCM drop、translation start/finish、cue ready/display 的 JSONL。这样才能回答“哪一秒开始落后、落后多少、几秒恢复”。

本报告只记录证据和方案，没有修改用户配置，也没有把任何凭据写入报告。
