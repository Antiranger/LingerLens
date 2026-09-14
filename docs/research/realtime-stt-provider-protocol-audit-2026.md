# Realtime STT Provider Protocol Audit（2026 官方协议核验版）

> **文档状态**：官方来源全面重审版（Ticket 01，状态 `in_review`）
> **最后核验日期**：2026-09-03（所有外部引用均在本次会话中读取原文）
> **适用范围**：LagLingo HLS Companion ASR Provider 扩展；为 Ticket 02（adapters/presets/API plan）提供协议依据
> **写作约定**：
> - **[verified]**：本会话已读取官方页面/官方 SDK 源码原文，并给出 URL；
> - **[code]**：本仓库当前代码观察（引用文件与行为），不是官方契约；
> - **Unknown**：官方未公开或本次未能取证，禁止在 adapter 测试中断言。
> - 不使用宣传性表述；价格未取证的条目一律记 Unknown。

---

## 1. 审计方法与代码基线

本次审计对照了两类事实来源：

1. **官方一手来源**：各 Provider 的 API reference、协议指南、官方 SDK 源码（`soniox/soniox-python` 等）、官方 changelog 与 pricing 页。搜索摘要仅用于发现链接，所有协议字段均回读原文。
2. **当前代码基线**（`prototype/hls-companion/companion/providers/`）：
   - `base.py`、`config.py`（契约与内置 preset）；
   - `asr_soniox_realtime.py`（`soniox-realtime`，`stt-rt-v5`）；
   - `asr_deepgram_streaming.py`（`deepgram-streaming`，`nova-3`）；
   - `asr_openai_realtime_transcription.py`（`openai-realtime-transcription`，`gpt-live-transcribe`/`gpt-transcribe`）；
   - `asr_dashscope_task.py`（`dashscope-task-asr`，fun-asr/qwen/paraformer 共用协议）；
   - `asr_qwen_realtime.py`（`dashscope-qwen-realtime`，OpenAI-like realtime 事件契约）；
   - `tests/test_soniox_realtime.py`、`test_deepgram_streaming.py`、`test_openai_realtime_transcription.py`、`test_providers.py`。

**与本报告冲突的旧结论（`docs/research/realtime-stt-provider-adapters.md` 为初步调研稿）以本报告为准**；实施计划以 `docs/global-language-provider-expansion-plan.md` 为准。

### 1.1 11 家裁决总览

| # | Provider | 目标模型（用户表格） | 官方核验结果 | 裁决 | 优先级 |
| :-- | :--- | :--- | :--- | :--- | :--: |
| 1 | Alibaba | `qwen-audio-3.0-asr-flash-streaming` | 官方存在；与 Fun-ASR-Realtime 共用 `run-task` WebSocket 协议（`/api-ws/v1/inference`） | **audit-existing**（`dashscope-task-asr`） | P0 |
| 2 | Soniox | `stt-rt-v5` | 官方当前主力实时模型（v4 于 2026-06-30 下线自动路由 v5） | **audit-existing** | P0 |
| 3 | Deepgram | `nova-3` | `nova-3` 官方支持日语；`multi` 为 10 语种 code-switching 模式 | **audit-existing** | P0 |
| 4 | OpenAI | GPT Live Transcribe | 当前正式 transcription session：`session.type="transcription"` + `gpt-live-transcribe`/`gpt-transcribe` | **audit-existing** | P1 |
| 5 | AssemblyAI | Universal-3.5 Pro Realtime | 官方模型 ID 为 `universal-3-5-pro`（连字符）；Streaming v3（v2 已 410） | **native-aiohttp**（新 kind） | P1 |
| 6 | 火山引擎 | 豆包大模型流式 ASR | 大模型流式协议为 `/api/v3/sauc/bigmodel`（v3 二进制协议）；`/api/v2/asr` 是旧小模型接口 | **native-aiohttp**（新 kind） | P1 |
| 7 | ElevenLabs | Scribe v2 Realtime | `scribe_v2_realtime`；endpoint 为 `wss://api.elevenlabs.io/v1/speech-to-text/realtime` | **native-aiohttp**（新 kind） | P2 |
| 8 | Speechmatics | Enhanced / Standard | Realtime WebSocket v2 协议公开；`operating_point` 已弃用改 `model` | **native-aiohttp**（新 kind） | P2 |
| 9 | 腾讯云 | 实时 ASR（V2 speaker clustering） | 存在经典协议与大模型 2.0 协议两套 WebSocket 文档；`16k_zh_en_speaker_2.0` 属大模型 2.0 版 | **native-aiohttp**（新 kind） | P2 |
| 10 | Google | `Gemini 3.5 Transcribe Live` | **官方已存在**：`gemini-3.5-transcribe-live`，2026-08-26 GA，走 Live API | **defer**（协议细节部分取证，见 §2.10） | Defer |
| 11 | Mistral | `Voxtral Realtime 2602` | API 模型 ID 为 `voxtral-mini-transcribe-realtime-2602`；官方仅公开 SDK facade，低层 WS 帧未公开 | **defer**（可官方 SDK，见 §2.11） | Defer |

与用户表格的差异说明：Google/Mistral 两条目官方均已实装（旧报告结论过时）；AssemblyAI 模型 ID 写法为连字符；火山引擎需使用 v3 sauc 端点而非 v2。

---

## 2. 11 家协议卡

每张卡按同一结构给出：身份 → 传输/端点 → 鉴权 → 会话开始 → 音频契约 → 响应契约（含 fixture）→ 时间戳语义 → 结束/finalize → 说话人分离 → 语言策略 → 错误与限制 → 价格 → LagLingo 映射 → 裁决。

---

### 2.1 Alibaba（百炼 DashScope）— `qwen-audio-3.0-asr-flash-streaming`

**身份** [verified]
- 官方模型 ID：`qwen-audio-3.0-asr-flash-streaming`（实时）。
- 官方模型表标注：实时 / WebSocket / 热词 + prompt context / **Speaker diarization: Unsupported** / Emotion: Unsupported / 多语言含方言 / 时长无上限。
  - 来源：https://www.alibabacloud.com/help/en/model-studio/asr-model
- 同族实时模型：`fun-asr-realtime`、`fun-asr-realtime-2026-02-28`、`fun-asr-realtime-2025-11-07`、`fun-asr-realtime-2025-09-15`、`paraformer-realtime-v2` 等，与 qwen-audio-3.0 共用同一 WebSocket 协议（文档标题即 “Qwen-Audio-3.0-ASR-Flash-Streaming/Fun-ASR-Realtime ... WebSocket API”）。

**Transport 与 endpoint** [verified]
- `wss://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/inference`（新加坡）
- `wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference`（北京）
- 官方推荐迁移到 workspace 专属域名；旧域名 `dashscope.aliyuncs.com` / `dashscope-intl.aliyuncs.com` 仍可用（当前 LagLingo config 使用旧域名形式 `wss://dashscope.aliyuncs.com/api-ws/v1/inference`，仍有效）。
- `/api-v1/tasks` 属于**非实时文件转写**的 HTTP 提交/轮询接口，与实时流式协议无关，不得混用。
  - 来源：https://www.alibabacloud.com/help/en/model-studio/fun-asr-realtime-websocket-api

**Authentication** [verified]
- 握手 Header：`Authorization: Bearer <api_key>`（必填）；可选 `user-agent`、`X-DashScope-WorkSpace`、`X-DashScope-DataInspection`（缺省即 `enable`）。key 无效时握手返回 401/403。秘密仅进 Header，不进 URL。

**Session start / run-task** [verified]
```json
{
  "header": {"action": "run-task", "task_id": "<uuid-with-hyphens>", "streaming": "duplex"},
  "payload": {
    "task_group": "audio", "task": "asr", "function": "recognition",
    "model": "qwen-audio-3.0-asr-flash-streaming",
    "parameters": {"format": "pcm", "sample_rate": 16000},
    "input": {}
  }
}
```
- `parameters` 可选项（当前文档）：`format`（pcm/wav/mp3/opus/speex/aac/amr；opus/speex 须 Ogg 封装）、`sample_rate`、`language_hints`、`semantic_punctuation_enabled`（默认 false）、`max_sentence_silence`（默认 1300ms，[200,6000]）、`multi_threshold_mode_enabled`、`heartbeat`（默认 false）、`speech_noise_threshold`、`vocabulary_id`、`vocabulary`（即时热词，**仅 qwen-audio-3.0-asr-flash-streaming 支持**，权重 [1,5] 或 50）、`special_word_filter`。
- `language_hints` 上限：qwen-audio-3.0 系列**最多 4 个**；fun-asr-realtime 系列**仅 1 个**。语种码表（30 种，含 `ja`）见 client events 文档；`fun-asr-realtime-2026-02-28` 仅 `zh/en/ja`。
- `input.context`（对话上下文，最多 5 条/类型、单轮 400 字符）与 `continue-task`（任务中更新上下文）仅 `qwen-audio-3.0-asr-flash-streaming`、`fun-asr-realtime`、`fun-asr-realtime-2025-11-07` 支持。
  - 来源：https://www.alibabacloud.com/help/en/model-studio/fun-asr-client-events

