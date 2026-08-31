# 实施计划：实时字幕管线（ASR → 清洗 → 翻译 → 画面对齐）

> 日期：2026-08-30。本文是**给落地会话的可执行规格**，不是调研文档。
>
> 前置阅读顺序：
> 1. `docs/asr-translation-handoff.md`（架构决策与延迟预算，**必读**）
> 2. `bailian_live_translation_plugin_handoff.md` §4 / §6 / §16 / §17（供应商与上下文策略）
> 3. `docs/live-delay-solution.md`（既有延迟管线，只在需要理解 setts/双腿时读）
>
> 本文对上述文档有 **3 处修正**，见 §0。修正优先级高于旧文。

---

## 0. 对既有 handoff 的修正（先读，避免按旧文写错）

### 修正 1：字幕的时间锚点必须是「句尾」，不是「句首」

`asr-translation-handoff.md` §4 隐含用句首墙钟给 cue 打戳。这在长句上必然失败：

一句日语 6 秒，句首在 T，句尾在 T+6。ASR final 最早在 T+6.8（VAD 静音 600ms + 网络），翻译完成约 T+8。而播放头在 T+6 左右就到达句首画面——**译文比它自己的锚点晚 2 秒以上，永远追不上**。

正确做法：cue 的显示锚点是 `tEnd`（这句话说完的墙钟），显示时长 `hold`。这也正是人工直播字幕的行为——句子说完才整句出现。

| 事件 | 相对句尾墙钟 T_end |
|---|---|
| 音频句尾通过 tee | 0 |
| ASR final 到达 | +0.6 ~ 1.2s |
| 翻译完成 | +1.2 ~ 2.7s |
| **播放头到达该句尾画面** | **+5 ~ 7s** |
| 余量 | **+2.5 ~ 5s** ✅ |

`tStart` 仍然要记录（用于 P2 的 interim 前缀字幕、诊断、以及"句子跨度"UI），但第一版不用它定位。

### 修正 2：ASR 默认模型改为 `qwen3-asr-flash-realtime`（与 asr-translation-handoff 一致，推翻 bailian §4 的 qwen-audio-3.0 首选）

理由：

1. 它的协议是 **OpenAI Realtime 形状**（`session.update` / `input_audio_buffer.append` / `conversation.item.input_audio_transcription.*`）。同一个 provider 适配器几乎零改动就能接 OpenAI realtime transcription、LiteLLM 网关、以及任何仿 Realtime 协议的服务——这正好是本次要做的 provider 抽象最想要的杠杆。
2. `text` / `stash`（稳定前缀 + 待修订后缀）天然支持后续的 stable-prefix 提前翻译（P2）。
3. 服务端 VAD（`turn_detection.server_vad`）由模型侧完成，我们不需要自己做 VAD。
4. 日语在支持列表内；`silence_duration_ms` 可调，正好匹配"我们有延迟预算，可以断得稍晚一点"的产品定位。

`qwen-audio-3.0-asr-flash-streaming` / `fun-asr-realtime` 走的是 DashScope 任务式协议（`run-task` / `sentence` 带 `begin_time`/`end_time`/`words[]`），作为**第二个 provider kind** 实现——见 §3 的"两个 kind 才算抽象"原则。

### 修正 3：播放器侧的墙钟映射直接用 hls.js 的 `hls.playingDate`

`asr-translation-handoff.md` §3 决策 2 让播放器自己解析播放列表算 `PDT_edge − (edge − currentTime)`。不必要：hls.js 已内建 `hls.playingDate`（返回当前播放头对应的 `Date`，来源就是 `EXT-X-PROGRAM-DATE-TIME`）。

第一版直接用它，`null` 时回退到 `LEVEL_UPDATED` 里 `details.fragments[].programDateTime` 手算。

---

## 1. 目标与验收总纲

**做完之后**：在工作台播放一路日语直播，画面上叠着与画面同步的中文字幕（可切双语），字幕的模型供应商可在 UI 里切换与配置。

端到端验收（人测，5 分钟以上真实直播）：

- [ ] 中文字幕与画面口型误差目视 < 1.5s（允许用 UI 的偏移滑块一次性校准）
- [ ] 连续 5 分钟无字幕断流、无内存增长、无播放卡顿
- [ ] 切换 ASR / 翻译 provider 后重启会话生效，UI 显示当前生效的 provider
- [ ] `/api/status` 报告累计 ASR 秒数与估算费用
- [ ] tee 队列满时只丢字幕、计数上报，播放画面不受任何影响

---

## 2. 已核实的外部事实（2026-08-30 检索，落地前请再确认打 ⚠️ 的项）

### 2.1 `qwen3-asr-flash-realtime`

- **WebSocket**：`wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3-asr-flash-realtime`
  （交叉验证来源：QwenLM/qwen-code `deriveQwenRealtimeUrl` 测试；makecindy/cindy 的 provider 表用同路径的 LiteLLM 透传 `/dashscope/api-ws/v1/realtime?model=...`）
