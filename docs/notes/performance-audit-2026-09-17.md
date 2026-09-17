# 性能与停滞路径审计（2026-09-17）

本轮发现并隔离复现了几条真实的停滞、重复计算和资源保留路径，但**尚未找到“切到另一块屏幕的 Edge 后，整机连鼠标都冻结”的根因**。这些问题的作用范围不同，不能合并成一个结论。

用户已明确选择：**先完成审计报告，暂不改业务代码**。本文及配套清单是本轮新增文件；候选修正、复现程序和结果保存在 `.scratch/performance-audit-20260917/`。候选补丁未应用，未修改运行配置、GPU 参数、显示适配器或注册表，未启动真实直播，也未操作已有播放器窗口。

审计基线为 `b56582fdcc1bf46faa2a7cae95b61d94813fae9f`，分支 `wip/subtitle-anchor-correction`，加上工作区已有的停止按钮改动。那些未提交的业务代码属于另一轮工作，本轮未覆盖、回退或纳入提交。逐文件 SHA-256 和 Git 状态见 `.scratch/performance-audit-20260917/source-snapshot.json`。

> **修订记录：** 第 6 节表中 `tmPairs` 一行的结论（原文为"生效"）由另一个会话在 `5cf8b05` 修正——`QwenMTTranslationProvider` 没有 `@register`，`qwen-mt` 不在翻译白名单且被测试断言不存在，所以默认模型下该选项是惰性的。**其余内容一字未改**，包括第 5.1 节"当前启用的 Soniox 不使用这些集合"这一限定——计划文档转述时丢掉了它。

## 结论与优先级

| 项目 | 证据等级 | 实际影响 | 建议顺序 |
|---|---|---|---|
| 字幕解码 stderr 没有读者 | 实际管道、实际字幕任务复现 | 解码器被错误输出堵住，音频/字幕停止推进 | 第一批 |
| 前端超时只覆盖响应头 | 原函数 + 本地真实 HTTP 复现 | 某条轮询永久等待正文，状态/字幕不再更新 | 第一批 |
| Soniox 无端点时重复扫描整段历史 | 实际适配器、合成合法消息测量 | 每次消息处理随未结束语音长度增长，阻塞后端事件循环 | 第二批，需明确不改变切句语义 |
| 翻译剩余预算 ≤ 2 秒时不保留兜底时间 | 实际 FallbackChain、确定性时钟复现 | 慢主服务耗尽剩余时间，兜底来不及执行 | 第二批，先决定短预算策略 |
| 部分识别适配器不回收去重记录 | 实际适配器消息重放 | 内存随会话增长；目前没有证据说明量级足以造成整机冻结 | 后续 |
| 每次翻译新建 HTTP 会话 | 本地服务观测到 12 次请求、12 条 TCP 连接 | 不复用连接，增加可避免的连接成本 | 优化项，需真实延迟对照 |
| 两个无效配置残留 | 静态全表 + language 行为验证 | 编辑字段无效/配置误导 | 小型清理 |
| 跨屏整机冻结、约 95 秒 CPU 周期 | 根因未确定 | 需要窗口可见性和系统显示调度证据 | 独立诊断，不能用上述修复代替结案 |

## 1. 字幕解码器可能被自己的错误管道堵住

位置：`companion/subtitle_pipeline.py:449–470` 的进程创建和任务注册，以及 `_pcm_reader`。

字幕解码 FFmpeg 使用 `stderr=asyncio.subprocess.PIPE`，但启动的任务只读取 stdout、发送 PCM、接收识别结果等，**没有持续消费 stderr**。这与媒体打包 FFmpeg 的 `_read_log` 路径不同。即使 FFmpeg 只输出 error 级别，连续损坏的媒体仍可能产生足够多的错误。

本轮通过现有 `subprocess_factory` 注入一个很小的本地子进程；`SubtitlePipeline.start()`、PCM reader/sender、Windows 管道及关闭流程均使用实际实现。子进程先输出 0.1 秒 PCM，再写 1 MiB stderr，最后再输出 0.1 秒 PCM。唯一实验变量是是否读取 stderr。

| 观测 | 结果 |
|---|---:|
| 无 stderr 读者时的 PCM 进度 | 停在 0.1 秒 |
| 此时子进程是否活着 | 是 |
| Python stderr 接收缓冲已积累 | 262,144 字节 |
| 增加 stderr 消费后的同一进程 PCM 进度 | 立即到达 0.2 秒 |

这确认了管道阻塞链条。262,144 字节是这次运行中 Python 接收缓冲的观测值，不是 Windows 管道容量的通用常数。实验没有测量真实直播的错误日志增长率，也没有证明用户那次运行曾满足触发条件。

