# Handoff：可调延迟的浏览器直播 AI 同传插件

> 目标：交给后续 Agent 继续技术研讨、原型开发与落地。  
> 日期：2026-08-29  
> 当前目标平台：Microsoft Edge / Chromium，优先 YouTube Live，后续扩展到任意网页直播/视频。  
> 核心供应商：阿里云百炼（DashScope / Model Studio）。  
> 核心产品思想：**不是极限追求“最低字幕延迟”，而是主动让观众播放器落后直播 Live Edge 若干秒，把这段延迟当作 ASR、上下文理解和高质量翻译的计算预算，最终让“观众看到的画面”和“高质量译文”同步。**

---

## 0. 一句话定义

这是一个 **viewer-side delayed live translation** 浏览器插件：

1. 从直播最前沿（Live Edge）持续抓取音频；
2. 通过百炼实时 ASR WebSocket 持续获得源语言字幕；
3. 利用前文累计上下文、术语表、频道信息等做更高质量文本翻译；
4. 用户真正观看的视频主动落后 Live Edge `D` 秒（例如 3/5/8/10/15/20 秒可选）；
5. 翻译好的字幕按照**源媒体时间戳**排队，等延迟播放器播放到对应时刻时再显示；
6. 初期至少支持中文、英语、日语、韩语之间任意方向互译。

核心不是：

> “语音识别有 2 秒延迟，然后字幕永远比画面慢 2 秒。”

而是：

> “直播整体晚 10 秒，但在这个人为延迟后的观看时间轴里，画面、声音和中文字幕尽量同步。”

---

# 1. 产品需求与边界

## 1.1 第一目标场景

最初需求来自：

- 用户观看日本 YouTube Live / VTuber / 游戏直播；
- 原直播没有中文字幕；
- 用户愿意牺牲 5～15 秒“绝对实时性”；
- 希望获得比普通极低延迟机器同传更稳定、更自然、更有上下文的中文翻译；
- 后续泛化到中 / 英 / 日 / 韩四种源语言与目标语言的互译；
- 延迟时间由用户选择，最终可升级为自动推荐 / 自适应。

## 1.2 第一版不追求

MVP 暂时不要把范围扩展到：

- 自己解析 YouTube HLS/DASH；
- 自己实现完整视频环形缓冲；
- 自己训练 ASR；
- 自己训练同传模型；
- 多说话人声纹分离；
- OCR 读画面文字；
- 语音配音 / TTS 同传；
- 手机端；
- Twitch/Netflix/DRM 全覆盖。

这些都可以后续扩展。

---

# 2. 本次研究后的核心结论

## 2.1 可行性：高

所有关键技术构件都已经存在：

- Chromium `chrome.tabCapture` 可捕获标签页音频；
- Chrome/Edge MV3 可使用 Offscreen Document 在后台持续处理媒体流；
- HTML5 `video.currentTime` / `seekable` 可以对启用 DVR 的直播进行回退；
- YouTube Live DVR 官方支持暂停、回退和继续播放（主播可关闭 DVR）；
- 百炼提供真正实时的 WebSocket ASR：音频流式输入，文本流式输出；
- 百炼 ASR 支持中、英、日、韩；
- 百炼能返回中间/最终识别结果及时间戳；
- 现有开源项目已证明 `tabCapture → WebSocket → ASR → 翻译 → Overlay` 可工作；
- 同时翻译研究已长期研究 latency-quality tradeoff、wait-k、re-translation、stable prefix。

**真正需要我们自己实现的创新层，主要是“媒体延迟与翻译时间轴同步层”。**

---

# 3. 与现有产品/项目的关系

## 3.1 已存在的“实时翻译字幕”产品

### Immersive Translate Live

公开宣传：

- YouTube Live 双语字幕；
- 100+ 语言；
- 宣传 `<1s` 翻译延迟；
- 目标是尽量实时。

来源：  
https://livetranslate.immersivetranslate.com/

**与本项目差异：**没有发现其公开说明采用“Live Edge 音频 + 单独延迟观众视频”的双时间线。

---

### Whisperr

公开宣传：

- YouTube Live / 任意直播实时翻译；
- 标签页/系统音频捕获；
- 100+ 语言；
- 宣传约 `0.2s` 平均延迟。

来源：  
https://whisperr.co/use-cases/translate-youtube-live/  
https://whisperr.co/use-cases/translate-live-stream/

**与本项目差异：**核心卖点是低延迟，而非主动制造 5～20 秒媒体缓冲以换翻译质量。

---

### Live Translate（Chrome Web Store，kjj...）

公开功能：

- 当前标签页音频直接流式发往 Google 翻译 API；
- 70+ 目标语言；
- “Tunable latency”；
- buffer 可在低延迟与平滑播放之间调节。

来源：  
https://chromewebstore.google.com/detail/live-translate/kjjelnicbdofofjkdmpnpijbbfbekmgb

**重要判断：**它确实证明“可调 latency/buffer”是实际产品思路，但公开描述更像翻译输出/网络/语音播放缓冲；目前没有证据证明它把**原视频本身**延迟，而 ASR 仍运行在 Live Edge。

---

### Live Translate（OpenAI Whisper + chunk）

另一个扩展公开说明：

- tab audio；
- Whisper；
- AI 翻译；
- chunk duration 可选 1～10 秒；
- 原文+译文 overlay。

来源：  
https://chromewebstore.google.com/detail/live-translate/ebaamfaihhpmhjimhpnhiipdfmhhendj

**价值：**说明简单产品确实会采用“固定 chunk → ASR → 翻译”，但这正是本项目希望避免成为主方案的结构，因为 chunk 本身会吃掉大量延迟预算。

---

### Voxis

公开说明：

- 浏览器标签页音频；
- 实时语音翻译与字幕；
- 几秒解释器式延迟；
- Gemini Live API。

来源：  
https://chromewebstore.google.com/detail/voxis-%E2%80%94-live-translate/eaoplhkoomnlgfhcjeccgkhnkodkfbjn

仍未发现其公开说明采用“视频人为回退”。

---

### Voco

Voco 官网明确写：

- Live Streaming；
- “Real-time translation with just a 10-second delay”。

来源：  
https://www.voco.studio/

其 Church 产品则是典型主播/制作方管线：

- 获取演讲音频；
- 生成字幕/翻译；
- 通过 OBS/vMix/Streamlabs Browser Source 叠到**输出直播**。

来源：  
https://voco.church/livestream-captions

**与本项目的关键区别：**

Voco 更接近：

```text
主播音频 → AI → 字幕 → OBS → 整个输出直播
```

本项目是：

```text
已经存在的 YouTube Live
       ├─ Live Edge 音频 → AI
       └─ Viewer 播放器 → 主动回退 D 秒
                         → 在过去时间轴显示已经算好的字幕
```