- **鉴权**：握手阶段 `Authorization: Bearer <DASHSCOPE_API_KEY>`；key 缺失/无效直接 HTTP 401/403，握手失败
- **音频**：PCM 16-bit 单声道，16000 Hz；`input_audio_buffer.append` 的 `audio` 字段是 **base64 字符串**（不是二进制帧）
- **客户端事件**：`session.update`、`input_audio_buffer.append`、`input_audio_buffer.commit`、`session.finish`
- **服务端事件**：`session.created` / `session.updated` / `session.finished` / `error` / `input_audio_buffer.speech_started` / `input_audio_buffer.speech_stopped` / `input_audio_buffer.committed` / `conversation.item.created` / `conversation.item.input_audio_transcription.text` / `.completed` / `.failed`
- **中间结果**：`...transcription.text` 事件带 `text`（已确认前缀，不会再变）+ `stash`（预测后缀，可能被改写）；完整预览 = `text + stash`
- **最终结果**：`...transcription.completed` 事件的 `transcript` 字段
- **session.update 形状**：
  ```json
  {
    "event_id": "event_123",
    "type": "session.update",
    "session": {
      "input_audio_format": "pcm",
      "sample_rate": 16000,
      "input_audio_transcription": { "language": "ja" },
      "turn_detection": { "type": "server_vad", "threshold": 0.2, "silence_duration_ms": 600 }
    }
  }
  ```
  服务端默认 `silence_duration_ms=800`、`threshold=0.2`；快速断句场景官方建议 400。本项目取 **600**（有延迟预算，宁可断得整一点）。
- **语言**：`zh / yue / en / ja / de / ko / ru / fr / pt / ar / it / es / hi / id / th / tr / uk / vi` 等
- **价格**：北京 0.00033 元/音频秒（≈1.19 元/小时）；新加坡 0.00066。⚠️ 落地前到控制台复核
- **RPM**：1200

⚠️ **必须在写代码前用 `scripts/asr-spike.py` 实测确认的点**：
1. `completed` 事件是否携带 `begin_time` / `end_time`（若有，直接用；若无，按 §5.3 的 VAD 事件推算）
2. 长会话空闲超时的实际行为与超时时长
3. 是否支持热词 / context 字段（bailian §4.4-4.5 描述的是 fun-asr 系；qwen3-asr-realtime 未必同名）
4. `dashscope.aliyuncs.com` 全局域名 vs 工作区域名 `{WorkspaceId}.cn-beijing.maas.aliyuncs.com` 哪个是当前账号可用的

### 2.2 翻译侧

- **通用 LLM（默认）**：OpenAI 兼容端点 `https://dashscope.aliyuncs.com/compatible-mode/v1`，`POST /chat/completions`，`Authorization: Bearer <key>`。模型 `qwen3.5-flash`，**必须关思考**（`extra_body: {"enable_thinking": false}`），否则延迟抖动不可控。
- **Qwen-MT（回退/极速）**：同一个 OpenAI 兼容端点，模型 `qwen-mt-flash` / `qwen-mt-plus` / `qwen-mt-turbo` / `qwen-mt-lite`，翻译参数走 `extra_body.translation_options`：
  ```json
  { "translation_options": {
      "source_lang": "Japanese", "target_lang": "Chinese",
      "terms": [{"source":"宝鐘マリン","target":"宝钟玛琳"}],
      "tm_list": [{"source":"...","target":"..."}],
      "domains": "English-only domain hint string" } }
  ```
  注意流式行为不一致：`qwen-mt-flash`/`lite` 增量返回，`qwen-mt-plus`/`turbo` 每个 chunk 返回累积内容。本项目字幕整句返回即可，**不用流式**。
- ⚠️ base_url 同样要确认全局域名 vs 工作区域名。

### 2.3 Provider 抽象的参考实现

| 项目 | 借鉴什么 | 不借鉴什么 |
|---|---|---|
| **LiveKit Agents**（`livekit-agents/livekit/agents/stt/stt.py`） | ✅ 这是本次抽象的主蓝本。`STTCapabilities`（`streaming` / `interim_results` / `keyterms` / `chat_context` 等能力位）、`SpeechEventType`（`START_OF_SPEECH` / `INTERIM_TRANSCRIPT` / `PREFLIGHT_TRANSCRIPT` / `FINAL_TRANSCRIPT` / `RECOGNITION_USAGE` / `END_OF_SPEECH`）、`RecognizeStream` 的 `push_frame` / `flush` / `end_input` + 异步迭代产出事件、内建重连重试、`FallbackAdapter` 供应商降级链 | 它的 `AudioFrame` / job / worker 体系；不要引入 `livekit-agents` 依赖 |
| **Pipecat** | 服务基类分层的思路 | 帧驱动 pipeline，过重 |
| **LiteLLM / Continue / LobeChat** | ✅ 配置记录形状：`{id, label, kind, model, baseUrl, apiKeyEnv, options}` + `active` + `fallback` 链；「OpenAI 兼容」作为一个通吃 kind | 不要引入 litellm 依赖（体积与依赖树远超本项目） |

