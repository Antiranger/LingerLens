# 04 — ASR 与翻译 Usage 计量及费用估算

**What to build:** 允许每个 Provider Profile 保存自己的计费参数，累积实际 ASR 音频秒数和翻译 API 返回的 token usage，并在前端分别展示 ASR、翻译与总估算费用；数据不完整时明确显示不可估算。

**Blocked by:** 01 — Provider Catalog 与本地 OpenAI-compatible ASR.

**Status:** complete

- [ ] 先在 Provider Catalog/API seam 写失败测试：ASR Profile 可保存 CNY/秒；翻译 Profile 可保存 CNY/百万 input、cached input、output token，重启后保持且非法负数被拒绝。
- [ ] 先在 Subtitle Pipeline seam 用独立手算样例写失败测试：`prompt_tokens=1000`、`cached_tokens=400`、`completion_tokens=200` 时，只按 600 个普通输入、400 个缓存输入、200 个输出计费。
- [ ] 归一化常见 OpenAI-compatible usage：`prompt_tokens`/`completion_tokens`/`total_tokens`、`prompt_tokens_details.cached_tokens`，并兼容 `input_tokens`/`output_tokens` 命名；未知字段被保留用于诊断但不参与猜测。
- [ ] 当前翻译 provider 返回的 `TranslationResult.usage` 不再丢弃；pipeline 按实际 result provider 累积调用次数、普通输入、缓存输入、输出、总 token 与费用，fallback 调用归属真实完成请求的 Profile。
- [ ] ASR 继续按实际送入/处理的音频秒数计量，并使用 active ASR Profile 的 CNY/秒价格；本地免费服务由用户显式填写 0，而非系统猜测。
- [ ] 缺少 usage 或任一所需价格时，相应费用为 unavailable/null；不得把“未知”显示为 ¥0，也不得引入本地 tokenizer 猜测。
- [ ] `/api/status` 和 `/api/subtitles` 状态分别返回 `asrUsage`/`asrEstimatedCostCny`、`translationUsage`/`translationEstimatedCostCny`、`totalEstimatedCostCny` 以及是否可估算的原因。
- [ ] 模型设置 UI 为 ASR 和翻译 Profile 提供计费输入与单位说明；可使用小数并清楚标注 CNY/秒或 CNY/百万 token。
- [ ] 运行数据 UI 分开展示 ASR 用量与费用、翻译 input/cached/output token 与费用、合计费用；长时间运行数字使用等宽/千位友好格式。
- [ ] 红→绿测试覆盖缓存 token、无缓存字段、fallback provider、缺失 usage、缺失价格、显式零价格和多次请求累积；现有延迟/字幕指标保持绿色。
