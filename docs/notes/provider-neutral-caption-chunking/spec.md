# LagLingo Provider-neutral 6 秒字幕分块与连续翻译 Spec

**Status:** ready-for-agent  
**Scope:** 所有现有 ASR Provider 共用的 Caption Chunker；单个 Caption Chunk 的目标最大语音跨度为 6.0 秒；改进跨块翻译衔接；不修改媒体 ingest、播放器时钟或 Provider 选择模型

## 1. Problem Statement

LagLingo 当前把 ASR Utterance 近似当成 Subtitle Cue。这个假设在主播持续说话、背景音乐导致 VAD 不停、或 Provider 的 semantic endpoint 长时间不结束时失效。真实 Twitch `hello_kiko` 会话已观察到 20–34 秒 ASR Utterance；15 秒播放延迟无法让这种 Cue 在第一个字播放前准备好。

当前提前切分只对 `ASRCapabilities.stable_prefix=true` 的 Provider 生效，并且主要等待稳定前缀中的强标点。它既不能覆盖全部 ASR Adapter，也不能保证 6 秒上限。另一方面，多 worker 翻译完成顺序可能不同于源文顺序，当前 `RollingContext` 又在翻译完成时追加 pair，导致后续请求可能看到乱序、缺失直接前文或不必要的较远历史。

本 Spec 将四件事分开：

1. **ASR Utterance**：Provider 认为的一轮讲话，可以持续很久；
2. **Recognition Evidence**：Adapter 提供的稳定 token、稳定前缀、可比较 partial snapshot 或 final；
3. **Caption Chunk**：LagLingo 为可读性和延迟主动划分的最多 6 秒字幕块；
4. **Translation Continuity Context**：按源文顺序提供的直接前文，用于让独立翻译的块连读自然。

此设计必须具有 Provider 泛用性。Soniox 只是能够提供最高等级 token finality 的一个 Adapter，不拥有分块策略；所有 Provider 都进入同一个 Caption Chunker。

## 2. Outcome

完成后：

- 所有现有 ASR Adapter 都映射到一个统一 Recognition Evidence 合同；
- 唯一的 `CaptionChunker` 根据稳定性证据、时间、标点、停顿、说话人和语言组选择断点；
- 所有 Provider 都以 **6.0 秒**为 Caption Chunk deadline；有 token timestamps、`<=6s` final window 或经 focused test 验证的安全 manual commit 时提供严格上限，其余 evidence 路径提供 best-effort 上限并显式报告 overshoot；
- Provider 的 ASR Utterance 可以跨越多个 Caption Chunk，分块不要求结束识别会话；
- 能本地切稳定文本时不调用 Provider `commit/finalize`；只有缺少可提交证据且该 Provider 的正式合同支持 manual commit 时，才在 6 秒硬边界使用它；
- 翻译请求始终看到按源文顺序排列、且只早于 CURRENT 的 Continuity Context；
- 通用 LLM 翻译 Prompt 明确处理“上一块的延续”和“下一块仍将继续”，要求并验收衔接、术语、主语、时态和语气一致，同时只输出 CURRENT 的译文；
- Caption Chunk 写入 CueStore 后源文和时间不可变；翻译只允许一次终态更新，不建立播放器显示确认协议。

## 3. Canonical Terms

`CONTEXT.md` 中以下术语为本 Spec 的权威语言：

- **ASR Utterance**：Provider 检测的一轮讲话，可能包含多个 Caption Chunk。
- **Stable Token**：Provider 明确确认、文本和时间不会继续改写的 lexical token。
- **Policy-Committed Token**：来自 mutable ASR snapshot、经过 LagLingo LocalAgreement 后不可撤销发布的 lexical token；它仍可能与后续 Provider final 不同。
- **Caption Chunk**：由按时间排序的 Stable Tokens 或 Policy-Committed Tokens 构成、受 6 秒 deadline 约束、最终映射为一个 Subtitle Cue 的可读单元。
- **Subtitle Cue**：已进入 Media Wall Clock 调度的 Caption Chunk。
- **Translation Continuity Context**：CURRENT 之前、按源文顺序提供的 Caption Chunks 与可用译文。