建议的最小修复：给字幕解码器注册一个 stderr 读取任务，并纳入现有任务生命周期；读取块/保存的诊断尾部要有上限。保留媒体与独立识别音频的设计。验证应覆盖错误洪流时 PCM 继续推进、停止时任务和进程都退出，以及既有字幕测试。**未实施。**

## 2. 前端“30 秒超时”没有覆盖读取响应正文

位置：`web-player/player.js:589` 的 `request()`。

现在的顺序是 `Promise.race(fetch, timeout)` → 清掉 AbortController 的计时器 → `await response.text()`。`fetch()` 在收到响应头后就能完成，正文还可能继续等待。若本地服务或桌面协议转发只交付了响应头/部分正文，`response.text()` 可以永远不结束。

剩余的 Promise 超时已经输掉前一场 race，不能中止后续的正文读取。`createSerialPoller` 会等待 `run()` 结束才安排下一次，所以这一条轮询会停住。另一个小问题是：正常请求也留下一个未清理的 Promise 超时计时器，直到 30 秒后自行触发。它是有时间上界的冗余工作，**不是无限增长的泄漏**。

实验直接提取当前 `request()` 原函数，在 Node 中调用。本地 HTTP 服务发送 JSON 响应头和不完整正文后保持连接；计时器由测试推进到到期，不需要实际等 30 秒。真实 fetch、响应正文流及 AbortSignal 均参与验证。

| 场景 | 当前实现 | 独立候选修正 |
|---|---|---|
| 正常请求完成后的残留计时器 | 1 | 0 |
| 正文卡住、推进到截止时间 | 仍 pending | 返回超时错误 |
| 截止时间到达是否发出 abort | 否 | 是 |

候选把 fetch 和正文读取放进同一个截止时间内，并只保留一个可清理计时器。20 秒 probe / 30 秒其他请求的原有预算、API 与调用者保持不变。

候选文件：`.scratch/performance-audit-20260917/request-timeout-proposed.patch`。`git apply --check` 已通过；**补丁没有应用**。本轮只验证了函数与本地 HTTP 行为，尚未做实际 Electron 协议转发及真实直播长跑回归。

## 3. Soniox 无句末标记时有可测的退化路径

位置：`companion/providers/asr_soniox_realtime.py:297–368` 的 `_map_event`。

`_final_tokens` 和去重键保留整段未结束的识别内容，直到 `<end>` / `<fin>` / finished 才清空。每条消息都会重新拼接全部文本、构造词片段、统计语言/说话人/置信度；即使消息没有新增 token，也重复这份工作。

使用实际适配器，持续送入带日语文本、时间戳和说话人的 finalized token，不发送端点消息，再测量空 token 消息的处理时间：

| 保留的 token 数 | 已向上层输出的 token 数 | 处理一条空消息的中位耗时 |
|---:|---:|---:|
| 100 | 100 | 1.394 ms |
| 1,000 | 1,000 | 13.836 ms |
| 5,000 | 5,000 | 77.296 ms |
| 10,000 | 10,000 | 151.459 ms |

发送端点后，保留量归零。这里的问题是已经交给字幕处理层的历史，仍被适配器反复计算；这些同步计算运行在服务端事件循环中，能够延迟同进程的 HTTP 和识别任务。

前端允许关闭 `enableEndpointDetection`，所以这不是完全不可到达的输入条件。不过，**本机当前 Soniox 配置启用了端点检测，maxEndpointDelayMs 为 700**。本轮没有证明真实问题发生时端点长期缺失，不能把这个条件性缺陷直接认定为现有“越跑越卡”的原因。

修复需要增量维护已完成词片段及统计，并保持最终文本核对、去重、说话人信息和原有切句行为；仅随意截断 `_final_tokens` 会破坏现有语义。建议单独设计和验证，不在本轮草率修改。

## 4. 翻译兜底在短预算下失去预留时间

位置：`companion/providers/fallback.py:161–174`。

`primary_budget = remaining - 2.0`，只有它大于 0 才缩短主服务截止时间。因此，当剩余预算已经不超过 2 秒时，主服务反而获得全部剩余时间。若主服务用满预算才失败，备用服务拿到的就是已经过期的请求；外层 `wait_for` 也可能直接取消整个调用。

用实际 FallbackChain 和确定性时钟，使主服务恰好消耗它得到的预算，观测如下：

| 总剩余预算 | 主服务获得 | 兜底到达时剩余 | 结果 |
|---:|---:|---:|---|
| 1.00 s | 1.00 s | 0.00 s | 失败 |
| 2.00 s | 2.00 s | 0.00 s | 失败 |
| 2.01 s | 0.01 s | 2.00 s | 成功 |
| 6.00 s | 4.00 s | 2.00 s | 成功 |