**决策：不新增任何 Python 依赖。** 本项目当前只依赖 `aiohttp`。`aiohttp.ClientSession.ws_connect()` 做 ASR WebSocket，`session.post()` 做翻译 HTTP，全部够用。LiveKit 只借接口设计，不借代码。

---

## 3. Provider 抽象设计

### 3.1 原则

1. **两个 kind 才算抽象**。只实现一个 provider 的"抽象层"是假的。ASR 必须同时实现 `dashscope-qwen-realtime`（Realtime 形状）和 `dashscope-task-asr`（fun-asr / qwen-audio-3.0 的任务形状）；翻译必须同时实现 `openai-compatible` 和 `qwen-mt`。这两对的协议差异足够大，能把接口逼正确。
2. **能力位显式声明**，调用方按能力降级，不做 `isinstance` 分支。
3. **配置即数据**。加一个新供应商 = 新增一个 `providers/xxx.py` + `@register("kind")` + 在 JSON 里加一条记录。不改管线代码。
4. **密钥永不出服务端**，`/api/providers` 只返回脱敏视图。

### 3.2 目录

```
prototype/hls-companion/companion/providers/
├─ __init__.py          # register() 装饰器 + REGISTRY + create_asr()/create_translation()
├─ base.py              # 下面 §3.3 的全部数据类与 ABC
├─ config.py            # 加载/校验/合并/脱敏 providers.json；env 解析
├─ asr_qwen_realtime.py # kind: dashscope-qwen-realtime
├─ asr_dashscope_task.py# kind: dashscope-task-asr（fun-asr-realtime / qwen-audio-3.0 / paraformer-realtime-v2）
├─ mt_openai_compat.py  # kind: openai-compatible（Qwen 通用 LLM / DeepSeek / Ollama / 任意兼容端点）
├─ mt_qwen_mt.py        # kind: qwen-mt
└─ fallback.py          # FallbackChain：超时/错误按序降级 + 冷却
```

### 3.3 接口（`base.py`）

```python
from __future__ import annotations
import abc, dataclasses
from typing import AsyncIterator, Literal

# ---------- ASR ----------

@dataclasses.dataclass(frozen=True)
class ASRCapabilities:
    streaming: bool
    interim_results: bool      # 是否有中间结果
    stable_prefix: bool        # 是否区分「已确认前缀 / 待修订后缀」(text+stash)
    server_vad: bool           # 断句由服务端 VAD 完成
    word_timestamps: bool      # sentence 是否带 begin_time/end_time/words[]
    hotwords: bool
    context: bool
    languages: tuple[str, ...]
    sample_rates: tuple[int, ...]

ASREventType = Literal[
    "speech_started", "speech_stopped", "interim", "final", "usage", "error"
]

@dataclasses.dataclass
class ASREvent:
    type: ASREventType
    text: str = ""             # final 的整句 / interim 的已确认前缀
    stash: str = ""            # interim 的待修订后缀；不支持时为 ""
    begin_pcm: float | None = None   # 相对本 ASR 会话音频起点，秒；provider 能给就给
    end_pcm: float | None = None
    language: str | None = None
    message: str = ""          # error 用
    raw: dict | None = None    # 诊断用原始事件，不进业务逻辑

class ASRStream(abc.ABC):
    """一次 ASR 会话。实现方负责重连；重连期间必须丢弃音频而不是缓冲。"""
    @abc.abstractmethod
    async def push_pcm(self, chunk: bytes, pcm_offset: float) -> None:
        """chunk 为 16-bit LE 单声道；pcm_offset 是 chunk 起点相对流起点的秒数。"""
    @abc.abstractmethod
    async def flush(self) -> None: ...
    @abc.abstractmethod
    def __aiter__(self) -> AsyncIterator[ASREvent]: ...
    @abc.abstractmethod
    async def aclose(self) -> None: ...

class ASRProvider(abc.ABC):
    id: str
    label: str
    model: str
    price_per_second_cny: float | None
    @property
    @abc.abstractmethod
    def capabilities(self) -> ASRCapabilities: ...
    @abc.abstractmethod
    async def stream(self, *, language: str, hotwords: list[str],
                     context: list[str]) -> ASRStream: ...

# ---------- Translation ----------

@dataclasses.dataclass(frozen=True)
class TranslationCapabilities:
    rolling_context: bool      # 能吃历史对话
    glossary: bool
    domains: bool
    json_output: bool
    max_input_chars: int

@dataclasses.dataclass(frozen=True)
class StreamMeta:
    title: str | None
    channel: str | None
    domain: str | None
    source_lang: str           # ISO: "ja"
    target_lang: str           # ISO: "zh"

@dataclasses.dataclass
class TranslationRequest:
    source_text: str
    meta: StreamMeta
    history: list[tuple[str, str]]          # [(源文, 译文), ...] 最旧在前
    glossary: list[tuple[str, str]]
    deadline_monotonic: float | None = None # 超过就放弃，别把队列堵死

@dataclasses.dataclass
class TranslationResult:
    text: str
    provider_id: str
    latency_ms: int
    usage: dict | None = None

class TranslationProvider(abc.ABC):
    id: str
    label: str
    model: str
    @property
    @abc.abstractmethod
    def capabilities(self) -> TranslationCapabilities: ...
    @abc.abstractmethod
    async def translate(self, request: TranslationRequest) -> TranslationResult: ...
```

