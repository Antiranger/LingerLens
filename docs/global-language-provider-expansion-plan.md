# LingerLens 全球语言与 Provider 扩展实施方案

> 状态：实施前方案
> 日期：2026-09-01
> 范围：用户自配置源语言/目标语言；主流 realtime/streaming STT 适配；OpenAI/Claude/Gemini 翻译适配；语言目录与 UI 国际化基础。
> 本文是本轮调研后的权威实施方案；`docs/research/` 下三份报告是证据材料，其中会快速变化的模型名、价格、延迟和配额必须在实际实施时再次核验。

## 1. 决策摘要

LingerLens 不承诺“静态支持所有语言”，而采用 **Provider-driven language support**：用户选择源语言策略、目标语言和 Provider，系统根据当前模型的有效能力给出可用、实验性或不支持状态。

确定以下原则：

1. **源语言和目标语言都由用户配置。** 目标语言始终明确选择；源语言支持“指定语言”和“自动检测”，自动检测可以附带候选语言、首选语言和是否允许 code-switching。
2. **内部语言标识统一使用规范化 BCP 47 tag。** 例如 `ja`、`zh-Hans`、`zh-Hant`、`pt-BR`、`sr-Latn`。`auto` 不是语言 tag，而是源语言策略。
3. **Provider Adapter 独占协议差异。** Pipeline 只认识 PCM、统一 ASR 事件、统一翻译请求和统一 usage/error；鉴权、消息格式、语言代码别名、SDK callback、session rollover 都隐藏在 adapter 内。
4. **不做一个“万能 WebSocket Provider”。** Deepgram、AssemblyAI、OpenAI Realtime、Speechmatics、DashScope 的事件和会话语义不同，分别实现 adapter；只有在官方明确公开低层协议时才直接使用 `aiohttp`。
5. **复杂私有传输使用官方 SDK。** Azure Speech、Google Speech-to-Text、AWS Transcribe 分别通过官方 SDK/官方样例实现，作为可选依赖，避免逆向私有帧格式。
6. **翻译保留现有 `openai-compatible`，新增 `anthropic-messages` 和 `google-genai`。** 不做破坏性重命名；OpenAI 官方与兼容端点共用现有协议，Claude/Gemini 使用各自正式 REST schema。
7. **语言列表不手写。** 后端使用 `langcodes` 规范化 BCP 47；仓库提交一份由固定版本 CLDR/IANA 数据生成的紧凑 catalog；浏览器用 `Intl.DisplayNames` 根据 UI locale 显示本地化名称。
8. **语言支持分三级。** `verified`（LingerLens 实测）、`provider_claimed`（官方声明）、`experimental`（通用 LLM best-effort）。UI 不把实验性语言伪装成已保证支持。
9. **先扩展契约，再写 Provider。** 音频采样率、源语言策略和 usage 统一契约是所有后续 adapter 的共同前置工作。

## 2. 当前代码与目标之间的差距

当前调用路径为：

```text
player.js 固定 sourceLanguage="ja"
  → server.py 读取 sourceLanguage/targetLanguage 字符串
  → SubtitlePipeline.source_language
  → ASRProvider.stream(language: str, ...)
  → ASREvent.language（部分 Provider 返回）
  → TranslationRequest.meta.source_lang/target_lang
```

主要缺口：

- `web-player/player.js` 启动时把源语言写死为 `ja`。
- `index.html` 目标语言只有中文和英语。
- `ASRProvider.stream(language: str)` 无法表达自动检测、候选列表、首选语言和 code-switching。
- Pipeline 固定输出 16 kHz PCM，而当前 OpenAI Realtime 示例使用 24 kHz；虽然 `ASRCapabilities.sample_rates` 已存在，Pipeline 没有消费它。
- ASR Provider 能力只有一个语言 tuple，不能描述语言检测方式和模型级差异。
- 翻译模块存在多个重复且不完整的语言名称字典。
- `TranslationResult.usage` 是供应商原始 dict，缓存读取、缓存写入和普通输入的语义不统一。
- Provider Catalog 已能 CRUD，但协议 kind、model capability preset 和语言覆盖没有统一视图。
- 字幕 DOM 没有完整的 RTL/bidi 隔离。