这不是说短预算下必然失败：主服务快速成功时没有问题。缺陷在于“为兜底预留”的承诺在边界处反转。直接跳过主服务、按比例分配、只在兜底可用时保留预算，都是不同的产品选择，需先确认策略。

本轮未运行收费翻译 API。现有 byteclock 长跑记录保存了播放/源状态，但缺少完整翻译累计计数，无法从它计算新的端到端翻译失败率。下一次真实长跑应记录 attempts、failures、deadline expired、dropped 的起止差值，并把主动停止/会话切换分开；不能拿 handoff 中旧会话的失败数当作修复后的失败率。

## 5. 其他资源与连接问题

### 5.1 识别适配器的去重集合一直增长

AssemblyAI `_turn_started`、Speechmatics `_started_items` 没有回收旧 ID。重放正常完成的识别消息后，两者都呈现：100 条 utterance 保留 100 个 ID，1,000 条保留 1,000 个，10,000 条保留 10,000 个。源码中的腾讯 `_started_items`、火山 `_started_items` / `_finalized_starts` 也有类似仅增加的集合，本轮没有对后两者做等量动态重放。

这些集合的成员判断通常仍是常数时间，当前证据主要是资源不回收，不是每次操作越来越慢。实际内存增量与“整机冻结”的规模相距甚远；当前启用的 Soniox 也不使用这些集合。后续应按协议的晚到修订/重复消息范围，设计有界去重记录，不能在 final 到来时直接全部删除。

### 5.2 翻译 HTTP 连接没有复用

`companion/providers/http.py:61` 每次 `post_json` 都创建并关闭一个 `aiohttp.ClientSession`。使用本地 HTTP 服务连续接收 12 次调用，观察到 12 条独立 TCP 连接。

这确认连接不能复用；它没有证明远程 TLS/代理握手具体占用了多少延迟，也不构成连接泄漏，因为会话按调用关闭。将会话变成 Provider 或应用持有的资源，涉及明确的创建/关闭边界，应在真实延迟对照后独立实施。

### 5.3 当前没有证据支持普通列表遍历会拖死机器

正常单条字幕在显示窗口内时，直接测量实际字幕调度器：400 条保留字幕的单次调用中位数约 0.030 ms、p95 约 0.079 ms；4,096 条时中位数约 0.229 ms、p95 约 0.366 ms。这是纯 JS 算法测量，不含 DOM、合成和视频解码。不能用它排除显示驱动问题，也没有理由据此大改字幕调度结构。

## 6. 配置项全表与两处无效残留

完整清单见 [88 个选项的连接表](performance-audit-2026-09-17-options.md)。静态扫描包括 options 的 get/下标/存在性检查和映射表，另补上服务端转发与前端编辑字段；嵌套 turnDetection 以顶层键计。

完整 Provider catalog 保存路径会保存 options，因此“没有 UI 字段”不等于“不能写入”。逐项核对 handoff 点名的键后：

| 选项 | 配置/写入 | 消费者 | UI | 结论 |
|---|---|---|---|---|
| contextSeconds | 默认模板 90 + catalog | server → RollingContext | 无单独字段 | 生效，但隐藏 |
| tmPairs | catalog / JSON，代码默认 4 | Qwen MT 路径 | 无 | 仅 qwen-mt 模型下生效（mt_openai_compat.py:156）。`QwenMTTranslationProvider` 没有 `@register`，`qwen-mt` 不在翻译白名单（config.py:362）且被 tests/test_providers.py:63 断言不存在，所以 mt_qwen_mt.py:105 不可达。默认模型下是惰性的 |
| enablePartials | Speechmatics 模板 + catalog | Speechmatics | 有 | 接通 |
| maxSpeakers | 模板/catalog | AssemblyAI、Speechmatics | 有 | 接通；后者由 diarization 控制 |
| maxDelaySeconds | Speechmatics 模板 + catalog | Speechmatics | 有 | 接通 |
| wordInfo | Tencent 模板 + catalog | Tencent 能力与握手 | 有 | 接通；Python 模板为 1，前端切协议默认 0 |
| vocabularyId | catalog | DashScope 能力与握手 | 有 | 接通 |
| heartbeat | DashScope 模板 + catalog | DashScope | 有 | 接通 |
| includeTimestamps | catalog | ElevenLabs 握手 | 有 | 接通；不能据此宣称实时词级时间戳可用 |
| includeLanguageDetection | ElevenLabs 模板 + catalog | ElevenLabs 能力与握手 | 有 | 接通 |
| enableSpeakerDiarization | Soniox 模板 + catalog | Soniox 能力与握手 | 有 | 接通 |
| maxEndpointDelayMs | Soniox 模板 + catalog | Soniox 映射表 | 有 | 接通 |

