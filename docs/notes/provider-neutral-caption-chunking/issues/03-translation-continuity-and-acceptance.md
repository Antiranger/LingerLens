# 03 — 跨块翻译连续性、Prompt 与真实验收

**What to build:** 让 6 秒 Caption Chunks 在并行翻译下仍使用按源文顺序的 Continuity Context，并强化通用 LLM Prompt，使句中切块后的译文连读自然、只翻 CURRENT、不重复前文、不预判后文。完成多语言与多 Provider 真实验收。

**Blocked by:** 02.  
**Status:** ready

**Primary ownership:**

- `prototype/hls-companion/companion/context_manager.py`
- `prototype/hls-companion/companion/translation_prompt.py`
- `prototype/hls-companion/companion/providers/base.py`（可选 request metadata）
- `prototype/hls-companion/companion/providers/mt_qwen_mt.py`
- `prototype/hls-companion/companion/subtitle_pipeline.py`（request wiring only）
- tests、`prototype/hls-companion/README.md`、真实验收结果

## Red tests first

- [ ] 先写并发乱序红灯：Chunk 翻译按 `3,1,2` 完成时，Chunk 4 的 history 必须按 source order `1,2,3`；当前实现 completion-order 应失败。
- [ ] 先写 future exclusion：Chunk 2 的 request 不包含 Chunk 2 自身或 Chunk 3；直接前块未完成时允许缺失，但绝不能出现未来块。
- [ ] 先写 Prompt contract tests：连续性、只译 CURRENT、不重复 HISTORY、不总结前文、不预测后文、保持未完状态、主语/指代/时态/语气/专名一致、只输出目标文本。
- [ ] 写 chunk-position tests：terminal punctuation `ends_mid_sentence=false`；weak/gap/language/hard 为 true；endpoint 无语法证据时 unknown；上一块未完时下一块 `starts_mid_sentence=true`。
- [ ] 写 Qwen-MT red test：并行完成后 `tm_list` 仍按 source order；不新增未被 Qwen-MT 官方协议支持的自由 prompt 字段。

## Chronological RollingContext

- [ ] Context pair 增加 `generation/chunk_order/media_t_end`；`add()` 可乱序调用，但重复 order 拒绝，查询按 source order。
- [ ] `history(generation, before_order, at_media_time)` 只返回同 generation、早于 CURRENT 的已完成 pairs；`contextSeconds` 按 current media time 与 pair media time 计算。
- [ ] 默认最近 4–6 pairs；Prompt 把最近 1–2 pairs 视为直接 Continuity Context。保留配置，不写死只用两条。
- [ ] 不为等待前一个翻译而把四 worker 全部串行化；统计 immediate predecessor 缺失。
- [ ] Cue/Context retention 清理后不返回过期或已删除 pair。

## TranslationRequest metadata

- [ ] 以向后兼容可选字段增加 `generation/chunk_order/starts_mid_sentence/ends_mid_sentence/cut_reason`；live-message 翻译等旧调用点使用 `None/unknown` 默认值。
- [ ] Pipeline 从 CaptionChunk 传入 metadata；Provider-neutral Prompt 读取它，Provider Adapter 不自行推断。
- [ ] 不把完整 ASR Utterance、未来 tentative text 或 raw tokens放进翻译请求。

## Generic LLM Prompt

- [ ] HISTORY 标记 oldest → newest，并明确只作为 CURRENT 之前的上下文。
- [ ] CURRENT 可能是前句延续，也可能下一块继续；根据 metadata 给模型准确提示。
- [ ] 强制只翻 CURRENT 对应语义；不得重复、解释、总结 HISTORY，不能提前补完下一块。
- [ ] mid-clause chunk 保持自然未完性：允许目标语言使用逗号、连接结构或省略结束标点，但不能为了完整句自行添加结论。
- [ ] 保持最近前文的指代、人称、时态、语气、礼貌级别、专名和术语；目标是相邻块连读像连续讲话。
- [ ] 对 CJK ↔ whitespace 语言的语序差异，不要求逐词对齐；可在 CURRENT 范围内自然重排，但不得吞掉 CURRENT 或借用未来内容。
- [ ] Prompt 继续简洁，避免 HISTORY 数量增长导致请求 token 爆炸；测试固定关键规则而非完整字符串快照。

