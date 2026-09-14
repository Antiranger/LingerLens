# 直播字幕延迟实验独立分析

分析日期：2026-09-05。分析对象是固定 `ironmouse-status-samples.json` 中的 0–600 秒共 11 个采样点，以及本次读取的工作区代码。没有把实验结束后的会话状态混入数据，也没有读取私密 Provider 配置、重启服务或调用真实模型。

本次结论：**实验已经揭示严重的观测失效，并离线复现一个会造成多余强制提交的分块器问题；但现有数据不足以判定实际观看中的字幕迟到率，也不足以认定 Soniox 或翻译服务是整场主要瓶颈。** 优先修复测量关联和已复现的计时起点问题，然后补一次同时包含浏览器播放位置的实验。

证据级别：以下区分“固定数据证明”“当前代码与离线输入证明”“需要实测”。离线复现不等于证明本次直播每条异常都走过该路径。

## 1. 数据有效性

| 采样秒数 | Chunk 累计 | 完整分解 | Unknown | 完整/(完整+Unknown) | 本分钟新增完整样本 |
|---:|---:|---:|---:|---:|---:|
| 0 | 2 | 2 | 0 | 100% | — |
| 60 | 18 | 18 | 0 | 100% | 16 |
| 120 | 46 | 35 | 7 | 83.33% | 17 |
| 180 | 92 | 38 | 52 | 42.22% | 3 |
| 240 | 124 | 39 | 84 | 31.71% | 1 |
| 300 | 175 | 39 | 131 | 22.94% | 0 |
| 360 | 210 | 39 | 169 | 18.75% | 0 |
| 420 | 270 | 39 | 230 | 14.50% | 0 |
| 480 | 299 | 39 | 259 | 13.09% | 0 |
| 540 | 318 | 39 | 277 | 12.34% | 0 |
| 600 | 338 | 39 | 298 | 11.57% | 0 |

- 338 个 Chunk 达到实验要求的至少 100 个，但完整分解占全部 Chunk 仅 11.54%；按已被计数的完整/Unknown 样本计算为 11.57%，远低于 spec 的 95%。
- 240–600 秒产生了 214 个新 Chunk、214 个新 Unknown、零个新完整样本。百分位窗口从未攒满 60 条，更没有在后六分钟更新。P95=6.344 秒不代表十分钟的 P95，也不代表后半场性能稳定。
- 600 秒时 `338-39-298=1`。可能是仍在处理，也可能属于未记录的丢弃/抑制路径；仅凭 snapshot 不能定性。下一轮应对每个 Chunk 的终态分类，避免分母漏项。
- 0 秒已经有 2 个 Chunk 和 3 次 manual decision，采样起点不是严格的进程零时刻。应记录 session 启动时间和 sampler 启动时间。
- `translationFailures`、`pcmDropped`、`teeDropped` 全程为 0：只证明对应计数没有报告失败/丢弃，不能证明字幕没有遗漏、翻译全部显示或网络没有停顿。

早期较完整的 60 秒 snapshot：ASR+Adapter P50/P95=2.718/5.016 秒，翻译调用 P50/P95=1.329/4.063 秒，总 ready P50/P95=5.266/7.047 秒。说明这些早期样本确有秒级后处理成本，值得关注 ASR 与翻译两个环节；但样本只有 18 条，且测量关联本身存在下述缺陷，不能据此给整场排名。

不同分布的 P50/P95 不能相加。五段加总是否等于总耗时，必须逐 Cue 检查，当前 JSON 无法完成这项验收。

## 2. 当前代码与离线复现发现

### 2.1 首次覆盖时间被淘汰后，会被较新时间冒充

位置：`companion/subtitle_pipeline.py:1318`、`:1551`。

`_frontier_time` 返回队列中第一个 `frontier >= position` 的时间，却没有检查该记录是否仍是最初覆盖记录。T0 直到翻译终态才查询，这期间 ring 可以继续淘汰历史。

离线输入使用实际 1200 项容量：原始位置 0.1 的真实时间是 0.1，淘汰后查询得到 0.2，而不是 unavailable。更长淘汰会使总延迟显著低估。相同查询函数也用于 T1。