`FallbackChain` 同时实现 `TranslationProvider`（也可用于 ASR）：按顺序尝试，某个 provider 连续失败 N 次进入 60s 冷却，冷却期跳过。这是 LiveKit `FallbackAdapter` 的简化版。

### 3.4 配置文件

路径 `prototype/hls-companion/runtime/providers.json`（文件权限 0600，永不进版本控制，永不经 HTTP 返回原文）。

```json
{
  "version": 1,
  "asr": {
    "active": "bailian-qwen3-realtime",
    "providers": [
      {
        "id": "bailian-qwen3-realtime",
        "label": "百炼 Qwen3-ASR-Flash-Realtime",
        "kind": "dashscope-qwen-realtime",
        "model": "qwen3-asr-flash-realtime",
        "baseUrl": "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
        "apiKeyEnv": "DASHSCOPE_API_KEY",
        "pricePerSecondCny": 0.00033,
        "options": {
          "sampleRate": 16000,
          "turnDetection": { "type": "server_vad", "threshold": 0.2, "silenceDurationMs": 600 }
        }
      },
      {
        "id": "bailian-paraformer",
        "label": "百炼 Paraformer-Realtime-V2（成本基线）",
        "kind": "dashscope-task-asr",
        "model": "paraformer-realtime-v2",
        "baseUrl": "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
        "apiKeyEnv": "DASHSCOPE_API_KEY",
        "pricePerSecondCny": 0.00024,
        "options": { "sampleRate": 16000, "semanticPunctuationEnabled": false, "maxSentenceSilence": 800 }
      }
    ]
  },
  "translation": {
    "active": "bailian-qwen35-flash",
    "fallback": ["bailian-qwen-mt-flash"],
    "providers": [
      {
        "id": "bailian-qwen35-flash",
        "label": "百炼 Qwen3.5-Flash（高质量·带上下文）",
        "kind": "openai-compatible",
        "model": "qwen3.5-flash",
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "apiKeyEnv": "DASHSCOPE_API_KEY",
        "options": {
          "temperature": 0.3, "maxTokens": 256, "timeoutSeconds": 6,
          "enableThinking": false,
          "contextPairs": 6, "contextSeconds": 90
        }
      },
      {
        "id": "bailian-qwen-mt-flash",
        "label": "百炼 Qwen-MT-Flash（极速基线）",
        "kind": "qwen-mt",
        "model": "qwen-mt-flash",
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "apiKeyEnv": "DASHSCOPE_API_KEY",
        "options": { "timeoutSeconds": 4, "tmPairs": 4 }
      }
    ]
  },
  "subtitle": {
    "sourceLanguage": "ja",
    "targetLanguage": "zh",
    "anchor": "end",
    "holdSecondsMin": 1.2,
    "holdSecondsPerChar": 0.06,
    "manualOffsetSeconds": 0.0,
    "bilingual": true
  }
}
```

规则：

- `apiKeyEnv`（环境变量名）是推荐写法；也允许 `apiKey` 内联，但 `config.py` 加载后立刻把它移进内存，且任何序列化输出都替换为 `"***"`。
- 缺省文件不存在时，`config.py` 用内置默认（上面这份）生成一份，并把 `apiKeyEnv` 指向 `DASHSCOPE_API_KEY`。
- 校验：未知 `kind` → 启动即报错并列出可用 kind；`active` 不在 `providers` 里 → 报错。
- 提供 `providers.example.json` 进仓库，真实文件不进。

---

## 4. 数据流与新模块

```
yt-dlp 音频腿 stdout
   └─ _TcpPump._run  ── sendall ──► 封装 FFmpeg（既有路径，不受任何影响）
                     └─ tee(chunk) ──► 有界队列（丢最旧 + 计数）
                                          ▼
                       SubtitlePipeline（新，生命周期跟随 LiveSession）
                          ├─ ffmpeg -f mpegts -i pipe:0 -vn -ac 1 -ar 16000 -f s16le pipe:1
                          │     └─ PCM 16k mono，按 3200B（100ms）切块，维护 pcm_offset
                          ├─ ASRProvider.stream()  ── ASREvent ──►
                          │     ├─ speech_started/stopped → 记 VAD 边界的 pcm_offset
                          │     └─ final → SubtitleText.clean() → 丢弃/去重 → Cue(state="src")
                          ├─ TranslationWorker（单飞 + 背压降级）
                          │     └─ TranslationProvider.translate() → Cue.zh，state="done"
                          └─ CueStore（deque，保留最近 120s，按 id 可更新）
                                          ▼
                       GET /api/subtitles?since=<epoch>  ── 500ms 轮询 ──► 播放器 overlay
```

新文件：