不得继续在注释、测试或文档中使用“one ASR final = one cue”作为通用不变量。新不变量是：

```text
one Caption Chunk = one Subtitle Cue
one ASR Utterance = one or more Caption Chunks
```

## 4. Scope and Non-goals

### 4.1 In scope

- 当前所有 ASR kinds：DashScope Qwen Realtime、DashScope Task ASR、Soniox、Deepgram、OpenAI Realtime Transcription、OpenAI Audio Transcriptions、AssemblyAI Streaming、Volcengine SAUC、ElevenLabs Scribe Realtime、Speechmatics Realtime、Tencent ASR。
- Provider-native immutable tokens、native stable prefix、mutable partial local agreement、manual commit 和 final/window fallback。
- 以 canonical BCP 47 primary subtag 选择少量内部语言策略。
- 通用 LLM Prompt 和 Qwen-MT `tm_list` 的连续上下文。
- 新增 chunking/status telemetry 与 deterministic fixtures。

### 4.2 Out of scope

- 训练新的神经分段模型、POS tagger、依存句法模型或大型 NLP 依赖；
- 为每种语言建立公开插件系统或动态注册框架；
- 可见的 partial translation、已经显示字幕的反复改写；
- 改变 Media Wall Clock、HLS 延迟、播放器布局、媒体 ingest 或 ASR Provider 协议本身；
- 用 LLM 在线决定每一个断点；
- 为了满足 6 秒而伪造 lexical timestamps、切开单个 Provider token、丢词或重复文本。

## 5. Minimal Architecture

### 5.1 One provider-neutral seam

新增一个深 Module：

```python
@dataclass(frozen=True)
class ChunkerDecision:
    chunks: tuple[CaptionChunk, ...] = ()
    request_hard_commit: bool = False
    pending_evidence: bool = False

class CaptionChunker:
    def observe(self, observation: CaptionObservation) -> ChunkerDecision: ...
    def advance_audio(self, frontier_pcm: float) -> ChunkerDecision: ...
    def flush_utterance(self, item_id: str | None) -> ChunkerDecision: ...
    def reset(self, generation: int) -> None: ...
```

调用者只需要按顺序输入 Recognition Evidence。Module 内部拥有：

- stable/mutable ledger；
- LocalAgreement；
- 6 秒时长规则；
- boundary candidate scoring；
- 语言组策略；
- utterance residual；
- 去重、无丢词、无重排不变量；
- cut reason 与 overshoot telemetry。

不得在 `SubtitlePipeline`、各 ASR Adapter 和翻译 Provider 中各复制一套断点算法。

### 5.2 Normalized recognition contract

在 `providers/base.py` 增加：

```python
@dataclass(frozen=True)
class RecognitionToken:
    text: str
    begin_pcm: float | None
    end_pcm: float | None
    provider_stable: bool
    language: str | None = None
    speaker: str | None = None
    confidence: float | None = None

ObservationKind = Literal[
    "stable_token_delta",
    "token_snapshot",
    "stable_prefix_snapshot",
    "text_snapshot",
    "utterance_final",
    "endpoint",
]

@dataclass(frozen=True)
class CaptionObservation:
    kind: ObservationKind
    generation: int
    item_id: str
    revision: int | None = None
    tokens: tuple[RecognitionToken, ...] = ()
    stable_text: str = ""
    tentative_text: str = ""
    begin_pcm: float | None = None
    end_pcm: float | None = None
```

`ASRCapabilities` 增加 `caption_evidence: frozenset[ObservationKind]`，默认只含 `utterance_final`；hybrid Provider 可以声明多个 observation kind。现有 `manual_commit`、`word_timestamps` 等字段保留。

语义：

