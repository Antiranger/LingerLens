# LingerLens 字幕系统：实测现象汇总（客观记录）

**日期**：2026-09-18　**分支**：`wip/subtitle-anchor-correction`　**用途**：把当前所有已实测到的字幕问题，连同证据、复现方法和相关代码位置，原样交给下一位处理者。

**本文的写法约束（请按此读）**：只写**可复现的观察、测量方法、原始数字和代码事实**。不写修法建议、不给方案排序、不做产品取舍。凡属推断而未经对照实验验证的，集中放在 §6 并明确标注。

---

## 1. 环境与运行方式

| 项 | 值 |
|---|---|
| 机器 | Windows；32 逻辑核；RTX 5070 Ti 16 GB；约 93.6 GB 内存 |
| 后端 | Python 3.10 + aiohttp（`prototype/hls-companion/companion/`） |
| 外壳 | Electron 44.2.0 / Chrome 152（`desktop/main.cjs`）+ 原生 JS 播放器（`prototype/hls-companion/web-player/`） |
| 跑哪个后端 | **应用跑的是仓库里的后端**：`main.cjs` 调用 `startBackend({packaged: app.isPackaged, ...})`，实测命令行是 `.venv-desktop\Scripts\python.exe desktop\companion_entry.py --data-dir C:\Users\MSI-NB\AppData\Roaming\lingerlens` |
| 启动命令 | `Start-Process F:\Projects\LagLingo\build-desktop\electron\electron.exe -ArgumentList ".","--remote-debugging-port=9222" -WorkingDirectory F:\Projects\LagLingo` |
| 页面 | `lingerlens://app/`（**这个页面本身就是播放器**，无 iframe；元素 `#video` `#url` `#probe` `#start` `#stop` `#stateBadge`） |
| 调试通道 | CDP `http://127.0.0.1:9222`；页面内 `fetch('/api/status')`、`fetch('/api/subtitles?afterSeq=0')` 可直接取后端数据 |
| Python 环境 | `py -3.10` 有 psutil / websocket-client / Sudachi；`.venv-desktop`（3.11）**没有** psutil / websocket / pytest |
| 测试运行器 | `py -3.10 scripts/run-hls-tests.py`（55 文件 / 695 测试）、`node scripts/run-hls-js-tests.js`（176）、`npm test`（31）、`npm run ci`（以上全部 + 守卫） |

**关键常量（代码事实）**

| 常量 | 值 | 位置 |
|---|---|---|
| `DEFAULT_TARGET_DELAY_SECONDS` | 15.0 | `companion/server.py:110` |
| `MIN_TARGET_DELAY_SECONDS` | 11.0 | `companion/server.py:111` |
| `PLAYER_LIVE_SYNC_SECONDS` | 12.0 | `companion/server.py:112` |
| 发布提前量 | `max(0, target − 12)` = 3.0 秒 | `companion/server.py:459-460` |
| `HARD_DEADLINE_SECONDS` | 7.0 | `companion/caption_chunker.py:33` |
| `_CONTINUATION_GAP` | 1.2 | `caption_chunker.py:34` |
| `_RESIDUAL_GRACE` | 1.2 | `caption_chunker.py:38` |
| 弱标点的最短长度闸门 | 4.0 秒 | `companion/punctuation_boundaries.py:103` |

**翻译预算（决定"没译文就不显示"的那个公式）**

- `companion/subtitle_pipeline.py:1355`（**在字幕入队那一刻**求值）：
  `audio_end_wall = wall_clock() − max(0, pcm_offset − chunk.end_pcm)`
- `companion/translation_budget.py:70-73`：
  `target_delay = playback_delay_seconds()`（生产接的是 `server.py:1057` → `target_delay_seconds`，默认 15.0）
  `age = max(0, wall_clock() − audio_end_wall)`
  `playback_window = max(0, target_delay − age)`
  `deadline = min(now + provider_timeout(6.0), now + playback_window)`
- 因此 `playback_window = 0` ⇒ `deadline = now` ⇒ **provider 不会被调用**（不是"翻译失败"，是"从未调用"）。

---

## 2. 数据来源：每次运行与每个仪器

**7 次带采样器的真实运行**（文件在 `output/soak/lag/`，采样间隔 5 秒；代码版本按提交号）：

| 采样文件（tag） | 时长 | 当时的代码 | `captionChunks` | `chunkCutReasons` | 翻译 attempts / failures / expired / dropped / sourceOnly | `readyLagP50/P95` |
|---|---|---|---|---|---|---|
| `lag-samples-20260917-232219-l1-20min.jsonl` | 1205 s | 改动前 | 未投影 | 未投影 | 242 / 4 / **29** / **29** / **33** | 6.271 / 12.167 |
| `lag-samples-20260918-001636-l1-upstream.jsonl` | 1205 s | 改动前 | 337 | clause 50 / terminal 286 / endpoint 1 | 335 / 4 / 0 / 0 / 4 | 6.965 / 11.309 |
| `lag-samples-20260918-005958-l1-fixed.jsonl` | 422 s | 含 bug 的硬到期 | 140 | clause 35 / **hard_deadline 19** / terminal 84 / endpoint 2 | 135 / 0 / 4 / 4 / 4 | 7.465 / 12.822 |
| `lag-samples-20260918-010956-l1-stubfixed.jsonl` | 879 s | 硬到期修正后 | 205 | clause 18 / terminal 187 | 203 / 0 / 1 / 1 / 1 | 5.132 / 9.162 |
| `lag-samples-20260918-012708-l1-vetofix.jsonl` | 1200 s | `a577b6c` | 312 | clause 44 / hard_deadline 1 / terminal 266 / endpoint 1 | 307 / 0 / 1 / 1 / 1 | 6.059 / 11.267 |

**两次无采样器、靠 CDP 抓 cue 正文的短会话**：

