# LagLingo 最小全链路延迟与对齐实验

**Status:** ready-for-implementation

## 目标

只回答两个问题：

1. `Caption Chunk end → 翻译字幕 ready` 的时间分别花在 ASR、LagLingo 分块、翻译排队和翻译 Provider 的哪一段？
2. 后续字幕持续偏移是单条字幕迟到，还是 PCM→视频 PDT 的对齐常数失效？

不先建设通用 tracing 平台，不增加复杂 alignment epoch，不记录字幕全文、Provider raw payload、URL、Cookie 或凭据。

## 最小实现

直接在 `SubtitlePipeline` 内增加固定长度为 60 的统计窗口和少量内部字典，不新增公共领域模型。每个 Caption Chunk 只记录六个 monotonic 时间点：

```text
T0 audio_pushed       chunk.end_pcm 对应的音频已经 push 给 ASR
T1 evidence_available 首个覆盖 chunk.end_pcm 的 normalized evidence 到达 Pipeline
T2 chunk_emitted      CaptionChunker 发出 Chunk
T3 translation_start  worker 真正开始处理
T4 translation_end    Provider 成功、失败或超时
T5 cue_ready           Cue 进入 done|failed
```

由此计算：

```text
ASR + Adapter delay = T1 - T0
Chunker policy delay = T2 - T1
Translation queue delay = T3 - T2
Translation Provider delay = T4 - T3
Store/update delay = T5 - T4
Total ready delay = T5 - T0
```

`T0` 使用一个小型 ring：`(pcm_frontier, monotonic)`，每次成功 `push_pcm` 后写入；按 `chunk.end_pcm` 查最近的覆盖样本。`T1` 在 `_handle_caption_observation` 记录该 item 当前 evidence frontier 的首次到达时间。Chunk emit、worker start/end 和 terminal update 在现有调用点直接记录。

Status 只暴露每段 P50/P95、样本数和 unknown 数；不暴露逐条 trace，也不修改 Cue JSON。

第一轮不改浏览器。浏览器当前字幕轮询周期为 500ms、render tick 为 100ms；如果服务端五段之和仍解释不了用户看到的迟到，再单独加一个浏览器 `firstSeen→firstRender` 指标，避免过早扩展范围。

## 对齐实验

### 现状

Cue timestamp 与 ready time 分离。某条 ASR/翻译迟到不会修改后续 Cue 的 `tStart/tEnd`，所以不会自然累计偏移。

真正可能让后续全部固定偏移的是 `MediaAnchor.C` 错误。当前 C 在 20 个样本后冻结，之后 `mediaAnchorDrift` 只报告、不修正。因此：

- 单条翻译迟到：只影响该 Cue 显示时间；
- C 启动估错或运行中两条媒体腿发生真实跳变：后续 Cue 会持续固定偏移，当前不会自动恢复；
- ASR reconnect 本身有 generation/breadcrumb 映射，不应重置 C。

### 最小验证

不先实现自动纠偏，只做两项：

1. 将现有 `mediaAnchorDrift` 每秒采样到 60 个窗口，暴露 P50/P95/Max；
2. 增加一个确定性 synthetic test：视频时间/PCM 同速时 drift 接近 0；中途人为给 packaging counter 加 2 秒后，drift 必须持续约 2 秒，证明系统能检测“后续全部错位”。

真实直播中同时观察：

```text
如果字幕持续偏移且 anchorDrift 同方向、同量级：MediaAnchor 基准失效。
如果 anchorDrift 接近 0，但 Cue 经常晚出现：readiness 延迟，不是时间轴错位。
如果只有少数 Cue 错、之后恢复：ASR token/chunk timing 局部错误，不是累计偏移。
```

只有实验确认存在持续 drift，下一步才设计最小恢复：在 Caption Chunk 边界用新的稳定 C 替换旧 C；旧 Cue 保持不可变。此轮不提前实现 alignment epoch 框架。

## 实验步骤

### 1. 自动测试

- Fake ASR 分别注入 0/2/5 秒 evidence delay，确认只增加 `asrAdapterDelay`；
- 提前给 evidence、延迟 Chunk emit，确认只增加 `chunkerPolicyDelay`；
- 用单 worker + sleep 分别制造 queue delay 和 Provider delay；
- 成功、失败、超时均进入统计；
- MediaAnchor 中途注入 +2 秒 counter jump，确认 drift 窗口捕获；
- 验证 status 不含文本和敏感数据。

### 2. `ironmouse` 真实实验

重启 Companion 后运行 10 分钟，至少 100 个 Caption Chunk。每分钟保存一次 `/api/status`，记录：

```text
asrAdapterDelay P50/P95
chunkerPolicyDelay P50/P95
translationQueueDelay P50/P95
translationProviderDelay P50/P95
totalReadyDelay P50/P95
mediaAnchorDrift P50/P95/Max
hardCapOvershoots
scheduler late/drop
```

### 3. 决策规则

- `asrAdapterDelay` 最大：继续拆 Soniox wire arrival 与 lexical hold，或调整 Provider endpoint/finality；
- `chunkerPolicyDelay` 最大：优化 CaptionChunker 发布策略；
- `translationQueueDelay` 最大：调整 worker/backpressure；
- `translationProviderDelay` 最大：换低延迟 Provider、流式翻译或降上下文；
- drift 持续异常：实现 Chunk 边界重新校准；
- 服务端总耗时正常但仍晚：再加浏览器 first-seen/render 测量。

## 验收

- 至少 95% Caption Chunk 能完成延迟分解；
- 五段之和与 total 的误差小于 100ms；
- 无注入故障时 MediaAnchor drift P95 小于 250ms；
- 实验结果能明确指出最大延迟阶段，而不是只给一个混合 readyLag；
- 不记录或返回字幕正文、聊天正文、Provider payload、签名 URL 或任何凭据。