- `stable_token_delta`：exactly-once append，只含 Provider-confirmed lexical tokens；`RecognitionToken` 必须是共享 CaptionChunker 可以安全断开的 lexical unit，Provider tokenizer/subword piece 必须在 Adapter 边界聚合，不能直接暴露成独立 token；
- `token_snapshot`：替换该 `generation/item_id` 的完整 mutable token snapshot；空 snapshot 也是有效 rewrite；
- `stable_prefix_snapshot`：`stable_text` 是从 utterance 起点计算的完整 confirmed prefix，`tentative_text` 是可改写尾部；
- `text_snapshot`：替换完整 mutable text；
- `utterance_final`：权威整轮 final，用于 residual reconciliation，不自动生成整句 Cue；
- `endpoint`：只表示 utterance 生命周期结束；它只有在本身是该 Provider 最后可用文本证据时才触发 residual flush；若同一 utterance 随后还有 authoritative `utterance_final`，`speech_stopped` 只提供时间/生命周期，不附带会提前发布文本的 endpoint observation；
- 所有 begin/end 均为 Provider session audio seconds，进入 Chunker 前由 Pipeline 映射为 pipeline PCM seconds；
- signed/raw Provider payload 不进入 status 或日志。

`ASREvent` 可在迁移期携带一个可选 `caption_observation`，并保留旧 `text/stash/begin_pcm/end_pcm/item_id`，避免所有调用点一次迁移。

### 5.3 Evidence ladder

所有 Provider 使用同一个 Caption Chunker，但提供不同证据等级：

1. **Immutable tokens**：直接追加 Stable Tokens，并按 token timestamps 本地切分；
2. **Stable prefix**：比较新的 confirmed prefix 与已消费 prefix，只提交新增稳定文本；无 token timestamp 时使用权威 utterance start 与 audio frontier 管理 deadline，但内部 lexical span 只能标为 estimated；
3. **Mutable tokens**：对连续 snapshot 做 LocalAgreement-2，最长公共 token prefix晋升为 Policy-Committed Tokens；
4. **Mutable text**：对连续文本 snapshot 做 LocalAgreement-2，回退到语言安全边界，并保留末尾 hold-back；结果是 Policy-Committed text，不冒充 Provider final；
5. **Final only**：将 Provider final/window 作为稳定输入；严格 6 秒要求 Adapter window `<=6s` 或使用已验证 safe commit，否则只提供 best-effort 并报告 overshoot。

这不是五套 Caption Chunker，而是同一 Module 的五种输入证据。断点优先级、6 秒上限、语言策略和输出合同完全共享。

## 6. Existing Provider Mapping

实施前先以 focused adapter tests 固定以下映射；若真实代码/官方协议与此表冲突，以测试证据和 Adapter 实际 wire contract 为准，更新本表后再实现。

| Provider kind | Verified starting evidence | 6 秒主路径 | Guarantee tier |
|---|---|---|---|
| `soniox-realtime` | wire `is_final` lexical tokens + exact timestamps | stable token local cut | strict lexical span |
| `dashscope-qwen-realtime` | native confirmed `text` + mutable `stash`, utterance start/end | stable-prefix local cut；必要时 safe commit | strict deadline, estimated internal timing |
| `deepgram-streaming` | wire words on Results；`is_final` and `speech_final` semantics differ | final batches + token-snapshot LocalAgreement if fixture proves replacement semantics | strict when timestamped evidence progresses |
| `assemblyai-streaming` | Turn text/words snapshot when present | token snapshot LocalAgreement；otherwise text snapshot | strict only with timestamped committed boundary |
| `volcengine-sauc` | current utterance text/span；wire words must be fixture-proven | token snapshot if proven；otherwise mutable text/final | evidence-dependent |
| `elevenlabs-scribe-realtime` | mutable partial text；timestamped committed words | text LocalAgreement + committed final | best-effort unless safe commit/window proven |
| `speechmatics-realtime` | partial/final transcript with result timings | token snapshot when fixture-proven；periodic final；safe commit available | strict commit/final span |
| `tencent-asr` | mutable text；`wordInfo>0` may expose word list | word LocalAgreement when proven；otherwise text/final | evidence-dependent |
| `dashscope-task-asr` | mutable sentence text + sentence begin/end；word wire fields must be proven | text LocalAgreement/final；token path only with fixture | best-effort unless final/window bounded |
| `openai-realtime-transcription` | accumulated mutable delta + utterance start/end | text LocalAgreement；safe hard commit | strict commit boundary, estimated internal timing |
| `openai-audio-transcriptions` | final-only local audio windows | validate `windowSeconds <= 6` | strict audio-window span |