| 编号 | 时间 | 当时的代码 | 抓取文件 | 播放覆盖 |
|---|---|---|---|---|
| **R6** | 2026-09-18 02:33 | `e769c1e` + `6274324`（本执行者所加，见 §4） | `.scratch/cue-dump-broken.json`、`.scratch/api-status.json`（17 123 B 那一版）、`.scratch/broken-cues.txt` | 78 秒 |
| **R7** | 2026-09-18 03:0x | 回退后（= `832d801`） | `.scratch/cue-dump-baseline2.json`、`.scratch/api-subtitles.json`（15 934 B 那一版）、`.scratch/lag-timeline.jsonl` | 287 秒 |

**R5 的 cue 正文**由四次抓取合并去重（页面与后端都只保留滚动窗口）：`.scratch/cue-dump-vetofix{,-b,-c,-d}.json` + 当时的 `.scratch/api-subtitles.json` → 222 条唯一字幕。

**仪器脚本（均在 `.scratch/`，未入库）**

| 脚本 | 作用 |
|---|---|
| `cdp-cue-dump.js` | 从页面 `window.__lingerlensSubtitleCues` 抓全部 cue（含 `src` `zh` `state` `cutReason` `hold` `tStart/tEnd`） |
| `cdp-api-dump.js` | 从页面内取 `/api/subtitles?afterSeq=0`、`/api/logs`、`/api/status` 三份原始载荷 |
| `cdp-stop-session.js` | 点 `#stop` 并等状态离开"运行中" |
| `l1-start-session.py` | 输入直播链接并点 Start，等播放到可用分辨率 |
| `l1-longrun.py <分钟> <tag>` | 起采样器 + 看门狗（独立 kill switch）+ 到点自动停会话 |
| `probe-lag-timeline.py <秒> <out>` | 每 2 秒取一次 `/api/status`，记录延迟分解（只读，不动会话） |
| `audit-cues-generic.py`、`span-floor-analysis.py`、`list-cues.py`、`delay-breakdown.py`、`all-run-counters.py` | 离线统计 |
| `why-cut.py` | 把真实字幕串喂回生产切分引擎，打印 Sudachi 词性与否决结果 |
| `trace-soniox-fixture.py` | 用 Soniox 事件形状逐帧跟踪"哪一条路径发布了这条字幕" |

**测量口径的已知限制（会影响任何复算）**

1. 页面与后端的 cue 列表都是**滚动窗口**：R5 开头 354 秒的 cue 正文已永久丢失（只剩计数器）。
2. 采样器每 5 秒一帧，且**不投影** `asrAdapterDelay*` / `chunkerPolicyDelay*`（这些只在 `/api/status` 里有）。
3. `/api/status` 的 `*DelayP50/P95` 是 `deque(maxlen=60)` 的滚动窗口；stage lag 的采样条件是**六个标记（`audioPushed / evidenceAvailable / chunkEmitted / translationStarted / providerFinished / cueReady`）全部非空且单调**（`subtitle_pipeline.py:1761-1784`），缺任一就 `_latency_unknown += 1` 并 `return`。翻译**失败**也会写 `provider_finished`（`:1721-1722`）因而**进入**统计；被排除的是**没有走到 provider 的那批**（队列到期、排队被丢弃等，`providerFinished` 为空 ⇒ 该 cue 的所有 stage 样本都不记）。
4. 百分位取法是 `index = round(fraction × (n−1))`（`subtitle_pipeline.py:1803-1805`）：**n=20 时"P95"就是第 19 个样本、n=25 时是第 24 个**，即窗口没满时"P95"≈ 最慢的那一两条，不是分布。
5. 会话开头音频腿是追赶式的，所有延迟量在开头都偏大（见 §3-P4）。

---

## 3. 问题清单

### P1 字幕被切碎

**现象（原样摘录，均来自真实抓取）**

- R7（回退后代码）：单条 `と、`、`西。`、`日本。`、`大気。`、`大。`、`60。`、`えー。`、`その。`、`少し。`
  其中 `東。`+`西。`+`日本。` 三段原本是"东日本"；`6。`+`60。` 原本是 60。
- R5：`仕事。` 与 `を熱心にされた方で、…` 被切成两条；`細野。` / `さんが所属されている…`；`11日、。`

**量化**

| 指标 | R5（222 条 / 943 秒） | R7（82 条 / 287 秒） |
|---|---|---|
| 条数速率 | 14.1 条/分 | 17.1 条/分 |
| 以 `。` 结尾 | 81% | 73.2% |
| **以助词开头**（＝上一条切在词组中间） | **27 条 = 12.2%** | **16 条 = 19.5%** |
| 以接尾辞/名词后续开头（さん/側/税） | 5 条 = 2.3% | 0 |
| **≤4 个字符** | **17 条 = 7.7%** | **15 条 = 18.3%** |
| 自身时长 < 0.25 秒 | 11 条（全部 0.060 秒） | 见 P6 |
| 含 `、。` | 4 条 | 1 条 |
| 相邻字幕间隙 > 0.25 秒 | 174 / 221 | — |
| 屏幕空档总计 / 最长单次 | 127.6 秒 / 17.22 秒 | 28.7 秒 / 6.36 秒 |
| 切断原因分布 | terminal 191 / clause 30 / endpoint 1 | terminal 50 / clause 17 / **endpoint 15** |

R7 的时长直方图（`tEnd − tStart`）：`<1s` 21、`1-2s` 17、`2-3s` 12、`3-4s` 8、`4-6s` 19、`≥6s` 5。**短于 4 秒的占 58/82 = 71%**；按切断原因拆：terminal 44 条、endpoint 14 条。

**涉及的代码事实**