| 文件 | 职责 |
|---|---|
| `companion/providers/*` | §3 |
| `companion/subtitle_pipeline.py` | `SubtitlePipeline`：ffmpeg 子进程、PCM 计时、ASR 事件循环、翻译 worker、生命周期与统计 |
| `companion/subtitle_text.py` | 纯函数：清洗、去重、切句、hold 时长计算。**纯函数才好测** |
| `companion/subtitle_store.py` | `CueStore`：有界、`since` 查询、按 id 更新 |
| `companion/context_manager.py` | 翻译上下文窗口 + 术语表 + prompt 构造（纯逻辑，可单测） |
| `scripts/asr-spike.py` | 离线 spike：喂一个 wav → 打印 ASR 事件时间线。**第一步就写它** |
| `runtime/providers.example.json` | 配置样例 |

修改文件：

| 文件 | 改动 |
|---|---|
| `companion/ytdlp_ingest.py` | `_TcpPump` 加 tee；`YtDlpLiveIngest` 暴露 `attach_audio_tee()` / `detach_audio_tee()` / tee 统计 |
| `companion/core.py` | `LiveSession` 持有可选 `SubtitlePipeline`，`start`/`stop`/`status` 联动；`DelayedPlaylistPublisher` 暴露首个分片的 `PROGRAM-DATE-TIME`（`pdt_epoch`） |
| `companion/server.py` | `/api/subtitles`、`/api/providers`（GET 脱敏 / POST 改 active+subtitle 段）、`/api/start` 接收字幕参数、`/api/status` 增字幕统计 |
| `web-player/index.html` `player.js` `style.css` | overlay 层、轮询、`playingDate` 映射、偏移滑块、双语开关、字号、provider 设置面板 |
| `package.json` | `test:hls-companion` 加入新测试 |
| `prototype/hls-companion/README.md` | 补数据流与合规说明（音频出本机到阿里云） |

---

## 5. 关键实现细节

### 5.1 tee（P0-1）

```python
class _TcpPump:
    def __init__(self, label: str, tee_maxlen: int = 64):
        ...
        self._tee: Callable[[bytes], None] | None = None
        self.tee_dropped = 0

    def set_tee(self, sink: Callable[[bytes], None] | None) -> None:
        self._tee = sink
```

在 `_run` 的 `conn.sendall(chunk)` **之后**调用：

```python
sink = self._tee
if sink is not None:
    try:
        sink(chunk)
    except Exception:          # 字幕可以死，播放不能卡
        self.tee_dropped += 1
```

铁律（对应 handoff §8 风险 1）：

- `sink` 必须自己是非阻塞的（内部放有界队列，满了 `popleft` 丢最旧并计数）
- `sink` 抛任何异常都在泵线程里被吞掉并计数，绝不上抛
- 只在字幕开启时 `set_tee`，关闭时 `set_tee(None)`
- 只挂音频腿：`self.pumps[1]`（双腿）或 `self.pumps[0]`（单腿 muxed）

跨线程投递：泵是普通线程，管线在 aiohttp 事件循环上。`sink` 里用
`loop.call_soon_threadsafe(queue.put_nowait, chunk)`，并在投递前检查 `queue.qsize()`，超阈值直接丢弃并计数（不要用 `call_soon_threadsafe` 去做丢弃逻辑，会把事件循环当锁用）。

### 5.2 TS → PCM

```
ffmpeg -hide_banner -loglevel error -nostdin
       -f mpegts -i pipe:0
       -vn -ac 1 -ar 16000 -f s16le pipe:1
```

用 `asyncio.create_subprocess_exec`，stdin/stdout 都是 asyncio 流。读端固定 3200 字节/次（= 100ms @16k/16bit/mono），每次读出后 `pcm_offset += 0.1`。

这是整条管线里**唯一新增的转码**，只有音频重采样，CPU 可忽略。不要在这里做别的事（不要顺手加 VAD、不要加响度归一）。

### 5.3 墙钟对齐（最关键，做砸了整个功能就没了）

**服务端产出 cue 的墙钟：**

```
cue.tEnd = pdt_epoch + end_pcm
cue.tStart = pdt_epoch + begin_pcm
```

其中 `pdt_epoch` 是"音频流媒体时间 0 对应的墙钟"：

1. 优先取 `private/live.m3u8` 里**第一个分片的 `#EXT-X-PROGRAM-DATE-TIME`**（FFmpeg 在写出首片时按系统时钟打的锚，之后的 PDT 是锚 + 累计媒体时长，是一条与媒体时间线性的墙钟）。这与我们的 `pcm_offset` 是同一种线性时钟，两边都不会随抖动漂移。
2. 首片 PDT 尚不可用时，退化为 worker ffmpeg 吐出第一个 PCM 字节时的 `time.time()`，拿到 PDT 后**一次性重锚**（只重锚一次，之后固定）。

`begin_pcm` / `end_pcm` 的来源，按 provider 能力分两条路：

