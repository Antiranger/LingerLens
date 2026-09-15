# LingerLens 修复与删除交付报告

承接 `docs/audit-2026-09-core-algorithm-and-stability.md` 的审查结论。
本报告只记录**实际改了什么、怎么验证的**，全部数据来自实跑。

---

## 1. 验收状态（全部实跑）

| 验收项 | 结果 |
|---|---|
| `npm run ci`（release guard + check:python + check:js + 全部测试） | **通过** |
| `guard:release` | passed for 167 tracked files（修复前因 5 个已提交的 `.ts` 抓包失败） |
| Python 测试 | **PASS=48 / 503 tests / 0 fail**（修复前 25/43 文件被 CI 漏跑） |
| 浏览器测试 | **75 tests / 0 fail** |
| 根测试 `npm test` | 13 tests / 0 fail |
| 分句器 8h CPU 曲线 | **0.87× / 1.15×**（修复前 **64.2× / 11.5×**） |
| ASR 握手失败泄漏 | **0 / 160**（修复前 80 / 160） |
| 前端轮询链 | **恒为 1 条，stop() 后 0 条**（修复前 5 次唤醒 → 6 条永久存活） |
| Provider 热切换 | **0 个卡死 cue / 0 条残留状态**（修复前 16 / 16） |
| HLS 分片关键帧对齐 | **10/10 分片以关键帧开始**（修复前约半数从 GOP 中间开始） |

---

## 2. 七条致命/高危缺陷的修复

### 2.1 分句器无界增长与 CPU 劣化

**根因**：三处回收被 `state.closed` 门控，而该标志只由 `utterance_final` 设置，Deepgram 从不发送。

**改法**（`caption_chunker.py`）：
- 回收改为按**证据可用性**：`_reclaim_items(now)` 按空闲时长 + 硬上限淘汰；lane 表按类型区分——`item:` lane 一旦为空即刻释放（其 key 由 item_id 派生，不可能再收新 unit），`speaker:` lane 保留跨句连续性并按静默时长淘汰。
- `_spans` 由无界 list 改为 `deque(maxlen=2048)`，新增累计计数 `_caption_chunk_total`，`telemetry()` 不再每次全量排序历史。
- 新增 `_ItemState.pending_reported`，取代原先只写不读的 `hard_decision_frontier`。

**实测**（`soak_cpu_curve.py`，8h，6545 句）：

```
utterance#  修复前      修复后
       545   562.4us 1.00x   291.4us 1.00x
      3270  8523.8us 15.2x   258.1us 0.89x
      6540 36101.0us 64.2x   254.0us 0.87x
      容器   6545/6545/6545 无界 → 377 / 0 / 2048 有界
```

端到端管线 soak（`soak_pipeline.py`）：`_items` 由线性涨到 4090 变为封顶 512；事件循环单次阻塞由 27ms 回落到 0.7ms；5h 仿真墙钟由 11.3s 降到 2.9s。

### 2.2 HLS 分片不做关键帧对齐（视频卡顿根因）

**改法**：`core.py` 移除 `split_by_time`，改为 `+independent_segments`；并把 HLS 输出参数抽成 `hls_output_args()` **单一事实来源**，生产、`synthetic-smoke.py`、`subtitle-alignment-smoke.py` 全部调用它——此前烟测自己手写了一套"更友好"的参数，因此永远测不出生产问题。

**联动修复**：`player.js` 的延迟配置由"分片个数"改为"秒"（`liveSyncDuration: 12` / `liveMaxLatencyDuration: 170`）。分片时长现在跟随源 GOP（可能 2s 或 5s），按个数配置会让延迟静默翻倍。hls.js 中秒制优先于个数制（已在 `vendor/hls.min.js` 中核实其取值逻辑），并由 `test_hls_packaging_contract.py` 断言两侧常量一致。

**实测**（`verify_gop_alignment.ps1`，同一个 20s、2s 关键帧间隔的源）：

