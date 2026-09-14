# 02 — Deepgram 与 OpenAI Realtime 多语言 STT

**What to build:** 在 Ticket 01 的统一语言和采样率合同上，新增两个轻量原生 WebSocket Adapter：Deepgram Streaming 负责全球多语言/code-switching/时间戳，OpenAI Realtime Transcription 负责 OpenAI 生态；同时刷新现有 DashScope model presets。

**Blocked by:** 01 — BCP 47 语言合同、配置迁移与选择 UI.

**Status:** done — fake-protocol 全覆盖；无真实凭据，保持 provider-claimed，未做 live spike

- [x] 先用本地 fake WebSocket 写 Deepgram 失败测试：握手参数、binary PCM、interim/final、`speech_final`、word timing、detected languages、Finalize/KeepAlive/CloseStream 和错误关闭。（`tests/test_deepgram_streaming.py`）
- [x] 实现 `deepgram-streaming`，只使用官方公开协议与 `aiohttp`；specified 与 `language=multi` 映射到统一 policy，Provider 事件 canonicalize 后进入 `ASREvent`。（`companion/providers/asr_deepgram_streaming.py`）
- [x] 先用当前官方 transcription-session fixture 写 OpenAI Realtime 失败测试：24 kHz PCM、append、delta/completed、item_id、VAD、usage/languages 与关闭。（`tests/test_openai_realtime_transcription.py`）
- [x] 实现 `openai-realtime-transcription`；分别提供 `gpt-live-transcribe` 与 `gpt-transcribe` presets，不复用 legacy realtime-preview payload，不伪造 word timestamps。（`companion/providers/asr_openai_realtime_transcription.py`）
- [x] Adapter 不实现第二套通用重连；Pipeline 继续拥有掉线退避，Adapter 只处理本 session keepalive/finalize/Provider 强制 rollover。（无 rollover 要求；Pipeline 重连由 `DeepgramPipelineIsolationTests` 覆盖）
- [x] 更新 Provider Catalog UI kind/options 与 capability presets；缺少 key、非法候选、模型能力不匹配在启动前给出明确错误。（config.py、player.js、`requires_api_key`、server 预检、detection_tags）
- [x] 刷新 DashScope Fun-ASR/Qwen-Audio presets；复用现有 `dashscope-task-asr` 和 `dashscope-qwen-realtime`，不新增重复协议 kind。（官方服务端事件字段已对照当前文档确认，映射未变）
- [ ] 至少完成 fake protocol 全覆盖；有可用凭据时分别运行 10 分钟真实直播 spike，并把结果记录为 verified，否则保持 provider-claimed。（fake protocol 全覆盖已完成；环境无 DEEPGRAM_API_KEY/OPENAI_API_KEY，live spike 未运行，两个新 Provider 均保持 `provider_claimed`，README 已注明未验证）
- [x] 所有停止、错误和背压测试证明 STT 不影响播放主链路。
