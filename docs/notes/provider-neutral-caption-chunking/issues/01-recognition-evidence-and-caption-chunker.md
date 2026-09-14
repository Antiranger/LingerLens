# 01 — Provider-neutral Recognition Evidence 与 6 秒 Caption Chunker

**What to build:** 为所有现有 ASR Adapter 建立统一 Recognition Evidence 合同，并实现唯一的 Provider-neutral `CaptionChunker`。所有普通 Caption Chunk 的 lexical span 目标硬上限为 6.0 秒；Soniox、Qwen、Deepgram 等只提供不同证据，不拥有独立断句策略。

**Blocked by:** None.  
**Status:** ready

**Primary ownership:**

- `prototype/hls-companion/companion/providers/base.py`
- `prototype/hls-companion/companion/providers/asr_*.py`
- 新 `prototype/hls-companion/companion/caption_chunker.py`
- 新/直接相关 chunker 与 adapter tests

## Red tests first

- [ ] 先为 normalized `RecognitionToken`、discriminated `CaptionObservation`、`ChunkerDecision` 和 capability observation-kind set 写红灯合同测试；新字段必须有默认值，旧 test fakes/positional constructors 不被无关破坏。
- [ ] 先写纯 `CaptionChunker` 红灯测试：bulk fixture 必须按 observation/frontier 时序重放；不同 transport batching 输出相同 partition，不丢、不重、不重排。
- [ ] 先写 6 秒红灯：无标点、无停顿、同一 ASR Utterance 持续 20 秒的 strict evidence path 被划分为 `<=6.0s` chunks；没有 ASR event 到达时，`advance_audio(6.0)` 仍返回 hard decision。
- [ ] 写 candidate hierarchy fixtures：强标点、speaker change、300ms gap、弱标点、语言 lexical signal、150ms gap、hard fallback。
- [ ] 写 minimum/residual tests：普通软切不早于 1.2 秒；endpoint/stream-close 可 flush 更短 residual。
- [ ] 写不可避免 overshoot tests：单个 token 自身 >6 秒、6 秒前无稳定 evidence 且 Provider 不能 commit，必须保留文本并计数，不能切 token 或伪造时间。

## Normalized Recognition Evidence

- [ ] 增加 frozen `RecognitionToken`：`text/begin_pcm/end_pcm/provider_stable/language/speaker/confidence`；lexical/control token 区分停在 Adapter 内。
- [ ] 定义 discriminated `CaptionObservation.kind`：`stable_token_delta | token_snapshot | stable_prefix_snapshot | text_snapshot | utterance_final | endpoint`，并含 generation/item/revision/frontier 所需字段。
- [ ] `ASRCapabilities` 增加默认只含 `utterance_final` 的 observation-kind set；hybrid Provider 可声明多个 kind。保留现有 flags，不引入新的 strategy registry。
- [ ] `ASREvent` 只增加向后兼容可选 `caption_observation`；迁移期保留 `text/stash`。
- [ ] 明确每个 kind 的 append/replace/finalize 语义、空 snapshot、revision 和 provider-session timestamp 坐标。
- [ ] raw Provider payload 只用于 Adapter 内解析/测试，status/log 不暴露。

## Adapter mappings

- [ ] Soniox：每个新 `is_final=true` lexical token exactly-once 输出；保留 start/end/language/speaker/confidence；`<end>/<fin>` 不输出 token；non-final 作为 tentative snapshot；endpoint final 保持但不重复 stable prefix。
- [ ] DashScope Qwen Realtime：confirmed `text` → `stable_text`，`stash` → `tentative_text`；不伪造 word timestamps。
- [ ] Deepgram、AssemblyAI、Volcengine、ElevenLabs、Speechmatics：先用 fixture 证明 wire words 的存在与 delta/snapshot/final 语义；只有证明后映射 token snapshot，否则保留 mutable text/final。
- [ ] Tencent：`wordInfo>0` 且 fixture 证明 word list 时 token snapshot，否则 mutable text；DashScope Task 同理，不把期望中的 wire words 当既定事实。
- [ ] OpenAI Realtime：accumulated delta 映射 mutable text；OpenAI Audio Transcriptions 保持 final-only/window path。
- [ ] 每个现有 ASR kind 至少一个 focused fixture 验证 evidence 类型、事件顺序、item ID 和时间字段。