现有可保留的深模块与 seam：

- `ASRProvider / ASRStream / ASREvent`
- `TranslationProvider.translate(TranslationRequest)`
- Provider registry 与本机 Provider Catalog
- `SubtitlePipeline` 的非阻塞音频 tee、重连和故障隔离
- loopback model-settings/providers API
- provider、pipeline、server 与浏览器测试入口

## 3. 目标领域模型

### 3.1 语言身份

```python
LanguageTag = str  # 服务端入口必须先规范化为 BCP 47
```

不要在配置或 Pipeline 中保存中文显示名、英文显示名或 Provider 私有代码。显示名称由 catalog/Intl 生成，私有代码只存在于 Adapter。

### 3.2 源语言策略

```python
@dataclass(frozen=True)
class SourceLanguagePolicy:
    mode: Literal["specified", "detect"]
    tag: str | None = None
    candidates: tuple[str, ...] = ()
    preferred: str | None = None
    allow_code_switching: bool = False
```

约束：

- `specified` 必须有 `tag`，不得有候选列表。
- `detect` 的候选列表可以为空，但只有支持 unrestricted detection 的 Provider 才接受为空。
- Azure/AWS 等 candidate-list Provider 必须提供符合其上限的候选语言。
- `preferred` 必须属于 `candidates`。
- `allow_code_switching` 只有 Provider/model 明确支持时才可启用。

### 3.3 检测结果

首期保留 `ASREvent.language` 作为主语言，并增加可选信息：

```python
language_confidence: float | None = None
languages: tuple[str, ...] = ()
```

`languages` 用于一句内或一个 turn 内的多语言结果。Cue 继续保留一个 dominant `lang`，同时允许保存 `languages`，避免把英文借词导致的瞬时变化当成整场语言切换。

### 3.4 Provider 语言能力

```python
@dataclass(frozen=True)
class ASRLanguageCapabilities:
    supported_tags: tuple[str, ...] | None
    detection: Literal["none", "unrestricted", "candidates"]
    max_candidates: int | None
    reports_detected_language: bool
    code_switching: bool

@dataclass(frozen=True)
class TranslationLanguageCapabilities:
    source_tags: tuple[str, ...] | None
    target_tags: tuple[str, ...] | None
    supported_pairs: tuple[tuple[str, str], ...] | None
    open_world_prompting: bool
```

能力是 **Provider profile + model** 的有效结果，不仅由协议 kind 决定。内置 model preset 提供官方能力；未知自定义模型显示为 `unknown/experimental`，允许用户显式覆盖，但不能自动升级成 verified。

## 4. STT Adapter 路线与优先级

### 4.1 第一批：高覆盖、低集成风险

#### A. Deepgram Streaming

- 新 kind：`deepgram-streaming`
- 实现：直接 `aiohttp` WebSocket，遵循官方 `/v1/listen` 低层协议。
- 音频：二进制 PCM16；复用 Pipeline 的音频时钟。
- 事件：`Results.is_final`/`speech_final`、word timestamps、confidence、detected languages；明确区分 transcription final 与 utterance end。
- 语言：指定语言或 `language=multi`；Nova/Flux 的可用语言由 model preset 决定。
- 原因：与当前 asyncio 架构最贴合；不增加 SDK 依赖；原生 code-switching 和时间戳适合直播字幕。

#### B. AssemblyAI Streaming v3

- 新 kind：`assemblyai-streaming`
- 实现：优先按照官方 v3 SDK/原始 WebSocket 示例验证后，用 `aiohttp` 实现公开协议；如果 v3 消息语义快速变化，则固定使用官方 SDK。
- Endpoint：`wss://streaming.assemblyai.com/v3/ws`。
- 事件：`Begin`、可修订 `Turn`、`end_of_turn`、`Termination`。
- 语言：按具体 Universal 模型 preset 暴露多语言/code-switching，不再按旧资料标记为 English-only。
- 特别测试：同一 turn 的 revision 必须覆盖 interim，而不能生成重复 cue。