**服务确认与响应** [verified]
- 服务端确认 `task-started` 后才可发音频。**注意：服务端事件用 `header.event`，客户端命令才用 `header.action`。**
- `result-generated`（`payload.output`）：
```json
{
  "header": {"task_id": "<uuid>", "event": "result-generated", "attributes": {}},
  "payload": {
    "output": {
      "sentence": {
        "begin_time": 170, "end_time": 920, "text": "OK, got it.",
        "heartbeat": false, "sentence_begin": false, "sentence_end": true,
        "sentence_id": 1,
        "words": [{"begin_time": 170, "end_time": 295, "text": "OK", "punctuation": ","}]
      },
      "usage": {"duration": 3}
    }
  }
}
```
  - `sentence_end=false` 为中间结果，`true` 为整句终态；新句首个中间结果带 `sentence_begin=true`；`sentence_id` 从 1 递增，heartbeat 包恒为 0；`heartbeat=true` 的结果可忽略。
  - `usage.duration`（计费秒数）仅在 `sentence_end=true` 时非空。
- 结束：客户端发 `finish-task` → 服务端下发最终结果与 `task-finished`；失败：`task-failed`（`header.error_code` + `header.error_message`），连接关闭且不可复用。
  - 来源：https://www.alibabacloud.com/help/en/model-studio/fun-asr-server-events

**Timestamps** [verified]：`begin_time`/`end_time` 为毫秒整数，句子级与词级同构；词级含 `punctuation` 字段。无说话人字段（实时模型官方标注 Unsupported）。

**Audio contract** [verified]：二进制帧原始音频，必须单声道；推荐 100ms 分片。

**Finalization** [verified]：`finish-task` 后仍会收到剩余 `result-generated`，直到 `task-finished`；`heartbeat=true` 参数用于持续静音保活。

**Language policy** [verified]：不设 `language_hints` 时自动检测；qwen-audio-3.0 支持最多 4 个候选（见上）。官方文档未承诺句内 code-switching 行为，候选并存 ≠ code-switching 合同。

**Errors/limits** [verified]：握手 401/403；`task-failed` 关连接；静音不保活会超时断连（`heartbeat=true` 可避免）。

**Pricing**：**Unknown**（本次未在官方页面取证到 qwen-audio-3.0-asr-flash-streaming 精确单价；`config.py` 中 `paraformer-realtime-v2` 0.00024 元/秒为既有配置值，非本次核验）。

**LagLingo mapping** [code]：`asr_dashscope_task.py` 已实现 `run-task/task-started/result-generated/finish-task`、`header.event` 解析、`begin_time/end_time` 毫秒转秒、`sentence_id` 作 `item_id`、`heartbeat` 过滤、UUID task_id、`X-DashScope-DataInspection` Header、等待 task-started 后再发音频。
真实剩余差异：
1. `language_hints` 硬编码为单个主子语言标签 [code: `stream()` 中只传 `primary_subtag(policy.tag)`]；对 qwen-audio-3.0 应允许最多 4 个候选；
2. 发送的 `parameters.hotwords` 不在当前官方参数表中（当前文档为 `vocabulary_id`/`vocabulary`）[code: `connect()`]；需按模型区分或移除；
3. `config.py` 无 `qwen-audio-3.0-asr-flash-streaming` 内置 profile（adapter `_MODEL_PRESETS` 已含 30 语种表，与官方一致）[code]；
4. 词级 `words[]` 未透传（`ASREvent` 当前无词级字段，见 §3）。

**裁决**：audit-existing（保留 `dashscope-task-asr` kind；新增 qwen-audio-3.0 preset；修正 hints/hotwords）。风险：`dashscope-qwen-realtime`（`asr_qwen_realtime.py`）是**另一条 OpenAI-like realtime 事件契约**（`wss://.../api-ws/v1/realtime` + `session.update` + `conversation.item.input_audio_transcription.text`/`completed`），不是本协议，不得合并 [code；计划文档 §4.2 同此结论]。

---

### 2.2 Soniox — `stt-rt-v5`

**身份** [verified]
- `stt-rt-v5` 为当前实时主力模型（Active）；stt-rt-v4 于 2026-06-30 起自动路由到 v5；实时会话单连接最长 5 小时。
  - 来源：https://soniox.com/docs/stt/models

**Transport 与 endpoint** [verified]
- 默认 `wss://stt-rt.soniox.com/transcribe-websocket`；区域节点如 `wss://stt-rt.eu.soniox.com/transcribe-websocket`（数据驻留）。
  - 来源：WebSocket API 文档 https://soniox.com/docs/stt/api-reference/websocket-api （本次取证经官方 pricing/models 页与官方 SDK 常量交叉确认）

**Authentication** [verified]
- 首条 JSON 配置消息携带 `api_key`。客户端应用应使用服务端生成的临时 key。

**Session start（首帧配置）** [verified]（官方 Python SDK `RealtimeSTTConfig`，https://github.com/soniox/soniox-python/blob/main/src/soniox/types/realtime.py ）
```json
{
  "api_key": "<key>", "model": "stt-rt-v5",
  "audio_format": "pcm_s16le", "sample_rate": 16000, "num_channels": 1,
  "language_hints": ["ja"], "language_hints_strict": true,
  "context": {"terms": ["..."]},
  "enable_speaker_diarization": true,
  "enable_language_identification": true,
  "enable_endpoint_detection": true,
  "max_endpoint_delay_ms": 2000, "endpoint_sensitivity": 0.0,
  "endpoint_latency_adjustment_level": 0
}
```
- raw 音频格式必须同时给 `sample_rate` 与 `num_channels`（SDK 校验）。
- `max_endpoint_delay_ms` 合法域 [500,3000] 默认 2000；`endpoint_sensitivity` 合法域 [-1.0,1.0] 默认 0.0，**v5 才支持，旧模型拒绝**；`endpoint_latency_adjustment_level` [0,3]。

**Audio contract** [verified]：后续帧为原始音频二进制帧。

**控制消息** [verified]（官方 SDK `stt.py`/`types/realtime.py`）
- 保活：文本帧 `{"type": "keepalive"}`（全小写）；
- 手动 finalize：文本帧 `{"type": "finalize"}`（已完成 token 不变，未定 token 转为 `is_final=true`，连接保持）；
- 结束流：**发送零长度帧**（官方 SDK `close()`/`FINISH` 用 `send("")`，即零长度文本帧；零长度二进制帧同为空帧——LagLingo adapter 发 `b""`）。之后继续消费剩余 token 直到连接关闭。

**Response contract** [verified]（SDK `RealtimeEvent`/`Token` 类型）
```json
{
  "tokens": [
    {"text": "Hello", "start_ms": 600, "end_ms": 760, "confidence": 0.97, "is_final": true, "speaker": "1", "language": "en"},
    {"text": "<end>", "is_final": true}
  ],
  "final_audio_proc_ms": 760, "total_audio_proc_ms": 880
}
```
- token 字段全集：`text`、`start_ms`（可选，毫秒）、`end_ms`（可选）、`confidence`（0.0–1.0）、`is_final`、`speaker`（**字符串**，开启 diarization 时出现；不是 `spk`）、`translation_status`、`language`（检测语言，开启 language identification 时出现）、`source_language`（翻译 token）。
- 事件级字段：`tokens[]`、`final_audio_proc_ms`、`total_audio_proc_ms`、`finished`（bool，最终结果/会话结束标志）、`error_code`（int）、`error_message`。
- 旧协议的 `start_time`/`duration_ms` 已废弃；Ticket 02 fixture 一律使用 `start_ms`/`end_ms`。

**控制 token 与时间戳语义** [verified]
- `TOKEN_TEXT_END = "<end>"`、`TOKEN_TEXT_FIN = "<fin>"` 为 SDK 常量：`<end>` 由端点检测生成（一句话边界），`<fin>` 由 finalize 生成（手动边界）。二者是控制边界，不是发音词汇。
- 词汇时间以 lexical token 的 `start_ms`/`end_ms` 为准；控制 token 的时间戳字段不参与语音时间计算（LagLingo adapter 对其取 0 值时已按此处理，见 `_finish_utterance` 注释）[code]。

**Finalization / 停流顺序** [verified]：`{"type":"finalize"}`（中途收束）→ 或零长度帧（结束）；服务端以 `finished=true` 的事件收尾后关闭连接。错误以 `error_code`/`error_message` 平铺字段出现（**不是** `{"type": "error"}` 包装）。

**Diarization** [verified]：`enable_speaker_diarization=true` 后每个 token 带 `speaker` 字符串标签。官方定价页明确 diarization 包含在费率内。

**Language policy** [verified]：多语言单模型 + `language_hints`（严格模式可偏置单语）；token 级 `language` 字段输出检测语言。60+ 语言（含 `ja`，见 adapter `_SUPPORTED` 列表与官方 supported languages 页）。

**Pricing** [verified]：实时流式 **$0.12/小时**，异步 $0.10/小时；diarization/时间戳/confidence 含在费率内；翻译/自定义上下文等高级用法按 token 另计。
  - 来源：https://soniox.com/pricing

