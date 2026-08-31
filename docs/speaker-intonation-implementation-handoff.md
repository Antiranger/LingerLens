# 说话人语调鲁棒性 —— 实施方案（新会话执行）

> **先读**：`docs/speaker-intonation-postmortem-and-replan.md`（诊断与全部实测数据）。
> 本文只讲**怎么改**，不重复论证。所有结论都已实测，**不要重新推导，也不要重新调研**。
>
> 项目不是 git 仓库，没有 `git diff` 可用。改动前先手动备份要动的文件。

---

## 0. 执行前必须知道的三件事

1. **这一轮的失败原因是「阈值写在了不存在的分数尺度上」，不是算法不够复杂。**
   上一轮五张 ticket 造了一台精密状态机，但 `AssignmentConfig` 里的
   0.82 / 0.88 / 0.90 在真实数据上的可达率是 0.76% / 0.08% / 0.08%，
   于是新说话人无法确认（74% UNKNOWN）、profile 终身停在 1 条观测。
   **本轮的主要工作是删代码和改常量，不是加功能。**

2. **顺序不能改。** Step 1–3 都会改变分数尺度，所以**阈值标定必须放在它们全部完成之后**，
   一次性做完（Step 4）。先标定会白做两遍。
   （这与上一轮对话里我说的「先修 bug」略有调整：结构性 bug 在 Step 3 修，
   阈值标定挪到 Step 4，因为阈值依赖尺度。）

3. **明确不要做**（每一条都有实测否定依据，见 postmortem §5.6–5.7）：
   ERes2Net large / ERes2NetV2（19x / 6x CPU，且 large 破坏 500ms 延迟门）、
   AS-Norm（负收益）、多子窗 pooling（负收益）、pyannote embedding（JVS 上最差）、
   GPU（当前构建无 CUDA provider，也不需要）。

---

## Step 0 —— 准备与基线快照（30 分钟）

### 0.1 取日语评测数据

```bash
cd prototype/hls-companion
python scripts/download-japanese-speaker-data.py          # JVNV，约 200MB
python scripts/download-japanese-speaker-data.py --jvs    # JVS，3.5GB，见下
```

**JVS 大概率仍会失败**，报 `Quota exceeded`。这是 Google 对该 3.5GB 公开单文件的
每日下载上限（已确诊：同账号的 sample 文件可以正常下载，只有大 zip 被限），
约 24 小时滚动重置，全网无镜像。**失败就继续，用 JVNV 做本轮验收**，
JVS 拿到后再补一轮复核即可，不要卡在这一步。

### 0.2 记录基线（必须，否则无法证明改动有效）

在动任何代码之前，跑一次并把数字写进 `.planning/` 的 progress 文件：

```bash
npm run test:hls-companion    # 记录当前通过状态
```

用 postmortem 附录里的 `ja_bench.py` / `ja_accum.py` 跑一遍当前模型，
记录：日语 3s EER、同性别 3s EER、累积 n=1/n=2 EER。这是 Step 5 验收的对照。

---

## Step 1 —— 换 checkpoint（15 分钟，两行）

**改 `scripts/download-diar-models.py`：**

```python
MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/"
    "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
)
MODEL_SHA256 = "aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2"
```

（实测值：28,281,164 bytes。上面的 SHA-256 已在本机核对过。）

**改 `vendor/models/diarization/NOTICE.md`**：源模型名改为
`3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx`，
上游改为 `iic/speech_campplus_sv_zh_en_16k-common_advanced`（ModelScope），
许可仍是 Apache-2.0。

然后 `python scripts/download-diar-models.py --force` 重新拉取。

**为什么**：同架构、同磁盘（27MB）、**更省内存（51M vs 73M）**、同延迟（19.5ms vs 19.9ms），
日语上 3s -17%、同性别 -19%、full -23%。接口完全相同（3d-speaker 框架、192 维、
`normalize_samples=1`、`feature_normalize_type=global-mean`），`diar_sherpa.py` 一行都不用改。