```
修复前：20 个 1.000s 分片，无 INDEPENDENT-SEGMENTS
修复后：10 个 2.000s 分片，全部以关键帧开始（0,2,4,…,18s），标签存在
```

### 2.3 hls.js 错误恢复无上限

**改法**：新增纯函数 `decideMseErrorRecovery()`（`playback-recovery.js`）并接入 `player.js`。阶梯：media #1–2 → `recoverMediaError`，#3 → `swapAudioCodec`，#4+ → `startLoad`，达到 `MSE_RECOVERY_MAX_ATTEMPTS` 则**明确报错停止**而不是无限重建；每次动作都有指数退避。计数在播放头真正前进时重置（`timeupdate`），因此一次恢复成功的流不会被此前的失败永久定罪。

### 2.4 ASR 适配器握手失败泄漏 ClientSession

**改法**：`asr_dashscope_task.py` 与 `asr_qwen_realtime.py` 的 `connect()` 全流程包进 `try/except BaseException: await self.aclose(); raise`（对齐同仓库 `asr_deepgram_streaming.py` 已有的正确范式）。

**实测**（`verify_asr_leak.py`，真实适配器对象打向已关闭端口）：

```
修复前                             修复后
dashscope-task  LEAK (False/False)   clean (True/True)
qwen-realtime   LEAK (False/False)   clean (True/True)
160 次失败握手 → 80 个未关闭会话       → 0
```

### 2.5 前端 `wake()` 永久分叉轮询链

**改法**：`poll-loop.js` 增加 `inFlight` / `pendingWake`；运行中收到 wake 只置标记由当前 tick 消费，`timer` 句柄只在同一处武装，消除了互相覆盖。

**实测**（`verify_poll_fork.js`）：修复前 5 次唤醒 → 6 条并发链、`stop()` 后仍剩 5 条且继续发包；修复后并发恒为 1、`stop()` 后 0 条。回归测试同时断言"合并的 wake 仍会再跑一次"，避免修成丢事件。

### 2.6 Provider 热切换导致 cue 永久卡死

**改法**：`subtitle_pipeline.py` 的 `if provider is None: continue` 改为 `self._drop_translation_cue(cue)`，走终态记录路径。

**实测**（`verify_provider_swap.py`）：修复前 16/60 卡在 `src`、`_cue_latencies`/`_audio_end_walls` 各残留 16；修复后全部为 0。

### 2.7 测试入口不可信

**改法**：新增 `scripts/run-hls-tests.py`（自动发现 43→48 个文件、自动设 `PYTHONPATH`、逐文件超时、可选依赖缺失记 SKIP 而非 FAIL、**自动 re-exec 到 3.10+**）与 `scripts/run-hls-js-tests.js`（自动发现 11 个浏览器测试文件），`package.json` 改指向它们。

顺带修出两个此前看不见的问题：
- `test_recovery_policy.py` 定义了 5 个测试却从未调用 `unittest.main()`——一直静默退出 0。运行器现在会把"用 unittest 但一个都没跑"标为 `EMPTY`。
- 旧的 npm 脚本漏跑 18 个 Python 文件与 1 个 JS 文件，其中包含核心分句测试 `test_punctuation_boundaries.py`。

---

## 3. 删除的死代码与化石

