# 新对话启动提示词

把下面整段复制到 LagLingo 项目的新对话中。主模型只负责协调和验证；每个 Ticket 恰好由一个 `kimi-k3-256k` Subagent 实现。由于当前工作区存在未提交改动，所有 Subagent 必须在当前 checkout 工作，禁止 worktree、reset、clean 或 stash。

```text
你是 LagLingo 全球语言 MVP 的总协调 Agent。工作目录是 F:/Projects/LagLingo。

目标：严格按照已有 Spec 和三个 Tickets 完成实现。每个 Ticket 必须由一个独立的 general-purpose Subagent 完成，模型必须精确指定为 `kimi-k3-256k`。你自己不直接编写 Ticket 功能代码，只负责读取上下文、按依赖启动 Subagent、检查实际改动、运行验证，并把失败信息交回同一个 Subagent 修复。

开始前必须完整阅读：
1. CONTEXT.md
2. .scratch/laglingo-global-language-mvp/spec.md
3. .scratch/laglingo-global-language-mvp/issues/01-language-contract-config-and-ui.md
4. .scratch/laglingo-global-language-mvp/issues/02-deepgram-and-openai-realtime-stt.md
5. .scratch/laglingo-global-language-mvp/issues/03-claude-gemini-translation-and-release.md
6. docs/global-language-provider-expansion-plan.md（只作为详细参考；MVP Spec 与 Ticket 优先）
7. 与当前 Ticket 直接相关的实现文件和测试。

工作区规则：
- 先运行 `git status --short`，确认并保留现有未提交改动。
- 禁止 `git reset`、`git clean`、`git stash`、回滚或覆盖用户现有改动。
- Subagent 使用 `isolation: "off"`，不能使用 worktree；worktree 看不到当前未提交基线。
- 三个 Ticket 不要同时启动。严格按 01 → 02 → 03 顺序执行，避免 Provider base/config/UI/tests 的交叉修改冲突。
- 每个 Ticket 只使用一个 Subagent。如果验证失败，使用 `resume` 继续同一个 Subagent，并把具体失败、文件和测试输出告诉它；不要另起第二个修复 Agent。
- 如果 `kimi-k3-256k` 不可用，立即停止并告诉我，不得静默换模型。
- 不要求云端凭据才能通过 CI。协议测试必须使用本地 fake HTTP/WebSocket server 和官方事件 fixture。只有环境中已有对应 Key 时才运行 live spike；没有 Key 就明确标记 provider-claimed/unverified。
- 外部协议实现前读取当前官方文档或官方 SDK/示例；不得根据旧模型名或记忆实现。
- 保持最小实现，不扩展 Spec 的 Out of Scope。

执行 Ticket 01：
- 启动一个 foreground Agent：
  - subagent_type: `general-purpose`
  - model: `kimi-k3-256k`
  - thinking: `high`
  - isolation: `off`
  - name: `ticket-01-language`
- Prompt 必须包含 Ticket 01 路径、MVP Spec 路径、CONTEXT.md 路径，以及以下要求：先读代码和测试；按 Ticket 01 逐项实现；先写最窄失败测试；保持现有行为；运行相关测试和 LSP；更新 Ticket checkbox 与 Status；报告改动文件、测试结果、未验证项。只实现 Ticket 01，不提前实现 02/03。
- Subagent 返回后，你必须检查实际文件，而不是只相信摘要；运行 LSP、Ticket 01 相关测试及必要的现有回归测试。
- 若失败，resume `ticket-01-language` 修复，直到 Ticket 01 通过。

执行 Ticket 02：
- Ticket 01 通过后，启动一个新的 foreground Agent，name=`ticket-02-stt`，其余模型参数相同。
- Prompt 指向 Ticket 02、MVP Spec、CONTEXT.md，并明确：实现 Deepgram Streaming、当前 OpenAI Realtime transcription-session 协议、DashScope preset 刷新；使用 fake WebSocket 测试；不实现第二套通用重连；不实现 Azure/Google/AWS/AssemblyAI/Speechmatics；不依赖云 Key 完成自动测试。
- 返回后检查实际改动，运行新 Provider 测试、pipeline/server 回归、LSP。
- 失败时只 resume `ticket-02-stt`。

执行 Ticket 03：
- Ticket 02 通过后，启动一个新的 foreground Agent，name=`ticket-03-translation`，其余模型参数相同。
- Prompt 指向 Ticket 03、MVP Spec、CONTEXT.md，并明确：共享 prompt contract；Anthropic Messages；Gemini GenerateContent；usage/cache-write 计费；错误分类/fallback；配置/UI/文档收口；fake HTTP 测试；不扩展到其他 STT 或完整 UI 国际化。
- 返回后检查实际改动，运行翻译/provider/pipeline/server/browser 测试、LSP。
- 失败时只 resume `ticket-03-translation`。

最终验收：
1. 运行 `npm run check:python`。
2. 运行 `npm run check:js`。
3. 运行 `npm run test:hls-companion`。
4. 运行 `npm test`。
5. 运行 `npm run guard:release`，或直接运行 `npm run ci`（若时间允许，优先完整 `npm run ci`）。
6. 检查三个 Ticket 的 checkbox 与 Status 是否与真实实现一致；不能为了宣称完成而勾选未验证项目。
7. 检查 README、providers.example.json、requirements/bootstrap/CI 是否与真实依赖和 Provider kinds 一致。
8. 最终只汇报：各 Ticket 结果、改动文件摘要、验证命令与结果、凭据导致的 live spike 缺口、剩余风险。不要声称未执行的 live Provider 已 verified。

现在开始：先读取上述文档和 git 状态，然后执行 Ticket 01。不要再向我重复询问已在 Spec/Ticket 中明确的需求。
```

## 推荐执行顺序

```text
主协调 Agent
  └─ ticket-01-language (kimi-k3-256k)
       └─ 主协调验证 / 同 Agent resume 修复
            └─ ticket-02-stt (kimi-k3-256k)
                 └─ 主协调验证 / 同 Agent resume 修复
                      └─ ticket-03-translation (kimi-k3-256k)
                           └─ 主协调运行完整 CI
```

不要并行启动 Ticket 02 和 03。它们都会修改 Provider base、config、registry、模型设置 UI、测试和文档，并行工作会产生不必要的合并冲突。
