# Realtime STT Provider 修订执行提示词

工作目录：`F:/Projects/LagLingo`

目标：严格按两个串行 Ticket 完成 Realtime STT Provider 官方协议审计、已有 Adapter 修订、新 Provider 适配，以及 API 修改计划。主协调 Agent 不凭记忆实现外部协议；所有 wire contract 必须来自 Ticket 01 报告中的一手来源。

## 必读

1. `CONTEXT.md`
2. `.scratch/laglingo-realtime-stt-provider-refresh/issues/01-official-protocol-research.md`
3. `.scratch/laglingo-realtime-stt-provider-refresh/issues/02-adapters-presets-and-api-plan.md`
4. `docs/global-language-provider-expansion-plan.md`
5. 当前 Provider、Pipeline、server、UI 和测试文件。

## 工作区规则

- 先运行 `git status --short`，保留全部现有未提交改动。
- 使用 `isolation: "off"`；不得 reset、clean、stash、回滚或覆盖用户改动。
- 两个 Agent 严格串行，Ticket 01 验收前不启动 Ticket 02。
- 每个 Ticket 恰好使用一个 server-side `general-purpose` Agent。
- 两个 Agent 的 model 必须是用户指定的 **GLM 5.3 Flash**，thinking 必须为 `high`。若模型名在当前 Agent harness 不可解析，立即停止并向用户报告可用模型名称；不得静默换模型。
- Ticket 01 Agent 只研究并写报告，不改产品代码。
- Ticket 02 Agent 必须首先完整读取 Ticket 01 报告，并按报告提供的官方 fixture 先写失败测试。
- Agent 完成后，主协调 Agent 检查真实文件和 diff，不只相信摘要；验证失败时 resume 同一个 Agent 修复，不另起替代 Agent。

## Ticket 01

启动 foreground Agent：

- `subagent_type: general-purpose`
- `model: GLM 5.3 Flash`（使用 harness 可识别的精确 ID）
- `thinking: high`
- `isolation: off`
- `name: realtime-stt-research`

Prompt 必须指向 Ticket 01，并要求：只使用官方 API reference、官方 SDK/源码、官方 examples、官方 model/language/pricing pages；逐家输出完整协议卡；报告写入 `docs/research/realtime-stt-provider-protocol-audit-2026.md`；更新 Ticket 状态；不改产品代码。

主 Agent 验收：11 个输入条目全部覆盖；每个 start/audio/result/timestamp/finalize/error 结论有 URL；已有 Alibaba/Soniox/Deepgram/OpenAI 有具体差异清单；待新增 Provider 有 native/SDK/defer verdict；表格价格与能力经过官方核验。

## Ticket 02

Ticket 01 通过后启动新的 foreground Agent：

- `subagent_type: general-purpose`
- `model: GLM 5.3 Flash`（同一精确 ID）
- `thinking: high`
- `isolation: off`
- `name: realtime-stt-adapters`

Prompt 必须指向 Ticket 02、Ticket 01 报告和 CONTEXT.md，并要求：先写 provider-specific red tests；修订现有四种 Adapter；仅实现报告判定可实施的新 Provider；使用独立 kind；落实最小统一 API/config 变化；写 `docs/realtime-stt-provider-api-modification-plan.md`；更新配置/UI/文档/测试；运行 focused 和完整验证；不把无官方证据的模型伪装成支持。

## 最终主 Agent 验收

- 检查每个 Provider 的测试 fixture 是否匹配报告中的官方事件，而不是自创 schema。
- 特别抽查 timestamp provenance：lexical/word、VAD、control/end token、session-relative 原点和零值 fallback。
- 检查 Provider Catalog、preset、migration、requires-api-key、optional SDK dependency 和 secret masking。
- 运行 `npm run ci`、targeted LSP、`git diff --check`。
- 对有现成 key 的 Provider 可跑 live spike；没有 key 的保持 `provider_claimed`。
- 最终报告：两个 Ticket 结果、实现/延后 Provider、API 变化、测试结果、live 验证缺口和剩余风险。