- `capabilities.word_timestamps == True`（`dashscope-task-asr`）：直接用 `sentence.begin_time / end_time`（毫秒 → 秒）。
- `capabilities.server_vad == True` 且无时间戳（`qwen3-asr-flash-realtime` 很可能是这种，⚠️ spike 确认）：
  - 收到 `input_audio_buffer.speech_started` 时记 `pending_start = last_sent_pcm_offset`
  - 收到 `input_audio_buffer.speech_stopped` 时记 `pending_end = last_sent_pcm_offset - silence_duration_ms/1000`（VAD 是在静音够长之后才报停，要把静音扣回去）
  - 随后的 `...transcription.completed` 用这对值
  - 事件错位（没有 started 就来 completed）时，`begin_pcm = None`，`end_pcm = last_sent_pcm_offset`，并把 cue 标 `timingSource: "approx"`

**残余常量偏差**：两个 ffmpeg 的启动 probe 缓冲不同，`pdt_epoch` 与 worker 的 PCM 0 之间会有一个 0.5~2s 的**固定**偏差。处理方式：

- UI 提供 `字幕偏移` 滑块（−3.0 ~ +3.0s，步进 0.1），存 `localStorage`，用户一次校准长期有效——这是所有同类产品的标准做法，不要试图纯自动搞定
- `/api/status` 同时报告 `pdtEpoch`、`workerEpoch`、`epochDeltaSeconds`，方便诊断
- P2 再做自动标定（音频 PTS ↔ 输出 tfdt 会话偏移），第一版不做

**播放器侧的墙钟：**

```js
const playing = hls?.playingDate;                      // Date | null
const wallNow = playing ? playing.getTime() / 1000 : fallbackFromLevelDetails();
const t = wallNow + manualOffsetSeconds;
// 显示满足 cue.tEnd <= t <= cue.tEnd + cue.hold 的最后一条
```

`fallbackFromLevelDetails()`：取 `LEVEL_UPDATED` 缓存的 `details`，找到包含 `video.currentTime` 的 fragment，`frag.programDateTime/1000 + (currentTime - frag.start)`。

### 5.4 字幕清洗（`subtitle_text.py`，纯函数）

按序执行，全部要有单测：

1. `unicodedata.normalize("NFKC", text)` —— 统一全半角
2. 去首尾空白（含全角空格 `　`）、折叠连续空白
3. 删除 ASR 特殊标记：`<|...|>`、`[BGM]` / `[Music]` / `(音楽)` 这类噪声标签（保留 `(笑)` 这类有语义的）
4. 折叠重复标点：`。。。` → `…`，`、、` → `、`，`!!!` → `!`
5. **丢弃**：清洗后长度 < 2；或全部是标点/符号；或只剩单个填充词（`えー` `あの` `うーん` `はい` 单独成句时）
6. **去重**：与上一条 final 完全相同 → 丢弃；是上一条的前缀且长度差 < 3 → 丢弃（ASR 偶发重发）
7. **切分**：清洗后 > 80 字符时，按 `。！？!?` 优先、其次 `、,` 切成 ≤ 80 字符的片段，每片各自成 cue，`end_pcm` 按字符比例线性内插（粗略即可）
8. 计算 `hold = clamp(holdSecondsMin, holdSecondsPerChar * len(zh or src), 8.0)`

另外在管线层做**异常窗口抑制**（handoff §8 风险 3）：若 `ingest.snapshot()["sourceError"]` 非空，或最近 3 秒的 `log_tail` 里出现 `skipping` / `expired from playlists`，把落在该窗口内的 final 直接丢弃并计数 `suppressedByIngestError`。

### 5.5 翻译上下文策略（用户明确要求的性能/速度权衡点）

**上下文构造**（`context_manager.py`）：

- **Layer A 固定元信息**：直播标题、频道、领域、源/目标语言。会话开始时从 `/api/probe` 的 `info` 拿一次。放 **system prompt**。
- **Layer B 术语表**：初始从标题/频道名抽取 + 用户自定义；上限 30 条。放 **system prompt**。
- **Layer C 滚动上下文**：最近 `contextPairs`（默认 6）对 (源文, 译文)，且不超过 `contextSeconds`（默认 90s）。放 **user message 的 HISTORY 块**。
- Layer D（长期摘要）**第一版不做**。

**为什么这样切**：system prompt 在整场直播里逐字不变 → 命中 DashScope 的前缀缓存；变化的只有 HISTORY + CURRENT 尾部。这是"质量 vs 延迟/成本"的最佳切点。不要把历史塞成多轮 `messages`，那会让缓存前缀每句都变。

**Prompt 骨架**（遵循 bailian §17）：

```
system:
你是直播字幕翻译器。把 CURRENT 从{源语}译成{目标语}。
规则：
1. 只输出 CURRENT 的译文，不要输出解释、不要重复 HISTORY。
2. HISTORY 只用于理解指代、省略主语和话题，不要翻译它。
3. 译文要像直播字幕：简洁、口语、可一眼读完。
4. 不要补全说话人没说完的内容，不要添加未表达的事实。
5. 人名/专有名词严格遵循术语表。
6. 只输出译文本身，不加引号、不加前缀。
直播信息：{title} / {channel} / 领域：{domain}
术语表：
{term} => {target}
...

user:
HISTORY:
{src1} -> {zh1}
...
CURRENT:
{src}
```