**验证**：`python scripts/diar-spike.py` 能跑通；维度仍是 192。

---

## Step 2 —— 删掉负收益的输入处理（1 小时，纯删代码）

文件：`companion/speaker_attribution.py`

### 2.1 删除

| 删除对象 | 当前位置 | 实测依据 |
|---|---|---|
| `_trim_pcm_edges` | `:113-132` | 边缘裁剪 20.83%→21.82%，内部裁剪→27.48%，中性偏负 |
| `_apply_pcm_gain` | `:135-151` | **no-op**：`max(1.0, requested)` 导致 0/240 片段被衰减；且两侧归一化实测只有 0.05% 收益 |
| `_pool_vectors` | `:154-167` | 见下 |
| `observe_embedding` 的 `mode=="pooled"` 整个分支 | `:186-214` | pooling 实测 20.83%→32.51%（1.5s 窗）→38.64%（1.0s 窗），**严重负收益**；且窗口 2.5s > 中位片段，66% 样本本就是单窗 |
| `PooledEmbeddingConfig` 里除 `minimum_effective_seconds` / `profile_minimum_seconds` 外的全部字段 | `:65-78` | 已无引用 |

### 2.2 `observe_embedding` 改成这样

```python
def observe_embedding(pcm, embed, *, config=None, sample_rate=16_000):
    """One whole-utterance embedding. No trimming, no gain, no windowing.

    Measured (docs/speaker-intonation-postmortem-and-replan.md §R3): every form
    of input preprocessing we tried was neutral or actively harmful, because EER
    on this task is dominated by how many seconds of speech reach the model.
    """
    config = config or PooledEmbeddingConfig()
    duration = len(pcm) / (sample_rate * 2)
    if not pcm or duration < config.minimum_effective_seconds:
        return EmbeddingObservation(None, duration, duration, 1.0, 0, ("insufficient",), False)
    return EmbeddingObservation(
        normalize_vector(embed(pcm)), duration, duration, 1.0, 1, (),
        duration >= config.profile_minimum_seconds,
    )
```

### 2.3 连带清理

- `subtitle_pipeline.py:370` 的 `diarization_embedding_config` 保留（字段变少了而已）。
- `benchmarks/crema_mini.py` 的 `POOLED_RAW_ALGORITHM`（`:22-28`）与
  `real_pooled_raw_report`（`:570`）引用了 pooled 路径 —— 改成调用新的
  `observe_embedding`，并把 `"embedding"` 字段改为 `"whole-utterance"`。
- `tests/test_speaker_attribution.py` 里所有 pooled/trim/gain 相关用例删除。

### 2.4 加 centering（唯一验证有效的打分改动）

在 `SpeakerGallery` 里加一个运行均值，**在打分时**对查询向量和 profile 中心同时做中心化：

```python
# in __init__
self._mean = None          # running mean of all observed raw vectors
self._mean_count = 0
self.centering_warmup = 8  # below this, score on raw cosine

def _observe_mean(self, vector):
    self._mean_count += 1
    if self._mean is None:
        self._mean = list(vector)
    else:
        k = 1.0 / self._mean_count
        self._mean = [(1 - k) * m + k * v for m, v in zip(self._mean, vector)]

def _centered(self, vector):
    if self._mean is None or self._mean_count < self.centering_warmup:
        return list(vector)
    return normalize_vector([v - m for v, m in zip(vector, self._mean)])
```

> ⚠️ **关键设计约束，写错会静默劣化**：`_Profile` 必须存**原始（未中心化）**向量，
> 打分时再对 profile 中心和查询同时做 `_centered()`。
> 如果把中心化后的向量存进 profile，随着均值漂移，旧向量就是用旧均值中心化的，
> 与新查询不在同一坐标系里，相似度会缓慢失真。**存原始，比时中心化。**

**实测依据**：英语 20.83%→17.48%；日语 11.00%→7.54%。

**验证**：`ja_accum.py` 里 raw/centered 两列的差值应能复现（日语 n=1：11.00% → 7.54%）。

---

