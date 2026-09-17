# Handoff：v2 计划执行完之后（给下一位执行者 / 只能读仓库的外部模型）

**写这份文档的原因**：`docs/LingerLens-performance-remediation-work-plan-2026-09-17-v2.md` 的十个工作包已全部执行完，但执行者（我）在做完之后仍然有一批**卡点、能力边界和搞不懂的事**。它们散落在提交信息与执行记录里，不足以让下一个人直接接手。这份文档只写这些，不重复别的文档。

**消费者说明**：如果你的运行环境只能读本仓库（不能读执行者的工作区、不能执行代码），那么**本仓库里能读到的东西就是全部证据**；工作区里 gitignore 的东西（`.scratch/`）你读不到，我会在提到它们时说明「你读不到，需要用户转述或另行上传」。

**语言**：用户用中文提需求；产品的代码注释、提交信息、测试名都是英文。文档是中文（沿用本批文档的惯例）。

---

## 1. 一句话现状

分支 `wip/subtitle-anchor-correction`（`origin/main` = `fda1319` 之后再 47 个提交，HEAD = `ad04dee`）已完成 v2 计划全部十个工作包，每项一个（S1 两个）独立可回退提交，全部已推送、工作树干净、`npm run ci` 退出 0。

**实测已经做到的，一句话**：十次真实运行（§3.5.1–§3.5.13）——抓出并修掉一个会让 R1 在**每个会话**上失效的生产缺陷；D1 / S1 / R1 在真机载荷上确认；`-copyts` 的 re-base **测到**并证明守卫挡掉的是约 10.4 秒错位；**M1 从「有现象」推到「有机制 + 有验证过的修法方向」**（预算 = `min(6 秒, 目标延迟 − age)`，应用自己建议 +18 秒，A/B 在 25 秒档零丢失）；真实页面点击与两页面抢会话都通过。**执行者能独立做的观测到此为止**，剩下的都在 §3.5.14 与 §4.1。

## 2. 提交 → 工作包对照

| 提交 | 工作包 | 内容一句话 |
|---|---|---|
| `8b3ce74` | D0 | 建立 U1/U2/U3 顶层需求登记表，把四份文档降到其证据等级 |
| `17c22ef` | A2-T | 重写 request 回归测试：断言自身边界、驱动真实 poller、所有等待有界 |
| `8fbef5d` | A1 | 排空解码器 stderr（有界尾部 + 去重诊断），关闭生命周期只用**一个**绝对期限 |
| `617a765` | B2-R | 逐次总期限检查；把「没轮到」与「失败」分开（`_eligibility_reason`） |
| `6978e31` + `951df0c` | S1 | 每个媒体会话有身份（后端 `mediaSessionId`）；停止屏障绑定到**哪一个会话**而非「本页 Start 成功」（页面） |
| `bcbcb2a` | E1-R | 恢复策略改收布尔 `upstreamStalled`，调用者同提交迁移，无兼容别名 |
| `8773f7e` | A3 | quiet-source 文案不再预测；自动暂停单独记录一次动作 |
| `85420f2` | R1 | 33 位 PTS 回绕展开 + 解析 `discontinuity_indicator` + 不可信时钟停发精确字幕 |
| `ad04dee` | R1 修复 | 实机实测发现消费者问的是**对象属性**而 R1 只写了**快照字典**，于是每个会话都被拒绝、字幕一条不发；改为单一 `source_clock_state` 属性，两端同读（§3.5.1） |
| `cb552e0` | C3 | 删除两个无消费者的选项入口（`options.language`、`hotwordsEnabled`） |
| `3a30ada` | D1 | 删除整个 `sourceRecovery` 投影与 `RecoveryPolicy` 接线 |
| `31e7c08` | L1 | 离线时长退化分析器（在隔离工作树完成，cherry-pick 进来） |

**施工顺序与计划的差异**：实际是 S1 → E1-R → A3 → C3 → D1 → R1。v2 §5.3 给的是「**默认**顺序」，其第 7 行明确写了 R1 被阻塞时「转 C3、已批准 D1 或 L1 工具」；R1 放最后是因为它最大、需要一整轮上下文。七项批准一次性给全，没有跳过批准门槛。

**不要重复读**（按路径引用即可）：v2 计划 `docs/LingerLens-performance-remediation-work-plan-2026-09-17-v2.md`；执行记录（含 §0 顶层需求表与 §2.15 逐项交代）`docs/notes/performance-remediation-execution-record-2026-09-17.md`；决策文档 `docs/LingerLens-performance-remediation-decision-2026-09-17.md`；审计 `docs/notes/performance-audit-2026-09-17.md` 与 `...-options.md`；M1 归因 `docs/notes/performance-m1-translation-stage-attribution-2026-09-17.md`；给外部模型的上一份简报 `docs/notes/performance-remediation-guidance-request-2026-09-17.md`。

## 3. 验证证据（可复算）

```
npm run ci          # guard:release && guard:tracked && guard:licences && check:python && check:js && test:hls-companion && npm test
python scripts/run-hls-tests.py     # PASS=55 | tests run=683 | ~155-160s
node scripts/run-hls-js-tests.js    # 172 pass
npm test                            # 31 pass（注意：npm test 默认只匹配 tests/*.test.js，不跑 prototype 下的 JS 测试）
```

每个工作包在自己的提交信息里带 red/green：A2-T 6/6 绿、于 `3cc2093^` 上 6/6 红；B2-R 13 条中 8 条于 `ce1e47e` 上红；S1 页面侧 10/10 绿、于 `31e7c08` 版本 9/10 红；E1-R 22/22 绿、8 红于 `33f3dce`；A3 6/6 绿、2 红；R1 见下。

**R1 的关键数字**（同一段跨边界五包序列，pre-R1 产品码 vs 现在）：

| | 改动前 | 改动后 |
|---|---|---|
| 展开值 | `95442.717689, 95443.217689, 0.0, 0.5, 1.0` | `95442.717689 … 95444.717689` |
| 相邻步长 | 含**一步 −95443.217689 秒** | 四步全 +0.5 秒 |
| `sourcePtsLast − sourcePtsFirst` | **−95441.717689 秒**（负数进度） | **+2.0 秒** |
| `_source_clock_offset()`（A0=3s，V0=W−2s） | **None** | **5.0**（误差 ≤1 tick） |
| 精确偏移被拒时 anchor 的行为 | 从采样窗发布 5.0（无任何门控） | 停发（`ready=False`、`offset=None`） |

red/green 用一个临时目录 + `git show <sha>:<path>` 复现，**没有切换工作树、没有动产品树**；R1 的 red 证据脚本在工作区 `.scratch/r1-red-evidence.py`（gitignored，你读不到）。临时目录已删除。

## 3.5 实机实测结果（十次真实运行，全部数据）

按时间顺序排；**每一节都尽量给出「怎么测的、测到什么数字、我没有测到什么」**。执行者独立能做的观测到此为止，边界见 §3.5.14。

**为什么做**：本分支此前**全部证据都是测试进程内的**。逻辑够用，接线不够用——见下面第一条，它背后站着 687 个通过的测试。

**方法**：`prototype/hls-companion/scripts/live-backend-smoke.py`（本轮新增、已入库）。它起一个**真的** companion 服务器（真 HTTP、真 `yt-dlp`、真 `ffmpeg`、真 ASR），对它打 `/api/status`、`/api/start`、`/api/subtitles`、`/api/logs`、`/api/stop`，只记录投影后的字段（不含签名 URL、不含凭据）。请求体与 `player.js:735-748` 逐字一致——**这一点是第一次运行教会的**：服务器用 `body["subtitles"]["enabled"]` 作为字幕总开关，只发 `{"url": ...}` 会跑出一个「一切正常但没有字幕管线」的会话，于是 R1 的路径根本没被碰到。

**安全属性**（都是刻意设计的，复现时请保留）：整个脚本有硬墙钟上限；runtime 目录在 TEMP，**从不碰用户的真实配置/媒体目录**；只按 PID 与已登记的子孙进程结束，**从不按进程名杀**；`auth-snapshot.json` 只在导入 cookie 时写，本脚本从不导入。

### 3.5.1 最重要的结果：R1 上线时是**坏的**，实测抓到并已修（`ad04dee`）

第一次带字幕的真实会话，日志里只有一行就把问题说完了：

```
[warn/media] 源时钟不可用于精确对齐，字幕改用保守路径：source-clock-invalid:clock-validity-not-reported
```

