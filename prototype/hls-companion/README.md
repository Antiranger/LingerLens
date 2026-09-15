# PROTOTYPE 2 — YouTube / Bilibili / Twitch 延迟 HLS/CMAF 浏览器播放器

这是 Windows-first 的本地直播播放器原型，支持**正在进行的** YouTube 直播、Bilibili Live 房间和 Twitch 频道直播：yt-dlp 负责平台提取与下载，FFmpeg 仅做 stream-copy fMP4 HLS 封装，浏览器通过本地 hls.js 播放并可启用实时字幕。

不支持普通视频、VOD、Clip、直播回放、upcoming/等待页或已结束直播。Twitch Chat 暂不支持；Bilibili 弹幕保持最佳努力，失败不阻止媒体和字幕。

## 当前链路

```text
直播页面 URL
  → 当前版 yt-dlp 探测格式并完整负责直播音视频下载/重试
  → 选择浏览器兼容的 H.264/AVC + AAC（或平台 muxed HLS 的未知音频 metadata）
  → YouTube：视频+音频两个 selector / 两个 yt-dlp / 两个 TCP Pump
  → Bilibili/Twitch：一个 muxed selector / 一个 yt-dlp / 一个 TCP Pump
  → yt-dlp 输出 MPEG-TS → FFmpeg -c copy
  → 私有滚动 fMP4 HLS
  → Companion 按目标总延迟保留完成分片，并在首次公开前攒够播放器追边预算
  → 最近约 30 秒的公开环形 playlist + localhost HTTP
  → 本地打包 hls.js → Chrome/Edge

音频 Pump（YouTube 音频腿；Bilibili/Twitch 为同一个 muxed Pump，非阻塞 tee）
  → 本机 FFmpeg 重采样为 PCM 16k mono
  → 可配置 ASR provider
  → Provider-neutral Recognition Evidence → CaptionChunker（6 秒软参考，已完成识别的残片有界收尾）
  → one Caption Chunk = one Subtitle Cue / 一个 ASR Utterance 可产生多个 Cue
  → 按 Media Wall Clock 对齐 / source-order Continuity Context / 翻译 provider
  → `/api/subtitles` → hls.js `playingDate` 对齐 overlay
```

媒体获取统一复用 `YtDlpLiveIngest`。YouTube 保持双路下载；Bilibili/Twitch 使用其既有单 selector/单 Pump 分支。字幕 tee 与包装共享同一份输入字节，字幕 FFmpeg 用 `-vn` 提取并解码音频，不会再次连接平台下载音频。LingerLens 不做运行时 FFprobe、不转码，也不建立第二套 ingest 生命周期。

## 目录

- `companion/core.py`：格式归一化、认证边界、FFmpeg 命令、延迟 playlist 发布、环形清理。
- `companion/ytdlp_ingest.py`：当前版 yt-dlp 子进程；完整负责 YouTube 直播下载并输出 MPEG-TS。
- `companion/server.py`：只绑定 loopback 的 HTTP/API 服务，提供字幕与 provider 配置接口。
- `companion/providers/`：ASR/翻译 provider 抽象、百炼 Realtime/任务式 ASR、Deepgram Streaming、OpenAI Realtime/Audio Transcriptions，以及 OpenAI 兼容、Claude、Gemini 翻译与 fallback。
- `companion/caption_chunker.py`：统一 Recognition Evidence、LocalAgreement 和有界续接的分句缓冲；6 秒仅为软参考。完整句立即输出，已完成识别的残片最多再等 1.2 秒取得合格续文；没有续文则按原音频范围收尾。仍在进行的识别不受此期限截断。**每句话的账本（item 与 lane）按"证据可用性"回收，而不是等 Provider 的生命周期事件**——Deepgram 之类只发 `stable_token_delta`/`endpoint`、从不发 `utterance_final` 的 Provider 不会让内部状态随会话时长线性增长（实测 8 小时会话每句处理成本保持不变；修复前为 64 倍劣化）。
- `companion/subtitle_pipeline.py`：音频 tee、TS→PCM、ASR evidence 接入、Cue 发布、并行翻译与终态统计。分句只有 CaptionChunker 一个决策者，不存在第二条切句路径，也不会为切句向 Provider 发手动 commit。
- `companion/subtitle_text.py` / `subtitle_store.py` / `context_manager.py`：纯字幕清洗、一次终态发布和 source-order Continuity Context。
- `web-player/`：独立播放器；`vendor/hls.min.js` 为本地打包的 hls.js 1.7.1。
- `extension/`：最小 MV3 开发扩展，从当前标签对应 Cookie Store 读取平台 Cookie。
- `companion/native_host.py`：Native Messaging → Companion 的敏感小消息桥。
- `companion/control_ipc.py`：带随机认证密钥的本机 named pipe / AF_UNIX 控制通道。
- `scripts/register-native-host.ps1`：Chrome/Edge Windows 开发注册助手。
- `scripts/synthetic-smoke.py`：本地 1080p H.264/AAC CMAF 延迟发布冒烟测试。

