# PRD：不更换声纹模型的说话人语调鲁棒性

> 状态：ready-for-agent  
> 关联方案：`docs/speaker-intonation-robustness-plan.md`  
> 技术规格：`docs/speaker-intonation-robustness-spec.md`

## Problem Statement

LagLingo 当前通过声纹 embedding 和在线 `SpeakerGallery` 给字幕分配稳定的数字说话人 ID。实际使用中，同一个人只要从正常语调切换为激动、低声、假声或其他明显不同的说话方式，就可能被永久拆成新的 ID，字幕颜色随之改变。

当前判决把一次低相似度直接解释为“出现了新人”，而且一旦分裂便不会自动合并。多个句子的声纹任务还可能以不同于音频时间的顺序完成，使依赖最近说话人、EMA 中心和 exemplar 的在线状态进一步不稳定。单纯降低阈值虽然减少分裂，却会提高两个真人被错误合并的风险。

团队目前也没有可重复的 ground truth 验收集，因此无法区分“识别真的改善”与“系统把所有人错误地合并成一个人”。

## Solution

在不替换、不微调当前声纹模型的前提下，升级 embedding 外部的输入、打分、判决和纠错层：

1. 用一套很小、可自动生成真实 speaker 标签的 CREMA-D mini benchmark 建立基线和验收结果。
2. 让声纹归属严格按照音频时间发生，并对输入做轻量、可标定的有效语音裁剪与多窗聚合。
3. 把“单阈值立即建新人”改成高置信匹配、模糊待定、新人候选三段判决；新人达到最小证据后才对外发布。
4. 每个说话人维护少量、质量受控的多语调 prototype，低质量或低置信样本不能污染 profile。
5. 周期性检查已经分裂的 ID，在严格证据下合并，并通过现有字幕更新通道修正历史颜色。
6. 用同一个 mini benchmark 比较修改前后，要求同时降低同人分裂和异人合并，不能通过大量 UNKNOWN 或全部合成一人“刷高”指标。

## User Stories