**LagLingo mapping / 真实剩余差异** [code]（`asr_soniox_realtime.py` + `test_soniox_realtime.py`）
已正确：v5、`start_ms/end_ms`、`<end>/<fin>` 过滤（`_END_TOKENS`）、控制 token 时间戳不覆盖词汇时间、小写 `{"type":"keepalive"}` 定时保活（`keepAliveSeconds`）、`{"type":"finalize"}`、停流发送 `b""`、`finished`/`error_code` 平铺事件解析、token `language` 主导语言、`max_endpoint_delay_ms`/`endpoint_sensitivity`/`endpoint_latency_adjustment_level` 透传。
剩余差异（真实待办）：
1. **`aclose()` 发送 `b""` 后立即调用 `ws.close()`，没有继续接收直到 `finished=true`**，可能丢失停流时最后一批 final tokens；
2. **未发送 `enable_speaker_diarization`、未解析 token `speaker` 字段**（报告旧稿所写 `spk` 字段名有误，官方字段是字符串 `speaker`）；
3. token `confidence` 未透传；
4. `context` 透传用的是 `{"terms": [...]}` 结构，官方 `StructuredContextInput` 还支持 general/domain 条目（当前够用，非缺陷）。

**裁决**：audit-existing；改动面小（speaker/confidence 透传）。

---

### 2.3 Deepgram — `nova-3`

**身份** [verified]
- `nova-3`（`nova-3-general`）为通用旗舰；官方语言表列明日语 `ja`；**`multi` 模式 = 10 语种 code-switching：en, es, fr, de, hi, ru, pt, ja, it, nl**。`nova-3` 本身无模型级 turn detection（Flux 是带 turn detection 的会话模型）。
  - 来源：https://developers.deepgram.com/docs/models-languages-overview
- 结论：**不需要也不应该为日语降级 nova-2**；`language=ja` 与 `language=multi`（含 ja）均在 nova-3 官方范围内。

**Transport 与 endpoint** [verified]
- `wss://api.deepgram.com/v1/listen` + query 参数；官方参考页：https://developers.deepgram.com/reference/speech-to-text/listen-streaming （低层协议：https://developers.deepgram.com/docs/lower-level-websockets ）
- 常用参数 [verified]：`model`、`encoding=linear16`、`sample_rate`、`channels`、`language`（或 `multi`）、`interim_results`、`smart_format`、`endpointing`（毫秒）、`vad_events`、`utterance_end_ms`、`diarize`。

**Authentication** [verified]：握手 Header `Authorization: Token <key>`（也支持 `api-key` 头式）。

**Audio contract** [verified]：二进制帧 16-bit LE PCM（或 Opus）；建议 20–100ms 分片。

**Client 控制消息** [verified]
- `{"type": "KeepAlive"}`（静音保活）、`{"type": "Finalize"}`（立即冲刷未决音频）、`{"type": "CloseStream"}`（优雅关闭，服务端回最终 Metadata）。

**Response contract** [verified]
```json
{
  "type": "Results",
  "channel_index": [0, 1], "duration": 1.02, "start": 0.0,
  "is_final": false, "speech_final": false,
  "channel": {"alternatives": [{
    "transcript": "こんにちは", "confidence": 0.98,
    "languages": ["ja"],
    "words": [{"word": "こんにちは", "start": 0.12, "end": 0.75, "confidence": 0.98, "speaker": 0, "language": "ja"}]
  }]}
}
```
- `is_final`：该段音频转写终态；`speech_final`：endpointing 判定的语音终点（utterance 边界）；`alternatives[].languages` 为检测语言（按词数排序）；词级 `start/end` 秒、`confidence`、`speaker`（diarize 时）、`language`。
- 其他服务端消息：`SpeechStarted`（`vad_events=true` 时，含 `channel`/`timestamp`）、`UtteranceEnd`（含 `last_word_end`）、`Metadata`。消息全集见上方流式参考页与低层协议页（本次会话未逐页回读这五条功能子页，字段名以流式参考页与当前 adapter/测试为准）。

**Timestamps** [verified]：秒（浮点），词级与句级均有；`UtteranceEnd.last_word_end` 为最后词结束时间（秒）。

**Diarization** [verified]：`diarize=true` 后词级 `speaker` 整数标签。官方定价页列 Speaker Diarization 为流式付费附加项（见下）。

**Language policy** [verified]：指定语言（`ja`/`zh-Hans` 等官方 tag）或 `multi`（固定 10 语种 code-switching）；检测语言经 `alternatives[].languages` 输出。

**Errors/limits**：官方错误码与限流页见 https://developers.deepgram.com/docs/stt-troubleshooting （本次未逐项取证，Unknown 部分：单连接时长上限）。

**Pricing** [verified]（https://deepgram.com/pricing ，2026-09-03 读取；流式为限时促销价）
- 流式 Nova-3 Monolingual：**$0.0048/分钟**（原价 $0.0077）；
- 流式 Nova-3 Multilingual（`multi`）：**$0.0058/分钟**（原价 $0.0092）；
- 流式 Speaker Diarization 附加：**$0.0020/分钟**；Smart Formatting 含在基价内。
- （预录制 mono $0.0043/min 为另一列，勿与流式混用——旧报告数字即此混用错误。）

**LagLingo mapping / 真实剩余差异** [code]（`asr_deepgram_streaming.py` + `test_deepgram_streaming.py`）
已正确：`nova-3`、`Authorization: Token`、query 组装（model/encoding/sample_rate/channels/interim_results/smart_format/endpointing/vad_events/language 或 multi）、`KeepAlive`/`Finalize`/`CloseStream`、`Results` 的 `is_final`/`speech_final` 映射（speech_final 先发 speech_stopped 再发 final，靠 `item_id` 对齐）、`SpeechStarted`/`UtteranceEnd`、`alternatives[].languages` + 词级语言主导语言、nova-3 multi 10 语种 `detection_tags` 校验。
剩余差异（真实待办）：
1. **未发送 `diarize=true`、未解析词级 `speaker`**；
2. **未发送 `utterance_end_ms`**：官方文档将 `UtteranceEnd` 与 `utterance_end_ms` 参数配对描述；当前 adapter 发 `vad_events=true` 但未发 `utterance_end_ms`，`UtteranceEnd` 分支是否会在生产触发未验证（需 live 验证或补参数）。

**裁决**：audit-existing。

---

### 2.4 OpenAI Realtime Transcription — `gpt-live-transcribe` / `gpt-transcribe`

**身份与模型** [verified]
- 当前官方指南以 `gpt-live-transcribe` 起步（增量 delta），`gpt-transcribe` 用于“commit 后转写 + 需要检测语言输出”的场景。
- **禁止作为当前依据**：`gpt-4o-realtime-preview`、`whisper-1`（REST 离线模型）、`OpenAI-Beta` 旧 header/旧 flat session schema。当前指南全文不含这些名称。
  - 来源：https://platform.openai.com/docs/guides/realtime-transcription （2026-09-03 原文）

**Transport 与 endpoint** [verified]：WebSocket（服务端音频管道）或 WebRTC（浏览器）；LagLingo 用 `wss://api.openai.com/v1/realtime` [code config]。

**Authentication** [code/official]：握手 Header `Authorization: Bearer <key>`（adapter 现行实现；Realtime 参考文档口径）。

**Session start** [verified]（当前 schema，嵌套 `audio.input` 结构）
```json
{
  "type": "session.update",
  "session": {
    "type": "transcription",
    "audio": {
      "input": {
        "format": {"type": "audio/pcm", "rate": 24000},
        "transcription": {
          "model": "gpt-live-transcribe",
          "prompt": "...", "keywords": ["..."],
          "languages": ["en", "fr"],
          "delay": "low"
        },
        "turn_detection": {"type": "server_vad"}
      }
    }
  }
}
```
- `turn_detection: null` 表示关闭自动 turn detection、由客户端 `input_audio_buffer.commit` 切轮；`{"type":"server_vad", threshold, prefix_padding_ms, silence_duration_ms}` 为自动切轮。
- `gpt-live-transcribe` 用 `languages`（复数）候选提示，**不用单数 `language` 字段，两者不得同发**；语言码支持 ISO 639-1、部分 639-3（`eng/spa/yue/cmn`）、`zh-cn/zh-tw/zh-hk`；不支持/格式错的码会被 API 拒绝。
- `delay`：`minimal|low|medium|high|xhigh`，官方说明按真实音频基准而非固定毫秒。
- `gpt-transcribe` 不接受 `languages` 提示；自动以已转写轮次作上下文。

**Audio contract** [verified]：`{"type":"input_audio_buffer.append","audio":"<base64 pcm16>"}`，官方示例 24 kHz。手动/强制切轮用 `input_audio_buffer.commit`。

**Response contract** [verified]
```json
{"type": "conversation.item.input_audio_transcription.delta", "item_id": "item_003", "content_index": 0, "delta": "Hello,"}
{"type": "conversation.item.input_audio_transcription.completed", "item_id": "item_003", "content_index": 0, "transcript": "Hello, how are you?"}
```
- **官方明示：不同语音轮次的 completed 事件顺序不保证，必须用 `item_id` 关联**。
- `gpt-transcribe` 的 completed 额外携带检测语言：`"languages": [{"code": "fr"}]`；无法可靠判断时为空数组。`gpt-live-transcribe` 不返回检测语言。
- VAD 事件：`input_audio_buffer.speech_started` / `input_audio_buffer.speech_stopped`（携带 `item_id` 与毫秒音频偏移 `audio_start_ms`/`audio_end_ms`）[code：adapter 事件映射与测试 fixture；官方指南正文仅承诺 VAD 自动切轮，未展示该两事件的字段清单]。
- 失败事件：`conversation.item.input_audio_transcription.failed` [code adapter 处理；官方 reference]。

