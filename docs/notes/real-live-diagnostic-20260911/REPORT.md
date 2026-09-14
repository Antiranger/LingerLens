# 真实直播时间线探测实验报告

## 实验目标

本实验不是只测“播放器有没有卡”，而是把延迟拆成四层：

1. 源端 HLS live edge：源 playlist 最新媒体时间相对本机墙钟的偏移。
2. yt-dlp/FFmpeg 输入：输入 PTS 的推进速率、字节速率、source idle 和重试。
3. LagLingo private/public HLS：分片 PDT、写入时间、发布隐藏秒数和媒体推进速率。
4. 字幕侧：先关闭字幕隔离媒体层，再用真实 Soniox ASR + 翻译配置做修复前/后的短时对照；只记录遥测，不保存字幕正文。

## 实验配置

- 源：公开、当前 `is_live` 的 YouTube 长时直播（实验记录不保存签名 URL）。
- Companion：仓库当前源码启动的独立实例，loopback 端口 39976–39979；使用独立 runtime 目录，不触碰 Electron 运行目录。
- 质量基线：API 选出的 `1080p-avc1-muxed-96`，H.264/AAC stream-copy；先关闭字幕隔离媒体，再开启字幕做修复前/后对照；live messages 始终关闭。
- 采样：local status 每 1 秒；源端 HLS manifest 每 2 秒；每次只保存脱敏字段，不保存媒体 URL、Cookie 或 provider 密钥。
- 时钟计算：本地 HLS 用 `firstPdt + privateMediaSeconds` 估计最新 PDT；源端 playlist 如果只有首个 `PROGRAM-DATE-TIME`，用首个 PDT 加上分片累计时长推导最后一个分片起点。

## 结果

### 本地 Companion 窗口（99 个有效样本，约 99.2 秒）

| 指标 | 结果 |
|---|---:|
| private 媒体推进 | 100.000 秒 |
| 墙钟经过 | 99.231 秒 |
| media over wall | **1.008×** |
| `latest local PDT - wall` | 中位数 +10.153 秒；范围 +5.887…+14.890 秒 |
| source idle | 0…5.1 秒 |
| 状态 | 全程 running，无 error |
| PTS 样本 | PTS 从约 1.40 推进到约 116.32 秒 |

这说明当前窗口内没有分钟级停顿，输入/封装以约 1.008× 推进；但本地输出 PDT 与本机墙钟存在约 10 秒的时间基准偏移。`/api/status` 当前把 `sourceDelaySeconds` 固定返回 0，这个字段不能用于判因。

### 源端 HLS 窗口（33 个有效样本，约 100 秒）

| 指标 | 结果 |
|---|---:|
| 音频 HLS target duration | 5 秒 |
| playlist 长度 | 721 个分片（约 1 小时 DVR 窗口） |
| source media sequence 增长 | 19 |
| `source latest PDT - wall` | 中位数约 −23.044 秒；范围 −20.413…−25.922 秒 |
| 请求错误 | 0（使用 requests + yt-dlp 返回的安全请求头） |

源端最新媒体本身约落后本机 20–25 秒，这是一个正常的 HLS live-edge/分片安全窗口量级，不是 3 分钟卡顿。

为判断它是否会持续扩大，又做了一次独立的 120 秒源端复测（39 个有效样本）。这次 fresh extraction 只返回了 audio-only rendition，但它的 HLS 时间线足以测 live edge 稳定性：`latest PDT - wall` 首/中位/末为 −21.261 / −23.036 / −23.517 秒，范围 −25.464…−20.418 秒；media sequence 增长 23 个（约 115 秒），请求错误为 0。五点移动中位数从 −22.564 变到 −22.324 秒（约 112 秒只变化 0.241 秒），线性斜率约 **+0.118 秒/分钟**，不是单调增长。换言之，这是围绕约 23 秒的抖动/安全窗口，不是“每分钟再落后几秒”的失速。

这 20–25 秒来自源站编码、HLS 分片完成、CDN playlist 传播和安全窗口的合计（本流 target duration 为 5 秒，约相当于 4–5 个分片）；它在源端就已经存在，本地下载器无法把尚未发布的媒体提前拿到。若“15 秒”指从现实直播墙钟到画面的绝对延迟，就必须选择/请求源端低延迟 HLS 或其他低延迟传输；若“15 秒”指字幕相对本地视频的处理延迟，则应单独看 cue ready 链路，不能把这 23 秒源端窗口算进字幕算法。

### 字幕开启、修复前（89 个有效样本，约 90 秒）

| 指标 | 首样本 | 末样本 |
|---|---:|---:|
| Companion uptime | 31.2 秒 | 120.8 秒 |
| private media | 34.001 秒 | 124.024 秒 |
| ASR PCM | 29.7 秒 | 119.6 秒 |
| caption chunks | 3 | 25 |
| pending finals | 3 | **25** |
| media anchor | `ready=false, samples=0` | `ready=false, samples=0` |
| translation attempts | 0 | **0** |
| PCM dropped / ASR reconnects | 0 / 0 | 0 / 0 |

媒体腿、ASR 音频腿和 ASR 本身都在推进，没有丢 PCM 或重连；但 25 个已完成 caption chunk 全部停在 `pendingFinals`，根本没有进入翻译和 cue store。因此这是可以让“画面、声音都连续，字幕却持续落后”的直接故障路径。

根因是采样模型与真实 playlist 更新方式不匹配：private HLS 内部虽是约 1 秒分片，但运行中 `privateMediaSeconds` 以约 **5.005 秒一批**更新；anchor 每 2.5 秒采样一次，连续看到的 video rate 便交替为 0× 和约 2×，都被允许范围 0.4×–1.6×拒绝。代码注释和旧测试只覆盖了每次推进 2–3 秒的理想量化，没有覆盖五秒批量写入。