- `caption_chunker._drain_ready()`（`caption_chunker.py:570-585`）把 `select_boundaries()` 返回的**每一个候选都立刻 `_emit()` 发布**，没有"最后一段先留着"的规则。
- `punctuation_boundaries.select_boundaries()`（`punctuation_boundaries.py:89-122`）在"切点右侧尚无文字"时（`after >= len(words)`，行 58-67）依然把该切点作为合法候选返回，reason 为 `terminal_punctuation`。
- **句号类切点没有任何最短长度闸门**；唯一的长度闸门在 `punctuation_boundaries.py:103`，只作用于 `、`/`,` 这类弱标点（要求 `end − begin ≥ 4.0`）。
- lane 的键是 `("speaker:<标签>", 语种)`，**跨 utterance 存活**；仅当新 unit 比 lane 末尾晚 `_CONTINUATION_GAP = 1.2` 秒以上（或时间回跳超过 `_TIMESTAMP_JITTER = 0.06`）才关闭并新建 lane（`caption_chunker.py:245-253`）。
- `HARD_DEADLINE_SECONDS = 7.0` 是兜底而非节奏：R5 的 312 次切断里 `hard_deadline` 只出现 1 次；R7 的 82 条里 0 次。
- 全仓库检索 `。` 的用法：**没有一处是在字幕文本后追加 `。`**（命中的只有测试夹具里的字面量、翻译提示词里的中文句号、以及 `player.js` 里的 HTML 模板字符串）。切分器只**使用**文本中已有的标点，不构造标点。
- Soniox 适配器把 token 文本原样拼接后当作整句文本（`providers/asr_soniox_realtime.py` 的 `_finish_utterance`：`text = "".join(str(token.get("text", "")) for token in tokens).strip()`），不做标点增删。

**复现**：起会话 → `node .scratch/cdp-cue-dump.js <out>` → `py -3.10 .scratch/audit-cues-generic.py <out>`。

---

### P2 端点出口会直接把短句发出去

**现象**：R7 的 15 条 `utterance_endpoint` 字幕里，**14 条短于 4 秒**，多为 0.060 秒（`西。` `日本。` `大気。` `大。` `60。`）。R5 同一计数只有 1 条（共 312 条），R4 有 2 条（共 205 条）。

**代码事实**

- `caption_chunker.observe()`（`caption_chunker.py:274-278`）：当观察是 `endpoint` / `utterance_final`，且 lane 内所有 unit 都属于该 item 时，**把整条 caption 立刻发出**，reason = `utterance_endpoint`。这条路径**不经过 `select_boundaries()`**，因此不受任何标点或长度闸门约束。
- ASR 侧：Soniox 的 `<end>` / `<fin>` token 触发 `_finish_utterance()`，其中 `item_id = str(self._utterance)` 且 `self._utterance += 1`（`providers/asr_soniox_realtime.py:393-416`），即**每个端点都会开一个新的 item**。注意 item 不等于 lane：lane 的键是 `("speaker:<标签>", 语种)`，有 speaker 时跨 item 延续（仅当新 unit 比 lane 末尾晚 `_CONTINUATION_GAP = 1.2` 秒以上、或时间回跳超过 `_TIMESTAMP_JITTER = 0.06` 才关闭），只有没有 speaker 时才落到 `("item:<id>", 语种)` 这类 item 局部 lane（`caption_chunker.py:245-253`）。

**复现**：`py -3.10 .scratch/trace-soniox-fixture.py`（用官方事件形状逐帧打印每条字幕由哪条路径发布）。

---

### P3 没有译文 ⇒ 该字幕完全不显示

**代码事实**

- `web-player/subtitle-scheduler.js:56-57`：
  `function displayable(cue) { return Boolean(cue) && cue.state === "done" && typeof cue.zh === "string" && cue.zh.trim().length > 0; }`
- 同文件 `:8` 注释：`A cue is displayable ONLY in state "done" with non-empty translation.`
- 同文件 `:30` 注释：`failed cues remain hidden.`
- `web-player/player.js:2022` 的时间线列表用同一条件过滤。
- 后端侧：`translation_budget.py:70-73` 的公式使 `playback_window = 0` 时**不调用 provider**（"从未调用"，不是"翻译失败"）。

**各次运行的 dropped / sourceOnly 计数**（`translationDeadlineExpired` / `translationDropped` / `sourceOnlyCues`）：

| 运行 | 字幕总数 | expired | dropped | sourceOnly | attempts | failures |
|---|---|---|---|---|---|---|
| `l1-20min`（23:22，改动前） | 未投影 | **29** | **29** | **33** | 242 | 4 |
| `l1-upstream`（00:16） | 337 | 0 | 0 | 4 | 335 | 4 |
| `l1-fixed`（00:59） | 140 | 4 | 4 | 4 | 135 | 0 |
| `l1-stubfixed`（01:09） | 205 | 1 | 1 | 1 | 203 | 0 |
| `l1-vetofix`（01:27） | 312 | 1 | 1 | 1 | 307 | 0 |
| **R6（本执行者的改动）** | 25 | — | — | **7** | 18 | 0 |
| R7（回退后） | 82 | 1 | 1 | 1 | 24（会话 +90 秒时） | 0 |

- R5 那一条的具体记录：`src = "消防によりますと、午後2時過ぎ。"`，`state = "failed"`，`zh = None`；计数显示它发生在会话头 ~90 秒内。
- R6 的 7 条：已逐条确认的 6 条位于 `＋1.86s` `＋8.46s` `＋16.38s` `＋22.32s` `＋53.46s` `＋53.82s`，全部 `state=failed`、`zh=None`；该次抓取覆盖到 `＋78s`，`＋62s` 之后抓到的都正常。R6 的 `translationAttempts = 18`，`25 − 18 = 7` ⇒ 这 7 条从未进入 provider。

**状态更新（2026-09-18 晚，`7966c12` 之后）**：本页描述的"没有译文 ⇒ 该字幕完全不显示"**已在工作树中修复**（改法与实测见 §4 的"已落地的改动"）。上面这些历史计数仍然有效——它们记录的是修复前每次运行丢了多少条可读原文。

**复现**：`node .scratch/cdp-api-dump.js` 取 `/api/subtitles?afterSeq=0`，筛 `state != "done" or not zh`。

---

### P4 会话开头的"欠账"，以及它随时间衰减

**现象**：会话刚开始时音频腿是追赶式灌入的（`uptimeSeconds = 82.8` 时 `asrSeconds = 89.9`），ASR 的"欠账"很大，随后单调衰减。

R7 的 100 个样本（每 2 秒一帧，`.scratch/lag-timeline.jsonl`）：**单位：秒**

