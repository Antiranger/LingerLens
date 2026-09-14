# 按分句证据切字幕：实现与实验记录

日期：2026-09-06。用户要求：长句尽量不截断；持续讲话时至少在分句结尾切；最小实现，不引入明显性能问题。

## 实现

- ASR item ledger 与 caption buffer 分离。final 确认识别、核对残差并关闭 item，不意味着字幕结束。
- 同一已知 speaker、同一主语言的未完成表达跨 item 接续；不同 speaker 保留独立重叠时间。未知 speaker 不跨 item 猜测身份。
- 六秒为软参考；删除任意词、时间间隙和 hard-deadline 切分。完整短句已在当前 batch 时保留整句，没有为凑时长额外等待。
- `clause_boundaries.py` 本地分析日语形态。保护助词/助动词链、否定和词内部；支持有限从句、口语因果、完整引用及独立反应。英文/中文弱边界覆盖有限，其余语言主要保守使用句末标点。
- Soniox 的 CJK immutable pieces 立即转交，保留原始文字与时间；删除 adapter 中额外的日语短语等待。
- cue 的 speaker 和 timing provenance 来自实际输出 chunk；相同时间相同文字、不同 speaker 不再相互去重。
- lifecycle flush 一次性保留尾部，generation reset 防止串入上次会话。关闭 item 清理大字段，duplicate-final tombstone 上限 512（超限回收至 256）；2000 个完成句子测试通过。
- 新增本地依赖 fugashi 1.4.0 / unidic-lite 1.0.8，requirements、README、CONTEXT、第三方声明及 CI 已更新。

## 可复现验证

`python .scratch/clause-segmentation-v2/verify.py`

- 24 个手写对抗案例：禁止切点 0，文字全部保留，17 个标注安全切点命中。
- 38 个日语细粒度 piece 变体：禁止切点 0，文字全部保留。
- 历史真实 trace 经实际 Soniox adapter + 新 chunker 回放：按 speaker 文字守恒，hard cut / manual commit 均为 0。
- 旧 endpoint-on 60 秒记录：起点至提交 P50 3.34s / P95 6.809s。该项仅为回放，不含真实翻译。
- 同一份新 120 秒识别 observation 输入，旧/新 chunker 计算中位耗时 3.442ms / 10.820ms（预热后 5 次）；新增约 7.4ms/两分钟音频。仅测切句 CPU，未涵盖网络、词典首次加载和整体 RSS。
- `npm run ci` 最终退出码 0：362 项 Python 测试、62 项 Node 测试，以及 release guard / Python compilation / JS syntax checks 通过。日志：`ci-final.log`。
- 新增完整 Soniox raw pieces → 正式 pipeline 回归：数字 pieces 拼接、跨 item 尾句、重叠说话、精确 cue 时间和 flush 去重均通过。

## 两轮真实 ASR + 翻译

`python .scratch/clause-segmentation-v2/live_pipeline.py`

取现有 `runtime/jinec-live-16k.wav` 前 120 秒，16k mono PCM 按真实时间发送。独立进程调用 Soniox `stt-rt-v5`（端点检测开启）、正式 SubtitlePipeline / chunker / translation worker，以及当前翻译 profile `gemini-3.7-flash-low`。日语到简体中文，15 秒播放预算。验证最终 `<fin>`，显式保留 stop residual，等所有翻译完成。没有启动直播采集、HLS 或浏览器播放器，不能称为实际字幕上屏延迟验证。

第一轮：33 条，29 成功、4 失败。失败定位到保守规则留下独立反应、遗漏口语 `んで`、长连续语音稳定识别晚到。修改语言规则后复测，不把成功样本的百分位冒充全样本。

第二轮（最终实现）：

| 指标 | P50 | P95 | 最大 |
|---|---:|---:|---:|
| 语音起点 → 提交字幕/翻译 | 3.449s | 10.609s | 13.365s |
| 语音起点 → 译文就绪 | 4.895s | 11.953s | 14.599s |
| 翻译排队 | 0s | 1.234s | 2.172s |
| 实际翻译请求 | 1.172s | 2.329s | 2.672s |

42 条全部成功，42 条均在起点后 15 秒内就绪。其中 2 条为测试结束时显式保留的残句。尾部确认完成，按 speaker 校验实际 pipeline 输出文字守恒。纯 chunker replay 另有一个空白-only chunk，被 pipeline 正常忽略，43 个原始 chunk 对应 42 条可见 cue。

第二轮稳定 token 首次到达相对 token 音频末尾（包括 final 中首次出现的 token、去重并排除纯空白）：P50 2.043s / P95 6.374s / max 6.939s。只统计 stable_token_delta 会漏掉直接随 final 到达的快 token，得到有偏结果；本报告没有使用该口径。

最慢一条：

> で、なんか見た目は。 ちょっとさ、赤チームさすがに両手すぎて、満点だよねってなって、

- 音频 94.26–100.92s，跨度 6.66s。
- 最后 `って` 和 `、` 的稳定证据直到 107.625s 到达，本地同一批次提交。
- 108.859s 译文就绪，排队 0，翻译 1.234s。
- 起点到就绪 14.599s = 分句 6.66s + 末尾稳定证据等待约 6.705s + 翻译约 1.234s。

输出保留在 `live-pipeline-results.json`、`live-soniox-evidence.jsonl`。第一轮原始结果留为 `live-pipeline-initial-results.json` / `live-soniox-initial-evidence.jsonl`。这些文件含测试识别内容，不含凭据。

## 解释与限制

这是分句边界与 ASR final 解耦的实现，不是普适语义解析器；不能保证所有口语都正确或都在 15 秒内完成。ASR 的错误词和错误标点仍可能出现在保留的原文中。长时间无可靠结尾会继续保留；未知 speaker 的未完成条目不能安全跨条目拼接，可能要等 lifecycle flush。未完成长文本的最坏内存/CPU 上界尚未验证，不能把完成句子 ledger 测试说成无限直播内存保证。

42 条是有限样本。两轮 ASR 输出不同，33→42 的变化同时受识别输出与切句规则影响，不是严格配对的线上因果对照。新增规则已另用固定输入回放验证，不能据单轮全部按时而降低默认播放缓冲。

现有翻译上下文的前序等待仍存在，本轮测得最长 2.172s；未在本次切句修改中重写翻译调度。pipeline 的全局 evidence frontier 在跨 speaker 的阶段归因上仍有限，本次最慢样本使用 raw 帧到达记录定位，未依靠该近似归因。

## 当前生效状态

已用既有 atomic config writer 把当前 Soniox profile 的 `enableEndpointDetection` 从 false 改为 true，确认其余配置保留；diarization 原本就是 true。

Companion 查询为 idle / subtitle running false。尝试运行项目重启脚本被自动审批策略拒绝（仅返回 blocked by policy），没有实际重启；新代码需用户手动重启 Companion 后加载。没有把独立实验进程结果说成当前桌面进程已经更新。