**Timestamps** [verified]：**无词级时间戳、无 speaker、无 confidence**（官方明示）；时间只能靠 server VAD 事件毫秒偏移 + 本地音频钟近似。

**Language policy** [verified]：见上；detected language 仅 `gpt-transcribe` 输出。

**Errors/limits**：`error` 事件与 `.failed` 事件；限流未逐项取证（Unknown）。

**Pricing**：**Unknown**（本次未取证当前两个转写模型单价；旧 `gpt-4o-*` 代际数字不得沿用）。

**LagLingo mapping / 真实剩余差异** [code]（`asr_openai_realtime_transcription.py` + `test_openai_realtime_transcription.py`）
已正确：`session.type="transcription"` 嵌套 schema、`audio/pcm` 24kHz、`gpt-live-transcribe` languages 提示 / `gpt-transcribe` 检测输出两个 preset、delta 累积为 interim、completed 为 final、`item_id` 关联、`speech_started/stopped` 毫秒偏移转秒、`.failed`/`error`、`input_audio_buffer.commit` 用于 flush 与手动切轮、completed 的 `usage` 透传。
剩余差异：无协议级缺陷；`input_audio_buffer.commit` 后 completed 可能与下一轮事件交错的顺序问题已由 `item_id` 化解 [code docstring 引官方“ordering not guaranteed”]。

**裁决**：audit-existing（schema 已对齐当前官方；模型名与 endpoint 保持现值）。

---

### 2.5 AssemblyAI Streaming v3 — `universal-3-5-pro`

**身份** [verified]
- 流式模型三选一：`universal-3-5-pro`、`universal-streaming-english`、`universal-streaming-multilingual`。**注意模型 ID 用连字符 `universal-3-5-pro`，不是 `universal-3.5-pro`**（旧稿写法错误）。
  - 来源：https://www.assemblyai.com/docs/streaming/api-spec/streaming-websocket

**Transport 与 endpoint** [verified]
- `wss://streaming.assemblyai.com/v3/ws?...`；旧 v2 `wss://api.assemblyai.com/v2/realtime/ws` 已停用（访问返回 410）。
  - 来源：同上 + https://www.assemblyai.com/docs/streaming/message-sequence

**Authentication** [verified]：握手 Header `Authorization: <api_key>`（**无 Bearer 前缀**）；浏览器场景用服务端生成的临时 `token` query 参数，不得把永久 key 放 URL。

**Session start（query 参数建连）** [verified]（节选实现相关项）
- `sample_rate`（8000–96000）、`encoding`（`pcm_s16le`/`pcm_mulaw`/`opus`/`ogg_opus`/`aac`）、`speech_model`（必填）、`language_codes`（token 级偏置 + 原生 code-switching；可选码：en,es,fr,de,it,pt,tr,nl,sv,no,da,fi,hi,vi,ar,he,ja,zh；**仅 Universal-3.5 Pro**）、`language_detection`（Turn 消息返回 `language_code`/`language_confidence`；3.5 Pro 与 Multilingual 可用）、`mode`（`max_accuracy|min_latency|balanced`，3.5 Pro）、`min_turn_silence`/`max_turn_silence`（默认随 mode；3.5 Pro 默认 1536ms，开 speaker_labels 时 768ms）、`speaker_labels`（流式 diarization；Turn 带 `speaker_label`，终态词带 `speaker`）、`max_speakers`（1–10）、`session_heartbeat`（5s 心跳）、`inactivity_timeout`、`prompt`/`keyterms_prompt`/`agent_context`（3.5 Pro）。
- 拼写错误的 query 参数会被**静默忽略**，必须校验 `Begin.configuration` 回显。

**Audio contract** [verified]：原始二进制帧（不得 JSON/base64 包装）；PCM 建议 ~50ms 分片（16k 下 800 样本）；超实时发送被节流至 ~1.25×，缓冲超 5 分钟报 3007 断连。

**Response contract** [verified]（https://www.assemblyai.com/docs/streaming/message-sequence ）
```json
{"type": "Begin", "id": "3207b601-...", "expires_at": 1772570132,
 "configuration": {"model": "universal-3-5-pro", "mode": "balanced", "api_version": "2025-05-12"}}
{"type": "SpeechStarted", "timestamp": 1216, "confidence": 0.987654}
{"type": "Turn", "turn_order": 0, "turn_is_formatted": true, "end_of_turn": true,
 "transcript": "My name is Sonny.", "end_of_turn_confidence": 1,
 "words": [{"start": 1216, "end": 1635, "text": "My", "confidence": 0.956583, "word_is_final": true, "speaker": "A"}],
 "utterance": "My name is Sonny.", "speaker_label": "A",
 "language_code": "en", "language_confidence": 0.99}
{"type": "Termination", "audio_duration_seconds": 45, "session_duration_seconds": 47}
```
- 旧稿错误更正：消息类型字段是 **`type`**（不是 `message_type`）；`Begin` 带 `id`（不是 `session_id`）；`Termination` 只有时长统计（无 `session_id`）。

**Timestamps** [verified]：词级 `start/end` **毫秒**（quickstart 明示 “Word timings are in milliseconds”）；`SpeechStarted.timestamp` 为相对音频流起点的毫秒。

**Finalization / turn 语义** [verified]
- 中间结果：`Turn` + `end_of_turn:false`；同一 `turn_order` 的每条消息**覆盖**前一条（渲染最新，不追加）。
- 终态：`end_of_turn:true` 且 `turn_is_formatted:true`（Universal Streaming 在 `format_turns=true` 时会对同一 turn 先发未格式化终态再发格式化终态，**必须同时满足两条件才算完成**，否则会重复处理）。
- `turn_order` 单调递增；某 turn 的所有消息先于下一 turn。
- 手动收束：`{"type": "ForceEndpoint"}`；中途改配置：`{"type": "UpdateConfiguration", ...}`（无确认回执）。

**Stop** [verified]：`{"type": "Terminate"}` → 服务端回 `Termination` 后关闭；开启 `speaker_labels` 时 Terminate 后可能补发一条 `SpeakerRevision`（修订历史 turn 的说话人标签）。保活 `{"type": "KeepAlive"}` 仅在设置 `inactivity_timeout` 时需要（默认不需要，会话最长 3 小时）。
- 错误：关闭前发 `Error`（code + detail）；3006 不活动超时 / 3007 缓冲超限 / 3008 到期 `expires_at`。

**Diarization** [verified]：`speaker_labels=true` + `max_speakers`；Turn 级 `speaker_label`、词级 `speaker`；**事后修订**（SpeakerRevision）是该产品的明确行为，Ticket 02 测试必须覆盖“修订覆盖已渲染 speaker”。

**Language policy** [verified]：3.5 Pro 原生 code-switching（默认全语种，`language_codes` 可偏置；`ja` 在列）；`language_detection=true` 时 Turn 带 `language_code`。

**Pricing** [verified]：Universal-3.5 Pro Realtime **$0.45/小时**（官方博客/定价口径，https://www.assemblyai.com/blog/real-time-speech-to-text-best-for-voice-agents ）。

**LagLingo mapping**：无现有 adapter。映射建议：`Turn.end_of_turn=false` → `interim`（覆盖式），`end_of_turn && turn_is_formatted` → `final`；`turn_order` 作 `item_id`；`SpeechStarted.timestamp`/词时间作 `begin_pcm/end_pcm`；`speaker_label` 作 speaker。

**裁决**：native-aiohttp（协议完全公开；fixture 见 §4）。

---

### 2.6 Google — `gemini-3.5-transcribe-live`

**身份** [verified]
- **官方已存在该产品（旧报告“不存在”的结论作废）**：Gemini API changelog（2026-08-26 条目，页面最后更新 2026-09-02）：
  - `gemini-3.5-transcribe`：非流式，85+ 语言 utterance 级检测、diarization、词级时间戳、1000 词自定义偏置；
  - **`gemini-3.5-transcribe-live`：低延迟双向流式转写，走 Live API over WebSocket，支持 interim 与 finalized 转写事件、Smart transcription mode、多种 VAD 策略**。
  - 来源：https://ai.google.dev/gemini-api/docs/changelog

**Transport 与 protocol（Live API 口径）** [verified]
- Live API 音频恒为 raw little-endian 16-bit PCM；输入原生 16 kHz（`audio/pcm;rate=16000` MIME，其他采样率会被重采样），输出 24 kHz。
- 输入转写：setup config 加 `"input_audio_transcription": {}`，服务端事件以 `serverContent.inputTranscription.text` 形式下发。
- VAD：自动 VAD（`start_of_speech_sensitivity`/`end_of_speech_sensitivity`/`prefix_padding_ms`/`silence_duration_ms`）或自定义 VAD（手动 `activityStart`/`activityEnd`）。
- 官方推荐经官方 SDK（`google-genai`，`client.aio.live.connect`）建连；底层为 BidiGenerateContent WebSocket（`wss://generativelanguage.googleapis.com/ws/...`）。
  - 来源：https://ai.google.dev/gemini-api/docs/live-api/capabilities