#### C. OpenAI Realtime Transcription

- 新 kind：`openai-realtime-transcription`
- 实现：直接 `aiohttp` WebSocket，使用当前 `type: "transcription"` session schema；禁止复用旧 `gpt-4o-realtime-preview` payload。
- 默认 preset：`gpt-live-transcribe`，用于增量 delta；需要 detected-language 时可选 `gpt-transcribe`。
- 音频：当前官方示例 24 kHz PCM；因此先完成 Pipeline sample-rate negotiation。
- 语言：`gpt-live-transcribe` 使用 `languages` 候选提示但不返回 detected-language；`gpt-transcribe` completion 可返回 `languages`。
- 时间戳：当前实时模型不返回 word timestamps，使用 server VAD + LingerLens audio clock 的现有 fallback。

### 4.2 第一批中的 DashScope 工作

当前 `dashscope-task-asr` 已经实现官方 `run-task → task-started → binary audio → result-generated → finish-task` 协议，因此不要另造重复 kind。

实施内容：

- 为 `Qwen-Audio-3.0-ASR-Flash-Streaming` 和当前 Fun-ASR 增加/刷新 model presets。
- 将语言、word timestamp、自动识别、热词、上下文能力放入 preset，而不是在 adapter 中按模型名猜测。
- 保留 `dashscope-qwen-realtime`，因为它使用不同的 OpenAI-like realtime event contract，并且 stable prefix 对当前 prefix split 有价值。
- 对新版模型做捕获测试，确认事件字段后再更新映射；不能只改 model string。

### 4.3 第二批：官方 SDK-backed

#### Azure AI Speech

- 新 kind：`azure-speech-sdk`
- 可选依赖：`azure-cognitiveservices-speech`
- 实现：`PushAudioInputStream` + continuous recognition；SDK callback 通过 `loop.call_soon_threadsafe` 进入 `asyncio.Queue`。
- 语言：指定 locale；at-start detection 最多 4 个候选；continuous detection 最多 10 个候选；同一句内 code-switching 不支持。
- SDK worker 的关闭、异常和 callback 生命周期必须由 adapter 内部处理，Pipeline 不接触 SDK 对象。

#### Google Cloud Speech-to-Text v2

- 新 kind：`google-cloud-speech-v2`
- 可选依赖：`google-cloud-speech`
- 实现：官方 `SpeechClient.streaming_recognize` gRPC bidi 示例；使用 ADC/service account 配置，不在 URL 字段里模拟 API key。
- 需要 adapter 内部 session rollover/stitching；对外仍是一个 `ASRStream`。
- 语言与多语言能力按 recognizer/model/region preset 表达，不把 `language_codes` 自动等同于句内 code-switching。

#### AWS Transcribe Streaming

- 新 kind：`aws-transcribe-streaming`
- 优先官方 SDK/官方 EventStream 实现；不从头手写 SigV4 + CRC EventStream。
- 语言检测明确建模为 candidates；官方要求至少两个 `language-options`，错误候选会被强制匹配，因此 UI 必须提示。
- 多语言识别与 custom language model/redaction 的功能冲突通过 capability validation 提前拦截。

### 4.4 第三批/按需

#### Speechmatics Realtime

- 新 kind：`speechmatics-realtime`
- 协议公开，可直接 `aiohttp`；官方也有 `speechmatics-python`。
- 先作为高质量“指定单语言 + diarization + timestamps” Provider。
- 不把普通 v2 realtime 自动宣传成 unrestricted code-switching；只有具体 Omni/model 文档和 live fixture 验证后再开放该能力。

### 4.5 暂不做