**因此，不能直接声称 298 个 Unknown 是 ring 超过 120 秒导致的。** 对已过期的旧位置，这段代码通常返回错误时间，而非 None。音频/证据 ring 非空时，None 更直接指向“没有保留 frontier 覆盖查询位置”；还需要记录缺的是 T0、T1、T3 还是 T4，以及位置与最早/最新 frontier 的关系。

### 2.2 负时间差被归零，会把无效关联计为完整样本

位置：`companion/subtitle_pipeline.py:1557–1576`。

完整性只检查字段非空，分段差值各自 `max(0, delta)`，没有验证时间单调关系。以真实记录函数输入一条 T0 被错配到较晚时间的样本，五段加总为 138 秒，总耗时为 19 秒，却仍增加 `latencySamples`。

这是已复现的测量缺陷，**不是说直播真的出现了 138 秒或 19 秒的那条样本**。它说明 spec 的“误差 <100ms”还没有被实现或现有测试守住。T0 在 `await push_pcm` 返回后才打点，本身也需要处理响应与 push 完成时间的并发顺序。

### 2.3 T1 不是“这个 Chunk 的全部稳定词已经可用”

位置：`providers/asr_soniox_realtime.py:312–342`；`subtitle_pipeline.py:1045–1049`。

Soniox 把 final+non-final 的最大结束位置放入 observation.end_pcm，但 tokens 只携带已闭合的稳定词。离线向真实 Adapter 输入仅含 non-final `hello` 的响应，输出稳定词数为 0，Pipeline 仍记录了该音频位置的 evidence 时间。

这符合“任意 normalized observation 覆盖位置”的宽口径，却不能解释为“可供分块发布的词已经齐备”。若继续用它作为切分点，一部分等待稳定词/词边界的时间会落入 `chunkerPolicyDelay`。此外该 evidence ring 没有按 item/token 关联，其他 item 的更远位置也可能提前满足查询。

最小改进是明确区分首次识别覆盖与本 Chunk 所需稳定词可用，后者应关联 item/generation 与发布 token。只有需要进一步归因时，再增加 Provider WebSocket 收帧时间。当前 asrAdapterDelay 既不能隔离网络与 Provider，也不能可靠承诺包括完整 lexical hold。

### 2.4 切块后的 hard deadline 起点会被下一次 observation 回退

位置：`companion/caption_chunker.py:175`、`:201–235`、`:431–439`。

`_emit` 正确把下一段起点推进到切块结束位置，但 `observe` 又用整句 observation.begin_pcm 与 state.begin_pcm 取最小值。Soniox 后续更新持续携带整句起点，即使没有新增稳定词。

离线复现：

1. 输入 0–6 秒的六个稳定词，正常发出一个完整 Chunk，剩余 units 为空，下一起点为 6 秒。
2. 输入同一 item 的空稳定词 delta，begin=0、end=6.1。
3. state.begin 从 6 回退到 0。
4. `advance_audio(6.1)` 立刻请求 manual commit。

切完后仅推进 0.1 秒就请求提交，违反了代码自己声明的“下一段预算从切块边界开始”的意图。这是具体的字幕分块逻辑问题。对 Soniox，这个 decision 会走到 `stream.commit()` → `{"type":"finalize"}`；是否造成真实额外等待、碎片字幕或服务端开销，需要对照数据，不能直接宣布它就是整场延迟根因。

### 2.5 hard-cap 计数不是请求数或超时条数

位置：`companion/caption_chunker.py:201–235`、`:442–444`；`subtitle_pipeline.py:1059–1065`。

- `hardCapPendingEvidence=47327` 是逐 PCM advance、逐未关闭 item 的计数；等待期间可以反复增加，不是 47327 条字幕失败，也不是 47327 秒。
- `manualHardCommits=1216` 是逐 item 的提交决策计数。同一 advance 内可以多个 item 同时计数，最终只返回一个布尔值，Pipeline 最多调用一次 commit。离线放入 10 个到期 item，计数增加 10，但对应一次 Pipeline commit 调用。
- 节流按每个 item 的 4 秒 PCM frontier 差执行，不是全连接 monotonic 4 秒限频；突发上传或多个 item 可能导致网络请求比预期密集。实际发送频率必须单独记录。
- `hardCapOvershoots=1` 只表示一条发出的 Chunk 音频跨度超过 6 秒，不能推出其 ready 延迟超标，也不能反过来说其他 Chunk 都按时显示。

