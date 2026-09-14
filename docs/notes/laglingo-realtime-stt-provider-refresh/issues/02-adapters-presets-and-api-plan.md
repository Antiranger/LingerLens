# 02 — Realtime STT Adapter 修订、新增与 API 修改计划

**What to build:** 严格以 Ticket 01 的官方协议审计报告为输入，修订已有 realtime ASR Adapter/model presets，并为报告判定可实施的表格厂商新增 Provider 支持。每个 Provider 必须先有能复现官方 wire contract 和时间语义的失败测试，再实现最小适配。最后写出并落实 API 修改计划。

**Blocked by:** 01 — `docs/research/realtime-stt-provider-protocol-audit-2026.md` 完成且通过主 Agent 审查。

**Status:** complete

**Execution owner:** Ticket 01 验收后启动另一个 server-side `general-purpose` Agent；模型必须使用用户指定的 **GLM 5.3 Flash**，thinking/reasoning 必须设为 **high**。若该模型不可用，停止并报告，不得静默换模型。该 Agent 必须先完整读取 Ticket 01 报告；不能自行用记忆替代报告中的协议结论。

## Inputs

- `CONTEXT.md`
- `docs/research/realtime-stt-provider-protocol-audit-2026.md`
- `docs/global-language-provider-expansion-plan.md`
- 当前 Provider base/config/registry、已有 realtime adapters、SubtitlePipeline、server model-settings API、Provider Catalog UI 和 tests。

## Scope rule

- 已适配的 Alibaba、Soniox、Deepgram、OpenAI：**审计修订**，不新建重复 kind。
- 待新增的 AssemblyAI、Gemini Transcribe Live、Mistral Voxtral Realtime、Speechmatics、ElevenLabs Scribe Realtime、火山豆包 ASR、腾讯云实时 ASR：只实现 Ticket 01 判定协议和模型当前确实可用、且能以稳定官方协议/SDK测试的项目。
- 官方资料不足、仅宣发、模型名不成立、需账户私有文档、或当前依赖风险过高的项目：配置中不伪造支持；在 API 修改计划中标为 defer，并写清解除阻塞所需证据。
- 不在本 Ticket 实现说话人 UI。若统一事件契约需要 speaker metadata，只添加最小后端字段和测试，不改变现有字幕展示。

## Phase A — Red tests from official fixtures

每个现有/新增 Provider 单独建立 vertical slice，先写失败测试，至少覆盖该协议适用的项目：

- 精确 endpoint、auth 与 session-start payload；
- PCM/sample rate/channels、binary/base64/protocol framing；
- interim revision 或 stable-prefix 语义；
- transcription final 与 utterance/endpoint final 的区别；
- turn/item/request ID；
- lexical/word/VAD/control timestamp 的单位和原点；
- detected language/code-switching；
- realtime speaker/diarization 字段及 revision（若官方支持）；
- keepalive/finalize/end-of-stream/stop；
- error frame 和资源释放；
- Pipeline 保持唯一通用 reconnect owner；
- ASR 故障和慢消费不阻塞媒体链路。

时间戳测试必须包含异常边界：control token 时间为 0、缺 timestamp、final 与 endpoint 分开发送、speaker/language 后续修订。测试应能捕获用户刚遇到的 `tStart == tEnd` / 字幕过早结束类型问题。

## Phase B — Existing Adapter audit fixes

根据 Ticket 01 差异清单修订：

1. `dashscope-task-asr`
   - 为 `qwen-audio-3.0-asr-flash-streaming` 提供经官方确认的 model preset/options/capabilities。
   - 仅在 wire contract 一致时复用；若不一致则新增明确 kind，不在旧 Adapter 里堆模型猜测。
2. `soniox-realtime`
   - 对 token accumulation、boundary token、timestamp provenance、finalize/finished/error/close 做完整回归。
3. `deepgram-streaming`
   - 对 `is_final`、`speech_final`、UtteranceEnd、diarization 和语言检测声明做审计修订。
4. `openai-realtime-transcription`
   - 更新到报告确认的当前 schema/model IDs/sample rate/event names/usage；旧 schema 需要显式迁移或拒绝。