## Step 3 —— 拆掉 profile 累积门（1 小时，本轮最关键的一处）

文件：`companion/speaker_attribution.py`

### 3.1 问题

`assign()` 的 `:432-438`：

```python
allow = (eligible and not flags and effective >= 2.0
         and best_score >= self.config.profile_update_threshold      # 0.88
         and margin >= self.config.profile_update_margin)            # 0.10
```

`0.88` 的真同人可达率是 **0.08%**。结果：**每个 profile 建立后终身停在 1 条观测**。
而实测跨句累积是全系统杠杆最大的机制（日语 centered：n=1 7.54% → n=2 **1.89%**，
n=4 0.18%）。这个 if 把它整个关掉了。

### 3.2 改法

**(a) `_Profile` 改成可无上限累积：**

```python
@dataclasses.dataclass
class _Profile:
    vector_sum: list[float]     # running sum of RAW observation vectors
    count: int                  # how many observations fed it
    prototypes: list[list[float]]   # keep <=4 raw multi-mode prototypes
    support: list[int]

    @property
    def center(self) -> list[float]:
        return normalize_vector([v / self.count for v in self.vector_sum])
```

（`center` 由 `vector_sum/count` 派生，删掉原来的 EMA `center` 字段和
`_update_profile` 里的 `0.9*old + 0.1*new`。EMA 会让早期证据指数衰减，
与「无上限累积」的目标相反。）

**(b) 相似度门整个去掉，只保留质量门：**

```python
allow = (not flags) and effective >= 1.0
if allow:
    self._update_profile(best_speaker, vector)   # vector 是 RAW 向量
```

即：**凡是判为 `confirmed_existing` 的观测，一律并入 profile**，
只用「无质量 flag + 有效语音 ≥1.0s」做准入。理由：相似度门在这里是循环论证 ——
它要求「已经很像才允许变得更像」，于是永远不会变像。误并入的风险由
Step 4 的迟滞阈值和 reconciliation 承担，不该由累积门承担。

**(c) 删掉这几个已无意义的常量**：`profile_update_threshold`、`profile_update_margin`。
`prototype_update_threshold` 保留但要在 Step 4 重标（原值 0.90 可达率 0.08%）。

**(d) `merge_immediate_threshold = 0.97` 是死代码**（可达率 0.00%）。
要么删掉整条 immediate 分支，要么在 Step 4 给它一个真实可达的值。**建议删掉**，
`reconcile` 只保留「提案 → 下一轮确认」这一条路径，逻辑更少。

### 3.3 测试

`tests/test_speaker_attribution.py` 新增：
- 同一说话人连续 5 条观测后，`profile.count == 5`（当前实现会是 1，这是回归防线）
- profile 累积后，对同说话人第 6 条的得分**高于**只有 1 条观测时的得分

---

## Step 4 —— 一次性标定全部阈值（半天，本轮唯一需要动脑的一步）

到这里分数尺度才固定下来（新 checkpoint + 无预处理 + centering）。

### 4.1 先量尺度

写 `scripts/calibrate-thresholds.py`，在**日语 calibration split**（JVNV 的一半说话人；
JVS 到手后换成 JVS 的 50 个 actor）上输出：

- 同人（跨情绪）centered cosine 的 p1 / p5 / p50
- 异人 centered cosine 的 p95 / p99
- EER 与 EER 阈值

**已实测的参考值**（JVNV，3s，`zh_en_advanced`，centered）：
同人 p5 **+0.128**、p50 **+0.350**、异人 p95 **+0.163**、**EER 阈值 +0.143**。
新脚本跑出来应该在这个附近；差太远说明前面某步改错了。

### 4.2 provisional 值（先用这一组，再用脚本确认）