## 依赖

- Python 3.10+（本机 yt-dlp 已提示未来应升级到 3.11+）
- `aiohttp`
- 当前版 `yt-dlp`（已在 `vendor/yt-dlp/yt-dlp.exe` 固定并校验；版本 2026.08.19）
- `ffmpeg` / `ffprobe`
- Chrome 或 Edge

安装 Python 依赖：

```bash
python -m pip install -r prototype/hls-companion/companion/requirements.txt
```

## 方式一：开发期浏览器 Profile Cookie

这是最快的登录直播验证方式，不是产品主路径：

```bash
npm run prototype:hls -- --cookies-from-browser chrome
# 或
npm run prototype:hls -- --cookies-from-browser edge
```

然后打开：

```text
http://127.0.0.1:8765/
```

输入直播 URL，点击“探测清晰度”，选择 1080p 或 720p，再启动播放。

注意：Chromium 正在运行时，`--cookies-from-browser` 可能受浏览器 Cookie 数据库锁定/系统解密策略影响。失败时界面会显示 yt-dlp 的尾部诊断，不会静默降级。

## 方式二：当前标签 Cookie Store + Native Messaging

1. 启动 Companion（不要加 `--cookies-from-browser`）：

   ```bash
   npm run prototype:hls
   ```

2. 在 `chrome://extensions` 或 `edge://extensions` 打开开发者模式，加载：

   ```text
   F:/Projects/LingerLens/prototype/hls-companion/extension
   ```

3. 复制加载后显示的扩展 ID。

4. 在 PowerShell 注册本地 Host：

   ```powershell
   powershell -ExecutionPolicy Bypass -File `
     F:/Projects/LingerLens/prototype/hls-companion/scripts/register-native-host.ps1 `
     -ExtensionId 你的扩展ID
   ```

5. 打开 YouTube/Bilibili/Twitch 直播页（公开直播可匿名），点击扩展图标。扩展会：
   - 确定当前标签对应的 Cookie Store；
   - 读取当前平台 allowlist 范围 Cookie（包括 HttpOnly 和可用的 partition 元数据）；
   - 通过 Native Messaging 传给本机 Host；
   - 打开带一次性 `authToken` 的本地播放器页。

6. 在本地播放器中探测并启动。Cookie 快照在开始提取时消费并从内存删除。

## 方式三：在播放器里手动导入 Cookie

不需要关浏览器，也不需要扩展：

1. 在已登录的浏览器里安装任意 Cookie 导出扩展（如 “Get cookies.txt LOCALLY”），在 YouTube 页面导出 `youtube.com` 的 Cookie（Netscape 格式）。
2. 启动 Companion（无需任何 cookie 参数）：`npm run prototype:hls`。
3. 播放器右上角「导入 Cookie」→ 选格式 → 粘贴 → 「导入并授权」。
4. 直接点「探测清晰度」「启动播放」；导入的授权在启动时消费，若启动失败重新导入一次即可。