| 会话时间 | `asrAdapterDelay` P50 / P95 | `chunkerPolicyDelay` P50 / P95 | `readyLag` P50 / P95 |
|---|---|---|---|
| 86 | 8.016 / 10.828 | 0 / 0.016 | 9.407 / 13.521 |
| 136 | 6.421 / 10.157 | 0 / 0.016 | 8.140 / 12.823 |
| 203 | 5.672 / 10.016 | 0 / 0.016 | 7.350 / 12.481 |
| 241 | 4.375 / 8.344 | 0 / 0.016 | 6.350 / 10.973 |
| 286 | 2.500 / 6.781 | 0 / 0.016 | 5.223 / 10.234 |

- 同期 `sourceOnlyCues` 全程停在第 1 条（不再增长），`captionChunks` 从 26 涨到 82。
- 其他运行的稳态 `readyLagP50/P95`（整轮末帧）：5.132/9.162 ～ 7.465/12.822，全部低于 15.0 的目标延迟，但 P95 与 15.0 的差值最小只有约 2.2 秒。
- R5 的第一次采样（会话 +88 秒）：`translationAttempts = 21`，`sourceOnlyCues = 1`。

**含义（纯算术，非推断）**：`playback_window = max(0, 15.0 − age)`，而 `age` 在字幕入队那一刻取值；`age ≥ 15.0` 时预算为 0 ⇒ 不调用 provider ⇒ 依 P3 不显示。

**复现**：`py -3.10 .scratch/probe-lag-timeline.py 200 .scratch/lag-timeline.jsonl`（会话运行中，只读）。

---

### P5 测量口径：`asrAdapterDelay` 的采样条件与 P95 取法

- `subtitle_pipeline.py:1761-1784`：只有在 `audioPushed / evidenceAvailable / chunkEmitted / translationStarted / providerFinished` 全部非空且单调时，六项 stage lag 才各记一个样本；否则 `_latency_unknown += 1` 并 `return`。**即：有一条完整的六段链路才记样本；没走到 provider 的字幕（队列到期/丢弃）不记，而翻译失败因为会写 `provider_finished`（`:1721-1722`）仍然会记。**
- `_lag_percentiles`（`1798-1806`）：`index = min(len−1, max(0, round(fraction × (len−1))))`；窗口 `deque(maxlen=60)`。n=20 时"P95"= 第 19 个、n=25 时 = 第 24 个（见 §2 口径限制 4）。
- 现场读数实例（**未落盘，无法复算**）：R6 在 `uptimeSeconds = 82.8` 时 `/api/status` 读到 `asrAdapterDelayP50/P95 = 11.797 / 14.953`、`chunkerPolicyDelayP50/P95 = 0.015 / 1.203`、`translationProviderDelayP50/P95 = 1.296 / 1.859`、`translationQueueDelay = 0 / 0`、`readyLagP50/P95 = 13.772 / 17.761`、`sourceReadyLagP50/P95 = 12.94 / 17.761`、`totalReadyDelayP50/P95 = 12.438 / 16.953`、`targetDelaySeconds = 15.0`。这组数字取自当时终端输出，没有写入任何文件；当时窗口内样本数未记录，因此不能据它复算具体分位索引。
- 落盘可复算的对照（R7，`.scratch/api-status.json`，`uptimeSeconds = 295.8`）：`asrAdapterDelayP50/P95 = 2.5 / 6.781`、`chunkerPolicyDelayP50/P95 = 0 / 0.016`、`translationProviderDelayP50/P95 = 1.297 / 3.235`、`readyLagP50/P95`（见 §4 表）、`latencyWindowSamples = 60`、`latencySamples = 81`、`latencyUnknown = 1`、`latencyUnknownReasons = {translationStarted: 1, providerFinished: 1}`（这条正好是"没走到 provider 的 cue 被排除"的现场实例）。

---

### P6 自身时长 0.060 秒的字幕

- R5：11 条；R7：`西。` `日本。` `大気。` `大。` `60。` `えー。` `その。` `少し。` `日本は。` 等，多为 0.060 秒（个别 0.30 秒）。
- 事实：这些 cue 的 `tEnd − tStart = 0.060`；它们可读**只因为 `hold` 有 1.2 秒的下限**（`subtitle_text.calculate_hold(..., minimum)`）。
- 相关来源**尚未确定**：本次执行者没有把 ASR 的原始 token 流落盘，所以"0.060 秒来自 provider 还是来自时间映射"没有被直接观测。可用来收窄范围的两条代码事实：(1) 适配器的时间投影 `_project_token_clock()`（`providers/asr_soniox_realtime.py:371-391`）对 `start_ms` 与 `end_ms` 减的是**同一个** `correction_ms`，因此**不改变 token 自身时长**，只有 `max(0.0, …)` 的截断（会话开头、或 correction 大于原时间戳时）会把起点压到 0 从而缩短跨度；(2) `caption_chunker.py:39` 存在常量 `_TIMESTAMP_JITTER = 0.06`（用于判定时间回跳），与观测到的 0.060 秒数值相同，但本执行者**没有**证明两者相关。

---

### P7 两个标点连写：`、。`

- R5：4 条 —— `だともうちょっといると思うので、。`、`それからもう1つ特徴は、。`、`関連するんですが、。`、`11日、。`；R7：1 条。
- 事实：仓库内没有任何代码会追加 `。`（全仓检索 `.py/.js` 无匹配）；ASR 适配器把文本原样透传。

---

### P8 英文正文被标成 `ja`

- R5：19 条正文含拉丁字母，其中 11 条是**纯英文句子**（`The plain fact is that inflation is too high and has been for too long.`、`I mean, I got just only a couple of things to make stuffed peppers, and I spent $45.` 等），**全部 `lang = "ja"`**。
- 代码事实：cue 的语种取 lane 内各 unit 语种的**众数**（`caption_chunker._drain_ready` 里的 `_dominant(u.language ...)`），lane 以说话人+语种为键，混有日英时取多数。

---

### P9 `/api/status` 报出的 `asrProviderId` 与实际引擎不一致