**并发与背压（性能权衡的执行机制）**：

- 默认 **串行**：一次只有一个翻译在飞。短句 `qwen3.5-flash` 非思考模式约 0.4~1.2s，2.5~5s 的余量足够，串行能保证上下文严格有序、译名一致。
- 队列积压 > 2 句 → **降级第一档**：`contextPairs` 临时降到 2。
- 积压 > 4 句 → **降级第二档**：切 `fallback` 链的下一个 provider（`qwen-mt-flash`，无历史上下文，更快）。
- 积压 > 8 句 → **丢弃最旧的未翻译 cue**，只保留原文（cue 保持 `state:"src"`，播放器仍会显示日文），并计数上报。
- 每个请求带 `deadline`：超过 `timeoutSeconds` 直接放弃，cue 停在 `state:"src"`。
- 积压恢复到 0 且稳定 30s → 逐档恢复。

这套降级是本项目"用延迟预算换质量、但绝不让字幕停摆"的具体落点，请原样实现，别简化成"无脑并发 3 个"。

### 5.6 Cue 数据模型与 API

```python
@dataclasses.dataclass
class Cue:
    id: int
    t_start: float | None    # epoch seconds
    t_end: float             # epoch seconds，显示锚点
    hold: float
    src: str
    zh: str | None
    state: Literal["src", "translating", "done", "failed"]
    lang: str
    timing_source: Literal["asr", "vad", "approx"]
    revision: int            # 每次更新 +1，供客户端判断是否要替换
```

`GET /api/subtitles?since=<float epoch>&sinceRevision=<int>`

```json
{
  "now": 1787996418.77,
  "pdtEpoch": 1787996380.12,
  "cues": [ { "id": 41, "tStart": 1787996410.3, "tEnd": 1787996414.1,
              "hold": 2.4, "src": "…", "zh": "…", "state": "done",
              "lang": "ja", "timingSource": "vad", "revision": 2 } ],
  "stats": { "asrSeconds": 612.4, "estimatedCostCny": 0.2021,
             "teeDropped": 0, "suppressedByIngestError": 1,
             "translationBacklog": 0, "degradeLevel": 0,
             "asrProviderId": "bailian-qwen3-realtime",
             "translationProviderId": "bailian-qwen35-flash",
             "lastError": null }
}
```

- 返回 `tEnd > since` **或** `revision > sinceRevision` 的 cue（译文回填要能推给已经取过的客户端）
- `CueStore` 保留最近 120s，`deque` 有界，同时按 id 建索引供更新
- 轮询 500ms；本机 127.0.0.1，不上 WebSocket

`GET /api/providers` → 脱敏配置（`apiKey` 一律 `"***"`，另给 `apiKeyConfigured: true/false`）
`POST /api/providers` → 只允许改 `asr.active`、`translation.active`、`translation.fallback`、整个 `subtitle` 段。**不接受**通过 HTTP 写入 `apiKey`（密钥只走环境变量或用户手改文件）。改完写回 `providers.json`（原子写：tmp + `os.replace`）。

### 5.7 生命周期

- `/api/start` body 增加：
  ```json
  { "subtitles": { "enabled": true, "sourceLanguage": "ja", "targetLanguage": "zh",
                   "asrProviderId": null, "translationProviderId": null } }
  ```
  （`null` = 用配置里的 `active`）
- `handle_start`：`session.start(...)` 成功后再启动 `SubtitlePipeline` 并 `attach_audio_tee`。字幕启动失败**不能让播放失败**——记 `subtitles.lastError`，播放照常。
- `handle_stop` / `LiveSession.stop` / `app.on_cleanup`：先 `detach_audio_tee`，再关 ASR ws、杀 worker ffmpeg、取消翻译任务。
- ASR 重连：指数退避 0.5s → 8s。**重连期间照常从队列取音频并推进 `pcm_offset`，但直接丢弃**（绝不缓冲、绝不补时间戳）。重连成功后按当前 `pcm_offset` 继续，时间轴自然对齐。

### 5.8 播放器 overlay

`index.html` 在 `.player-stage` 内加：

```html
<div id="subtitleLayer" class="subtitle-layer" data-mode="bilingual" aria-live="off">
  <div class="subtitle-zh"></div>
  <div class="subtitle-src"></div>
</div>
```

- 绝对定位在 stage 底部，`pointer-events: none`（不能挡 `<video controls>`），`bottom` 留出原生控制条空间
- 描边（`text-shadow` 四向 / `paint-order: stroke fill`）保证任何画面上可读
- 渲染节拍 100ms（`setInterval`，不是 rAF——rAF 在标签页隐藏时会停）
- `state:"src"` 时中文行显示占位（半透明"翻译中…"）或直接只显示日文行；`state:"done"` 原地替换
- 控件：字幕开关、双语/仅中文/仅原文、字号（3 档）、偏移滑块。全部存 `localStorage`
- Provider 设置面板：从 `/api/providers` 拉列表，两个下拉（ASR / 翻译）+ 目标语言下拉；改动 `POST` 回去；提示"下次启动播放生效"