对于没有 token timestamps 的 stable-prefix/text 路径，6 秒是 Caption Chunk 的音频预算，不允许按字符比例回填所谓“精确词时间”。首块 `tStart` 使用权威 utterance start，块 `tEnd` 使用该 stable prefix 被确认时的 provider/local audio frontier；下一块从上一块 `tEnd` 链接。`timingSource` 必须保留为 `vad` 或 `approx`，不得冒充 word-level `asr`。

### 6.1 Manual commit policy

Provider `manual_commit` 不是首选分块方式。只有满足以下全部条件才在硬边界调用：

- 当前 evidence 在 6 秒前仍没有任何足够的 Stable Token/Stable Prefix/Policy-Committed prefix 可本地提交；
- Provider capability 明确声明 `manual_commit=true`；
- 当前确有 open ASR Utterance；
- 距上次 hard commit 至少 4 秒；
- 该 Adapter 的 focused test 证明 commit 后会继续同一 session，不丢后续音频。

可本地提交文本时，Caption Chunking 必须产生零次 `commit()`。即使 ASR 在 6 秒前没有发新事件，Pipeline 也必须从 PCM sender 调用 `advance_audio(frontier_pcm)`，由 `ChunkerDecision` 准时请求 hard commit 或报告 pending evidence。不支持 manual commit 的 Provider 在 final 到达前只能 best-effort；status 必须报告 `hardCapPendingEvidence`/overshoot，不得丢弃 mutable speech 或伪造 final 文本。

## 7. Six-second Boundary Contract

### 7.1 Fixed timing controls

```text
minimumSpan       = 1.2s
preferredSpan     = 3.0s
hardMaxSpan       = 6.0s
hardLookback      = 1.25s
strongWordGap     = 0.30s
weakWordGap       = 0.15s
localAgreementN   = 2 consecutive snapshots
```

- 6 秒指第一个 lexical unit 的开始到最后一个 lexical unit 的结束；音频 frontier 到达 deadline 不等于当前已拥有足够 lexical evidence，LagLingo 主动 hard cut 前的可提交 evidence span 仍必须达到 `minimumSpan`，不足时请求安全 manual commit 或报告 pending evidence；真正 endpoint/final 的短 utterance residual 不受此限制；
- timestamped lexical evidence、`<=6s` final window 或 safe-commit 路径的普通 Caption Chunk 不得超过 6 秒；
- stable-prefix/text-only 无内部时间路径保证 6 秒 deadline 响应，但 lexical span 标为 estimated；没有 safe commit 的 final-only/mutable-only 路径只做 best-effort，不得声称 strict；
- 不可避免 overshoot 包括单个不可分 Provider token 自身跨越 6 秒，或 Provider 在 6 秒前不提供任何可提交证据且不支持安全 commit；都必须计数并暴露原因；
- ASR Utterance endpoint、stream close 或 speaker turn 可 flush 小于 1.2 秒的 residual；
- minimumSpan 只约束普通软切，不得阻止 endpoint 或 hard cap。

### 7.2 Candidate ranking

只在可提交 lexical boundary 上切。候选得分按以下次序：

1. final strong punctuation：`. ! ? … 。！？`；
2. 经确认的 speaker change；
3. `>=300ms` lexical gap；
4. final weak punctuation：`, ; : — 、，；：`；
5. language-policy lexical boundary；
6. `>=150ms` lexical gap；
7. 最新完整 lexical unit（hard fallback）。