---

## 3.2 已存在的开源项目

### Kami Subs

非常重要的参考实现。

架构：

```text
Chrome Extension
  • tabCapture
  • chunk + resample
  • subtitle overlay
       │
       │ PCM 16k mono / WebSocket
       ▼
Python Backend
  • faster-whisper
  • translation
  • FastAPI WebSocket
```

还使用 Native Messaging 自动启动后端。

来源：  
https://github.com/MohammdKopa/kami-subs

**可以复用/参考的模块：**

- MV3 权限结构；
- tabCapture；
- PCM 重采样；
- WebSocket；
- overlay；
- Native Messaging；
- 本地 companion process 组织方式。

**我们新增：**

- LiveEdgeTracker；
- DelayController；
- TimelineMapper；
- SubtitleScheduler；
- ContextualTranslator；
- 延迟 deadline 管理。

---

### Youtube-Live-Stream--Local-Whisper-Translation

面向 VTuber 的真实 Chrome 扩展项目：

- Local Whisper；
- Ollama；
- YouTube Live；
- 作者称翻译延迟约 1～1.5 秒。

来源：  
https://github.com/CookieProduction/Youtube-Live-Stream--Local-Whisper-Translation

相关社区讨论：  
https://www.reddit.com/r/VirtualYoutubers/comments/1tinl6c/google_chrome_live_subtitle_translation_for/

价值：已经证明“VTuber YouTube Live → 浏览器 → ASR → LLM 翻译”这一场景有人实际使用。

---

### WhisperStreaming

把原本偏离线的 Whisper 做成流式系统：

- LocalAgreement；
- self-adaptive latency；
- 未切分 long-form speech；
- 论文报告约 3.3 秒延迟。

来源：  
https://github.com/ufal/whisper_streaming  
https://aclanthology.org/2023.ijcnlp-demo.3/

价值：解释为什么“每 1 秒独立切一段 Whisper”不是理想流式方案，以及 stable prefix/local agreement 的意义。

---

### SimulStreaming

WhisperStreaming 后续：

- streaming ASR；
- LLM simultaneous translation；
- AlignAtt / LocalAgreement；
- 支持领域术语/RAG；
- 面向 long-form speech；
- 公开称相较前代约 5 倍快。

来源：  
https://github.com/ufal/SimulStreaming

价值：说明“ASR stable partial + LLM translation + context”的级联架构是成熟研究路线。

---

### WhisperLiveKit

提供：

```text
ws://localhost:8000/asr
```

并支持：

- native WebSocket；
- Deepgram-compatible WebSocket；
- incremental/diff protocol；
- source language；
- target language；
- 多种 streaming policy。

来源：  
https://github.com/QuentinFuxa/WhisperLiveKit

价值：如果百炼路线未来需要本地替代，可直接作为本地 ASR/翻译 backend 备选。

---

## 3.3 研究层面的依据

### Re-translation versus Streaming for Simultaneous Translation

研究指出：

- live captioning 允许对尚未稳定的翻译进行有限修订；
- re-translation 可以非常有竞争力；
- latency 与 quality 是需要共同优化的指标。

来源：  
https://aclanthology.org/2020.iwslt-1.27/

### wait-k / simultaneous translation

IWSLT 等大量工作围绕：

- 先等待一定源语言信息；
- 再开始输出；
- 在 latency 和 quality 间做 trade-off。

例：  
https://aclanthology.org/2020.iwslt-1.5/  
https://aclanthology.org/2020.iwslt-1.29/

**本项目的区别：**
传统 simultaneous translation 主要优化“语言 buffer”；我们额外引入一个**媒体播放 buffer**，从产品层面给翻译模型更大的观察窗口。

---

# 4. 百炼实时 ASR 调研

官方总览：  
https://help.aliyun.com/zh/model-studio/asr-model

官方明确：

> 实时语音识别：WebSocket，音频流式输入，文本流式输出；适用于实时字幕、语音助手、会议转写。

当前官方推荐实时 ASR：

```text
qwen-audio-3.0-asr-flash-streaming
```

---

## 4.1 首选：qwen-audio-3.0-asr-flash-streaming

模型页：  
https://help.aliyun.com/zh/model-studio/qwen-audio-3-0-asr-flash-streaming

特性：

- WebSocket；
- Binary 音频流；
- 实时“边说边出字”；
- 音频时长不限；
- 支持 Context；
- 支持热词；
- 支持多语种；
- 支持中 / 英 / 日 / 韩；
- 官方把它列为实时 ASR 首选。

当前官方价格（2026-08-29 检索）：

- 北京：0.00033 元 / 音频秒；
- 新加坡：0.00066 元 / 音频秒。

注意：价格会变，Agent 落地前再次查控制台。

支持语言至少包括：

```text
zh / en / ja / ko
```

并远超四种语言。

---

## 4.2 Qwen-Audio / Fun-ASR 的实时事件

客户端/参数文档：  
https://help.aliyun.com/zh/model-studio/fun-asr-client-events

服务端事件：  
https://help.aliyun.com/zh/model-studio/fun-asr-server-events

Python SDK：  
https://help.aliyun.com/zh/model-studio/fun-asr-realtime-python-sdk

服务端返回：

```json
{
  "sentence": {
    "begin_time": 170,
    "end_time": 920,
    "text": "...",
    "sentence_begin": true,
    "sentence_end": true,
    "sentence_id": 1,
    "words": [
      {
        "begin_time": 170,
        "end_time": 295,
        "text": "...",
        "punctuation": "..."
      }
    ]
  }
}
```

关键字段：

- `begin_time`：句开始，相对 ASR 音频流起点，ms；
- `end_time`：句结束，ms；
- `text`；
- `sentence_begin`；
- `sentence_end=false`：中间结果；
- `sentence_end=true`：最终结果；
- `words[]`：字/词级时间戳。

这对 TimelineMapper 极其重要。

---

## 4.3 断句策略

参数：

```text
semantic_punctuation_enabled
```

- `false`（默认）：VAD 断句，更低延迟；
- `true`：语义断句，更高断句质量。

VAD 参数：

```text
max_sentence_silence
```

- 默认：1300ms；
- 范围：200～6000ms。

MVP 建议：

```text
semantic_punctuation_enabled = false
max_sentence_silence = 600~1000ms
```

然后做 A/B。

原因：本项目已有媒体延迟预算，可以比普通交互产品稍微等久一点，但不应等到完整长句 10～15 秒后才翻译。

---

## 4.4 Context 增强

Qwen-Audio-3.0-ASR-Flash-Streaming 支持识别上下文。

官方约束（当前文档）：

