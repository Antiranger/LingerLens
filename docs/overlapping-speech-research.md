# 重叠语音（多人同时说话）实时识别专项调研

> 调研时间：2026-08-30
> 问题：**四五个主播直播时同时说话，有没有 ASR API 能把每个人的字幕都识别出来？**
> 前置阅读：`docs/multi-speaker-provider-research.md`（供应商筛选，本文聚焦重叠场景）
> 所有关键结论带来源；官方口径与营销口径、二手转述已分开标注。

---

## 0. 一句话结论

**没有。** 在「单声道混流 + 实时 + 重叠」这三个条件叠加下，目前**没有任何商用云 API
能把同时说话的每个人的字幕完整分开识别**——这是学术界和工业界公认的多说话人 ASR
最难未解问题。现在能买到的最好能力是：

1. **非重叠段**（轮流发言，正常直播约 80–90% 的时间）：说话人归属基本可靠；
2. **短暂抢话/重叠段（1–3 秒）**：文字归给主导说话人、或标 UNKNOWN、或混成一串，
   被压住那个人的话基本丢失；
3. **持续同时说话**（合唱、吵架、齐声）：内容本身无法完整恢复，任何方案都不行。

唯一「真正每人一路字幕」的公开方案是 **NVIDIA MultiTalker ASR**（开源、自建 GPU、
英语为主），没有云 API 形态。详见 §3。

---

## 1. 为什么这么难（决定了「能不能等以后解决」）