规则：

- 1.2 秒前只接受 ASR endpoint/speaker change，不做普通软切；
- 每次 `observe()` 或 `advance_audio()` 按当前 frontier 执行同一确定性规则：span `>=3s` 时，从 minimumSpan 后的 eligible candidates 取字典序最高 score，score 相同时取最新 boundary；这是 eager soft cut，不等待未来更强候选；
- 到 6 秒时，在 `[4.75s, 6.0s]` 范围选择最高分候选；
- 如果范围内没有语言自然候选，在不超过 6 秒的最后完整 lexical unit 后切；
- boundary scoring 可以改善选择，但不能把切分推迟到 6 秒之后；
- 不切开 URL/email、数字与小数、缩写、人名、成对标点或同一 Provider word 的 sub-token 序列；如果无法可靠判断，只使用完整 Provider token fallback。

### 7.3 LocalAgreement

对 mutable snapshot：

- 以 `item_id` 和 Provider session 为作用域；
- token 路径比较 normalized `(text, begin, end)` 序列，允许极小 timestamp jitter，但不能仅按字符前缀猜测；
- text-only 路径取连续两个 snapshot 的最长公共前缀，再退回最后一个语言安全边界；
- 永远 hold back 最后一个可能继续增长的 lexical unit；
- LocalAgreement 晋升的是 Policy-Committed 内容，不是 Provider Stable Token；晋升后不可撤销；snapshot rewrite 只替换 tentative tail；
- endpoint final 通过 Unicode normalization + token/text alignment 与已提交内容 reconcile，只生成尚未提交 residual；不可调和差异保留已发布内容、丢弃冲突 correction 并计数 `finalReconciliationConflicts`，不重复整句。

## 8. Language Policies

只实现三个 Caption Chunker 内部 policy，不建立公共语言插件系统，不增加第三方 NLP 运行时依赖。

### 8.1 Whitespace policy

适用于主要使用空格分词的语言，包括但不限于：`en, es, fr, de, pt, it, nl, sv, no, da, fi, pl, cs, ro, tr, id, ms, vi`。

优先在以下位置切：

- 强/弱标点之后；
- 连接词、从属连词、介词引导的新从句之前；
- 300ms/150ms gap；
- speaker change。

第一版 lexical tables 只覆盖高频安全提示，不做完整句法分析：

- shared Latin connectors：`and, but, because, so, then, however, although, though, while, if, when, which, that`；
- Spanish：`y, pero, porque, entonces, aunque, mientras, si, cuando, que`；
- French：`et, mais, parce que, donc, pourtant, bien que, si, quand, que`；
- German：`und, aber, weil, deshalb, obwohl, während, wenn, dass`；
- Portuguese/Italian 可按同样少量表补充。

这些词仅用于候选加分，不能单独突破 minimum/hard 时间规则。避免切在冠词+名词、助动词/否定词+动词、人名、数值和缩写内部；没有可靠证据时宁可使用时间上最近的完整词。

### 8.2 CJK policy

适用于 `zh, ja, ko`。它们共享“不依赖空格和 Latin word count”的策略，但各自有少量边界提示：

- Chinese：`。！？；：，、`，以及已稳定的语气/连接边界，如 `但是、所以、然后、因为、如果、而且`；
- Japanese：`。！？、`，以及 `けど、けれど、でも、だから、それで、ので、から、なら、たら` 等从句连接；避免切在格助词、接续助词、助动词、数词+助数词和专名内部；
- Korean：`.?!,` 与 `하지만, 그래서, 그리고, 때문에, 만약` 等连接；避免把黏着语尾/助词与词干分开。

如果 Provider token 是 subword/character 而不是 word，只在 Provider 给出同一 word/group ID、标点边界或有界 duration/gap 证据时合并，保留首尾时间；不得把整段连续无空格 CJK 合并成一个不可切单元。第一版不声称完成形态学分词。硬边界优先级为：永不切开单个 Provider token；除此之外 6 秒 deadline 高于助词、专名等启发式保护；语言自然度是 best-effort，由人工验收衡量。