- 不在首轮适配所有区域性厂商；Provider registry 已允许后续添加。
- 不把 file transcription 的短窗模拟称为 realtime streaming；现有 `openai-audio-transcriptions` 继续保留为本地 Whisper/兼容服务方案。
- 不在本轮加入 speaker UI；可先保留 speaker 元数据扩展位，但不能让 diarization 扩大首轮范围。

## 5. 翻译 Adapter 方案

### 5.1 保留和新增的 kind

| Kind | 处理协议 | 决策 |
|---|---|---|
| `openai-compatible` | `/chat/completions`、Bearer、OpenAI message schema | 保留；OpenAI 官方与兼容端点继续使用 |
| `anthropic-messages` | `/v1/messages`、`x-api-key`、top-level `system` | 新增 |
| `google-genai` | Gemini `models/{model}:generateContent`、`systemInstruction` | 新增 |
| `qwen-mt` | `translation_options`、术语与 TM | 保留 |

不建议只增加“OpenAI/Claude/Gemini 三个品牌选项”然后在同一类中塞 if/else。协议、鉴权、usage 和错误语义差异应该由三个 Adapter 隔离。

### 5.2 共享 Prompt 模块

新增 `companion/translation_prompt.py`，只生成 provider-neutral 内容：

```python
@dataclass(frozen=True)
class TranslationInstruction:
    system_text: str
    user_text: str
```

- 统一使用 canonical tag 对应的稳定英文名称，如 `Japanese`、`Simplified Chinese`、`Portuguese (Brazil)`；不要根据当前中文 UI locale 改写系统提示词。
- `system_text` 包含任务规则、源/目标语言、直播元数据和术语表。
- `user_text` 包含 rolling HISTORY 与 CURRENT。
- Qwen-MT 不使用此 prompt，而把 canonical tags 映射为正式 `translation_options`。

### 5.3 Usage 与价格模型

现有三个价格字段不足以准确表示 Anthropic cache write。升级为：

```text
pricePerMillionInputTokensCny
pricePerMillionCachedInputTokensCny       # cache read
pricePerMillionCacheWriteTokensCny        # 新增
pricePerMillionOutputTokensCny
```

统一 usage：

```python
@dataclass(frozen=True)
class TranslationUsage:
    non_cached_input_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int
    output_tokens: int
    raw: dict | None = None
```

映射规则：

- OpenAI：`prompt_tokens - cached_tokens`、`cached_tokens`、0、`completion_tokens`。
- Anthropic：`input_tokens`、`cache_read_input_tokens`、`cache_creation_input_tokens`、`output_tokens`。不要假设 `input_tokens` 包含 cache read/write。
- Gemini：`promptTokenCount - cachedContentTokenCount`、`cachedContentTokenCount`、0、`candidatesTokenCount`。
- Provider 未返回某项时用 0；整个 usage 缺失时费用不可估算。

### 5.4 错误语义

新增统一异常类别，供 `FallbackChain` 决策：

- `ProviderAuthError`：401/403/bad key；不做当前请求重试。
- `ProviderRequestError`：400/unsupported model/language；立即 fallback，并标记配置问题。
- `ProviderRateLimitError`：429；读取 `Retry-After`，进入短 cooldown。
- `ProviderUnavailableError`：5xx、Anthropic 529、网络断连；计入连续失败。
- `asyncio.TimeoutError`：保留当前 deadline 语义。

Provider 返回空 candidates、safety block、refusal 或非文本内容时，不得当成成功空译文。

## 6. 语言目录与国际化方案

### 6.1 后端依赖与数据

新增轻量依赖：

```text
langcodes>=3.5,<4
```

用途：

- `standardize_tag()`：规范化 `eng_US → en-US`、旧代码替换、冗余 script 处理。
- `tag_is_valid()`：服务端配置入口验证。
- tag matching/distance：Provider 只支持 `pt` 时，判断 `pt-BR` 是否可降级；任何降级都必须由 Provider mapping policy 明确允许。