也支持粘贴请求头格式的 Cookie 串（`SID=xxx; VISITOR_INFO1_LIVE=yyy`），此时选择作用域域名。导入接口仅接受 loopback 且校验同源 Origin，Cookie 只存内存，不落盘、不回显。

卸载注册：

```powershell
powershell -ExecutionPolicy Bypass -File `
  F:/Projects/LingerLens/prototype/hls-companion/scripts/register-native-host.ps1 `
  -ExtensionId 你的扩展ID -Unregister
```

## Provider Catalog 与本地 Whisper

播放器右上角「模型设置」是 ASR 和翻译 Provider 的唯一新增、编辑、删除与 active 选择入口。每类至少保留一条记录；当前 active 记录必须先切换到另一条后才能删除。保存后，下一次启动字幕只使用目录中 active 的 ASR 与翻译记录。

LingerLens **不安装、不下载、也不启动 Whisper 服务**。请先自行运行 Speaches、faster-whisper-server、Xinference、LocalAI 或其他兼容 `POST /v1/audio/transcriptions` 的服务，再新增 ASR：

- 协议：`OpenAI Audio Transcriptions`
- Base URL：例如 `http://127.0.0.1:8000/v1`
- 模型：例如 `whisper-1` 或服务实际暴露的模型名
- API Key：本地服务不校验时可留空；需要占位值时按该服务要求填写
- 语言/分窗/超时：按模型速度与直播语言调整

兼容性取决于服务实现。LingerLens 发送 multipart WAV、`model`、`language` 与时间戳格式请求，并接受普通 JSON 文本或带 segment 时间的 verbose JSON。短窗请求失败只影响字幕，不会阻塞媒体播放。

## 实时 STT Provider

除 DashScope 与本地 OpenAI-compatible 文件转写外，LingerLens 提供多种彼此独立的原生 WebSocket ASR Adapter，均直接使用公开协议与 `aiohttp`，不依赖厂商 SDK：

- **Deepgram Streaming**（`deepgram-streaming`，模型 `nova-3`）：全球多语言 + 混合语言识别（`language=multi`，官方 10 语言集合）与 word 时间戳；指定语言覆盖官方 nova-3 列表。事件按官方 `Results` 的 `is_final`/`speech_final` 与 `SpeechStarted` 映射。
- **Soniox Realtime STT**（`soniox-realtime`，模型 `stt-rt-v5`）：16 kHz mono PCM 二进制流，支持 60+ 语言、语言提示/限制、逐 token 语言识别、word 时间戳、上下文 terms、语义 endpoint 与手动 `finalize`。适配器把已 final 的 token 前缀作为稳定 interim，并在 `<end>`/`<fin>` 边界生成一条 final cue；默认端点为 `wss://stt-rt.soniox.com/transcribe-websocket`，密钥环境变量为 `SONIOX_API_KEY`。
- **OpenAI Realtime Transcription**（`openai-realtime-transcription`）：当前 `type: "transcription"` 会话协议，24 kHz PCM；低延迟 preset 为 `gpt-live-transcribe`，需要 detected-language 输出时选择 `gpt-transcribe` preset。该协议不返回 word 时间戳，字幕边界依赖 server VAD 事件。
- **AssemblyAI Streaming v3**（`assemblyai-streaming`）：`universal-3-5-pro`，binary PCM、覆盖式 Turn revision、`end_of_turn && turn_is_formatted` 双条件 final、语言检测与可选 speaker labels。
- **火山引擎大模型流式 ASR v3**（`volcengine-sauc`）：实现官方 4-byte header、sequence、payload-size、gzip 与负包收尾协议；公共 streaming 合同只声明中英，不把 nostream 日语能力误报为实时能力。
- **ElevenLabs Scribe v2 Realtime**（`elevenlabs-scribe-realtime`）：query 配置、Base64 `input_audio_chunk`、manual/VAD commit、typed error；timestamped committed copy 不会重复生成 cue。
- **Speechmatics Realtime v2**（`speechmatics-realtime`）：指定单语、binary PCM、`AddPartialTranscript`/`AddTranscript`、词时间戳与可选 speaker diarization。
- **腾讯云实时 ASR**（`tencent-asr`）：HMAC-SHA1 短时签名 URL、binary PCM、经典 `slice_type` 与 speaker sentence 两种已确认响应；日语使用 `16k_ja`，中英说话人分离使用相应 speaker 引擎。