| 常量 | 旧值 | **新值** | 依据 |
|---|---:|---:|---|
| `match_threshold` | 0.82 | **0.20** | EER 阈值 0.143 之上留裕度 |
| `ambiguous_low` | 0.68 | **0.08** | 迟滞下界，落在同人 p5 附近 |
| `match_margin` | 0.08 | 0.05 | 尺度整体压缩了，margin 同比缩小 |
| `continuity_margin` | 0.05 | 0.03 | 同上 |
| `new_speaker_ceiling` | 0.68 | **0.20** | 与 match_threshold 同量级 |
| `candidate_match_threshold` | 0.78 | **0.10** | 同一新人的两条要能进同一 candidate |
| `candidate_internal_threshold` | 0.80 | **0.10** | **74% UNKNOWN 的直接来源，必须改** |
| `candidate_margin` | 0.08 | 0.05 | |
| `prototype_update_threshold` | 0.90 | **0.30** | 落在同人 p50 附近 |
| `merge_threshold` | 0.90 | **0.45** | profile 中心之间的比较，比单句更集中 |
| `merge_compactness_threshold` | 0.88 | 0.40 | |
| `merge_margin` | 0.08 | 0.05 | |
| `merge_immediate_threshold` | 0.97 | *删除* | 死代码 |

> 这组数与我在英语数据上做的小网格搜索结果一致
> （t_match=0.15 / t_low=0.05 / t_ceiling=0.20 / t_cand=0.10 / t_merge=0.45），
> 可以作为交叉印证。

### 4.3 标定纪律（上一轮就是栽在这里）

- **只在 calibration split 上选参**，evaluation split 只在最后报告一次。
- 选择目标必须**同时惩罚** false_split、false_merge、UNKNOWN，并奖励 B³。
  只优化其中一个会被「全合成一个人」或「全部 UNKNOWN」刷分。
- **UNKNOWN 必须有绝对上限（建议 10%）**。上一轮交付版本正是用 74% 的 UNKNOWN
  换到了好看的 split/merge。
- 选中的参数和搜索指纹写进 report。

### 4.4 同时修 `providers/config.py`

`diarizationThreshold` 默认 `0.4`（`:100`）已经不在新尺度上，改成与
`match_threshold` 一致的新值，并在注释里写明「centered cosine 尺度，
由 scripts/calibrate-thresholds.py 标定，勿手改」。

---

## Step 5 —— turn 级证据合并（1 天，收益最大的一步）

文件：`companion/subtitle_pipeline.py`

### 5.1 现状

`_enqueue_speaker_job`（`:919`）为**每个 final cue** 立刻入队一个 `_SpeakerJob`，
一句一次归属决策。直播里一个 turn 通常被 ASR 切成 3–5 句，每句 2–3 秒。

### 5.2 目标

把 gap < 0.8s 的连续 final 合并成**一个证据块**再送去 embed，一次决策给这一批 cue 统一上色。

**实测依据**：日语 3s → 6.5s，EER 10.67% → 6.53%；这一步比换模型的收益更大，
且与换模型可叠加（`zh_en_advanced` @6.5s = 6.53%，比 ERes2Net large @3s 的 7.34% 还好，
而且快 10 倍）。

### 5.3 改法

**(a) `_SpeakerJob` 改为承载多个 cue：**

```python
@dataclasses.dataclass(frozen=True)
class _SpeakerJob:
    cue_ids: tuple[int, ...]        # was: cue_id: int
    pcm: bytes
    begin_pcm: float
    end_pcm: float
    audio_key: tuple[float, float, int]
    enqueued_monotonic: float
    overlap_confirmed: bool = False
```

`_attribute_speaker_job` 里所有 `job.cue_id` 的用法改为遍历 `job.cue_ids`，
一个决策写多条 cue（`store.update(cue_id, speaker=...)` 本来就是逐条的）。
`_finalize_speaker_unknown` 同理。

**(b) 在 pipeline 上加一个待合并缓冲：**

```python
self._turn_buffer: list[_PendingFinal] = []
self._turn_cue_ids: list[int] = []
TURN_MAX_GAP_SECONDS = 0.8      # 超过就认为换 turn
TURN_TARGET_SECONDS = 4.0       # 攒够就先发，不要无限等
```

`_enqueue_speaker_job(cue_id, pending)` 改为 `_buffer_speaker_evidence(cue_id, pending)`：