- **声纹特征被破坏**：重叠时两个人的频谱混在一起，声纹嵌入（voiceprint embedding）
  不再是任何一个人的特征。腾讯云官宣原文：「语音重叠（多人同时说话）……会严重破坏
  声纹特征」。[来源：腾讯云开发者社区](https://cloud.tencent.com/developer/article/2671883)
- **单声道混叠在信息论上不可逆**：两个声音叠加成一个波形，想恢复两路就需要
  语音分离（source separation），而分离模型本身有极限。
- **流式约束**：系统只有几百毫秒上下文就要做决定，不能像离线那样回头看全局。
  pyannote 官方数据：流式分离 DER ~19.8% vs 批式可到个位数。
- **正确的衡量指标是 cpWER**（带说话人标签的词错误率）：即使最先进的系统在干净
  双人金融音频上也 ~10%+（SPGISpeech 2.0 论文，Canary-1B baseline 10.32%），
  多人重叠直播音频会显著更差。[来源：arXiv 2508.05554](https://arxiv.org/html/2508.05554v1)
- 技术综述结论原文：重叠语音处理「remains the hardest unsolved challenge in
  multi-speaker ASR」。
  [来源：Multi-Speaker ASR 综述](https://www.arunbaby.com/speech-tech/0012-multi-speaker-asr)

**含义：这不是「哪家厂商还没做」的问题，而是全行业的能力边界。**
等商用 API 做到「重叠也完美分开」，短期内不现实。

---

## 2. 能力分级（先对齐预期，再看厂商）

| 级别 | 能力 | 现状代表 |
|---|---|---|
| L0 | 所有人混成一条字幕流（现状） | Gummy / qwen3-asr |
| L1 | 非重叠段正确归属说话人；**重叠段归主导者或 UNKNOWN** | AssemblyAI / Soniox / Speechmatics / 腾讯云 / pyannote Live-1 |
| L2 | 重叠段**标注出来**（「这几秒有 2 人同时说」），文字仍是一路 | pyannote batch（默认输出重叠标注）/ IBM Granite Speech 4.1 Plus（flag overlap，二手口径） |
| L3 | 重叠段**每人一路文字**（真·分开） | **只有 NVIDIA MultiTalker**（开源自建，英语为主，每人一个模型实例） |
| L4 | 物理分轨：每人独立麦克风/音轨，并行识别 | 会议平台内部做法；**直播观看方拿不到分轨，不可行** |

直播 4-5 人场景的真实音频形态与可达到级别：

- 轮流发言（大头）：L1 即可，体验良好
- 抢话/插嘴（1–3 秒，常见）：L1 会丢被压住者的话；L2 至少能标出来
- 长时间同时说（少见）：L3 也只有英语勉强可行
- 叠加 BGM/游戏音（LingerLens 已实测 VAD 误判 30 秒 BGM）：所有级别都会进一步劣化

---

## 3. 各厂商重叠语音官方口径（中外）

### 3.1 外国厂商

**AssemblyAI —— 官方直言不支持重叠分离**（最诚实的一家）：

> "Can streaming diarization separate overlapping speech?
> **No—when two people talk simultaneously, streaming diarization assigns the entire
> overlapping segment to one speaker.** If you frequently deal with cross-talk,
> use separate microphones for each speaker to eliminate the overlap problem entirely."

另有：短于 1 秒的发言 speaker 标签可能返回 "UNKNOWN"；说话人越多准确率越低
（音频越少声纹越弱）；流结束后 ~0.5 秒内有一次标签回溯修正（diarization with
revision），修正标签但不恢复重叠内容。
[来源：官方博客 Streaming Speaker Diarization](https://www.assemblyai.com/blog/streaming-speaker-diarization)

**Speechmatics —— 营销口径与实际策略矛盾**：

- 营销页：「Accurately separates and tracks multiple speakers — even in overlapping,
  messy conversations」
- 但另一篇官方文章暴露实际策略：「Speechmatics' advanced AI **locks onto the primary
  speaker** and filters out background noise」——即重叠时锁定主说话人。
  [来源：real-time 文章](https://www.speechmatics.com/company/articles-and-news/our-fastest-growing-companies-have-one-thing-in-common-real-time)

**Soniox v5 —— 营销口径最强，但没承诺每人一路文字**：

- v5 发布博客：「Speaker separation has been completely reinvented... more accurately
  identify who said what... even with interruptions... and **overlapping speech**」
- models 文档：「More accurate speaker diarization, even in overlapping speech」
- 注意措辞是「重叠时归属**更准**」，不是「重叠时把两个人的话都转出来」。
  官方同时也写明实时分离精度低于异步。
  [来源：soniox.com/blog/soniox-v5-real-time](https://soniox.com/blog/soniox-v5-real-time)

**pyannoteAI —— 唯一明确支持「重叠标注」的分离厂商**：

- batch 分离（Precision-2）**默认输出可含重叠段**：同一时间段可以同时挂
  SPEAKER_00 和 SPEAKER_01；`exclusive=true` 才是压平成单说话人版。
- Live-1（流式）宣传「trained and validated on overlapping speech」，输出事件是
  `diarization_speaker_start/end`——**事件流结构上能表达并发**（两个 start 都发出、
  都还没 end = 重叠），但官方文档没有明确承诺 streaming 会输出并发标签，**需实测**。
  [来源：docs.pyannote.ai/features](https://docs.pyannote.ai/features)、
  [docs.pyannote.ai/models](https://docs.pyannote.ai/models)

**NVIDIA MultiTalker —— 唯一的真·每人一路方案（L3）**：

- 模型 `nvidia/multitalker-parakeet-streaming-0.6b-v1`（HuggingFace 开放权重）+
  Streaming Sortformer Diarizer v2
- 多实例架构：**每个说话人部署一个模型实例**，各自专注于一个人，「enables the model
  to handle severe speech overlap by having each instance focus exclusively on one
  speaker, eliminating the permutation problem」
- 官方 demo：最多 4 人同时说话，每人一路清晰字幕，无需声纹注册，实时
- 代价：**没有云 API**，必须自己用 NeMo/Riva 部署；GPU 成本随说话人数线性增长
  （4 人 = 4 个 0.6B 实例 + 分离模型）；语言以英语为主
  [来源：HuggingFace 模型卡](https://huggingface.co/nvidia/multitalker-parakeet-streaming-0.6b-v1)、
  [NVIDIA MultiTalker demo](https://www.youtube.com/watch?v=AThOsk2qJbs)

**其他**：

- Deepgram：官方 GitHub 讨论中对多人归属的推荐方案是**多声道分轨**
  （multichannel transcription），不是单声道重叠分离。
  [来源：deepgram discussion #814](https://github.com/orgs/deepgram/discussions/814)
- IBM Granite Speech 4.1 Plus：支持 overlap flag（标记重叠段而非静默归一人），
  二手口径，未核实 API 形态与实时性。
- ElevenLabs Scribe v2 Realtime：无 diarization（前报告已核实）。
- AWS Transcribe 流式分离仅 en-US；Google Chirp 3 分离仅 batch——均与重叠无关。

### 3.2 中国厂商

**腾讯云 —— 实时分离规格明确，但仅中英，重叠是承认的短板**：

- 实时语音识别 V2（WebSocket，`16k_zh_en_speaker_2.0` 引擎）：
  `speaker_id` 有效范围 0–9，**最多分离 10 个说话人**；自动判断人数无需预设。
  [来源：API 文档](https://cloud.tencent.com/document/product/1093/131127)
- 官方最佳实践文档原话：**「话者分离在 3-5 人时效果最佳，人数过多可能出现误判」**——
  正好覆盖用户「四五个人」的场景，但这是对**轮流发言的聚类质量**说的，不是重叠。
  [来源：会议场景话者分离实践](https://cloud.tencent.com/document/product/1093/130881)
- 官宣文章承认重叠破坏声纹特征（见 §1 引文），未承诺重叠段处理策略。
- 声纹聚类「标签稳定不篡改历史结果」（与外国厂商的回溯修正路线相反，各有取舍）。

**阿里云 —— 官方 FAQ 直接承认并给出绕行建议**：

- 「单声道录音的说话人分离无法保证 100% 准确，**在抢话、短句等场景下准确率会明显
  下降**。建议：优先物理分轨；或用百炼 Fun-ASR（离线）；**实时角色分离效果不佳时，
  建议会议结束后采用离线角色分离重新处理**。」
  [来源：ISI FAQ](https://help.aliyun.com/zh/isi/support/faq-about-speech-recognition)
- 百炼实时模型全部无 diarization（前报告核实）；离线文件转写支持（最多 2 小时音频）。

**讯飞 —— 说话人识别是强项，但那是另一个问题**：

- VoxSRC 说话人识别比赛世界纪录（EER 0.81%）；说话人分离覆盖电话/会议/实时场景。
  但「识别两条音频是否同一人」≠「重叠语音各自转写」。开放平台实时转写大模型的
  分离以中英+方言为主，ja/ko 未证实。
  [来源：讯飞听见资讯](https://www.iflyrec.com/zixun/5f3f35e5.html)

**火山引擎（豆包）/百度**：流式识别无实时分离能力（本轮未发现相关参数）。

### 3.3 社区实测口碑（UGC，非官方事实）

- Reddit r/artificial 用户（找会议转写工具）：「as soon as the conversation gets
  longer or **people start talking over each other, accuracy really drops. Even tools
  that claim to handle multiple speakers sometimes mix up who said what**」
- Gladia 自己的评测指南也建议用「2–3 秒重叠的 clip」专门测 diarization——
  业内默认重叠是必测的劣化点。
- 综合：官方口径 ≈ 用户实际体验，没有「藏着掖着的神厂」。

---

## 4. 给 LingerLens 的落地判断

### 4.1 直接回答：4-5 人直播同时说话，能做到什么程度？

| 直播实际情形 | 占比（典型） | 可达到的效果 |
|---|---|---|
| 轮流发言 | 大部分时间 | ✅ 每人字幕正确归属（L1 方案即可） |
| 抢话/插嘴 1–3 秒 | 常见 | ⚠️ 主导者正常，被压住者丢失或归错人；L2 可标注「重叠」 |
| 长时间同时说话 | 少见 | ❌ 无法完整恢复，最多标注；英语可上 MultiTalker（L3，自建） |
| 叠加 BGM/游戏音 | 取决于直播 | ⚠️ 进一步劣化（LingerLens 已实测 VAD 误判 BGM） |

**结论：值得做（覆盖大部分时间的轮流发言），但重叠段必须有诚实的降级表现，
而不是假装能分开。**

### 4.2 推荐路线（延续前报告，不推翻）

1. **主力方案不变**：Gummy 直译 + pyannote Live-1 分离（方案 A）或 Soniox（方案 B）。
   腾讯云实时分离因语言限制（仅中英）仍不满足 en/ja/ko 刚需。
2. **字幕 UI 增加「重叠态」**：
   - L2 语义：cue 上可标注「多人同时说话」（Live-1 若实测输出并发标签即可点亮；
     Soniox/AssemblyAI 拿不到并发标签，只能标 UNKNOWN/主导者）
   - 重叠/UNKNOWN 段的翻译上下文**回退到全局桶**，不要进任何人的个人桶
     （错归属的历史会污染该说话人的后续翻译）
3. **验收 clip 必须包含**：≥2 段轮流发言 + ≥2 段 1–3 秒抢话 + 1 段 BGM。
   量化指标建议：轮流段的 speaker 归属准确率；抢话段的「主导者归属」准确率；
   假说话人（独白被拆成多人的次数）。
4. **如果非要 L3（每人一路重叠字幕）**：
   - NVIDIA MultiTalker 自建：英语可用，日语/韩语无预训练多实例模型，等于要自己做
     ja/ko 的 finetune——工程量和 GPU 成本远超当前项目规模，**不建议现在做**
   - 记住这个能力边界，等商用化（Soniox v5 营销方向、AssemblyAI cpWER 指标都在
     往这边走，但都没有产品化承诺）

### 4.3 对交接文档的补充修订

在前报告修订建议之上追加：

- §6 spike 清单加一条：**用「抢话 clip」测 Live-1 是否输出并发 speaker 标签**
  （两个 speaker 的 start 事件重叠）——这决定 UI 能否做 L2 重叠标注
- §4.4 上下文分桶规则补充：speaker 归属置信度低（UNKNOWN/重叠/过短）时，
  该句进全局桶而非个人桶
- §2.4 风险升级：BGM + 重叠是叠加伤害，spike 不过关的判定标准要先写下来

---

## 5. 来源清单

官方（一手）：

- AssemblyAI：blog/streaming-speaker-diarization（重叠=No 的官方 FAQ）、
  blog/top-speaker-diarization-libraries-and-apis（cpWER 30.17、10 人上限）
- pyannoteAI：docs.pyannote.ai/features（overlapped speech detection、exclusive 参数）、
  docs.pyannote.ai/models（Live-1 规格）、tutorials/speaker-configuration
- Soniox：blog/soniox-v5-real-time、blog/soniox-v5-async、docs（models 页）
- Speechmatics：公司文章（locks onto the primary speaker）、产品页营销口径
- NVIDIA：HuggingFace nvidia/multitalker-parakeet-streaming-0.6b-v1（多实例架构）、
  MultiTalker demo 视频（4 人同时说话每人一路）
- 腾讯云：document/product/1093/131127（V2 API、10 人上限）、
  document/product/1093/130881（3-5 人最佳实践）、开发者社区官宣（2026-05）
- 阿里云：help.aliyun.com/zh/isi（FAQ 抢话/短句降准 + 离线重处理建议）
- Deepgram：GitHub discussion #814（多通道推荐）
- 讯飞：iflyrec.com 资讯（VoxSRC 纪录）

学术/技术（专业二手）：

- arXiv 2508.05554（SPGISpeech 2.0，cpWER 基准：Canary-1B 10.32%）
- arXiv 2506.20288（流式重叠转写的 target-speaker 研究路线）
- arXiv 2506.05796（MLC-SLM Challenge，ja/ko 双人对话联合分离+转写仍是挑战赛题）
- arunbaby.com 多说话人 ASR 综述（「hardest unsolved challenge」）

社区（UGC）：

- Reddit r/artificial（重叠下准确率骤降的用户实测）
- Reddit r/rust（Sortformer v2 移植讨论）

---

## 6. 覆盖与缺口

- **已覆盖**：主要中外厂商对重叠语音的官方口径（AssemblyAI / Soniox / Speechmatics /
  pyannote / NVIDIA / 腾讯云 / 阿里云 / 讯飞 / Deepgram）+ 学术能力边界 + 社区口碑交叉
- **未覆盖 / 失败项**：
  - IBM Granite Speech 4.1 Plus 的 overlap flag 仅二手口径，API 形态与实时性未核实
  - Azure / Google 在重叠场景的官方口径未逐页核实（两家实时分离本身语言受限，
    对本项目决策无影响）
  - Live-1 流式并发标签、Soniox 重叠段实际行为：**只能实测**，文档无明确承诺
  - 各家均未实际试跑（需要 key，属下一步 spike）
- **停止原因**：核心问题（重叠语音能不能靠商用 API 做到每人一路字幕）已有
  官方 FAQ、学术综述、社区口碑三路独立证据收敛为同一个答案：不能；
  剩余不确定性（L2 标注能否点亮）是实测问题。