- `input_text` 上下文与 `text` 上下文各最多 5 条；
- 超出保留最近 5 条；
- 每轮上下文文本总长度不超过 400 字符。

来源：  
https://help.aliyun.com/zh/model-studio/fun-asr-client-events

ASR Context 应用于：

- 主播名；
- 频道名；
- 直播标题；
- 游戏名；
- VTuber 名；
- 人名；
- 专有名词；
- 最近识别稳定文本；
- 领域背景。

**不要把整个几分钟历史 transcript 都塞进 ASR Context。**
ASR Context 的目的主要是“识别正确”，不是负责完整 discourse-level 翻译。

---

## 4.5 热词

Qwen-Audio / Fun-ASR 支持热词能力。

典型：

```text
宝鐘マリン
兎田ぺこら
ホロライブ
モンスターハンター
Nintendo Switch 2
Unreal Engine
```

后续可以自动从以下信息生成热词：

- YouTube 视频标题；
- 频道名；
- description；
- 用户自定义词典；
- 最近高频实体；
- 游戏/作品词库。

---

## 4.6 qwen3-asr-flash-realtime：重要 A/B 模型

模型页：  
https://help.aliyun.com/zh/model-studio/qwen3-asr-flash-realtime

实时服务端事件：  
https://help.aliyun.com/zh/model-studio/qwen-asr-realtime-server-events

支持：

- WebSocket；
- 中英日韩等；
- 无限时长；
- 自动语种识别；
- 更重要的是 `text + stash`。

事件：

```json
{
  "type": "conversation.item.input_audio_transcription.text",
  "language": "ja",
  "text": "...",
  "stash": "..."
}
```

语义：

```text
text
= 已确认、后续不会改变的文本前缀

stash
= 临时预测、未来可能修订的后缀
```

这与 streaming ASR 中的：

```text
stable prefix + unstable suffix
```

高度一致。

**价值：**
如果我们希望在主播长时间不停顿时仍能提前翻译稳定前缀，`qwen3-asr-flash-realtime` 的 `text/stash` 可能比纯 `sentence_end` 模式更适合。

**缺点：**
官方当前把 Qwen-Audio-3.0 Streaming 作为实时 ASR 首选，而且后者的 Context/热词增强能力更适合直播专有名词。

因此：

> MVP 默认 Qwen-Audio 3.0；必须做 qwen3-asr-flash-realtime A/B。

---

## 4.7 Fun-ASR-Realtime

模型页：  
https://help.aliyun.com/zh/model-studio/fun-asr-realtime

同样支持实时 WebSocket。

适合作为第三个 benchmark。

---

## 4.8 Paraformer-Realtime-V2

模型页：  
https://help.aliyun.com/zh/model-studio/paraformer-realtime-v2

特性：

- 实时；
- 中/英/日/韩；
- 热词；
- 支持任意采样率；
- 北京价格当前为 0.00024 元/秒。

作为成本敏感基线即可，不建议作为默认首选。

---

# 5. 百炼 LiveTranslate：为什么不是主方案，但一定要做对照

模型：

```text
qwen3.5-livetranslate-flash-realtime
```

模型页：  
https://help.aliyun.com/zh/model-studio/qwen3-6

实时 API：  
https://help.aliyun.com/zh/model-studio/qwen3-5-livetranslate-flash-realtime

服务端事件：  
https://help.aliyun.com/zh/model-studio/live-translator-server-events

官方把它定位为：

- 实时音视频同传；
- WebSocket；
- 支持 60 种输入语言；
- 文本/语音输出；
- 可同时输出源语言 ASR。

翻译事件也提供：

```text
text  = 已确认翻译
stash = 临时翻译
```

它实际上能够做到：

```text
日语音频
  ↓
LiveTranslate
  ├─ 日文 ASR（可选）
  └─ 中文实时翻译
```

为什么仍不作为本项目主链路：

本项目核心价值是：

> 利用额外 5～20 秒 budget + 更长历史上下文，做“比普通同传更精细”的翻译。

端到端 LiveTranslate 更偏向最低延迟同传。其内部上下文策略不如我们自己拆分 ASR + Translation LLM 可控。

因此它应该作为：

- benchmark；
- 极速模式；
- fallback；
- 产品未来“零配置实时模式”。

而不是第一版高质量模式的唯一核心。

---

# 6. 翻译模型设计

## 6.1 不要让 ASR 直接承担翻译上下文

架构必须分两层：

```text
ASR Context
→ 提升“听对了什么”

Translation Context
→ 提升“这句话到底该怎么翻”
```

### ASR Context 关注

- 人名；
- 专有名词；
- 游戏/作品；
- 前一两句；
- 频道主题。

### Translation Context 关注

- 前面几十秒～几分钟说了什么；
- 日语省略主语；
- “これ/それ/あれ/あの人”指代；
- 角色/人物关系；
- 前文已经确定的译名；
- 说话语气；
- 术语一致性；
- 当前话题；
- 直播标题/频道背景。

---

## 6.2 翻译模型候选 A：通用 Qwen LLM（推荐高质量模式）

例如：

```text
qwen3.5-flash
qwen3.5-plus
```

当前官方模型页：

https://help.aliyun.com/zh/model-studio/qwen3-5-flash  
https://help.aliyun.com/zh/model-studio/qwen3-5-plus

优势：

- 长上下文；
- 可以真正传递 rolling conversation context；
- 可以输出 JSON；
- 可以做“只翻当前 segment，但参考历史”；
- 能处理口语、省略、上下文、梗；
- 10 秒媒体延迟允许使用比传统字幕更强的模型。

MVP 推荐先使用：

```text
qwen3.5-flash
```

非思考模式。

不要启用长思考，否则 latency 波动不必要。

后续可 A/B `qwen3.5-plus`。

---

## 6.3 翻译模型候选 B：Qwen-MT

文档：  
https://help.aliyun.com/zh/model-studio/machine-translation/  
https://help.aliyun.com/zh/model-studio/qwen-mt-api  
https://help.aliyun.com/zh/model-studio/qwen-mt-plus

`qwen-mt-plus/flash/turbo` 支持 92 语言，包含：

```text
zh / en / ja / ko
```

并支持：

- `terms`：术语干预；
- `tm_list`：翻译记忆；
- `domains`：领域提示。

这非常适合字幕一致性。

但当前 API 主要围绕单个 User Message，最大输入 8192 token，和通用 Chat LLM 的长对话 rolling context 不一样。

因此建议：

### 高质量模式

```text
Qwen general LLM + rolling context
```

### 专业 MT 基线

```text
Qwen-MT + terms + tm_list + domains
```

实测哪个更好再决定默认值。

---

# 7. 最终推荐架构