不要求运行时安装完整 `language_data`。增加开发脚本：

```text
scripts/generate-language-catalog.py
prototype/hls-companion/companion/data/languages.json
```

生成输入固定版本：

- IANA Language Subtag Registry / BCP 47
- Unicode CLDR display/language/script/territory 数据
- LingerLens Provider model presets 声明的 tags

生成输出至少包含：

```json
{
  "version": 1,
  "cldrVersion": "...",
  "languages": [
    {
      "tag": "zh-Hant",
      "englishName": "Chinese (Traditional)",
      "autonym": "繁體中文",
      "language": "zh",
      "script": "Hant",
      "region": null,
      "direction": "ltr",
      "aliases": ["zh-TW", "zh-HK"]
    }
  ]
}
```

不要声称 catalog 中每一项都是所有 Provider 可用语言；它只是身份与显示目录。

### 6.2 浏览器职责

- `Intl.getCanonicalLocales` / `Intl.Locale` 做前端早期校验，服务端仍是最终权威。
- `Intl.DisplayNames([uiLocale], {type: "language"})` 生成当前 UI 语言下的名称。
- 列表展示：`本地化名称 · autonym · BCP 47 tag`。
- `Intl.Collator` 做大小写/重音不敏感排序和搜索。
- `Intl.Locale.prototype.getTextInfo()` 仅作增强；旧浏览器使用 catalog `direction`。
- 字幕使用 `dir="auto"`，源文和译文分别使用 `<bdi>`，而不是给整个双语窗口强制同一方向。

### 6.3 UI 控件

字幕设置改为：

```text
源语言：[自动识别 ▼ / 指定语言 ▼]
  自动识别时：[候选语言多选] [允许混合语言]
  指定语言时：[可搜索语言 combobox]
目标语言：[可搜索语言 combobox]
能力提示：当前 ASR / 翻译模型、支持等级、冲突原因
```

行为：

- 目标语言列表先显示与当前翻译 Provider 明确兼容的项，再显示实验性项。
- 源语言选项依据当前 ASR model preset；不支持 auto 时禁用自动识别并解释原因。
- 用户切换 Provider 后重新计算能力，但不静默替换已保存语言；无效配置显示阻断错误。
- 提供“常用语言”快速区和完整搜索，不在 `<select>` 中一次渲染几百项。
- 将现有“中日双语/仅中文”改成“原文+译文/仅译文/仅原文”，避免显示模式绑定具体语言。

### 6.4 UI locale

本轮只建立独立字段和资源 seam：

```json
"ui": { "locale": "zh-CN" }
```

将中文 UI 字符串全部抽取为翻译资源是单独的后续 vertical slice，不能与字幕目标语言混用。第一阶段可继续只有中文 UI，但数据模型必须从一开始分离。

## 7. 配置 Schema v3

建议一次升级到 v3：

```json
{
  "version": 3,
  "asr": {
    "active": "deepgram-nova3",
    "providers": [
      {
        "id": "deepgram-nova3",
        "kind": "deepgram-streaming",
        "model": "nova-3",
        "baseUrl": "wss://api.deepgram.com/v1/listen",
        "apiKeyEnv": "DEEPGRAM_API_KEY",
        "capabilityPreset": "deepgram:nova-3:multi",
        "options": {
          "endpointingMs": 100,
          "interimResults": true,
          "smartFormat": true
        }
      }
    ]
  },
  "translation": {
    "active": "claude-fast",
    "fallback": ["gemini-flash", "openai-compatible-default"],
    "providers": []
  },
  "subtitle": {
    "sourceLanguage": {
      "mode": "detect",
      "candidates": ["ja", "en", "zh-Hans"],
      "preferred": "ja",
      "allowCodeSwitching": true
    },
    "targetLanguage": "zh-Hans"
  },
  "ui": { "locale": "zh-CN" }
}
```

迁移规则：

