# LingerLens 直播字幕修复计划 v2（实测驱动）

> 生成时间：2026-08-30
> **状态：P0 + P1 已实施并通过端到端实测（见 §9）。P2 经实测判定不需要做。**
> 取代：`docs/subtitle-debugging-handoff.md` 中的假设列表、
> `docs/subtitle-alignment-redesign.md` 中的 Fix C / Fix D / Fix F。
> 本文的每一条根因都有本轮**实机抓取的运行态证据**，不是代码推断。

---

## 0. 三个问题的直接答案

**问：Qwen 的 ASR 能不能把直播里的话都识别成字幕？**
**能，而且识别得很完整。ASR 不是瓶颈。** 用同一条直播的真实音频实测，60 秒音频完整转出
203 个字符的连贯日语，包含所有短感叹词。最终文本可用性没有问题。
问题**全部**出在「分句参数 + 时间戳来源 + 播放器窗口」这三层，以及一个完全独立的翻译故障。

**问：时间点怎么才能打准？**
**Qwen 已经把精确时间戳发给我们了，是当前代码把它扔掉了。**
`input_audio_buffer.speech_started` 带 `audio_start_ms`，`speech_stopped` 带 `audio_end_ms`，
两者与 `transcription.completed` 通过 **`item_id`** 严格一一对应。
现在的代码用 `_last_sent_pcm_offset - 常数` 去猜，实测**句尾系统性偏早 0.34 秒**（600ms 配置下约 0.54 秒）。
改用服务端偏移后误差归零。

**问：翻译模型为什么一次都没被调用？**
**因为 `runtime/providers.json` 里翻译 baseUrl 写成了 `https://127.0.0.1:8045/v1`，
而那个本地代理只说明文 HTTP。** 每次请求都死在 TLS 握手，请求根本没发出去。
备用 provider 又没有 API key。两条腿全断 → 熔断 → 107/107 全部 `failed` → 只剩日语。

---

## 1. 现场证据（本轮实机抓取）

### 1.1 运行中 companion 的 `/api/status.subtitles`

```
pcmOffset            803.7      (13 分钟音频)
translationFailures  107        <-- 107 个 cue，107 次失败
lastTranslationLatencyMs  null  <-- 从来没有一次成功
lastError            "RuntimeError: all translation providers are cooling down"
timingSourceCounts   {asr: 0, vad: 86, approx: 21}
forcedCommits        23
suppressedByIngestError 5
teeDropped 0  pcmDropped 0  asrReconnects 0  unmappedDropped 0
mediaAnchorC -0.3  frozen=true  samples=572  spread=0.1  drift=0.1
```

`/api/subtitles?afterSeq=0` 里 26 个 cue，**state 全部是 `failed`，`zh` 全部为空**。

**读法：**

- 采集/tee/PCM/anchor 这一层是**健康的**（0 丢弃、0 重连、anchor 已冻结且抖动 0.1s）。
  MediaAnchor 是对的，**这次不要动它**。
- `asr: 0` 说明「用 ASR 自带时间戳」这条路径从来没生效过 —— 见 1.3。
- `approx: 21` ≈ `forcedCommits: 23`，两者是同一件事的两面 —— 见 1.4。

### 1.2 翻译：TLS 版本错误（根因已定位并验证修复）

用与 pipeline 完全相同的代码路径直接打 provider：

```
bailian-qwen35-flash @ https://127.0.0.1:8045/v1
  -> FAIL ClientConnectorSSLError: [SSL: WRONG_VERSION_NUMBER] wrong version number
bailian-qwen-mt-flash @ https://dashscope.aliyuncs.com/compatible-mode/v1
  -> FAIL ValueError: API key is not configured for provider bailian-qwen-mt-flash
把 baseUrl 改成 http://127.0.0.1:8045/v1
  -> OK 3781ms -> '这才对嘛。'
```

直接探测端口 8045：`https://` 握手失败，`http://127.0.0.1:8045/v1/models` 返回 200 和 89 个模型，
其中**包含配置里写的 `gemini-3.1-flash-lite`**。模型名没错，**只有 scheme 错了**。

`FallbackChain` 的行为让故障被放大成永久性的：主 provider 连续失败 3 次 → 冷却 60 秒 →
备用 provider 无 key 也失败 3 次 → 冷却 60 秒 → 之后所有请求直接抛
`all translation providers are cooling down`，**连 HTTP 都不再尝试**。这就是
"模型侧一次调用记录都没有" 的完整解释。

### 1.3 ASR 事件协议（实测抓包，这是最关键的发现）

用直播 private 分片解出的 60 秒真实音频喂 Qwen realtime，完整 dump 原始事件：

```
speech_started   keys = [audio_start_ms, event_id, item_id]
speech_stopped   keys = [audio_end_ms,   event_id, item_id]
committed        keys = [item_id, previous_item_id, event_id]
completed        keys = [item_id, content_index, language, emotion, usage, transcript]
```

**结论 A：`completed` 事件里根本没有任何时间字段。**
`asr_qwen_realtime.py` 现在读的 `begin_time / end_time / audio_start_ms / audio_end_ms`
在 `completed` 上**永远不存在**，所以 `timingSourceCounts.asr` 恒为 0。这段代码是死代码。