额外发现：

* `openai-audio-transcriptions` 编辑器的 `options.language`（player.js:1393）没有消费者。实际语言由全局 SourceLanguagePolicy 生成。分别填 en、fr，而全局指定 ja，实际 stream.language 都是 ja。应删除无效字段或明确设计其语义，不能并存两个互相矛盾的语言来源。
* `hotwordsEnabled` 出现在 Python 默认模板与示例配置中，没有代码读取。DashScope 热词能力实际检查 `vocabulary`/`vocabularyId`。
* `reasoningEffort` 有读取及完整 catalog/JSON 写入路径，当前编辑器没有字段。它属于隐藏选项，不是之前已经删除的 enableThinking 那类无效设置。

## 7. 重新检查原始性能记录

读取了 `output/soak/lag/lag-samples.jsonl`（48 个样本）和 `tree-samples.jsonl`（101 个样本），没有对运行中的应用采样。

**这两个现存文件不能支持“全程零个 >100 ms 帧间隔”的表述。** 前者有一个 237.5 ms 间隔；后者有 106.1 ms 和 4,650.1 ms 间隔。特别是后一处：

| 本地时间（UTC+8） | 当前媒体时间 | 全进程树 CPU，100%=1 核 | 最大 rAF 间隔 | JS 长任务最大时长 |
|---|---:|---:|---:|---:|
| 2026-09-16 12:50:53 | 727.076 s | 63.2% | 6.4 ms | 0 |
| 2026-09-16 12:50:58 | 732.094 s | 25.7% | 4,650.1 ms | 0 |
| 2026-09-16 12:51:03 | 737.156 s | 71.6% | 50.0 ms | 0 |

这个区间里媒体时间仍在推进，readyState=4，未暂停，前向缓冲约 7.9 秒；累计丢帧从 52 变为 58。rAF 是页面动画回调，不等价于视频帧。采样器没有保存 `document.hidden`、visibilitychange、焦点或遮挡状态，所以无法区分后台/遮挡导致的回调暂停和显示调度故障。也不能认定这个时间就是用户说的整机冻结时刻。

约 95 秒的 CPU 周期在 tree-samples 中可以再次看到：renderer+GPU CPU 序列在 95 秒滞后的自相关约 0.739。它与同批样本最大帧间隔的相关约 -0.086，至少不是“CPU 峰值一到就出现记录中的大间隔”。样本数量有限、间隔约 5 秒，这些相关系数只是描述，不构成因果排除。

下一次最有价值的采集应同时记录：窗口 visible/hidden、focus/blur、实际视频帧推进、rAF、JS 长任务、renderer/GPU PID 与 CPU、后端进度，以及 DWM/GPU/调度 ETW。确认风险、准备按明确 PID 退出的手段后，再重放跨屏点击。一次实验“不再冻结”不足以证明 MPO、虚拟显卡或某个开关是原因；低复现率事件需要反复对照。**本轮未进行该实验。**

## 8. handoff 剩余项目核对

| handoff 项目 | 本轮结论 |
|---|---|
| GPU disable-gpu 的已修复问题 | 没有改动现有正常渲染路径；未用 GPUFeatureStatus 的 disabled_software 做健康判据 |
| RecoveryPolicy.action 无消费者 | 仍成立；server 输出的是建议，未执行重连。建议先明确诊断字段语义，不擅自接上自动重连 |
| 单独重启下载 leg 不安全 | 仍成立：缓存签名 URL、source_pts_first 重置及 origin latch 都在；当前无自动调用路径，暂不引入 |
| playback-recovery 的固定 5 秒阈值 | 仍存在；调用端先分类后传值，属于规则重复，未证明当前行为故障 |
| 五个 stall 标志位 | 仍主要靠源码断言保护；可测试性弱，但抽成状态机属于结构调整，未实施 |
| quiet-source 日志自相矛盾 | 当前代码仍会先声称“未打扰播放”，再因缓冲不足暂停。handoff 补丁本轮 `git apply --check --recount` 成功，未应用 |
| 翻译 fallback 预算边界 | 已用实际类复现，详见第 4 节 |
| 翻译失败率 | 本轮不启动收费真实长跑；已有 byteclock 记录字段不足，不能补算修复后的失败率 |
| Streamlink 可达性 | 当前 server 不导入/实例化该后端，桌面打包显式排除 streamlink；不在现有正常播放器执行路径。未联网验证独立旧模块 |
| 33-bit PTS 回绕 | 90 kHz 时约 **26.512 小时**，不是 4 小时。实际 `_source_clock_offset` 对跨回绕的巨大差值返回 None；同侧有效差值保持 5 秒。这里只验证拒绝错误映射，未验证跨回绕后的长期字幕质量 |
| internalException | 源码有捕获详细信息的逻辑，但在本轮检索的 docs/output/.scratch 审计数据中未找到可重放的完整事件载荷；未声称已解决 |
| Event loop is closed 关闭噪声 | 未在本轮管道实验中重现，不能将新发现的 stderr 问题直接等同于该 traceback；需要原始关闭栈或真实停止重现 |