- v2 `sourceLanguage: "ja"` → `{"mode":"specified","tag":"ja"}`。
- v2 `targetLanguage: "zh"` → `zh-Hans`，因为现有产品中文译文和 UI 明确是简体中文。
- 保留 provider IDs、active、fallback、keys、pricing 和其他 subtitle preferences。
- `openai-compatible` 不改名；迁移不得迫使用户重录 endpoint。
- v3 持久化前先 canonicalize tags；原始非法值返回可行动错误，不静默截断。

## 8. Pipeline 改造

### 8.1 Sample-rate negotiation

当前 FFmpeg 固定 `-ar 16000`。改为：

1. 读取 active ASR profile 的 `preferred_sample_rate`。
2. `SubtitlePipeline.start()` 用该值创建 FFmpeg PCM 输出。
3. `PCM_BYTES_PER_SECOND`、chunk bytes 和 audio-clock 计算改为实例字段。
4. Provider adapter 再校验实际 sample rate 是否在 capabilities 内。

首期支持 16 kHz 和 24 kHz；不为每个 Provider 启动第二个 FFmpeg。

### 8.2 Detected-language propagation

- `SubtitlePipeline` 将 `SourceLanguagePolicy` 交给 ASR Provider。
- 每个 final cue 使用 event 报告的 canonical language；没有报告时回退 specified/preferred/effective language。
- 翻译请求的源语言使用 cue 的 effective language，而不是整场写死的 `meta.source_lang`。
- 对 auto dominant-language provider 增加一个小型稳定器：仅用于填补“事件没有语言”时的 session effective language，不覆盖 Provider 已明确给出的 cue language。
- 对原生 code-switching Provider，允许一条 cue 带多语言；LLM prompt 写成 `mixed Japanese and English` 或仅省略固定 source，专用 MT Provider 若不支持则跳过/fallback。

不要实现“统一三句 hysteresis 后才允许每条 cue 更新语言”的硬规则。它会错误压制真正的 code-switching；稳定器只服务于 session fallback 和 UI 状态。

### 8.3 Reconnect ownership

现有 `_asr_manager` 继续拥有跨 Provider 的指数退避和“掉线期间不补历史音频”。Adapter 内只负责：

- 当前 session 建立/关闭；
- Provider 强制时长 rollover，在能无损衔接时内部完成；
- SDK callback 转 async event；
- keepalive/finalize；
- 将 Provider error 映射为统一异常。

不要再增加一个通用 `ReconnectingASRStream`，否则会与 Pipeline 双重重连。

## 9. 分阶段实施顺序

### Phase 1 — 语言领域与配置基础

文件：

- 新增 `companion/languages.py`
- 新增 generated `companion/data/languages.json`
- 修改 `providers/base.py`、`providers/config.py`
- 新增 `tests/test_languages.py`
- 扩展 `test_providers.py`、`test_server_providers.py`

交付：BCP 47 canonicalization；source policy；schema v3 迁移；capability model；language catalog API。

### Phase 2 — UI 源/目标语言配置与 bidi

文件：

- `web-player/index.html`
- `web-player/player.js`
- `web-player/style.css`
- 新增 `web-player/language-selector.js`
- 浏览器测试

交付：可搜索源/目标语言；auto/candidates；Provider 能力提示；通用显示模式文案；`dir=auto`/`bdi`；设置持久化。

### Phase 3 — Pipeline 契约与采样率

文件：

- `subtitle_pipeline.py`
- `providers/base.py`
- cue/store model（仅在需要保存 `languages` 时）
- pipeline tests

交付：policy 传递、16/24 kHz negotiation、detected language → cue → translation、混合语言 fallback。

### Phase 4 — 第一批 STT

顺序：

1. Deepgram
2. OpenAI Realtime transcription
3. AssemblyAI v3
4. DashScope model preset refresh

每个 Provider 单独 vertical slice：先 fake WebSocket fixture 测协议，再 live spike，再加入 UI preset；不得一次性写完四个后统一测试。

### Phase 5 — 翻译 Provider

