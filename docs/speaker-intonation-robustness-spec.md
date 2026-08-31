# 技术规格：说话人语调鲁棒性

> 状态：ready-for-agent  
> 产品需求：`docs/speaker-intonation-robustness-prd.md`  
> 目标：不更换当前 speaker embedding 模型，减少同一人因情绪、语调和发声方式变化而产生的 ID 分裂，同时约束异人误合并、UNKNOWN 和延迟。

## 1. 固定基线和成功标准

本次只修改句级 speaker attribution。ASR final 继续提供句子边界，管线切取 16 kHz mono PCM，当前 embedder 生成向量，上层分配 speaker ID，并通过 cue late-fix 更新颜色。

**冻结基线**定义为当前生产行为：

- 整句单窗、无新增预处理；
- raw cosine；
- threshold `0.4`；
- EMA center + 当前 5 exemplar max；
- 长且不匹配的句子立即创建新 ID；
- 当前短句规则；
- attribution completion 维持当前实现。

benchmark 必须能显式运行 `legacy-baseline`，后续相对改善都与该 fingerprint 对比。`diar-spike.py` 的 0.7 仅是历史实验值，不属于基线。

发布闸门：

- evaluation false-split utterance rate 至少下降 50%；若基线为 0，则要求保持 0；
- evaluation false-merge utterance rate 不高于 `max(baseline + 1 percentage point, baseline × 1.10)`；
- UNKNOWN utterance rate 相对基线增加不超过 5 percentage points；
- B-cubed F1 不低于基线；
- attribution processing latency 从 job 入队到首次 `confirmed` / `tentative` / `unknown` 决策计时，p95 < 500 ms；candidate 的后续确认/过期等待另计，不纳入该实时处理闸门；
- 以 1× 音频速率回放全部 evaluation sessions 时，队列峰值不超过容量的 50%，结束后 2 秒内清空；
- 现有字幕、翻译、cue polling 和 web asset 测试保持通过。

AS-Norm/中心化不通过上述闸门时保持关闭，不阻止 raw-cosine 路径发布。

## 2. CREMA-D Mini Benchmark

### 2.1 数据

首批只使用 CREMA-D，以最快获得可自动推导的 ground truth：

- 固定 20 位 actor；前 10 位为 calibration，后 10 位为 evaluation；
- 每位 actor 固定 12 条：neutral/happy/angry/sad 各 3 条，并按 high → medium → low → unspecified 的顺序补齐；
- actor 列表、文件列表和 split 写入版本化 manifest；
- manifest 一经提交，不能按结果替换 evaluation 样本；
- 原始音频不进 Git，数据目录进入 ignore；
- 仓库记录官方来源和 ODbL/Database Contents License。

准备脚本按文件名解析 actor、sentence、emotion、intensity。某 actor 无法满足选择规则时立即失败并列出缺项，不静默换人。

### 2.2 固定 pair 和 session

准备阶段直接生成并提交 pair/session manifest，benchmark 运行时不再随机采样。

**Pair manifest**：

- 每位 actor 生成 12 个 same-speaker cross-emotion pairs；
- 每位 actor 生成 12 个 different-speaker pairs，负例匹配 emotion/intensity，并优先匹配 sex；
- calibration/evaluation 分别 120 positive + 120 negative。

**Session manifest**：每个 split 固定 10 个 session，每个 24 utterances：

- 3 位 actor 轮流出现；
- 每位至少有一次 neutral → angry/happy 的连续段；
- 至少有一次 A → B 的快速换人；
- 至少有一次同 actor 单个极端情绪离群后回归；
- utterance 顺序、gap 和 truth actor 全部固化在 manifest。

### 2.3 指标定义

只统计有效、非人工 overlap 的 utterance：

- **false-split utterance rate**：对每个 truth actor，将其映射到占比最高的系统 ID；该 actor 被分到其他正式 ID 的 utterance 数 / 该 actor 已分配正式 ID 的 utterance 数，再按全部 utterance micro-average；
- **false-merge utterance rate**：对每个系统 ID，将其映射到占比最高的 truth actor；来自其他 truth actor 的 utterance 数 / 全部正式分配 utterance 数；
- **UNKNOWN rate**：speaker 为空的 utterance 数 / 全部有效 utterance 数；
- **color churn**：truth actor 连续两次发言且中间没有其他 actor 时，正式系统 ID 发生变化的次数 / 可比较连续对数；
- **B-cubed precision/recall/F1**：对所有非 UNKNOWN utterance 按标准定义计算；
- **processing latency**：attribution job 入队 monotonic time 到该 job 首次产生 `confirmed` / `tentative` / `unknown` 决策的时间；
- **candidate resolution latency**：tentative candidate 从首次创建到确认或过期的音频时间和墙钟时间，只做诊断，不受 500 ms 闸门约束。

报告包含 dataset manifest hash、algorithm/config fingerprint、pair EER、same p1/p5、different p95/p99、上述 session 指标和 latency p50/p95。

## 3. Attribution 顺序和生命周期

### 3.1 Job 所有权

