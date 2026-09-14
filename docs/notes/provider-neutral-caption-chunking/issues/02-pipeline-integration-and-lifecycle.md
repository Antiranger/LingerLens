# 02 — Caption Chunker 管线集成、时间映射与生命周期

**What to build:** 将 Ticket 01 的 Provider-neutral `CaptionChunker` 接入唯一 `SubtitlePipeline`，让一个 ASR Utterance 产生多个 Caption Chunks，再通过现有 MediaAnchor/CueStore/翻译队列形成 Subtitle Cues。strict evidence paths 保证 `<=6s`；其余路径诚实报告 best-effort overshoot。保留 fallback，不改变媒体路径。

**Blocked by:** 01.  
**Status:** ready

**Primary ownership:**

- `prototype/hls-companion/companion/subtitle_pipeline.py`
- `prototype/hls-companion/companion/server.py`
- `prototype/hls-companion/companion/subtitle_store.py`（仅必要兼容字段；优先不改）
- directly related pipeline/server/lifecycle tests

## Red integration tests

- [ ] 先写长 ASR Utterance 红灯：20 秒 immutable-token stream 产生多个 exact-timestamp cues，最大 span `<=6s`，final endpoint 不再产生重复 20 秒整句 Cue。
- [ ] stable-prefix、mutable-token LocalAgreement、mutable-text、final-only 各写一条 pipeline tracer test，证明共享 chunker 而非 Provider-specific branch 生成 Cue，并断言各自 guarantee tier/timingSource。
- [ ] 写无事件计时红灯：speech_started 后 6 秒没有 ASR event，PCM sender 的 frontier 仍驱动 `advance_audio()` 并请求 commit 或标记 pending evidence。
- [ ] 写 commit 红灯：有稳定 evidence 时 20 秒长讲话也不调用 `stream.commit()`；无稳定 evidence + manual commit 时只在 hard boundary 调用，并有 4 秒 cooldown。
- [ ] 写 reconnect/Stop 红灯：已稳定 residual 被 flush；mutable tail 丢弃并计数；旧 item/session snapshot 不污染新 session；重复 Stop 幂等。
- [ ] 写 malformed timestamp red test：降级到 stable-text/final timing source，不伪造 word-level `asr`。

## Pipeline integration

- [ ] `SubtitlePipeline` 实例化唯一 `CaptionChunker`，把 Adapter observation 先通过现有 `_server_to_pipeline()` 映射到 pipeline PCM clock，再 feed；Chunker 不知道 PDT/MediaAnchor。
- [ ] 每次 PCM chunk push 后以 `_last_sent_pcm_offset` 调用 `advance_audio()`；ASR 沉默时 hard deadline 仍有时钟输入。
- [ ] `CaptionChunk` 复用现有 `_PendingFinal`/anchor/CueStore 路径；不要建立第二个字幕 Store、timeline 或 worker 生命周期。
- [ ] token 精确时间使用 `timingSource="asr"`；stable-prefix frontier 使用 `vad`/`approx`；不得按字符数插值为精确 source time。
- [ ] 一个 Caption Chunk 只生成一个 Cue；visual line wrap 仍是前端展示问题。
- [ ] utterance final/endpoint 调用 `flush_utterance()` 并 reconcile residual；对已由 chunker 消费的 prefix 不再走旧整句 final materialization。
- [ ] Providers 没有新 evidence 或 evidence malformed 时，保留现有 final-only行为作为降级，不破坏当前适配器。

## Retire overlapping legacy behavior

- [ ] 对已经由 CaptionChunker 处理的 event，禁用旧 `newly_confirmed_sentences()` prefix cue 路径，避免双重切分。
- [ ] 旧 punctuation-prefix splitting 只作为尚未迁移/无 normalized evidence Provider 的兼容 fallback；不要维护两套 6 秒算法。
- [ ] 旧 `maxUtteranceSeconds` 不能继续表示 Caption Chunk 长度；迁移为 hard-commit safety 设置或废弃文案，默认仍不频繁 finalize。
- [ ] 更新“one ASR final = one cue”的注释和 tests，只保留为 final-only fallback 合同。

## Hard commit orchestration

- [ ] Pipeline 根据 `ChunkerDecision.request_hard_commit` 和 Provider capability 调用 `stream.commit()`；作用域为当前 open utterance/session。
- [ ] cooldown 至少 4 秒；commit failure 关闭该 session 的 hard-commit 能力并回退，不终止 ASR/媒体。
- [ ] Soniox/其他有 local stable evidence 的正常运行必须 `manualHardCommits=0`。
- [ ] OpenAI Realtime 等 text-only Provider 可在证据无法满足 6 秒时 hard commit；focused test 证明后续音频继续同 session。

## Timing and state invariants

- [ ] token paths 的 Cue `tStart/tEnd` 来自 lexical bounds；estimated paths 使用 utterance start/audio frontier 并标记 `vad|approx`；连续 Chunk 可有真实小 gap/overlap，renderer 负责桥接。
- [ ] Pipeline 为每次 Start 建立 generation-local `chunk_order`，从 1 单调递增；同 observation 多 Chunk 按 lexical 顺序分配，重复 order 不允许。
- [ ] `starts_mid_sentence/ends_mid_sentence/cut_reason/generation/chunk_order` 保存在 pipeline/translation request 所需内存对象；除非 UI 需要诊断，不要求扩大 Cue 公共 JSON。
- [ ] diarization speaker change 需稳定至少两个 lexical units 或 final turn 证据；抖动不能造成大量零碎 Chunk。
- [ ] ASR language switch 按当前 Chunk dominant/canonical language；mixed chunk 可使用 `fr+en` 等既有 generic-translation表达。

## Status and errors

- [ ] 增加 spec 定义的 chunk count/span/cut reason/overshoot/local-agreement/manual-commit/residual telemetry；不输出 transcript。
- [ ] 6 秒前无稳定 evidence 是 `degraded/pending evidence` 诊断，不是媒体 error。
- [ ] translation/source-only 失败不回滚 Chunk、ASR 或媒体。

## Lifecycle matrix

- [ ] 按 Spec 表逐项测试 normal endpoint、application Stop、transport reconnect、Provider switch/new Start、startup failure 的 provider flush、stable residual、mutable tail、late event 和 generation 行为。
- [ ] Start → Stop；Stop → Start；同 Provider 重启；切换 ASR Provider；ASR reconnect；pipeline startup failure；application cleanup。
- [ ] `flush_utterance()` 按 generation/item 幂等；Stop drain + endpoint + late final 不能重复 residual。
- [ ] 每条路径清理 chunker ledger、LocalAgreement snapshots、pending finals、translation refs 和 generation callbacks。
- [ ] 原有 tee、PCM queue、FFmpeg 和 MediaAnchor tests 全绿。

## Verification

- [ ] focused `test_subtitle_pipeline.py`、adapter integration/server status tests。
- [ ] 完整 `npm run test:hls-companion`，Python compile，LSP diagnostics，scoped `git diff --check`。
- [ ] 本 Ticket 不修改 translation prompt/context；不修改 media ingest、player 或 live messages。