**取证缺口（明确记录）**
- 官方文档导航含独立 “Live transcription” 页，但本次两次 URL 猜测均 404（`/docs/live-transcription`、`/docs/live-api/live-transcription`），该页的 Smart transcription mode 参数、interim/finalized 事件消息类型、会话上限等**未取证 → Unknown**；
- 定价 **Unknown**；转写会话是否复用对话式 Live API 的会话管理（session resumption/GoAway）**Unknown**。

**Language policy**：85+（transcribe 非流式）/ live 语言覆盖以模型页为准（未取证细节，Unknown）。

**裁决**：**defer**。理由：模型已 GA 且协议主干（Live API + input_audio_transcription）已确认，但转写专用页细节（finalized 事件、smart mode、计费）未取证；且 Live API 是对话式会话模型，作为纯字幕管道的确定性与成本未验证。Ticket 02 如需启用，先按官方 SDK（`google-genai`）做 spike，再决定 `google-genai-live-transcribe` kind。

---

### 2.7 Mistral — `voxtral-mini-transcribe-realtime-2602`

**身份** [verified]
- API 模型 ID：`voxtral-mini-transcribe-realtime-2602`（`Voxtral-Mini-4B-Realtime-2602` 是 Hugging Face 开放权重名，非 API ID）。实时转写延迟可配置至 sub-200ms；13 语言；权重 Apache 2.0。
  - 来源：https://docs.mistral.ai/studio/audio/overview ；https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602
- 定价 [verified]：API **$0.006/分钟**（官方发布文，2026-02-04，https://mistral.ai/news/voxtral-transcribe-2/ ）。

**Transport 与 facade** [verified]（https://docs.mistral.ai/studio/audio/speech_to_text/realtime_transcription ）
- 官方仅公开 **SDK facade**：`pip install mistralai[realtime]`；
  `client.audio.realtime.transcribe_stream(audio_stream=..., model="voxtral-mini-transcribe-realtime-2602", audio_format=AudioFormat(encoding="pcm_s16le", sample_rate=16000), target_streaming_delay_ms=...)`；
- 默认基址 `wss://api.mistral.ai`（官方示例 `--base-url` 缺省值）；音频 pcm_s16le，采样率 8000/16000/22050/44100/48000；
- 事件类型：`RealtimeTranscriptionSessionCreated` / `TranscriptionStreamTextDelta`（`.text` 增量）/ `TranscriptionStreamDone` / `RealtimeTranscriptionError` / `UnknownRealtimeEvent`；
- `target_streaming_delay_ms` 支持双流（fast ~240ms / slow ~2400ms）快慢合并；
- **Realtime 与 `diarize` 参数互斥**（官方 Note）。
- **低层 WebSocket 帧格式未公开**（文档全部经 SDK 表达）→ native-aiohttp 不可行。

**Language policy**：13 语言集合未逐项取证，`ja` 是否在列 **Unknown**（HF 卡片仅给总数）。

**裁决**：**defer**（如启用则 `official-sdk`，作为可选依赖；旧稿“受邀预览、无公开资料”的表述过时——现状是“公开 API + 公开 SDK facade、无公开低层协议”）。

---

### 2.8 Speechmatics Realtime v2 — `enhanced` / `standard`

**Transport 与 endpoint** [verified]
- `wss://eu.rt.speechmatics.com/v2/`（固定区域）或 `wss://global.rt.speechmatics.com/v2/`（自动就近路由）。旧稿所写 `eu2.rt.` / `neu.rt.` 非当前文档口径。
  - 来源：https://docs.speechmatics.com/api-ref/realtime-transcription-websocket

**Authentication** [verified]：握手 Header `Authorization: Bearer <key>`；浏览器用临时 JWT query 参数 `?jwt=<temporary-key>`（key 不进 URL；临时 key 例外，须短时效）。

**Session start / StartRecognition** [verified]
```json
{
  "message": "StartRecognition",
  "audio_format": {"type": "raw", "encoding": "pcm_s16le", "sample_rate": 16000},
  "transcription_config": {
    "language": "ja",
    "model": "enhanced",
    "enable_partials": true,
    "diarization": "speaker",
    "speaker_diarization_config": {"max_speakers": 6},
    "max_delay": 4,
    "conversation_config": {"end_of_utterance_silence_trigger": 0.7}
  }
}
```
- `encoding`：`pcm_f32le|pcm_s16le|mulaw`；`language` 必填；
- **`operating_point` 已弃用，改用 `model`**（`standard|enhanced|melia-1`）；
- `diarization`: `none|speaker|channel|channel_and_speaker`；`max_speakers ≥ 2`；`get_speakers`/`speakers`（预置标签）可选；
- `max_delay` 0.7–4 秒（默认 4，final 出字延迟）；`enable_partials` 默认 false；
- `conversation_config.end_of_utterance_silence_trigger` 0–2 秒（默认 0 关闭）：说话人停顿触发 `EndOfUtterance` 消息。

**Audio contract** [verified]：二进制 `AddAudio` 帧；服务端逐帧回 `AudioAdded {seq_no}`；发送快于实时时注意 `bufferedAmount` 背压。

**Response contract** [verified]
```json
{
  "message": "AddTranscript",
  "format": "2.4",
  "metadata": {"start_time": 0.12, "end_time": 1.45, "transcript": "こんにちは"},
  "results": [
    {"type": "word", "start_time": 0.12, "end_time": 0.6, "attaches_to": "none", "is_eos": false,
     "alternatives": [{"content": "こんにちは", "confidence": 0.98, "language": "ja", "speaker": "S1"}]}
  ]
}
```
- 中间结果 `AddPartialTranscript` 结构相同但 `confidence` 无意义；`AddTranscript` 为该段终态；`is_eos` 标记句子结束词；`alternatives[].speaker` 开启 diarization 时出现；
- `RecognitionStarted`（含 `id`、`language_pack_info`：`language_description`/`writing_direction`/`itn` 等）确认会话；
- 结束：客户端 `{"message": "EndOfStream", "last_seq_no": N}` → 服务端 `{"message": "EndOfTranscript"}`；
- 手动收束：`{"message": "ForceEndOfUtterance"}`；中途改配置：`SetRecognitionConfig`（`language` 不得改）。

**Timestamps** [verified]：`start_time`/`end_time` 浮点**秒**（词级与段级）。

**Errors/retry** [verified]：握手 400/401/405；连接后可收到错误并关流，官方建议对 `4005 quota_exceeded`、`4013 job_error`、`1011 internal_error` 以 ≥5–10 秒间隔重试。

**Language policy**：`language` 必填单语；`ja` 在官方语言支持范围（provider 声明；本次未单独回读语言页，标注 provider_claimed）。无官方句内 code-switching 合同——不得自动开放（与计划文档 §4.4 一致）。

**Pricing**：**Unknown**（官方 pricing 页未在本次取证中读取；旧稿 $1.05/$1.65 每小时数字无本次证据，删除）。

**裁决**：native-aiohttp（协议公开、无 SDK 依赖）。

---

### 2.9 ElevenLabs Scribe v2 Realtime — `scribe_v2_realtime`

**Transport 与 endpoint** [verified]
- `wss://api.elevenlabs.io/v1/speech-to-text/realtime`（**旧稿 `/v1/speech-to-text/streaming` 路径错误**）。
  - 来源：https://elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime

**Authentication** [verified]：Header `xi-api-key`，或单次 `token` query 参数（客户端场景）。注意：**配置走握手 query 参数，没有首帧 session 配置消息**（旧稿的 `session_init` 帧为虚构）。

**Session start（query 参数）** [verified]
- `model_id`（必填，如 `scribe_v2_realtime`）；`audio_format`：`pcm_8000|pcm_16000|pcm_22050|pcm_24000|pcm_44100|pcm_48000|ulaw_8000`；`language_code`（ISO-639-1/3，缺省自动）；`secondary_languages`（提升语言识别可靠性）；`commit_strategy`：`manual|vad`；`vad_threshold`/`vad_silence_threshold_secs`/`min_speech_duration_ms`/`min_silence_duration_ms`；`include_timestamps`（默认 false）；`include_language_detection`（默认 false）；`keyterms`（≤50，+20% 计费）；`no_verbatim`；`entity_detection`；`filter_background_audio`；`enable_logging=false` 为零留存（企业/试用专属，否则回 warning 且仍记录）。

**消息契约** [verified]（官方 reference 内嵌示例）
```json
// server → client
{"message_type": "session_started", "session_id": "sess_123456789",
 "config": {"audio_format": "pcm_16000", "language_code": null, "model_id": "scribe_v2_realtime", "sample_rate": 16000}}
{"message_type": "partial_transcript", "text": "hello wor"}
{"message_type": "committed_transcript", "text": "hello world"}
// client → server（音频为 base64 JSON 文本帧）
{"message_type": "input_audio_chunk", "audio_base_64": "....", "commit": true}
```
- `commit:true` 的音频帧触发手动提交（`commit_strategy=manual` 时必须显式提交）；`vad` 模式按静音自动提交；
- `include_timestamps=true` 时每次 commit 后**延迟**追加 `committed_transcript_with_timestamps`（词级时间戳）；`include_language_detection=true` 时同消息携带 `language_code`；
- 错误为类型化事件：`rate_limited`、auth/quota/throttled/queue_overflow/resource_exhausted/session_time_limit_exceeded/input/invalid_request/chunk_size_exceeded/insufficient_audio_activity/transcriber 等；
- 结束：官方文档未定义 close 控制帧——直接关闭 WebSocket（旧稿 `{"type": "close"}` 无证据，删除）。

