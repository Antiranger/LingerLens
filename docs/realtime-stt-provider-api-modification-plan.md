# Realtime STT Provider API 修改计划（已落实基线）

> 状态：Ticket 02 实施记录与后续 rollout 计划  
> 协议核验日期：2026-09-03  
> 证据：`docs/research/realtime-stt-provider-protocol-audit-2026.md`

## 1. 本轮已落实的 Provider 矩阵

| Kind | Preset / model | 协议与默认 endpoint | 状态 | 一手来源 |
|---|---|---|---|---|
| `dashscope-task-asr` | `qwen-audio-3.0-asr-flash-streaming` | DashScope duplex `run-task`, `/api-ws/v1/inference` | 已增加 preset；候选语言≤4、`vocabulary`、context | Alibaba realtime WebSocket/client/server event docs |
| `soniox-realtime` | `stt-rt-v5` | Soniox token stream, `wss://stt-rt.soniox.com/transcribe-websocket` | 已修订 lexical timestamp、speaker/confidence、尾部 drain | Soniox WebSocket docs + `soniox-python` |
| `deepgram-streaming` | `nova-3` | `/v1/listen` | 已增加 diarize/utterance-end options 与 speaker mapping | Deepgram streaming/model docs |
| `openai-realtime-transcription` | `gpt-live-transcribe`, `gpt-transcribe` | current transcription session, `/v1/realtime` | 已审计；保留当前正式 schema | OpenAI Realtime transcription guide |
| `assemblyai-streaming` | `universal-3-5-pro` | Streaming v3, `wss://streaming.assemblyai.com/v3/ws` | 新增 | AssemblyAI v3 API/message sequence |
| `volcengine-sauc` | `bigmodel_async` | v3 binary SAUC, `/api/v3/sauc/bigmodel_async` | 新增 | Volcengine v3 protocol/SDK endpoint docs |
| `elevenlabs-scribe-realtime` | `scribe_v2_realtime` | `/v1/speech-to-text/realtime` | 新增 | ElevenLabs realtime STT reference |
| `speechmatics-realtime` | `enhanced` / `standard` | Realtime v2, `wss://global.rt.speechmatics.com/v2/` | 新增 | Speechmatics realtime v2 reference |
| `tencent-asr` | `16k_ja`, `16k_zh_en_speaker_2.0`, etc. | signed `/asr/v2/<appid>` | 新增 | Tencent classic/V2/speaker docs |

所有新云端 preset 当前是 `provider_claimed`；fake protocol 测试不把真实云端质量升级为 `verified`。

## 2. 统一契约变化

本轮没有新增第二套 transcript 类型。继续使用 `ASRProvider → ASRStream → ASREvent`：

- `ASREvent.speaker: str | None`：Provider 报告的 dominant speaker；不猜测缺失标签。
- `ASREvent.confidence: float | None`：Provider 明确给出的转写置信度；不从文本估算。
- `ASREvent.type += "speaker_revision"`：承载 AssemblyAI 等事后 speaker 修订；`item_id` 是覆盖键。
- `ASRCapabilities.speaker_labels: bool`：只有 model 支持且 profile 开启时为 true。

兼容性：字段都有默认值；旧 Adapter、旧序列化调用和旧前端可忽略它们。

### 时间戳所有权

- Adapter 负责解释 Provider 原点、单位和字段含义，并转换为 session-relative 秒。
- lexical/word/VAD/control timestamp 必须分别处理。控制 token 的 0/缺失时间不得覆盖 lexical end。
- Pipeline 只使用 `begin_pcm/end_pcm` 和 breadcrumbs 映射到媒体时钟；它不解析 Provider raw frame。
- `raw` 仅用于诊断/未来词级功能，不是通用调度接口。

### Speaker revision 所有权

- Adapter 产生 `speaker_revision(item_id, speaker)`。
- Pipeline 当前只计数，不追溯修改已翻译 cue；普通 final 的 speaker 写入 `Cue.speaker` 并由 `/api/subtitles` 序列化。
- 本轮不增加 speaker UI。后续 UI ticket 若需回写，应维护 provider item-id → cue-id 索引并通过 CueStore revision 更新，而不是让 Adapter操作 Store。

## 3. Config / Provider Catalog