**原因**：R1 把 `sourceClockValid` / `sourceClockReason` 只放进了 `snapshot()` 返回的**字典**里，而 `server.py` 的消费者问的是**对象**上的 `getattr(ingest, "sourceClockValid", None)`——对象从来没有这个属性。于是 `_leg_clock_state` 对**每一个会话**都回答「untrusted」。

**后果比「保守」严重得多**：拒绝同时会把 `sampled_fallback_allowed` 关掉，所以不是退回采样窗，而是**完全不再发布对齐字幕**：

| | 修前（run 2） | 修后（run 3） |
|---|---|---|
| 时钟拒绝日志行数 | **1** | **0** |
| `mediaAnchor` | 无（`ready: false`） | `{ready: true, samples: 17, offset: 4.985, exactOffset: 4.985, windowOffset: 0.4, spread: 0.25, drift: 0.1}` |
| 发布字幕条数 | **0**（会话已跑 80 秒、ASR 已转写 65 秒） | **27**（`maxSeq: 80`） |
| `pendingFinals` / `finalDiscarded` / `unmappedObservations` | — | **0 / 0 / 0** |

**为什么 687 个测试没抓到**：所有测消费者的用例都用**假腿**，而假腿写成 `SimpleNamespace(sourceClockValid=True)`——它们声明的正是真实对象缺少的那个字段。假腿只能和它照抄的接口一样正确，而它抄的是 bug。

**修法**：`YtdlpLiveIngest` 增加 `source_clock_state` 属性，**`/api/status` 的投影与消费者都读它**，只有一处计算，所以「读到的」和「据以行动的」不可能再分叉。

**能抓住它的两个测试**（已加，且已验证在旧码上红）：
- `test_a_real_leg_object_answers_the_question_the_consumer_asks`：真实 ingest + 真实 `_TcpPump` + 真实 `MpegTsPtsProbe` + 真实 `_record_pts`，喂真实 TS 包。在 `85420f2` 上失败信息是 `('untrusted', 'clock-validity-not-reported') != ('trusted', None)`——**与线上失败逐字相同**。
- `test_the_status_field_and_the_consumer_read_one_source_of_truth`：把「投影字段与属性必须同步」本身钉住。

red/green：临时目录 + `git show 85420f2:<path>`，30 条中**旧码 8 条红**（6 failures + 2 errors），新码 30/30 绿。

### 3.5.2 各次运行的原始数字

| | run 1 | run 2（修前） | run 3（修后） | run 4（脚本自检） |
|---|---|---|---|---|
| 请求体 | 只有 `url` | 完整 | 完整 | 完整但 `--no-subtitles` |
| `D1_sourceRecovery_absent` | **true** | **true** | **true** | **true** |
| `S1_mediaSessionId`（idle → 会话中） | null → `1ef0cbb4…` | null → `ef8d6877…` | null → `8c3f5224…` | null → `b1d5c8e2…` |
| 时钟拒绝行 | — | **1** | **0** | **0** |
| `sourcePtsFirst`（asr-audio / media video） | 无 ASR 腿 | `686.388656` / `671.4` | `26.384667` / `21.4` | `null` / `421.4` |
| 两腿 C（raw） | — | **14.988656 s** | **4.984667 s** | — |
| 两腿 C（tick 取模） | — | 14.988656 s（相等 → 无回绕） | 4.984667 s（相等） | — |
| `clockValid`（两腿、每个 pump） | — | true / null | true / true | true |
| 字幕条数 | 0（未请求） | **0** | **27** | 0（未请求） |
| 进程残留 | 0 | 0 | 0 | 0 |

**交叉验证**：run 3 里我从两腿首 PTS 自己算的 C 是 `26.384667 − 21.4 = 4.984667 s`，而 anchor 独立报出 `exactOffset: 4.985`——**同一毫秒级数字的两次独立计算**，一次来自我的取模算术、一次来自产品自己的路径。

**顺带确认了决策文档的一个断言**：同一会话里采样窗中位数是 `windowOffset: 0.4`，而真值是 `4.985`——**采样窗偏了 4.6 秒**，与文档记录的「偏 4.44 秒」同量级。所以「精确路径必须真的生效」不是整洁问题。

**四次运行的 PTS 基线**：671.4、21.4、421.4（三次带媒体腿的会话）。**同一个流、同一天，基线差了一个数量级**。它显然不是墙上时钟（否则每次都会很大且递增），更像是该流自己编码器的运行时长——但**我没有确认**。这件事直接影响 §6.1 的风险判断：只有当基线落在 2 秒附近时，那个守卫才会误伤，而基线本身在 21~671 之间游走，**离 2 秒并不远**。

### 3.5.3 顺带量到的其它真实数字（run 3，150 秒会话）

```
timelineSource: private-hls      captionSource: audio-leg      asrProviderId: asr-1 (Soniox)
translationProviderId: bailian-qwen35-flash (gemini-3.7-flash-low)   translationWorkersAlive: 4/4
pcmOffset: 144.5   asrSeconds: 144.5   captionChunks: 27   translationAttempts: 27
chunkSpanP50/P95/max: 2.1 / 6.18 / 6.84      chunkCutReasons: {terminal_punctuation: 26, clause_boundary: 1}
sourceReadyLagP50/P95: 7.26 / 12.46          totalReadyDelayP50/P95: 8.375 / 15.109
terminalOutcomeLagP50/P95: 9.09 / 15.006     readyLagP50/P95: 9.09 / 15.006
asrAdapterDelayP50/P95: 6.328 / 12.875       translationProviderDelayP50/P95: 1.547 / 2.75
asrReconnects: 0   degradeLevel: 0   latencyUnknown: 0   translationBacklog: 0   translationDropped: 0
translationDeadlineExpired: 0    translationFailures: 2    translationProviderFailures: 2
lastTranslationError: "TimeoutError"    lastError: "TimeoutError"    avgTranslationLatencyMs: 1898.5
sourceOnlyCues: 2   overlongCues: 1   pendingEvidenceOverSoftSpan: 12   spanOverSoftTarget: 2
translationContextMissingImmediatePredecessor: 10
```

**三条值得下一位执行者注意的**：

1. **`translationFailures: 2 / translationAttempts: 27`，且 `translationProviderFailures: 2`、`translationDeadlineExpired: 0`、`lastTranslationError: "TimeoutError"`。** 这是 M1 那个「75 条失败」现象在**受控短会话里的复现**：失败是**provider 层的超时**，不是期限到期、不是 B2-R 的资格跳过（跳过一次都没有）。provider 延迟 p95 是 **2.75 秒**，而预算是 6 秒。这意味着 M1 的归因问题仍然开放，但**现在有一个可重复的短实验**可以去查它，不必再依赖四份旧日志。
2. **延迟的主项是 ASR，不是翻译**：`asrAdapterDelayP50 6.328` 对 `translationProviderDelayP50 1.547`。目标的 `targetDelaySeconds` 是 15，实测 `totalReadyDelayP95 15.109` —— **p95 刚好压线**。
3. **费用无法从 status 读出**：`asrEstimatedCostCny: null`（reason「ASR pricing unavailable」）、翻译定价「incomplete for provider bailian-qwen35-flash」。能读出的是用量：本例 ASR 144.5 秒、翻译 24–27 次调用 / 18,104 tokens，**全部走 `bailian-qwen35-flash`，回退 provider 一次都没被触发**——**但注意：这只对我这几次运行成立。用户应用自己的运行里回退被触发了、而且也失败了，见 §3.5.7。** 要估价必须另配价目表。

**服务端资源也顺手验证了**：`/player.js` 200（132,794 字节，含 S1 的 `claim !== uiGeneration`）、`/playback-recovery.js` 200（11,633 字节，含 D1 的注记）。两个文件是**从服务器真取回来的**，不是磁盘上的。

### 3.5.4 复现方式

```bash
# 生产 venv；带字幕会有真实 ASR/翻译调用，--no-subtitles 则只验证播放
.venv-desktop/Scripts/python.exe prototype/hls-companion/scripts/live-backend-smoke.py \
    --stream https://www.youtube.com/@ANNnewsCH/live --watch 150 --out evidence.json
```

脚本会打印并写出：`checks`（D1/S1/R1 三项断言 + 资产）、`twoLegOffset`（两腿 C，raw 与取模并列）、`subtitlesEndpoint`（`cueCount`/`maxSeq`/`mediaAnchor` 与全部 stats）、`logs`、`serverStillAlive`。**每次运行都换一个端口和 TEMP runtime 目录，不会碰正在运行的应用。**

### 3.5.5 第五次运行：把剩下三件也补上了（一个服务器进程、两个会话）