**结论 B：时间戳在 VAD 事件上，而且是会话内绝对毫秒偏移。**
实测 23 段全部满足 `speech_started(item=X).audio_start_ms` →
`speech_stopped(item=X).audio_end_ms` → `completed(item=X)` 严格一一对应，
并且相邻两段 `audio_end_ms == 下一段 audio_start_ms`（时间轴连续无洞）。

**结论 C：ASR 本身几乎没有延迟。**
final 相对语音结束的可用延迟：**p50 = 0.16s，p95 = 0.23s，max = 0.27s**。
「字幕晚」跟 ASR 无关。

**结论 D：还有一路被完全忽略的增量结果。**
60 秒里有 **124 个** `conversation.item.input_audio_transcription.text` 事件，
每个带 `text`（已确认的稳定前缀）+ `stash`（试探性尾巴）+ `item_id`。
`_handle_asr_event` 里**没有 `interim` 分支**，这 124 个事件全部被丢弃。
这是长句想「从第一句就开始显示」唯一的物理可行手段。

### 1.4 分句参数 A/B（同一段 60 秒音频，三组对照）

| 配置 | final 数 | 转出字符 | 单句最长 | 实际观感 |
|---|---|---|---|---|
| A：`silence=600ms`，无 manual commit（= 当前线上配置） | **3** | 158 | **29.2s** | 一句话 29 秒才出，必然全丢 |
| B：`silence=400ms` + 每 6s manual commit | 多 | — | ~6s | 切在词中间，出现 `お。` 这类残句 |
| C：`silence=400ms`，无 manual commit | **23** | **203** | 10.5s | 天然字幕粒度，且**多转出 45 个字符** |

**这是本轮最反直觉、也最重要的一条：把静音阈值从 600ms 降到 400ms，
不但把 3 个巨块切成 23 个正常字幕单元，还多识别出 45 个字符**
（`ええ。` `うわ。` `ははは。` `おおお。` `どうだ。` 这些短反应在 600ms 下被并进长句然后丢失了）。

`runtime/providers.json` 里现在是 **600**，`DEFAULT_CONFIG` 里才是 400 —— 线上跑的是 600。

**manual commit（`maxUtteranceSeconds=6.0`）实测有害：**
它确实能触发 final（`commitFailures: 0`，协议支持），但

1. 切在词中间，产出 `お。` `ええ。` 这类无意义残片，丢失语义；
2. 它产生的 `completed` **前面没有 `speech_stopped`** → 现在的代码走 `approx` 分支 →
   `tStart = None`、`tEnd = 当前发送位置`，**这个 cue 的时间是纯猜的**。
   这就是 `forcedCommits: 23` ↔ `approx: 21` 的因果；
3. 一个 item 会收到两次 `committed`（manual 一次、speech_stopped 一次），状态机变脏。

### 1.5 当前时间戳估算 vs 服务端真值（23 段逐条比对）

```
== CURRENT 估算器的绝对误差（对照服务端真值）==
  句首边界: n=22 mean=0.083s p50=0.068s max=0.168s
  句尾边界: n=22 mean=0.336s p50=0.340s max=0.368s   <-- 系统性偏早
  会被标成 approx(tStart=None) 的 cue: 1/23

== 语音段时长分布（= 修好后的显示窗口长度）==
  n=22 min=0.42s p50=1.70s p90=6.56s max=10.54s
```

句尾误差是**固定偏置**，来自 `speech_stopped` 时做的 `- silence_duration_seconds` 减法 ——
但服务端给的 `audio_end_ms` **本来就已经是语音结束点**，不含静音窗口，这个减法是重复扣除。
线上 600ms 配置下这个偏置约 0.54 秒，**每一条字幕都早半秒**。

### 1.6 翻译吞吐（修好 scheme 之后立刻会撞上的第二个墙）

用真实 ASR 句子实测本地代理：

```
=== 串行（当前 pipeline：1 个 _translation_worker）===
  12 句耗时 35.5s   p50=2969ms  max=4344ms
=== 并发 x12 ===
  12 句耗时  3.6s   p50=2484ms  max=3609ms
```

**单次调用有约 2 秒的固定地板**（连 `ええ。` 都要 3.4 秒），而且**串行不可能跟上**：
60 秒音频产生 23 个句子 × ~3 秒 = **69 秒翻译工作量**。
当前只有一个 worker，队列上限 8，溢出即标 `failed`。
**即使只修 baseUrl，也只能救回大约三分之一的中文字幕，其余仍然掉队被丢。**
翻译必须并发化 —— 实测完全可以并发。

### 1.7 播放器窗口（用户已明确否定的行为）

`web-player/subtitle-scheduler.js:44-45`

```js
var windowEnd = cue.tEnd + cue.hold;
admission = { from: Math.max(t, cue.tEnd), until: windowEnd };
```

字幕最早只能在 **tEnd** 出现。用户要求从 **tStart** 出现。这一条不需要再验证，代码就是这么写的。

---

## 2. 长句怎么办：分句，而且分三层

### 2.1 预算公式

要在 `tStart` 显示完整一句，必须满足：

```
观看延迟  ≥  句长(tEnd - tStart)  +  ASR 就绪延迟  +  翻译延迟
```

