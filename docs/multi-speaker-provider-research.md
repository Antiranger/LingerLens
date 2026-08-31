# 实时多说话人分离供应商调研报告

> 调研时间：2026-08-30
> 背景：`docs/multi-speaker-handoff.md` 中的多说话人需求（区分说话人 + 分离字幕），
> 本文回答两个问题：① Gummy realtime v1 到底支不支持多说话人识别+翻译；
> ② 哪些方案能满足「实时说话人分离 + 源语言至少 en/ja/ko」的刚需。
>
> 所有关键结论都带来源；竞品自家 benchmark 与官方事实已分开标注。

---

## 0. 一句话结论

**Gummy realtime v1 不支持说话人分离。** 它能「听清所有人并翻译」（多说话人音频没问题，
所有人的话都会被识别和翻译），但**不知道哪句话是谁说的**——响应里没有任何说话人标识参数。
交接文档 §2.1 的判断经本轮独立核实**成立**。刚需必须换或加供应商，见 §3 推荐。

---

## 1. Gummy 核实过程

三处独立证据，全部来自阿里云官方文档：

1. **Gummy WebSocket API / Python SDK / iOS SDK 的完整参数表里没有任何 diarization 参数**。
   可配项只有：`source_language`、`transcription_enabled`、`translation_enabled`、
   `translation_target_languages`、`max_end_silence`、`vocabulary_id`、`sample_rate`、`format` 等。
   来源：
   - [Gummy 实时语音识别、翻译 WebSocket API](https://help.aliyun.com/zh/model-studio/real-time-websocket-api)
   - [Python SDK 调用 Gummy](https://help.aliyun.com/zh/model-studio/real-time-python-sdk)
   - [Gummy iOS SDK](https://help.aliyun.com/zh/model-studio/ios-sdk-for-gummy)
2. **百炼 ASR 模型能力矩阵：所有 Real-time 模型的 Speaker diarization 一栏全部 Unsupported**
   （`qwen3-asr-flash-realtime`、`fun-asr-realtime` 系列等）。支持 diarization 的只有
   `paraformer-v2`、`fun-asr` 等离线 HTTP 文件转写。直播用不了。
   来源：[ASR 模型矩阵（国际站）](https://www.alibabacloud.com/help/en/model-studio/asr-model)
3. **没有官方文档提及实时模式会填充 `words[].speaker_id`**。交接文档 §2.1 的这个开放问题
   本轮仍未找到文档级答案，只能维持「接通后实测」的 spike 项，但不要抱期望。

Gummy 本身的强项保持成立（官方参数表确认的翻译对）：

- **日语（ja）→ 中文（zh）✓**
- **韩语（ko）→ 中文（zh）✓**
- **英语（en）→ 中文（zh）✓**
- 价格 0.00015 元/秒（≈0.54 元/小时）
- 注意：官方明确「暂不支持同时翻译为多种语言，请仅设置一个目标语言」

---

## 1.5 Gummy 补充核查（第二轮，2026-08-30）

**上下文机制（回答「模型会自己维护上下文吗」）：**

- API 参数表**没有任何上下文/翻译历史参数**（对比：`qwen3-asr` 系列有「上下文增强」功能，
  可传对话历史；Gummy 只有 `vocabulary_id` 热词）
- 官方 FAQ「可能影响识别准确率的因素」第 4 条原文：
  「**上下文理解：缺乏对对话上下文的理解可能会导致误解，尤其是在含义模糊或依赖于上下文的情境中**」
  ——官方自认上下文依赖是弱点
- 翻译以句为单位返回（`sentence_end` + `translations[]`），无跨句窗口说明
- **工程假设：不推指望 Gummy 替你维护翻译上下文**（内部是否有流式隐藏状态不透明、不可控）。
  日语主语省略/代词消解是最大风险点。
- **架构矛盾提醒**：直译模式下 per-speaker 上下文没有注入点（无法喳给 Gummy）。
  「直译省一跳」与「每人独立上下文」两个目标有张力 → 建议保留两段式作为可选模式，
  UI 模式 A（两段式，带上下文）/ B（直译，低延迟）并存，正好与用户「模式单独列出」的要求吻合。

**计费细节（修正成本结论）：**

- 官方文档：「识别与翻译分别计费，费用按各自调用量独立计算，**两项服务的单价一致**」
- 即直译全开可能是 2× 单价 → Gummy 的成本优势主要来自**省掉 qwen-mt 跳数与延迟**，
  而非交接文档 §2.2 所说的「不到一半」；实际单价需在模型与价格页核实

**其他要点：**

- 热词支持「固定译法」（`text` + `target_lang` + 指定 `translation`）——人名/游戏术语一致性抓手
- 双模型：`gummy-realtime-v1`（长语音/直播）与 `gummy-chat-v1`（短语音，停顿敏感）——选 realtime
- 阿里系后续：Qwen3.5-Livetranslate（60 语言互译）可作质量不达标时的同系备胎

## 2. 候选全景对比

刚需定义：**实时**（直播流，非离线转写）+ **说话人分离**（能拿到每句的 speaker 标识）
+ **源语言至少覆盖 en/ja/ko**。价格单位统一为人民币约数（1 USD≈7.2，1 EUR≈8.1）。

| 方案 | 实时分离 | en | ja | ko | 说话人数 | 延迟 | 价格（约） | 直译 zh | 备注 |
|---|---|---|---|---|---|---|---|---|---|
| **pyannoteAI Live-1** | ✅ GA | ✅ | ✅ | ✅ | 8 | <300ms | €0.198/h≈1.6元/h | ❌（纯分离） | **语言无关**：分离不依赖语言，与任意 STT 并行 |
| **Soniox v5** | ✅ 全语言 | ✅ | ✅ | ✅ | 15 | sub-500ms | ~$0.25/h（三方口径） | ✅ 3600 语言对 | 一站式 STT+分离+实时翻译；ko/ja 官方点名为重点语言 |
| **Speechmatics v2** | ✅ 全语言 | ✅ | ✅ | ✅ | 多人 | — | ~$0.8/h（竞品口径，旧） | 部分（69 对围绕英语） | 日语 96% 词准确率（自家口径）；DER 31.3%（pyannote 口径） |
| **AssemblyAI U-3.5 Pro Realtime** | ✅ per-word | ✅ | ✅ | ❌ | 10+ | ~300ms | $0.45/h≈3.2元/h+分离加价 | ❌ | 流式语言集 18/19 种**含 ja 不含 ko**；ko Realtime 官方标注 Coming soon |
| **Gladia Solaria** | ✅ 实时 | ✅ | ✅ | ✅ | 多人 | ~300ms | $0.25–0.75/h all-in | 加价项 | 实时分离基于 pyannote；无独立 DER 口径 |
| **Deepgram Nova-3** | ✅（流式） | ✅ | ✅ | ✅ | 10 | ~300ms | ~$0.55/h | ❌ | Nova-3 已含 ko（streaming+batch）；流式分离×ja/ko 组合未逐页核实；DER 39.1%（pyannote 口径） |
| ElevenLabs Scribe v2 Realtime | ❌ | — | — | — | — | 150ms | $0.28/h | ❌ | **官方 FAQ 明确 diarization 是 batch 专属功能**，Realtime 无分离 |
| Amazon Transcribe | ✅ | 仅 en-US | ❌ | ❌ | ~5 | — | — | ❌ | 流式分离仅美式英语（竞品口径） |
| Azure 会议转写 | 部分 | 部分 | ❓ | ❓ | 10 | — | — | ❌ | 实时对话转写语言集小，ja/ko 覆盖未核实（本轮缺口） |
| 腾讯云 实时识别 V2 | ✅ | ✅（中英） | ❌ | ❌ | 2+ | — | — | ❌ | `16k_zh_en_speaker_2.0` 引擎，声纹聚类式实时分离，**仅中英**；另有 16k_ja/16k_ko/16k_multi_lang 识别引擎但**不带分离** |
| 讯飞 实时转写大模型 | ✅ | ✅ | ❓ | ❓ | 2+ | — | — | 有 | role_type=2 盲分/声纹分离；`autominor` 37 语种免切（含 ja/ko）但**与分离的兼容性官方未明确**，且 autominor 需商务对接 |
| NVIDIA Streaming Sortformer（本地） | ✅ 开源 | ✅ | ❓ | ❓ | 2–4 | GPU | 免费+GPU | ❌ | en 验证/zh 演示，ja/ko 未验证；需自建推理 |
| 阿里云百炼（现供应商） | ❌ | — | — | — | — | — | — | Gummy ✓ | 本轮再次核实：所有实时模型 diarization Unsupported |

### 关键证据细节

**pyannoteAI Live-1**（[官方模型文档](https://docs.pyannote.ai/models)、
[GA 公告](https://www.pyannote.ai/changelog)、
[发布博客](https://www.pyannote.ai/blog/introducing-live-1-streaming-diarization)）：

- 2026-07-07 GA。16kHz 单声道、100ms 分块、WebSocket 输入；
  输出 `diarization_speaker_start` / `diarization_speaker_end` 事件（时间戳+speaker 标签）
- 每流最多 8 说话人、最长 5 小时（直播超长时要处理重连续流）
- 官方定位就是「与任意流式 STT 并行，同一路音频分两路，按时间戳对齐」——和 LagLingo 的架构天然匹配
- 价格：Developer €0.198/h、Starter €0.170/h（changelog 明确列出）
- **DIHARD III 流式分离 benchmark（pyannote 自家评测，注意口径）**：
  DER 全语言 pyannote 19.8% vs Speechmatics RT v2 31.3% vs Deepgram Nova-3 39.1% vs
  AssemblyAI Universal Streaming v3 39.2%
  （[来源](https://www.pyannote.ai/blog/streaming-diarization-benchmark)）

**Soniox v5**（[官方分离文档](https://soniox.com/docs/stt/concepts/speaker-diarization)、
[v5 发布](https://soniox.com/blog/soniox-v5-real-time)）：

- 「Speaker diarization is available for **all supported languages**」，每会话最多 15 说话人
- 官方明示：实时分离精度低于异步（低延迟约束下的固有代价）
- 60+ 语言，官方点名 Korean/Japanese 是重点优化的「underserved」语言；
  自家口径 ko 词错率 1.25%
- 实时语音翻译 3,600 语言对（60+ 语言任意互译，含 zh 目标），翻译在句尾前就出低延迟结果
- 价格 ~$0.0042/min 来自三方测评（novascribe），未在官方定价页核实

**AssemblyAI**（[流式产品页](https://www.assemblyai.com/products/streaming-speech-to-text)、
[changelog](https://www.assemblyai.com/changelog)、
[韩语页](https://www.assemblyai.com/languages/korean)）：

- 流式刚升级 per-word speaker labels（Universal-3 Pro Streaming / Universal-Streaming）
- **U-3.5 Pro Realtime 18 语言：含 Japanese，不含 Korean**；流式全集 19 种同样无 ko；
  Korean 页 Realtime 列标注 Coming soon
- batch 分离覆盖 95 语言（含 ja/ko/en），但那是离线，直播用不上
- 交接文档 §2.3 里「流式分离最多 10 人」仍然成立；「日语分离质量未知」的判断也维持

**ElevenLabs**（[官方 FAQ](https://elevenlabs.io/realtime-speech-to-text-api)）：

- 原文：「Scribe v2 (batch) is built for recorded audio with **additional features like
  speaker diarization**... Use Realtime for agents and live applications」
- 即 **Realtime 无 diarization**。交接文档 §2.3 里「号称 30 语言、最多 32 人」的说法
  查无官方实据，应从候选里删除。

**国内厂商**：

- 腾讯云实时语音识别 V2（WebSocket，`16k_zh_en_speaker_2.0`）确实做实时说话人分离
  （声纹聚类，边说边出角色），但**分离引擎只有中英**。识别侧另有 `16k_ja`、`16k_ko`、
  `16k_multi_lang`（15 语种自动识别，含 ja/ko/en）等引擎，但这些引擎**不带分离**——
  分离与多语种识别是两套绑定，不能组合。
  [V2 文档](https://cloud.tencent.com/document/product/1093/131127)、
  [引擎列表](https://cloud.tencent.com/document/product/1093/48982)
- 讯飞实时语音转写大模型：`role_type=2` 实时角色分离（盲分，可配注册声纹）；
  `lang=autominor` 支持 **37 语种免切识别，明确含 ja/ko/en**。
  **但文档没有写明 autominor 与 role_type=2 能否同时开启**（标准版 rtasr 的角色分离
  只出现在中英模式下），且 autominor「需单独付费、人工对接」。
  这是国内唯一可能的 ja/ko 实时分离路径，**值得一次工单/商务确认**。
  [大模型版文档](https://www.xfyun.cn/doc/spark/asr_llm/rtasr_llm.html)

---

## 3. 推荐方案（按优先级）

### 方案 A（推荐）：Gummy 直译 + pyannote Live-1 分离，双流并行

```
                         ┌─> Gummy realtime (ja/ko/en → zh 直译+时间戳)
16k mono PCM ──分叉──────┤
                         └─> pyannote Live-1 (speaker start/end 事件)
                                    │
                     按时间戳把 speaker 归属到 Gummy 的句子上
```

- **不动交接文档的阶段 A/B**：Gummy 直译、按说话人分桶上下文全部照旧；
  speaker 不再来自 ASR 响应，而是由独立的分离流按 `begin/end_time` 对齐填充 `ASREvent.speaker`
- 分离与语言无关 → en/ja/ko 以及混说天然全覆盖，将来加任何源语言都不用重评分离
- DER 口径目前流式最优（19.8%，自家 benchmark）
- 成本：Gummy 0.54 元/h + Live-1 ≈1.6 元/h ≈ **2.2 元/h**，比现在的
  qwen3-asr（1.19 元/h）+ qwen-mt 略高，但换来了分离能力
- 风险：Live-1 是 2026-07 才 GA 的新产品；两条流各有自己的时间基准，对齐误差要 spike；
  每流最长 5 小时，直播需要续流逻辑

### 方案 B：整体换 Soniox v5（一站式）

- STT + 分离 + 实时翻译（含 zh 目标）单供应商，单条 WebSocket，协议最简单
- 官方把 ko/ja 当重点语言优化，并有实时翻译能力
- 风险：日韩分离质量没有独立 benchmark；丢掉 Gummy 的价格与已验证的直译质量；
  整个 ASR 层重写

### 方案 C：Speechmatics

- 56+ 语言实时全功能、企业级、日语精度宣传口径好（96%，自家口径）
- 风险：价格贵（竞品口径 ~$0.8/h）；DER 31.3%（pyannote 口径）；直译对围绕英语

### 淘汰记录

| 候选 | 淘汰理由 |
|---|---|
| ElevenLabs Scribe v2 Realtime | 官方确认 Realtime 无 diarization（交接文档里的线索不成立） |
| AssemblyAI | 流式无 ko（Coming soon）；en/ja 可用，可作备选 |
| Amazon Transcribe | 流式分离仅 en-US |
| Azure | 实时对话转写语言覆盖存疑，本轮未核实出 ja/ko 支持 |
| 腾讯云 / 讯飞 | 实时分离仅绑定中英引擎；讯飞 autominor（含 ja/ko）与分离的兼容性未明确——唯一待确认的国内路径，见 §2
| 本地 FunASR/pyannote.audio/Sortformer | 仍是备胎：Sortformer 仅 en 验证/zh 演示；自建成本高 |

---

## 4. 对交接文档（multi-speaker-handoff.md）的修订建议

1. **§2.3 表格更新**：
   - ElevenLabs 行改为「❌ 已核实：Realtime 无分离」
   - 新增 pyannoteAI Live-1 行（语言无关、8 人、<300ms、€0.198/h）
   - 新增 Soniox v5 行（全语言分离 + 实时翻译 zh）
   - AssemblyAI 行补充「流式无 ko」
2. **§6 spike 顺序建议改为**：
   1. Gummy `words[].speaker_id` 是否填充（接完阶段 A 顺手看，预期为空）
   2. **pyannote Live-1 用真实直播 clip（含 BGM 段）测误分离率**——用 §6 的抠音频命令
   3. 双流时间对齐误差：Live-1 事件时间戳 vs Gummy `begin_time/end_time` 的对齐精度
      （复用 `compare-timing.py` 思路）
   4. （可选）Soniox 同一 clip 对比，作为方案 B 的数据
3. **§4 代码设计补充**：speaker 来源从「ASR 响应字段」扩展为「可插拔的分离器接口」
   （`DiarizerProvider`），Gummy 模式下由 Live-1 对齐层填充；`speaker=None` 桶逻辑不变。
4. **陷阱 1（标签回溯修正）仍然适用**：Live-1 文档未说明是否回溯修正历史标签，spike 时验证。

---

## 5. 残留风险（决策点）

- **直播 BGM 误分离**：交接文档 §2.4 的实测（30s 游戏 BGM 被判人声）对任何分离器都成立。
  流式 DER 最优也 ~20%，意味着约每 5 句可能错 1 句归属；若一段独白被拆成多个假说话人，
  按说话人分桶的上下文反而比全局上下文更差。**spike 不过关就不上线阶段 C**。
- **pyannote benchmark 是自家口径**：横向排名未必独立可信，但「流式分离普遍比 batch 差很多、
  且各家 DER 都在 20–40% 区间」这个量级判断是稳的。
- Soniox/Speechmatics/Deepgram 价格为三方或旧口径，签约前需官方报价复核。

---

## 6. 来源清单

官方（一手）：

- 阿里云：Gummy WS API（help.aliyun.com/zh/model-studio/real-time-websocket-api）、
  Python SDK（real-time-python-sdk）、iOS SDK（ios-sdk-for-gummy）、
  ASR 模型矩阵
- pyannoteAI：docs.pyannote.ai/models、changelog（GA+定价）、
  introducing-live-1 博客、streaming-diarization-benchmark 博客
- Soniox：docs.soniox.com（deployment、speaker-diarization）、soniox.com/blog（v5 发布）
- AssemblyAI：products/streaming-speech-to-text、changelog、
  languages/korean、blog（diarization 新语言、multilingual streaming）
- Speechmatics：product/real-time、speech-to-text/japanese、speech-to-text/korean、
  实时翻译发布新闻
- Gladia：docs.gladia.io（supported-languages）、gladia.io 博客（对比页）
- ElevenLabs：realtime-speech-to-text-api（FAQ）
- Deepgram：官方博客（Nova-3 发布、11 新语言）、GitHub discussion #1097
- 腾讯云：cloud.tencent.com/document/product/1093/131127（实时识别 V2）
- 讯飞：xfyun.cn/doc/spark/asr_llm/rtasr_llm（实时转写大模型）

三方/社区（二手，已标注口径）：

- novascribe.ai（Soniox 价格、diarization 工具盘点）
- Picovoice 产品页（Amazon 流式分离仅 en-US 的对比口径）
- Reddit r/speechtech（流式分离实际体验讨论，未深读正文）

---

## 7. 覆盖与缺口

- **已覆盖**：官方文档与官方博客（阿里云、pyannote、Soniox、AssemblyAI、Speechmatics、
  Gladia、ElevenLabs、Deepgram、腾讯云、讯飞）+ GitHub discussion + 竞品 benchmark 交叉
- **未覆盖 / 失败项**：
  - `fetch_url` 对阿里云与 AssemblyAI 域名持续失败（jina 后端故障），
    官方正文改经搜索引擎 advanced 摘要获取，关键参数表已逐字核对
  - Azure 实时对话转写的语言矩阵未核实（无强证据前不列入推荐）
  - Reddit 帖子只读标题未读正文；各家 API 未实际试跑（需要 key，属下一步 spike）
  - Soniox / Speechmatics 官方定价页未直接读取，价格标注为三方口径
- **停止原因**：两个核心问题（Gummy 能力边界、满足刚需的候选集）已有官方一手证据闭环，
  剩余不确定性（实测 DER、对齐误差、Live-1 是否回溯修正）只能靠 spike 消除，搜索无法替代。

---

## 8. 第三轮增量（2026-08-30）：国内「纯分离」方案核查

背景变化：放弃 Gummy 直译与 per-speaker 上下文，回到「qwen3-asr 识别 +
qwen-mt 全局上下文翻译」；分离只做 UI 展示。核心问题：**国内有没有只做说话人分离的模型？**

### 结论：国内没有 diarization-only 的流式云 API，但有两条可行路

**路 A｜腾讯云实时识别 V2 当「分离信号源」用（国内商用，零工程）**

- 引擎 `16k_zh_en_speaker_2.0`（大模型 2.0 版）：中英粤+30 方言识别 + **默认开启话者分离**，
  返回 `sentences[].speaker_id`（0–9）+ `start_time/end_time`
- 用法：同一路 PCM 分叉——qwen3-asr 出多语种文本，腾讯云 V2 只取 speaker_id 和时间戳，
  **它的识别文本直接丢弃**
- 依据：声纹聚类理论上语言无关（音色 ≠ 语言），日/韩人声的音色区分大概率可用；
  但其声纹模型以中英语料训练为主，**日语/韩语音频下的聚类质量必须实测**
- 成本：按识别时长计费（标准版实时识别 3.2 元/h 起，2.0 版单价见计费页；
  对照 pyannote Live-1 约 1.6 元/h，贵约一倍，但国内付款无障碍）
- 风险：纯日语音频喂给中英引擎，VAD/断句行为未知（需实测是否照常出句+speaker）

**路 B｜sherpa-onnx 本地管道（国内开源主导，零充值，中等工程量）**

- 组件：sherpa-onnx（k2-fsa/小米系）+ `pyannote-segmentation-3.0` ONNX（6.6MB）+
  **3D-Speaker CAM++（阿里达摩院模型）**，全 CPU、无 GPU 依赖
- 官方管道是**离线** diarization（VAD→分割→嵌入→聚类），CPU RTF ~0.24
  （3DSpeaker int8）；社区同类纯 CPU 管道（Silero VAD + WeSpeaker + 谱聚类）
  RTF 低至 0.12，VoxConverse DER ~10.8%
- RTF < 1 意味着可以滚动分块追直播（延迟 ≈ 回看窗口 3–5s），但要**自己做**：
  滚动 buffer、在线增量聚类（diart 式）、与 ASR 句子时间戳对齐、标签稳定性
- handoff doc §2.3 原判「要自己做聚类与对齐、CPU 开销」—— sherpa-onnx 已把
  模型+管道打包，工程量比当时预估低，但流式壳仍是自研部分

**对照组（可选）**：pyannoteAI 有 "No credit card required" 免费试用，
spike 阶段可白嫖当质量基准；生产充值（欧洲公司/信用卡）确实是障碍，顾虑成立。

### 推荐动作

1. 同一真实 clip（含日/韩轮流发言 + BGM 段）跑三个分离源并排对比：
   sherpa-onnx 本地 / 腾讯云 V2 信号源 / pyannoteAI 免费试用
2. 量化：轮流段 speaker 归属准确率、假说话人数、与 qwen3-asr 句子边界对齐误差
3. 决策：开源质量够 → 全国内供应链（qwen3-asr + qwen-mt + 本地分离）；
   不够且腾讯 V2 日韩分离也不行 → 再回头解决充值问题

### 顺带修正：全局上下文 + 说话人前缀

不做分桶后仍可零成本改善翻译：拼接全局 10 句历史时给每句加说话人前缀
（`A: ...` / `B: ...`），qwen-mt 能据此消解指代——不需要 SpeakerContexts 分桶，
只是 history 拼接格式变化。

来源：腾讯云计费概述（1093/35686）、V2 文档（1093/131127）、话者分离实践
（1093/130881）、sherpa-onnx discussion #3233（RTF/CPU 事实）、
OpenWhispr 博客（pyannote-3.0 ONNX + CAM++ 全 CPU 栈）、HN diarize
（CPU RTF 0.12 / DER 10.8%）、pyannote.ai 首页（免费试用）