**Timestamps** [verified]：partial/committed 文本本身无时间戳；词级时间戳仅在延迟的 `committed_transcript_with_timestamps` 中。

**Diarization**：实时文档未提供 speaker 输出 → **Unknown**（不得宣称）。

**Language policy** [verified]：`language_code` 单语提示或缺省自动；`secondary_languages` 候选集；检测语言需开启 `include_language_detection` 且在延迟消息中返回。

**Pricing**：**Unknown**（本次未取证实时条目单价；官方页仅“弹性定价”）。

**裁决**：native-aiohttp（协议公开；注意 base64 音频 + query 配置与其它家不同）。

---

### 2.10 火山引擎（豆包）大模型流式 ASR

**身份** [verified]
- 大模型流式识别模型 1.0 / 2.0（Resource-Id 区分，见下）；官方 SDK 文档确认服务 URI 固定 `/api/v3/sauc/bigmodel`。
  - 来源：https://docs.volcengine.com/docs/6561/1354869?lang=zh （API 正文）；https://www.volcengine.com/docs/6561/1395846 （SDK 配置引用 URI）

**Transport 与 endpoint** [verified]
- 双向流式：`wss://openspeech.bytedance.com/api/v3/sauc/bigmodel`；
- 双向流式优化版（推荐）：`.../api/v3/sauc/bigmodel_async`（结果变化才回包，rtf 与首尾字时延更优；支持二遍识别 `enable_nonstream`）；
- 流式输入（一次性整段返回，准确率更高）：`.../api/v3/sauc/bigmodel_nostream`；
- **旧稿所写 `/api/v2/asr` 为旧小模型全双工接口，非大模型协议，删除**。

**Authentication** [verified]（握手 Header）
- 新版控制台：`X-Api-Key` + `X-Api-Resource-Id` + `X-Api-Request-Id`(UUID) + `X-Api-Sequence: -1`（+ `X-Api-Connect-Id`）；
- 旧版控制台：`X-Api-App-Key`(APP ID) + `X-Api-Access-Key`(Access Token) 替代 X-Api-Key；
- `X-Api-Resource-Id`：模型 1.0 小时版 `volc.bigasr.sauc.duration` / 并发版 `volc.bigasr.sauc.concurrent`；**模型 2.0：`volc.seedasr.sauc.duration` / `volc.seedasr.sauc.concurrent`**；
- 响应 Header `X-Tt-Logid` 用于排障。

**二进制分帧协议** [verified]（所有帧：4 字节 Header + 4 字节 Payload size + payload；整数大端）
- Byte0：`0x11`（version 0b0001 | header size 0b0001×4B）；
- Byte1 高 4 位 message type：`0b0001` full client request（参数 JSON）/ `0b0010` audio only request / `0b1001` full server response / `0b1111` error；低 4 位 flags：`0b0010`/`0b0011` 表示最后一包音频（负包）；
- Byte2 高 4 位序列化（`0b0001` JSON）| 低 4 位压缩（`0b0000` 无 / `0b0001` gzip）；
- 服务端 response 在 header 后多 4 字节 sequence。

**Session start（full client request payload，JSON）** [verified]（节选）
```json
{
  "user": {"uid": "..."},
  "audio": {"format": "pcm", "rate": 16000, "bits": 16, "channel": 1, "codec": "raw"},
  "request": {"model_name": "bigmodel", "enable_punc": true, "show_utterances": true,
              "result_type": "full", "end_window_size": 800}
}
```
- `audio.format`：pcm/wav/ogg/mp3（pcm/wav 内部须 pcm_s16le；ogg 必配 codec=opus）；`rate` **仅支持 16000**；`bits` 仅 16；`channel` 1/2；
- `audio.language`：**仅 bigmodel_nostream 支持**；空 = 中英 + 多方言；可指定 `ja-JP`/`en-US` 等 25 语种；`enable_auto_lang`（仅 nostream）自动语种；
- `request`：`model_name` 恒 `bigmodel`；`enable_nonstream`（二遍识别，仅优化版）；`enable_itn`（默认 true）；`enable_speaker_info`（说话人聚类；限制：language 未指定或 zh-CN；优化版需 `enable_nonstream=true` 且 `ssd_version="200"`）；`output_zh_variant`（简转繁 traditional/tw/hk）；`result_type` full/single；`end_window_size`（强制判停 ms，默认 800，最小 200）；`vad_segment_duration`（语义切句静音阈值，默认 3000）；`corpus.context` 热词/对话上下文（热词直传 100 tokens，上下文 800 tokens/20 轮；2.0 支持图片上下文）。

**Audio contract** [verified]：audio only request，payload 为（gzip 后）音频；单包 100–200ms（双向流式 200ms 最优）；发包间隔 100–200ms；结束发 flags `0b0010`/`0b0011` 的负包，服务端以 flags `0b0011` 的最终 response 收尾。

**Response contract** [verified]
```json
{"audio_info": {"duration": 3696},
 "result": {"text": "这是字节跳动，今日头条母公司。",
            "utterances": [
              {"definite": true, "start_time": 0, "end_time": 1705, "text": "这是字节跳动，",
               "words": [{"text": "这", "start_time": 740, "end_time": 860, "blank_duration": 0}]},
              {"definite": true, "start_time": 2110, "end_time": 3696, "text": "今日头条母公司。", "words": []}
            ]}}
```
- **分句终态字段是 `definite`（bool），不是 `is_final`（旧稿错误）**；`start_time`/`end_time` 毫秒；词级 `words[]` 含 `blank_duration`。

**Errors** [verified]：`0b1111` 错误帧（4B code + JSON message）；`20000000` 成功、`45000001` 参数无效、`45000002` 空音频、`45000081` 等包超时、`45000151` 音频格式不正确、`550xxxxx` 服务内部错误、`55000031` 服务器繁忙。

**Language policy / 日语限制（重要）** [verified]
- 双向流式（含优化版）**不接受 `audio.language`**，默认中英+方言；日语实时识别仅在 `bigmodel_nostream`（`language="ja-JP"` 或 `enable_auto_lang=true`）下有官方合同。**把大模型流式用于日语直播字幕在官方文档中没有依据**，选型时必须记录此限制。

**Diarization**：`enable_speaker_info=true` 开启说话人聚类（约束见上）；响应中 speaker 标签的具体字段在本次抓取的文档正文中未展示 → 字段级 **Unknown**。

**Pricing**：**Unknown**（本次未读取官方计费页；旧稿“0.20 元/小时”无本次证据，删除）。

**裁决**：native-aiohttp（协议完全公开；需实现 4 字节分帧 + gzip + 负包语义）。

---

### 2.11 腾讯云实时语音识别

**两套协议并存** [verified]
- 经典协议：实时语音识别（WebSocket），`wss://asr.cloud.tencent.com/asr/v2/<appid>`，文档 https://cloud.tencent.com/document/product/1093/48982 ；
- 大模型 2.0 协议：实时语音识别 V2（WebSocket），文档 https://cloud.tencent.com/document/product/1093/131127 ，引擎 `16k_zh_en_2.0`（中英粤+30 方言）与 **`16k_zh_en_speaker_2.0`**（同语种 + 说话人分离，**默认开启话者分离**；官方注明“仅 16k_zh_en_speaker_2.0 支持话者分离”）。
- 用户表格提到的 “V2 realtime speaker clustering” 对应 `16k_zh_en_speaker_2.0`（大模型 2.0 计费）或经典引擎 `16k_zh_en_speaker` + `speaker_diarization=1` + `sentence_strategy`（会议话者分离方案，https://cloud.tencent.com/document/product/1093/130881 ）。

**Authentication** [verified]：URL query 签名：`secretid`、`timestamp`、`expired`（>timestamp 且差 <90 天）、`nonce`、`signature`（HMAC-SHA1，签名生成见官方“签名生成”文档）。**凭据材料参与 URL 构造**——adapter 不得记录完整签名 URL，临时签名须短时效。
- 引擎参数（经典协议，节选）[verified]：`engine_model_type`（大模型 2.0 版仅适用大模型 2.0 计费；通用引擎含 `16k_ja` 日语、`16k_multi_lang` 15 语种自动识别等）、`voice_id`（每连接新生成 UUID，≤128 字符，断线作废）、`voice_format`（默认 4=speex；**PCM 用 1**；另支持 6/8/10/12/14/16）、`needvad`（默认 0；>60s 强制切分，长音频建议 1）、`word_info`（0/1/2 词级时间戳，支持引擎列表含 `16k_ja`；`100` 仅 `16k_zh_en` 字级）、`vad_silence_time`（500–2000ms，默认 1000）、`max_speak_time`（5000–90000ms 强制断句→`slice_type=2`）、`hotword_id`/`hotword_list`（≤128 词，权重 1–11 或 100）、`filter_empty_result`（需要 `slice_type=0/2` 配对时设 0）、`input_sample_rate`。

**Audio contract** [verified]：二进制 PCM 分片；V2 大模型文档建议 **200ms/包**（16k=6400 字节），超 1:1 实时率或包间隔 >6s 会被断连。