| 删除对象 | 规模 | 为什么可以删 |
|---|---|---|
| `_handle_interim` / `_handle_final` / `_split_state_for` | 166 行 | AST 扫描全部 12 个 `asr_*.py`：11 个 interim + 14 个 final **全部**带 `caption_observation`，legacy 路径不可达（`verify_legacy_unreachable.py`） |
| `_SplitState`、`_split_state`、`prefix_split_*`、`max_utterance_seconds`、`_maybe_force_commit`、`_open_utterance_start`、`_previous_final*`、`_last_forced_commit_at`、`_caption_evidence_seen` | — | 同上，均为该机制的配套状态 |
| `_ingest_pcm_chunk` | 15 行 | 生产走 `_enqueue_pcm_chunk`；旧方法只在测试里被调用，且是"静默丢弃"变体而文档说"分发" |
| `request_hard_commit` + 整条 commit 分支 | — | 全仓库无任何路径将其置 True，`_advance_caption_frontier` 的整个 commit 块不可达 |
| `manual_commit` 参数 / `disable_manual_commit()` / `_manual_commit_ok` | — | 写入后无人读取，随 commit 分支一并删除 |
| `hard_cap_cuts` / `manual_hard_commits` 计数器 | — | 永远为 0，测试断言它们为 0 因此永远不可能失败（空转断言） |
| `_ItemState.latest_frontier` / `hard_decision_frontier` | — | 只写不读 |
| 6 个恒零计数器（`unjoined_finals`/`prefix_cues`/`final_tails`/`final_absorbed`/`split_conflicts`/`prefix_rewrites`）+ `final_deduplicated` + `forced_commits` + `commit_failures` | — | 随机制删除；新增 `unmapped_observations` 记录"观测无法映射"这一真实情况 |
| `caption_candidate_scorer.py` + 其测试 | 9.6KB | **只被自己的测试引用**，从未接入生产，且自带第三张与另两处矛盾的标点表 |
| `media-clock.js` 的 `createFollowModeController` / `isCueVisibleInTimeline` / `isLiveMessageVisibleInTimeline` + 3 个测试 | — | 浏览器中不可达（`workbench-controller.js` 后加载覆盖前者）；且其 `failed` 可见规则与真正生效的 `subtitle-scheduler.js:57` **直接矛盾** |
| `"approx"` 计时来源 | — | `CaptionChunk.begin_pcm` 是非可选 float，该分支在类型上恒真不可达；前端却把它当作有意义的计数显示 |
| 5 个已提交的 MPEG-TS 抓包 | — | `.gitignore` 漏了 `*.ts`（已有 `*.mp4`/`*.wav`/`*.m3u8`）；它们使 `guard:release` 一直失败。已从索引移除（**文件仍在磁盘**）并补精确范围规则 `/.scratch/**/*.ts` |

**收敛重复实现**：三张互相打架的标点表统一到 `clause_boundaries.py` 作为唯一来源（`punctuation_boundaries.py` 缺 `…`、`caption_chunker.py` 独有 `—`，现已取并集）；`punctuation_boundaries.py:5` 那句与事实相反的注释（"尚未接入 CaptionChunker"）已更正。

---

## 4. 中优先级修复

| 问题 | 改法 |
|---|---|
| `finish_reason` 从不检查 | `_extract_chat_text` 现在拒绝 `content_filter` / `length`，与兄弟适配器（Anthropic `stop_reason`、Gemini `finishReason`）一致 |
| `max_input_chars` 声明后从无人读 | FallbackChain 在调用前强制；超长 cue 降级为**单句跳过**而不是 400 → 永久禁用该 Provider（默认单 Provider 配置下那意味着字幕彻底消失到重启） |
| `enable_thinking` 用子串匹配 | 改为解析 host 并匹配 `dashscope.aliyuncs.com` / `dashscope-intl.aliyuncs.com`；旧写法漏掉国际站（导致 Qwen3 思考占满 token 后返回空内容或把推理文本当字幕），又会误伤含该子串的任何 URL |
| `_pending_finals` 在 anchor 永不收敛时无限持有 | 加计数上限（256）+ 超期丢弃（超出播放延迟窗口即不可能再显示），并新增 `pendingFinalsDropped` 使其可见而非静默 |
| 子代理发现的潜在崩溃：`item_id=None` 时 `CaptionChunker` 抛 TypeError | 在证据边界 `_map_caption_observation` 规范化为 `str(item_id or "0")`（与 11 个适配器一致）。已独立复现该崩溃——生产中会经 `_consume_asr_events` 升级为 ASR 重连风暴 |
| `player.js` 重复调用 `updateStallOverlay` | 并非纯重复（首次覆盖 error 分支，因为下一行会 throw 跳过第二次），改为意图显式：error 分支内单独调用，正常路径保留一次 |