final cue 在创建时立即从 `PcmTimeline` 复制该句 PCM，形成 job：

- cue ID；
- copied PCM bytes；
- `begin_pcm`、`end_pcm`；
- `audio_key = (end_pcm, begin_pcm, cue_id)`；
- enqueue monotonic time；
- 可选 `overlap_confirmed`，首版生产路径固定为 false，仅测试/未来 detector 可注入。

复制 PCM 避免 job 等待时 timeline 被 prune。缺少 `begin_pcm` 的 approx cue 不入队，保持 `speaker=None` 并计数；不估算边界。

### 3.2 顺序定义

首版使用**单 worker + 按 ASR final 接收顺序 FIFO**。当前 provider 的 final 是顺序事件；`audio_key` 仅用于检测异常：如果新 job 的 `end_pcm` 小于上一个已入队 job 超过 100 ms，则记录 `speaker_jobs_out_of_order`，但不等待未知的未来 final，也不回滚已经发布的 profile。

因此首版不存在并行 embedding 完成乱序。测试验证 FIFO 即 profile mutation 顺序，并验证检测到 timestamp inversion。以后若并行前向，必须新增 reorder buffer，不能直接共享 gallery。

### 3.3 Queue 和 shutdown

- bounded `asyncio.Queue`，默认容量 32；
- enqueue 不等待；满时移除最旧的 queued、尚未开始 job，将其 cue 最终决定为 UNKNOWN，并计数；
- worker 取出 job 后，该 job 不再可被 drop；
- stop 顺序：停止接收 → `queue.join()`，最多等待 2 秒 → 超时后取消 worker并把剩余 queued job 决定为 UNKNOWN；
- extractor 只在该 worker 线程调用，不并发使用；
- 测试用 `wait_for_diarization()` 等待 queue empty + active job complete。

## 4. Pooled Embedding

embedder 上层返回 `EmbeddingObservation`：normalized vector、raw/effective duration、speech ratio、window count、quality flags、profile-update eligibility。

处理顺序：

1. 20 ms 帧计算 RMS；
2. 裁掉首尾低于全段 RMS -30 dB 的连续低能量帧；
3. 不删除中间静音，避免拼接造成声学突变；
4. 有效语音 < 1.0 s 时返回 insufficient；
5. 可选 RMS gain，最大 +12 dB，峰值保护至 0.98；
6. 默认 2.5 s window / 1.25 s hop；短句单窗，尾窗 < 1.0 s 丢弃；
7. 每窗 embedding L2 normalize；
8. 默认 normalized mean 后再 normalize；medoid 作为 benchmark 选项。

`baseline` 模式关闭 1–7 的新增行为并整句单窗。默认 pooled config 只能由 calibration 选择，Ticket 的 acceptance 要求 evaluation false merge 不高于冻结基线容差，且 same p5 或 false split 至少一项改善。

## 5. Scorer 和 Profile 公式

### 5.1 Raw scorer

每个 profile 有 normalized stable center 和最多 4 个 normalized prototypes。query 对 profile 的分数固定为：

- `center_score = cosine(query, center)`；
- prototype scores 降序；
- 一个 prototype 时 `prototype_support = best`；
- 两个及以上时 `prototype_support = 0.7 * best + 0.3 * second_best`；
- `profile_score = 0.4 * center_score + 0.6 * prototype_support`。

这避免单个脏 exemplar 的纯 max 决策。所有阈值由 calibration report 写入生产配置。

### 5.2 可选 scorer

固定 population mean 和 AS-Norm 只作为额外 scorer：

- artifact JSON/NPY metadata 包含 schema version、模型文件 SHA-256、embedding dimension、预处理 fingerprint、calibration manifest hash；
- 任一字段不匹配或 std < `1e-6` 时回退 raw scorer并记录原因；
- population mean/cohort 只能来自 calibration actors 或独立数据，不能包含 evaluation actors；
- 不实现在线滑动均值。

### 5.3 Profile 更新

prototype 准入：confirmed-existing 的 raw decision score ≥ `profile_update_threshold`、margin ≥ `profile_update_margin`、effective speech ≥ 2.0 s、无 quality flags、不是 continuity-only。

- 若与某 prototype cosine ≥ `prototype_update_threshold`：该 prototype EMA alpha 0.15 更新并 normalize；
- 否则有空位时新增；
- 已满时找到互相 cosine 最高的两个 prototype，将支持数较低者视为最冗余；仅当新 query 到现有 prototypes 的最大相似度低于该冗余对相似度时替换较低支持者；
- stable center 对所有高置信准入 observation 用 alpha 0.1 EMA 并 normalize。

## 6. Assignment 状态机

结果类型：`confirmed_existing`、`confirmed_new`、`tentative`、`unknown`，携带 speaker/candidate ID、best/second score、margin、reason、allow_profile_update。

### 6.1 Existing 判决

- best score ≥ `match_threshold` 且 margin ≥ `match_margin`：confirmed existing；
- `ambiguous_low ≤ best < match_threshold`：只有 best speaker 等于 `last_speaker`、gap < 1.0 s、margin ≥ `continuity_margin` 时 continuity-confirm；不更新 profile；
- 其他情况进入 new candidate；
- 短句可以走高置信 existing 判决，但不能进入 new candidate。