翻译（2.5–3.6s，本地代理有 ~2s 固定地板）现在是**预算里最大的一项**，
比 p50 句长（1.6s）还大。这一点后面很关键。

### 2.2 第一层：VAD 分句 —— 主力，而且已经调到头了

在 130 秒真实直播音频上扫 `silenceDurationMs`：

| 阈值 | final 数 | 转出字符 | 句长 p50 | 句长 p90 | 句尾误差(当前估算器) |
|---|---|---|---|---|---|
| 200ms | 40 | 437 | 1.56s | 5.61s | 0.126s |
| **400ms** | **40** | **437** | **1.56s** | **5.61s** | 0.323s |
| 600ms | 33 | 428 | 1.86s | 5.50s | 0.502s |

**关键发现 1：200ms 和 400ms 的分句结果逐条完全相同。**
说明 400ms 已经是 Qwen 服务端 VAD 的有效下限，**再往下调不会有任何收益**，
不用在这个参数上继续花时间。

**关键发现 2：600ms → 400ms 多出 7 个 final、9 个字符**，
和 §1.4 在另一段音频上的结论一致（多出 45 个字符）。

**关键发现 3：句尾误差随阈值线性变化（0.126 / 0.323 / 0.502）**，
这是 §1.5 那个 `- silence_duration_seconds` 重复扣除的确定性证据。

**光靠 VAD 分句，p90 = 5.6s → 预算 5.6 + 0.4 + 3.0 = 9.0s，10 秒延迟就够。**

### 2.3 那个 30 秒的"长句"是假警报

三组配置里 `max` 都是 **30.20s**，看着很吓人。实际抓出来看：

```
=== 长句 span 96.66..126.86 (30.2s), final 到达 sent=127.10 ===
  final: 。
  稳定前缀增长过程: (空)
```

**这 30 秒是游戏音乐被 VAD 误判成人声，ASR 正确地什么都没转出来，
只吐了一个 `。`，随后被 `clean_subtitle_text` 丢弃。**
它对字幕零影响，只是污染了句长统计（也白烧 ASR 的钱）。

**教训：看 `max` 会吓自己，要看真实语音的分布。**
排除噪声段后，400ms 下真实语音句长上限约 **7s**，
另一段音频里出现过一个 **10.5s 的双句 utterance**（23 句里 1 句，约 3–4%）。

### 2.4 第二层：稳定前缀分句 —— 处理残余长句

对 VAD 仍然没切开的长句，用 Qwen 的增量结果切。
Qwen 每条 `transcription.text` 都带 `text`（已确认稳定前缀）+ `stash`（试探尾巴），
**只要稳定前缀里出现一个 `。！？` 结尾的完整句子，就立刻发出去，不等 utterance 结束。**

实测（真实的 10.5s 双句 utterance）：

```
=== span 42.42..52.96 (10.5s), final 到达 sent=53.10 ===
  final: やっとこのゲームの終着点にたどり着くことができた。終わりがないってのが一番怖いからさ。進む。私は。
  sent=48.80 (比 final 早 4.30s)  やっとこのゲームの終着点にたどり着くことができた。   <-- 第1句可以提前发
  sent=53.10 (比 final 早 0.00s)  ...終わりがないってのが一番怖いからさ。              <-- 最后一句没得赚
```

在 600ms 配置下那个 **29.2 秒真实长句**（就是用户描述的 1/2/3/4/5）收益更大：

```
  第1句 比 final 早 13.10s
  第2句 比 final 早 11.10s
  平均提前 9.15s
```

**规律：前缀分句让长句里除最后一句以外的每一句都提前发出；
最后一句必然和 final 同时到达（它本来就是在 utterance 结束时才说完的）。**

代价：前缀的时间戳不如 VAD 精确（没有逐句 `audio_*_ms`，只能按前缀确认时刻估算，
实测滞后 0.5–4s 不等），所以**只对超过阈值的长句启用，短句一律走 VAD 真值**。

### 2.5 第三层：不要做 —— 切已经到手的 final

这是最直觉、但**完全没用**的做法：把 `completed` 的长文本按 `。` 切成子 cue，
用字符比例插值出时间戳（`subtitle_text.py:split_with_timing` 就是干这个的）。

**零收益**：所有子 cue 在同一时刻到达，前 n-1 句的 `tEnd` 都已经是过去时间，
播放器的迟到策略会**直接丢弃它们**，最后只显示最后一句。
它只改善「一屏 70 个字太长」的可读性，**一点延迟都不省**。

`split_with_timing` 现在的注释已经写明「绝不能用来生成新的排期单元」，这个约束要保持。

### 2.6 结论：加上分句之后的预算表

| 场景 | 占比 | 句长 | ASR | 翻译 | 需要延迟 |
|---|---|---|---|---|---|
| p50 | ~50% | 1.6s | 0.2s | 2.5s | **4.3s** ✅ |
| p90 | ~90% | 5.6s | 0.4s | 3.0s | **9.0s** ✅ |
| 10.5s 双句，**不**分句 | ~3% | 10.5s | 0.4s | 3.0s | 13.9s ❌ |
| 同上，前缀分句后第 1 句 | | ~6.4s | ~0.6s | 3.0s | **10.0s** ⚠️ |
| 同上，前缀分句后第 2 句 | | ~4.1s | 0.4s | 3.0s | **7.5s** ✅ |