**① 真实的 cue 字段名（上次我是猜的，读出来全是 null）**——`/api/subtitles` 的 cue 实际长这样：

```json
{"id":1,"seq":3,"tStart":1789649408.352711,"tEnd":1789649411.772711,"hold":1.2,
 "src":"短命爆の一つとされる「龍図の滝」。","zh":"被视为短命瀑布之一的“龙图瀑布”。",
 "state":"done","lang":"ja","timingSource":"asr","revision":3,"speaker":"1",
 "generation":3,"chunkOrder":1,"startsMidSentence":false,"endsMidSentence":false,
 "cutReason":"terminal_punctuation"}
```

字段是 **`src` / `zh`**（不是 `sourceText`/`translatedText`）、**`tStart` / `tEnd`**（不是 `start`/`end`）。**这就是整条字幕链路的端到端硬证据**：日语源文 + 中文译文 + 时间轴 + `state: "done"` + `timingSource: "asr"`，两个会话分别产出 **16 和 18 条**。

**② `-copyts` 在真机命令行上确实存在**（§6.2 原先只是从代码读出来的）——按 OS 看到的进程树：

```
yt-dlp.exe   copyts=True  mpegts=True     ← 两条腿各一个
yt-dlp.exe   copyts=True  mpegts=True
ffmpeg.exe   copyts=True                  ← yt-dlp 外部下载器的 ffmpeg
ffmpeg.exe   copyts=True
ffmpeg.exe   copyts=False                 ← 打包 ffmpeg（吃 TCP 输入，本来不需要）
```

**③ 一个服务器进程内的 会话 A → stop → 会话 B**：

| | 会话 1 | 会话 2 |
|---|---|---|
| `mediaSessionId` | `f9330488f34ae63add1b8e70f1d291e4` | `1280be91c9b5fab77a6e555f4283a800` |
| 字幕条数 | 16 | 18 |
| `anchor` | `exactOffset 5.007`，`windowOffset 7.3`，`spread 4.1` | `exactOffset 4.992`，`windowOffset 5.3`，`spread 0.2` |
| `translationFailures` | 2 | **0** |
| stop 之后 | `state: idle`、`mediaSessionId: null`、`sourceIngestCount: 0`、`subtitlesRunning: false` | 同左 |

**这就是 A1 的 teardown 工作被真机跑了两遍**：同一个进程里字幕管线完整拆掉、再完整起来，`pendingFinals` 两次都是 0，**没有残留 ingest、没有会话重叠**。

**顺带纠正我自己在 §6.2 末尾写错的一句。** 我说「腿间差每会话可变」，现在有三次会话的 `exactOffset`：**4.985、5.007、4.992**——**稳定在 ~5.0 秒**，与历史记录的 5.006 秒一致。真正可变的是**音频腿起步慢时**的情形：run 2 的 C 是 14.99 秒，因为那次音频腿晚了约 10 秒才出第一个 PTS。这**不是缺陷**：C 和该腿的 PCM 计数器是同一条腿同一次起步的产物，腿晚起步 10 秒时 C 和 pcm 一起平移 10 秒，映射 `privateMedia = pcm + C` 不变。这正是代码把原点对**锁存一次、之后拒绝移动**的原因。所以 600 秒界和 `< 2.0` 守卫针对的是**两条腿各自的首 PTS**，不是 C。

### 3.5.6 第六次：真实页面点击（计划要求的 Start→Stop→Start，已在正在运行的应用里做完）

用 CDP 驱动**用户正在运行的开发版应用**（`lingerlens://app/`，Electron 44.2.0 / Chrome 152，`--remote-debugging-port=9222`）。页面本体就是播放器（有 `#video`、`#url`、`#probe`、`#start`、`#stop`，**没有任何 iframe/webview**），所以这是 `player.js` 的真实运行环境。全部通过**点它自己的按钮**完成，未改任何设置；开始播放前把 `#video` 静音，所以没有出声（ASR 走服务端自己的音频腿，静音不影响它）。

| | 第一轮 | 第二轮 |
|---|---|---|
| 解析 → 可开始 | true | true |
| 点击 开始播放 后 | **延迟播放中**，`hiddenDelay 5.0 秒`，缓冲 15.4 → **77.4 秒**，`1280×720 @ 30fps`，`readyState 4`，`paused false` | **延迟播放中**，`hiddenDelay 3.0 秒`，缓冲 14.2 → **59.2 秒**，`1280×720 @ 30fps`，`readyState 4`，`paused false` |
| 点击 停止 后 | **已停止**，`startDisabled true`、`stopDisabled true` | 同左 |

**零 console 错误、零页面异常、零日志条目。**

**这就把 S1 的存在理由直接证伪了（在好的方向上）**：停止之后页面**能够**完整回到可工作状态并**再开一场**、真的播起来。S1 要防的那个失效模式——停止的栅栏再也放不下来——**在真实点击下没有出现**。

**两个观察，如实记录，不算缺陷**：
1. **停止之后 `#start` 是 disabled 的，必须重新解析才能再开**——这是页面**本来就有**的流程（它自己的提示就写着「已停止。可以重新解析，或粘贴另一场直播的链接。」）。我第一次跑这个测试时直接点了这个 disabled 按钮，于是第二轮什么都没发生；**那是我的脚本错，不是页面的错**，已在脚本里改成「每一轮都先重新解析」。
2. 第二轮里 `uptime` 有一次从 `53.2 秒` 跳到 `8.2 秒` 再回 `59.2 秒`，同时 `hiddenDelay` 从 3.0 变回 5.0。**单次采样，我没有解释它**（可能是页面重新 attach 或自适应延迟调整）。记在这里，免得被当成没发生过。

### 3.5.7 页面实测顺带撞上的：翻译失败是**两个 provider 都超时**

跑完页面测试后，应用自己的诊断栏从 **5 条变成 17 条**（我这两场会话贡献了 12 条），内容是：

```
translation failed, showing source text only: RuntimeError: all translation providers failed:
gemini-3.7-flash-low: TimeoutError; deepseek: TimeoutError: translation deadline has expired
```

**这条比我在后端会话里量到的东西更重**：那里我只看到主 provider（`bailian-qwen35-flash`）超时 2/27，而且**回退一次都没被触发**；而用户应用自己的运行里，**回退 `deepseek`（真实付费 API）被调用了，并且也 `TimeoutError`**。所以 M1 的失败不是「一个 provider 慢」，而是**两个 provider 都超时、整体期限到期**。

这与 B2-R 的实现直接相关：那条消息里同时出现了「provider 失败」与「deadline has expired」两种语义，而 B2-R 特意区分「没被调用」与「调用失败」正是为了让这两种情况在日志里可分辨。**目前我无法从这条 UI 摘要判断 `deepseek` 是真的被调用后超时，还是期限已到而未被调用**——要分辨它，得去看该应用那次运行的 `/api/logs`（我够不到应用后端的随机端口与 session token）。**这是给下一位的一件具体、便宜的事。**

### 3.5.8 `-copyts` 的 A/B：re-base 被**测到**了，守卫也被**测到**了

§6.2 原来只能引用代码注释里那条旧测量。现在它是本机实测，方法是**把 companion 包整体复制到临时目录**，只改那一对 argv（`"--downloader-args", "ffmpeg_i:-copyts"`），仓库与正在运行的应用**都没碰**。两次运行同一条流、同样 40 秒、同一台机器。

| | `media/video` 首 PTS | 说明 |
|---|---|---|
| 对照（原样） | **2131.4** | 绝对源时间 |
| 处理（去掉那一对 argv） | **1.417689** | **mpegts 复用器的默认输出原点** |

注释里写的 1.400，**实测复现为 1.417689**。§6.2 从「代码这么说」变成「这里测到」。

**再跑一次带字幕的处理组，看守卫会不会抓到、代价是什么**：两腿各自 re-base 到 `video 1.409533` / `asr-audio 1.4`，**两个探针都 `clockValid: true`**（时钟没坏，是**原点被改写**），日志打出

```
[warn/media] 源时钟不可用于精确对齐，字幕改用保守路径：origin-rebase-or-wrap-ambiguous
```

结果是 `mediaAnchor {ready: false, offset: null, exactOffset: null, windowOffset: 10.4, spread: 0.3, drift: 0.0}`、**`cueCount: 0`**——但 `subtitlesRunning: true`、`asrSeconds 74.6`、`captionChunks 12`，也就是**管线照跑、字幕一条不发**。