## 7.1 核心：双播放器 / 双时间线

MVP 使用两个 YouTube 标签页，而不是自己解析视频流。

```text
                         YouTube Live

                ┌────────────┴─────────────┐
                │                          │
                ▼                          ▼
       Source Tab A                  Viewer Tab B
       永远贴近 Live Edge            用户真正观看
       不给用户听                     落后 D 秒
                │                          │
          tabCapture                      │
                │                          │
                ▼                          │
       AudioWorklet / PCM                 │
                │                          │
                ▼                          │
        Local Companion                   │
                │                          │
                ▼                          │
      Bailian Streaming ASR               │
                │                          │
                ▼                          │
      Contextual Translator               │
                │                          │
                ▼                          │
        Subtitle Timeline ────────────────→│
                                           ▼
                                     Subtitle Overlay
```

---

## 7.2 为什么不能只延迟一个标签页

错误方案：

```text
YouTube video
   ↓
往回拖 10 秒
   ↓
tabCapture
   ↓
ASR
```

这样 ASR 听到的同样是 10 秒前的声音，没有获得任何提前量。

必须实现：

```text
ASR 处理 Live Edge = T
用户看到 Viewer = T - D
```

---

# 8. 浏览器实现细节

## 8.1 Chromium API

### chrome.tabCapture

官方：  
https://developer.chrome.com/docs/extensions/reference/api/tabCapture

关键事实：

- 需要 `"tabCapture"` 权限；
- 必须由用户主动触发扩展（例如点击 action）；
- `getMediaStreamId()` 可获取目标标签页流 ID；
- Chrome 116+ 可在 service worker 取得 ID 后由 offscreen document 消费；
- 获取 tab MediaStream 后，原标签页音频默认不再播放给用户。

这对 Source Tab A 很方便：

> A 继续直播、继续产生音频供 ASR，但用户不用听到 A。

---

## 8.2 Offscreen Document

官方：  
https://developer.chrome.com/docs/extensions/reference/api/offscreen

MV3 Service Worker 没有完整 DOM / Web Audio 生命周期，因此建议：

```text
Service Worker
   ↓
Offscreen Document
   ↓
getUserMedia(tab stream id)
   ↓
AudioContext
   ↓
AudioWorklet
```

Chrome 109+ 支持 Offscreen API。

---

## 8.3 推荐 Manifest 权限

初步：

```json
{
  "permissions": [
    "tabCapture",
    "offscreen",
    "activeTab",
    "scripting",
    "storage",
    "tabs"
  ],
  "host_permissions": [
    "https://www.youtube.com/*"
  ]
}
```

后续如果扩展到任意网页，需要重新设计 host permission 策略，不要一开始申请 `<all_urls>`。

---

# 9. 启动 UX：建议流程

用户位于 YouTube Live Tab A，并且 A 在 Live Edge：

1. 用户点击插件“开始 AI 同传”；
2. 因为用户刚主动点击，扩展取得 A 的 `activeTab` / `tabCapture` 权限；
3. `getMediaStreamId(targetTabId=A)`；
4. Offscreen Document 开始消费 A 音频；
5. A 被标记为 `sourceTabId`；
6. 扩展 duplicate/create 一个同 URL 的 Tab B；
7. 切换用户到 B；
8. B content script 找到 `<video>`；
9. 等 B 可 seek 后，把 B 调到 Live Edge - D；
10. A 始终保持 Live Edge；
11. B 显示字幕 Overlay。

建议：

- A 自动静音/后台；
- 不自动关闭 A；
- 在 B 顶部/插件 UI 显示“AI 源标签页正在后台运行”；
- 用户停止同传时关闭/恢复 A 由设置决定。

---

# 10. YouTube 延迟控制

YouTube DVR 官方：  
https://support.google.com/youtube/answer/9296823

DVR 允许 viewer：

- pause；
- rewind；
- resume。

主播可以关闭 DVR。

HTML API：

- `video.currentTime`：  
  https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/currentTime
- `video.seekable`：  
  https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/seekable

---

## 10.1 Live Edge 的定义

对 Viewer B：

```js
const ranges = video.seekable;
const liveEdge = ranges.end(ranges.length - 1);
const liveLatency = liveEdge - video.currentTime;
```

目标：

```text
liveLatency ≈ D
```

例如：

```text
D = 10s
liveEdge = 1205.4
viewer.currentTime = 1195.6

实际延迟 = 9.8s
```

---

## 10.2 不要每帧 seek

不要：

```js
video.currentTime = liveEdge - D;
```

每 100ms 执行。

会导致卡顿。

建议控制带宽：

```text
target D = 10.0s

deadband = ±0.75~1.0s
```

如果：

```text
9.2s <= actual <= 10.8s
```

不干预。

漂移较小时：

- 可短时调 playbackRate 0.98 / 1.02 做软校正（需验证 YouTube 行为）；

漂移大于阈值：

- 一次 seek 到 `liveEdge - D`。

---

## 10.3 DVR 不可用

检测：

```text
seekable.length == 0
```

或可回退范围不足 `D + safety`。

MVP：

> 提示“当前直播未提供足够 DVR 回退范围，本模式不可用”。

V2：

- 自己实现 MediaSource/WebCodecs 环形缓冲；
- 这样不依赖 YouTube DVR。

不要把 V2 缓冲当第一版阻塞项。

---

# 11. 音频采集和百炼发送

推荐音频流水线：

```text
Source Tab A
    ↓
tabCapture MediaStream
    ↓
AudioContext
    ↓
AudioWorklet
    ↓
mono PCM 16kHz
    ↓
Local Companion WebSocket
    ↓
Bailian WebSocket
```

MVP 可以使用：

```text
PCM S16LE
16 kHz
mono
```

虽然 Qwen-Audio 3.0 支持更多格式/采样率，但统一 16k mono 最方便调试和测量。

---

## 11.1 为什么不用“每 5 秒一个 WAV”

固定块：

```text
录 5s
→ POST
→ ASR
→ translate
```

最坏情况下仅等待 chunk 填满就消耗接近 5 秒。

本项目应该优先：

```text
持续 WebSocket
小音频帧
服务端持续返回 partial/final
```

官方实时 ASR 本身就是这一设计。

---

# 12. Local Companion 为什么建议保留

推荐：

```text
Edge Extension
    ↓ localhost WebSocket
Local Companion (Python / Node)
    ↓ Bailian
```

而不是浏览器直接拿永久 `DASHSCOPE_API_KEY`。

理由：

1. API Key 不暴露在扩展包代码里；
2. 可以统一连接百炼；
3. 方便重连；
4. 方便日志和 benchmark；
5. 方便未来切模型；
6. 可以做翻译队列；
7. 可以生成 SRT；
8. 可以进行上下文压缩；
9. 可以做成本统计；
10. 可以从 Edge 扩展中完全隔离供应商 SDK。