计数增长说明应检查 item 生命周期、空稳定词更新、PCM 推进速度与 finalize 次数，但不足以单独证明 Provider 慢或 item 泄漏。

## 3. 15 秒目标延迟能否保证字幕准时

**不能用 15 - 6.344 判断。** totalReadyDelay 从 Chunk 最后音频 push 开始算；屏幕预期从 tStart 显示整条字幕，还需要覆盖 Chunk 本身的音频跨度、音频到达/push 的进度差、实际播放缓冲和浏览器拉取/渲染耗时。

按“实际可用缓冲约 15 秒、Chunk 跨度 6 秒、后处理 7 秒、理想轮询/渲染约 0.6 秒”举例，余量可能只有约 1.4 秒。这只是解释预算的假设算例，不是本次实验测得的余量，更不能把不同样本的百分位简单相加来验收。

浏览器行为已确认：

- `subtitle-scheduler.js:54–78` 只允许 done/failed 显示，src/translating 不显示。因此即使 ASR 原文早已生成，翻译等待仍可能表现为完全没字幕。
- 晚于 tStart、但还在 `tEnd + min(hold, 1.5s)` 内的字幕，会中途显示，却不增加 lateCues。现有 late 计数不等于“相对说话开头迟到”。
- 超过上述结束窗口后，若又晚了超过默认 2 秒，则计入 droppedLateCues。
- `player.js:1435–1441` 的 500ms poll 和 100ms render 是调度间隔，不是包括 HTTP、后台标签和主线程阻塞的延迟保证。
- spec 提到 scheduler late/drop，但原始 JSON 实际未采集；也没有浏览器 first seen/first render 数据。

下一轮直接测 `startSlack = tStart - playbackWallTimeAtFirstSeenTerminal`；负数就是字幕到浏览器时已经错过句首。首次实际渲染记录 `max(0, playbackWallTimeAtFirstRender - tStart)`，并保留未显示原因及可见持续时间。两者使用同一媒体时间轴，不跨进程相减 monotonic 时钟。

## 4. 对齐结论

非空 snapshot 的 drift P95 为 0.1–0.2 秒，窗口最大绝对值曾达 1.1 秒，最终窗口降至 0.3 秒。现有采样**没有显示持续增长的数秒 counter residual**，暂不支持优先做自动 anchor 重校准。

仍不能说字幕语义同步已经通过：

- P95 对有符号 residual 排序，不是 abs residual P95，大幅负偏移可能被 P95 掩盖。
- 只存最近 60 个有效样本，采样拒绝单腿推进；JSON 未保存 driftSamples、最近有效样本年龄、冻结 C 和采样坐标。小残差可能来自旧窗口。
- C 是冻结的启动中位数。持续固定的启动估计偏差、ASR token 时间误差和真实音画偏移可能不会体现在 residual 中。
- `core.py` 的 private counter 来源是新发现的 HLS segment duration 累加；`capture_clock.py` 也主要依据首次 PDT 和累计时长。这些是同系统坐标一致性证据，不能替代已知口语/音画事件的绝对校验。

建议补一个带已知语音起止及可视时间标记的可重复媒体片段。正负 2 秒 counter jump 与单腿暂停/恢复分别测试，报告 abs drift P95、max、有效样本年龄；只有真实持续偏移被独立证实才做边界重校准。

## 5. 最少补实验与优化顺序