**这次实测给出了此前缺失的那个数字**：如果不拦，精确路径会算出 `A0 − V0 = 1.4 − 1.409533 ≈ −0.0095 秒`（≈0），而**独立**的采样窗说 **10.4 秒**（spread 0.3，很紧）。所以守卫挡掉的不是「一个更差的估计」，而是**偏差约 10.4 秒的错位**。§6.1 的取舍因此第一次有了两边的数字：

| 情形 | 精确路径 | 守卫 | 结果 |
|---|---|---|---|
| `-copyts` 正常 | 算出可信 C | 不触发 | 正常发字幕 |
| `-copyts` 失效（回归） | 会算成 ≈0（**错约 10.4 秒**） | **触发、拒绝** | 该会话 0 条字幕 |

### 3.5.9 M1：**不是 provider 超时**，是显示预算先耗光；而聊天翻译是发条

用**用户自己的配置**（`%APPDATA%\lingerlens\runtime\providers.json`，读前读后 sha256 一致，`1619D5B9…53C2`）跑独立后端，两次各约 180 秒，唯一差别是**有没有开聊天翻译**：

| | 不开聊天 | 开聊天 + 聊天翻译 |
|---|---|---|
| provider 链 | `fallback:bailian-qwen35-flash,translation-1` | 同左 |
| 调用数 / tokens | 26（20+6）/ 17,327 | **40（35+5）/ 27,191** |
| `translationFailures` / `providerFailures` | 0 / 0 | **1 / 1** |
| `translationDeadlineExpired` / `translationDropped` | 1 / 1 | 1 / 1 |
| `finalDiscarded` / `sourceOnlyCues` | 0 / 1 | **2 / 2** |
| `lastTranslationError` | 空 | **`TimeoutError`** |
| 日志 | — | **`[error/translation] translation failed, showing source text only: TimeoutError`** |
| 字幕条数 | 27 | 42 |

**那行日志与应用诊断栏里的那条逐字相同**，所以用户应用里那 17 条诊断，就是这一族。

**但「TimeoutError」这个名字会骗人，机制在代码里**：

- `subtitle_pipeline.py:220`：`class TranslationDeadlineExpired(asyncio.TimeoutError)`——**显示预算耗尽是 `asyncio.TimeoutError` 的子类**，所以它一路冒上来时名字就叫 `TimeoutError`。
- **两条不同的丢失路径，必须先分清**：
  1. **队列缝丢弃**（`subtitle_pipeline.py:1543-1550`）：进队列前预算已 `<= 0`，**直接丢、不调用任何 provider**，只把 `translation_deadline_expired += 1`。它上面那句注释恰好预言了这次的误读：这么做是为了「不让陈旧的启动积压变成一串**误导性的 provider 超时错误**」。**这条路不会产生「translation failed」那条消息。**
  2. **调用中被期限打断**：预算在 provider 往返期间耗尽，`TranslationDeadlineExpired` 在调用内部抛出。
- 实测的 provider 延迟是 **P50 1.094 / P95 2.094 秒**（开聊天）对 **6 秒**预算——**provider 一点都不慢**，跑光的是**这条字幕自己的显示预算**，而 provider 的往返只要 1～2 秒，在余量已经为负的情况下足够让窗口在调用途中关闭。

**所以 M1 的机制是**：显示预算的余量长期贴着零甚至为负（见 §3.5.11 里应用自己的 `budgetMargin`），于是 provider 往返途中窗口关闭 → 该次调用被 `translation deadline has expired` 打断 → 因为期限作用于整条链，回退 provider 同样被打断 → 最终聚合成「all translation providers failed」。任何额外负载（聊天翻译让调用数 +54%）都会把更多 chunk 推过线，于是只显示原文（`sourceOnlyCues`）。

**可选修法方向**（提高目标延迟、压低单 chunk 延迟、或让聊天翻译不与字幕抢同一预算）**属于产品决定，不是执行者该自选的**。它同时否掉了上一版 handoff 里的一个问题提法：**「deepseek 是被调用后超时还是没被调用」不是判断依据**——`fallback.py:269` 的格式（由 B2-R 建立并被它自己的测试钉住）已经把「没被调用」写成 `not called (why)`，所以消息里出现 `deepseek: TimeoutError: ...` **就证明它被调用了**。B2-R 在这里是**承重的**：没有它，这条消息根本无法区分这两种情况。

### 3.5.10 预算锚在哪：找到了，而且**修法验证过了**

`translation_budget.py:59` 的 `allocate()` 是那条链的最后一环：

```python
deadline = now + self.provider_timeout_seconds          # 6 秒
if playback_delay_seconds is not None and audio_end_wall is not None:
    target_delay = float(self.playback_delay_seconds())
    age = max(0.0, self.wall_clock() - audio_end_wall)   # 该 cue 的音频结束到现在
    playback_window = max(0.0, target_delay - age)
    deadline = min(deadline, now + playback_window)
```

而生产上那个回调是 **`server.py:1057`：`playback_delay_seconds=lambda: self.target_delay_seconds`**——**配置的那个目标延迟，不是自适应的 hiddenDelay**。所以：

> **每条字幕的预算 = min(6 秒, 目标延迟 − 该 cue 的 age)**

**先纠正我自己搞错的一个字段**：应用的 `budgetMargin` **不是**每条字幕的翻译预算，而是页面侧的告警量：

```js
// Cues are displayed from tStart, so the delay must cover the sentence
// itself as well as the time spent producing its translation.
const margin = totalDelaySeconds - p95 - (spans ? spans.p95 : 0);
```

即 **`budgetMargin = totalDelay − readyLagP95 − chunkSpanP95`**。所以**「age ≈ 17.5 秒」这个反解是我从错误字段推出来的，撤回**。它真正说的是：**总延迟连「p95 就绪延迟 + p95 句子时长」都覆盖不住**——因为字幕从 `tStart` 就开始显示，延迟必须同时覆盖句子本身和生成它的时间。

**而它能与 per-cue 预算接上是同一件事的两面**：`server.py:1057` 把**同一个** `target_delay_seconds` 既当总延迟、又当预算锚点。所以余量为负时，cue 到达翻译队列就已经没有窗口了。两条路径由同一个数字决定，这不是巧合。

**这个公式还给出一个可检验的预测**：要余量为正，延迟至少要 ≥ `readyLagP95 + chunkSpanP95`。用 §3.5.9 那次 15 秒会话的实测值算：**14.972 + 6.48 = 21.45 秒**。于是延迟 25 秒应当够用——**而 A/B 在 25 秒档确实是零丢失**（下表）。预测与实测对上了。

**于是「把目标延迟调大」是不是有效修法，可以直接做 A/B**（两次都开聊天+聊天翻译、都用用户的配置，唯一差别是 `--target-delay`）：

| | 目标 **15 秒** | 目标 **25 秒** |
|---|---|---|
| `translationFailures` / `providerFailures` | 1 / 1 | **0 / 0** |
| `translationDeadlineExpired` / `translationDropped` | 1 / 1 | **0 / 0** |
| `finalDiscarded` / `sourceOnlyCues` | 2 / 2 | **0 / 0** |
| `lastTranslationError` | `TimeoutError` | 空 |
| 错误日志行 | 有 | **无** |
| `readyLag` P50 / P95 | 8.509 / 14.972 | 7.661 / **12.523** |
| 字幕条数 | 42 | 43 |

**同样负载下，把目标延迟从 15 提到 25，这一场里所有翻译丢失都消失了。**

**必须说清证据强度**：这是**每档一场、各 180 秒**，15 秒那档的失败率本来就低（41 次尝试里 1 次）。所以这是**方向性证据，不是测出来的比率**——25 秒档「零失败」不能证明失败率是零。它之所以可信，是因为机制独立成立（预算 = min(6, 目标−age)，而 age 的 p95 已经顶满 15）。

**这条把 M1 从「有现象」推到了「有机制 + 有验证过的修法方向」**，但**采取哪种修法仍然是用户的产品决定**：调大目标延迟（代价是字幕更晚）、压低上游 ASR/分块延迟（`asrAdapterDelayP50` 5.8～6.3 秒是主项）、或让聊天翻译不与字幕抢同一预算。三条路我都没有擅自选。

复现：`live-backend-smoke.py --providers <应用配置> --chat --chat-translate --target-delay 25`（默认读仓库 dev 配置，要复现用户的现象必须换成应用那份）。

### 3.5.11 应用自己的数字：预算余量长期为负（M1 的结构性原因）

上面 §3.5.9 是我在**独立后端**上用**用户的配置**复现的。为了看**用户应用自己**的数字（它的后端随机端口 + session token，够不到 `/api/logs`，而 dev 日志这次没开），我用 CDP 驱动应用跑了一场真实会话，只读它自己的检查器字段，**没有改任何设置**：