支持等级说明：这些云 Provider 当前均为 `provider_claimed`（官方协议与文档为准），本仓库环境没有对应全部真实凭据，**尚未完成真实直播矩阵验证**；自动测试使用本地 fake WebSocket + 官方事件/帧样本。Google `gemini-3.5-transcribe-live` 和 Mistral `voxtral-mini-transcribe-realtime-2602` 已确认存在，但转写专用 wire contract / SDK 边界尚不足以安全实现，因此本轮保持 defer，不在 UI 中伪装为已支持。

### Provider-neutral Caption Chunking 合同

LingerLens 不再把“一个 ASR final”当成一个显示 Cue。Adapter 先把协议事件归一为 Recognition Evidence，然后唯一的 `CaptionChunker` 生成 Caption Chunks：

- **ASR final 只确认识别**：核对并关闭识别条目，不强制关闭字幕。相同已知说话人、相同主语言的未完成表达可以跨条目接续；未知说话人只在自己的条目内合并，避免混入另一人的话。
- **按语言证据切分**：完整句子及时提交；持续讲话时，允许在已完成的从句末尾提交。日语使用本地 fugashi + unidic-lite 做词形分析，拒绝词内部、助词和助动词链上的切点；不为每个 token 请求网络模型。其他语言采取保守的标点及有限分句规则，尚不具有同等的日语语法识别覆盖。
- **6 秒是软参考**：不因超时、词间空隙或 ASR final 硬切，不因字幕预算发 manual commit。当前响应中已包含不超过 6 秒的完整短句时，保留整句；没有可靠结尾则继续等待，不保证每句都在固定时间内发出。停止、断线或会话结束时显式保留剩余文字一次。
- **真实时间不变**：严格时间标记只说明来自真实 token 时间，不表示六秒保证。Soniox 的 CJK 稳定 tokenizer pieces 立即上交，保留原始文字和各自时间，由共享切句器判定可切边界；不拆分单个 piece 后伪造时间。不同说话人的重叠时间与相同发言均保留。
- **其他证据路径保留**：稳定前缀/无时间文本使用 VAD 或 audio frontier，不声称精确词时间；mutable snapshot 仍经 LocalAgreement。Provider 的音频请求窗口独立于字幕切分策略。
- **配置与观测**：Soniox 直播推荐开启 endpoint detection 和 speaker diarization；端点是辅助证据。`pendingEvidenceOverSoftSpan` / `spanOverSoftTarget` 分别记录超过软参考仍待证据、输出跨度超过软参考的情况。二者都是纯信息性的：**没有任何计时器会强制切句**，所以非零值只说明一段话在软参考 6 秒后仍没有安全边界，不代表某个上限被违反。（旧名 `hardCapPendingEvidence` / `hardCapOvershoots` 以及 `hardCapCuts` / `manualHardCommits` 已随"硬上限"机制一并删除——那套机制从未被任何代码路径触发过。）

安装新增依赖：`python -m pip install -r prototype/hls-companion/companion/requirements.txt`。日语词典在进程中复用，不产生在线模型费用；已有 Companion 进程需要重启才能加载代码。

示例 `runtime/providers.example.json` 已包含 `local-whisper` 记录。Speaches/faster-whisper-server 通常可直接使用上述本机 URL；Xinference 请把 Base URL 改为其 OpenAI-compatible API 根路径并使用已启动模型的 ID。