可参考 Kami Subs 的 Native Messaging + 本地 backend 设计。

MVP backend：

```text
Python 3.11+
FastAPI / websockets
DashScope SDK 或裸 WebSocket
OpenAI-compatible client for Qwen text model
```

---

# 13. 最关键模块：TimelineMapper

这是整个项目最需要认真实现和测试的部分。

百炼 ASR 时间戳：

```text
begin_time/end_time
```

是相对**本次 ASR 音频流起点**的毫秒值。

而字幕最终需要映射到：

```text
YouTube Viewer B 的 video.currentTime
```

不能简单：

```text
字幕收到时间 - 10 秒
```

因为存在：

- 网络延迟；
- ASR 延迟；
- 翻译延迟；
- 播放器漂移；
- tab 加载时间差；
- Web Audio buffering；
- 重连。

---

## 13.1 建立媒体锚点

启动 ASR 流时记录：

```text
asrZeroSample = 0
sourceMediaAnchor = A.video.currentTime
```

理论：

```text
sourceMediaTime =
sourceMediaAnchor
+ asrTimestampMs / 1000
```

但需要考虑 capture pipeline 初始 offset。

因此正式实现应周期采集同步点：

```text
{
  wallClock,
  sourceVideoCurrentTime,
  audioSampleCounter
}
```

并对 offset/drift 做校正。

---

## 13.2 Subtitle 数据模型

建议内部统一：

```ts
interface SubtitleSegment {
  id: string;

  sourceLanguage: "zh" | "en" | "ja" | "ko";
  targetLanguage: "zh" | "en" | "ja" | "ko";

  asrStartMs: number;
  asrEndMs: number;

  sourceMediaStart: number; // YouTube media seconds
  sourceMediaEnd: number;

  sourceText: string;
  translatedText: string;

  asrFinal: boolean;
  translationFinal: boolean;

  createdAt: number;
  readyAt: number;

  revision: number;
}
```

调度只认：

```text
sourceMediaStart / sourceMediaEnd
```

不要认 API 返回 wall clock。

---

# 14. SubtitleScheduler

简单原则：

```text
if B.currentTime >= segment.sourceMediaStart
and B.currentTime < segment.sourceMediaEnd + displayTail:
    show(segment)
```

但真实字幕显示需要更合理。

建议：

```text
showStart = sourceMediaStart - 0.1s
showEnd   = max(
    sourceMediaEnd + 0.5s,
    showStart + minimumReadingTime
)
```

同时：

- 下一个字幕开始时及时收起上一个；
- 极长译文可两行；
- 不允许同一个 segment 多次闪烁；
- final 后不修改已进入 viewer 当前时间之前的字幕。

---

# 15. 翻译流水线：推荐设计

## 15.1 基础模式：final sentence translation

```text
ASR sentence_end=true
        ↓
Translation Context Builder
        ↓
Qwen Translator
        ↓
Subtitle Queue
```

最简单、最稳定。

如果 D=10～15 秒，大多数日常直播句子应该有足够时间。

---

## 15.2 长句模式：stable-prefix translation

问题：

日本主播可能连续 10～20 秒不停顿。

如果一定等：

```text
sentence_end=true
```

那么 10 秒 buffer 也可能不够，因为未来信息本身还没发生。

解决：

### 使用 qwen3-asr-flash-realtime

把：

```text
text
```

当稳定前缀；

```text
stash
```

继续观察。

当 stable text 新增长到足够语义单位：

```text
2～4 秒语音
或
20～60 个字符
或
出现可切分标点
```

触发翻译。

### 或 Qwen-Audio 中间结果 + 自己做稳定检测

维护最近 N 次 partial：

```text
partial_1
partial_2
partial_3
```

取 longest common prefix 作为 committed prefix，类似 LocalAgreement。

但 MVP 建议先不自己发明，优先 benchmark `qwen3-asr-flash-realtime`。

---

# 16. 翻译 Context Manager

这是本项目“比普通实时字幕更高质量”的关键。

每次翻当前 segment 时，不应只发送当前句。

推荐构造：

```json
{
  "stream": {
    "title": "...",
    "channel": "...",
    "domain": "...",
    "sourceLanguage": "ja",
    "targetLanguage": "zh"
  },

  "glossary": [
    {"source": "宝鐘マリン", "target": "宝钟玛琳"},
    {"source": "ホロライブ", "target": "Hololive"}
  ],

  "recent_context": [
    {
      "source": "...",
      "translation": "..."
    }
  ],

  "current_source": "..."
}
```

---

## 16.1 Context 分层

### Layer A：固定元信息

直播开始时获取一次：

- title；
- channel；
- description（如容易获取）；
- 用户指定领域；
- 源/目标语言。

### Layer B：术语表

动态维护：

```text
source term → canonical target
```

### Layer C：短期 rolling context

保存最近：

```text
30～120 秒
```

源文+译文。

不是无限增长。

### Layer D：长期摘要

每 2～5 分钟后台压缩：

```text
当前讨论主题
人物/实体
确定译名
上下文事实
```

替换早期逐句 transcript。

---

# 17. 翻译 Prompt 原则

要求：

1. 只输出当前 segment 的译文；
2. 历史文本只用于理解，不要重复翻译历史；
3. 保留口语自然度；
4. 避免把未说完句子强行补全；
5. 人名/游戏名遵循 glossary；
6. 对日语省略主语，只有上下文足够明确才补；
7. 中文字幕适合阅读，不做逐词硬译；
8. 不输出解释；
9. 最好结构化 JSON。

示意：

```text
System:
你是直播字幕翻译器。根据历史上下文理解当前句，
但只翻译 CURRENT_SOURCE。不得翻译或重复 HISTORY。
译文必须适合作为直播字幕，简洁自然，不添加未表达事实。
严格遵循 glossary。

Context:
...

CURRENT_SOURCE:
...
```

---

# 18. 四语互译设计

第一版 UI 支持：

```text
Source:
Auto / Chinese / English / Japanese / Korean

Target:
Chinese / English / Japanese / Korean
```

任何不同语言 pair：

```text
zh → en / ja / ko
en → zh / ja / ko
ja → zh / en / ko
ko → zh / en / ja
```

ASR 端：

- 固定语种时明确设置 `language_hints` / language；
- Auto 只作为用户不知道源语种时使用。

**固定源语种通常有利于准确性。**

翻译端：

- 通用 Qwen：prompt 指定；
- Qwen-MT：`source_lang` + `target_lang`；
- LiveTranslate：session source/target。

---

# 19. 延迟 D 的产品设计

不要固定写死 10 秒。

