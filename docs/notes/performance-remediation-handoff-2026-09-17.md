# Handoff：v2 计划执行完之后（给下一位执行者 / 只能读仓库的外部模型）

**写这份文档的原因**：`docs/LingerLens-performance-remediation-work-plan-2026-09-17-v2.md` 的十个工作包已全部执行完，但执行者（我）在做完之后仍然有一批**卡点、能力边界和搞不懂的事**。它们散落在提交信息与执行记录里，不足以让下一个人直接接手。这份文档只写这些，不重复别的文档。

**消费者说明**：如果你的运行环境只能读本仓库（不能读执行者的工作区、不能执行代码），那么**本仓库里能读到的东西就是全部证据**；工作区里 gitignore 的东西（`.scratch/`）你读不到，我会在提到它们时说明「你读不到，需要用户转述或另行上传」。

**语言**：用户用中文提需求；产品的代码注释、提交信息、测试名都是英文。文档是中文（沿用本批文档的惯例）。

---

## 1. 一句话现状

分支 `wip/subtitle-anchor-correction`（`origin/main` = `fda1319` 之后再 46 个提交，HEAD = `435fddb`）已完成 v2 计划全部十个工作包，每项一个（S1 两个）独立可回退提交，全部已推送、工作树干净、`npm run ci` 退出 0。**没有任何一项被声称已验收，除它自己的测试之外。**

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

## 4. 还没做的，分五类（**这是本文档最主要的部分**）

### 4.1 等用户授权或材料（执行者做不了，也不该自己决定）

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

1. **`source_timeline.py` 的 `_last_pts` 变成了只写不读的状态。** R1 用基于 raw tick 的规则取代了旧的 `value < self._last_pts` 比较，于是第 58 行声明、第 158 行赋值，**没有任何读取者**。
   ```bash
   git grep -n "_last_pts" -- "*.py"     # 只有 source_timeline.py:58 与 :158
   ```
   修法是一行（删掉声明与赋值）。**我没有在宣布完成后自行改它**，因为用户要求「每次改动之前都要 Git 回复一下」（可回退），也要求机制类改动先讨论；但这条是纯清理，任何下一位执行者可以顺手删。
2. **A1 覆盖计划 D/G 九行中的 8 行。** 未覆盖的一行是「provider 关闭时产生的 tail final 不得从完整字幕路径上被丢掉」——drain 本身有测试（`test_a_tail_final_emitted_at_close_is_still_consumed`），start 失败路径另有覆盖，但这一行本身没有。我**不知道怎么诚实地补**（见 §6.3）。
3. **S1 的 `stop()` 有一个相对旧行为的收窄**：屏障先判 `pendingStop`、后判 idle 采样，所以停止请求在途时 idle 也不解锁。这是 v2 §W4 规定的顺序，我照做并用单独测试钉住了，但它确实是收窄，值得复核。
4. **B2-R 的 `R ≤ 0` 偏离**：原决策文档 §3.2 的写法我做了偏离，后来由 B2-R 的「每次尝试检查总期限」取代。已在执行记录 §3 登记。

## 5. 执行者在这台机器上「能实测什么、不能实测什么」

用户明确问了这一点，所以写清楚，**不要假设下一位执行者能跑所有东西**。

**能（技术上可行，但需要用户点头，因为会起真实进程 / 真实网络 / 可能产生费用）**：

- 单独启动 companion Python 后端（不需 GUI），用真实 HTTP 打 `/api/status`、`/api/start`、`/api/stop`。这能给出**真的**载荷级证据：D1 真的不再有 `sourceRecovery`、S1 的 `mediaSessionId` 真的在换、R1 的 `sourceIngest[].clockValid` 真的出现、A1 在真实解码器失败下真的排空 stderr、B2-R 面对真实 localhost 网关的期限行为。**这一类实测我一次都没做过**（所有证据都是进程内测试），是最便宜、最该先补的一块。
- 用真实 `yt-dlp` + `ffmpeg` 拉一条 24/7 流（本次会话已验证可用：`@ANNnewsCH/live`、`@tbsnewsdig/live`、`@ntv_news/live`），观察两腿真实 `sourcePtsFirst`、`sourceIdleSeconds`、`clockValid`。**这直接回答 §6.1 与 §6.2 的两个未知**。
- 跑 Electron 并按要求驱动 player 页面（`npm run desktop:dev:log -- --remote-debugging-port=9222`，日志落 `.scratch/dev-logs/`），做计划要求的**一次** Start→Stop→Start。风险：这会启动 GUI 应用，而这台机器历过整机冻结（U2），所以**必须用户在场同意**。
- 离线跑 L1 分析器（已做，47 个测试）。

**不能**：

- **任何视觉/体感类**：U1「鼠标不跟手」、U2「跨屏后整机冻结」、U3「越跑越卡」。它们需要用户的手、眼与真实显示器栈，且 U3 需要数小时墙钟时间。计划本身也只允许「被动记录」。
- **真等 26.5 小时看回绕**。只能合成回放（已做）。计划明确「不先跑真实26.5小时来确认已知数学边界」。
- **复现真实的 provider close**（A1 未覆盖那一行需要真实 ASR 会话）。真实 ASR 主 provider 走本机 gateway、回退 provider 是**真实付费 API**——任何长跑都要先算这笔账。
- **不改注册表 / 不碰 MPO / 不碰显示适配器 / 不按进程名杀**（用户与计划的硬规则）。