## 翻译 Provider（OpenAI 兼容 / Claude / Gemini）

翻译侧提供三个可选协议 kind，全部只用 `aiohttp` 直连 REST 协议，不引入厂商 SDK：

- **OpenAI Compatible**（`openai-compatible`）：OpenAI 官方或兼容端点的 `/chat/completions`。
- **Anthropic Messages**（`anthropic-messages`）：`POST /v1/messages`，`x-api-key` + `anthropic-version` 头，顶层 `system`；错误形状 401/403/429/500/529 按官方文档映射。
- **Google Gemini**（`google-genai`）：`POST models/{model}:generateContent`，`x-goog-api-key` 头，`systemInstruction` + `contents.parts`；安全阻断（`promptFeedback.blockReason` / candidate `finishReason`）视为失败而非空译文。

通用 LLM（OpenAI 兼容 / Claude / Gemini）共用一份 provider-neutral 翻译 prompt 合同（`companion/translation_prompt.py`）：固定 system 规则、稳定英文源/目标语言名（来自语言目录）、流元数据、术语表、source-order HISTORY 与 CURRENT。Prompt 明确只译 CURRENT，不重复/总结 HISTORY，不预判下一块；句中切块保持自然未完状态，并保持最近前文的主语、指代、人称、时态、语气、礼貌级别、专名和术语。

Continuity Context 以 `(generation, chunk_order, media_t_end)` 在字幕切出后立即保存原文，翻译成功再补译文。后一句不等待前一句译完：请求按顺序携带前文，未翻译的前文标为 `SOURCE ONLY`，已翻译的前文同时带上译文；模型只翻译 CURRENT，不重复输出前文。查询排除 CURRENT、future Chunk、其他 generation 和媒体时间在后面的内容，`contextSeconds` 使用媒体时间差。`translationContextMissingImmediatePredecessor` 仍表示前一块译文尚未完成，不意味着其原文也缺失。Qwen-MT 的 `tm_list` 协议只接受真正的原文/译文对，因此过滤仅有原文的条目；通用 OpenAI 兼容、Claude、Gemini prompt 支持原文上下文。

翻译采用已有 `translationWorkers`（默认 4）并发，不按前后句串行等待。并发名额满时仍可能短暂排队；每句从进入队列时固定自己的截止时间，排队不会延长期限，超时结束为原文状态，过期任务不再占用新的网络请求。显示位置始终沿用原始说话时间，翻译完成顺序不改写时间戳。晚到字幕可以在空档短暂补显示，但已有后续讲话开始时必须让位，且不能在后续字幕结束后重新出现；真实重叠说话保持同时显示。


Cue 进入 Store 后源文、时间、generation/order 与 cut metadata 不可变；翻译只允许一次终态 `src/translating -> done|failed`。状态 telemetry 分开报告 `sourceReadyLagP50/P95`、`translationSuccessReadyLagP50/P95` 与 `terminalOutcomeLagP50/P95`；旧 `readyLagP50/P95` 保留为 terminal-outcome alias，失败不能混入翻译成功 ready。usage 统一归一化为普通输入、缓存读取、缓存写入、输出四类 token；缓存写入单价可选，缺 usage 或缺必需单价时费用显示不可估算而不是 0。错误统一归类为 auth/request/rate-limit/unavailable/timeout，fallback 链对配置类错误（auth/请求/语言对）在本次会话内禁用该 Provider，对 429 立即降级并短冷却，对 5xx/529/超时按连续失败阈值冷却。

支持等级说明：`anthropic-messages` 与 `google-genai` 当前为 `provider_claimed`（官方协议与文档为准），本仓库环境没有 ANTHROPIC_API_KEY / GEMINI_API_KEY 凭据，**尚未做真实 API 验证**；测试全部通过本地 fake HTTP server + 官方请求/响应样本完成。使用前请在「模型设置」中新增对应记录并填入自己的 API Key（环境变量 `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` 亦可）。示例记录见 `runtime/providers.example.json`。