```
目标延迟输入框: 15        实际 hiddenDelay: 5.0 秒 -> 3.0 秒（不是自适应，见 §3.5.12）
budgetMargin:  -8.8s  -2.4  -2.5  -2.5  -2.5  -2.8  -2.8  -2.8  -2.8  +0.2  -0.4  -1.0  +3.0  +0.2  -0.6
readyLag:      13.82s/13.82s  ->  7.56s/13.81s
translationLatency: 0.83 - 1.69 秒        schedulerDrops: 0 丢 / 0 迟到
cueDuration: 2.0 - 2.5s / 4.4 - 4.9s      timingSources: asr 100% · vad 0%
diagCount: 17 -> 20（这场会话新增 3 条，且 chatTranslate = false）
```

**预算余量 15 个采样里有 12 个是负的。** 这就是结构性原因：应用绝大多数时间**在超出自己的显示预算运行**，provider 往返只要 1～2 秒，而余量在 −0.5 秒这个量级——**窗口在调用途中关闭是常态而不是意外**。聊天翻译只是把它推得更狠的一种方式，不是必要条件（这一场 `chatTranslate = false` 仍然新增了 3 条诊断）。

**两条副产物，都值得记**：
- **配置的 15 秒与实际的 3～5 秒不是一回事**：`targetDelay` 输入框是 15，而实际 `hiddenDelay` 是自适应降到 5.0/3.0 秒的。所以「把目标延迟调大」是不是有效修法，**取决于那个自适应值是怎么算出来的**——这一点我没有查。
- `schedulerDrops: 0 丢 / 0 迟到`：这一场**没有**发生队列丢弃，所以这 3 条诊断走的是 §3.5.9 的第 2 条路径（调用中被期限打断），与 §3.5.9 末尾从消息格式推出的结论一致。两条独立证据指向同一条路径。

### 3.5.12 延迟分解：**没有自适应，全是算术**（并撤回我自己的两处说法）

三个常量（`server.py:110-112`）：

```python
DEFAULT_TARGET_DELAY_SECONDS = 15.0
MIN_TARGET_DELAY_SECONDS = 11.0
PLAYER_LIVE_SYNC_SECONDS = 12.0
_publisher_delay(target) = max(0.0, target - PLAYER_LIVE_SYNC_SECONDS)
```

而发布器按**整段**释放（`core.py:890-897`：只有 `hidden − segment.duration >= publish_delay` 才放行），所以实际隐藏量落在 **`[publish_delay, publish_delay + 段长)`** 区间里抖动。全部对得上实测：

| 目标延迟 | `publish_delay = 目标 − 12` | 实测 `hiddenMediaSeconds` |
|---|---|---|
| 15 | 3 | **3** 与 **5**（都在 [3, 8) 内 ✓） |
| 25 | 13 | **15**（在 [13, 18) 内 ✓） |

并且 `estimatedTotalDelaySeconds = sourceDelay + hiddenMedia + 12` → `0 + 3 + 12 = 15` ✓ 正好等于目标。

**所以我要撤回自己写过的两句话**：

1. ~~「hiddenDelay 自适应降到 5.0/3.0 秒」~~——**没有自适应控制器**。它就是 `目标 − 12`，在整段粒度上抖动。我看到的 5.0 → 3.0 是段边界，不是调节。
2. ~~「预算锚在一个并非真实播放延迟的数字上，可能偏宽」~~——**锚的就是真实总延迟**，而且是**正确**的：字幕要在 `audio_end + 总延迟` 那一刻出现在屏幕上，预算用同一个数字是应该的。`estimatedTotalDelaySeconds = 15` 证实了这一点。

于是 §3.5.10 那条链可以写得干净了：**预算的构造是对的**；出问题的是**上游把 cue 交到翻译手上时，窗口已经不够了**。

**而应用自己早就在说这件事。** 页面里那段告警（`player.js:1940-1954`）：

```js
const margin = totalDelaySeconds - p95 - (spans ? spans.p95 : 0);
...
if (Date.now() - subtitleBudget.lowSince < 30_000) return;     // 必须连续 30 秒为负
subtitleBudget.suggested = Math.max(11, Math.min(60, needed));
el("applyDelayButton").textContent = `增加 ${subtitleBudget.suggested} 秒缓冲`;
```

我在应用会话后用 CDP 读到那个按钮上的字是 **「增加 18 秒缓冲」**——也就是应用用它自己的公式算出 **33 秒**。而我用实测值手算的阈值是 `readyLagP95 + chunkSpanP95 = 14.972 + 6.48 = 21.45 秒`，并且 A/B 在 **25 秒**档已经零丢失。**三方独立地指向同一个方向，应用的建议比我的 A/B 还保守。**

**对用户最直接的一条结论**：M1 那个「翻译失败、只显示原文」不需要改代码就能缓解——**把缓冲调大（应用已经给了按钮）**，或者把目标延迟从 15 提到 25～33 秒。代价是字幕更晚。要不要接受这个代价，是用户的产品决定。

### 3.5.13 两个页面抢同一会话（S1 最后没覆盖的场景）：**已测，通过**

Electron 拒绝 `Target.createTarget`（实测返回 `"Not supported"`），所以改用 **Chrome 打我自己起的后端**：同一份 `player.js`、同一份 `server.py`、两个真标签页，**完全不碰用户的应用**。开始前把 `#subtitlesEnabled` 取消勾选，所以这次不产生任何 ASR/翻译费用（这个场景考的是会话身份，不是字幕）。

| 步骤 | 结果 |
|---|---|
| A 解析并开始 | 会话 `aada05112c1ae0d9df98d45779de77ad`，**延迟播放中**，`readyState 4`，`720p30` |
| 开第二个标签页 B（它什么都没启动） | **B 直接接管了 A 的会话**：状态「延迟播放中」、`uptime 17.2 秒`、`stopDisabled false` |
| **B 按「停止」** | 服务端 `mediaSessionId → null` |
| **A 必须察觉** | **3 秒内**变为「已停止」，无异常、无卡住的栅栏；`startDisabled true` 是设计行为（需重新解析） |
| **A 还能再开一场吗** | **能**：重新解析→开始→会话 `6029997459f9f5fcec6b139a71d93dba`（**不同 id** ✓），**延迟播放中、`readyState 4`** |
| 两个页面的异常 | **都是 0** |
| 结束状态 | 服务端 `idle / null`，A「已停止」，B 关闭 |

**这就是 S1 存在的理由被正着验证**：一个**从未启动过任何会话**的页面可以停掉别人的会话，而**拥有那个会话的页面会察觉、并且仍然完全可用**（还能再开一场）。没有卡死的栅栏，没有「我的停止没生效」的假象。

**第一次跑的时候我得到一个含糊的结果，这里必须记下来**：那次 A 在第二轮停在「就绪待播放」、视频 `readyState 4` 但 `paused: true`，65 秒没播起来。原因不是产品——**新建 B 让 B 成了活动标签页，headless Chrome 会节流后台标签页的定时器和 rAF**。在 B 创建后加一次 `Target.activateTarget(A)`，第二轮就正常到「延迟播放中」。**含糊的那一条我没有当成结论，重跑消掉了它。**

**这次测试的边界，说清楚**：环境是 **Chrome + 我自己的后端**，不是 **Electron + 用户的应用**；每页各一场会话，没有测两个页面**同时**发 start、或有页面在中途刷新。Electron 里做不到的部分（`Target.createTarget` 不支持）我没有绕过去假装做到。

### 3.5.14 这几次实测**没有**覆盖的

- **回绕仍然没有发生过**（不是没测，是没等到）。九次带 PTS 的会话里 raw 都等于取模，没有一次跨越 26.5 小时边界。所以「可确认的回绕路径」在真机上**仍然只有合成回放的证据**——**但守卫本身已经用 §3.5.8 的办法实测过**（拿掉 `-copyts` 造出同族的歧义原点），所以「守卫会不会触发、代价是什么」不再是未知。
- ~~**没有 Electron、没有页面点击**~~ **已做，见 §3.5.6**：在正在运行的应用里真实点了 解析→开始→停止→解析→开始→停止，两场都真的播起来（缓冲涨到 77.4 / 59.2 秒、`readyState 4`、720p30），两场都干净停止，零 console 错误。
- ~~**多标签页并发抢占同一会话**~~ **已做，见 §3.5.13**（Chrome + 我自己的后端，两页两会话，通过）。**仍然没有覆盖的**：两个页面**同时**发 start 的竞争、页面中途刷新、以及 Electron 里的同等场景（Electron 拒绝 `Target.createTarget`，我没有绕过它假装做到）。
- ~~**`/api/subtitles` 的 cue 字段名未证实**~~ **已证实，见 §3.5.5。**
- **没有测 U1/U2/U3**，一次都没有；这三条仍需用户的手、眼与长时墙钟。
- **单个会话最长 180 秒**（页面会话 150 秒），所以「越跑越卡」在应用层有没有任何表现，这几次实测给不出证据。**要回答它只能靠 L1 的 120 分钟长跑，而那需要用户授权。**
- **`devLogClient` 那条路没测过**（页面「记录日志到文件」）。这次能读到应用自己的检查器数字靠的是 CDP 读 DOM；**应用后端的 `/api/logs` 仍然够不到**（随机端口 + 主进程注入的 session 头，且页面上下文的 fetch 不进 resource timing）。