| 顺序 | 最小工作 | 验证与通过条件 | 停止/回退条件 |
|---|---|---|---|
| P0 | 修时间关联：在仍可确定首次覆盖时锁定 T0/T1；缺样本分原因；越界/逆序无效化；显示有效样本数与年龄；阶段可独立保留已知耗时 | 至少连续 10 分钟/超过 1200 PCM push；正常、迟证据、迟翻译、重连、突发推送都覆盖；按已有 spec 总覆盖≥95%、逐 Cue 分段和误差<100ms；所有 Chunk 可解释去向 | 覆盖不达标或窗口停更时停止性能排名，先解决观测问题；不能单纯扩大 ring 后宣布修好 |
| P1 | 修已复现的 hard deadline 起点回退；保留既有切词和文本不变性 | 本文空 delta 复现由失败变通过；真实调用级 finalize 次数/间隔可见；同一输入下切词无重复、无丢失、跨度约束不回退 | 若降低请求数但字幕等待/丢弃变多，停止扩大策略修改并回到确定性用例 |
| P1 | 一轮固定设置的前后台联合采样，至少 10 分钟/100 个 Chunk | 每个 Cue 用 session/generation/item/cue id 关联；有 T0–T5、tStart/tEnd、终态、firstSeen/firstRender、playbackWallTime、可见时长/未显示原因；统计相对句首迟到率与丢弃率，原文/翻译 readiness 分开 | 关联覆盖不足、播放暂停/拖动/后台时段未标记时不做体验结论；历史加载字幕不能计入本轮迟到分母 |
| P2 | 当归因指向 ASR 时拆首帧/稳定词发布时间；指向 MT 时做同一录音的一变量对照 | ASR 0/2/5 秒延迟只改变对应段；队列与 translate 调用各自注入延迟；成功/失败/超时均可解释；候选改变须改善实际句首迟到/丢弃，同时文本完整性不退化 | 不因旧 P95 换 Provider、加 worker 或缩短所有 Chunk；质量退化或收益不稳定就停止 |
| P2 | 使用已知媒体标记验证绝对同步，再决定是否修 anchor | 无故障时 abs residual 与语义误差分别量化；±2 秒故障可被识别，样本停更可见 | 仅有瞬时 residual 或共同坐标偏差证据时，不引入自动校准机制 |

联合采样只需实验专用的紧凑逐 Cue JSONL，不必建设通用 tracing 平台。数字时间点与 id 不需要字幕全文、raw payload、密钥或媒体 URL。内容正确性另用固定人工可核对的小片段，覆盖连续语流、短句停顿、无声、插话/重叠语音；观察遗漏、重复、句中断词、超短闪现及翻译上下文是否丢失。仅延迟指标无法发现这些字幕功能问题。

额外保存一份白名单实验元信息：代码版本/工作区相关文件摘要、实际加载版本、模型 ID、非敏感 options、worker 数、目标与实测缓冲、session/sampler 起点。handoff 和当前磁盘代码不自动证明实验进程加载的版本相同。

## 6. 本次验证与文件变更

已运行现有窄测试：

```powershell
python -X utf8 -m unittest discover -s prototype/hls-companion/tests -p test_subtitle_pipeline.py -q
python -X utf8 -m unittest discover -s prototype/hls-companion/tests -p test_soniox_realtime.py -q
python -X utf8 -m unittest discover -s prototype/hls-companion/tests -p test_caption_chunker.py -q
node prototype/hls-companion/tests/test_cue_scheduler.js
```

结果分别为 54、13、14、13 项通过，共 94 项。现有延迟测试主要手工填入时间点、检查数值与缺失，并非 spec 中全部异步故障注入场景；测试通过不能覆盖本次新复现。

新增离线诊断脚本：

```powershell
python -X utf8 .scratch/live-pipeline-latency-observability/analysis-probes.py
```

已运行：数据覆盖和新鲜度两项失败；首次覆盖淘汰、分段和守恒、可发布证据口径、空 delta 起点回退四项失败；六秒切块 fixture 通过；多 item 决策计数观察为 10 对 1。这里 FAIL 是诊断捕获的当前问题，脚本执行正常；第 2.3 项是指标含义不满足更强解释，不能当作既有宽口径协议必然违约。合成输入输出不能当成真实直播测量值。

本次仅新增本分析文档、`analysis-probes.py` 和 `analysis-probe-results.jsonl`。没有修改生产代码或现有测试，没有重跑真实直播；实际观看迟到率、Unknown 的逐条原因、已复现分块问题在该直播中的贡献仍待补实验。