1. 作为直播观众，我希望同一个主播在正常、激动或低声说话时保持同一种字幕颜色，从而不用重新判断谁在说话。
2. 作为直播观众，我希望真正出现新嘉宾时仍能获得新的颜色，从而不会把不同的人误认为同一人。
3. 作为直播观众，我希望偶发的一句喊叫或假声不会永久创建新的说话人颜色。
4. 作为直播观众，我希望系统不确定时宁可暂时不着色，也不要立即显示一个错误的新身份。
5. 作为直播观众，我希望暂时错误的颜色能在证据充足后自动修正。
6. 作为直播观众，我希望颜色修正不改变字幕文本、翻译内容和显示时间。
7. 作为直播观众，我希望开启说话人颜色后不会显著拖慢字幕出现速度。
8. 作为产品使用者，我希望该改进继续使用当前本地声纹模型，不增加必须联网的识别服务。
9. 作为产品使用者，我希望不需要手工给每场直播标注说话人才能使用改进后的功能。
10. 作为产品使用者，我希望配置保持少量且有安全默认值，不需要理解复杂的声纹算法参数。
11. 作为开发者，我希望有一个小型、可重复执行的 benchmark，能够提供真实的 same-speaker 和 different-speaker 正确答案。
12. 作为开发者，我希望 benchmark 能分别报告同人分裂、异人合并、UNKNOWN 和延迟，而不是只给一个可能被误导的总分。
13. 作为开发者，我希望 benchmark 数据由官方数据集的文件名和元数据自动生成标签，避免人工猜测正确答案。
14. 作为开发者，我希望 benchmark 只下载或选取必要的小子集，以便本地和 CI 外的验收快速运行。
15. 作为开发者，我希望同一套 benchmark 可以比较原始 cosine、输入聚合和可选归一化 scorer，从而只启用有测量收益的策略。
16. 作为开发者，我希望声纹 profile 按音频时间更新，从而让最近说话人和轮次先验具有确定含义。
17. 作为开发者，我希望同一个 embedding extractor 不会被不受控地并发使用，从而避免线程安全和乱序问题。
18. 作为开发者，我希望短句、静音过多或质量不足的片段不能创建新说话人。
19. 作为开发者，我希望低置信度归属不能更新 prototype，从而避免一次错误产生后续连锁污染。
20. 作为开发者，我希望一个人可以拥有多个稳定的声纹 prototype，从而表达不同语调和说话方式。
21. 作为开发者，我希望 prototype 数量有上限并按代表性更新，从而避免 exemplar 越积越多导致偶然高匹配。
22. 作为开发者，我希望新人先作为 tentative candidate 累积证据，从而过滤一次性语调漂移。
23. 作为开发者，我希望 tentative candidate 确认后可以回填等待中的字幕，而不是永久丢失颜色。
24. 作为开发者，我希望轮次先验只是模糊分数下的软证据，不能覆盖明显属于另一个人的声纹证据。
25. 作为开发者，我希望发生过重叠发言的两个 ID 被视为不可合并，而“没有重叠”本身不作为合并证据。
26. 作为开发者，我希望合并后的说话人 profile 保持有效，最近说话人和 ID 复用规则保持一致。
27. 作为开发者，我希望批量 speaker remap 是幂等的，并通过单调 cue 序列号被播放器可靠接收。
28. 作为维护者，我希望原始 benchmark 音频不进入 Git 仓库，只提交下载/选样脚本、manifest 和结果格式。
29. 作为维护者，我希望第三方数据许可证和来源被记录，避免把研究或非商业数据误随产品分发。
30. 作为维护者，我希望复杂的 AS-Norm、固定均值中心化等策略只有在 benchmark 证明有效时才成为默认值。
31. 作为维护者，我希望重叠语音仍被明确视为独立问题，避免本次改动被错误承诺为完整 diarization。

## Implementation Decisions

- 保持现有 `PCM -> embedding` 模型契约；当前 CAM++ 实现和未来 pyannote embedding 实现共享同一上层逻辑。
- 使用 CREMA-D 的确定性 mini subset 作为首个 ground-truth benchmark。默认规模控制在约 20 位 actor、每位 12 条音频，覆盖 neutral、happy、angry、sad 以及至少两档强度；具体清单由固定 manifest 决定。
- benchmark 原始音频不纳入源码仓库。仓库只保存准备脚本、manifest、许可证说明和报告 schema。
- embedding 的提取与 speaker profile 的 mutation 必须具有确定的音频顺序。实现可以串行处理整个 attribution，也可以并行计算后按句序提交，但最终 profile 更新不得乱序。
- 输入预处理只包含经 benchmark 验证有收益的轻量操作：有效语音裁剪、质量门槛、有限增益和可配置的多窗聚合。每项必须可以关闭用于对照。
- scorer 是可替换的内部策略。raw cosine 永远保留为基线；固定 population mean 和 AS-Norm 为 benchmark-gated 可选项，不使用持续漂移且无法同步旧 profile 的在线均值。
- speaker 判决返回结构化结果，至少区分 confirmed、tentative、unknown，并携带最佳分数、第二名分数、margin 和是否允许更新 profile。
- 匹配使用高低双阈值。高置信度归入已有 speaker；模糊区间可参考最近轮次但不能创建新人；明显不匹配的长句进入 tentative candidate，而不是立即获得正式 ID。
- tentative candidate 至少需要两句、累计三秒有效语音，并且候选内部相似，才对外发布正式 ID。阈值和时长可由 benchmark 调整，但默认行为保持保守。
- 每个 speaker 使用少量质量受控 prototype，而不是无限扩大最近 exemplar 并取单个最大值。默认上限为 4 个，可在 3 至 6 之间通过 benchmark 选择。
- 只有高置信、有效语音足够长且非已知污染的 embedding 能更新 prototype。模糊归属、短句和疑似重叠片段不能更新 profile。
- 轮次连续性仅在模糊区间作为软加分，并同时考虑最佳与第二名分数差；它不能覆盖显著更强的其他 speaker 证据。
- 周期性 reconciliation 使用 profile 级证据检测重复 ID。合并要求多个 prototype 的稳定交叉相似、合并后类内散度可接受、相对其他 speaker 有足够 margin，并且不存在已确认重叠。
- “没有重叠”只表示允许继续评估，不是正向合并证据。
- 合并保留较早确认的稳定 ID，并通过字幕存储的批量 remap 更新保留窗口内的历史 cue。播放器继续以 cue ID 和单调序列号处理修订，不创建重复字幕。
- 普通用户只暴露启用开关和极少数稳定配置。窗口、阈值、prototype 数量和 scorer 实验参数主要属于 benchmark/高级配置。
- 不在本次范围中引入新的深度学习训练流程或模型服务。