顺序：

1. 共享 prompt/usage/error contract
2. Anthropic Messages
3. Gemini GenerateContent
4. 现有 OpenAI-compatible/Qwen-MT 迁移到共享 contract
5. FallbackChain 语义升级

### Phase 6 — SDK-backed STT

顺序建议：Azure → Google → AWS。各自为可选依赖和独立安装检查，不阻塞核心 Companion 启动。

### Phase 7 — Speechmatics 与 UI locale

- Speechmatics 指定单语言 realtime adapter；经官方模型合同验证后再开放 auto/code-switching。
- 抽取 UI 文案资源，首批至少 `zh-CN` 和 `en`；这不影响字幕语言覆盖。

## 10. 测试与验收

### 10.1 Language 模块

- 规范化：`eng_US → en-US`、`iw → he`、`zh-CN → zh-Hans`（产品 alias policy）、`zh-TW → zh-Hant`。
- 区分：`pt-BR` 与 `pt-PT`、`sr-Cyrl` 与 `sr-Latn`。
- 拒绝非法 tag、把 `auto` 当 tag、候选上限超限、preferred 不属于 candidates。
- generated catalog 构建可重现；输入版本与输出 hash 进入测试。

### 10.2 Provider contract fixtures

每个 STT fixture 必须覆盖：

- 握手鉴权与 session start；
- PCM frame 格式和 sample rate；
- interim revision；
- final + item ID；
- timestamps/VAD；
- detected language / multi-language；
- keepalive/finalize/close；
- 401/429/5xx/protocol error；
- Pipeline stop 时资源释放；
- 网络中断后仅由既定重连 owner 重建 session。

SDK-backed adapter 使用 fake SDK facade，不让单元测试依赖云服务或系统音频。

### 10.3 翻译 fixture

- OpenAI、Anthropic、Gemini 正确 endpoint/header/payload。
- system instruction 与 rolling history 不串位。
- usage 映射覆盖普通输入、cache read、cache write、输出。
- safety/refusal/empty candidate 归类为失败。
- 429/529/503/timeout 触发正确 fallback/cooldown。
- Qwen-MT provider alias 映射和 unsupported pair 提前跳过。

### 10.4 Pipeline 行为

- 指定日语 → 英语保持现有行为。
- auto 候选中检测法语，翻译 prompt source 为 French。
- 一条 Deepgram cue 包含 `fr/en` 时，通用 LLM 可翻译，专用不支持 MT 自动 fallback。
- OpenAI Realtime 无 timestamp 时使用 VAD/approx，不伪造 ASR timestamp capability。
- ASR 24 kHz 时 audio clock、cue wall-clock 和成本秒数正确。
- ASR/翻译失败仍不阻塞媒体播放。

### 10.5 浏览器行为

- 搜索 native name、UI-localized name、tag 均能找到语言。
- 选定源/目标在刷新和 Companion 重启后保存。
- Provider 切换后冲突有明确提示，不静默替换。
- Arabic/Hebrew 译文和 LTR 原文同屏无 bidi 污染。
- 键盘可完成 combobox 搜索、选择和候选多选。

### 10.6 Live acceptance matrix

每个上线 STT 至少使用以下真实样本：

- 单语言：英语、日语、普通话、西班牙语、阿拉伯语；
- code-switching：日英、英西；
- 噪声、音乐间隙、专有名词、连续说话；
- 连续 30 分钟 session；如 Provider 上限更短，验证 rollover；
- 指标：final WER/CER、partial rewrite 数、final latency p50/p95、timestamp error、空/幻觉 cue、重连次数。

语言支持升级为 `verified` 必须有测试记录；只通过官方列表的仍为 `provider_claimed`。

## 11. 依赖与发布策略

核心依赖：

```text
aiohttp
langcodes
```

可选依赖文件：

```text
requirements-azure.txt
requirements-google-stt.txt
requirements-aws-transcribe.txt
```