**结论：10 秒延迟 + 400ms VAD + 前缀分句，可以覆盖约 97% 的句子；
把延迟放到 12 秒就基本全覆盖，不需要 15 秒。**

### 2.7 还有一个更便宜的杠杆：翻译

翻译现在占预算 2.5–3.6 秒，**比 p50 句长还大，是单项最大头**。
本地代理（`gemini-3.1-flash-lite` 经 Antigravity）连翻译 `ええ。` 都要 3.4 秒，
有约 2 秒的固定地板 —— 这是代理开销，不是模型算力。

**换一个更快的翻译通道能 1:1 直接买回延迟预算。**
`qwen-mt-flash` 是专用翻译模型（不是通用 LLM），正常应在 1 秒内，
配好 `DASHSCOPE_API_KEY` 就能测。**省下的 2 秒，比在 VAD 参数上折腾划算得多。**

### 2.8 需要用户拍板

- **观看延迟**：建议 **12 秒**（覆盖 ~99%）；坚持 10 秒也可以，代价是约 3% 的长句第一句会迟到。
- **要不要做第二层（前缀分句）**：不做的话，约 3% 的长句仍然会整块迟到。
  P0/P1 不依赖它，可以先上线看实际比例再决定。

---

## 3. 开源参考

| 项目 | 许可 | 可借鉴点 |
|---|---|---|
| [QuentinFuxa/WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit) | Apache-2.0 | **最贴近的参考。** 它给前端下发的是 `lines`（已确认、带时间戳的段）+ `buffer_transcription`（未确认尾巴），前端把两者拼起来渲染。这正是我们 P2 要的形态。它还有 `buffer_trimming`（segment/sentence）+ `buffer_trimming_sec` 阈值，解决的就是我们的「超长句」问题。 |
| [ufal/whisper_streaming](https://github.com/ufal/whisper_streaming) | MIT | LocalAgreement-2 的原始实现与[论文](https://arxiv.org/html/2307.14743v2)。用「连续 n 次更新在前缀上达成一致」来确认稳定前缀。 |
| [KoljaB/RealtimeSTT](https://github.com/KoljaB/RealtimeSTT) | MIT | `core/realtime_text_stabilizer.py`：稳定前缀 commit、前缀冲突处理、`stable_prefix_conflict` / `commit_reason` 这类可诊断字段的设计值得抄。 |

**重要判断：我们不需要实现 LocalAgreement。**
WhisperLiveKit / whisper_streaming 之所以要跑 LocalAgreement，是因为 Whisper 不给稳定前缀，
必须靠多轮重叠解码去推。**Qwen realtime 直接把 `text`（稳定前缀）和 `stash`（试探尾巴）分开发给我们**，
这一步是白送的。要抄的是它们的**前端契约**（committed lines + live buffer），不是它们的推断算法。

---

## 4. 修改计划

原则：**一次只改一层，每层有独立验收信号**，不要像上一轮那样同时动翻译/ASR/anchor/scheduler。

### P0 — 让中文出来（30 分钟，风险最低，独立可验收）

**P0.1 修 baseUrl scheme**

`runtime/providers.json`：`translation.providers[bailian-qwen35-flash].baseUrl`
`https://127.0.0.1:8045/v1` → `http://127.0.0.1:8045/v1`

**P0.2 摘掉必然失败的 fallback**

`runtime/providers.json`：`translation.fallback: ["bailian-qwen-mt-flash"]` → `[]`

理由：该 provider 的 key 在 companion 进程环境里也是 `apiKeyConfigured=false`（已通过
`/api/providers` 确认）。留着它没有任何冗余价值，只会在主 provider 抖动 3 次后
把全部流量导向一个 100% 失败的腿，并额外触发 60 秒熔断。
等真正配好 `DASHSCOPE_API_KEY` 再加回来。

**P0.3 翻译 worker 并发化** — `companion/subtitle_pipeline.py`

- `start()` 里改为创建 N 个 `_translation_worker`（`translation_workers`，默认 **4**）；
- 队列上限从 8 提到 `max(16, 4 * workers)`；
- `_enqueue_translation()` 溢出丢弃时补 `self._translation_queue.task_done()`
  （现在漏了，`unfinished_tasks` 计数会失准）；
- `status()` 增加 `translationWorkers`、`translationAttempts`、
  `translationWorkerAlive`（存活 worker 数）、`lastTranslationAttemptAt`。

> 注意：cue 的显示顺序由播放器按 `tStart` 决定，**不依赖翻译完成顺序**，
> 所以并发不需要保序提交，直接并发即可。

**P0.4 加 worker 死亡可观测性** — `_translation_worker`

把 `context.history(...)` / `provider.capabilities` / `TranslationRequest(...)` /
provider 选择这几行**移进 try 块**，并在整个 while 循环体外再包一层
`except Exception -> 记录 self.stats.translation_worker_exception 后 continue`。
现在这几行抛异常会让 worker task 静默退出，且 status 完全看不出来。

**P0 验收（必须全部满足）**

```bash
curl http://127.0.0.1:8765/api/status | jq .subtitles
# translationFailures 不再等于 cue 总数
# lastTranslationLatencyMs / avgTranslationLatencyMs 有数值（预期 2000-4000）
# translationBacklog 稳定在 0-3，不持续增长
curl "http://127.0.0.1:8765/api/subtitles?afterSeq=0" | jq '[.cues[].state] | group_by(.) | map({(.[0]): length}) | add'
# 出现 "done"，且 done 占多数
```

播放器上应立刻看到中文。**P0 只做这些，先验收，再进 P1。**

---

### P1 — 把时间点打准（核心改动）

**P1.1 ASR provider 暴露 item_id 和 VAD 时间戳** — `providers/base.py` + `providers/asr_qwen_realtime.py`

- `ASREvent` 增加字段 `item_id: str | None = None`；
- `_map_event`：
  - `speech_started` → `ASREvent("speech_started", begin_pcm=_seconds(raw["audio_start_ms"]), item_id=raw.get("item_id"))`
  - `speech_stopped` → `ASREvent("speech_stopped", end_pcm=_seconds(raw["audio_end_ms"]), item_id=raw.get("item_id"))`
  - `completed` → `item_id=raw.get("item_id")`，**删掉 `begin_time/end_time/audio_start_ms/audio_end_ms` 的读取**（实测证明是死代码）
  - `transcription.text` → 已有的 interim 映射加上 `item_id`（P2 要用）

> 语义定义必须写进注释：`audio_start_ms/audio_end_ms` 是**本次 ASR 会话内**、
> 服务端**实际收到的音频**的绝对毫秒偏移。它不是墙钟，也不是我们的 `_pcm_offset`。

**P1.2 会话帧 → 管线帧 的精确映射** — `subtitle_pipeline.py`

服务端偏移是「服务端收到了多少音频」，`_pcm_offset` 是「我们产出了多少音频」。
两者在 `pcmDropped > 0` 或 ASR 重连时会分叉，必须显式换算，不能默认相等。

```python
# _pcm_sender 中，push 之前记录面包屑：
self._push_breadcrumbs.append((self._stream_pushed_seconds, offset))   # deque(maxlen=1200)
self._stream_pushed_seconds += len(chunk) / PCM_BYTES_PER_SECOND

def _server_to_pipeline(self, server_seconds: float) -> float | None:
    """把 ASR 会话内偏移换算成 pipeline 的 _pcm_offset 帧。"""
    # 取最后一个 pushed <= server_seconds 的面包屑，线性外推
```

`_asr_manager` 每次新建 stream 时重置 `_stream_pushed_seconds = 0.0` 并清空面包屑。

**P1.3 用 item_id 关联，取代 `_pending_vad_start/_pending_vad_end`** — `subtitle_pipeline.py`

```python
self._vad_spans: OrderedDict[str, dict] = OrderedDict()   # 上限 64，超出丢最老

# speech_started: self._vad_spans[item]["start"] = self._server_to_pipeline(event.begin_pcm)
# speech_stopped: self._vad_spans[item]["end"]   = self._server_to_pipeline(event.end_pcm)
# final:          span = self._vad_spans.pop(event.item_id, {})
#                 begin_pcm, end_pcm, timing_source = span.get("start"), span.get("end"), "asr"
#                 若 end 缺失（manual commit / EOF）→ end = self._last_sent_pcm_offset, source="approx"
```

**同时删除：**

- `vad_event_lag` 及其 `- 0.3` 补偿（服务端已给真值，补偿是纯误差源）；
- `speech_stopped` 里的 `- silence_duration_seconds`（重复扣除，实测造成 0.34-0.54s 固定偏早）；
- `_pending_vad_start` / `_pending_vad_end` 两个字段本身（它们无法处理交错，是 `approx` 的来源之一）。

`timing_source` 语义重定义：`"asr"` = 来自服务端 VAD 偏移（正常路径），
`"approx"` = 兜底。改完后 `timingSourceCounts.asr` 应该接近 100%，这就是验收信号。

**P1.4 关掉 manual commit，改对静音阈值** — `runtime/providers.json`

- `asr.providers[bailian-qwen3-realtime].options.turnDetection.silenceDurationMs`：**600 → 400**
- `subtitle.maxUtteranceSeconds`：**新增并设为 `0`**（0 = 关闭强制 commit）
- `subtitle.holdSecondsMax`: 新增 `7.0`（当前文件缺这个键，靠 server 默认值兜着）

代码侧：`subtitle_pipeline.py` 保留 `_maybe_force_commit`，但默认关闭；
`providers/config.py` 的 `DEFAULT_CONFIG.subtitle.maxUtteranceSeconds` 也改成 `0`，
并删掉 `vadEventLagSeconds`。

> 依据见 §1.4：400ms 比 600ms **多**转出 45 个字符，且把 29 秒巨块切成正常粒度；
> manual commit 会切碎词并产出无时间戳的 cue。

**P1.5 播放器窗口改成 `[tStart, tEnd]`** — `web-player/subtitle-scheduler.js`

```js
// 用户要求：整句在第一句话开始时就出现，并持续覆盖到整句语音结束。
var start = (cue.tStart != null) ? cue.tStart : cue.tEnd;
var windowEnd = cue.tEnd + tailHold;          // tailHold = Math.min(cue.hold, 1.5)
if (t <= windowEnd) {
  admission = { from: Math.max(t, start), until: windowEnd };   // 译文早到就等 tStart，晚到就立刻补显示
} else {
  // 迟到策略保持不变
}
```

`tailHold` 的作用：相邻 cue 的 `tStart == 前一个 cue 的 tEnd`（实测时间轴连续），
所以 tail 只在**真的有静音间隔**时才可见，不会压住下一句 ——
`pick()` 已经按 `tEnd` 最大者优先，下一句会自动接管。

同时把 `minDwell` 从 **1.2s 降到 0.6s**：实测句长 p50 只有 1.7s、min 0.42s，
1.2s 的最小驻留会把后一句往后推，产生新的错位。

**P1.6 补齐诊断字段（不改行为，只加可观测性）**

- `status()` 增加 `finalDiscarded`、`finalDeduplicated`（`PipelineStats` 里已有，没往外暴露）；
- `player.js` 把 `subtitleScheduler.stats` 的 `lateCues / droppedLateCues / sourceOnlyCues`
  渲染到调试面板 —— 否则「缺字幕」永远分不清是服务端没产还是客户端丢了。

**P1 验收**

```bash
curl http://127.0.0.1:8765/api/status | jq '.subtitles | {timingSourceCounts, forcedCommits, finalDiscarded, finalDeduplicated}'
# timingSourceCounts.asr 占比 > 95%，approx 接近 0
# forcedCommits == 0
curl "http://127.0.0.1:8765/api/subtitles?afterSeq=0" | jq '[.cues[] | select(.tStart == null)] | length'
# == 0
curl "http://127.0.0.1:8765/api/subtitles?afterSeq=0" | jq '[.cues[] | (.tEnd - .tStart)] | {min: min, max: max}'
# 不再出现 tStart == tEnd 的零长 cue（当前线上有）
```

人工验收：播放器上一句长字幕应在**说话人开口的瞬间**整句出现，并一直挂到这句说完。

---

### P2 — 长句的稳定前缀分句（§2.4；先上 P0/P1 看实际长句比例再决定）

Qwen 已经在发 `text`（稳定前缀）+ `stash`，60 秒里有 124 条，现在**全部被丢弃**
（`_handle_asr_event` 根本没有 `interim` 分支）。

1. `_handle_asr_event` 增加 `interim` 分支：按 `item_id` 累积当前 utterance 的稳定前缀；
2. 当某个 item 满足**两个条件**时才启用前缀分句 —— 避免影响正常短句：
   - 距该 item 的 `speech_started` 已超过 `splitAfterSeconds`（建议 **8s**，
     实测 p90 只有 5.6s，8s 不会误伤正常句子）；
   - 稳定前缀里出现了新的 `。！？` 结尾的完整句子；
3. 满足则把该完整句子作为**独立 cue** 发出：
   - `tStart` = 上一个已发子句的 `tEnd`（首个子句用该 item 的 `audio_start_ms`，
     **在 `speech_started` 时就已知**）；
   - `tEnd` = 当前前缀确认位置（换算到 pipeline 帧），标 `timing_source="prefix"`；
   - 正常进翻译队列；
4. 该 item 的 `completed` 到达时，把**剩余未发出的尾部**作为最后一个 cue 发出，
   `tEnd` 用权威的 `audio_end_ms` 修正；已发出的子句不再改动；
5. `status()` 增加 `prefixSplitCues`，用来验证这条路径的实际触发率。

**不要**把前缀分句用在短句上（会用不精确的时间戳替换掉 VAD 真值）。
**不要**用 `stash` 的内容生成 cue（它是试探性的，会被改写）。

参考 WhisperLiveKit 的 `lines`（已确认段）+ `buffer_transcription`（未确认尾巴）
前端契约，以及它的 `buffer_trimming_sec` 阈值思路。

**P2 验收**：构造/回放一个 >8s 的多句 utterance，确认它产出多个 cue、
第一个 cue 比 `completed` 到达时间早 ≥3s，且时间戳不与相邻 VAD cue 重叠。

---

### P3 — 遗留问题（不阻塞，但要修）

**P3.1 `_ingest_is_unhealthy()` 在静默删字幕** —— 线上 `suppressedByIngestError: 5`，
而同时 `sourceError: null`。也就是说这 5 条是被 `log_tail` 里的
`"skipping"` / `"expired from playlists"` 子串匹配掉的。
问题在于 yt-dlp 的 stderr 尾巴会**长时间**保留这些字样，而 cue 文本其实已经拿到了。
**已经付费识别出来的文本不应该因为日志噪声被丢弃。** 建议：改成给 cue 打
`degraded: true` 标记照常显示，或者至少把匹配窗口限制在最近 2 秒内的日志行。

**P3.2 相邻重复 final 被静默丢弃** —— `is_duplicate_final` 只比对上一条。
400ms 分句下短感叹词（`うん。` `うわ。`）连说两次是常态，第二次会被吞。
建议：只在两条 final 的时间间隔 < 1s 时才判重。

**P3.3 `clean_subtitle_text` 的过滤在 400ms 粒度下偏激进** ——
`len(cleaned) < 2` 和 `_FILLER_WORDS`（含 `はい`）会丢掉实测中大量出现的合法短句。
建议把 `はい` 从 filler 列表移除，长度门槛降到 1。

**P3.4 `translation_timeout_seconds`** —— 实测 max 4.34s，当前配 6s。并发化后够用，
但如果代理抖动会直接落 `failed`。建议提到 **8s**，同时把 `holdSecondsMax` 保持 7s 不变。

---

## 5. 需要用户拍板的两件事

1. **超长句策略**：§2 的 (a) 抬延迟到 15s / **(b) 12s + stable prefix 渐进（推荐）** / (c) 接受晚出。
   P0/P1 与这个选择无关，可以先做。
2. **观看延迟目标值**：建议默认从当前值调到 **12 秒**（播放器已有该选项，
   `/api/publish-delay` 可以运行中热调）。低于 10 秒时 p90 长句无法「从首句整句显示」。

---

## 6. 不要做的事

- **不要动 MediaAnchor。** 实测 `C=-0.3s`、已冻结、572 个样本、spread 0.1s、drift 0.1s，它是对的。
  上一轮怀疑它是误判方向。
- **不要再从 `completed` 事件里找时间戳。** 已抓包证实那里没有。
- **不要用 manual commit 切长句。** 实测切碎词并产出无时间戳 cue。
- **不要只修 baseUrl 就宣布翻译修好了。** §1.6 证明串行 worker 跟不上，
  只修 URL 只能救回约三分之一。
- **不要用 `npm run test:hls-companion` 全绿当作直播验收**，那些全是 stub。
  真实验收信号是 §4 各阶段列出的 `/api/status` 字段。
- **不要一次做完 P0+P1+P2**，按阶段验收，否则又会无法归因。

---

## 7. 复现本轮实验的脚本

以下脚本在 `%TEMP%\claude\...\scratchpad\`，建议固化进 `prototype/hls-companion/scripts/`：

| 脚本 | 作用 |
|---|---|
| `probe_translation.py` | 按 pipeline 的真实代码路径逐个打翻译 provider，打印异常类型与 cause（不打印 key） |
| `asr_probe.py` | 把 16k mono WAV 喂 Qwen realtime，dump 全部原始事件到 JSON，支持 `--silence-ms` / `--commit-every` 做 A/B |
| `compare_timing.py` | 回放事件 JSON，逐条对比「当前估算器」与「服务端真值」的误差，并输出句长/就绪延迟分布 |
| `mt_bench.py` | 用真实 ASR 句子测翻译延迟，串行 vs 并发对照 |

取直播真实音频的方法（无需重新拉流）：

```powershell
# 把 private 分片拼成可解的 mp4（必须带 init.mp4，且用绝对路径）
$priv = 'F:\Projects\LingerLens\prototype\hls-companion\runtime\media\private'
$segs = Get-ChildItem (Join-Path $priv 'seg_*.m4s') | Sort-Object Name | Select-Object -Last 60
$fs = [System.IO.File]::Create('clip.mp4')
$init = [System.IO.File]::ReadAllBytes((Join-Path $priv 'init.mp4')); $fs.Write($init,0,$init.Length)
foreach ($s in $segs) { $b = [System.IO.File]::ReadAllBytes($s.FullName); $fs.Write($b,0,$b.Length) }
$fs.Close()
ffmpeg -y -i clip.mp4 -vn -ac 1 -ar 16000 -c:a pcm_s16le clip.wav
```


---

## 9. 实施结果（2026-08-30 实测）

### 9.1 又发现两个真实 bug（都在翻译路径上）

用户把翻译换成 `qwen-mt-turbo`（阿里云 MaaS 端点）后，实测暴露出两个新问题：

**Bug A：`qwen-mt-turbo` 拒绝 `system` 角色。**
`openai-compatible` provider 一直发 system prompt，全部返回
`HTTP 400: Role must be in [user, assistant]` —— 100% 失败。

**Bug B：`translation_options` 被嵌在 `extra_body` 里，服务端根本收不到。**
`extra_body` 是 **OpenAI Python SDK** 的约定（SDK 会把它摊平进请求体顶层）；
我们用 aiohttp 直接发原始 JSON，嵌套之后服务端只看到一个不认识的 `extra_body` 字段并忽略。
结果模型拿不到 `source_lang/target_lang`，自己乱猜：

```
原文: ええ。
  [extra_body 嵌套(现状)]   328ms  Yes.          <-- 翻成英文
  [顶层 translation_options] 188ms  嗯。          <-- 正确
原文: なんかすごい感動している。
  [extra_body 嵌套(现状)]   500ms  それは素晴らしいですね！感動って本当に…（当成聊天回复了）
  [顶层 translation_options] 203ms  我好像特别感动。
```

同样的错误也在 `mt_openai_compat.py` 的 `enable_thinking` 上。两处都已改成顶层字段。

**Bug C（顺带）：`qwen-mt` 的 `rolling_context` 声明成 False**，
导致 pipeline 在调用前把 `request.history` 清空，`tm_list` 永远是空的 —— 翻译记忆从未生效。

### 9.2 翻译性能：qwen-mt-turbo 修好后

| | 旧（本地代理 gemini-3.1-flash-lite） | 新（qwen-mt-turbo，修好后） |
|---|---|---|
| p50 | 2969ms | **282ms** |
| max | 4344ms | **360ms** |
| 12 句串行总耗时 | 35.5s | 3.5s |

**快了约 10 倍。** 翻译从预算里最大的一项变成了最小的一项。

### 9.3 端到端实测（`scripts/pipeline-e2e.py`）

用 130 秒真实直播音频跑完整 pipeline（真 ASR + 真翻译 + 真 CueStore）：

```
timingSourceCounts     = {'asr': 39, 'vad': 0, 'approx': 0}   <-- 39/39 全部用服务端真值
forcedCommits          = 0
unjoinedFinals         = 0
translationAttempts    = 39
translationFailures    = 0                                     <-- 39/39 全部译出中文
translationWorkersAlive= 4 / 4
translationBacklog     = 0
avgTranslationLatencyMs= 292.8
readyLagP50 / P95      = 0.858s / 1.269s
pcmDropped/teeDropped/asrReconnects = 0 / 0 / 0

句长: n=39 p50=1.64s p90=3.24s max=5.90s
需要的观看延迟 (p90句长 + readyLag p95) ≈ 4.5s
```

不变量全部通过：每个 cue 都有 tStart、tStart ≤ tEnd、cue 不重叠（最大 0.222s，
是服务端自己报的相邻段轻微交叠，调度器按 tEnd 大者优先确定性解决）、
全部到达终态、译文齐全、worker 全存活。

### 9.4 延迟预算复核：15 秒非常宽裕

| 场景 | 句长 | readyLag(ASR+翻译, 实测 p95) | 需要延迟 | 15s 余量 |
|---|---|---|---|---|
| p50 | 1.6s | 1.3s | 2.9s | 12.1s |
| p90 | 3.2s | 1.3s | 4.5s | 10.5s |
| 本次最长 | 5.9s | 1.3s | 7.2s | 7.8s |
| 历史观测最长真实长句 | 10.5s | 1.3s | 11.8s | 3.2s |

**结论：15 秒延迟下，连历史上观测到的最长真实长句（10.5s 双句）都能从 tStart 整句显示。**

### 9.5 P2（稳定前缀分句）判定：不需要做

原设计里 P2 是为了救「句长 + 翻译延迟超过预算」的长句。
现在翻译只要 0.3 秒、观看延迟 15 秒，预算余量最少也有 3.2 秒。
**前缀分句的收益（提前 4.3s）已经小于现有余量，而它会把精确的 VAD 时间戳换成估算值。**

保留 §2.4 的设计供将来参考；只有在遥测显示
`句长 p95 + readyLagP95 > 观看延迟 - 2s` 时才值得启用。

### 9.6 实际改动清单

| 文件 | 改动 |
|---|---|
| `providers/base.py` | `ASREvent` 新增 `item_id` |
| `providers/asr_qwen_realtime.py` | 时间戳改从 `speech_started/stopped` 读；删掉 `completed` 里的死代码；全事件带 `item_id` |
| `providers/mt_qwen_mt.py` | `translation_options` 移到顶层；`rolling_context` → True |
| `providers/mt_openai_compat.py` | 自动识别 `qwen-mt*` 模型改用翻译接口（无 system role）；`enable_thinking` 移到顶层 |
| `subtitle_pipeline.py` | `_server_to_pipeline` 面包屑映射；`item_id` 键控 VAD span 取代全局单槽；删除 `vad_event_lag` 与重复扣静音；4 个翻译 worker；worker 异常隔离；去重加时间条件；`_ingest_is_unhealthy` 不再匹配日志尾巴；新增 8 个 status 字段 |
| `providers/config.py` | 默认 `silenceDurationMs=400`、`maxUtteranceSeconds=0`、`translationWorkers=4`、`anchor="start"`；删 `vadEventLagSeconds` |
| `server.py` | `--publish-delay` 默认 2 → 8；传 `translation_workers` |
| `web-player/subtitle-scheduler.js` | 显示窗口 `[tEnd, tEnd+hold]` → **`[tStart, tEnd+tail]`**；`minDwell` 1.2 → 0.6；`maxTail` 1.5 |
| `web-player/player.js` / `index.html` | 延迟选项加 15 秒并默认 8 秒；预算计算计入句长；新增句长/时间戳来源/客户端丢弃三项遥测 |
| `runtime/providers.json` | `silenceDurationMs` 600 → 400；`fallback` 清空；新增 subtitle 字段；超时 6 → 8 |
| tests | pipeline 测试重写为 item_id 契约（新增交错、丢包、去重、commit 开关用例）；scheduler 测试重写为 tStart 契约 |

### 9.7 尚未验证的部分

- **播放器的 `[tStart, tEnd]` 调度只有单元测试覆盖（13 个 node 测试全过），没有在真实直播里肉眼验证** ——
  测试期间源直播已结束（`また遊びに来てね。バイバイ。`），拿不到可用的直播 URL。
  下次开播时需要人工确认：字幕在说话人开口瞬间整句出现。
- `fallback` 已清空。若要恢复冗余，需要给 `bailian-qwen-mt-flash` 配 `DASHSCOPE_API_KEY`，
  否则它会在主 provider 抖动时把流量导向一条 100% 失败的腿。
- P3 里 `clean_subtitle_text` 的过滤阈值未调整（实测 `finalDiscarded=1`，影响很小）。