`style.css` 相应新增；`tests/test_web_assets.js` 里加断言（overlay 元素存在、`pointer-events:none`）。

---

## 6. 实施顺序与验收

按顺序做，每步都能独立验收。**不要跳到 P1 之前先把 P0-0 的 spike 跑通**——外部 API 的真实事件形状是整个设计的地基。

| # | 内容 | 验收 |
|---|---|---|
| **P0-0** | `scripts/asr-spike.py`：读一段本地 ja 音频（或用 ffmpeg 从录制的 ts 生成 wav）→ 连 `qwen3-asr-flash-realtime` → 打印全部服务端事件带本地时间戳 | 打印出完整事件序列，回答 §2.1 的 4 个 ⚠️ 问题，把答案写回本文 §2.1 |
| **P0-1** | `providers/` 骨架 + `config.py` + 两个 ASR kind + 两个翻译 kind + `FallbackChain` | 单测：配置加载/校验/脱敏；kind 注册表；`FallbackChain` 在首个 provider 抛错时降级 |
| **P0-2** | `_TcpPump` tee | 单测：订阅者收到与 socket 相同的字节序列；队列满时丢最旧且计数；sink 抛异常不影响泵 |
| **P0-3** | `subtitle_text.py` + `subtitle_store.py` + `context_manager.py`（纯逻辑） | 单测覆盖 §5.4 的 8 条规则、`since`/`revision` 查询、上下文窗口裁剪与 prompt 构造 |
| **P0-4** | `subtitle_pipeline.py`：tee → ffmpeg → ASR → 原文 cue（**先不翻译**） | 真实直播 5 分钟：`/api/subtitles` 里日文 cue 连续；进程 RSS 不增长；`teeDropped == 0` |
| **P0-5** | `/api/subtitles` + overlay + `playingDate` 映射 + 偏移滑块 | 浏览器实测：**日文**字幕与口型误差 < 1.5s（允许滑块一次校准） |
| **P1-1** | 翻译 worker + 上下文 + 降级阶梯 | 同屏中日双语目测通顺；人为堵塞（把 timeout 调到 0.2s）验证降级与恢复 |
| **P1-2** | 双语 overlay UI + provider 设置面板 + `/api/providers` | 切换 provider 后重启生效；密钥在任何响应里都是 `***` |
| **P1-3** | `/api/status` 成本与统计；README 数据流/合规说明 | 累计秒数与费用随时间增长且量级正确 |
| **P2** | interim（`text`+`stash`）占位字幕、stable-prefix 提前翻译、自动时间标定、ASR A/B、长期摘要 Layer D | — |

**明确不做**（沿用 bailian handoff §1.2）：声纹分离、OCR、TTS 配音、移动端、DRM 平台。

---

## 7. 风险清单（落地时逐条对照）

1. **tee 阻塞播放** —— 最高优先级。队列有界 + 丢最旧 + 吞异常 + 计数上报，泵线程里不得有任何可能阻塞的调用。
2. **长句锚点** —— 见 §0 修正 1。用 `tEnd`。评审代码时确认没有人"顺手"改回 `tStart`。
3. **ASR 重连期间补时间戳** —— 禁止。丢弃即可，`pcm_offset` 照常推进。
4. **采集断裂期的乱句** —— §5.4 的异常窗口抑制必须实现，否则跳片时会出现整段幻觉字幕。
5. **密钥泄漏** —— `providers.json` 不进版本控制；`/api/providers` 脱敏；不写日志；不进 ffmpeg/yt-dlp 命令行。web-player 与 extension 永远不接触 key。
6. **LLM 添加没说过的内容**（bailian R5）—— prompt 里已有约束；上线前用 10 条已知短句做人工抽检。
7. **成本失控** —— ASR 按秒计费，1 小时约 1.2 元。`/api/status` 必须暴露累计秒数与费用，UI 上要能看见。
8. **合规** —— 音频离开本机进入阿里云，与现有"Cookie 不出本机"口径不同，README 必须显式说明这一条数据流。
9. **测试基建已知问题** —— `tests/test_control_ipc.py` 在服务器进程运行时因命名管道冲突失败（环境问题，非代码问题）。新增测试不要复用命名管道。

---

## 8. 落地会话的第一条命令

```bash
# 1. 确认环境
python -c "import aiohttp, sys; print(sys.version, aiohttp.__version__)"
$env:DASHSCOPE_API_KEY = "<你的 key>"   # PowerShell

# 2. 先跑 spike，回答 §2.1 的 4 个 ⚠️，把答案写回本文
python prototype/hls-companion/scripts/asr-spike.py --audio <某段日语 wav>

# 3. 然后才开始写 providers/
```

不要在 spike 之前动 `ytdlp_ingest.py`。