建议 UI：

```text
极速        3s
低延迟      5s
平衡        8s
高质量      10s
极高质量    15s
自定义      3～30s
```

MVP 默认：

```text
10s
```

---

## 19.1 D 不是单纯“AI 推理时间”

字幕 ready latency：

```text
L =
语言信息等待
+ ASR commit latency
+ Translation latency
+ IPC/network
+ scheduler margin
```

最容易被忽略的是：

```text
语言信息等待
```

例如主播一句话说 12 秒，如果系统一定等完整句结束，那么哪怕模型推理 0.1 秒，10 秒 buffer 也不可能提前看到第 12 秒才发生的信息。

所以必须：

- 合理 VAD；
- stable prefix；
- semantic chunk；
- 不无限等“完整语法句”。

---

# 20. Deadline-aware 翻译：本项目最值得发展的算法

给一个 segment：

```text
sourceMediaStart = S
```

Viewer 会在真实世界约：

```text
S + D
```

时播放它。

因此翻译有一个硬 deadline：

```text
deadline = sourceMediaStart + D - displayLead
```

系统可以利用这个 deadline 做质量控制。

例如 D=10s：

```text
T+0 ~ T+3.5
继续收 ASR 上下文

T+3.5 ~ T+6
等待 stable prefix / 更完整语义

T+6
必须提交翻译请求

T+7
译文 ready

T+10
Viewer 播放
```

**这与普通“识别出来就立刻翻译”的系统目标不同。**

---

# 21. Adaptive Delay（V2）

记录实际指标：

```text
asr_commit_latency
translation_latency
subtitle_ready_latency
```

统计：

```text
P50
P90
P95
P99
```

推荐延迟：

```text
D =
P99(subtitle_ready_latency)
+ safety_margin
```

或按不同模式：

```text
极速：P80
平衡：P95
高质量：P99 + context wait
```

插件 UI 可以显示：

```text
过去 10 分钟字幕：
98.7% 在播放前准备完成

推荐延迟：
8.5 秒
```

---

# 22. 失败情况下怎么处理

## 22.1 字幕没赶上 deadline

不要卡住视频。

策略：

```text
priority 1: 使用已完成较粗译文
priority 2: 使用 ASR 原文
priority 3: 迟到字幕直接丢弃/快速显示
```

记录：

```text
deadline_miss = true
```

用于 benchmark。

---

## 22.2 ASR 重连

必须：

- 记录 ASR session 起点；
- 重连创建新 timeline epoch；
- 新 epoch 重新建立 source media anchor；
- 不能把新 ASR 的 `begin_time=0` 错映射到整场直播 0 秒。

建议：

```ts
interface AsrEpoch {
  id: string;
  asrZeroMediaTime: number;
  startedAt: number;
}
```

---

## 22.3 Viewer 用户手动拖进度

如果用户手动：

- rewind；
- fast forward；
- pause；

字幕不应靠 `setTimeout`。

因为字幕已经基于：

```text
sourceMediaTime
```

只要 B.currentTime 改变，Scheduler 自动选择对应字幕。

如果用户点击“返回直播”：

- 自动重新设置 `D`。

---

# 23. MVP 工程模块

建议仓库：

```text
/live-translate-delay
│
├─ extension/
│  ├─ manifest.json
│  ├─ service-worker.ts
│  ├─ offscreen.html
│  ├─ offscreen.ts
│  ├─ audio-worklet.ts
│  ├─ content-source.ts
│  ├─ content-viewer.ts
│  ├─ delay-controller.ts
│  ├─ subtitle-overlay.ts
│  ├─ options/
│  └─ popup/
│
├─ companion/
│  ├─ main.py
│  ├─ websocket_server.py
│  ├─ bailian_asr.py
│  ├─ translator.py
│  ├─ context_manager.py
│  ├─ timeline.py
│  └─ metrics.py
│
├─ protocol/
│  └─ messages.schema.json
│
└─ docs/
   ├─ architecture.md
   ├─ benchmark.md
   └─ handoff.md
```

---

# 24. Extension ↔ Companion 协议

不要把内部消息随便散落。

建议统一 JSON 控制消息 + binary PCM。

控制：

```json
{
  "type": "session.start",
  "sessionId": "...",
  "sourceLanguage": "ja",
  "targetLanguage": "zh",
  "delaySeconds": 10,
  "sourceMediaAnchor": 1234.56,
  "sampleRate": 16000
}
```

周期 sync：

```json
{
  "type": "timeline.sync",
  "sourceVideoCurrentTime": 1240.12,
  "audioSamplesSent": 89024,
  "clientWallClockMs": 123456789
}
```

ASR：

```json
{
  "type": "asr.partial",
  "segmentId": 42,
  "startMs": 5310,
  "text": "...",
  "final": false
}
```

翻译：

```json
{
  "type": "subtitle.ready",
  "segmentId": 42,
  "sourceMediaStart": 1239.87,
  "sourceMediaEnd": 1242.21,
  "sourceText": "...",
  "translatedText": "...",
  "revision": 1
}
```

---

# 25. Benchmark：必须先做，不要凭感觉开发

## 25.1 测试素材

准备 5 类各 20～60 分钟：

1. 日本 VTuber 单人闲聊；
2. 日本游戏直播；
3. 新闻/访谈；
4. 英语科技直播；
5. 韩语直播。

每类包含：

- 快语速；
- 背景音乐；
- 游戏音效；
- 专有名词；
- 长句；
- 多人说话（即使首版不做 diarization）。

---

## 25.2 ASR A/B

至少：

```text
A: qwen-audio-3.0-asr-flash-streaming
B: qwen3-asr-flash-realtime
C: fun-asr-realtime
D: paraformer-realtime-v2（成本 baseline）
```

指标：

```text
WER/CER（有人工参考时）
人名准确率
专有名词准确率
断句质量
partial revision 次数
commit latency
P50/P95/P99
长连续说话表现
背景音乐鲁棒性
```

---

## 25.3 翻译 A/B

至少：

```text
A: qwen3.5-flash + rolling context
B: qwen3.5-plus + rolling context
C: qwen-mt-plus + tm_list/terms
D: qwen3.5-livetranslate-flash-realtime
```

人工评价：

```text
Accuracy
Fluency
Context Resolution
Term Consistency
Hallucination
Subtitle Readability
Latency
```

特别关注日→中：

- 主语省略；
- 指代；
- 句末信息；
- 男女/人物关系；
- 游戏角色名；
- 网络梗；
- 否定；
- 反问；
- 语气。

---

# 26. 延迟实验

对同一段直播离线重放或真实测试：

```text
D = 3
D = 5
D = 8
D = 10
D = 15
```

记录：

```text
deadline hit rate
```