---

## 4. 还没做的，分五类（**这是本文档最主要的部分**）

### 4.1 等用户授权或材料（执行者做不了，也不该自己决定）

**已解除一项**：真机后端实测已获授权并做完（§3.5，四次运行，脚本已入库）。它抓到了一个 687 个单元测试漏掉的接线缺陷。剩下三项：

1. **L1 真实 120 分钟同会话长跑**：未授权、未跑。因此 **U3「越跑越卡」保持未验收**——不能从「清理了十条缺陷」推导它已解决。
2. **R1 的歧义类输入**：见 §6.1，行为已按批准实现，但**问题本身未解决**，需要真机材料。
3. **S1 的一次真实 Electron Start→Stop→Start**：未跑，所以「无会话重叠、无残留进程」仍未验证。

### 4.2 机制未接线（不需要新设计，但需要用户认可「要不要做」）

- `watchdog.py` 存在且是唯一真正的 kill switch（`--pids-file` / `--driver-pid` / `--deadline-epoch` / `--cpu-guard 90` / `--tick 2.0` / `--log` / `--dry-run`；只杀列出的 PID 及子孙，**从不按进程名杀**），但**没有任何东西会为 `lag-measure.py` 启动它**。
- `lag-measure.py` 自身还缺：没有 argparse（裸 `sys.argv`：URL、DURATION、`--attach`、`--tag`）、**没有硬墙钟上限**、**没有输出上限**、`MAX_HEIGHT` 定义未用。即：长跑目前只有「外部 watchdog」一层保险，采样器自己没有「自己停下来」的能力。这两条都在工作区 `.scratch/laglingo-audit/`（你读不到）。
- 用户批准清单第 7 项「L1 的运行费用和允许退出的实例」批的是**语义**，接线尚未做。

### 4.3 计划明确规定「不做」（不是遗漏）

不重审原九条断言、不重算四份 dev 日志的 49/26/9、不实现 C2 持久 HTTP session / B1 增量 token / 四个休眠 C1 适配器回收、不做跨屏触发与 MPO／注册表／显示适配器实验、不重开 `disable-gpu` 长跑对照、不接自动重连、不重启下载腿。

### 4.4 侦察时发现、计划没派活、因此没动

- `source_timeline.py` 里 `_AUDIO_TYPES` 与 `SourceTimeline` 是死代码（R1 只动 probe，没顺手删）。**为什么没删**：我没找到它们的设计出处，分不清是遗留还是某件未完成功能的准备。
- `.scratch/live-caption-onset-v1/browser-observer.js` 第 68 行设 `video.muted = true` 且**从不恢复**，并且无保护地解引用 `#subtitleLayer` 与 `video`。这与「不要静音」的用户要求直接冲突，但我不知道它是有意还是 bug。
- M0 采样器代码就绪但**从未采集过**（当时探测：`state: "idle"`、窗口 `hidden: true`）。
- L1 分析器自己声明过的诚实缺口：manifest schema 是猜的（23 个可疑键）、会话/配置身份字段不在它记录的 6 个键里、遮挡从未采样、256 MiB / 7260 秒是「有界退出」而不是「失控采样器会停」、假设 (f) 通常只得到「数据不足」、rVFC 的 p95 是有标记的推导值。
- **M1 的更细归因是可做的、但没人认领**：四份 dev 日志在工作区 `.scratch/dev-logs/dev-20260916-123749.log`、`dev-20260916-162253.log`、`dev-20260916-170423.log`、`dev-20260917-013209.log`（你读不到，需要用户上传）。计划只在「旧数字发生争议」时才要求读它们。

### 4.5 **执行者自己留下的缺陷**（最该先修的一类）

1. ~~**`source_timeline.py` 的 `_last_pts` 变成了只写不读的状态。**~~ **已修（`ad04dee`）。** R1 用基于 raw tick 的规则取代了旧的 `value < self._last_pts` 比较，于是第 58 行声明、第 158 行赋值，没有任何读取者。经用户同意后一并删除，并在原位留了一条注释说明为什么不再需要「上一个返回值」。
2. **A1 覆盖计划 D/G 九行中的 8 行。** 未覆盖的一行是「provider 关闭时产生的 tail final 不得从完整字幕路径上被丢掉」——drain 本身有测试（`test_a_tail_final_emitted_at_close_is_still_consumed`），start 失败路径另有覆盖，但这一行本身没有。我**不知道怎么诚实地补**（见 §6.3）。
3. **S1 的 `stop()` 有一个相对旧行为的收窄**：屏障先判 `pendingStop`、后判 idle 采样，所以停止请求在途时 idle 也不解锁。这是 v2 §W4 规定的顺序，我照做并用单独测试钉住了，但它确实是收窄，值得复核。
4. **B2-R 的 `R ≤ 0` 偏离**：原决策文档 §3.2 的写法我做了偏离，后来由 B2-R 的「每次尝试检查总期限」取代。已在执行记录 §3 登记。

## 5. 执行者在这台机器上「能实测什么、不能实测什么」

用户明确问了这一点，所以写清楚，**不要假设下一位执行者能跑所有东西**。

**能做、而且已经做过了（§3.5）**：

- 单独启动 companion Python 后端（不需 GUI），用真实 HTTP 打 `/api/status`、`/api/start`、`/api/subtitles`、`/api/logs`、`/api/stop`，并对真实 24/7 流跑真实会话。**四次运行，脚本已入库：`prototype/hls-companion/scripts/live-backend-smoke.py`。** 它一次就抓到一个 687 个单元测试漏掉的接线缺陷（§3.5.1），所以**下一位执行者在声称任何接线完成之前都应该先跑它**。
- 用真实 `yt-dlp` + `ffmpeg` 拉真实 24/7 流（本次用到 `@ANNnewsCH/live`），观察两腿真实 `sourcePtsFirst`、`sourceIdleSeconds`、`clockValid`、`mediaAnchor`。**已得到真数字**（§3.5.2、§3.5.3）。**仍未得到回绕**：三次带 PTS 的会话 raw == 取模。

**还没做、但技术上可行（需要用户点头，因为会起真实进程 / 真实网络 / 产生费用）**：

- ~~跑 Electron 并驱动 player 页面做计划要求的**一次** Start→Stop→Start。~~ **已做，见 §3.5.6**，脚本与后端那个并列入库：`prototype/hls-companion/scripts/live-page-smoke.js`（Node 22+ 自带 WebSocket，通过 CDP 驱动**已在运行**的应用，按「解析→开始→停止」两轮跑；只点页面自己的按钮，只额外把 `#video` 静音以免出声）。
- **应用后端的日志环怎么读**：应用的后端用随机端口 + 每次启动的 session token，页面自己也不发那个头（应该是主进程注入的），而页面上下文里的 fetch 不进 resource timing，所以**从前端侧够不到 `/api/logs`**。**这一条已经解决，而且不需要读日志**：`fallback.py:269` 把「没被调用」写成 `not called (why)`，而应用那条消息里两个 provider 都带着 `TimeoutError`，所以**两个都被调用了**；用应用配置在独立后端上复现（§3.5.9、§3.5.10）进一步确认了机制是预算而非 provider。若仍要看应用自己的日志，两条路线：**(a)** 打开应用自带的「记录日志到文件」再复现一次；**(b)** 用用户自己的 providers 配置在独立后端上复现（我的 `live-backend-smoke.py` 默认读仓库里那份 dev 配置，与应用的配置不是同一份，这大概就是为什么我的运行里回退一次都没被触发）。**(b) 更便宜、不碰应用，§3.5.9 已按此做过。**
- 更长的会话（>150 秒）以观察退化、或让它跨过 26.5 小时边界。
- **多标签页/多窗口并发抢占同一会话**：这是 S1 唯一还没被真实覆盖的场景。技术上可以做（在应用里或我自己的服务器页面上开第二个标签页，驱动它对同一会话发 start/stop），但它比上面那些更容易把状态搅乱，**需要用户明确同意**。