### 8.3 Universal fallback

未知语言、混合语言或无法可靠 canonicalize 时：

- Unicode 强/弱标点；
- speaker change；
- token gap；
- Provider lexical token boundary；
- 6 秒 hard fallback。

不得因语言未知而禁用分块。

## 9. Translation Continuity Contract

### 9.1 Source-order history

并行翻译可以乱序完成，但 Continuity Context 不得乱序。

Pipeline 每次 Start 建立一个 generation-local、从 1 开始单调递增且不复用的 `chunk_order`；同一 observation 产生多个 Chunk 时按 lexical 顺序连续分配。`RollingContext` 的每条记录保存 `generation/chunk_order/media_t_end`，重复 order 拒绝。查询接口必须：

```python
history(
    generation=current_generation,
    before_order=current_order,
    at_media_time=current_t_end,
)
```

- 只返回同 generation 且 `order < current_order` 的已完成 pair；
- 按 source order 排序，不按请求完成时间排序；
- 不包含 CURRENT、未来 Chunk 或 live-message 翻译；
- `contextSeconds` 使用 `current_t_end - pair.media_t_end`，不使用翻译完成 wall time；
- 仍受 `contextPairs` 和 `contextSeconds` 限制；
- 如果直接前块尚未翻译，可以缺失，不为等待上下文而串行化所有翻译；
- 默认保留最近 4–6 个 pair，Prompt 强调最近 1–2 个是直接衔接，上层配置仍可调整。

### 9.2 Generic LLM prompt

所有通用 Prompt Provider 共用 `translation_prompt.py`。System contract 至少包含：

```text
HISTORY 按时间顺序列出 CURRENT 之前的字幕块，仅用于连续性。
CURRENT 可能从上一块中途接续，也可能在当前块结尾保持未完。
只翻译 CURRENT，不重复、不总结、不改写 HISTORY。
不要猜测或补完尚未出现的下一块内容。
如果 CURRENT 语法或语义未完，目标文本也保持自然的未完状态；不要擅自补句号或结论。
保持与最近前文一致的主语指代、人称、时态、语气、礼貌级别、专名和术语。
当 CURRENT 以连接词、代词、省略主语或承接结构开头时，利用 HISTORY 译出自然衔接，但输出仍只对应 CURRENT。
相邻块连读应像一段连续讲话，而不是互相独立的摘要。
只输出目标语言译文，不加说明、标签或引号。
```

User payload 应明确区分：

```text
PREVIOUS CONTEXT (oldest → newest):
[order] source -> target

CURRENT CHUNK:
source text

CHUNK POSITION:
starts_mid_sentence=<true|false|unknown>
ends_mid_sentence=<true|false|unknown>
```

为避免扩大所有 Provider 的公开接口，`generation/chunk_order/starts_mid_sentence/ends_mid_sentence/cut_reason` 作为 `TranslationRequest` 的向后兼容可选字段；旧调用点默认 `None/unknown`，不能把未知伪装成 false。定义闭集 `CaptionCutReason = terminal_punctuation | weak_punctuation | speaker_change | strong_gap | weak_gap | language_hint | hard_deadline | utterance_endpoint | final_window`。Caption Chunker 根据 boundary 语义设置：

- terminal punctuation：`ends_mid_sentence=false`；
- weak/gap/language/hard：`ends_mid_sentence=true`；
- utterance endpoint 只说明 ASR Utterance 结束；如果 residual 本身没有 terminal punctuation，`ends_mid_sentence=unknown`，不能假设语法完整；
- 前一 Chunk `ends_mid_sentence=true` 时，下一 Chunk `starts_mid_sentence=true`；unknown 继续保持 unknown。

### 9.3 Qwen-MT