定义：

```text
字幕在 Viewer 播到 sourceMediaStart 前 ready
= hit
```

目标：

### 平衡模式

```text
P95+ segment hit rate > 98%
```

### 高质量模式

```text
>99%
```

最终不要因为“10 秒听起来合理”就写死 10 秒。

---

# 27. 第一阶段落地顺序

## Phase 0：API Spike

不要先做完整 UI。

目标：证明：

```text
YouTube tab audio
→ Bailian realtime ASR
→ console 持续打印日文
```

完成标准：

- 连续 30 分钟不断；
- 不积累 backlog；
- 记录 ASR timestamps。

---

## Phase 1：双标签页

目标：

```text
A = Live
B = Live - 10s
```

完成标准：

- 30 分钟内 B 稳定保持 `10±1s`；
- A 不出声；
- B 正常播放声音；
- 暂停/恢复可恢复。

---

## Phase 2：时间轴字幕

先不翻译：

```text
日语 ASR
→ B 上显示日语字幕
```

如果这一步同步不准，不要进入 LLM 翻译。

完成标准：

- 字幕和 B 画面语音体感误差 < 300～500ms；
- 用户 seek 后字幕仍对应。

---

## Phase 3：简单翻译

```text
ASR final sentence
→ qwen3.5-flash
→ 中文
```

不加复杂 context。

---

## Phase 4：rolling context

加入：

- 最近 N 段；
- title/channel；
- glossary；
- summary。

比较翻译质量。

---

## Phase 5：stable prefix / deadline-aware

解决长句和 10 秒内不结束的语音。

---

## Phase 6：多语言 UI

完成 zh/en/ja/ko 任意 pair。

---

# 28. 第一版推荐参数

```text
source language: 手动选择，默认 ja（可 Auto）
target language: zh

delay D: 10s

ASR:
qwen-audio-3.0-asr-flash-streaming

audio:
PCM S16LE
16kHz
mono

segmentation:
VAD
max_sentence_silence = 800ms（实验起点）

translation:
qwen3.5-flash
non-thinking

context:
最近 30～60 秒源文+译文
+ stream title
+ glossary
+ 1 个滚动 summary

viewer sync deadband:
±0.8s

hard seek threshold:
约 ±2s（实测调）

subtitle:
双语可选
默认只显示译文
```

这些不是最终参数，必须 benchmark。

---

# 29. 极简 MVP 与正式版的差别

## 极简 MVP

```text
两个 tab
+ Qwen Audio ASR final
+ Qwen LLM 翻译
+ 10s 固定 delay
+ 简单 overlay
```

## 正式版

```text
动态 delay
stable prefix
translation deadline
上下文摘要
术语学习
自动实体提取
断线 epoch
P99 latency estimator
DVR fallback
多平台
字幕导出
LiveTranslate 极速模式
本地 Whisper fallback
```

---

# 30. 关键风险

## R1：YouTube DVR 被关闭

MVP 无法主动回退。

缓解：

- 检测并提示；
- V2 自建媒体缓冲。

---

## R2：YouTube DOM/播放器实现变化

不要严重依赖 YouTube 私有 JS 对象。

优先：

```text
document.querySelector("video")
HTMLMediaElement standard API
```

---

## R3：Source Tab 被浏览器休眠/Memory Saver 丢弃

需要：

- 监听 tab discarded；
- 必要时提示用户把 source tab 加入性能白名单；
- source 心跳；
- 检测 A.video.currentTime 是否继续前进。

---

## R4：ASR partial 改写导致翻译闪烁

默认只把：

- final；
- 或 stable prefix

交给正式字幕。

---

## R5：LLM 为了“自然”添加没说过的内容

翻译 Prompt 强调：

- fidelity first；
- no extra facts；
- 只翻 CURRENT；
- 可对 fragment 保持 fragment。

记录 hallucination benchmark。

---

## R6：字幕早于/晚于人物口型

优先排查 TimelineMapper，而不是调 translation delay。

---

## R7：API Key

不要硬编码在扩展。

MVP：local companion 环境变量/配置文件。  
正式产品：服务端 token / 用户 BYOK / 阿里临时凭证方案需另研。

---

# 31. “是否已经有人做过完全相同的产品”的最终判断

截至 2026-08-29，本轮检索找到大量：

- viewer-side tab audio live translation；
- YouTube Live translation；
- 可调 chunk；
- 可调 translation buffer；
- 主播侧 10 秒延迟翻译；
- OBS broadcast delay；
- real-time ASR；
- simultaneous translation。

但**没有找到一个成熟产品明确公开实现以下完整组合**：

```text
1. viewer-side YouTube Live
2. ASR 音频保持 Live Edge
3. 用户播放器主动落后可选 D 秒
4. 用这段媒体延迟换更长翻译上下文
5. 字幕按源媒体 timestamp 映射到延迟播放器
```

注意：

> 这是“没有在本轮检索中找到明确证据”，不是数学意义上的“世界上绝对不存在”。

---

# 32. 为什么这个项目仍值得做

现有产品普遍优化：

```text
minimize latency
```

本项目优化：

```text
given delay budget D,
maximize:
ASR accuracy
× translation fidelity
× context consistency
× subtitle stability
```

这使得 10 秒不再是“缺点”，而是：

```text
Quality Budget
```

尤其适合：

- VTuber；
- 游戏直播；
- 发布会；
- 访谈；
- 长时聊天；
- 技术直播；
- 用户不参与实时互动、只想理解内容的场景。

---

# 33. 给下一位 Agent 的明确任务

不要继续泛泛讨论“这可不可行”。

下一步应直接进入 **技术 Spike + Benchmark 设计**：

## Task A：百炼 ASR 最小可运行客户端

输出：

```text
Python / Node
持续推 PCM
持续收到日语 partial/final
打印 begin_time/end_time
```

## Task B：Edge MV3 tabCapture 最小项目

输出：

```text
点击扩展
→ 当前 YouTube tab 音频
→ Offscreen AudioWorklet
→ localhost WebSocket
```

## Task C：双 Tab delay controller

输出：

```text
sourceTabId
viewerTabId
liveEdge
actualDelay
自动维持 D
```

## Task D：TimelineMapper 原型

验证：

```text
ASR begin_time
→ source video.currentTime
→ viewer video.currentTime
```

是否能稳定在 300ms 量级。

## Task E：翻译 benchmark

拿同一段日语 VTuber 直播 transcript 比较：

```text
qwen3.5-flash no context
qwen3.5-flash + rolling context
qwen3.5-plus + rolling context
qwen-mt-plus + terms/tm
```

不要先凭主观选模型。

---

# 34. 关键源码/文档清单

## 阿里百炼