1. 若缓冲非空且 `pending.begin_pcm - buffer[-1].end_pcm >= TURN_MAX_GAP_SECONDS`
   → 先 `_flush_turn()`，再把当前 pending 放入新缓冲；
2. 否则加入缓冲；
3. 若缓冲累计时长 `>= TURN_TARGET_SECONDS` → `_flush_turn()`。

`_flush_turn()` 用 `self._pcm_timeline.slice(buffer[0].begin_pcm, buffer[-1].end_pcm)`
取整段 PCM，构造一个携带全部 cue_id 的 job 入队，清空缓冲。

**(c) 必须处理的四个边界**（漏掉任何一个都会出线上问题）：

1. **停止时 flush**：`_stop_diarization_worker`（`:1110`）在停止接收之前先
   `_flush_turn()`，否则最后一个 turn 的 cue 永远拿不到颜色。
2. **超时 flush**：如果主播停说话，缓冲会一直挂着。加一个基于 `_pcm_offset` 的检查 ——
   当 `self._pcm_offset - buffer[-1].end_pcm >= TURN_MAX_GAP_SECONDS` 时也要 flush。
   建议挂在 `_ingest_pcm_chunk`（`:603`）里顺带检查，不要新开定时器。
3. **approximate cue**：`pending.begin_pcm is None` 的分支（`:921-926`）保持原样 ——
   它没有可信边界，不能进缓冲，直接记 unknown。它同时应当**打断**当前 turn（先 flush）。
4. **队列丢弃路径**：`:949-955` 的 drop 分支现在要把 `dropped.cue_ids` 全部标 unknown。

**(d) 延迟影响，必须实测**

这会让一个 turn 里最早那句的上色延后最多 `TURN_TARGET_SECONDS`（4s 音频）+ 推理时间。
**字幕本身不受影响**（cue 立即显示，只有颜色晚到），所以这是「颜色晚一点」换
「颜色不会错」，方向是对的。但要实测 p95 并记录。

若判断 4s 太长，再考虑二阶段方案：首句先用单句做一次**临时**决策立刻上色，
turn 结束后用合并证据**改判**。代价是推理次数翻倍。**先做简单版，测了再说，不要一上来就做二阶段。**

### 5.4 测试

`tests/test_subtitle_pipeline.py` 新增：
- 三条 gap<0.8s 的 final → 只产生 **1** 个 job，且 `cue_ids` 含三个 id
- gap≥0.8s → 产生 2 个 job
- 累计 ≥4s → 提前 flush
- `stop()` 会 flush 未满的缓冲
- 一次决策会写入全部 `cue_ids` 的 speaker

---

## Step 6 —— benchmark 两条自检（半天，不能省）

没有这个，Step 1–5 是否生效无法判断，而现有 benchmark 已被证明**连要修的 bug 都复现不了**
（legacy 在 CREMA-D 上 false_split 0.004 / false_merge 0.662，是过度合并，
与用户报告的过度拆分方向相反）。

### 6.1 `benchmarks/ceiling.py` —— 先算天花板

对每个 session 跑 oracle-k 离线层次聚类（average linkage，k=真实说话人数），
输出 B³ F1 上界。**任何在线算法都不可能超过它。**

已实测：现有 CREMA-D benchmark 的天花板是 **B³ 0.657–0.692**，
而上一轮交付的 0.598 已经是天花板的 87% —— 那个 release gate 数学上无解，
五张 ticket 是在追一个可以事先证明不可达的目标。**天花板计算约 30 行，本该是第一件事。**

规则：**release gate 的目标必须表述为「天花板的百分比」**（建议 ≥0.85×ceiling），
不接受绝对值 gate。

### 6.2 症状准入自检 —— benchmark 必须先复现要修的 bug

在出任何报告之前先跑 legacy baseline，若
`false_split_utterance_rate` 不显著高于 `false_merge_utterance_rate`
→ **fail fast，拒绝出报告**，并提示「该数据集复现的不是生产症状，用它调优有害」。