## 9. 热路径检查范围

| 路径 | 本轮观察 |
|---|---|
| 字幕渲染 100 ms | 隐藏时跳过；复用 DOM；纯调度算法已测，正常输入很轻 |
| 时间线 250 ms | 隐藏时跳过；仍做过滤/排序/时钟与控件刷新；字幕 DOM 100 条、聊天存储/DOM 有界 |
| 状态/字幕/诊断轮询 | 串行、可见性降频、防 wake 重入已存在；request 正文等待可使某一条串行链停住 |
| 直播消息轮询 | 500 ms，仍在后台运行；无统一隐藏降频，但不会因此分叉成无限并发链 |
| 发布器 200 ms | 私有/公开滚动窗口、旧文件裁剪和 seen_names 回收已存在；复制/目录扫描在发布线程执行 |
| 字幕与翻译队列 | PCM 有界背压，翻译队列有淘汰/期限控制，多处统计 deque 有上限；没有简单把它们误报成无界队列 |
| CaptionChunker | 词条记录与 lane 已有回收、统计窗口有上限；没有重做已有回收改动 |
| 下载时间戳探针 | source_pts 有裁剪，首次 origin 单独保留；没有发现每次轮询复制全部会话媒体 |
| 桌面主进程 | 请求转发、进程与 GPU 生命周期已检查；日志启用时存在同步文件追加，但尚未测出它导致本次症状 |
| 浏览器扩展/旧 Streamlink | 与当前桌面播放路径区分；未把不可达模块的潜在开销算到本机现象上 |

本轮不是逐行正确性认证，也没有将代码审计当成系统级冻结的替代实验。

## 10. 可复跑证据与验证

本地隔离程序均位于 `.scratch/performance-audit-20260917/`（显式调试目录，Git 忽略）：

```powershell
py -3.10 .scratch/performance-audit-20260917/probe_backend.py
node .scratch/performance-audit-20260917/probe_frontend.cjs
node .scratch/performance-audit-20260917/probe_frontend.cjs .scratch/performance-audit-20260917/request-candidate.js
py -3.10 .scratch/performance-audit-20260917/probe_handoff.py
py -3.10 .scratch/performance-audit-20260917/audit_options.py
py -3.10 .scratch/performance-audit-20260917/analyse_recordings.py
```

`probe_backend.py` 会创建一个有界的合成子进程和一个临时 loopback HTTP 服务，均在 finally 中关闭。它不启动 yt-dlp、真实 FFmpeg 或外部模型服务。前端实验也只启动临时 loopback HTTP 服务并在 finally 中关闭。Soniox 测量是在实际适配器中重放合成消息；不建立 WebSocket。

结果文件：`backend-results.json`、`frontend-results.json`、`frontend-candidate-results.json`、`handoff-results.json`、`recordings-summary.json`、`options-inventory.json`。这些是审计结果，出现已复现缺陷是预期的，并非“业务测试全绿”的替代物。

现有回归测试另行执行：

* `py -3.10 scripts/run-hls-tests.py --only subtitle_pipeline --timeout 90`：60 项通过。
* `py -3.10 scripts/run-hls-tests.py --only providers --timeout 90`：3 个文件，94 项通过。
* `node scripts/run-hls-js-tests.js`：139 项通过。
* 前端请求候选补丁、handoff quiet-source 补丁的 `git apply --check`：通过，均未应用。

随后补跑了完整套件：`py -3.10 scripts/run-hls-tests.py --timeout 180` 发现 49 个 Python 测试文件、**557 项通过**；`node scripts/run-hls-js-tests.js` 为 **139 项通过**；`npm run check:python` 与 `npm run check:js` 也通过。前面按问题范围跑的 154 项 Python 是子集验证，不再作为总数引用。没有做完整 CI、真实跨屏实验或修改后的真实直播长跑；业务代码未修改，因此也不宣称已完成性能修复验收。