---

## 5. 测试加固

新增 7 个测试文件、约 40 条断言，全部锁定本次修复的真实不变量：

| 文件 | 锁定的东西 |
|---|---|
| `test_caption_chunker_reclamation.py` | Deepgram 事件形状下容器有界、lane 释放、跨度样本封顶、冻结时钟下仍守硬上限 |
| `test_hls_packaging_contract.py` | 不出现 `split_by_time`、`independent_segments` 存在、init 相对路径、两个烟测脚本共用生产参数、播放器秒制配置与服务端常量一致 |
| `test_asr_handshake_cleanup.py` | 四个适配器握手失败后 session 与 connector 均已关闭；反复失败不累积 |
| `test_pipeline_provider_swap.py` | 热切换后无 cue 缺终态、无状态残留、队列计数平衡 |
| `test_translation_adapter_hardening.py` | `finish_reason` 拒绝、主机匹配、超长 cue 单句跳过且不禁用 Provider |
| `test_caption_item_id_contract.py` | `item_id=None` 不会打断会话 |
| `test_poll_loop.js` / `test_playback_recovery.js` / `test_cue_scheduler.js` 增补 | wake 不分叉且不丢事件、MSE 恢复阶梯与退避、叠加层与时间轴用同一可见性规则 |

**同时修正了两条"假测试"**：
- `test_web_assets.js:215` 原本断言的是**一句注释**（`cue.state === "done" || cue.state === "failed"`，该文本只出现在与代码相反的注释里），因此无论 `displayable()` 怎么写都是绿的。现改为剥掉注释后断言真实规则，并加一条"绝不接受 failed"的反向断言。
- `test_caption_chunker.py` 中 `assertEqual(hard_cap_cuts, 0)` / `assertEqual(manual_hard_commits, 0)` 断言的计数器永远为 0，属于空转断言，已随死代码移除。

---

## 6. 测试迁移说明（`test_subtitle_pipeline.py`，74 → 60 测试）

删除 14 条（10 条 `PrefixSplitTests` 整类 + 3 条 manual-commit + 1 条 legacy 禁用验证），迁移约 20 条到新路径（统一走一个 `deliver_final()` 辅助函数，模拟所有适配器真实发出的事件形状）。

**每处期望值变更都有源码依据**，我已独立核实其中两条关键理由：

1. `timing_source` 由 `"asr"` 改判为 `"vad"` —— 已核实 `subtitle_pipeline.py` 只在 `chunk.exact_timing` 为真时标 `"asr"`；纯文本 final 无 token 时间戳，故为 `"vad"`。**边界数值未变**。
2. `"approx"` 分支不可达 —— 已核实 `caption_chunker._chunk_times` 为 `begin = begins[0] if begins else (state.begin_pcm or 0.0)`，恒返回 float。这直接推动了上面第 3 节对 `"approx"` 的删除。

---

## 7. 我没有动的东西（有意保留）

- **`commit()` 与 `capabilities.manual_commit`**：确认当前无生产代码调用/读取。保留是因为它们是 Adapter 协议面的合法成员（Soniox 的 finalize 控制帧、AssemblyAI 的 ForceEndpoint 都是协议正确的实现），删除需要改 12 个适配器及其测试，风险大于收益。已把"管线刻意不调用它、原因是什么"写进 docstring，避免它继续以"看起来在生效"的姿态误导读者。
- **`_spans` 的 2048 上限**：跨度百分位现在反映**近期**行为而非全部历史——这是更有用的遥测语义，累计块数走独立计数。
- **`CaptionChunker` 观测契约、`CueStore`、`TranslationBudgetPolicy`、`RollingContext` 排序键、`_frontier_time` 环形设计**：审查判定这些是写对的部分，一律未动。