- 事实：`%APPDATA%\lingerlens\runtime\providers.json` 中 `asr.active = "bailian-fun-asr-2026-02-28"`，而该条记录的内容是 Soniox：
  `label: "Soniox"`、`kind: "soniox-realtime"`、`model: "stt-rt-v5"`、
  `baseUrl: "wss://stt-rt.soniox.com/transcribe-websocket"`、
  `options: { enableEndpointDetection: true, enableLanguageIdentification: true, enableSpeakerDiarization: true, maxEndpointDelayMs: 700 }`、`currency: "USD"`。
- 后果（客观）：`/api/status` 的 `asrProviderId` 显示的是那个 id 字符串；`asrCostCurrency` 为 USD；`asrEstimatedCostCny` 为 `null`（reason「ASR pricing unavailable」）。
- 同一文件里另有内置预设 `bailian-fun-asr-2026-02-28`（label「百炼 Fun-ASR-Realtime 2026-02-28」、kind `dashscope-task-asr`、options 含 `semanticPunctuationEnabled: false`、`maxSentenceSilence: 400`）——**它是否在本次会话中生效，取决于那条被覆盖的记录**，本执行者未做两者对照。

---

### P10 碎片与"是否显示"是两件独立的事（实测对照）

- R6（本执行者改动后）：25 条中 7 条无译文（不显示），但 `、。` 0 条、空档 0.0 秒、以 `。` 结尾 60%、助词开头 8.0%。
- R7（回退后）：82 条全部有译文（除会话头 1 条），但助词开头 19.5%、≤4 字 18.3%、含 `、。` 1 条、空档 28.7 秒。
- 即：**"文本切得好不好"与"这条会不会被显示"两组指标可以反向变化**；只看其中一组会得出相反结论。

### P11 停止会话偶发 400，页面从此"点了没反应"

- 观测（2026-09-18 19:0x，本执行者用 CDP 驱动）：`POST /api/stop` 返回 **HTTP 400**，body `{"error": "subtitle decoder teardown did not finish: decoder process, stdout drain"}`；同一时刻 `/api/status` 的 `state` 已经是 `idle`，`ffmpeg`/`yt-dlp` 进程数为 0。即**报错与真实状态不一致（拆除其实完成了）**。
- 后果：页面停在"已停止。可以重新解析…"，此后点 `#probe`（解析）**不发任何请求、不报任何错、按钮也不进入忙碌态**——与 `probe()` 开头的 `if (controlsBusy || sessionAction || lastSessionState === "running") return;`（`web-player/player.js:670`）吻合：`sessionAction` 被上一次未正常收尾的 Stop 留在非 null 上。
- 只有**整页重载**能恢复（本次实测：重载后同一段脚本立刻成功；不重载时空等 80 秒无任何状态变化）。
- 对排查的影响：这会让"驱动脚本没生效"看起来像"直播/后端坏了"。本执行者据此浪费了三轮实验。
- 复现：跑一场会话 → 点停止 → 若返回 400（本次 3 次里出现 1 次）→ 点解析。

### P12 字幕仓库跨会话保留旧 cue，且 id 会撞车

- 观测（2026-09-18 18:54 会话，采样文件 `.scratch/l2-pacing-tbs5.jsonl`）：会话开始后的 `/api/subtitles` 里**同时存在 19 分钟前那场会话的 cue**（`tStart = 1789727744.85`，而本场 `wall` 从 `1789728899.96` 起算），且**同一个 `id` 在不同时刻指向不同的 `tStart`**（新会话的 id 从 1 重新开始，与旧会话残留的 id 重叠）。
- 影响（本执行者亲历）：按 `id` 缓存的分析脚本会把两场直播的字幕混成一条时间线，第一版因此把 39 条字幕全部算成"晚了 1140 秒"。任何按 `id` 做缓存/统计的渲染或诊断代码都有同样的风险。
- 相关：`subtitle-scheduler.js` 的 `stats` 只统计 `droppedLateCues` 等计数（§3 已记录其虚高问题），不区分会话。

### P13 无人声直播与代码故障在状态里无法区分

- 观测（2026-09-18 18:31–18:33）：`@ANNnewsCH/live` 当时在播"亚马逊雨林环境音"（用 `/api/probe` 读到的标题为 `【LIVE】癒しの熱帯 アマゾンのジャングルから配信 … 夜の川に響くカエルと昆虫の声`）。该会话 127 秒里 `captionChunks = 0`、`translationAttempts = 0`、`pendingEvidenceOverSoftSpan = 0`、`asrReconnects = 0`、`latencyUnknownReasons` 为空，`/api/logs` 只有 3 条 ingest 启动记录；`asrSeconds = 125.1` / `uptime = 127.6` 证明音频确实在推。
- 即：**"ASR 一条证据都没回来"在状态里没有任何直接指示**（既没有"无人声/静音"、也没有"provider 无响应"的字段），只能靠反推。本执行者据此怀疑过刚做的改动，并做了一次无意义的 A/B。
- 教训（方法论）：**测试素材必须先用 `/api/probe` 的标题确认有人声**；本执行者已把这一步固化进 `.scratch/l2-probe-streams.py`。

---

## 4. 本执行者做过的代码改动与回退

**改动（2 个提交，均已回退）**

| 提交 | 文件 | 行为 |
|---|---|---|
| `e769c1e` | `companion/caption_chunker.py` | 新增 `_TAIL_CONFIRM_SECONDS = 1.2` 与 `_CaptionState.tail_hold_until`；`_drain_ready(state, now)` 中，**把"切点正好落在已到达文字末尾"的那一个候选扣住**，等下一个 token 到达或窗口到期再决定；`next_deadline` 纳入该窗口；`expire()` 到点后经 `_drain_ready` 放行（保留原 cut reason），并清理已空的 item lane |
| `e769c1e` | `companion/punctuation_boundaries.py` | 删除 `acb35bb` 引入的 `ja_tail_noun` 分支（"名词后的句号先按住"），因与上面的机制重复 |
| `6274324` | `companion/caption_chunker.py` | 在 `observe()` 的端点发布条件里加入 `caption.tail_hold_until is None`：**端点不再发布正被扣住的末尾**（此前实测 R6 中 24 次切断里有 20 次由该端点路径发布，扣住等于无效） |
| `6274324` | 6 个测试文件 | 时序调整（`deliver_final`、Soniox fixture 的时钟推进、reclamation 的收尾 drain 等） |