Qwen-MT 不使用通用 Prompt，但必须获得同样 source-order 的 `tm_list`。第一版不为 Qwen-MT 增加未经官方协议证明的自由文本指令字段；连续性依赖按序 translation memory、术语表和当前短块。若真实验收表明专用 MT 无法处理 mid-clause chunk，记录为 Provider 限制并由通用 LLM fallback 处理，不伪造协议参数。

### 9.4 Publication stability

- Chunk 一旦进入 CueStore，源文与 `tStart/tEnd` 不再因后续 partial 重写；
- 每个 Cue 的译文最多发生一次终态更新：`src/translating -> done|failed`；
- 本功能不发起跨块 re-translation，也不需要知道播放器是否已经实际显示 Cue；
- 本 Spec 不实现 visible re-translation 或 display acknowledgement。

## 10. Lifecycle and Failure Modes

| Transition | Provider action | Stable/Policy-Committed evidence | Mutable tail | Late events |
|---|---|---|---|---|
| normal utterance endpoint | 不额外 finalize | flush residual once | discard | same generation endpoint/final only用于 reconcile |
| application Stop | 先按现有协议 graceful `flush/aclose` 并在有界 drain 内消费 final | drain 后 flush remaining committed residual once | discard | generation closed 后忽略 |
| transport disconnect/reconnect | 不向断开的 stream finalize | flush already committed residual once | discard and count | old generation 全部忽略 |
| Provider switch/new Start | stop old pipeline before new one | old generation按 Stop 规则 | discard | new generation 从 order 1 开始 |
| startup failure | best-effort close | 未公开 evidence 可直接 reset | discard | ignore |

`flush_utterance()` 必须按 generation/item id 幂等，避免 Stop drain、endpoint 和 reconnect 重复产生 residual。

- malformed/missing token timestamps 回退到 stable-prefix/text/final evidence，不伪造 word-level timestamp；
- mixed speaker tokens：仅在 speaker change 连续稳定至少两个 lexical units 或 Provider final turn 明确时作为强边界，避免 diarization 抖动；
- background music/singing：VAD 可能不结束，6 秒 hard cap 仍生效；歌词重复不得被现有 final dedupe 错删；
- 单个翻译失败只产生 source-only Cue，不阻塞后续 Chunk；
- Caption Chunking 不改变媒体、ASR PCM 或翻译 worker 的背压隔离。

## 11. Telemetry

`/api/status.subtitles` 最小增加：

- `captionChunks`
- `chunkSpanP50/P95/Max`
- `chunkCutReasons` map
- `hardCapCuts`
- `hardCapOvershoots`
- `hardCapPendingEvidence`
- `localAgreementCommits`
- `localAgreementRewrites`
- `manualHardCommits`
- `residualFlushes`
- `translationContextMissingImmediatePredecessor`

不得把 raw transcript、Provider payload、API Key 或音频写入 status/log。

## 12. Deterministic Acceptance Criteria

### 12.1 Shared chunker

- bulk fixture 必须按 observation timestamp/frontier 顺序重放 `observe/advance_audio`；同一时序证据无论 transport batch 如何分包，输出 token partition 完全一致；
- 不丢词、不重复、不重排；
- strong/weak punctuation、speaker、300ms gap、语言提示和 hard fallback 按优先级选点；
- strict guarantee paths 的普通 Chunk span `<=6.0s`；best-effort paths 不伪装 strict，overshoot 有明确 evidence tier/reason；endpoint residual 可 `<1.2s`；
- 6 秒 frontier 即使没有 ASR event 也通过 `advance_audio()` 触发 hard decision，不依赖标点或 VAD；
- 第三个 mutable snapshot 改写 LocalAgreement-2 已提交内容时，已发布文本不回写并计数 final reconciliation conflict；
- 覆盖空/缩短 snapshot、timestamp jitter/crossing、复用 item id、CJK punctuation 分离 token、URL/小数跨 hard boundary、重复歌词；
- unknown/mixed language 仍分块；
- Stop/reconnect 不把旧 session evidence 带到新会话。

### 12.2 Provider fixtures

