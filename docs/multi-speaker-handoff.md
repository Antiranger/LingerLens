# LagLingo 多说话人 + 每人独立上下文 + 语音直译 交付文档

> 生成时间：2026-08-30
> 工作区：`F:/Projects/LagLingo`，实现在 `prototype/hls-companion/`
> 前一轮的成果与依据：`docs/subtitle-live-fix-plan-v2.md`（**必读 §9**）
>
> 本文给下一个会话。前置结论都带实测证据；**标注"未验证"的地方不要当成事实**。

---

## 0. 先读这一段：当前状态是好的，不要推倒重来

上一轮把直播字幕从「完全不可用」修到了「用户实测基本没问题」。真实直播运行态：

```
timingSourceCounts  = {asr: 74, vad: 0, approx: 0}   ← 74/74 用服务端精确时间戳
translationFailures = 0    avgTranslationLatencyMs = 299.5
forcedCommits = 0   unjoinedFinals = 0   pcmDropped = 0   asrReconnects = 0
readyLagP50/P95 = 1.33s / 2.49s
mediaAnchor: C=-0.2  frozen  spread=0.2  drift=0.3
```

**这些不变量是新功能的验收底线，任何改动之后都必须仍然成立。**
`scripts/pipeline-e2e.py` 会逐条检查它们，改完跑一遍。

当前生效配置（`runtime/providers.json`）：

| 项 | 值 | 为什么 |
|---|---|---|
| ASR | `qwen3-asr-flash-realtime`，`silenceDurationMs=400` | 实测 200ms 与 400ms 分句结果逐条相同，400 是 Qwen VAD 有效下限；600 会把 3 个巨块和 45 个字符吞掉 |
| 翻译 | `qwen-mt-turbo`（阿里云 MaaS 端点），4 个并发 worker | p50 282ms |
| `maxUtteranceSeconds` | `0`（关闭强制 commit） | 强制 commit 会切碎词并产出无结束边界的 cue |
| `translation.fallback` | `[]` | 那条腿没配 key，留着只会在主 provider 抖动时把流量导向 100% 失败 |
| publish delay | 3s（服务端） | 其余 ~12s 延迟来自播放器 `liveSyncDurationCount`，那才是能吸收卡顿的缓冲 |

---

## 1. 用户要的三件事

1. **多人声识别**：区分不同说话人，需要接新 API。
2. **每个说话人独立的翻译上下文**：翻译某人的一句话时，带上**这个人自己**之前的 5/10 句，让译文更连贯。
3. **语音直译**：有的 API 直接就能日语→中文，这种情况根本不需要翻译模型；
   **用户明确要求这种模式要在 UI 里单独列出来。**

---

## 2. 已查证的 API 事实（这一节决定整个方案，先看完）

### 2.1 ⚠️ 阿里云百炼**所有实时模型都不支持说话人分离**

两处独立来源确认：