**回退**

| 提交 | 内容 |
|---|---|
| `832d801` | 一次性 revert 上述两个提交，恢复 `a577b6c` + `acb35bb` 的行为；推送后工作树干净 |

**实测对照（同一台机器、同一路直播、都在会话开头附近）**

| 指标 | R6（改动后） | R7（回退后） |
|---|---|---|
| 抓取覆盖 | 78 秒 | 287 秒 |
| 无译文（不显示）条数 | **7 / 25** | **1（会话 +90 秒时，attempts 24）** |
| `chunkerPolicyDelay` P50 / P95（现场读数，未落盘） | 0.015 / **1.203** | 0 / **0.016** |
| 以助词开头 | 8.0% | 19.5% |
| ≤4 字符 | 8.0% | 18.3% |
| 以 `。` 结尾 | 60.0% | 73.2% |
| 含 `、。` | 0 | 1 |
| 空档总计 | 0.0 秒 | 28.7 秒 |
| `readyLag` P50 / P95（现场读数，未落盘；会话 ≈83–86 秒处） | 13.772 / 17.761 | 9.407 / 13.521 |

该表 R6 的两行延迟值取自终端现场读取，**没有落盘**；R7 那一列的两个值可在 `.scratch/lag-timeline.jsonl` 的首个样本（`uptimeSeconds = 86.4`：`readyLagP50/P95 = 9.407 / 13.521`）与 `.scratch/api-status.json`（`uptimeSeconds = 295.8`）中复算。表格其余各行都由落盘的 cue 抓取统计得出（见 §2 的抓取文件清单）。

**回退前后的两次全量测试**：改动后 `PASS=55 | tests run=696`、`npm run ci` 退出 0；回退后同样全绿。

**未改动的部分**：`a577b6c`（名词+助词否决）与 `acb35bb`（名词后句号先按住）在回退后仍留在分支上，即当前工作树包含这两条规则。

### 已落地的改动（在树内，未回退）

| 提交 | 文件 | 行为 |
|---|---|---|
| `bc91c8b` | 本文件 | 更正三处证据不实的说法（stage lag 的采样人群、item 与 lane 的区别、P95 例子的索引与那组未落盘的数字、0.060 秒的来源） |
| `7966c12` | `web-player/subtitle-scheduler.js`、`player.js`、`style.css`、`tests/test_cue_scheduler.js`、`tests/test_web_assets.js` | 原文兜底：把"这条字幕能不能显示"的规则收进一个函数 `subtitleLines(cue)`（只有 `done` 才算译文，其余状态保留原文；两者都没有 ⇒ 不显示），调度器的准入、浮层渲染、历史面板过滤都改用同一规则；浮层的译文行/原文行身份不变，没有译文的行打上 `data-source-only`，由 CSS 让原文行在"仅译文"模式下顶到主行；历史面板按 `seq/revision` 就地重写行正文，译文到达时同一条 DOM 行从原文升级为译文 |
| `49e8f8c` | `companion/subtitle_pipeline.py`、`companion/server.py`、`web-player/player.js`、3 个测试文件 | 第一次流控：渲染端把 `mediaClock.playingWallTime()` 作为 `?playhead=` 挂在它本来就每秒发一次的 `/api/status` 上，后台换算成音频腿 PCM 位置并**把领先观众的秒数压在 8 秒**。**这次控制量选错了，见下节对照数据**；播放头上报本身保留下来做测量 |
| `a27d08d` | `companion/subtitle_pipeline.py`、`tests/test_subtitle_pipeline.py` | 第二次流控：控制量改为 **provider 自己的确认位置**（`_asr_evidence_pcm`，取 `_evidence_times` 里最远的已确认位置），发送端在"即将交出的位置 − 已确认位置 > 2 秒"时按住这个 chunk。**这次也被实测否掉了，见下节数据** |
| `fae9256` | `companion/subtitle_pipeline.py`、`tests/test_subtitle_pipeline.py` | **当前生效**：拆掉上面那条规则（发送端不再等待），保留两个测量字段 `asrQueueSeconds` / `viewerLeadSeconds`；新增回归测试"provider 长期不确认时，送端仍把队列里的音频全部送出" |

#### 两次流控都被实测否掉：数据与机理

第一次（按领先观众量压，`49e8f8c`）在 TBS NEWS DIG 新闻直播上实测 200 秒、39 条字幕；对照组是同一天改动前的两次 ANN 录像：

| 指标 | 改动前 R-a（16 条） | 改动前 R-b（34 条） | 压领先量（39 条） |
|---|---|---|---|
| 字幕首次出现比窗口起点晚：P50 / P90 / 最大 | 0.23 / 0.81 / 0.91 秒 | 0.26 / 0.74 / 1.39 秒 | **0.66 / 3.37 / 5.45 秒** |
| 晚于窗口起点 0.5 秒以上的条数 | 4/16 | 10/34 | **24/39** |
| 首次出现时只有原文的条数 | 2/16 | 2/34 | **9/39** |
| `asrAdapterDelay` P50（队列） | 2.4–5.8 秒 | 2.4–5.8 秒 | **1.5 秒** |

- 第一次的结论：**队列确实被压下去了，但字幕整体变晚**——"领先观众量"本身就是这条字幕的安全余量（要先喂进 ASR、再翻完，观众才走到那句话）。同一场录像内部也印证：领先量被压到 10–14 秒的那 19 个采样里"只有原文"占 17.6%，>14 秒的 180 个采样里只占 6.9%；`viewerLeadSeconds` 从 21.124 单调衰减到 8.19。

第二次（按 provider 确认位置压，`a27d08d`）在 B 站 `live.bilibili.com/7734200` 上同流对照（同一房间、同一时间窗、都是会话头 125 秒左右）：