## Dedicated MT behavior

- [ ] Qwen-MT 继续使用 structured `translation_options`；`tm_list` 接收 chronological history。
- [ ] 如果 Provider 不支持 rolling context，pipeline 仍传空 history，不能偷偷换 Provider contract。
- [ ] 真实测试发现 dedicated MT 在 hard mid-clause cut 上明显漏译/补译时，记录 Provider limitation；只有现有 fallback chain 可用时才切 generic LLM，不新增隐藏平台策略框架。

## Translation failure interaction

- [ ] 超时/429/5xx 的重试策略不是本 Ticket 的主要范围；但失败 Chunk 必须 source-only 并且不污染后续 Context。
- [ ] 成功 Context 只包含真实 successful source/target pair；失败前块可作为 source-only previous context 需要另起 spec，不在本轮猜造 target。
- [ ] latency telemetry 必须区分成功与失败，验收计算 boundary-to-ready P95 包含失败/超时结果。

## Multi-language deterministic fixtures

- [ ] Whitespace：英语和西班牙语至少各一段 15–20 秒 run-on，预期在 clause/connector/gap 附近切且各块 `<=6s`。
- [ ] CJK：日语、中文、韩语至少各一段；不依赖空格，不把助词/语尾/数量词明显拆散；hard fallback 仍保证上限。
- [ ] Universal：混合语言/未知标签、URL、数字、小数、缩写、emoji 和专名。
- [ ] 每组包含跨块翻译期望：模型只译 CURRENT，衔接自然，不重复前块、不补写下一块。

## Real acceptance

- [ ] `hello_kiko` 或同类英语连续讲话直播：原始 ASR Utterance 可 >20 秒；strict evidence path max `<=6s`，best-effort path 单报 overshoot；15 秒 delay 下 source-ready、translation-success-ready、terminal-outcome 分别记录。
- [ ] 至少一个西班牙语/其他 whitespace live sample。
- [ ] zh/ja/ko 至少两种真实直播。
- [ ] 至少三类 Evidence 路径真实通过：immutable tokens、native stable prefix、mutable LocalAgreement 或 final-only/window。
- [ ] 背景音乐/歌词样本验证 VAD 不结束时 hard cap 仍工作。
- [ ] 人工抽查至少 50 个断点：统计 natural / acceptable hard / bad split，bad split 目标 `<10%`；记录坏例，不用自动化结果冒充语言质量。
- [ ] strict paths 的 source Cue 在 `tStart` 播放前进入 `done|failed` 目标 `>=99%`（Provider outage 单报）；translation-success readiness 不用 source-only 结果填充。
- [ ] 人工抽查相邻译文：主语、指代、语气、连接、术语、重复、漏译、未来补全。
- [ ] 没有 Provider 凭据或语言直播样本时标 blocked，不标 pass。

## Documentation and final verification

- [ ] README 解释 ASR Utterance 与 Caption Chunk 分离、所有 Provider evidence ladder、6 秒合同、语言组策略、Continuity Context 和已知降级。
- [ ] 更新研究/验收矩阵；明确哪些 Provider 是 exact-token、estimated-time 或 final-only。
- [ ] 为真实验收发现并修复的每个缺陷增加旧实现会失败的 focused test；禁止无证据重构。
- [ ] 运行受影响 focused suites、生产 browser smoke、`npm run test:hls-companion`、完整 `npm run ci`、Python compile、Node syntax、LSP diagnostics 和 scoped `git diff --check`。