- [ASR 模型能力矩阵](https://help.aliyun.com/en/model-studio/asr-model/)：
  所有 streaming 模型（`qwen3-asr-flash-realtime`、`fun-asr-realtime`、
  `qwen-audio-3.0-asr-flash-streaming`…）的说话人分离一栏**全部是 Unsupported**。
- [Gummy 实时 API 文档](https://help.aliyun.com/zh/model-studio/real-time-speech-translation)：
  明确写「不支持说话人分离」。

**支持分离的只有离线文件转写**：`qwen-audio-3.0-asr-flash-filetrans`、`fun-asr`、
以及 [Paraformer 录音文件识别](https://help.aliyun.com/zh/model-studio/paraformer-recorded-speech-recognition-api-reference/)
（`diarization_enabled=true` + `speaker_count` 2~100，仅单声道，结果里每句带 `speaker_id`）。
**离线转写用不了 —— 我们是直播。**

> 有一个待验证的线索：Gummy 实时响应的 `words[]` 数组里**有 `speaker_id` 字段，但可空**。
> 很可能只是和离线共用 schema、实时不填。**必须实测确认，不要基于字段存在就假设可用。**

**结论：多说话人这件事，不能靠现在这家供应商做。**

### 2.2 ✅ Gummy 可以做实时语音直译，而且更便宜

[`gummy-realtime-v1`](https://help.aliyun.com/zh/model-studio/real-time-websocket-api-1)：

- **日语 → 中文明确支持**（ja 源支持 en/zh/vi/fr/it/de/es/th）
- **同时输出原文和译文**：`output.transcription` + `output.translations[]`
- 每句带 `begin_time` / `end_time`，单位毫秒，**从音频起点计**（正是我们要的坐标系）
- 有 `sentence_end` 布尔和 `fixed` 字段（中间结果是否还会变）
- 关键参数：`source_language`、`transcription_enabled`、`translation_enabled`、
  `translation_target_languages`（数组）、`max_end_silence`（200~6000ms，默认 800）
- **价格 0.00015 元/秒**

对比现在：`qwen3-asr-flash-realtime` 是 **0.00033 元/秒**，**再加**一次 qwen-mt-turbo 调用。
**Gummy 单价不到一半，还省掉整个翻译环节。**

协议是 DashScope 的 task 式（run-task / finish-task + 二进制音频帧），
跟 `providers/asr_dashscope_task.py` 同一套，不是 Qwen realtime 那套。

> 注意 `max_end_silence` 默认 800ms。上一轮实测证明 600ms 会把长句吞掉、400ms 明显更好，
> **接 Gummy 时一定要把它设到 400 并重跑分句对比**，不要用默认值。

### 2.3 实时说话人分离要换供应商（或本地做）

| 方案 | 说明 | 主要风险 |
|---|---|---|
| [AssemblyAI](https://www.assemblyai.com/blog/best-api-models-for-real-time-speech-recognition-and-transcription) | 流式分离最多 10 人，**会随上下文回溯修正之前的标签**；$0.45/hr 起 + 分离 $0.12/hr | 日语实时分离质量未知 |
| ElevenLabs Scribe v2 Realtime | 号称 30 语言、最多 32 人、<150ms | 未验证 |
| Deepgram / Gladia / Speechmatics / Azure | 都有实时分离 | 强项基本都在英语 |
| 本地分离（FunASR `campplus`、pyannote、3D-Speaker） | 在我们已有的 16k 单声道 PCM 上跑，不换 ASR | 要自己做说话人聚类和与 VAD 段的对齐；CPU 开销 |

**最大的未知数是日语**。上面几家的 benchmark 基本都是英语会话。
源语言是日语，**必须先用真实直播音频实测，再决定接哪家**。

### 2.4 直播场景本身的风险

上一轮实测发现：**30 秒的游戏 BGM 被 VAD 误判成人声**（ASR 正确地只吐了一个 `。`，随后被过滤）。
说话人分离面对混了游戏音/BGM 的单声道直播音频，**误分离的概率比会议录音高得多**。
这不是理论担忧，是这条流上已经观测到的现象。

---

## 3. 建议的方案与顺序

三件事的难度和确定性差别很大，**不要打包做**：

| | 确定性 | 工作量 | 建议 |
|---|---|---|---|
| 语音直译（Gummy） | 高（文档已确认支持 ja→zh） | 中 | **先做这个** |
| 每人独立上下文 | 高（纯本地重构） | 小 | 第二做，可先不依赖真实 speaker |
| 多说话人识别 | **低**（要换供应商 + 日语未验证） | 大 | 最后做，先 spike |

### 阶段 A：语音直译（一段式）+ UI 模式选择

这是唯一一件现在就能确定做成的事，而且**省钱**。

### 阶段 B：把上下文改成按说话人分桶

即使还没有真实的 speaker id，也可以先把数据结构和翻译请求改好，
用一个固定的 `speaker=None` 桶跑通，行为与现在完全一致。
等阶段 C 有了真 speaker id，直接就生效。

### 阶段 C：多说话人

**先 spike，再写代码。** 见 §6。

---

## 4. 代码改动设计

### 4.1 `providers/base.py` —— 扩展两个契约

```python
@dataclasses.dataclass(frozen=True)
class ASRCapabilities:
    ...
    manual_commit: bool = False
    speech_translation: bool = False   # 新增：ASR 自己就能出译文
    diarization: bool = False          # 新增：事件里带 speaker

@dataclasses.dataclass
class ASREvent:
    ...
    item_id: str | None = None
    translation: str | None = None     # 新增：语音直译的译文
    speaker: str | None = None         # 新增：说话人标识
```

**`speaker` 的语义必须写进注释**：它是**供应商在本次会话内**的说话人标识，
跨会话（ASR 重连）不保证稳定；有的供应商还会**回溯修正**（见 §7 陷阱 1）。

### 4.2 新 provider：`providers/asr_gummy_realtime.py`

照 `asr_dashscope_task.py` 的 task 式协议写（run-task / 二进制帧 / finish-task），
`kind = "dashscope-gummy-realtime"`：

- `capabilities`：`speech_translation=True`、`diarization=False`
- `run-task` 参数：`source_language`、`transcription_enabled=True`、
  `translation_enabled=True`、`translation_target_languages=[target]`、
  `sample_rate=16000`、`format="pcm"`、`max_end_silence=400`
- `_map_event`：
  - `output.transcription.sentence_end == True` → `ASREvent("final", text=..., begin_pcm=begin_time/1000, end_pcm=end_time/1000, item_id=sentence_id)`
  - 从同一帧的 `output.translations[]` 里挑 `lang == target` 的那条，填进 `translation`
  - 未 `sentence_end` 的 → `interim`（目前 pipeline 不消费，但要正确映射）

**注意**：Gummy 直接在句子上给 `begin_time`/`end_time`，
**不需要 Qwen realtime 那套 `item_id` 关联 VAD 事件的逻辑** —— 它更简单。
但仍然要走 `_server_to_pipeline()` 换算，因为那些毫秒是「服务端收到的音频」的偏移，
和我们的 `_pcm_offset` 在丢包/重连时会分叉（见 `subtitle_pipeline.py::_server_to_pipeline` 的注释）。

### 4.3 `subtitle_pipeline.py` —— 直译短路

`_PendingFinal` 加 `translation: str | None` 和 `speaker: str | None`。

`_materialize_cue()` 里：

```python
if pending.translation:
    # ASR 已经给了译文，不走翻译队列，直接终态。
    cue = self.store.add(..., zh=pending.translation, state="done", speaker=pending.speaker)
    self._record_ready_lag(cue.id)          # readyLag 仍然要记，UI 预算要用
    self.context_for(pending.speaker).add(pending.text, pending.translation, self.wall_clock())
else:
    ... 现有的 state="src" + _enqueue_translation 路径
```

**不要**在直译模式下还创建翻译 worker。`start()` 里的条件改成
`if self.translation_provider is not None and not asr_caps.speech_translation`。

`status()` 增加 `speechTranslationCues`，用来确认这条路径真的在跑。

### 4.4 每说话人独立上下文

`context_manager.py` 现在是**单个全局** `RollingContext`。改成分桶：

```python
class SpeakerContexts:
    """Per-speaker rolling translation context.

    Keeping one shared history across speakers actively hurts: the model sees
    another person's turn as if it were the same speaker's prior sentence and
    resolves pronouns/ellipsis to the wrong subject.
    """
    def __init__(self, context_pairs=6, context_seconds=90.0, max_speakers=8):
        ...
    def for_speaker(self, speaker: str | None) -> RollingContext: ...
```

- `speaker=None`（无分离能力时）走一个默认桶 → 行为与现在完全一致
- `max_speakers` 用 LRU 淘汰，防止误分离刷出几十个假说话人把内存吃掉
- `_translation_worker` 里 `self.context.history(...)` → `self.contexts.for_speaker(cue.speaker).history(...)`
- 写回也要写进同一个桶

**用户明确要 5/10 句可配**：`subtitle.contextPairs` 已经存在（默认 6），
按说话人分桶后它的含义变成「每人各自 N 句」，UI 上要把文案改清楚。

### 4.5 `subtitle_store.py`

`Cue` 加 `speaker: str | None = None`，`to_dict()` 里输出 `speaker`。
`update()` 的 `allowed` 集合会自动带上它（它是 dataclass field），
所以供应商回溯修正 speaker 时可以直接 `store.update(id, speaker=...)`，
seq 会自增，客户端 `afterSeq` 轮询天然拿到修正 —— **协议不用改**。

### 4.6 UI：把模式单独列出来（用户明确要求）

当前 `providers/config.py` 的 `model_settings_view()` / `update_model_settings()`
**四处都按 kind 硬编码找 provider**：

```
config.py:182  _provider_by_kind(persisted["asr"],         "dashscope-qwen-realtime")
config.py:190  _provider_by_kind(persisted["translation"], "openai-compatible")
config.py:216  _provider_by_kind(config["asr"],            "dashscope-qwen-realtime")
config.py:217  _provider_by_kind(config["translation"],    "openai-compatible")
```

`_provider_by_kind()` 找不到就 **抛 `ValueError`**，所以两条路都会炸：

- 切到直译模式、没有翻译 provider → `/api/model-settings` 直接 500
- Gummy 是**新的 ASR kind**（`dashscope-gummy-realtime`）→ 同样找不到，同样炸

`update_model_settings()` 还有第二个坑：它无条件执行
`persisted["translation"]["active"] = translation["id"]`，
也就是**用户在设置对话框里点一次保存，就会把 active 强行改回那个 openai-compatible provider**，
静默覆盖直译模式的选择。

**改法**：`_provider_by_kind` → 按 `section["active"]` 找当前 provider，
找不到返回 `None` 而不是抛；`update_model_settings` 只更新它实际编辑的那个 provider，
不要强行改写 `active`。

`tests/test_server_providers.py:67` 和 `:93` 也断言了 `openai-compatible`，要一起更新。

建议在 `subtitle` 配置里引入显式模式：

```json
"subtitle": {
  "mode": "asr+mt",        // 或 "speech-translation"
  ...
}
```

设置对话框按模式切换：

- **模式 A｜识别 + 翻译（两段式）**：显示 ASR 端点/密钥 + 翻译端点/模型/密钥/温度/超时
- **模式 B｜语音直译（一段式）**：只显示语音直译端点/密钥；
  **明确标注「此模式不调用翻译模型」**，把翻译那一整块灰掉或隐藏
- 诊断面板上 `translationProviderId` 在模式 B 下显示 `—（语音直译）`

`model_settings_view()` 改成**按 `translation.active` 找 provider**，
而不是按 kind 硬找；找不到时返回 `None` 而不是抛异常。

---

## 5. 阶段 A 的验收

```bash
cd F:/Projects/LagLingo
npm run test:hls-companion                       # 全绿
python prototype/hls-companion/scripts/pipeline-e2e.py --audio <clip>.ts
```

`pipeline-e2e.py` 的不变量必须仍然全过，另外确认：

- `timingSourceCounts.asr` 仍 > 95%（Gummy 的 begin/end_time 换算正确）
- `translationAttempts == 0`（真的没调翻译模型）
- `speechTranslationCues == cue 总数`
- 每个 cue 都有非空 `zh`
- 成本对比：`estimatedCostCny` 应约为原来 ASR 部分的 45%，且不再有翻译调用

---

## 6. 阶段 C 之前必须先做的 spike

**不要先写 provider 代码。** 按这个顺序花最小代价拿到事实：

1. **Gummy 的 `words[].speaker_id` 在实时模式下到底填不填？**
   接完阶段 A 就顺手 dump 原始事件看一眼。如果它真的填了，
   整个多说话人问题成本骤降 —— 但**大概率是空的**，别抱期望。
2. **拿真实直播音频测候选供应商的日语分离质量。**
   复用 `scripts/asr-probe.py` 的模式：喂同一段 WAV，dump 原始事件，人工核对
   「谁说的」是否正确。**至少要覆盖一段有 BGM/游戏音的片段**（见 §2.4）。
3. **量化误分离率。** 如果一段独白被拆成 3 个假说话人，每人上下文就都是碎的，
   这个功能反而会让译文**变差**。这是要不要做的决策点。

从 `runtime/media/private/` 抠真实音频的方法（`docs/subtitle-live-fix-plan-v2.md` §7 有完整命令）：

```powershell
$priv = 'F:\Projects\LagLingo\prototype\hls-companion\runtime\media\private'
$segs = Get-ChildItem (Join-Path $priv 'seg_*.m4s') | Sort-Object Name | Select-Object -Last 130
$fs = [System.IO.File]::Create('clip.mp4')
$init = [System.IO.File]::ReadAllBytes((Join-Path $priv 'init.mp4')); $fs.Write($init,0,$init.Length)
foreach ($s in $segs) { $b = [System.IO.File]::ReadAllBytes($s.FullName); $fs.Write($b,0,$b.Length) }
$fs.Close()
ffmpeg -y -i clip.mp4 -vn -ac 1 -ar 16000 -c:a pcm_s16le clip.wav   # 给 asr-probe
ffmpeg -y -i clip.mp4 -vn -c:a aac -f mpegts clip.ts                # 给 pipeline-e2e
```

---

## 7. 陷阱（都是这个代码库里真实存在的）

**陷阱 1：说话人标签会被回溯修正。**
AssemblyAI 明确会「随上下文修正之前的标签」。如果按 speaker 分桶存上下文，
标签一改，之前塞进那个桶的历史就归错人了。
`CueStore.update()` 能改已显示 cue 的 speaker（seq 自增，客户端能收到），
但**已经发出去的翻译请求无法回收**。
建议：上下文分桶只在标签稳定后生效，或干脆接受这点误差 —— 但要**明确写进代码注释**。

**陷阱 2：`_server_to_pipeline()` 不能跳过。**
任何供应商给的时间戳都是「它收到的音频」的偏移，和 `_pcm_offset` 在丢包/ASR 重连时会分叉。
新 provider 一样要走这个换算，并在 `_asr_manager` 新建 stream 时重置面包屑。

**陷阱 3：不要动 `MediaAnchor`。**
线上实测 C=-0.2、已冻结、spread 0.2、drift 0.3，它是对的。
前两轮都有人怀疑错方向，浪费了时间。

**陷阱 4：`extra_body` 陷阱会再犯。**
`extra_body` 是 OpenAI **Python SDK** 的约定（SDK 会把它摊平进 body 顶层）。
我们用 aiohttp 直接发原始 JSON，**任何厂商扩展字段都必须放在请求体顶层**。
上一轮 `translation_options` 和 `enable_thinking` 都栽在这里，
症状是「不报错但参数静默失效」（模型翻成英文、或当成聊天回复）。

**陷阱 5：翻译 worker 里的异常隔离不要退回去。**
`_translation_worker` 里所有可能抛异常的语句都在 try 内。
之前 `context.history()` / `capabilities` / `TranslationRequest()` 在 try 外面，
抛异常会让 worker task **静默死掉**，cue 永远停在 `src`，status 完全看不出来。
改成按 speaker 取上下文时，`contexts.for_speaker()` 也必须在 try 内。

**陷阱 6：单测全绿 ≠ 直播可用。**
现有 Python/Node 测试全是 stub。真实验收信号是 `/api/status` 的字段和
`scripts/pipeline-e2e.py`（它跑真 ASR + 真翻译）。

**陷阱 7：不要打印 `runtime/providers.json` 里的 API key。**
所有诊断脚本都只输出 `bool(key)`，保持这个习惯。

---

## 8. 可复用的脚本（`prototype/hls-companion/scripts/`）

| 脚本 | 用途 |
|---|---|
| `pipeline-e2e.py` | **最重要**。真 ASR + 真翻译跑完整 pipeline，逐条检查不变量并输出句长/延迟分布 |
| `asr-probe.py` | 喂 WAV 给 ASR，dump 全部原始事件到 JSON；`--silence-ms` / `--commit-every` 做 A/B。**接新供应商时先用它看协议** |
| `probe-translation.py` | 按 pipeline 真实代码路径打翻译 provider，打印异常类型和 cause（不打印 key） |
| `mt-bench.py` | 翻译延迟，串行 vs 并发 |
| `compare-timing.py` | 回放事件 JSON，对比时间戳估算与服务端真值，输出句长/就绪延迟分布 |
| `inspect-long.py` | 挖长句内部的稳定前缀增长过程 |
| `prefix-split-sim.py` | 模拟「稳定前缀按句号切分」能提前多久（阶段 C 若长句回潮可复用） |

---

## 9. 建议的 skills

- `agent-reach`：查各家实时分离 API 的**日语**支持和真实口碑，不要凭记忆猜协议
- `playwright`：服务端 cue 正确之后，自动化验证播放器上多说话人的显示/着色
- `planning-with-files`：这个功能跨 spike、供应商选型、多阶段实现，值得持久化计划

---

## 10. 一句话总结给下一个会话

**先接 Gummy 做语音直译（省一半钱、少一跳延迟、文档已确认 ja→zh），
顺手把上下文改成按说话人分桶（speaker=None 时行为不变），
UI 上按用户要求把「语音直译」作为独立模式列出来。
多说话人先 spike 日语分离质量再决定做不做 —— 百炼所有实时模型都不支持，必须换供应商，
而且直播里混着 BGM，误分离会让译文比现在更差。**

---

## 11. 分离器接入设计（第三轮补充，2026-08-30）

> 决策更新：放弃 Gummy 直译与 per-speaker 上下文分桶。架构定为
> **qwen3-asr 识别 + qwen-mt 全局上下文翻译 + 独立分离器**（pyannoteAI Live-1 为主选，
> sherpa-onnx 本地为备选，腾讯云 V2 当信号源为国内备胎）。分离只影响 cue 的
> speaker 字段与 UI 展示；翻译上下文仍是全局 rolling（拼接时加 `A:`/`B:` 前缀零成本增强）。

### 11.1 为什么"直播没有支持"不是问题

Live-1 的输入就是「16k mono PCM，100ms 分块，WebSocket 二进制帧」——而管道里
`PCM_CHUNK_BYTES = 3200`（subtitle_pipeline.py:45）**恰好就是 100ms 的 16k s16le**。
`_pcm_sender()` 每次取出的 `(chunk, offset)` 就是 Live-1 想要的全部东西。
不需要碰 HLS/ffmpeg/tee——**分叉点就是 `_pcm_queue` 的消费端**。

### 11.2 时间对齐：复用已被证明的 breadcrumb 机制

`_server_to_pipeline()`（subtitle_pipeline.py:539）解决的问题和分离器一模一样：
「供应商时钟 = 它本次会话收到的音频秒数」≠「管道 `_pcm_offset`」。丢块/重连时分叉。
breadcrumb 每 push 一个块记一对 `(服务端秒, 管道 offset)`，锚定每个已发送块的起点，
**即使中途丢块映射也精确**（丢的块没有 breadcrumb，服务端时钟也不会经过它）。

分离器是同一机制的第二实例：

```
_pcm_reader ──(_pcm_offset 时间轴)──> _pcm_queue
                                         │        ┌────────────────┐
                              ┌──────────┴──────┐ │ 两条独立会话时钟 │
                         _pcm_sender        _diar_sender
                       ASR breadcrumbs    diar breadcrumbs（各自 deque）
                              │                  │
                       qwen3-asr WSS       Live-1 WSS（100ms/3200B 帧）
                              │                  │
                        ASREvent(final)    diarization_speaker_start/end
                              │                  │
                     _server_to_pipeline   _diar_server_to_pipeline
                              │                  │
                              └──► _assign_speaker(begin, end) ◄──┘
                                   （区间重叠加权投票 → speaker）
                                            │
                                  _materialize_cue 填 cue.speaker
```

**重构建议**：把 breadcrumb 逻辑抽成 `ServerClockMapper` 小类
（`record(pushed_seconds, offset)` / `to_pipeline(s)` / `reset()`），
ASR 与 diarizer 各持一个实例——行为不变，现有测试应全绿。

### 11.3 组件清单

1. `providers/base.py`：
   - `DiarizerEvent(speaker: str, begin_server: float, end_server: float | None, open: bool)`
   - `DiarizerStream` 协议（`push_pcm` + async 迭代事件 + `aclose`）
   - `DiarizerProvider` 协议（`open_stream()`）
2. `providers/diar_pyannote_live.py`：Live-1 WebSocket 客户端
   （wss + 鉴权头；二进制帧 = 3200B；JSON 事件解析为 DiarizerEvent；4.5h 主动轮换）
3. `subtitle_pipeline.py`：
   - `_diar_queue`（独立有界队列，满则丢最旧 + `stats.diar_pcm_dropped`，
     **绝不反压 ASR 路径**——字幕延迟优先）
   - `_diar_sender` task：push + 记 mapper breadcrumb
   - `_diar_manager` task：照 `_asr_manager`（:626）的模式写——重连退避、
     新会话 `mapper.reset()`、`_consume_diar_events`
   - `_diar_segments`：管道时间轴上的 `(speaker, begin, end|None)` 有界 deque（~15min）
     start 事件即可用（end=None 视作开放区间），不必等 end
   - `_assign_speaker(begin, end)`：按重叠时长加权 argmax；
     最佳重叠 < 句长 40% → 返回 None（UNKNOWN）
   - `_materialize_cue` 里调用，填 `cue.speaker`
   - **late-fix**：diar 事件晚到时，对近 ~30s 内 speaker=None 的 cue 做
     `store.update(id, speaker=...)`（seq 自增，afterSeq 轮询天然送达）；
     **只允许 None→有值，绝不改写已赋值**（防 UI 闪烁）
   - status 增加：`diarReconnects` / `diarPcmDropped` / `speakerAssigned` /
     `speakerUnknown` / `activeSpeakers`
4. `providers/config.py`：`providers.json` 可选 `diarization` 节；缺省 → 全部
   speaker=None，行为与现在完全一致

### 11.4 必须处理的边界

- **两流重连时钟各自重置**：各自 mapper，互不影响；重连期间 ASR 照常出字幕
  （speaker=None），分离恢复后 late-fix 补
- **重连后 speaker 命名空间重置**（S1/S2 重新计数，可能换了人）：标签加会话前缀
  （`d0:A`、`d1:B`），UI 颜色按标签哈希；`max_speakers` LRU 上限防假说话人膨胀
- **时序天然友好**：Live-1 标签 <300ms 即到，ASR final 要等句尾+网络延迟，
  对齐时标签几乎总是就绪；未就绪走 late-fix
- **BGM 假 speaker**：预期内；spike 量化「独白被拆成几个假说话人」

### 11.5 spike 顺序（先验证再动管道，~1 天）

1. `scripts/diar-probe.py`（仿 asr-probe）：真实 clip WAV 按 100ms 块、1:1 实时率
   喂 Live-1，dump 原始事件 JSON——**验证协议、时间戳单位与原点、事件粒度**、
   重叠段是否出并发标签
2. 对齐验证：同 clip 跑 asr-probe，compare-timing 思路并排——句子区间 vs 分离区间，
   人工核对：轮流段归属正确率、假说话人数、时间戳偏差（应 <200ms）
3. 通过 → 写 provider + 管道集成；`pipeline-e2e.py` 加分离不变量
   （speakerAssigned 占比、无假说话人爆炸）

### 11.6 sherpa-onnx 备选（同一接口）

`providers/diar_sherpa.py` 实现同一 `DiarizerProvider` 协议；本地跑在同一个
`_pcm_sender` 分叉上，**mapper 是恒等映射**（自己产时间戳，直接用管道 offset）。
难点是流式化：滚动窗口（回看 3–5s）+ 每 ~8s 重跑窗口内离线分离 + 跨窗口用
聚类中心（CAM++ 嵌入均值）相似度缝合标签。工程量集中在缝合逻辑，
接口与 Live-1 完全互换。

### 11.7 部署与资源消耗分析（Live-1 vs 自托管）

「pyannote」是两个东西，部署成本天差地别：

**A｜pyannoteAI Live-1（SaaS API）——零部署**

- 本地零模型、零 GPU、零推理：只是多一个 WebSocket 客户端（~200 行，
  与现有 ASR 客户端同级）
- 本地消耗：带宽 +32 KB/s 上行（3200B/100ms，与 ASR 流等量级，上行翻倍）；
  内存可忽略（事件队列）
- 钱：€0.198/h ≈ ¥1.6/h；欧洲公司信用卡付款（已知顾虑）
- 依赖：无新增（websockets/aiohttp 已在用）

**B｜sherpa-onnx 本地自托管——小而轻，但要写流式壳**

- 模型体积：pyannote-segmentation-3.0 ONNX **6.6MB**（实测口径，OpenWhispr）
  + CAM++/WeSpeaker 嵌入 ~20–30MB + Silero VAD ~2MB → **全部 <50MB**；
  onnxruntime 另计（几十 MB）。**无 torch、无 GPU**
- CPU：离线管道实测 RTF 0.24（3DSpeaker int8，sherpa-onnx #3233）；
  社区同构纯 CPU 管道（Silero+WeSpeaker+谱聚类）RTF 0.12（HN diarize，
  VoxConverse DER 10.8%）→ **追一路直播约占 1 核的 12–24%**；
  sherpa-onnx 的 diarization 路径本来就只走 CPU，装 GPU 也不加速
- 内存：<200MB（窗口 + 嵌入缓存）
- 代价不在算力在工程：滚动窗口 + 跨窗口标签缝合（§11.6），预计 1–2 周
- 流式延迟 = 回看窗口 3–5s（Live-1 是 <300ms）

**C｜全量 pyannote.audio（PyTorch）——不推荐生产嵌入**

- 模型本身不大，但运行时要 torch（wheel ~2GB，CUDA 版更大）；
  HuggingFace 模型 gated（要接受条款 + token）；
  PyTorch 管道 CPU RTF ~0.86（diarize 作者实测对比），追直播余量太小
- 定位：离线评测/基准用，不进 companion 进程

**结论**：能接受国外付款 → Live-1（零部署零算力，延迟精度都最好）；
不能 → sherpa-onnx（算力开销可忽略，难点是工程不是性能）；
两条路共用同一 `DiarizerProvider` 接口，随时互换。

### 11.8 B 方案定案：自托管细化（2026-08-30 第四轮）

**关键设计简化：句级声纹归属（verification 式），不做滚动 diarization**

重新审视目标：只需要「轮流发言场景下，给 ASR final 的每句话标 speaker」。
句子边界 ASR VAD 已经给了 → **不需要 segmentation 模型、不需要聚类、不需要
滚动窗口与跨窗缝合**。最小栈只要一个东西：

- **CAM++ 声纹嵌入模型（~28MB，阿里 3D-Speaker，Apache-2.0）**
- 逻辑：每句 final 到达 → 取该句 PCM（±0.5s 上下文）→ 嵌入前向 →
  与「说话人画廊」（gallery，嵌入中心的 EMA）余弦比对 →
  高于阈值归属该人；低于则新建说话人（LRU 上限 8）
- 短句（<1.5s）嵌入不稳 → 只有与已知中心高相似时才上色，否则保持无色（宁缺毋滥）
- gallery 中心在线 EMA 更新，抗音色漂移
- 每句一次嵌入前向：3s 句音频 ≈ <0.1s CPU，性能消耗可忽略
- sherpa-onnx 的 SpeakerEmbeddingExtractor API 直接支持此用法
- 已知弱点（spike 量化）：音色相近的两人误归；抢话句嵌入被污染（轮流场景可接受）

若 spike 显示句级归属质量不行，再升级为完整管道
（segmentation 6.6MB + VAD 2MB + 聚类 + 滚动窗口），接口不变。

**嵌入开源项目的合规性（全部核实过）：**

| 组件 | 许可证 | 体积 |
|---|---|---|
| sherpa-onnx（运行时，pip 依赖） | Apache-2.0 | wheel（含 onnxruntime） |
| CAM++ embedding（3D-Speaker） | Apache-2.0（阿里） | ~28MB |
| pyannote-segmentation-3.0（备用） | MIT | 6.6MB（sherpa-onnx release 里的 ONNX 版，无 HF gating） |
| Silero VAD（备用） | MIT | ~2MB |

全部允许再分发与商用；义务仅是保留 LICENSE/NOTICE 归属文件。
**结论：可以合法嵌入，开箱即用没有法律障碍。**

**分发策略（推荐首启下载，不 vendor 进 git）：**

1. **定案改为 vendor**：CAM++ 模型（28MB）随项目放在
   `vendor/models/diarization/campplus.onnx`，用户安装依赖后可直接开关，真正开箱即用
2. `scripts/download-diar-models.py` 保留为模型丢失/更新时的恢复工具，
   从 sherpa-onnx GitHub release 下载并做 SHA-256 校验
3. 国内网络兜底镜像仍可后续补充；当前仓库已携带模型，不影响首次启动
4. 28MB 会增加 clone 体积，但单文件低于 GitHub 50MB 警告线；相对开箱即用收益可接受

**实现顺序：**

1. `scripts/diar-spike.py`：真实 clip 离线验证句级归属质量（半天）
   ——输出：轮流段归属正确率、假说话人数、阈值敏感性
2. `providers/diar_sherpa.py`：`DiarizerProvider` 实现
   （`asyncio.to_thread` 包同步推理；句子 PCM 从 `_pcm_queue` 分叉缓冲取回）
3. 管道接线（§11.3，mapper 恒等——本地时钟）+ late-fix
4. 下载脚本 + config 检查 + UI 提示
5. `pipeline-e2e.py` 加不变量（speakerAssigned 占比、假说话人上限）