| 指标 | 上界＝2 秒 | 上界＝关闭（`fae9256`） |
|---|---|---|
| 字幕条数 | **3** | **34 → 27**（第二次复测 100 秒 27 条） |
| `asrSeconds` / uptime（音频消费速率） | **0.16×** | **0.97×** |
| `asrAdapterDelay` P50 / P95 | 6.4 / 10.4（更早一次 31.3 / 72.5） | **2.4 / 8.4** |
| `readyLag` P50 | 7.6 | 4.9 |
| `chunkSpan` P50 / P95 | **0.24 / 1.92 秒**（碎片） | **2.76 / 4.92 秒** |
| 字幕正文 | "但这"、"容。" | "看到机不可失,终于有机会杀一个人…" |

- **机理（这是根子上的错误）**：`evidence` 是**只在有人说话时才前进**的信号。解说一停（音乐/团战音效），provider 什么都不确认 ⇒ 发送端按住馈送 ⇒ 解说再开口时 provider 已被饿住 ⇒ **字幕更少、延迟更高**，与意图完全相反。也就是说 **provider 的"确认"不能当作"消费量"来测量**；用静音门控的信号做流控是自锁。
- 因此当前生效的行为是：**不做任何馈送限流**，只保留 `viewerLeadSeconds` / `asrQueueSeconds` 两个测量字段（这次实验正是靠它们判定的）。为什么两次都错，已写进 `subtitle_pipeline.py` 里 `VIEWER_POSITION_TTL_SECONDS` 上方的注释。
- 附带发现（同一轮 B 站排查）：桌面端 `desktop/companion_entry.py` 的参数缺 `host`/`port`，导致**任何需要重新打包的直播（B 站即其一）字幕流水线根本不启动**（`startError: AttributeError: 'Namespace' object has no attribute 'host'`），已修（`a3d5eb2`）；B 站这条私有 HLS 没有 PDT，所以客户端报不出播放头（`viewerLeadSeconds: null`，测量字段在该平台不可用，但不影响字幕）；`_apply_request_proxy` 只写环境变量、永不清除（一次设过代理后，后端进程内所有 yt-dlp 都带 `--proxy`，直到重启）；以及用户 Windows 环境里本身有 `ALL_PROXY=127.0.0.1:7890`，所有 yt-dlp 都会走 Clash（±代理 A/B 因 yt-dlp 拒收 fmp4 格式而未能测出下载速率差异，此项**未定论**）。

#### 修掉上面两个 bug 之后，B 站的真实状态（2026-09-18 20:2x，`live.bilibili.com/7734200`，中文→英文）

| 现场读数 | 值 | 判读 |
|---|---|---|
| `state` / `lastError` | `running` / null | 没有崩溃 |
| `captionChunks` / `translationAttempts` / `failures` / `expired` | 94 / 94 / 0 / 0 | 字幕在正常产出与翻译 |
| `asrSeconds` / uptime | 276.9 / 279.3 | 音频消费 **0.99×**（上界拆除后不再饿死） |
| 页面 `#subtitleLayer` | `display: grid`、`visibility: visible`、1522×261、`.subtitle-content` 有文字 | **屏幕上确实在显示** |
| 屏幕上当时的内容 | `and also, JunJia, as you just mentioned earlier, maybe your overall form through` | 译文行 |
| `asrAdapterDelayP50 / P95` | 3.391 / **16.016** | 中位数正常，**长尾 16 秒** |
| `sourceReadyLagP95` / `totalReadyDelayP50 / P95` | 17.941 / 5.172 / **20.032** | 因此会有十几秒的"空窗" |
| `mediaAnchor` 及全部 anchor 字段 | **null** | 该平台用 `timelineSource: "private-hls"` + `timelineEpoch`（= `pdtEpoch`）直接给 cue 打时间戳，**不建音频→视频锚点** ⇒ `viewerLeadSeconds` 在该平台结构上不可用（不是上报丢了） |
| `timingSourceCounts` | `{asr: 94}` | 时间戳全部来自 ASR 自身 |

- 因此"好多字幕 ASR 根本没输出"的实测解释是：**中位数 3.4 秒没问题，P95 16–20 秒的空窗才是看到的"没输出"**——即最初那条"队列长尾"问题，两次流控都没能解决（都已拆除）。
- 复现：`py -3.10 .scratch\l2-session-api.py start https://live.bilibili.com/7734200 15 source=zh-Hans target=en`（走接口驱动，绕开 P11 的页面卡死），再用 `__lingerlensSoakProbe()`（`player.js:2659`）读屏幕真实内容——注意浮层元素是 `#subtitleLayer`（**不是** `subtitleOverlay`；写错 id 会得到 `null` 并误判成"屏幕空白"，本执行者踩过这个坑）。
- 已完成的验证：`py -3.10 scripts/run-hls-tests.py` → `PASS=55 | tests run=695`；`node scripts/run-hls-js-tests.js` → 176 项全绿（含新增"状态轮询是否带上播放头"一项）；`npm run check:js` 通过。
- 复现仪器（均在 `.scratch/`，未入库）：`l2-probe-streams.py`（先确认直播有人声）、`l2-session-api.py start|stop|state`（走 `/api/probe` + `/api/start` 驱动会话，绕开 P11 的页面卡死）、`l2-watch-live.py`（只读采样，记录 cue 窗口与 `shown`）、`l2-phase-analysis.py`（按 `viewerLeadSeconds` 分段统计字幕准点率）、`l2-start-session.py`（走 UI 的旧路径，遇到 P11 会静默失败）。

- **明确没动**：字幕锚点、目标延迟、6 秒翻译预算、切分规则、迟到/丢弃策略。超过 `maxLateSeconds` 的 cue 仍被丢弃，且不会因为后来有了译文或原文被重新拉回屏幕；`failed` 仍是后端终态；没有任何地方把原文写进 `zh`。
- **验证（JS）**：`node scripts/run-hls-js-tests.js` → 201 项全绿；`npm run check:js` 通过。
- **验证（真机，页面内测试缝）**：三种显示模式、同一条行就地升级、无文字 cue 不渲染；用页面里留下的真实 cue（`state=failed`、`zh=None`）渲染并截图。
- **验证（真机，实时会话 2 次）**：
  - 第一次 300 秒（从会话头开始观测）：第 43–47 个采样 tick 上，`id=4`（`state=failed`、`zh=null`、原文「インフレ率、それからトレンドインフレ率、合成予想物価上昇率も2%近づいている中で、」）**连续约 2 秒显示在屏幕上**，`data-source-only=1`、原文行 `display:block`；诊断条同时给出这条为什么没有译文：`translation failed, showing source text only: RuntimeError: all translation providers failed: deepseek; TimeoutError: gemini-3.5-flash-low: not called (not called: 0.01s left does not cover the 2.0s fallback reserve)`。
  - 第二次会话：第一次采样就抓到 `id=5`（`state=failed`、`zh=null`、原文「よろしくお願いいたします。」）在屏幕上，并截图；截图里同时可见左侧历史面板把这一条列为当前行、只有原文没有译文。
  - 现场文件（均未入库）：`.scratch/l2-head-watch.jsonl`（762 条采样）、`.scratch/evidence-live-source-only-failed.png`、`.scratch/evidence-source-fallback.png`。