UI 在选择缺少依赖的 Provider 时给出精确安装命令；Companion 启动不因未安装可选 SDK 失败。CI 核心 job 不安装所有 SDK；另设 adapter contract job，可按平台能力安装 Azure/Google/AWS 依赖并运行 fake tests。

官方 SDK/样例的使用原则：

- 参考或调用官方 SDK，不复制大段实现进入仓库。
- 官方 sample 代码许可与链接记入 `THIRD_PARTY_NOTICES.md`；如果只根据协议重写，不复制源码，则记录文档来源即可。
- 所有 model presets 带 `lastVerifiedAt` 和官方 capability source URL，便于后续刷新。

## 12. 关键风险与控制

| 风险 | 控制 |
|---|---|
| 模型名和 preview schema 快速变化 | adapter protocol test + model preset 分离；实施时再次读取官方文档 |
| “多语言”含义不一致 | 能力枚举区分 specified、candidate detection、dominant detection、code-switching |
| 候选不含真实语言却强制误识别 | UI 警告；AWS/Azure candidate validation；运行状态展示 detected language |
| Provider 支持列表过期 | preset 带来源/日期；未知模型默认 experimental |
| 大量 SDK 使 Windows 安装脆弱 | 可选依赖；核心保持 aiohttp + langcodes |
| RTL 双语字幕方向混乱 | 每行 `dir=auto` + `<bdi>` + logical CSS |
| 自动语言切换造成 prompt 抖动 | 每 cue 优先使用 Provider 明确语言；session 稳定器只作 fallback/UI，不覆盖真实 code-switching |
| Provider adapter 重复重连 | Pipeline 保持唯一通用重连 owner；adapter 只处理强制 rollover |
| 翻译缓存计费错误 | cache read/write 分栏；缺价格显示不可估算 |

## 13. 不在本轮范围

- 不保证世界上所有自然语言和方言的质量。
- 不为每种语言建立人工翻译文案或 benchmark。
- 不把 speaker diarization 做成用户功能。
- 不合并 STT 与翻译成某厂商专有的一体化 speech translation pipeline；现有 ASR → text → MT seam 保持可替换。
- 不让 Provider 自动决定目标语言；目标语言始终由用户明确选择。
- 不自动下载或运行本地 ASR 模型。

## 14. 主要证据

STT：

- OpenAI Realtime transcription: https://platform.openai.com/docs/guides/realtime-transcription
- Deepgram low-level streaming/code-switching: https://developers.deepgram.com/reference/speech-to-text/listen-streaming 、https://developers.deepgram.com/docs/multilingual-code-switching
- AssemblyAI Streaming v3: https://www.assemblyai.com/docs/speech-to-text/streaming
- Azure language identification: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-identification
- Google STT v2 streaming: https://cloud.google.com/speech-to-text/v2/docs/streaming-recognize
- AWS streaming language identification: https://docs.aws.amazon.com/transcribe/latest/dg/lang-id-stream.html
- Speechmatics Realtime API/SDK: https://docs.speechmatics.com/api-ref/realtime-transcription-websocket 、https://github.com/speechmatics/speechmatics-python-sdk
- DashScope realtime WebSocket: https://help.aliyun.com/zh/model-studio/fun-asr-realtime-websocket-api

翻译：

- OpenAI API docs: https://platform.openai.com/docs/
- Anthropic Messages API: https://platform.claude.com/docs/en/api/messages/create
- Google Gen AI SDK/API: https://github.com/googleapis/python-genai

语言与 i18n：

- BCP 47 / RFC 5646 and RFC 4647: https://www.rfc-editor.org/bcp/bcp47
- Unicode CLDR / UTS #35: https://unicode.org/reports/tr35/
- ECMA-402: https://tc39.es/ecma402/
- `Intl.Locale.getTextInfo` proposal/compatibility: https://tc39.es/proposal-intl-locale-info/ 、https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl/Locale/getTextInfo
- `langcodes`: https://github.com/rspeer/langcodes
