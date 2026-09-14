# 03 — Claude/Gemini 翻译 Adapter 与 MVP 收口

**What to build:** 用一个共享翻译合同补齐 Anthropic Messages 和 Gemini GenerateContent，同时迁移现有 OpenAI-compatible/Qwen-MT 的 prompt、usage、错误与语言能力；完成端到端验收和文档。

**Blocked by:** 01 — BCP 47 语言合同、配置迁移与选择 UI. Ticket 02 可并行，最终验收需要 02。

**Status:** done — 共享 prompt/usage/错误合同与两个新 Adapter 全部由本地 fake HTTP server 测试覆盖，完整 `npm run ci` 全绿；凭据受限验收：环境无 ANTHROPIC_API_KEY / GEMINI_API_KEY，Claude/Gemini 保持 `provider_claimed` 未 live 验证；已用既有 DashScope 凭据对迁移后的 `openai-compatible` 路径做一次真实 API smoke（ja→zh-Hans 成功，usage 正常归一化）。

- [x] 先写共享 prompt 失败测试：canonical source/target 名称、固定 system 规则、rolling HISTORY、CURRENT 与 glossary 在 OpenAI/Claude/Gemini 间语义一致；Qwen-MT 仍走结构化 options。（`tests/test_translation_providers.py::SharedPromptContractTests`，先红后绿）
- [x] 抽取 provider-neutral prompt builder，删除 `context_manager.py`、`mt_openai_compat.py`、`mt_qwen_mt.py` 中重复且不完整的语言字典。（新增 `companion/translation_prompt.py`；`context_manager.py` 只留 `RollingContext`；Qwen-MT 名称由 canonical catalog 主语言条目 + 两条 provider 协议词汇（zh→Chinese、pt→Portuguese）派生）
- [x] 用 fake HTTP server 写并实现 `anthropic-messages`：`/v1/messages`、`x-api-key`、`anthropic-version: 2023-06-01`、top-level system、文本响应、usage（input_tokens 不含 cache read/write）和官方错误形状（401/403/400/429/500/529）。（`companion/providers/mt_anthropic_messages.py`，测试覆盖 exact endpoint/headers/payload、refusal/空响应、缺 key 不发请求）
- [x] 用 fake HTTP server 写并实现 `google-genai`：`models/{model}:generateContent`、`x-goog-api-key`、systemInstruction、contents.parts、candidate 文本、usageMetadata（promptTokenCount 含 cached，归一化时减去）、安全阻断（promptFeedback.blockReason / finishReason SAFETY）和错误形状。（`companion/providers/mt_google_genai.py`）
- [x] 统一 usage 为普通输入、cache read、cache write、输出；增加可选 cache-write 单价 `pricePerMillionCacheWriteTokensCny`，缺 usage/价格显示不可估算（不为 0），并按实际 fallback Provider 归属。（`subtitle_pipeline.normalize_translation_usage` 按形状识别 OpenAI/Anthropic/Gemini；server 透传 cacheWrite 单价；pipeline metering 对 cache-write token 无单价时标记 pricing incomplete）
- [x] 统一 ProviderAuth/Request/RateLimit/Unavailable/Timeout 错误；更新 FallbackChain，使配置错误（auth/request/语言对拒绝）本次会话内禁用该 Provider、429 立即降级并短冷却、5xx/529/超时按连续失败阈值冷却。（`companion/providers/base.py` 新增错误类；`companion/providers/http.py` 统一映射；`fallback.py` 增加 `disabled_reason` 与 429 即冷却；`asyncio.TimeoutError` 保留 deadline 语义）
- [x] Provider 能力验证：通用 LLM（openai-compatible/anthropic-messages/google-genai）open-world（`open_world_prompting=True`）；Qwen-MT 不支持的语言对与混合语言 cue 在发请求前抛 `LanguageNotSupportedError` 并交给 fallback；空 candidate/refusal/safety-block 统一抛 `ProviderRefusalError`，不算成功空译文。
- [x] 更新模型设置 kind/options（`player.js` 两个新 kind + 缓存写入单价字段）、示例配置（`runtime/providers.example.json` 增加 Claude/Gemini 示例记录）、README（翻译 Provider 一节与未 live 验证声明）、THIRD_PARTY_NOTICES（官方协议文档来源）；SECURITY.md 的 Provider Key/云端字幕边界为 provider 无关表述，已覆盖新 Provider，无需改动。CI seam 加入 `test_translation_providers.py`；无新依赖（仅 aiohttp）。
- [x] 运行完整 Python/Node/CI 测试（`npm run ci` 全绿，31 个新翻译测试 + 全部既有测试）；验收记录：无 ANTHROPIC_API_KEY/GEMINI_API_KEY，`auto/multilingual STT → Claude/Gemini → 非中文目标语言` 真实验收**未运行，保持未验证（provider_claimed）**；用既有 DashScope 凭据对迁移后的 `openai-compatible` 做了单发真实 API smoke（`ja → zh-Hans` 返回「大家好。」，usage 归一化 nonCached=225/output=2，见 `.scratch/laglingo-global-language-mvp/mt-live-smoke.py`）。

## Implementation notes

- `companion/translation_prompt.py`：`TranslationInstruction(system_text, user_text)`；`prompt_language_name` 从 Ticket 01 catalog 取稳定英文名；`fr+en` 形式的 `+` 连接 tag 表示混合语言 cue，prompt 渲染为 "mixed French and English"。
- `companion/providers/http.py`：`post_json` 统一 status→错误类映射（401/403→Auth、429→RateLimit、5xx→Unavailable、其余 4xx→Request、断连→Unavailable），`require_api_key`、`request_timeout`（含 deadline 上限）。
- Anthropic baseUrl 为 `https://api.anthropic.com`（Adapter 追加 `/v1/messages`）；Gemini baseUrl 为 `https://generativelanguage.googleapis.com/v1beta`（Adapter 追加 `/models/{model}:generateContent`）。
- usage 归一化按形状识别：`promptTokenCount` → Gemini；`cache_read_input_tokens`/`cache_creation_input_tokens` → Anthropic（不假设 input_tokens 含缓存）；其余 → OpenAI 兼容。`cacheWriteInputTokens` 进入 status/byProvider。
- FallbackChain capabilities 语言合同按并集合并：任一成员 open-world 则链 open-world，否则合并 closed pair/tag 合同。
- 官方文档来源：Anthropic <https://platform.claude.com/docs/en/api/messages/create>、<https://platform.claude.com/docs/en/api/errors>；Gemini <https://ai.google.dev/api/generate-content>（均记录在 THIRD_PARTY_NOTICES.md）。