- **这两次会话同时也是碎片现状的基线（本改动没有碰切分）**：第二次会话 `asrSeconds = 304.9` 时 `captionChunks = 83`、`chunkCutReasons = {clause_boundary 13, terminal_punctuation 52, utterance_endpoint 18}`、`chunkSpanP50 = 2.46`、`attempts = 80`、`expired = 1`、`sourceOnly = 1`。

---

## 5. 用户已明确提出的要求（原话要点，2026-09-18）

1. "没译文的时候就原文兜底吧。"
2. 关于碎片："每一句话，如果要切，那么这段话至少要是 4 秒钟的长度才能触发我们逻辑的硬切"；并"那个 1.2 秒的设计也有用，也可以保留"。
3. "开头如果确实有问题，干脆就不显示字幕也可以。"
4. 在此之前已提出的要求：无译文时不要让屏幕空着；字幕不要出现只有一两个字符的句子；不要在解决一个问题的同时引入另一个问题。

---

## 6. 明确标注：未经对照实验验证的部分

以下为推断，**没有做过 A/B**，仅供下一位处理者判断是否需要先验证：

1. "本执行者的 1.2 秒等待导致 R6 的 7 条无译文"：依据是**代码算术**——`audio_end_wall` 在入队时按音频时间轴算死（`subtitle_pipeline.py:1355`），而 `age = wall_clock() − audio_end_wall` 是在**分配预算那一刻**取的（`translation_budget.py:66-73`），分配与入队同一时刻（`:1542`），因此发布推迟 δ 秒会让 `age` 大 δ 秒、可用窗口少 δ 秒（前提是 PCM 按 1:1 推进）；再加旁证"两次历史会话在会话头各丢 1 条、R6 丢 7 条"。**未做"回退后同样 78 秒"的直接对照**。
2. R6 与 R7 的 `readyLag` 差异（13.772 vs 9.407）包含内容差异与会话起点的混淆，不能当作纯效应量。
3. `asrAdapterDelay` 在会话开头偏大与"音频腿追赶式灌入"的关系：只测到两者同时出现，未做因果实验。
4. `。` 出现在词组中间（`仕事。を`、`細野。さん`）由 ASR 侧产生：依据是"仓库无任何代码追加 `。`（全仓检索无匹配）"与适配器把 token 文本原样透传；**未取得 ASR 原始 token 流**（`raw` 未落盘）来直接展示。
5. 本节第 1 条的算术（"晚发 δ 秒 ⇒ 少 δ 秒预算"）是**代码结构上的推论**，不是两次只有该变量不同的对照实验；`audio_end_wall` 本身是估算量（由 `pcm_offset` 与 `chunk.end_pcm` 推出），不是独立测得的声音发生时刻。

---

## 7. 尚未测量的边界

1. `utterance_endpoint` 计数在不同会话差 1 到 15 倍的原因（内容？说话人停顿密度？`<end>` 触发率？）。
2. 每分钟/每 10 分钟的碎片率时间序列（现有数字都是整轮或整段汇总）。
3. 页面滚动窗口的真实淘汰策略（多少条 / 多少秒）。
4. SRT/历史面板等其它消费 `state/zh` 的路径是否同样受 P3 影响。
5. 6 个说话人并存时，lane 跨说话人合并/串行的边界行为。
6. 目标延迟调到 21–22 秒后，P1–P4 各指标的变化（此前只在 150 秒受控会话里做过 15 vs 25 的 A/B：15 秒档 1 次 provider 失败 / 2 条丢弃 / 2 条只显示原文；25 秒档全部为 0）。

---

## 8. 复现步骤（可直接照做）

```powershell
# 0) 前置：应用在跑，页面 lingerlens://app/，会话未启动
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*companion_entry*' } |
  Select-Object ProcessId, CreationDate      # 进程启动时间必须晚于最后一次改动的文件时间，否则磁盘新代码没被加载

# 1) 起一个会话（输入直播链接并点 Start，等到可播放）
cd F:\Projects\LagLingo; $env:PYTHONIOENCODING="utf-8"
py -3.10 .scratch\l1-start-session.py *> .scratch\l1-longrun\start.log

# 2) 抓这一轮的每条字幕 + 后端原始状态
node .scratch\cdp-cue-dump.js .scratch\cue-dump.json
node .scratch\cdp-api-dump.js                 # 覆盖 .scratch\api-subtitles.json / api-status.json / api-logs.json

# 3) 统计
py -3.10 .scratch\audit-cues-generic.py ".scratch/cue-dump*.json"
py -3.10 .scratch\span-floor-analysis.py .scratch\cue-dump.json
py -3.10 .scratch\list-cues.py .scratch\api-subtitles.json

# 4) 延迟分解时间线（会话运行中，只读，200 秒 × 每 2 秒）
py -3.10 .scratch\probe-lag-timeline.py 200 .scratch\lag-timeline.jsonl

# 5) 收尾：停会话，确认没有游离进程
node .scratch\cdp-stop-session.js
Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'ffmpeg|yt-dlp' } | Select-Object ProcessId, Name
```

**注意**：`.scratch/` 与 `output/` 均被 gitignore，脚本不会随分支带走；本文引用的所有脚本路径都是相对仓库根目录的实存文件（截至 2026-09-18）。