## Phase C — New Provider vertical slices

对 Ticket 01 判定可实施的 Provider，逐家按以下顺序完成，不一次性写完后统一测试：

1. Provider kind + model capability preset；
2. fake server 或 fake official-SDK facade；
3. Adapter session/audio/result/finalization/error mapping；
4. Provider Catalog UI kind 与 default/example profile；
5. server preflight validation；
6. focused provider + pipeline isolation tests；
7. README/third-party/optional dependency说明。

低层协议公开且稳定时优先原生 `aiohttp`。官方要求 SDK、gRPC、签名 EventStream 或私有 framing 时使用可选 SDK Adapter；缺依赖不得阻止 Companion 核心启动，UI/启动预检需给出精确安装提示。

## Phase D — API modification plan and implementation

创建并维护：`docs/realtime-stt-provider-api-modification-plan.md`。

计划必须列出：

- Provider kind / model preset / protocol version / `lastVerifiedAt` / official source URLs；
- 现有 `ASRCapabilities`、`ASREvent` 是否足够；若不足，最小新增字段及兼容默认值；
- speaker metadata、turn revision、timestamp provenance、language collection 的拥有者和序列化边界；
- Provider Catalog/config schema 是否需要升级和迁移；已有 profile、key、active/fallback 必须保留；
- pricing 不能只保留 CNY 单值时的最小演进方案；缺价格显示 unavailable，不填 0；
- loopback `/api/model-settings`、`/api/providers`、`/api/status` 和 `/api/subtitles` 的字段变化；
- browser compatibility：旧前端忽略新增字段仍能工作；
- optional SDK dependency/bootstrap/CI strategy；
- 每项修改的文件、测试、兼容性和 rollout 顺序。

Agent 应直接落实计划中本 Ticket 必需的最小 API 变化；未来 UI/benchmark/付费数据功能保留为后续，不做超范围实现。

## Configuration expectations

- 内置 preset 只加入官方当前有效且报告已核验的模型；未知自定义模型保持 `experimental`。
- capability 由 Provider profile + model preset 决定，不能仅按 kind 泛化。
- 表格中的价格、日语和 diarization 能力若与官方当前资料不一致，以官方资料为准，并在文档中标差异。
- 对已经配置的用户 profile 做兼容迁移；不得要求重新输入密钥。
- 每个 cloud Provider 设置 `requires_api_key` 或正确的云鉴权预检；密钥不进入普通 status/log/error。

## Verification

至少运行：

1. 每个新增/修订 Provider 的 focused tests；
2. `test_providers.py`、`test_server_providers.py`、`test_subtitle_pipeline.py`；
3. Provider Catalog browser asset tests；
4. Python compile、Node syntax、targeted LSP；
5. `npm run test:hls-companion`；
6. `npm run ci`；
7. `git diff --check` 和 release guard。

有环境中的真实 API key 时，按报告的 live spike protocol 跑短时验证并记录；没有 key 仍必须以 fake official fixtures 完成自动测试，preset 保持 `provider_claimed`，不得标 `verified`。

## Acceptance criteria

- [x] Ticket 01 报告中所有 `audit-existing` 差异都有对应测试和修订/明确无需修改的证据。
- [x] 所有新增 kind 都有独立 Adapter；没有“万能 WebSocket Provider”。
- [x] 每个实现的 Provider 都覆盖 start/audio/result/timestamp/finalize/error 生命周期的适用协议测试。
- [x] lexical timestamp、VAD timestamp 和 control timestamp 不再混用；零值/缺失值有明确 fallback。
- [x] Provider/model capability、日语、auto/code-switching、diarization 声明与官方证据一致。
- [x] 配置/API 迁移保留现有 profiles、active IDs、keys、pricing 和 subtitle settings。
- [x] 本轮未新增 SDK 依赖；核心 Companion 启动不受 Google/Mistral defer 项影响。
- [x] `docs/realtime-stt-provider-api-modification-plan.md` 与实际实现一致。
- [x] `npm run ci` 通过；未执行的 live 云验证已在文档中记录。