1. ASR 总选型  
   https://help.aliyun.com/zh/model-studio/asr-model

2. Qwen-Audio-3.0-ASR-Flash-Streaming  
   https://help.aliyun.com/zh/model-studio/qwen-audio-3-0-asr-flash-streaming

3. 实时 ASR 用户指南  
   https://help.aliyun.com/zh/model-studio/real-time-speech-recognition-user-guide

4. Qwen-Audio / Fun-ASR Client Events  
   https://help.aliyun.com/zh/model-studio/fun-asr-client-events

5. Qwen-Audio / Fun-ASR Server Events  
   https://help.aliyun.com/zh/model-studio/fun-asr-server-events

6. Python SDK  
   https://help.aliyun.com/zh/model-studio/fun-asr-realtime-python-sdk

7. qwen3-asr-flash-realtime  
   https://help.aliyun.com/zh/model-studio/qwen3-asr-flash-realtime

8. Qwen-ASR realtime server events (`text + stash`)  
   https://help.aliyun.com/zh/model-studio/qwen-asr-realtime-server-events

9. Fun-ASR-Realtime  
   https://help.aliyun.com/zh/model-studio/fun-asr-realtime

10. Paraformer-Realtime-V2  
    https://help.aliyun.com/zh/model-studio/paraformer-realtime-v2

11. Realtime API 总览  
    https://help.aliyun.com/zh/model-studio/realtime-api-overview

12. qwen3.5 LiveTranslate realtime  
    https://help.aliyun.com/zh/model-studio/qwen3-5-livetranslate-flash-realtime

13. LiveTranslate server events  
    https://help.aliyun.com/zh/model-studio/live-translator-server-events

14. Qwen-MT  
    https://help.aliyun.com/zh/model-studio/machine-translation/

15. Qwen-MT API（terms / tm_list / domains）  
    https://help.aliyun.com/zh/model-studio/qwen-mt-api

16. qwen-mt-plus  
    https://help.aliyun.com/zh/model-studio/qwen-mt-plus

17. qwen3.5-flash  
    https://help.aliyun.com/zh/model-studio/qwen3-5-flash

18. qwen3.5-plus  
    https://help.aliyun.com/zh/model-studio/qwen3-5-plus

19. 百炼总价格  
    https://help.aliyun.com/zh/model-studio/model-pricing

---

## Chromium / Web

20. chrome.tabCapture  
    https://developer.chrome.com/docs/extensions/reference/api/tabCapture

21. chrome.offscreen  
    https://developer.chrome.com/docs/extensions/reference/api/offscreen

22. Chrome 116 后台 tab capture  
    https://developer.chrome.com/blog/chrome-116-beta-whats-new-for-extensions/

23. HTMLMediaElement.currentTime  
    https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/currentTime

24. HTMLMediaElement.seekable  
    https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/seekable

25. YouTube Live DVR  
    https://support.google.com/youtube/answer/9296823

---

## 开源/竞品/研究

26. Kami Subs  
    https://github.com/MohammdKopa/kami-subs

27. VTuber Local Whisper Translation  
    https://github.com/CookieProduction/Youtube-Live-Stream--Local-Whisper-Translation

28. WhisperStreaming  
    https://github.com/ufal/whisper_streaming

29. WhisperStreaming 论文  
    https://aclanthology.org/2023.ijcnlp-demo.3/

30. SimulStreaming  
    https://github.com/ufal/SimulStreaming

31. WhisperLiveKit  
    https://github.com/QuentinFuxa/WhisperLiveKit

32. Re-translation vs Streaming  
    https://aclanthology.org/2020.iwslt-1.27/

33. wait-k simultaneous ST example  
    https://aclanthology.org/2020.iwslt-1.5/

34. Alignment-Based Chunking  
    https://aclanthology.org/2020.iwslt-1.29/

35. Immersive Translate Live  
    https://livetranslate.immersivetranslate.com/

36. Whisperr YouTube Live  
    https://whisperr.co/use-cases/translate-youtube-live/

37. Live Translate adjustable buffer  
    https://chromewebstore.google.com/detail/live-translate/kjjelnicbdofofjkdmpnpijbbfbekmgb

38. Live Translate chunk-size implementation example  
    https://chromewebstore.google.com/detail/live-translate/ebaamfaihhpmhjimhpnhiipdfmhhendj

39. Voxis  
    https://chromewebstore.google.com/detail/voxis-%E2%80%94-live-translate/eaoplhkoomnlgfhcjeccgkhnkodkfbjn

40. Voco（10-second live translation claim）  
    https://www.voco.studio/

41. Voco livestream captions / OBS  
    https://voco.church/livestream-captions

---

# 35. 最终推荐结论（不要丢）

**第一版架构：**

```text
Microsoft Edge MV3
      │
      ├─ Source YouTube Tab A @ Live Edge
      │      ↓ tabCapture
      │   Offscreen AudioWorklet
      │      ↓ PCM 16k mono
      │
      ▼
Local Companion
      │
      ├─ qwen-audio-3.0-asr-flash-streaming
      │      ↓ source transcript + timestamp
      │
      ├─ Context Manager
      │
      ├─ qwen3.5-flash contextual translation
      │
      ▼
Subtitle Timeline
      │
      ▼
Viewer YouTube Tab B @ Live Edge - D
      │
      ▼
Chinese / English / Japanese / Korean subtitle overlay
```

**默认：**

```text
D = 10s
```

**但产品层面：**

```text
D 用户可选，并最终做 adaptive。
```

**ASR 首选：**

```text
qwen-audio-3.0-asr-flash-streaming
```

**ASR 必测对照：**

```text
qwen3-asr-flash-realtime
```

因为其 stable `text` + unstable `stash` 非常适合长句。

**翻译首选实验：**

```text
qwen3.5-flash + rolling context
```

**翻译必须对照：**

```text
qwen-mt-plus + terms/tm_list
qwen3.5-livetranslate-flash-realtime
```

**最先解决的问题不是 UI，也不是模型质量，而是：**

```text
TimelineMapper
```

必须先证明：

> Live Edge 音频识别得到的时间戳，可以稳定映射到落后 D 秒的 Viewer `<video>.currentTime`。

一旦这个成立，剩下基本都是可替换模块。

---

# 36. Handoff 最终状态

当前结论：

```text
Feasibility: HIGH

Novel engineering:
  • viewer-side dual timeline
  • delay controller
  • source-media timestamp mapping
  • deadline-aware contextual translation

Commodity / existing:
  • tab audio capture
  • streaming ASR
  • text translation
  • subtitle overlay
  • WebSocket
  • VAD
```

下一位 Agent 应从 **Phase 0～2** 开始真正写代码与做测量，不要继续停留在概念讨论。