## 安全边界

- Companion 只允许绑定 `127.0.0.1`、`localhost` 或 `::1`。
- Cookie 不通过普通 localhost HTTP API 提交；Native Host 通过带本机随机认证密钥的 Windows named pipe（Unix 上为用户级 AF_UNIX socket）把敏感快照交给 Companion。用户主动在播放器「导入 Cookie」时，同一份快照也可经仅限 loopback、同源校验的 `/api/auth-cookies` 提交（见方式三）。
- Cookie 不写日志、不回显、不放在 yt-dlp/FFmpeg 命令行。
- Native Host 会生成用户私有的 Netscape 临时 Cookie 文件供当前版 yt-dlp 启动；yt-dlp进入下载阶段后立即删除，停止/失败时也会清理。
- Cookie 只接受 YouTube/Google、Bilibili 或 Twitch 域名，并按目标平台隔离；公开 Bilibili/Twitch 直播不要求 Cookie。直播页面 URL 仅允许 HTTPS YouTube、`live.bilibili.com/<数字房间>` 和 `twitch.tv/<频道>`。
- YouTube 部分格式可能还要求 PO Token。本原型保留了 `AuthenticationProvider` 边界，但没有伪装已实现 PO Token。
- 开启云端字幕后，音频会从本机发送到用户配置的 ASR 服务（默认示例为阿里云百炼），源文/上下文会发送到翻译服务。这与 Cookie 只在本机流转是两条不同的数据边界；不开启字幕时不会挂载音频 tee。
- API Key 从服务端 `runtime/providers.json` 或环境变量读取；普通 `/api/providers`、状态、日志、错误与扩展消息永远脱敏。按产品要求，只有 loopback + same-origin 且 `Cache-Control: no-store` 的「模型设置」路由会把原始 Key 返回给浏览器并在输入框中直接显示，便于检查和替换。
- **本地屏幕风险：** 打开模型设置时，原始 Key 会对屏幕旁观者、截图、录屏和远程桌面共享可见。不要在共享屏幕时打开该窗口；真实 `runtime/providers.json` 也应作为本机私有文件保存，不要提交或共享。

## 质量策略

- 显示 yt-dlp 返回的候选清晰度。
- YouTube 保持现有 H.264 视频 + AAC 音频双路选择、排序和 FFmpeg 双输入映射。
- BiliLive 即使缺少尺寸、fps、码率和音频 codec metadata，也可暴露单路 AVC HLS；未知分辨率会如实显示，多 CDN 镜像按逻辑质量/codec/protocol 去重，AVC HLS 优先于 AVC FLV。
- Twitch 使用单路 muxed HLS；Auto 在 `maxHeight` 内优先 Source/最高分辨率，其次 fps 和码率。
- HEVC/AV1 等不兼容选项不会进入 Auto；本原型不为了“成功”静默转码或降质。Bilibili/Twitch 的真实输入若缺少视频或音频，包装 FFmpeg 会失败并触发完整清理。

## 验证

字幕配置样例位于 `runtime/providers.example.json`。首次启动会生成本机 `runtime/providers.json`；推荐设置 `DASHSCOPE_API_KEY` 后运行。ASR 协议 spike：

```bash
python prototype/hls-companion/scripts/asr-spike.py --audio <16k单声道日语wav>
```

运行全部 Prototype 2 静态/单元测试：

```bash
npm run test:hls-companion
```

运行本地 1080p H.264/AAC → fMP4 HLS 延迟发布/ffprobe 冒烟测试：

```bash
python prototype/hls-companion/scripts/synthetic-smoke.py
```

运行旧 spike 测试：

```bash
npm test
```

## 浏览器冒烟边界