现有 CREMA-D 数据集会在这一步被直接拒掉。**这是它应有的结局，不要为了让它通过而放宽这条检查。**

### 6.3 接日语数据

新增 `benchmarks/jvnv.py`，复用 `crema_mini.py` 的 `session_metrics` 与
`evaluate_release_gate`，数据源换成 `.benchmark-data/jvnv-16k`。
session 构造要贴近直播（postmortem §R6 实测：每人连续发言从 2 条提到 8 条，
B³ 从 0.595 涨到 0.822，纯结构差异）：

- 每人连续发言 4–8 条（**不要 2 条**）
- 每 session ≥5 分钟音频
- 说话人 2–3 人

**回归 fixture 用 `anger ↔ sad`**：实测这是最伤的语调组合（当前模型 17.7%，
前三名全部含 anger），正是「主播突然激动又冷静下来」的场景。

---

## Step 7 —— 验收

### 7.1 必须全绿

```bash
npm run test:hls-companion
npm test
```

### 7.2 效果验收（在日语数据上，与 Step 0.2 的基线对比）

| 指标 | 基线（当前） | 目标 |
|---|---:|---:|
| 日语 3s EER | 10.67% | ≤ 9.0%（换模型 + centering） |
| 日语同性别 3s EER | 19.33% | ≤ 16% |
| turn 合并后有效片段时长 p50 | ~2.5s | ≥ 5s |
| session B³ F1 | 记录 | ≥ 0.85 × ceiling |
| **UNKNOWN 率** | 74%（上一轮） | **< 10%（硬上限）** |
| profile 平均观测数 | 1（bug） | ≥ 3 |
| 归属延迟 p95 | 记录 | < 500ms（不含 turn 缓冲等待，缓冲时长单独报） |

**`profile 平均观测数 ≥ 3` 是最能说明 Step 3 是否真的生效的一个数**，务必打点上报。

### 7.3 诚实收口

如果 gate 没过，ticket 状态写「实现完成但 release blocked」，
**不要用高 UNKNOWN 或全合并规避**。上一轮就是这么把失败包装成 0.598 的。

---

## 陷阱清单

1. **centering 必须存原始向量、比时中心化**（Step 2.4 的警告）。存中心化向量会随均值漂移静默失真。
2. **阈值标定必须在 Step 1–3 全部完成之后**，否则要重做两遍。
3. **turn 合并的四个边界**（停止 flush / 超时 flush / approximate cue 打断 / 丢弃路径）
   漏一个就会有 cue 永远不上色。
4. **`_pool_vectors` 删干净**，`benchmarks/crema_mini.py` 里还有引用。
5. **LSP 缓存不可信**：上一轮出现过 workspace diagnostics 误报
   `CueStore.remap_speaker` 不存在。改完跑**单文件** diagnostics，别看 workspace 缓存。
6. **`.benchmark-data/` 与 `benchmark-results/` 不得提交**，JVNV/JVS 音频不得再分发
   （JVS 许可：学术与非商业研究、个人使用，禁止再分发）。
7. **不要把 Common Voice 拉进来**：其条款明文禁止用于确定说话人身份。

---

## 工作量估计

| Step | 内容 | 估时 |
|---|---|---|
| 0 | 数据 + 基线快照 | 0.5h |
| 1 | 换 checkpoint | 0.25h |
| 2 | 删预处理 + 加 centering | 1h |
| 3 | 拆 profile 累积门 | 1h |
| 4 | 一次性标定全部阈值 | 4h |
| 5 | turn 级证据合并 | 8h |
| 6 | benchmark 天花板 + 症状自检 + 日语接入 | 4h |
| 7 | 验收与收口 | 2h |
| | **合计** | **约 2.5 天** |

Step 1–3 加起来只有 2.25 小时，而且大部分是删代码 —— **先把这三步做完跑一遍**，
UNKNOWN 率应该立刻从 74% 掉到 10% 以下。如果没有，说明前面某处理解有偏差，
先停下来查，不要继续往 Step 4 走。