**不能**：

- **任何视觉/体感类**：U1「鼠标不跟手」、U2「跨屏后整机冻结」、U3「越跑越卡」。它们需要用户的手、眼与真实显示器栈，且 U3 需要数小时墙钟时间。计划本身也只允许「被动记录」。
- **真等 26.5 小时看回绕**。只能合成回放（已做）。计划明确「不先跑真实26.5小时来确认已知数学边界」。
- **复现真实的 provider close**（A1 未覆盖那一行需要真实 ASR 会话）。真实 ASR 主 provider 走本机 gateway、回退 provider 是**真实付费 API**——任何长跑都要先算这笔账。
- **不改注册表 / 不碰 MPO / 不碰显示适配器 / 不按进程名杀**（用户与计划的硬规则）。

**实测时的硬规则**：不许留下任何游离进程；按 PID + 创建时间匹配，只有登记过的 PID 才可结束；不重启任何下载腿（签名清单 URL 有 `expire` 6 小时，且 `source_pts_first` 重置会把精确锚点打到一个实测偏差 **4.44 秒**的路径上）。

## 6. 执行者真正搞不懂的（按风险排序）

### 6.1 R1 的「首原点 < 2 秒」拒绝：风险已量化，但没有消除（最高风险）

回绕本身是确定的数学：33 位 PTS @ 90 kHz，周期 **95,443.717688 秒 = 26.512144 小时**。歧义不在回绕，而在**一条腿的第一个 PTS 落在 0～2 秒**这种开局，它有三种长得一样的成因：

1. 合法：腿恰好在回绕刚过之后启动，首 PTS = 0.5 秒；
2. mpegts 复用器的 re-base 伪原点（默认输出原点 **1.4 秒**）——这条腿从来没有过绝对时钟；
3. 数值上恰好像回绕、但其实是上游重置且未带 discontinuity 标记。

1 与 2 都是「0～2 秒」，**单看两腿首次数值无法区分**；而区分决定 `C = A0 − V0` 是对的还是**自信地错**（两条 re-base 原点相减得 ~0）。按用户批准的第 6 项，我把整类**明示为不可确认**（原因串 `origin-rebase-or-wrap-ambiguous`），不取模硬凑、也不退回采样映射。

**实测把风险量化了三件事**（§3.5.2）：

- 三次带媒体腿的真实会话，首 PTS 分别是 **671.4、21.4、421.4**——**同一个流、同一天，差一个数量级**。它显然不是墙上时钟，最像该流自己编码器的运行时长，**但我没有确认**。
- 因此「2 秒」离真实值**并不远**：最近的一次只有 **21.4 秒**，是阈值的 ~10 倍。如果某天基线落在 2 秒附近（流刚开播、或刚跨过回绕），守卫就会触发，而代价是**那条会话整段不再发布对齐字幕**。
- 同时，实测也确认了守卫**在正常会话上不会误触发**（两腿都 ≥ 2.0，全部 `clockValid: true`、`clockReason: null`，`exactOffset` 正常算出 4.985）。

**所以剩下的问题不是「守卫对不对」，而是「代价可不可以接受」**：它只在合法情形上误伤时才有代价，而那种情形**概率低但代价是那一次会话完全没有字幕**。计划接受了这个取舍，理由建立在「第 2 种是真实可能的」之上——见 §6.2，那条理由**现在是站得住的**。要不要重新权衡，是用户的产品决定（§7 第 4 项）。

### 6.2 一条腿到底会不会被 re-base —— **已从代码回答：会，但只在 `-copyts` 失效时**

这个我原先解不开的自相矛盾，现在有答案了。`ytdlp_ingest.command()` 里：

```python
"--downloader-args", "ffmpeg_i:-copyts",
```

是**无条件**写进命令的（`ffmpeg_live_args` 总是被拼进返回值），而且它上面那段注释记录了一次**真机测量**（2026-09-16，TBS NEWS DIG / ANNnewsCH 两条流）：

> the mpegts muxer's default output offset is what rewrites it: **1.400 without this flag, ~27000 with it**, rising with wall clock, **on both the video-only and the audio-only leg**

所以结论是：

- **`-copyts` 生效时**：两腿首 PTS 都是绝对源时间，re-base 不会发生 → 第 2 种成因不可达，`< 2.0` 守卫只在第 1 种（合法回绕后启动）上触发。
- **`-copyts` 失效时**（有人改了 `ffmpeg_live_args`、或 yt-dlp 改了 `--downloader-args` 的解析方式——注释里恰好记录了「同名 downloader key 的重复 `--downloader-args` 是**拼接**而不是替换」这条被实测过、因此也可能随版本变化的性质）：两腿首 PTS 都是 ~1.4 → **守卫是唯一能发现这件事的东西**。

所以守卫不是多余的谨慎，它是**对 `-copyts` 回归的唯一检测**。这一点让 §6.1 的取舍偏向了「保留」。**而且这一点已在真机上确认过**（§3.5.5）：按 OS 看到的命令行，`yt-dlp.exe` 与它拉起的下载器 `ffmpeg.exe` **都带 `-copyts`**，打包用的那个 ffmpeg 不带（它吃 TCP 输入，本来不需要）。所以「`-copyts` 一直在生效」现在是实测事实，不是代码推断。

**仍未见过的是 1.400 本身**：我没有在真机上见过 re-base，也没有去制造它（制造它等于故意破坏一个正在工作的会话）。所以「守卫能抓到 `-copyts` 回归」这一步是推理，不是实测。

顺带：实测两次会话的腿间差是 **14.99 秒**和 **4.98 秒**，而仓库记录的历史值是 5.006 秒。**腿间差本身是每会话可变的、正常的**（两条腿各自从 HLS 活窗边界开始读，差几个分片），所以 600 秒界不会因此绷紧；要小心的是「腿间差大」不代表异常。

### 6.3 A1 那一行未覆盖的测试，我不知道怎么诚实地补

「provider 关闭时产生的 tail final 不得从完整字幕路径上被丢掉」需要一个真实 provider 进程关闭的时序。我可以用 fake 模拟关闭，但那正是「用手写模型代替真实行为」——v2 计划明确禁止这种做法（S1/E1-R/A3 都因此要求抽取真实函数）。所以这一行我留着不补，而不是用假证据填上。

### 6.4 `_AUDIO_TYPES` / `SourceTimeline` 是遗留还是未完成功能

死代码，但我找不到设计出处（没有任何文档引用），所以分不清删掉会不会毁掉某人的计划。

### 6.5 `browser-observer.js` 静音视频、且无保护解引用

第 68 行 `video.muted = true` 且不恢复，与「不要静音」的要求冲突；同时无保护地解引用 `#subtitleLayer` 与 `video`。**我判断不了它是有意（诊断期避免出声）还是 bug**，所以没动。

### 6.6 U2 的根因仍然完全未知

轻症只录到过一次 **4.65 秒 rAF 回调空档**，计划已经把三条原本写成「已排除」的推理降级为「证据限制」。重症从未按原步骤复现。我没有任何关于根因的假设能站得住。

### 6.7 翻译失败（49/26/9）的真实成因

我曾声称「B2 消除了那 9 次和冷却误判」，**已撤回**。75 是**已记录的失败行数**（下界：队列过期不写行）。计划明确说关键词与异常名前缀不能承担全部因果，所以真正成因（排队／网关／网络握手）**未知**，需要读四份 dev 日志重做归因（见 §4.4）。

### 6.8 `_reset_source_clock()` 是否覆盖了所有会话重启路径

我把「原点锁存 + 拒绝记录」的重置放在字幕管线启动处（`server.py` 里紧邻原有 `self._source_clock_origins = None` 的位置），并核对了「会话之前两个 ingest 都是 None → 视为未测到、不会误记拒绝」。但我**没有枚举**这个产品里所有可能的会话重启路径。

## 7. 需要用户拍板的决定（供下一位执行者组织提问）