## CaptionChunker deep Module

- [ ] 唯一接口为 `observe(...) -> ChunkerDecision`、`advance_audio(...) -> ChunkerDecision`、`flush_utterance(...)`、`reset(generation)`；隐藏 ledger、LocalAgreement、language policy 和 scoring。
- [ ] 实现 immutable token append、stable-prefix delta、mutable-token LocalAgreement-2、mutable-text LocalAgreement-2、final-only residual 五种 evidence ingest。
- [ ] LocalAgreement 作用域含 provider session/generation + `item_id`；新 session 不复用旧 snapshot。
- [ ] token agreement 允许有界 timestamp jitter，但文本和顺序必须一致；text agreement 回退到最后语言安全边界并 hold back 可增长尾词。
- [ ] LocalAgreement 只产生 Policy-Committed 内容；第三个 snapshot/Provider final 冲突时不回写已发布文本，按明确定义的 normalization/alignment 只补 residual，并计数 reconciliation conflict。

## Fixed boundary rules

- [ ] 固定：minimum 1.2s、preferred 3.0s、hard max 6.0s、hard lookback 1.25s、strong gap 300ms、weak gap 150ms。
- [ ] 定义 exact score tuple、eligibility、eager soft-cut trigger 与 latest-boundary tie-break；6 秒在最近 1.25 秒选最高分，无候选则最后完整 lexical token hard fallback。
- [ ] boundary scoring 不得把 chunk 延迟到 6 秒之后。
- [ ] 不切 URL/email、数字/小数、缩写、人名、paired punctuation 或同一 lexical token；无法判断时完整 token 优先。
- [ ] Issue 01 固定 `CaptionChunk`/`CaptionCutReason`/telemetry snapshot 合同；Chunk 至少含 text、begin/end、language、speaker、cut reason、`starts_mid_sentence/ends_mid_sentence`，ordering 由 Issue 02 pipeline 分配。

## Language policies

- [ ] 仅三个内部 policy：whitespace、CJK、universal fallback；按 canonical primary subtag 选择，不新建公共注册/插件机制。
- [ ] whitespace policy 覆盖英语/西班牙语/法语/德语和共享 Latin connectors；词表只加候选分，不作硬语法裁决。
- [ ] CJK policy 对 zh/ja/ko 使用标点、gap、少量连接表达与有证据的 Provider token grouping；不把整段无空格文本合成一个 lexical unit。除单个 Provider token 外，hard deadline 高于助词/专名保护；语言自然度明确为 best-effort。
- [ ] unknown/mixed language 使用 universal punctuation/gap/token fallback，6 秒仍有效。

## Manual commit boundary

- [ ] `CaptionChunker` 不直接持有或调用 ASRStream；`ChunkerDecision.request_hard_commit` 是唯一动作建议，`pending_evidence` 表示不能严格切分。
- [ ] 只有 6 秒前没有任何可提交稳定 evidence 且 Provider `manual_commit=true` 时，pipeline 才可执行 hard commit；可本地切时 commit 次数必须为 0。
- [ ] 明确 guarantee tier：timestamped lexical/window/safe-commit 为 strict；其余为 best-effort。不能 strict 的 Provider 保留 mutable text并报告 pending-evidence overshoot，不得丢词或把 partial 当 final。
- [ ] OpenAI Audio Transcriptions 在 Adapter 配置边界拒绝 `windowSeconds>6`，不静默 clamp；覆盖 exactly 6、fractional、invalid 和 final residual。

## Verification

- [ ] focused adapter + chunker suites 全绿；现有 protocol tests 保持绿色。
- [ ] Python compile、LSP diagnostics、scoped `git diff --check`。
- [ ] 本 Ticket 不改 SubtitlePipeline/CueStore/translation prompt 的生产行为；只交付合同、Adapter evidence 和纯 chunker。