每个现有 Adapter 至少一个 fixture 覆盖其 Evidence mapping。重点验证：

- Soniox final token exactly-once，控制 token 不进入 Chunk；
- Qwen confirmed prefix/stash 分离；
- Deepgram/AssemblyAI/Volcengine/ElevenLabs/Speechmatics/Tencent/DashScope Task 的 mutable snapshot LocalAgreement；
- OpenAI Realtime hard boundary 在缺少可提交 evidence 时才 commit；
- OpenAI Audio Transcriptions window 不超过 6 秒；
- Provider final 只 flush residual，不重复已提交 prefix。

### 12.3 Translation continuity

- 翻译完成顺序 `3,1,2` 时，Chunk 4 的 history 仍为 source order `1,2,3`；
- Chunk 2 的 history 不得出现 2/3/未来 Chunk；
- prompt 包含连续性、只译 CURRENT、不重复 HISTORY、不预测未来、保持未完状态和术语/语气一致规则；
- weak/gap/language/hard cut 的 `ends_mid_sentence=true`，terminal punctuation 为 false，endpoint 无语法证据时保持 unknown；
- `chunk_order` generation-local 且唯一；`contextSeconds` 按 media time；未来 Chunk 即使先完成翻译也不能进入 history；
- Qwen-MT `tm_list` 使用相同 chronological history；
- parallel workers 保持现有并发，不因等待缺失前块而永久阻塞。

### 12.4 Regression

- Media ingest、PDT、CueStore seq/revision、播放器 scheduler 和 live messages 不改变；
- 非 stable-token Provider 的正常 final 仍产生 Cue；
- 不新增运行时 FFprobe、音频重复下载、转码或新平台逻辑；
- full `npm run ci`、Python compile、Node syntax、LSP diagnostics 和 scoped `git diff --check` 通过。

## 13. Real Acceptance

至少覆盖：

1. 英语连续讲话直播：存在原始 20 秒以上 ASR Utterance；strict evidence Provider 的 Caption Chunk 最大值不超过 6 秒，best-effort Provider 单独报告 overshoot；
2. 西班牙语或另一 whitespace language：连接词/标点断点可读；
3. 日语、中文或韩语至少两种：不依赖空格，标点/gap/hard fallback 工作；
4. 背景音乐/歌词：VAD 不结束时仍保持 6 秒上限；
5. 至少三种 Evidence 类型的真实 Provider（immutable token、stable prefix、mutable partial/final-only）；
6. 记录 source-ready、translation-success-ready、terminal-outcome 三套 latency；失败/超时不得混成成功 ready；
7. 至少人工抽查 50 个 boundary，按 natural / acceptable hard / bad 分类，bad split 目标 `<10%`；
8. strict paths 的 source Cue 在其 `tStart` 播放前进入 `done|failed` 的目标 `>=99%`（平台/Provider outage 另报）；translation-success readiness 另按成功请求统计，不用 source-only 掩盖。

Prompt 只能被要求和验收，不能数学保证翻译衔接。没有凭据或直播样本时标为 blocked，不能用 fixture 代替真实通过。

## 14. Primary Files

预期最小修改范围：

- `prototype/hls-companion/companion/providers/base.py`
- `prototype/hls-companion/companion/providers/asr_*.py`（仅 evidence normalization）
- 新 `prototype/hls-companion/companion/caption_chunker.py`
- `prototype/hls-companion/companion/subtitle_pipeline.py`
- `prototype/hls-companion/companion/context_manager.py`
- `prototype/hls-companion/companion/translation_prompt.py`
- `prototype/hls-companion/companion/providers/mt_qwen_mt.py`（仅验证 chronological `tm_list`）
- `prototype/hls-companion/companion/server.py`（配置与 status wiring）
- directly related Python/Node/browser tests
- `prototype/hls-companion/README.md`

不得为此工作重构 Provider Registry、CueStore、MediaClock、播放器设计系统、YtDlpLiveIngest 或媒体生命周期。