### 6.2 Candidate 关联

candidate 保存最多 3 个 normalized observations、utterance count、effective duration、waiting cue IDs、created ordinal、last ordinal。

- query 与每个 candidate observation 算 cosine，candidate score 为 top-2 平均（只有一个时取一个）；
- best candidate score ≥ `candidate_match_threshold` 且相对第二 candidate margin ≥ `candidate_margin` 时加入；
- 否则创建新 candidate；
- candidate 不互相拆分或自动合并；互相不一致的 query 会成为另一个 candidate；
- TTL 为 10 个后续 attribution ordinals；超时丢弃并让 waiting cue 保持 UNKNOWN；
- 默认确认：2 句、累计 effective speech ≥ 3.0 s、candidate 内所有 pair cosine ≥ `candidate_internal_threshold`，且每句对 confirmed profiles 均低于 `new_speaker_ceiling`。

确认后获得最小从未在本会话使用过的数字 ID，并回填仍在 store 的 waiting cues。

### 6.3 ID 容量

正式 ID 在会话内**不复用、不 LRU eviction**。达到 `diarizationMaxSpeakers` 后，不再确认新 candidate；候选到期为 UNKNOWN。这样 historical remap、cannot-link 和颜色身份不会被复用破坏。会话重启后 ID 从 1 重新开始。

当前“首个短句创建 speaker 1”和 LRU eviction 测试应改为新契约：短句无历史时 UNKNOWN；容量满后新人 UNKNOWN。

## 7. Reconciliation 与 Remap

每 20 个有效 observation 或 30 秒检查 profile pair。首版不引入通用 AHC 库，使用确定性 pair merge：

- 取两边最多 4 个 prototype 的所有交叉 cosine；
- `cross_support` = 最高 3 个交叉分数的平均（不足 3 个则全部平均）；
- 合并后的所有 prototype 到 pooled medoid 的平均 cosine 为 `merged_compactness`；
- 两 profile 对任意第三 profile 的最高 profile score 记为 competitor；
- proposal 条件：`cross_support ≥ merge_threshold`、`merged_compactness ≥ merge_compactness_threshold`、`cross_support - competitor ≥ merge_margin`、不存在 cannot-link；
- proposal 连续两次 reconciliation 成立才合并；若 `cross_support ≥ merge_immediate_threshold` 可一次合并。

`overlap_confirmed` 首版没有生产 detector；接口保留，测试和未来 detector 可在同时出现的 observation 上建立双向 cannot-link。文档和 UI 不声称本次可以检测 overlap。

合并保留更早 confirmed 的 ID，重新选择最多 4 个 prototype：先取全部 prototype 的 medoid，再用 farthest-first 选择剩余代表点；重算 center，更新 `last_speaker`、candidate 引用和 proposal 状态。

CueStore 提供幂等 `remap_speaker(old, kept)`：只修改当前 speaker 等于 old 的 cue，每个实际修改 cue 推进 revision 和 seq；重复调用无修改、无 seq 增长。播放器按 cue ID 覆盖 Map，speaker 修订只改变颜色。

## 8. 配置与遥测

生产配置新增：queue capacity、window/hop/pooling、effective speech、match/ambiguity/margin、candidate、profile、merge thresholds、reconciliation interval、scorer kind/artifact path。普通 UI 仍只暴露启用开关；参数属于配置文件和 benchmark CLI。

状态增加：queue depth/drop、timestamp inversion、insufficient、confirmed/tentative/unknown、candidate created/expired/confirmed、candidate resolution latency、profile update accept/reject、reconciliation/proposal/merge/cannot-link、processing latency p50/p95、active config fingerprint。不得输出 embedding。

## 9. 最小测试集

仅实现以下高价值测试：

1. benchmark manifest 和报告可重复；
2. FIFO worker mutation、queue drop 和 2 秒 drain 契约；
3. approx cue 不入队；
4. pooled embedding 处理 insufficient 和尾窗；
5. 单个离群不建正式 ID；
6. 两个一致 candidate 确认并回填；
7. 短句无历史 UNKNOWN，容量满后新人 UNKNOWN；
8. continuity-only 不更新 profile，多 prototype 容纳两种模式；
9. cannot-link 阻止 merge；
10. 两次稳定 proposal 合并；
11. remap 幂等且 cue seq 正确；
12. 玩家对同 cue 的 speaker revision 只更新颜色；
13. 最终 benchmark 发布闸门。

## 10. Tickets 顺序

1. benchmark 与冻结基线；
2. 有序 worker/lifecycle；
3. pooled embedding 与 scorer 标定；
4. assignment 状态机、多 prototype 和 candidate；
5. reconciliation、remap、播放器修正和发布收口。

Ticket 01 与 02 可同时开始；03 依赖 01；04 依赖 01–03；05 依赖 04。固定主播 enrollment、JVS 日语专项验证和完整 overlap diarization 另开任务。