Config schema 保持 version 3，避免没有必要的破坏性迁移。已有 profile、active/fallback、API key、pricing 和 subtitle settings 原样保留。新增内容只包括：

- 独立 Provider kinds；不做通用 WebSocket editor。
- model-level capability preset；未知 model 为 `experimental`，不得继承 kind 的能力。
- Provider 专属 options（例如 Soniox speaker、Deepgram diarize、AssemblyAI mode、Volcengine resource ID、Speechmatics model/diarization、Tencent appId/secretId/engine）。
- `runtime/providers.example.json` 提供未验证示例；不会自动改变现有用户的 active provider。

秘密边界：

- Tencent SecretKey、SecretId 和签名 URL不得进入 status/log/error；签名 URL仅用于 `ws_connect`。
- 普通 `/api/providers`、`/api/status` 和异常响应继续脱敏。
- 原始 key只在现有 loopback + same-origin、`no-store` 的 `/api/model-settings` 编辑边界内显示。

## 4. Loopback API 兼容

- `/api/model-settings`：继续读写完整 catalog；新增 kind/options 不改变 envelope。
- `/api/providers`：继续返回 masked config。
- `/api/status`：不新增密钥或 signed URL；现有 provider ID 和 capability/status统计继续工作。
- `/api/subtitles`：Cue 新增可选 `speaker` 字段。旧浏览器忽略未知字段；未启用 diarization 时为 null。
- `/api/languages`：使用每个 model preset 的 `ASRLanguageCapabilities` 做启动前校验。

## 5. Pricing 演进

当前运行成本仍以 `pricePerSecondCny` 计算。本轮不把未经核验的 USD/地域价格硬编码为 CNY。后续兼容演进建议：

```json
{
  "pricing": {
    "currency": "USD",
    "unit": "audio_second",
    "rate": 0.000033333,
    "addons": {"diarization": 0.000016667},
    "lastVerifiedAt": "2026-09-03",
    "sourceUrl": "https://..."
  }
}
```

迁移期间保留 `pricePerSecondCny`；只有币种为 CNY 且来源明确时才自动映射。缺价格必须显示 unavailable，不得填 0。

## 6. Lifecycle 与重连

- SubtitlePipeline 仍是唯一通用重连 owner。
- Adapter 只负责单 session 的 start/audio/control/drain/release。
- Soniox、AssemblyAI、Volcengine 等有尾部 final 的协议使用 bounded drain；停止不能无限等待，也不能在官方 final 到达前主动丢弃。
- 网络断开期间不缓存/补发历史 PCM，保持现有真实时间线语义。

## 7. 自动验证与 CI

每个新 Adapter 有独立 fake WebSocket suite，覆盖 auth/start/audio、interim/revision/final、timestamp、speaker/language、finalize/stop/error、资源释放。所有 suite 已加入 `package.json` 的 `test:hls-companion`，不要求云 key。

核心安装仍只有 `aiohttp` 与既有依赖；本轮没有新增厂商 SDK，因此 bootstrap/CI 无额外 wheel 或平台依赖。

## 8. Defer 项目

### Google `gemini-3.5-transcribe-live`

模型已确认存在，但当前可取得的一手资料未完整公开转写专用 finalized event、Smart transcription 配置、会话限制和计费合同。解除阻塞条件：读取官方专用页/SDK类型，完成 official-SDK spike，并获得稳定 final/timestamp语义后再创建独立 kind。

### Mistral `voxtral-mini-transcribe-realtime-2602`

官方只公开 `mistralai[realtime]` SDK facade，低层 WebSocket frame未公开。解除阻塞条件：选择可选 SDK Adapter、定义缺依赖预检与 CI fake facade，并确认 13 语言列表是否包含日语。不得按猜测实现 native aiohttp。

## 9. Rollout 顺序

1. 合并统一可选 speaker/confidence contract与现有四家审计修复。
2. 发布 AssemblyAI、ElevenLabs、Speechmatics（普通 JSON/PCM协议）。
3. 发布 Volcengine（二进制 framing）和 Tencent（签名 URL），先做真实凭据 smoke。
4. 逐家完成至少 10 分钟日语/中英 live spike；只有有记录的 preset升级为 `verified`。
5. 单独规划 speaker UI与多币种 pricing schema，不与基础 Adapter 上线耦合。