自动测试覆盖字幕浮窗控制器的归一化位置持久化、边界夹取、键盘移动、重置与样式参数，并检查播放器舞台全屏的 DOM 资源合同。仓库当前没有 DOM/Playwright 依赖，因此 Chromium 原生全屏归属仍需人工冒烟：启动媒体后点击 LingerLens 的“全屏”，确认 `document.fullscreenElement` 是 `.player-stage`、字幕仍可见；退出全屏后确认字幕调度继续。

## 已完成的自动验证

- 格式归一化和自动选择可选 1080p AVC/AAC；
- 分离视频/音频映射到 FFmpeg，命令明确使用 `-c copy`；
- fMP4 HLS 使用 `.m4s + init.mp4`；
- 当前版 yt-dlp 完整负责 YouTube 远程下载；LingerLens/FFmpeg 不再直接请求 Googlevideo URL；
- 延迟 publisher 保持下载后额外媒体预算，并把公开回看窗口限制在最近约 30 秒；
- hls.js 由本地文件加载，不依赖 CDN；
- MV3 清单与脚本可解析；
- 当前公开 YouTube 直播探测到实际 `1920×1080 AVC/AAC` 格式；
- 合成 1920×1080 H.264 + AAC 成功生成/发布本地 HLS，ffprobe 识别为 H.264/AAC；
- Chrome 真实浏览器已打开本地播放器并完成初始 UI/控制可访问性检查。

## 仍需人工完成的验收

自动验证不能替代以下真实直播测试：

1. Edge/Chrome 连续播放当前 YouTube 直播至少 30 分钟；
2. Edge 重复同一测试；
3. 实测总延迟是否稳定在约 10 秒，并据 UI 数据把服务端 7 秒调为 6/8 秒；
4. 音画同步是否持续无漂移；
5. 实际 `video-only + audio-only` 1080p 直播是否稳定双输入合流；
6. 使用账号可见的登录受限直播验证 Native Messaging Cookie 路线；
7. Bilibili/Twitch 每个平台至少两个样本连续播放 10 分钟，验证画面、声音、PDT、字幕 PCM 与 Stop 清理；
8. Twitch 广告或 `EXT-X-DISCONTINUITY` 边界仅在实际观察到时才能标为通过；不会移除广告；
9. Twitch Chat 不在本轮范围，UI 稳定显示“暂不支持”。

本轮真实结果记录在 `.scratch/bilibili-twitch-live-support/acceptance-results.md`：Bilibili 与 Twitch 已各完成生产路径短启动 smoke，但 10 分钟播放、字幕云端 PCM、Twitch 广告边界、Bilibili 弹幕和 YouTube 真实回归仍未覆盖，不能标为完整通过。

Ticket 03 的离线验收 fixture 位于 `tests/fixtures/ticket03_translation_continuity.json`，覆盖英语、西班牙语、中文、日语、韩语、mixed/unknown（含 URL、数字/小数、专名、emoji）以及翻译完成顺序 `3,1,2`。这些 deterministic fixtures 只证明分块/顺序/Prompt 合同，不冒充真实语言质量或云端通过。仍 blocked 的真实验收包括：英语 >20 秒原始 utterance、西班牙语直播、zh/ja/ko 至少两种直播、三类真实 evidence Provider、背景音乐/歌词、50 个 boundary 人工分类、source-ready / translation-success-ready / terminal-outcome latency 与 strict source-before-playback 99% 指标；需要对应直播样本和 Provider 凭据。

## 是否适合下一步包装成扩展 iframe

**接口形态适合**：播放器资源自包含、媒体走 localhost HLS、敏感数据走 Native Messaging，后续可以把同一播放器打包为扩展 viewer 页面并用 extension-origin iframe 覆盖原播放器区域。

**现在还不应正式包装**：先完成 Chrome/Edge 30 分钟、登录受限直播、真实分离音视频 1080p 和延迟校准。通过后再做 iframe 覆盖、原播放器暂停/静音与停止恢复。