**0（新，最可操作的一条）。字幕「翻译失败、只显示原文」要不要按实测结论调整目标延迟？**
实测：预算 = `min(6 秒, 目标延迟 − cue 的 age)`（§3.5.10），而应用自己的余量公式要求 `目标延迟 ≥ readyLagP95 + chunkSpanP95`，**用实测值算是 21.45 秒**，而默认值是 15。A/B（同负载、同用户配置）**15 秒档有 1 次 provider 失败 / 2 条丢弃 / 2 条只显示原文；25 秒档全部为 0**（§3.5.10）。**应用自己也已经在建议 `增加 18 秒缓冲`（→33 秒）**（§3.5.12）。所以这是「改默认值 / 改文档 / 什么都不改，让用户自己点那个按钮」之间的产品选择，**执行者不该自选**。代价是字幕更晚。三条可选方向：调大延迟、压低上游 ASR/分块延迟（`asrAdapterDelayP50` 5.8～6.3 秒是主项）、或让聊天翻译不与字幕抢同一预算。

1. ~~**是否授权一次短的真机后端运行**~~ **已做（§3.5），并且抓到了一个会让 R1 在每个会话上失效的缺陷。** ~~是否授权在 Electron 里做那一次真实 Start→Stop→Start~~ **也已做（§3.5.6），多页面场景同样做了（§3.5.13）。** 实测层面**执行者能独立完成的都已经做完**，见 §3.5.14 列出的边界。
2. **是否授权 L1 真实 120 分钟长跑**（费用 + 机器时间 + 需要先接 kill switch）。不授权则 U3 永远保持未验收。实测已知：带字幕的一次 150 秒会话消耗 ASR 144.5 秒、翻译 24 次调用 / 18,104 tokens，**价目不可得**（`asrEstimatedCostCny: null`），所以费用必须先估。
3. **是否让执行者接 L1 的 kill switch**（只接线、不跑长测）。
4. **R1 的 `< 2.0` 守卫要不要重新权衡**：现在有了两边的证据（§6.1 代价、§6.2 它是 `-copyts` 回归的唯一检测）。要么接受「合法回绕后启动 = 整段无字幕」，要么要求换一种可验证的源时间依据（那就需要材料，见下）。
5. **R1 歧义材料**：两腿同一会话的首/末 PTS、边界前后的真实 PES PTS 与 discontinuity 标记、会话/阶段时间、必要时短 TS 片段与生成参数；**缺可信前史就明确说缺**。合成回放给不出这个——合成的序列正是已经假设了答案的序列。（实测已经提供了「同一会话两腿首 PTS」这一项的一半材料：14.99 s 与 4.98 s 两次，但**没有一次跨过边界**。）
6. **是否清理剩下这几条自己留下的东西**：`_last_pts` 已删；仍待处理的是 A1 那一行未覆盖的测试（§6.3）、S1 `stop()` 的先 `pendingStop` 后 idle 这一处相对旧行为的收窄（§4.5.3）。
7. **是否要 PR / 合并到 `main`**：现在成果都在 `wip/subtitle-anchor-correction`，**56 个提交**领先 `origin/main`（`fda1319`），尚未开 PR。

## 8. 环境与硬规则

Windows；32 逻辑核；RTX 5070 Ti 16 GB；MPO **启用**；约 93.6 GB 内存。Python 3.10（aiohttp）后端 + Electron 外壳（`desktop/main.cjs`）+ 原生 JS player（`prototype/hls-companion/web-player/player.js`）。

测试运行器在**仓库根目录**，不是产品根目录：`scripts/run-hls-tests.py`（每个文件一个子进程；`--only` 是文件名子串过滤；发现零文件退出 2；用了 unittest 但一个都没跑标 `EMPTY` 退出 1）、`scripts/run-hls-js-tests.js`（`readdirSync` + `/^test_.*\.js$/`；零文件退出 2）。**`npm test` 只匹配 `tests/*.test.js`，不跑 `prototype/hls-companion/tests/*.js`**——那个目录归 `npm run test:hls-companion`。

共享测试工具 `prototype/hls-companion/tests/player-harness.js`：从**真实 `player.js`** 按名字切出函数、断言两侧边界、花括号配对找函数体结尾、在 `vm` 里编译，自由变量由测试显式给。S1/E1-R/A3/C3 都用它，R1 的 probe 测试用它同一套思路（真实 TS/PES 包回放）。**下一位执行者应该复用它，不要为 player.js 里的函数另写模型。**

**用户反复强调的硬规则（不要违反）**：
- 工作树**同时只能有一个作者**；不要为产品代码开并行工作树（`player.js` / `server.py` 是 S1/E1-R/A3/C3/R1/D1 的共同热点）。
- 每次改动**之前**要能还原（每项一个可回退提交）；**机制类改动必须先跟用户讨论取得认可**，不能自己造。
- 「最优雅、最精巧、最根本、也最简单」的修改；不要改用户真实的设计。
- **证据高于叙述**：数字要标来源，不知道就说不知道，不要把「没测」写成「已解决」。
- **不留游离进程**；不按进程名杀；不重启下载腿；不碰 MPO／注册表／显示适配器；未获授权不做跨屏实验。
- UI-only 改动不必跑慢测试；长跑必须有独立 kill switch。
- 密钥在 `%APPDATA%\lingerlens\runtime\providers.json`（**绝不复制进仓库、绝不粘贴**）；非密钥部分可引用：主 provider `bailian-qwen35-flash`（label `gemini-3.7-flash-low`，本机 gateway，`timeoutSeconds 6`，`maxTokens 256`）；回退 `translation-1`/`deepseek-flash`（**真实 API**，`maxTokens 1024`）。

**绝不能忘的一条产品事实**：`C_true = A0 − V0` 是精确语义，已经定案，**不要重新争论这个**。

## 9. 建议的 skills

如果你的环境有这些 skill（它们出现在执行者这一侧的目录里，不一定在你那边；没有就按名字的含义手动做）：

- **`code-review`** —— 对 `origin/main..HEAD`（46 个提交）按 Standards 与 Spec 两轴复核。这是最值得先做的一件事：十二个提交里 R1 与 S1 的判定逻辑是新写的。
- **`diagnosing-bugs`** —— 用于 U2（跨屏整机冻结）与 U3（越跑越卡）这类「先有现象、根因未知」的问题；不要直接跳到改代码。
- **`playwright`** —— 如果要驱动 Electron 页面做 Start→Stop→Start，或读取 `--remote-debugging-port=9222` 上的真实页面状态。
- **`screenshot`** —— U2/U3 需要视觉证据时的系统级截图。
- **`codebase-design`** —— 如果决定重审 MediaAnchor 与源时钟这条缝（R1 就在这条缝上）。
- **`research`** —— 需要外部资料（例如 MPEG-TS 的 `discontinuity_indicator` 语义、HLS 变体时钟行为）时，把结论落到仓库里。
- **`tdd`** —— 补 A1 那一行或 6.8 的路径枚举时。
- **`handoff`** —— 下一轮交接时再用一次，保持这条链。

## 10. 下一次会话最省事的三个开场

1. **先跑一次真机 smoke，再看别的**：`prototype/hls-companion/scripts/live-backend-smoke.py`（§3.5.4）。它便宜、有界、自我清理，而且已经证明能抓到单元测试抓不到的接线缺陷。**任何「接线已完成」的声明都应该先过它。** 特别注意 `checks.R1_clockRefusals` 必须是 0——上一轮它是 1，而那时 687 个测试全绿。
2. **把 §4.4 的无人认领项逐条决定「做／不做＋理由＋归属」**，并处理 §4.5 剩下的两条（A1 那一行、`stop()` 的收窄复核）——这是 v2 计划 §0 那三条交接规则要求的动作。
3. **如果用户要 U3 有结论**，那么顺序必须是：接 kill switch → 校准仪器 → 冻结代码 → 跑 120 分钟 → 用 `scripts/analyze-duration-degradation.py` 出结论（它只有离线分析能力，**不跑测量**）。

**如果用户想要的是「字幕质量」而不是「性能」**，最短路径已经现成：`live-backend-smoke.py --watch 300 --out …`，然后读 `subtitlesEndpoint.stats` 里的 `readyLagP50/P95`、`totalReadyDelayP50/P95`、`chunkSpanP50/P95`、`translationProviderDelayP50/P95`、`translationFailures`。run 3 已经给出了基线数字（§3.5.3），其中**最有价值的一条是 p95 总延迟 15.109 秒对目标 15 秒——刚好压线**，而瓶颈是 ASR（p50 6.3 秒）不是翻译（p50 1.5 秒）。

---

**一句话给下一位执行者**：代码层面 v2 计划已经做完并且可回退地推送了；**真正剩下的不是写代码，而是三类东西——用户要不要授权真机实测与长跑、我留下的几条清理项、以及一批我明确不知道自己不知道的根因（U2、U3、翻译失败成因、re-base 是否可达）。请不要把这三类混成一句「已全部完成」。**