**Response contract** [verified（经典协议口径）]
```json
{"code": 0, "message": "success", "voice_id": "v_12345", "message_id": "...",
 "result": {"slice_type": 2, "index": 1, "start_time": 0, "end_time": 1280,
            "voice_text_str": "您好，欢迎体验腾讯云实时语音识别。",
            "word_size": 2,
            "word_list": [{"word": "您好", "start_time": 0, "end_time": 320, "speaker": 1}]}}
```
- `slice_type`：0 段开始 / 1 段中间 / 2 段结束（终态）；`word_info` 开启后 `word_list[]` 带词级时间戳；
- speaker 流（`16k_zh_en_speaker(_2.0)`）：会议方案文档显示回调结构为 `sentences.sentence_list[]`：`{sentence_id, sentence, sentence_type (0 识别中/1 已确认), speaker_id, start_time, end_time}` [verified，https://cloud.tencent.com/document/product/1093/130881 ]。**两套响应形态并存**，V2 大模型 speaker 引擎的最终 JSON 需 live 抓包定稿（Unknown 项）。

**Stop**：经典协议以关闭连接结束；旧稿 `{"type": "end"}` 终结帧未在当前文档找到证据 → **删除该断言，标 Unknown**（以“发送完最后一片后关闭连接，等待含 slice_type=2 的终态”为保守实现）。

**Language policy**：`engine_model_type` 决定语言（单语引擎 `16k_ja` 等；`16k_multi_lang` 句/段级 15 语种自动识别）；无句内 code-switching 合同。

**Pricing**：**Unknown**（分级计费，官方计费概述页未在本次取证中读取；旧稿 1.0 元/小时删除）。

**裁决**：native-aiohttp（注意：签名 URL 构造、voice_id 生命周期、200ms 发包节律）。

---

## 3. 统一契约现状（以当前代码为准）

**旧稿删除项**：不存在也不提案 `TranscriptionSegment`；旧稿引用的 `ASREventType("transcript_interim"/"transcript_final")`、`ASRCapabilities(language_switching=...)`、`frozen slots ASREvent(start/end)` 均与当前代码不符，全部作废。

**当前真实契约** [code]（`companion/providers/base.py`）：

```python
ASREventType = Literal["speech_started", "speech_stopped", "interim", "final", "usage", "error"]

@dataclasses.dataclass
class ASREvent:
    type: ASREventType
    text: str = ""
    stash: str = ""              # Qwen realtime 稳定前缀的未定尾部
    begin_pcm: float | None = None   # Provider 已收音频内的偏移（秒），非墙钟
    end_pcm: float | None = None
    language: str | None = None      # 规范化后的检测语言
    message: str = ""                # error 事件文本
    raw: dict | None = None
    item_id: str | None = None       # Provider 侧 utterance join key
```

```python
@dataclasses.dataclass(frozen=True)
class ASRLanguageCapabilities:
    supported_tags: tuple[str, ...] | None
    detection: Literal["none", "unrestricted", "candidates"]
    max_candidates: int | None
    reports_detected_language: bool
    code_switching: bool
    tier: Literal["verified", "provider_claimed", "experimental"]
    detection_tags: tuple[str, ...] | None   # 候选检测的更小子集（如 nova-3 multi）

@dataclasses.dataclass(frozen=True)
class ASRCapabilities:
    streaming: bool; interim_results: bool; stable_prefix: bool; server_vad: bool
    word_timestamps: bool; hotwords: bool; context: bool
    languages: tuple[str, ...]; sample_rates: tuple[int, ...]
    manual_commit: bool = False
    language: ASRLanguageCapabilities = ASRLanguageCapabilities()
    preferred_sample_rate: int | None = None
```

`ASREvent.item_id` 是跨事件关联键（OpenAI/Deepgram/VAD 与转写分离事件靠它配对）；`ASRStream` 接口为 `push_pcm / flush / commit / __aiter__ / aclose`，Pipeline 是唯一通用重连 owner [code]。

**最小扩展需求（按本报告证据，供 Ticket 02 评估，非本票实施）**
- speaker 元数据：Soniox（token `speaker`）、Deepgram（word `speaker`）、AssemblyAI（`speaker_label`/词级 `speaker`+SpeakerRevision）、腾讯（`speaker_id`）。`ASREvent` 尚无 speaker 字段；扩展时应同时定义 SpeakerRevision 的覆盖语义（AssemblyAI 已证实存在事后修订）。
- 词级时间戳：AssemblyAI/火山/腾讯词级、Deepgram word timing、Soniox token 级均已验证；`ASREvent` 无 words 字段，当前由 `raw` 承载。
- 以上两项在 `global-language-provider-expansion-plan.md` 的既有范围内，不需要新契约类型。

---

## 4. 现有四家 Adapter 审计清单（正确 / 待修 / 未验证）

| Provider | 正确（已验证实现） | 待修（有官方依据的差异） | 未验证 / Unknown |
| :-- | :-- | :-- | :-- |
| Soniox `soniox-realtime` | v5；`start_ms/end_ms`；`<end>/<fin>` 过滤且其时间戳不覆盖词汇时间；小写 keepalive；finalize；发送空帧；`finished`/`error_code` 平铺事件解析；endpoint 三参数透传 [code+官方 SDK] | ① 空帧后立即 close，未等 `finished=true` 排空尾部 final；② 未发 `enable_speaker_diarization`、未解析 token `speaker`（字符串）；③ token `confidence` 未透传 | 停流空帧官方 SDK 用零长度文本帧、LagLingo 用零长度二进制帧——live 验证网关是否等价 |
| Alibaba `dashscope-task-asr` | run-task→task-started→binary→result-generated→finish-task；服务端 `header.event`；`begin_time/end_time`；`sentence_id`；heartbeat 过滤；UUID task_id；DataInspection header；先等 task-started [code+官方文档] | ① qwen-audio-3.0 的 `language_hints` 最多 4 个，当前只传 1 个；② `parameters.hotwords` 不在当前文档（应为 `vocabulary_id`/`vocabulary`，且即时热词仅 qwen-audio-3.0 支持）；③ 缺 qwen-audio-3.0 内置 preset | qwen-audio-3.0 真机 result 字段（words/punctuation）未见 live fixture；价格 Unknown |
| Deepgram `deepgram-streaming` | nova-3；`Token` 鉴权；query 全集；KeepAlive/Finalize/CloseStream；is_final/speech_final 语义；SpeechStarted/UtteranceEnd 映射；`alternatives[].languages`；multi 10 语种校验 [code+官方文档] | ① 未发 `diarize=true`、未解析词级 `speaker`；② 未发 `utterance_end_ms`（`UtteranceEnd` 是否触发存疑） | 单连接时长上限；`UtteranceEnd` 在现参数组合下的实际触发行为 |
| OpenAI `openai-realtime-transcription` | `session.type="transcription"` 嵌套 schema；24kHz `audio/pcm`；gpt-live-transcribe（languages 提示）/gpt-transcribe（检测输出）双 preset；delta/completed + `item_id` 关联；VAD 毫秒偏移；`.failed`；commit [code+官方指南] | 无协议级缺陷 | 当前模型单价 Unknown；`languages` 提示对识别质量的影响需 live 验证 |

---

## 5. 选型矩阵与推荐 preset（供 Ticket 02）

| 优先级 | Kind | Preset 建议 | 模型 / 引擎 | Endpoint 默认 | 备注 |
| :--: | :-- | :-- | :-- | :-- | :-- |
| P0 | `soniox-realtime`（已有） | `soniox-stt-rt-v5` | `stt-rt-v5` | `wss://stt-rt.soniox.com/transcribe-websocket` | 待办：speaker/confidence 透传 |
| P0 | `dashscope-task-asr`（已有） | `bailian-qwen-audio-3.0-asr-streaming`（新增） | `qwen-audio-3.0-asr-flash-streaming` | `wss://dashscope.aliyuncs.com/api-ws/v1/inference` | hints≤4；vocabulary 热词 |
| P0 | `deepgram-streaming`（已有） | `deepgram-nova3-multilingual`（已有） | `nova-3` | `wss://api.deepgram.com/v1/listen` | 待办：diarize / utterance_end_ms |
| P1 | `openai-realtime-transcription`（已有） | `openai-gpt-live-transcribe` / `openai-gpt-transcribe`（已有） | `gpt-live-transcribe` / `gpt-transcribe` | `wss://api.openai.com/v1/realtime` | schema 已对齐 |
| P1 | `assemblyai-streaming`（新） | `assemblyai-universal-3-5-pro` | `universal-3-5-pro` | `wss://streaming.assemblyai.com/v3/ws` | 覆盖式 Turn；SpeakerRevision |
| P1 | `volcengine-sauc`（新） | `volcengine-bigasr-sauc` | Resource-Id 决定（1.0/2.0） | `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async` | 4B 分帧+gzip；日语仅 nostream |
| P2 | `elevenlabs-scribe-realtime`（新） | `elevenlabs-scribe-v2-rt` | `scribe_v2_realtime` | `wss://api.elevenlabs.io/v1/speech-to-text/realtime` | query 配置；base64 音频 |
| P2 | `speechmatics-realtime`（新） | `speechmatics-rt-enhanced` | `model: enhanced` | `wss://global.rt.speechmatics.com/v2/` | 单语 + diarization |
| P2 | `tencent-asr`（新） | `tencent-asr-v2-speaker` | `16k_zh_en_speaker_2.0` | `wss://asr.cloud.tencent.com/asr/v2/<appid>` | URL 签名；日语走 `16k_ja` |
| Defer | — | — | `gemini-3.5-transcribe-live` | Live API | 已 GA；细节未取证（§2.6） |
| Defer | — | — | `voxtral-mini-transcribe-realtime-2602` | `wss://api.mistral.ai`（SDK facade） | 无公开低层协议（§2.7） |

纯 `aiohttp` 可实现：Alibaba、Soniox、Deepgram、OpenAI、AssemblyAI、ElevenLabs、Speechmatics、火山、腾讯（火山需二进制分帧、腾讯需 URL 签名）。需官方 SDK/暂不可 native：Google（Live API 官方 SDK）、Mistral（SDK facade）。

---

## 6. 供 Ticket 02 的 fixture 摘要与断言要点

> fixture 值均出自本文引用的官方页面/SDK；断言只测协议行为，不断言未取证的值。

1. **Soniox**：首帧含 `api_key/model="stt-rt-v5"/audio_format="pcm_s16le"/sample_rate=16000/num_channels=1/language_hints/enable_endpoint_detection`；token 流 `{"tokens":[{"text":"Hello ","start_ms":100,"end_ms":400,"is_final":true,"language":"en"},{"text":"世","start_ms":410,"end_ms":520,"is_final":false,"language":"ja"},{"text":"世界","start_ms":410,"end_ms":700,"is_final":true,"language":"ja"},{"text":"<end>","start_ms":0,"end_ms":0,"is_final":true}]}` → 断言 interim “Hello 世”、final “Hello 世界”（`begin=0.10,end=0.70`，控制 token 的 0 值不得破坏词汇端点）、无 `<end>` 文本残留；`{"error_code":401,"error_message":"bad key"}` → error；controls 序列含 `{"type":"finalize"}`、`{"type":"keepalive"}`；close 后服务端收到一个空帧。 speakers 扩展：token 加 `"speaker":"1"` 且首帧加 `enable_speaker_diarization:true`。
2. **Alibaba**：`run-task`（task_id 带连字符 UUID）→ `{"header":{"task_id":..., "event":"task-started", "attributes":{}},"payload":{}}`；`result-generated` 用 §2.1 fixture（`sentence_begin:true` 起始包、`sentence_end:true` 终包、`heartbeat:true` 忽略）→ 断言 `header.event`（非 action）、final 在 0.17–0.92s、item_id="1"、usage.duration 透传；`task-failed` header.error_code/error_message → error。 qwen 扩展：`language_hints` 允许 ≤4、`vocabulary` 即时热词透传。
3. **Deepgram**：query 断言 `model=nova-3&encoding=linear16&sample_rate=16000&channels=1&interim_results=true&smart_format=true&endpointing=...&vad_events=true&language=...`；`Results(is_final=false)`→interim；`Results(is_final=true, speech_final=true)`→speech_stopped+final（同 item_id）；`UtteranceEnd{last_word_end:0.7}`→speech_stopped；`SpeechStarted{timestamp:0.1}`→speech_started；controls：`Finalize`/`KeepAlive`/`CloseStream`。 diarize 扩展：query 加 `diarize=true`，词对象带 `"speaker":0`。
4. **OpenAI**：session.update 断言 `session.type=="transcription"`、`audio.input.format=={"type":"audio/pcm","rate":24000}`、`transcription.model`、languages 提示仅 live 模型、无单数 `language` 字段；delta 累积→interim；`completed{item_id,transcript,languages:[{"code":"ja"}]}`→final+language（gpt-transcribe）；`.failed`→error；乱序 completed 靠 item_id 配对。
5. **AssemblyAI（新）**：query 断言 `wss://streaming.assemblyai.com/v3/ws?sample_rate=16000&speech_model=universal-3-5-pro&...`；`Begin{type,id,expires_at,configuration}` 校验 model 回显；`Turn(end_of_turn=false)` 覆盖式 interim；`Turn(end_of_turn=true,turn_is_formatted=true)`→final（两真才 final，防重复）；`turn_order` 作 item_id；`{"type":"Terminate"}`→`Termination{audio_duration_seconds,session_duration_seconds}`；`SpeakerRevision` 覆盖已渲染 speaker。
6. **火山（新）**：帧头 `0x11`、full client `0b0001|flags`、audio `0b0010`、server `0b1001`、error `0b1111`；JSON gzip；负包 flags `0b0010/0b0011`→最终 response；`utterances[].definite=true`→final、false→interim；`start_time/end_time` ms→s；`45000151`→格式错误处理。
7. **ElevenLabs（新）**：query 断言 `model_id=scribe_v2_realtime&audio_format=pcm_16000&commit_strategy=...`；`session_started`→ready；`partial_transcript`→interim；`{"message_type":"input_audio_chunk","audio_base_64":...,"commit":true}` 手动提交→`committed_transcript`→final；`rate_limited` 等类型化错误映射。
8. **Speechmatics（新）**：StartRecognition（`model:"enhanced"`，非 operating_point）→`RecognitionStarted`；binary→`AudioAdded`；`AddPartialTranscript`→interim（confidence 不采信）；`AddTranscript`→final；`EndOfStream{last_seq_no}`→`EndOfTranscript`；`4005/4013/1011` 走 ≥5–10s 重试。
9. **腾讯（新）**：签名 URL 参数齐全且 `expired>timestamp`；PCM 200ms 分片；`result.slice_type` 0/1→interim、2→final；`word_info=1` 时 `word_list` 词级时间戳透传；`voice_id` 每连接重建；speaker 流断言 `sentences.sentence_list[].speaker_id`（待 live 定稿字段）。

---

## 7. 证据缺口与未知项汇总

- **价格 Unknown**：Alibaba qwen-audio-3.0 流式单价、火山大模型流式刊例、腾讯大模型 2.0 刊例、ElevenLabs Scribe v2 Realtime、Speechmatics、OpenAI 当前转写模型、Gemini 3.5 Transcribe Live。接入前须回读对应官方 pricing 页并记 `lastVerifiedAt`。
- **协议 Unknown**：Gemini Live 转写专用页（Smart mode、finalized 事件、会话管理）；火山 speaker 输出字段；腾讯 V2 speaker 引擎响应 JSON；Deepgram 单连接时长上限与 `UtteranceEnd` 在 `vad_events` 单参数下的触发；Mistral 13 语种是否含 `ja`；Soniox 空帧文本/二进制在网关层的等价性。
- **需 live 验证**：各家真机 fixture 与错误码实测；AssemblyAI `language_codes` 偏置对日语的实际收益；OpenAI `languages` 提示效果。
- **检索范围声明**：本会话读取了上列官方 URL 原文；soniox.com 文档站两次直接抓取失败（网关路由异常），Soniox 协议经官方定价页、models 页、GitHub 官方 SDK 源码（`soniox/soniox-python`）交叉取证；Google “Live transcription” 专属页两次 URL 猜测 404 后按规则停止猜测，已记录。

---

## 8. 参考索引（本会话读取原文的 URL）

- Soniox：https://soniox.com/docs/stt/models ；https://soniox.com/pricing ；SDK 源码 https://github.com/soniox/soniox-python （`src/soniox/types/realtime.py`、`types/common.py`、`realtime/stt.py`）
- Alibaba：https://www.alibabacloud.com/help/en/model-studio/fun-asr-realtime-websocket-api ；…/fun-asr-client-events ；…/fun-asr-server-events ；https://www.alibabacloud.com/help/en/model-studio/asr-model
- Deepgram：https://developers.deepgram.com/docs/models-languages-overview ；https://deepgram.com/pricing ；参考页 https://developers.deepgram.com/reference/speech-to-text/listen-streaming
- OpenAI：https://platform.openai.com/docs/guides/realtime-transcription
- AssemblyAI：https://www.assemblyai.com/docs/streaming/api-spec/streaming-websocket ；https://www.assemblyai.com/docs/streaming/message-sequence ；https://www.assemblyai.com/docs/streaming/getting-started/transcribe-streaming-audio ；定价口径 https://www.assemblyai.com/blog/real-time-speech-to-text-best-for-voice-agents
- Google：https://ai.google.dev/gemini-api/docs/changelog ；https://ai.google.dev/gemini-api/docs/live-api/capabilities
- Mistral：https://docs.mistral.ai/studio/audio/overview ；https://docs.mistral.ai/studio/audio/speech_to_text/realtime_transcription ；https://mistral.ai/news/voxtral-transcribe-2/ ；https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602
- ElevenLabs：https://elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime ；产品页 https://elevenlabs.io/realtime-speech-to-text
- Speechmatics：https://docs.speechmatics.com/api-ref/realtime-transcription-websocket
- 火山引擎：https://docs.volcengine.com/docs/6561/1354869?lang=zh ；https://www.volcengine.com/docs/6561/1395846
- 腾讯云：https://cloud.tencent.com/document/product/1093/48982 ；https://cloud.tencent.com/document/product/1093/131127 ；https://cloud.tencent.com/document/product/1093/130881
- 仓库内上游文档：`docs/global-language-provider-expansion-plan.md`（实施计划）、`docs/research/realtime-stt-provider-adapters.md`（初步调研稿，时间敏感结论以本报告为准）