**实测时的硬规则**：不许留下任何游离进程；按 PID + 创建时间匹配，只有登记过的 PID 才可结束；不重启任何下载腿（签名清单 URL 有 `expire` 6 小时，且 `source_pts_first` 重置会把精确锚点打到一个实测偏差 **4.44 秒**的路径上）。

## 6. 执行者真正搞不懂的（按风险排序）

### 6.1 R1 的「首原点 < 2 秒」拒绝，可能对**合法**情形过狠（最高风险）

回绕本身是确定的数学：33 位 PTS @ 90 kHz，周期 **95,443.717688 秒 = 26.512144 小时**。歧义不在回绕，而在**一条腿的第一个 PTS 落在 0～2 秒**这种开局，它有三种长得一样的成因：

1. 合法：腿恰好在回绕刚过之后启动，首 PTS = 0.5 秒；
2. mpegts 复用器的 re-base 伪原点（默认输出原点 **1.4 秒**）——这条腿从来没有过绝对时钟；
3. 数值上恰好像回绕、但其实是上游重置且未带 discontinuity 标记。

1 与 2 都是「0～2 秒」，**单看两腿首次数值无法区分**；而区分决定 `C = A0 − V0` 是对的还是**自信地错**（两条 re-base 原点相减得 ~0）。按用户批准的第 6 项，我把整类**明示为不可确认**（原因串 `origin-rebase-or-wrap-ambiguous`），不取模硬凑、也不退回采样映射。

**我不确定的地方**：如果第 2 种在本产品里根本不可能发生（`-copyts` 一直在生效，两腿首 PTS 实测是绝对源时间 27886.406 / 27881.4），那么这个 `< 2.0` 守卫就只在第 1 种——**完全合法**的情形——上触发，代价是**那条会话整段不再发布对齐字幕**（`exact_offset` 一直 None、`ready` 一直 False、`offset` 一直 None，直到下一次新会话）。这个代价是不是可接受，我没有把握；计划接受了它，但计划的理由建立在「2 是真实可能」之上。**我无法从仓库里确认 2 是否可达**，因为没有任何记录显示一条腿曾被 re-base（也没有反向记录）。这需要一次真实运行去观察两腿首 PTS 的分布。

### 6.2 这个产品里一条腿到底会不会被 re-base（同一个问题的另一半）

`server.py` 的注释说 `-copyts` 让两腿保持源时间戳、「而不是让每条腿的 mpegts 复用器 re-base 到自己的 1.4 秒原点」；`ytdlp_ingest.py` 的 `start()` 注释又说「本地 ffmpeg 复用器把每条腿的输出 re-base 到自己的 1.4 秒原点」。**这两句话是互相矛盾的读法**，我没有解开。它直接决定 6.1 的风险有多大。

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

1. **是否授权一次短的真机后端运行**（起 companion、打真实 HTTP、可选拉一条真实 24/7 流）。这是最便宜也最缺的一类证据，能同时回答 6.1、6.2、4.1.3 的一部分。需要先说清：会起真实进程、真实网络、ASR 主走本机 gateway 但**回退是真实付费 API**、以及长跑需要先接 kill switch。
2. **是否授权 L1 真实 120 分钟长跑**（费用 + 机器时间 + 需要先接 kill switch）。不授权则 U3 永远保持未验收。
3. **是否让执行者接 L1 的 kill switch**（只接线、不跑长测）。
4. **R1 的 `< 2.0` 守卫要不要重新权衡**：接受「合法回绕后启动 = 整段无字幕」，还是要求另找可验证的源时间依据（那就需要材料，见下）。
5. **R1 歧义材料**：两腿同一会话的首/末 PTS、边界前后的真实 PES PTS 与 discontinuity 标记、会话/阶段时间、必要时短 TS 片段与生成参数；**缺可信前史就明确说缺**。合成回放给不出这个——合成的序列正是已经假设了答案的序列。
6. **是否清理 §4.5 那几条自己留下的东西**（`_last_pts`、A1 那一行、`stop()` 的收窄复核）。
7. **是否要 PR / 合并到 `main`**：现在成果都在 `wip/subtitle-anchor-correction`，46 个提交领先 `origin/main`（`fda1319`），尚未开 PR。

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

1. **先做一次真机后端实测**（须用户授权）：它最便宜，且能一次性回答 6.1、6.2，并为 §4.1.3 铺路。不要从长跑开始。
2. **先把 §4.5 的三条自己留下的东西清掉**（`_last_pts` 一行、`stop()` 收窄复核、A1 那一行的处理方式），并把 §4.4 的无人认领项逐条决定「做／不做＋理由＋归属」——这是 v2 计划 §0 那三条交接规则要求的动作。
3. **如果用户要 U3 有结论**，那么顺序必须是：接 kill switch → 校准仪器 → 冻结代码 → 跑 120 分钟 → 用 `scripts/analyze-duration-degradation.py` 出结论（它只有离线分析能力，**不跑测量**）。

---

**一句话给下一位执行者**：代码层面 v2 计划已经做完并且可回退地推送了；**真正剩下的不是写代码，而是三类东西——用户要不要授权真机实测与长跑、我留下的几条清理项、以及一批我明确不知道自己不知道的根因（U2、U3、翻译失败成因、re-base 是否可达）。请不要把这三类混成一句「已全部完成」。**