### 最小修复与真实回归

修复只改变 `MediaAnchor` 的基线规则：当 video counter 没变时不覆盖上一个基线；等下一批媒体到达后，用整段经过时间计算平均速率。这样 5 秒媒体 / 5 秒墙钟会得到 1×；真实停顿期间仍不会产生任何可信样本。

同一直播、同一 1080p format、字幕开启的修复后回归（64 个有效样本，约 65 秒）：

| 指标 | 末样本 |
|---|---:|
| media anchor | `ready=true, samples=16` |
| anchor offset / spread / drift | 4.351 秒 / 0.161 秒 / −0.042 秒 |
| pending finals | **0** |
| ASR PCM / caption chunks | 104.7 秒 / 24 |
| PCM dropped / ASR reconnects | 0 / 0 |
| translation attempts / failures / dropped | 35 / 9 / 0 |
| translation backlog | 0 |
| source ready P95 | 23.351 秒（包含启动期等待 anchor 的旧 chunk） |
| translation provider P95 | 1.547 秒 |
| total ready P95 | 16.062 秒 |

回归证明 anchor 能收敛，积压会被立即释放，字幕链不再无限等待。仍有 9 次翻译超时（35 次尝试中），但队列为 0、没有 translation drop，表现为个别 cue 退化/失败，而不是分钟级持续积压；这是下一项需要独立优化的可靠性问题。

### 翻译 fallback 的实现状态

当前实际配置是 `active=translation-1`（本地 `gemini-3.7-flash-low`），`translation.fallback=[]`；配置里已有 `bailian-qwen35-flash` 记录，但当前没有可用密钥，因此本轮没有强行启用它。

代码层已做低开销的 fallback 修正：

- `FallbackChain` 为后一个 provider 预留 2 秒总 deadline；主 provider 即使耗尽自己的时间，兜底仍有预算。
- `SubtitlePipeline` 对已经拥有 fallback 的 chain 只执行一次 chain 调用，避免外层“三次重试”把一次故障放大成多次主模型和兜底模型请求。
- 没有增加常态 worker 数、后台轮询或双模型并行；只有主请求失败时才调用兜底。

推荐的第一候选仍是当前配置中已有的 `qwen3.5-flash`：官方文档将其定位为更高推理效率的 Flash 模型，适合作为短文本翻译的快速 HTTP 兜底；但“最快”不能仅凭模型名保证，必须在当前网络和账号区域实测 P95。若改为接入阿里云专用的 `qwen3.5-livetranslate-flash-realtime`，官方资料给出低至约 2.8 秒的同传延迟，但它是 WebSocket/AOQ/WebRTC 实时接口，不是当前 OpenAI-compatible adapter 的零改动替换，需要另做协议适配。

## 重要限制

本实验的 API probe 在不同提取时刻返回过不同的 YouTube format 集合：API 选中的 96 与独立源探针某些时刻只能看到 270/234 或仅音频 234。因而本轮已经证明源端和本地各自的推进行为，但**还没有做到“同一个 signed rendition URL 的逐分片一一对照”**。重现旧会话的 190 秒数字时，不能直接把它归因给 YouTube live edge；它可能来自旧 format 的 PTS 基准、某次输入 backlog，或本地 PTS 重基准。

## 判定

1. “播放连续”与“绝对时间是否新”是两件事；连续 1× 播放可以带着固定偏移。
2. 本次新鲜源的实测 live edge 约 −20…−25 秒，未复现 3 分钟源端落后。
3. 本次本地媒体推进率 1.008×、无停顿，未复现“正在卡住”或低于 1× 的持续吞吐。
4. 字幕开启后复现并定位了真正的阻断点：媒体 anchor 的速率门控拒绝了所有批量更新样本，导致已完成 ASR chunk 一直滞留在 `pendingFinals`。
5. 最小修复后的真实回归中 anchor 稳定、`pendingFinals=0`，证明该故障已被解除；翻译超时仍需后续治理。
6. 旧会话的“约 190 秒”仍应降级为“旧本地输出 PDT 相对墙钟的观测差值”，不能直接写成“字幕相对画面落后 190 秒”。

## 下一轮必须补的探针

- 在同一次 fresh probe 中保存 format id、manifest 的不可逆 hash（不保存 URL），并把该 manifest 的 PDT/sequence 与实际启动的 format id 绑定。
- 在 `YtDlpLiveIngest` 记录每个 leg 的 `sourcePtsFirst/Last`、PTS 速率和重连/skip 日志事件。
- 在 publisher 记录 `latestPrivatePdt`、`latestPublicPdt`、文件完成时间和 `sourceLagSeconds`；移除 `/api/status` 中固定为 0 的假值。
- 把 `anchorRejectedNoAdvance`、`anchorRejectedVideoRate`、`anchorRejectedPcmRate` 计数写入状态，下一次无需离线反推门控原因。
- 字幕开启时继续记录 `audioEndPdt → asrFinal → translationStart → translationFinish → cueReady → cueDisplay`，并把播放器确认显示的时间回传，补齐最后一跳。
- 配置一个有凭据的 `qwen3.5-flash` 后，做主模型故障注入：验证主超时后是否在剩余 2 秒内只发出一次兜底请求，并记录实际 P95；未配置凭据前不要把 fallback 列表写入生产配置。
- 只有当 `sourceLagSeconds` 超过 30 秒且同一 rendition 的 PTS/playlist 也确认落后，才触发 re-extract 或 bounded catch-up；否则不能误清空正常的 HLS 安全窗口。