---

## 8. 仍需真实长跑验收的项（本次无法替代）

本次是**仿真加速 soak + 定向复现 + 全量单测**。以下必须在真实直播上跑：

1. 真实 YouTube/Bilibili 直播连续 **8 小时**，每 30 分钟记录容器尺寸与每句耗时，验证曲线确实持平。
2. 修好分片对齐后，用真实 1080p60 源观察 30 分钟内 `MEDIA_ERROR` / 缓冲停顿次数（应从"每几分钟一次"降到接近 0）。
3. 用**错误的 API Key** 让 ASR 失败 1 小时，观察进程 FD 数保持平稳（修复前线性增长）。
4. 切标签页 20 次，用 DevTools Network 确认 `/api/subtitles` 请求速率不随时间上升。
5. 真实中日/中英直播各 1 小时，确认 `sourceOnlyCues` 占比（本次仿真在积压场景下到 33%，真实值待测）。

---

## 9. 复现方式

```powershell
cd F:\Projects\LingerLens
npm run ci          # 全绿：guard + python + js + 503 测试 + 13 根测试

# 各项实测（全部可重跑）
python .scratch\lingerlens-audit\soak_cpu_curve.py        # 8h CPU 曲线（应为 ~1.0x）
python .scratch\lingerlens-audit\soak_pipeline.py         # 端到端管线 soak
python .scratch\lingerlens-audit\verify_asr_leak.py       # 应为 0 泄漏
node   .scratch\lingerlens-audit\verify_poll_fork.js      # 应为 1 条链 / stop 后 0
python .scratch\lingerlens-audit\verify_provider_swap.py  # 应为 0 卡死
python .scratch\lingerlens-audit\verify_legacy_unreachable.py
pwsh   -File .scratch\lingerlens-audit\verify_gop_alignment.ps1
node   .scratch\lingerlens-audit\verify_desktop_dev_serves_fixed_source.js
```

---

## 10. 两个运行版本：命名、状态与数据隔离

### 10.1 命名（沿用仓库既有词汇，不另造）

| 建议叫法 | 仓库依据 | 实质 |
|---|---|---|
| **桌面版**（Desktop） | `desktop/README.md:1`「Windows 桌面版」 | Electron 打包应用，双击运行；随机端口 + 会话令牌 |
| **源码版 / Prototype** | `start-lingerlens.cmd:26`「For the source Companion」 | 直接跑 `companion/server.py`；固定 8765；浏览器打开 |

`start-lingerlens.cmd` 的分工已经是：默认启动桌面版，`-Prototype` 才启动源码版。口语上叫**「桌面版 vs Prototype 版」**最不容易混。

### 10.2 桌面版此前跑的是修复前的代码（已实测确认）

| 证据 | 内容 |
|---|---|
| 构建时间 | `release/win-unpacked` 与 `build-desktop/backend` 均为 **2026-09-14 18:12**，早于本次源码修改（22:24） |
| 内嵌前端 | 冻结的 `_internal/web-player/player.js` = 95597 B，仍含 `liveSyncDurationCount: 12`；源码 = 96524 B，已改为 `liveSyncDuration: 12` |
| 内嵌后端 | `desktop/companion.spec:25` 的 `Analysis()` 直接指向 `prototype/hls-companion`，Python 代码编译进 `lingerlens-backend.exe` |

即桌面版**同时**缺少分句器回收、分片关键帧对齐、播放器秒制延迟、hls.js 恢复上限、ASR 泄漏修复与轮询链修复。

### 10.3 数据目录互不相通（易踩的坑）