## Testing Decisions

- 主要验收 seam 是“固定 benchmark manifest + 当前生产 embedder + 实际 SpeakerGallery/attribution 流程”的离线端到端命令。它验证可观察的 speaker 分配结果，而不是内部数学函数。
- benchmark 至少生成两种场景：成对 verification 评估，以及按已知顺序拼出的伪直播 session。前者标定分数，后者验证 ID 分裂、误合并、tentative 确认和迟滞纠错。
- 报告至少包含：pairwise same/different EER 或最佳阈值、B-cubed 或等价聚类 precision/recall/F1、false split、false merge、UNKNOWN rate、speaker churn、归属延迟 p50/p95。
- calibration 和 evaluation 必须使用不同 actor 子集，避免在同一批人上选择参数并宣称最终准确率。
- 核心通过条件是：evaluation 子集的 false split 明显低于基线，false merge 不高于基线允许的窄幅容差，UNKNOWN 不能通过大幅上升掩盖错误，p95 延迟仍满足字幕 late-fix 预算。
- 单元测试只覆盖不可由 benchmark 快速定位的关键状态不变量：乱序完成仍按音频顺序归属、单个异常句不建新人、候选确认、低置信样本不污染 prototype、重叠 speaker 不合并、speaker remap 幂等、播放器接受同一 cue 的 speaker 修订。
- 沿用现有 speaker attribution、subtitle pipeline、cue store 和 web asset 测试风格，不为第三方聚类或数值库本身重复写测试。
- 真实业务直播只需要一段短的人工抽查作为发布前 smoke check，不阻塞第一轮实现。

## Out of Scope

- 替换、微调或重新训练 CAM++/pyannote speaker embedding 模型。
- 完整的 segmentation + clustering diarization 管道。
- 解决两个人同时说话时的精确分离；本次只防止疑似重叠样本污染和错误合并。
- 大规模数据平台、标注后台、在线训练、自动超参搜索。
- 将 CREMA-D、JVS、ESD 或其他第三方原始音频提交到仓库或随产品分发。
- 默认实现 LDA 或其他需要项目自有大规模标注数据的监督投影。
- 固定主播的跨场次实名 enrollment/profile 管理；多中心思想会在本次 profile 中使用，但持久化实名注册另行处理。
- 为所有可能的 scorer 和预处理组合编写穷举测试。

## Further Notes

- 首选 CREMA-D 是为了速度：数据直接可得、speaker/emotion/intensity 可从文件名得到，而且 20 人的小子集已经能同时暴露分裂和合并。
- 如果最终业务主要是日语直播，完成首轮后可额外用 JVS 的 normal/whisper/falsetto 小子集做第二语言 smoke check；它不是首批 tickets 的阻塞项。
- 当前配置中的 0.4 与 spike 脚本中的 0.7 不应继续被视为可靠默认值。新默认值必须来自固定 calibration 子集，并记录在 benchmark 报告中。
