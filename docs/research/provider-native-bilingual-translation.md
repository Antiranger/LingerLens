# 端到端双语 Provider（Provider 侧翻译）

> 状态：已落地  
> 协议核验日期：2026-09（官方文档 + 官方 SDK 源码）  
> 相关代码：`companion/providers/native_session.py`、`asr_soniox_realtime.py`、`asr_dashscope_livetranslate.py`

## 1. 为什么需要它

原本的链路是 **ASR 出源文 → 另一个翻译模型出译文**，两条时间线、两次计费、两次失败机会。
现在有两家把翻译放进了同一条 ASR 会话：

| Profile | kind | 模型 | 方向 | 官方依据 |
|---|---|---|---|---|
| ⭐ Soniox STT RT v5 | `soniox-realtime` | `stt-rt-v5` + `translation` | 单向 / 双向 | [STT translation](https://soniox.com/docs/translation/stt-translation) |
| 百炼 Qwen3.8-LiveTranslate | `dashscope-livetranslate-realtime` | `qwen3.8-livetranslate-flash-realtime` | 仅单向 | [qwen3-5-livetranslate 指南](https://help.aliyun.com/zh/model-studio/qwen3-5-livetranslate-flash-realtime)（同页含 3.8 与 3.5 两个 tab） |

这两条 Profile 选中后 **不再调用任何翻译 Provider**，`capabilities.native_translation.enabled` 为真时
`_prepare_subtitles` 直接换上会话内翻译 Provider。界面上的星只有一颗，标在 Soniox 上：两颗并列读到的是
「这两条一样」，不是排序，所以另一条只按目录顺序排在第二。

## 2. 架构：一根总线，不是第二条通路

```
ASR Adapter ──record/close──▶ NativeTranslationBus ──resolve──▶ NativeSessionTranslation
                                                                    │
                                                     （就是普通 TranslationProvider）
                                                                    ▼
                                       SubtitlePipeline 既有的 worker / deadline / CueStore
```

`NativeSessionTranslation` 是一个普通的 `TranslationProvider`，所以
`_translation_worker`、`translation_budget`、cue 状态机、`/api/status` 统计全部原样复用，
没有长出第二条翻译路径。它与 LLM Provider 的区别只有三点：

1. `capabilities.rolling_context = False` —— Provider 自己维护上下文，LingerLens 不重复喂历史；
2. 目标语言在**会话建立时**就要定下来，因此 `SubtitlePipeline` 在每次 `stream()` 前调用
   `asr_provider.set_translation_target(meta.target_lang)`（`ASRProvider` 上的默认空实现，
   其余 12 个 Adapter 不需要新增参数）；
3. 解析不到译文时抛 `ProviderRefusalError`，cue 退化成 `failed`（只显示原文），与翻译模型失败同款。

### 归属规则：一条字幕 = 一个 Provider 段或它的一个对齐点

Provider 的翻译属于它自己切出来的那一段（Soniox 的 `<end>`、LiveTranslate 的 turn detection），
所以 `SubtitlePipeline` 在启用会话内翻译时构造 `CaptionChunker(realtime=True, segments_only=True)`：
不按标点再切，只在 Provider 的 endpoint 收口。开说话人分离时字幕轨按 `speaker:<label>` 归并，
因此 `segments_only` 下**任何 item 变化都先收口**——一条装着两个段的字幕，两个段的译文都归属不上。

`ASREvent.item_id`（`CaptionChunk.item_id`）是 Provider 段与 cue 之间的 join key；
比对用的文本两侧都过 `subtitle_text.subtitle_match_key()`（即 `clean_subtitle_text` 的 NFKC +
重复标点折叠）。cue 走过这道清洗而 Provider 侧不走，全角 `！`、`、、` 就会让同一句话永远不相等，
字幕静默失去译文——这是历史上最常见的丢翻译原因。

比对时**空格也要整个抹掉**：Provider 会在两个日文词中间写空格（LiveTranslate 在一句话里面就有），
而字幕这边 `CaptionChunker` 合并片段时会删掉中日韩相邻字符之间的空格，一侧留一侧不留，
同一句话照样不相等。2026-09-22 拿真账号录下的那一手数据离线重放，8 条字幕里 5 条只剩原文，
逐条对下去，丢的正好是"该段转写含内部空格"的那 5 条。

剩下两件事由**对齐锚点**负责：`ASREvent.translation_anchors` 是 Adapter 从 Provider 自己的流序里
抽出来的 `(累计原文, 累计译文)` 对——Soniox 每在 `translation` 之后重新出现 `original`，就说明它
对"译文到这里为止"表了态。总线据此把段内切片发给各自的 cue，并推进 `consumed_source` /
`consumed_translation` 游标，同一段不会被重复取走。两个后果：

1. **延迟**：锚点是已确认文本，段还没闭合就能解析，cue 不再排在 Provider 的静音标记后面；
2. **7s 硬上限**：主播长时间不停顿时靠它兜底，切点若正好落在锚点上，两截各得自己的译文。

切点落在两个锚点之间时**不猜**：官方明确说译文 token 与原文 token 不是一一对应的，
按比例取前缀属于伪造对齐，因此这一条保持只有原文。段已闭合而对不上任何边界即视为终局，
立即 `failed`，不再占用 worker 等到预算耗尽。

### 投影行：原文先上屏，译文后补

`segments_only` 的代价是"话已经听完，字幕还没出"。2026-09-22 用真账号录下的会话离线重放，
viewer 摆在音频前沿后面 **11s（App 允许的最小值）**；把同一场摆到 4s 只能测出更乐观的数字，
而 4s 是播放器不给的档位，所以下面按 11s 记：

* LiveTranslate 的转写增量和它自己的收口事件几乎同时到——8 个 turn 的 `delta → completed`
  只有 0.03–0.4s。也就是说"等它那段静音"本身不值一秒：调小 `silenceDurationMs` 不会让翻译变快，
  它换来的是**更多句子由 Provider 自己收尾**（见第 3 节那条旋钮），也就是少几处需要事后配对的地方；
* 真正被吞掉的是不停顿的长句：要等 `HARD_DEADLINE_SECONDS` 才切。11s 档下这一场 10 条字幕里
  **3 条本可以提前，中位 3.33s，最长 3.78s，合计 9.0s**（把 viewer 挪到 4s 会得到"7/8 条、
  最长 7.00s"，那是不存在的档位）；其余 7 条是 Provider 自己收尾的，投影在收口那一刻就没了，
  所以提前上屏这条路对双语模型的收益比想象的窄得多。

于是有了这条通道：`CaptionChunker.pending_caption(heard_pcm)` 把**还压在轨上没收口**的文本
投影出来，走的是同一个 `_join_units`、同一套时间，所以投影和后来那条 cue 对"这句话在什么位置"
不会各说一套。链路是 `SubtitlePipeline.caption_draft()` → `/api/subtitles` 的 `draft` 字段 →
`player.js` 里画在 `.subtitle-content` **之外**的 `#subtitleDraft`（那里每帧按 cueId 对账，
塞进去会被当残留删掉）。

三条边界都是必要的，不是谨慎：

1. **它是投影，不是 cue**：不入 store、不进导出、不交给总线。Provider 没做过的切点，正是它的
   会话翻译无法归属的切点——给这份文本一个自己的身份，等于把"只剩原文"的 bug 放回去。
2. **按 playhead 截断**：音频腿比观众快整个播放延迟，轨里的文本因此也快。`heard_pcm` 来自播放器
   每 1s 报上来的 `/api/status?playhead=`（`set_viewer_wall_time`），只保留 begin 不晚于它的 unit；
   拿不到可信 playhead（anchor 未收敛）时整条投影不出——猜不得。
3. **跟在屏的 cue 对账**：投影文本被任一在屏 cue 的原文包含、或反过来包含它，就不画。

顺带一问：既然提前上屏的原文已由这条通道负责，双语 Profile 的 7s 硬上限还有必要吗？
（上面那场 76s 重放里，13 条字幕有 5 条是硬切出来的。）
第 5 节把这一问实测掉了：**不能去掉**。投影只解决"观众先看到原文"，解决不了"这一条道
什么时候能放出去做归属"——千问自己把一轮留到 15.8 秒（800 ms 档 36.4 秒），到那时预算已经
花光，切不切都得拒翻译。硬切是两条双语路线唯一共同的上界，留着。

## 3. 协议要点（容易踩的坑）

### Soniox

- 配置帧里 `"api_key"` 是字段，不是 header；音频走 **binary** 帧。
- `translation` 官方 schema 只要 `type`：`{"type":"one_way","target_language":"zh"}` /
  `{"type":"two_way","language_a":"ja","language_b":"zh"}`（下划线，别写成 `one-way`）。
- token 流是一维数组，靠 `translation_status` 区分：`"original"`（被翻译的原文）、
  `"translation"`（译文）、`"none"`（不在语言对里、只转写）。**译文 token 没有 `start_ms`/`end_ms`**，
  所以绝不能进源时间轴 —— Adapter 在 `_split_translation()` 里就分开了。
- `is_final: false` 的译文可能被改写，所以只累加 `is_final: true` 的；未确认的尾巴放在
  `ASREvent.translation_stash`，只做诊断，不参与解析。
- keepalive 是 `{"type":"keepalive"}`（一个词，无下划线）；结束流发**空帧**。
- 服务端帧没有 `type` 字段，错误帧的关闭码是 1000 —— 不能靠关闭码判断失败。
- `context.translation_terms: [{source,target}]` 只在开了翻译时有效。
- `maxNonFinalTokensDurationMs` → `max_non_final_tokens_duration_ms` **服务端认**，下限
  **360 ms**（发 100 直接回 `Field max_non_final_tokens_duration_ms cannot be less than 360.`），
  官方文档里没有它的说明和取值范围。它强制的是"未 final 的 token 变 final"，**不保证发出
  `<end>`**，所以拿它当断句兜底并不可靠；实测设 5000 ms 一场，模型自己的端点全在 4.6 秒内，
  一次都没轮到它生效。Catalog 里因此不填它。

### Qwen LiveTranslate

- 地址用**全局的 `wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=<id>`**。2026-09-21 拿真账号实测：
  只给 API Key 就连得上、出字幕。官方文档印的 `wss://{WorkspaceId}.<region>.maas.aliyuncs.com/...`
  是**另一条可选入口**，不是唯一入口——把它当成必填，就是"千问一句字幕都不出"的第一现场：
  占位符没换 → 会话根本连不上，而报错藏在后台重连日志里，界面上只表现为"没有字幕"。
  `endpoint_url()` 仍然支持它：地址里带 `<workspace-id>` / `{WorkspaceId}` 时，用
  `options.workspaceId`（其次 `DASHSCOPE_WORKSPACE_ID`）替换，两者都没有就抛中文提示。
- **`voice` 必须显式给**，这是第二现场。3.8 服务端自己发下来的会话默认音色是 `Chelsie`，
  而它在第一个生成回合就被拒绝：`<400> InternalError.Algo.InvalidParameter: Voice 'Chelsie'
  is not supported.`，紧接着连接断开——此时一个字节的转写都还没出来。文档写着 3.8 不需要音色、
  默认音色是 `Tina`，实测与文档不符，所以 Adapter 无论是否输出音频都把音色填上（默认 `Tina`）。
  官方说的"只发 `output_modalities:[\"text\"]`"也不足以关掉音频：服务端回显的 `modalities`
  仍是 `[\"text\",\"audio\"]`，因此两个字段名一起发。
- **译文与原文按"第几轮"配对，不能按"转写什么时候到"配对**，这是第三现场。服务端不给 response
  和输入 item 之间的任何关联（response 帧上的 item_id 是助手那条消息的 id，跟原文 item 不是一个），
  而且**每个 VAD 回合恰好一个 response，包括那种没听出内容的空回合**。空回合没有转写增量，
  于是被"转写到发队列"漏掉、又被它自己的空 `completed` 塞回队尾，从这一句起所有译文整体前移一行：
  `おい、真似すんなよ` 配到空译文，上一句却挂着它的中文。现在按 `speech_started` 的先后排队，
  `response.created` 到就取队首（`_next_item()`）。
- 鉴权只有 upgrade header `Authorization: Bearer <key>`；没有 `OpenAI-Beta`。
- **3.8 与 3.5 是两套 schema、两套事件名**，官方明确说不可互换：
  - 3.8 会话字段 `output_modalities` / `translation.language`，事件是 `*.delta`（取 `delta` 字段）。
  - 3.5 会话字段 `modalities` / `input_audio_format` / `sample_rate`，
    事件是 `response.text.text`（`text` + `stash`）、`response.text.done`（`text`）、
    `response.audio_transcript.done`（**`transcript`**）。
  Adapter 按模型 id 前缀选会话 schema，读取时两种事件形状都接（3.5 的 `response.text.text`
  是**整段快照**，按替换处理而不是累加，否则一句重复一遍）。
- 只有单向。双向只在 Soniox 那边有：3.8 的目标语言是会话级设置，源语言由模型自动判断，
  **官方没有给 3.8 任何锁定源语言的字段**（`input_audio_transcription.language` 属于 3.5）。
  所以 `options.sourceLanguage` 只会进 3.5 的会话，填给 3.8 会被丢弃而不是猜个字段塞进去。
  3.8 的 delta 也不带 `language`，因此 `reports_detected_language=False`。
- **字幕在哪一步收口**：`conversation.item.input_audio_transcription.completed`，
  **不是** `input_audio_buffer.speech_stopped`。VAD 停止时转写还没说完，在切点闭合的字幕
  对不上任何 Provider 自己对齐过的边界，结果就是满屏只有原文、没有译文——这是第二现场。
  `speech_stopped` 只取时间信息，不带 caption 证据。
- **完全没有说话人信息**，所以这条 Profile 上不会出现说话人配色。四场真账号直播（2026-09-21/22）
  收到的 1,326 帧里，任何一层都没有 `speaker` / `spk` / `diarization` 这类键，会话回显里也没有
  对应开关，因此 `capabilities.speaker_labels=False` 是实测结论而不是偷懒没接。配色系统本身是通的，
  只是没人给它标签——Soniox / Deepgram / AssemblyAI / Speechmatics / Tencent 那几条会带。
- **不发** `input_audio_buffer.commit`：3.8 的分句是服务端的（`audio.input.turn_detection.type =
  speaker_detection`），官方把手动 commit 归给 3.5 的手动模式；对服务端 VAD 会话发它等于
  从 Provider 正要翻译的那句话中间剪一刀，而这恰好是总线唯一无法重新对齐的情况。
- 必须发 `{"type":"session.finish"}` 并等 `session.finished`，否则最后一句的识别和翻译都会丢。
- **断句静音阈值是这个 Profile 唯一的延迟旋钮**。不问就直接用的服务端默认 `turn_detection` 是
  `server_vad / threshold 0.5 / prefix_padding_ms 300 / silence_duration_ms 800`（这几项是
  2026-09-21 会话建立时**服务端自己回显**的，不是文档里读的），也就是每出一句字幕都要先等 0.8 秒静音。
  Adapter 现在把整份对象原样发回去、只把 `silence_duration_ms` 换成 `silenceDurationMs`
  （默认 300）；
  文档里那条写的是 `audio.input.turn_detection.type = speaker_detection`，实测回显是 `server_vad`，
  以回显为准。逐字段照发而不是只发改动的那一个，因为合并语义官方没写，而少发一个 `create_response`
  就会让会话不再翻译——跟音色那次是同一类事故。
  同一段 70 秒日语直播跑三遍（`.scratch/qwen-livetranslate-live/report_*_sil*.txt`）：
  800→5 段（单段 26 字以上、跨好几句），500→8 段，300→13 段，**每一段原文译文都齐全**，
  "说完→定稿"中位 0.14～0.20 秒不随之变化。调小它换来的是"字幕早 0.3～0.5 秒出现、每条更短更好读"，
  而不是翻译变快。
- **计费（官方 2026-09 价格页 + 实测 usage 字段）**：北京地域音频输入 40 元/百万 token、
  文本输出 100 元、音频输出 160 元、图片输入 3.3 元；音频按输入 7 token/秒、输出 12.5 token/秒换算。
  源语言原文（转写结果）**按输出文本计费**，官方在“计费说明”里写明，早先两处矛盾的说法以此为准。
  本 Profile 默认 `["text"]`：实测一场直播的 `response.done.usage` 里 `output_tokens_details`
  只有 `text_tokens`，没有 `audio_tokens`，也就是不产生 160 元/百万那一档。
  关掉语音输出即省掉整块音频输出费用（连续一小时语音 ≈ 45,000 token ≈ 7.2 元）。

## 4. 配置

Profile 选项一律 camelCase，由 Adapter 映射成wire字段：

| Soniox | 含义 |
|---|---|
| `translationType` | `""` 关闭 / `"one_way"` / `"two_way"` |
| `translationLanguageA` / `B` | 双向的两侧 |
| `translationTerms` | `[{source,target}]` |

| LiveTranslate | 含义 |
|---|---|
| `workspaceId` | 可选。只有把 Base URL 换成按业务空间分配的 `*.maas.aliyuncs.com` 地址时才用；填了会替换地址里的占位符，留空则读 `DASHSCOPE_WORKSPACE_ID`。默认的全局地址不需要它 |
| `voice` | 输出音色，默认 `Tina`。**只出字幕也要填**：服务端默认音色会被 3.8 拒掉，会话在第一句就断 |
| `audioOutput` | 是否同时输出合成语音（额外计费）；关掉时音色仍然要发，原因同上 |
| `sourceLanguage` | 仅 3.5 代生效；留空=自动识别 |
| `silenceDurationMs` | 说完之后静音多久就断句出字幕，默认 **300**（服务端自己给的是 800）。这条 Profile 上唯一能调延迟的参数；调小是因为 Provider 自己收尾的那条字幕一定带译文，我们切出来的那条要靠事后配对 |
| `phrases` | `{源词:译词}` → `translation.corpus.phrases`（只能在 `providers.json` 里手写） |

译文语言不来自 Profile 选项：每次建会话前 `SubtitlePipeline` 把**字幕与弹幕设置里的目标语言**
递给它（`zh-Hans` → `zh`，正好落在官方那张 62 个裸语言码的表上）。

未设 `translationType` 的 Soniox Profile **保持纯识别**：不因为换了协议就悄悄改成翻译计费。

协议下拉分两组，组名直接说清这件事：`端到端双语（识别与翻译在同一会话 · 已实测）` 和
`只做识别（需再配一个翻译模型）`。千问的纯识别早就有独立协议（`dashscope-qwen-realtime`，
即 qwen3-asr-flash-realtime，早年实测过分句阈值），Soniox 以前只有双语那一条——要纯识别
得自己把"翻译模式"调到关闭。现在它有了别名协议 `soniox-realtime-transcribe`：复用同一个
Adapter，只是在构造函数里把 `translationType` 钉成空串，所以卡片上既没有那个开关、也不可能
出现"协议说只识别、请求里却带着 translation"。配套的内置 Profile 是
`soniox-stt-rt-v5-transcribe`（不带 ⭐，因为它不是推荐路径）。

## 5. 断句预算：不做硬切能不能守住 15 秒

要求写进了代码：`caption_chunker.RELEASE_BUDGET_SECONDS`，回归测试是
`tests/test_caption_chunker.py::ReleaseBudgetTests`。一条字幕必须在播放头还追得上它的
时候放出来，而播放器最少只给用户留 `server.MIN_TARGET_DELAY_SECONDS = 11s`，所以
**预算是 11 秒，不是 15 秒**——15 秒只是默认档位，测试要求两个数一起动。
`HARD_DEADLINE_SECONDS = 7s` 就是这条预算的执行方式，剩下 4 秒给"放出→对齐→上屏"。

`segments_only` 下 chunker 不自己切，一条道只等 Provider 收尾，所以"能不能去掉硬切"
完全等于"Provider 会不会在预算内自己断句"。2026-09-21 拿真账号、真日语各跑了一场
（`.scratch/bilingual-turn-deadline/probe.py --provider soniox|qwen [--raw]`，
`summarize.py` 出表，结果 JSON 留在 `out/`）：

| 场景 | 最长一轮(语音秒) | 说完→收尾(秒) | 开口→整轮到手(秒) | 看到什么 |
|---|---|---|---|---|
| Soniox，60s 剪掉所有停顿 | 4.20 | ≤1.67（均 1.06） | 最远 5.15（均 3.18） | 15/15 轮全部自己闭合 |
| Soniox，60s 原始带停顿 | 4.56 | ≤1.72 | 最远 5.56（均 3.45） | 13 轮里 12 轮闭合，最后 0.8 秒开的那轮随流一起结束 |
| Qwen，60s 剪掉所有停顿，`silenceDurationMs=500` | — | — | — | 60 秒里给了 16 次收尾，从开口到"有内容可发"最远 3.92 秒 |
| Qwen，60s 原始带停顿，500 ms | 3.89 | 0.27–0.47 | 最远 4.16（均 2.80） | 最远的一次是开口后 3.86 秒才有内容可发 |
| Qwen，2026-09-21 真实直播里服务端自报的一轮，500 ms | **15.7 / 15.8** | — | **≈16.0** | 已经越过 11 秒预算 |
| Qwen，同一场直播，800 ms（服务端默认） | **36.4** | — | **≈36.6** | 同上，三倍超预算 |

"开口→整轮到手"这一列才是预算的度量：从观众的播放头碰到这句话的第一个字起，到这一整句
被 Provider 判完、可以放出去，隔了多久。要分清的正是这两件事——**开口之后多久拿到字**
（两家都在 4 秒内，所以原文先上屏那条路一直是通的）和**一轮多久才被判结束**
（Soniox ≤4.6 秒，千问在真实直播录音里能到 15.8 秒、800 ms 档 36.4 秒）。硬切管的是后者，
因为 `segments_only` 下一条道要等到 Provider 判完这一轮才放行。

另外别拿探针里的"未关闭 item 数"当结论：千问每轮会同时给一个输入 item 和一个输出 item，
只有输入侧才有 `transcription.completed`，所以 24 个 item 里永远有 10 来个"没关闭"——
那是探针按 item id 数出来的，不是服务端没断句。

结论：**能不能去掉硬切 = Provider 会不会在预算内自己收尾**。千问实测不会，而且它收尾是
收在"说到最后"那一刻——那时观众的播放头早就走完了开头。所以硬切保留，它是两条双语路线
**唯一共同**的上界；Soniox 那个 `maxNonFinalTokensDurationMs` 只覆盖自己、语义又没文档，
不拿它替换硬切。

### 5.1 硬切曾经只限"等了多久"，不限"盖了多少语音"（已修，剩一条例外）

把四场真实直播（`.scratch/qwen-livetranslate-live/events_*.json`）离线重放进真管线，
每条字幕按"是谁切的"分类（2026-09-22）：

| 直播 | 字幕 | 自己收尾切的 | 硬切出来的 | 超过 7s 的字幕 |
|---|---|---|---|---|
| 默认 | 8 | 7 条有译文 / 0 条没有 | 1 条有译文 / 0 条没有 | 3 条（最长 12.4s） |
| 300 ms | 14 | 12 / 1 | 0 / 1 | 2 条（12.0s, 8.0s） |
| 500 ms | 13 | 5 / 3 | **0 / 5** | 3 条（12.0s, 11.1s） |
| 800 ms | 9 | 2 / 1 | **1 / 5** | 6 条（最长 12.2s） |

（表里"有译文 / 只剩原文"是**修之前**的状态。）

两件事：

1. **硬切（修之前）只限"等了多久"，不限"这一条盖了多少秒语音"**。`caption.hard_deadline` 按单调
   墙上时间算（第一条文本到货时 `now + 7`），而到期时 `_close_caption` 一次把整条道倒空——
   所以道里攒了多少语音，切出来的字幕就有多长。四场里每场都出现超过 7s 的字幕，而且
   11.1s、11.9s 这种正是 `hard_deadline` 自己切出来的。**为什么 7 秒墙钟里能攒下 11 秒语音**：
   服务端报的是每个词的时间戳，卡死之后的追赶会在几秒墙上时间里送进一批时间戳横跨十几秒的词
   （重放里那条 11.2s 的字幕就是这么来的：它 12 个 unit 在同一次轮询里到齐）。也就是说
   "语音时长"和"墙钟等待"本来就是两个量，只按后者设限等于没设限；证据比墙上时间跑得快在真机上
   是常态，卡死后的追赶就是这种时刻。（重放的墙上时钟和录音的音频时钟本身差着 ~4 秒，合成用例
   里两者 1:1 时切出来不超过 8s：见 `ReleaseBudgetTests`。真机上还没有单独复测过这一条。）
2. **凡是被我们硬切出来的字幕，几乎都拿不到译文**（500 ms 场 0/5，800 ms 场 1/6）。
   2026-09-22 已修。原因在第 2 节的
   归属规则：总线只认 Provider 自己给过的对齐点，而千问从不给对齐点——Soniox 给
   （`translation_anchors`），所以它被硬切也不丢中文，千问一被硬切就整条只剩原文。
   修法：`_LiveTranslateStream._settle_boundary` 在原文流和译文流**各自每次增长时**记下
   "此刻已到的原文 ↔ 此刻已到的译文"，按 Soniox 同一个字段交给总线——两边都是服务端
   自己的输出，不是我们插进来的片段。四场重放再跑一遍：**丢译文 0 条**
   （原来 0 / 2 / 8 / 6），硬切的 1+1+5+6 条全部带上了中文。
   代价要说清楚：千问的译文比转写慢半拍到一句，所以被切那一条的中文**可能少掉结尾一个从句**，
   而那半句会出现在下一条的开头。语义不丢、不串题，只是切缝两边各差一点；比起整条没有中文，
   这笔账是划算的。Provider 自己收尾的那些条仍然是精确配对。

**(a) 已做（2026-09-22）**：**每一条放行路径**都按"盖了多少语音"设限了。`expire` 的到期分支、
模型自己收尾的 `utterance_endpoint`、换 item 的收尾、`flush_utterance`，现在统统走
`_release()`——一次把这条道倒空，倒出来的每一片不超过 `begin + HARD_DEADLINE_SECONDS` 的语音，
不再是"到期时整道倒出去，攒多长就切多长"。四场重放（`--delay 11`，App 允许的最小值）：

| 直播 | 字幕 | Provider 自己收尾 | 我们硬切 | 超过 7s | 丢译文 | 放出时已晚于自身终点 |
|---|---|---|---|---|---|---|
| 默认 | 10 | 9 | 1 | 1 条（11.2s） | 0 | 0/10（最晚的一条仍早 4.2s） |
| 300 ms | 17 | 15 | 2 | 1 条（8.0s） | 0 | 0/17（早 3.8s） |
| 500 ms | 15 | 8 | 7 | 1 条（12.4s） | 0 | 0/15（早 5.1s） |
| 800 ms | 15 | 4 | 11 | 1 条（12.1s） | 0 | 0/15（早 7.1s） |

超 7s 的字幕 3/3/3/6 → **每场 1 条**，字幕数 8/14/13/9 → 10/17/15/15，**丢译文仍然是 0**。
最后一列是"观众会不会干脆看不到这条"的判据：播放器只在发布时间已经晚于这条自己的终点
3.5 秒（1.5s 尾巴 + 2.0s 宽限，`web-player/subtitle-scheduler.js:83-104`）时才丢它，
而四场里最晚的一条也比自己的终点早 3.8 秒以上——没有一条字幕会因为"太长所以放得太晚"消失。
观众真正错过的是**开头**：11s 档每场 1~2 条错过 1.5~3.9 秒，屏上仍剩 3.8~10.4 秒可读；
15s 档（默认）实测同一场重放是 **0/10 条**错过开头，而字幕条数、超 7s 条数、丢译文条数全都不变
——延迟只挪观众，不挪发布时机，所以这两档之间换的不是"切不切"，只是"来不来得及"。

同一次改动撞出下面 (b) 说的那个坑，而且比预想的更硬：第一版无脑按预算切，默认那场把
`悪乗り`（一个 unit）单独切了出去，结果**前后两条同时失去译文**——切点不是 Provider 站立过的
对齐点，ledger 的游标就再也对不上任何 boundary，一条长字幕的"丢译文"变成了两条。于是加了现在
这条护栏：**预算落在 lane 的第一个 unit 上时不切**，整条照旧放出去（默认那场因此仍是 11.2s）。

**(b) 未做**：上面那 1 条就是护栏留下的，**这是权宜，不是终局**。真正的条件是"切点必须是
Provider 给过 boundary 的前缀"，而现在 chunker 只看得到 word unit 的边界、看不到 ledger 的
对齐点集合，两者不重合（`_take` 要求整段文本与某个 boundary 的前缀**逐字相等**，而千问的译文
增量不按 `words` 数组的边界来），所以它只能选择"第一个 unit 就已超预算 → 干脆不切"。
第一场撞到的就是这个盲区；把它去掉需要一条小接口（chunker 能问 ledger"这个前缀你配过对吗"），
而不是重写切分。

**(c) 副作用，未处理**：把一条长道切成多片之后，四场里出现了 3 条时长为 0.00s 的字幕
（300ms 场 1 条、500ms 场 2 条）——同一条道里相邻两片的 unit 报了同一个时间点。它照样会上屏
（播放器给 1.2~1.5s 的 `hold` 尾巴），但时间轴上和前后条重叠。切点改成 (b) 的对齐点后应该一并消失，
所以先记着不动。

## 6. 未验证 / 待实测

- **Qwen LiveTranslate 已经真账号实测过**（2026-09-21，`.scratch/qwen-livetranslate-live/`）：
  全局地址 + 只给 API Key 建会话成功，60 秒真实日语直播 → 8 条字幕**原文与译文同时到手**，
  "说完→定稿"中位 0.19s、最差 0.33s。上面三条"现场"（占位符地址、默认音色、按轮次配对）
  就是这一场直播里抓出来的，`tier="provider_claimed"` 里的收发形状已按实测改过。
  Soniox 的双语会话现在也有真账号证据了（上面断句预算那五场），原文与译文同样每条同时到手。
  那场直播的事件流还被离线重放过（`.scratch/qwen-livetranslate-live/replay-captured-session.py`：
  本地 WS 按录制时间差重发原始帧，跑真 Adapter + 真 `SubtitlePipeline`），修匹配键之前 8 条字幕
  5 条只剩原文，之后 8/8 双语齐全——**Adapter 层"两边都有"不等于字幕层"两边都在"**，
  中间那次静默丢弃只有重放才看得见。
- Soniox 译文 token 的 `is_final` 改写语义是统一 token 模型推出来的，官方没有逐字说明译文；
  接真 key 跑一场直播确认一次。
- Qwen LiveTranslate 的**最大会话时长**与**并发上限**官方未公开（只给了 RPM 10 / TPM 100k）。
- **长会话到底怎么计费，没测出来**：实测每一轮 `response.done.usage` 报的输入音频 token 是
  从会话开始到那一轮为止的**累计值**（4.2s→28、71.1s→434，正好等于 7 × 累计语音秒数），
  而上下文最大输入 49,152 token 只相当于约 7,000 秒音频。如果按实时全模态那套“每轮把
  历史上下文一起算”的规则，一小时连续直播的费用会远高于按 67 秒线性外推的结果
  （那 67 秒：只算一次 0.033 元，逐轮累加 0.212 元）。要分辨只需跑满 10 分钟再去百炼
  控制台读那一段的 token 扣减量——两种口径差 6～7 倍，一眼可辨。
- 3.5 代也实测过（同一场直播，音色发与不发都成功、结果一致），但它不在默认 Profile 里，
  源语言锁定那条路径没跑过长直播。