- 桌面版：`%APPDATA%\LingerLens\runtime\`（`companion_entry.py:22`，来自 Electron `userData`）
- 源码版：`prototype\hls-companion\runtime\`

**两边的 Provider Key、语言设置、Cookie 完全独立。** 在一边配置不会出现在另一边。

### 10.4 `npm run desktop:dev` 是第三条路（无需构建即用上修复）

`desktop/backend.cjs:10`：非打包模式跑 `desktop/companion_entry.py`，即**源码**。因此 `npm run desktop:dev` ＝ Electron 界面 + 最新源码。

已用 `verify_desktop_dev_serves_fixed_source.js` 实测确认，7/7 通过：

```
PASS  session guard rejects a request without the desktop token   (status=403)
PASS  served player.js uses the fixed second-based latency config (liveSyncDuration:12=true, Count=false)
PASS  served poll-loop.js has the wake-coalescing fix             (pendingWake/inFlight 均在)
PASS  served playback-recovery.js exposes the bounded MSE policy
PASS  default config no longer offers the removed legacy segmentation knobs
PASS  default config still offers the live subtitle settings
PASS  idle /api/status reports the expected reduced subtitles shape
```

> 说明：分句器遥测键（`unmappedObservations` 等）只在会话**运行中**才并入 `/api/status`；空闲时 `server.py` 返回 14 键的精简结构。因此它们的出现与否无法在不开启直播的情况下验证，该探针刻意不做这件事。

### 10.5 重建桌面版的前置条件（已核实全部就绪，无需联网）

```
ffmpeg.zip  sha256  expected == cached  ✓   (111 MB，免下载)
electron.exe                            ✓   已缓存
.venv-desktop  Python 3.11.15 + PyInstaller 6.22.2 + companion 依赖  ✓
yt-dlp 校验和                            ✓
```

重建命令：`npm run desktop:backend && npm run desktop:pack`（出可双击目录），
加 `npm run desktop:dist` 出 NSIS 安装包。

---

## 11. 归档旧源码快照

### 11.1 问题

`.scratch/` 与 `.planning/` 下散落着 7 组实验遗留的源码快照，共 32 个与线上源码**同名**的文件。搜索一次 `subtitle_pipeline.py` 会返回 **8 份内容互相矛盾的结果**，而只有一份是真正运行的代码——这是"逻辑看起来很乱"的一个真实来源。其中 4 个文件还被 git 追踪，所以 `git grep` 同样会命中。

### 11.2 处理

移动到 `.archive/source-snapshots/<原相对路径>/`，**保留原始路径以便追溯来历**，字节一个未删：

```
.planning/speaker-intonation-implementation/backup-before-replan   (11 files)
.scratch/caption-residual-lifecycle/baseline                       ( 8 files)
.scratch/clause-segmentation-v2/before                             (10 files)
.scratch/live-pipeline-latency-v2/backup                           ( 4 files)
.scratch/startup-sync-experiment/backup-20260909-115026            ( 4 files)
.scratch/subtitle-fixes-2026-09-05/backup-before-fixes             ( 8 files)
.scratch/translation-no-predecessor-wait/before                    ( 3 files)
```

各实验的**脚本、报告、结果数据仍留在原处**，只移动了源码副本。

**一个差点犯的错误**：最初的匹配器按文件名扫描，命中了 411 个第三方文件——`.scratch/boundary-model-evaluation` 与 `.scratch/fbk-english-evaluation` 里 vendored 的评估 venv 中存在同名的 `config.py`/`base.py`/`core.py`。已加入"必须位于 LingerLens 目录布局内"与 vendor 路径排除，最终只识别出 32 个真实快照。

### 11.3 验证

```
权威源码 sha256 未变                  E471F615C47F9E2E…  ✓
归档外的 subtitle_pipeline.py 数量     1（只有权威源码）    ✓
归档内数量                            7                       ✓
git grep '_handle_final' 命中文件数    0                       ✓
npm run ci                            全绿（503 + 75 + 13）   ✓
```

`.archive/README.md` 本身保持可追踪（`.gitignore` 用 `!/.archive/README.md` 例外），以便任何人看到该目录时立刻明白其性质。
