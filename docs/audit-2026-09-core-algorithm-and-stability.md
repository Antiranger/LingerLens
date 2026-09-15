# LagLingo 核心算法与长时稳定性审查报告

审查对象：`F:\Projects\LagLingo`，git rev `07dc338`
审查方式：**只认代码与实跑数据**。仓库内 `README.md` / `docs/*.md` / `docs/notes/findings.md` / `docs/notes/progress.md` 的全部结论一律不作为证据，仅用于"知道该验证什么"。
本报告分两级标注：**【实测】**＝本次亲自跑出来的数据；**【代码确认】**＝逐行读到、行号可复核但未单独跑复现脚本。

---

## 0. 结论摘要

**逻辑混乱度：不是"设计混乱"，而是"一个设计 + 一具化石 + 三处未收口"。**

主链路（ffmpeg → PCM 队列 → ASR → 归一化观测 → CaptionChunker → CueStore → 翻译队列 → worker）是**单事件循环、零线程、零锁、队列有界**的干净结构。真正让它看起来乱的是：

1. **一整套被取代的分句机制还挂着**（`subtitle_pipeline.py:996-1151` 的 `_handle_interim`/`_handle_final`，约 250 行 + 6 个计数器）。所有在售 ASR Provider 都会发 `caption_observation`，这条路永远走不到，但两套机制对 cue 元数据的处理**不一致**（legacy 路不写 `generation`/`chunk_order`，导致 `RollingContext` 拒绝它 —— `context_manager.py:54-55`）。
2. **分句器的内存回收被一个永远不成立的条件门控**，这是长时运行的头号杀手。
3. **CI 的测试入口是坏的**，18/43 个测试文件从未被执行过，包括核心分句测试。

**长时稳定性：会在数小时内确定性劣化，不是"可能不稳"，是"必然劣化"。**

| 实测项 | 结果 |
|---|---|
| CaptionChunker 每句处理成本（8h，无说话人标签） | 562 µs → **36,101 µs（64 倍劣化）**，近似 O(n²) |
| CaptionChunker 每句处理成本（8h，有说话人标签） | 222 µs → 2,553 µs（11.5 倍） |
| 8h 后 `_items` / `_captions` / `_spans` | 6,545 / 6,545 / 6,545 条，**全部 1:1 线性增长，无任何回收** |
| ASR 握手失败的资源泄漏 | **1:1 泄漏** ClientSession + TCPConnector；40 次失败 → 40 个未关闭会话 |
| 前端 `wake()` 轮询链 | 5 次唤醒 → **6 条永久并发的轮询链**，`stop()` 只清掉 1 条 |
| Provider 热切换 | 16/60 个 cue 永久卡在 `src` 状态，状态字典残留 16 条 |

**视频卡顿的根因已定位并复现**：生产打包用了 `-hls_time 1` + `split_by_time`（`core.py:696-710`），在 2 秒关键帧间隔的源上强制切 1 秒，导致**每两片就有一片从 GOP 中间开始**，播放器无法解码。同一份源，冒烟测试的打包参数切出 10 片（GOP 对齐），生产参数切出 20 片（GOP 不对齐）。

---

## 1. 审查方法

1. 在 Python 3.10（项目要求 3.10+，本机默认是 3.9）建独立环境，跑全部 43 个 Python 测试文件。
2. 五路并行代码深审：分句算法 / Provider 适配层 / 媒体与延迟发布 / 前端播放器 / 管线本体。
3. 对关键结论**全部自己写脚本复现**，脚本落在 `.scratch/laglingo-audit/`，可重跑。

复现脚本清单：

| 脚本 | 验证内容 |
|---|---|
| `soak_chunker.py` | 分句器 1h/8h 容器增长 |
| `soak_cpu_curve.py` | 分句器 8h CPU 劣化曲线 |
| `soak_attribution.py` | 劣化归因到具体扫描行 |
| `soak_pipeline.py` | 真实 SubtitlePipeline 端到端 soak |
| `verify_asr_leak.py` | ASR 适配器握手失败泄漏 |
| `verify_poll_fork.js` | 前端轮询链分叉 |
| `verify_provider_swap.py` | Provider 热切换状态泄漏 |
| `verify_gop_alignment.ps1` | HLS 分片关键帧对齐 |

---

## 2. 实测结果（铁证）

### 2.1 【实测】分句器无界增长与 64 倍 CPU 劣化 —— 长时稳定性头号问题

**根因（代码确认）**：三处回收全部被 `state.closed` 门控，而 `closed` 只由 `utterance_final` 设置：

```python
# caption_chunker.py:190-192   —— closed 的唯一来源
elif observation.kind == "utterance_final":
    self._reconcile_final(state, observation)
    state.closed = True

# caption_chunker.py:238-250   —— _items 回收
if state.closed:
    ...
    if len(self._items) > 512:
        for item_id in list(self._items):
            if len(self._items) <= 256: break
            if self._items[item_id].closed:
                del self._items[item_id]

# caption_chunker.py:252-254   —— _captions 回收
for key in affected:
    if key[0].startswith("item:") and state.closed and not self._captions[key].units:
        del self._captions[key]

# caption_chunker.py:231-237   —— 残片截止期
if caption.units and all(self._items[u.item_id].closed for u in caption.units ...):
    if caption.deadline is None:
        caption.deadline = now + _RESIDUAL_GRACE
else:
    caption.deadline = None
```

而 **Deepgram 适配器从不发 `utterance_final`**：

```python
# asr_deepgram_streaming.py:126
caption_evidence=frozenset({"token_snapshot", "stable_token_delta", "endpoint"}),
```

`stable_token_delta` 走 `:174`，`endpoint` 走 `:216` 但**不设置 closed**。于是上述四段代码在真实 Deepgram 会话中**全部不可达**。

`_spans` 则是完全没有任何回收（`:531` `self._spans.append(span)`，全仓库无删除），并且每次 `telemetry()` 都全量排序（`:316 sorted(self._spans)`），而 `status()` 被浏览器以 3 次/秒轮询。

**实测数据（8 小时连续讲话仿真，4.4 秒/句，818 句/小时）**：

```
=== 8h, 无说话人标签（diarization OFF） ===
 hour   _items  _captions   _spans  telemetry_ms
  0.5      410        410      410         0.010
  2.0     1637       1637     1637         0.053
  4.0     3273       3273     3273         0.036
  6.0     4910       4910     4910         0.068
  7.5     6137       6137     6137         0.065
_items 增长/句: 1.0   _captions 增长/句: 1.0   _spans 增长/块: 1.0
```

**CPU 劣化曲线（同一 8 小时仿真，按句采样）**：

```
utterance#  us/句    vs 首段     (diarization OFF)
       545    562.4     1.00x
      1635   2506.7     4.46x
      3270   8523.8    15.16x
      4905  18443.0    32.80x
      6540  36101.0    64.20x
```

**归因（8h 状态下的单方法耗时）**：

| 方法 | 0.5h | 8h | 劣化 | 走的扫描 |
|---|---|---|---|---|
| `expire()` | 13.2 µs | **1673.1 µs** | **127×** | `:268` 遍历全部 `_captions` |
| `observe()` | 14.1 µs | 1629.7 µs | 115× | `:217` 遍历全部 `_captions` |
| `advance_audio()` | 22.8 µs | 537.8 µs | 24× | `:280` `_captions` + `:289` `_items` |
| `next_deadline` | 8.4 µs | 212.1 µs | 25× | `:259` 遍历全部 `_captions` |
| `telemetry()` | 9.0 µs | 62.1 µs | 7× | `:316` 全量排序 `_spans` |

**推算**：分句器 CPU 占用 0.5h 时 0.31 s/小时 → 8h 时 **18.8 s/小时** → 24h 约 56 s/小时 → 48h 约 113 s/小时，且永远线性增长。

**为什么这直接毁掉体验**：`expire()` 和 `observe()` 是**同步调用**，跑在唯一的 asyncio 事件循环上，而同一个循环还要服务 `/api/status`、`/api/subtitles`、PCM 推送、ASR 消费。8h 时每次 ASR 事件阻塞事件循环 1.6 ms 起步，48h 后到 10 ms 量级，届时字幕、状态接口、音频推送会一起抖动。

**附带发现**：说话人标签（diarization）**开启时反而更稳**（11.5× vs 64×），因为 `_captions` 的 key 变成 `speaker:Sx`，被复用成固定 3 条。而 README 推荐开启 diarization —— 这次文档恰好说对了，但理由是错的。

---

### 2.2 【实测】ASR Provider 握手失败泄漏 ClientSession —— 而默认 Provider 正好中招

**代码确认**：

```python
# asr_dashscope_task.py:149-154   —— session 创建后 ws_connect 不在 try 内
async def connect(self) -> None:
    self.session = aiohttp.ClientSession()
    self.ws = await self.session.ws_connect(
        self.provider.base_url,
        headers={"Authorization": f"Bearer {self.provider.api_key}", ...},
    )
    ...
    await self.ws.send_json({...})        # :182 也不在 try 内
    try:
        await asyncio.wait_for(self._wait_for_task_started(), ...)   # :188-192
    except BaseException:
        await self.aclose()               # :193-195 只覆盖这一段
        raise

# asr_qwen_realtime.py:75-78   —— 整个 connect 没有任何 try
async def connect(self) -> None:
    self.session = aiohttp.ClientSession()
    url = f"{self.provider.base_url}?model={self.provider.model}"
    self.ws = await self.session.ws_connect(url, headers={...})
```

对照正确写法（同仓库内已有范式）：

```python
# asr_deepgram_streaming.py:192-203
try:
    ...
except BaseException:
    # ...must not leak the ClientSession.
    await self.aclose()
```

**实测（真实适配器对象，非 fake，打向已关闭的本地端口）**：

```
adapter            connect() ->           session.closed  connector.closed  verdict
dashscope-task     ClientConnectorError   False           False             LEAK
qwen-realtime      ClientConnectorError   False           False             LEAK
deepgram           ClientConnectorError   True            True              clean
soniox             ClientConnectorError   True            True              clean

Compounding: 40 consecutive failed handshakes per adapter
  dashscope-task     attempts=40  unclosed ClientSessions=40   unclosed TCPConnectors=40
  qwen-realtime      attempts=40  unclosed ClientSessions=40   unclosed TCPConnectors=40
  deepgram           attempts=40  unclosed ClientSessions=0    unclosed TCPConnectors=0
  soniox             attempts=40  unclosed ClientSessions=0    unclosed TCPConnectors=0
```

**1:1 泄漏**，且 aiohttp 在 GC 时打印 `Unclosed client session`。

**为什么会累积**：`_asr_manager` 的重连循环永不放弃：

```python
# subtitle_pipeline.py:713-756
except Exception as exc:
    self._record_error(exc)
finally:
    stream = self._stream        # 关键：stream() 抛异常时 self._stream 从未被赋值
    self._stream = None
    if stream is not None:       # → 永远 None → 什么都不关
        ...
if self._running:
    self.stats.asr_reconnects += 1
    await asyncio.sleep(backoff)
    backoff = min(8.0, backoff * 2)
```

`asr_provider.stream()` 抛异常 → `self._stream` 未被赋值（`:738` 未执行）→ `finally` 读到 `None` → **管线侧也无法清理**，而适配器自己也没清。

**默认配置就是 `dashscope-task-asr`（bailian-fun-asr）**。API Key 填错或网络抖动时：8 小时约 3,600 次重连 → 3,600 个泄漏会话与连接器，最终 FD 耗尽。这是一个"配置一错、跑几小时必崩"的组合。

---

### 2.3 【实测】前端 `wake()` 永久分叉轮询链

**代码确认**：

```javascript
// poll-loop.js:16-25
async function tick(expectedGeneration) {
  if (!running || expectedGeneration !== generation) return;
  try { await run(); }
  finally {
    if (!running || expectedGeneration !== generation) return;
    var delay = isHidden() ? hiddenIntervalMs : intervalMs;
    timer = setTimer(function () { tick(expectedGeneration); }, delay);
  }
}
// poll-loop.js:42-47   —— 没有"是否有 tick 在飞行"的判断
wake: function () {
  if (!running) return;
  if (timer !== null) clearTimer(timer);
  timer = null;
  tick(generation);         // <- 直接再起一条
}
```

`tick` 只被 `running` 和 `generation` 守卫，而 `wake()` 两者都不改，于是第二个 `tick` 在第一个仍 `await run()` 时启动；两者结束时都执行 `finally` 并各自 `timer = setTimer(...)`，**后一次赋值覆盖前一次的句柄**，前一条链从此不可达，`stop()` 永远清不掉。

生产触发点：

```javascript
// player.js:1880-1883
document.addEventListener("visibilitychange", () => {
    ...
    statusPoller?.wake();
    subtitlePoller?.wake();
});
```

**实测**：

```
after start:            live chains=0 inFlight=1
wake #1:               live chains=2 maxConc=2
wake #3:               live chains=4 maxConc=4
wake #5:               live chains=6 maxConc=6

live timer chains before stop() = 6
live timer chains after  stop() = 5   <-- must be 0
chains that still fired after stop() = 5

RESULT: LEAK CONFIRMED -- stop() left chains that keep polling forever.
```

**后果**：用户每切一次标签页就多一条永久轮询链。切 5 次 → 6 倍请求速率，且只会 `stop()` 也关不掉，只能刷新页面。而服务端是**单线程事件循环**——这正是"视频加载很卡"的第二个来源：客户端自己把服务端打爆了。

---

### 2.4 【实测】视频卡顿根因：HLS 分片不做关键帧对齐

**代码确认**：

```python
# core.py:696-713
"-hls_time",
"1",  # 1 秒分片；split_by_time 使其真正生效（不再受 GOP 约束）
...
"-hls_flags",
"delete_segments+program_date_time+temp_file+split_by_time",
```

注释本身就承认了：`split_by_time` 让 1 秒切分**不受 GOP 约束**。而 MSE/hls.js 只能在关键帧处开始解码。

**实测**：同一 20 秒源、`-g 60`（2 秒关键帧间隔 @30fps），分别用生产参数与冒烟测试参数打包：

```
--- PRODUCTION (-hls_time 1 + split_by_time) ---
#EXT-X-TARGETDURATION:1
(无 #EXT-X-INDEPENDENT-SEGMENTS)
#EXTINF:1.000000,  seg_000000000.m4s
#EXTINF:1.000000,  seg_000000001.m4s
...  共 20 片

--- SMOKE (independent_segments, 无 split_by_time) ---
#EXT-X-TARGETDURATION:2
#EXT-X-INDEPENDENT-SEGMENTS
#EXTINF:2.000000,  seg_000000000.m4s
#EXTINF:2.000000,  seg_000000001.m4s
...  共 10 片
```

**同一份输入，生产版切 20 片、冒烟版切 10 片。** 关键帧每 2 秒才有一个，却硬切 1 秒 —— 结论只有一个：**生产版约一半的分片从 GOP 中间开始，不是独立可解码分片**。冒烟版因为 ffmpeg 拒绝在非关键帧切分，自动退回 2 秒对齐。

**为什么这表现为用户说的"视频很卡"**：
- hls.js 任何一次 `recoverMediaError()` / seek / 追边 / 初始拉流，落点有约 50% 概率在非关键帧分片上 → 解码器要等到下一个关键帧才有画面，1~2 秒黑屏或冻结，而**音频照常播放**。
- 而 `player.js:416-421` 对每一个 fatal MEDIA_ERROR 都无条件调用 `hls.recoverMediaError()`：

```javascript
if (data.type === Hls.ErrorTypes.NETWORK_ERROR) hls.startLoad();
else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
```

该方法是 `detachMedia() + attachMedia() + startLoad()` 的完整 MSE 重建，会丢弃全部缓冲并必然重新缓冲，且**没有任何次数上限、没有退避、没有 `swapAudioCodec()` 回退**。解码失败 → 重建 → 又落在非关键帧分片 → 又失败，形成自激循环。

**冒烟测试为什么没发现**：`synthetic-smoke.py` 用的是另一套参数（`independent_segments`、`-g 30`、无 `split_by_time`），打包出的流比生产环境"友好"，因此永远测不出这个问题。仓库里 check-in 的 `runtime/edge-e2e/public/live.m3u8` 仍带着 `#EXT-X-INDEPENDENT-SEGMENTS`，而 `core.py:893-899` 根本不写这个标签 —— 这是早期版本遗留的假证据。

---

### 2.5 【实测】Provider 热切换导致 cue 永久卡死与状态泄漏

`server.py:551-553` 会在 worker 运行中把 provider 置空；`subtitle_pipeline.py:1340-1341` 随后：

```python
provider = self.translation_provider
if self._degrade_level >= 2 and self.fallback_translation_provider is not None:
    provider = self.fallback_translation_provider
if provider is None:
    continue          # <- finally 里 task_done() 会跑，但没有任何终态记录
```

`continue` 跳过了 `_record_ready_lag()`，而 `_cue_latencies` / `_audio_end_walls` 只在 `_record_ready_lag` 里 pop（`:1490-1491`）。

**实测（先堆积 60 个 cue、4 个 worker 忙、再热切换为 None）**：

```
phase 1: 60 cues queued, provider alive, workers busy
  store=60    states={'translating': 4, 'failed': 40, 'src': 16}
  _cue_latencies=20  _audio_end_walls=20  _translation_budgets=20  _queue=16
>>> provider set to None mid-flight (as server.py:551-553 does)
phase 2: after workers drained the queue
  store=60    states={'done': 4, 'failed': 40, 'src': 16}
  _cue_latencies=16  _audio_end_walls=16  _translation_budgets=0  _queue=0

  cues never given a terminal state      = 16
  _cue_latencies retained                = 16
  _audio_end_walls retained              = 16
  translation queue unfinished tasks     = 0        <- task_done() 确实跑了
  RESULT: LEAK
```

**16 个 cue 永久停在 `src`**（前端会一直显示原文无译文），`_cue_latencies` / `_audio_end_walls` 各残留 16 条。注意 `task_done()` 已执行 —— 证明泄漏点正是 `continue` 跳过了终态记录。

**顺带暴露的第二个问题**：健康 Provider 下 60 个 cue 里 **40 个被丢弃为 `failed`**。这是队列上限 `_queue_limit = max(16, 4*workers) = 16`（`:1267`）在积压时丢弃最旧任务的行为。本次仿真压缩了时间轴会放大该比例，但机制本身是真实的：**积压时字幕会成批变成"仅原文"**。

---

### 2.6 【实测】测试与 CI 基线：入口是坏的，18 个文件从未被执行

**Python 版本**：项目要求 3.10+（`README.md:50`、`requirements-build.txt:1`），但 `package.json` 的测试脚本用裸 `python`，本机解析到 Anaconda 3.9.13。在 3.9 下 `test_subtitle_pipeline.py` 直接 **5 个 ERROR**：

```
File ".../subtitle_pipeline.py", line 321, in __init__
    self._caption_deadline_changed = asyncio.Event()
  File "<python>/lib/asyncio/locks.py", line 177, in __init__
    self._loop = events.get_event_loop()
RuntimeError: There is no current event loop in thread 'MainThread'.
```

**未跑 `pip install -r requirements.txt` 时**（`langcodes` / `fugashi` / `unidic_lite` 缺失）测试同样崩溃。

**根因之二：`PYTHONPATH`**。`test_ytdlp_ingest.py:32`、`test_recovery_policy.py:3` 等用 `from companion.xxx import ...`，但以 `python tests/test_x.py` 方式运行时 `sys.path[0]` 是 `tests/` 而非包根，于是 `ModuleNotFoundError: No module named 'companion'`。

**修正后的真实基线（Python 3.10 + `PYTHONPATH=prototype/hls-companion`）**：

```
TOTAL PASS=41 FAIL=2
FAIL test_browser_smoke.py       ModuleNotFoundError: No module named 'playwright'
FAIL test_streamlink_ingest.py   ModuleNotFoundError: No module named 'streamlink'
```

→ **代码本身基本是绿的**（41/43），2 个失败只是可选依赖未安装（`streamlink` 甚至不在 `requirements.txt` 里）。**之前那 10 个"失败"全部是环境问题，不是代码 bug。** 这一点必须说清楚，否则会误导重构方向。

**但 CI 覆盖面是真问题**：`npm run test:hls-companion` 只声明了 25 个 Python 测试文件，实际存在 43 个，**18 个从未被执行**：

```
test_bilibili_chat_auth.py        test_media_anchor.py
test_browser_smoke.py             test_provider_roles.py
test_caption_candidate_scorer.py  test_punctuation_boundaries.py
test_chat_batch.py                test_realtime_translation_contract.py
test_chat_timing.py               test_recognition_contract.py
test_control_ipc.py               test_recovery_policy.py
test_hls_ingest.py                test_source_timeline.py
test_streamlink_ingest.py         test_translation_budget.py
test_twitch_chat_ingest.py        test_youtube_chat_streaming.py
```

其中 **`test_punctuation_boundaries.py` 和 `test_caption_candidate_scorer.py` 正是核心分句算法的测试**。绿色 CI 是假象。

---

## 3. 逻辑混乱地图（补丁堆叠的具体证据）

以下均为【代码确认】，行号可复核。

### 3.1 一具 250 行的化石：两套分句机制并存

| 证据 | 位置 | 说明 |
|---|---|---|
| legacy 路径入口 | `subtitle_pipeline.py:799-807` | 仅当 `observation_handled == False` 才走 |
| 不可达条件 | `:816-850` `_map_caption_observation` 返回 `None` 仅在 token 时间戳非法时 | 所有在售 Provider 都发合法观测 |
| 元数据不一致 | `:1056-1064` vs `:977-978` | legacy 路不写 `generation`/`chunk_order` → `RollingContext.add` 直接 return False（`context_manager.py:54-55`）→ legacy cue 永远没有翻译上下文 |
| 自带 6 个计数器 | `:155-159` `prefix_cues`/`final_tails`/`final_absorbed`/`split_conflicts`/`prefix_rewrites`/`unjoined_finals` | 全部恒为 0 |
| 自带构造参数 | `:245` `max_utterance_seconds=0.0`、`:246-247` `prefix_split_*` | 默认关闭，且被 `:669 not self._caption_evidence_seen` 二次关闭 |

### 3.2 三个"硬上限"其实是死代码

```python
# caption_chunker.py:55
request_hard_commit: bool = False      # 全仓库没有任何地方赋值为 True
# caption_chunker.py:147  manual_commit 被写，全仓库无读取
# caption_chunker.py:131-133  _hard_cap_cuts / _hard_cap_pending_evidence 只增不减但从不因"硬切"而增
```

`telemetry()` 仍然对外暴露 `hard_cap_cuts` / `manual_hard_commits`（`:323`、`:328`），`test_caption_chunker.py:188,321` 断言它们为 0 —— **测试是空转的，永远不可能失败**。

### 3.3 三份互相打架的"短句保护"与标点表

| 实现 | 位置 | 常量 |
|---|---|---|
| 分句器内置 | `caption_chunker.py:472-480` | `SOFT_TARGET_SPAN = 6.0` |
| 标点模块 | `punctuation_boundaries.py:95-102` | `4.0 / 8` |
| 候选打分器 | `caption_candidate_scorer.py:160-182` | `6 / 8` |

标点集合不一致：`caption_chunker.py:32-33` 含 `—`，`clause_boundaries.py:13-14` 不含；`punctuation_boundaries.py:14` 的 TERMINALS 缺 `…`，另两处都有。**同一段音频，因走哪条路而切法不同。**

`punctuation_boundaries.py:5` 的注释写着"This candidate is not wired into CaptionChunker yet"，而 `caption_chunker.py:459-461` 正在 `import` 并使用它 —— 注释与代码直接矛盾。

### 3.4 与代码相反的注释、与配置无关的分支

```python
# mt_openai_compat.py:87-88   —— 只匹配国内 endpoint 子串
if "dashscope.aliyuncs.com" in self.base_url:
    payload["enable_thinking"] = bool(...)
```
`https://dashscope-intl.aliyuncs.com/...` 不含该子串 → 国际站的 Qwen3 推理模型 `enable_thinking` 不被关闭 → 推理吃满 `max_tokens=256` → 空 content → 被计为 Provider 失败。反过来，任何含该子串的非 DashScope 端点会被塞入厂商私有字段。

```python
# subtitle_pipeline.py:1395-1397  注释声称"本模块刻意不重复 fallback 交接"
# subtitle_pipeline.py:1338-1339  实际却按 _degrade_level>=2 整个切换 provider
```

```python
# player.js:93
minDwell: 1.2
# subtitle-scheduler.js:43-46 同一模块自述"deliberately short … 0.6"
```

### 3.5 未被强制的能力声明

`TranslationCapabilities.max_input_chars`（`base.py:481`）在 `fallback.py:91` 被 `min()` 合并，然后**全仓库再无读取**。一次超长 cue → 400 → `ProviderRequestError`（`http.py:101`）→ `fallback.py:129-134` **把该 Provider 整个会话禁用**。默认单 Provider 配置下，这意味着**一条超长字幕后字幕彻底消失，直到重启**。

### 3.6 前端复制粘贴痕迹

- `createFollowModeController` 存在两份：`media-clock.js:181` 与 `workbench-controller.js:47`；`index.html:535/538` 的加载顺序让后者胜出，前者在浏览器里是死代码，但**仍有单元测试覆盖**（`test_media_clock.js:117`）。
- `updateStallOverlay(data)` 每轮状态轮询被调用两次：`player.js:472` 和 `:490`。
- `test_web_assets.js:215` 用正则断言了**一句注释**（`cue.state === "done" || cue.state === "failed"`），而真实代码在 `subtitle-scheduler.js:57` 是相反的逻辑。该测试永远为绿。
- 字幕叠加层用 `wall + offset + SUBTITLE_RENDER_ADVANCE_SECONDS(0.15)`（`player.js:1530`），时间轴列表用 `wall + offset`（`:1323`），两者相差 150 ms；叠加层用 `active()`（`:1536`），列表用 `pick()`（`:1340`）。

---

## 4. 问题清单（按严重度排序）

| # | 严重度 | 问题 | 位置 | 实测证据 | 长时后果 |
|---|---|---|---|---|---|
| 1 | **致命** | 分句器回收被 `state.closed` 门控，Deepgram 永不置位 | `caption_chunker.py:192/238/253` | 8h → `_items`/`_captions`/`_spans` 各 6,545 条；每句成本 64× | 数小时后事件循环被分句器拖死，字幕整体抖动 |
| 2 | **致命** | HLS 分片不做关键帧对齐 | `core.py:696-710` | 2s GOP 源切出 20 片 vs 冒烟 10 片 | 约 50% 分片不可独立解码 → 卡顿/黑屏，音画仍在走 |
| 3 | **致命** | `hls.recoverMediaError()` 无上限无条件调用 | `player.js:416-421` | 代码确认 | 解码失败 → 完整 MSE 重建 → 再失败，自激循环 |
| 4 | **高** | ASR 握手失败 1:1 泄漏 ClientSession（默认 Provider） | `asr_dashscope_task.py:149-154`、`asr_qwen_realtime.py:75-78` | 40 次失败 → 40 个未关闭会话 | FD/连接器耗尽 |
| 5 | **高** | 前端 `wake()` 永久分叉轮询链 | `poll-loop.js:42-47` + `player.js:1880-1883` | 5 次唤醒 → 6 条链，`stop()` 后仍剩 5 条 | 请求速率翻数倍，反向打爆单线程服务端 |
| 6 | **高** | Provider 热切换使 cue 永久卡在 `src` | `subtitle_pipeline.py:1340-1341` | 16/60 卡死，16 条状态残留 | 字幕"只有原文"，状态字典缓慢泄漏 |
| 7 | **高** | `_pending_finals` 在 anchor 永不收敛时无限持有 | `subtitle_pipeline.py:373/1155-1161` | 代码确认 | 无超时、无计数、无提示，字幕**完全不出现**而状态仍报 running |
| 8 | **高** | CI 漏跑 18/43 个测试，含核心分句测试 | `package.json:9` | 已比对 | 重构无安全网 |
| 9 | **高** | 测试入口用裸 `python`（本机 3.9），且无 `PYTHONPATH` | `package.json:9` | 3.9 下 5 ERROR；无 PYTHONPATH 时 5 文件 import 失败 | 开发者看到的红/绿都不可信 |
| 10 | **中** | `finish_reason` 从不检查，无回显防护 | `mt_qwen_mt.py:134-140` | 代码确认 | `content_filter`/`length` 截断被当作有效字幕发布 |
| 11 | **中** | 一次超长 cue 永久禁用整个 Provider | `fallback.py:129-134`；`max_input_chars` 无人读 | 代码确认 | 单 Provider 配置下字幕彻底消失 |
| 12 | **中** | 超时/慢模型被记为"Provider 故障" | `subtitle_pipeline.py:1447-1454,1470-1471` | 代码确认 | 失败率指标无法区分"模型慢"与"服务坏" |
| 13 | **中** | 阶段延迟遥测整体失效 | `subtitle_pipeline.py:1252/1518-1522` | 管线 soak：`latencyUnknown = 4090/4090`（100%） | 用户看到的延迟归因全是空的 |
| 14 | **中** | 后端 1 个 `runtime/providers.json` 明文落盘（env key 被固化） | `config.py:665-675,586` | 代码确认 | 环境变量密钥变成磁盘明文 |
| 15 | **中** | 两个 Provider 失败计数器永远相等；多组别名键 | `subtitle_pipeline.py:1470-1471,1733-1740,1693-1703` | 代码确认 | 指标自相矛盾 |
| 16 | **中** | 源/音频两腿时间戳独立重写 → 永久 A/V 偏移 | `core.py:630-644` | 子代理复现（待我复核） | 音画不同步 |
| 17 | **中** | 公开窗口 180s < 播放器预算 200s；且先删文件后改 playlist | `core.py:738/867-872` | 代码确认 | 落后播放器请求 404 → 跳变 |
| 18 | **低** | `_spans` 每次 `telemetry()` 全量排序，被 3Hz 轮询 | `caption_chunker.py:316` / `player.js:1869-1874` | 实测 62 µs@8h | 随会话线性增长的空转 CPU |
| 19 | **低** | recover 后 `retime()` 用过期时钟；用户拖动不回 `retime()` | `player.js:584-593` vs `:1762` | 代码确认 | 回看时字幕错位/永久不显示 |
| 20 | **低** | NaN 污染导致前端 cue Map 永不清理 | `player.js:1295-1299` | 代码确认（潜在） | 一旦触发即无界增长 |

---

## 5. 重构方案

### 5.1 先修 5 个致命/高危（低风险、高收益，1~2 天）

1. **分句器回收改为按"无待处理单元"而非 `closed`**
   `caption_chunker.py:238-254`。具体：引入 `_last_touched: dict[key, float]`，在 `observe`/`advance_audio` 时按 `now` 做 LRU 淘汰（例如 >256 条且超过 30 秒未更新的 lane 直接释放）；`_items` 的淘汰条件从 `state.closed` 改为"该 item 在 64 条窗口外且无 pending unit"。同时给 `_spans` 加 `maxlen`（如 2048），并让 `telemetry()` 复用增量百分位而不是每次全排序。
   **验收**：重跑 `soak_cpu_curve.py`，8h 时 `us/句` 应与首段同量级（<1.5×），容器尺寸收敛到常数。

2. **HLS 分片恢复关键帧对齐**
   `core.py:696-710`。移除 `split_by_time`，并把 `-hls_time` 设成与源 GOP 匹配的值（或改为 `-force_key_frames` 由我们自己控制关键帧间隔）。若必须保持 1 秒分片的低延迟，就要在 ingest 侧用 `-force_key_frames "expr:gte(t,n_forced*1)"` 重新生成关键帧（但那需要重编码，与"不做转码"的约束冲突 → 因此实际应改为按 GOP 切分，接受 2 秒分片）。
   **验收**：把 `verify_gop_alignment.ps1` 加进 CI，断言生产参数下 `#EXT-X-INDEPENDENT-SEGMENTS` 存在且分片数与关键帧间隔一致。

3. **hls.js 错误恢复加上限与退避**
   `player.js:416-421`。加计数器（如 3 次内 `recoverMediaError()`，之后 `swapAudioCodec()`，再之后 `hls.startLoad()`，超过阈值停下来报错而不是无限重建）。

4. **ASR 适配器补 `try/except BaseException: await self.aclose(); raise`**
   `asr_dashscope_task.py:149-195`（把 `ws_connect` 与首个 `send_json` 一起纳入）、`asr_qwen_realtime.py:75-78`。照抄 `asr_deepgram_streaming.py:192-203` 的写法。
   **验收**：把 `verify_asr_leak.py` 转成正式测试，断言 `session.closed is True`。

5. **`poll-loop.js` 的 `wake()` 加在飞行判定**
   `poll-loop.js:42-47`。加 `inFlight` 标志：`wake()` 时若 `inFlight` 为真则只置一个 `pendingWake` 标记，由当前 `tick` 的 `finally` 消费；并在 `finally` 里用局部句柄（`if (timer === null || timer === myTimer)`）避免覆盖。
   **验收**：把 `verify_poll_fork.js` 转成正式测试，断言 N 次 wake 后链数为 1、`stop()` 后为 0。

6. **修 `provider is None` 的终态缺失**
   `subtitle_pipeline.py:1340-1341`。把 `continue` 改成走 `_drop_translation_cue(cue)`（它会写 `failed` 并记终态），或在 `finally` 里补一次"若仍无终态则记为 dropped"。

7. **修测试入口**
   `package.json:9`。改成显式 `py -3.10` / venv python、设 `PYTHONPATH=prototype/hls-companion`、用 `unittest discover` 全量发现（而不是手工列 25 个文件），并补 `streamlink` / `playwright` 的可选跳过。

### 5.2 再删化石（中等风险，收益大：约 −250 行 + −6 个恒零指标）

把 `_map_caption_observation` 返回 `None` 的行为从"落到 legacy 路"改为"计数并丢弃"（新增一个 `unmapped_observations` 计数器），然后删除：

`_handle_interim`、`_handle_final`、`_SplitState`、`_split_state_for`、`_maybe_force_commit`、`_open_utterance_start`、`_ingest_pcm_chunk`、`prefix_split_enabled`、`prefix_split_after_seconds`、`max_utterance_seconds`、`_caption_evidence_seen`、`_previous_final*`、`_last_forced_commit_at`，以及 `prefix_cues`/`final_tails`/`final_absorbed`/`split_conflicts`/`prefix_rewrites`/`unjoined_finals` 六个计数器。

同时清掉分句器里的死代码：`request_hard_commit`、`manual_commit`、`hard_cap_cuts`、`manual_hard_commits`，以及 `punctuation_boundaries.py:5` 那句与事实相反的注释；统一三份"短句保护"常量与标点表到一处。

### 5.3 最后再拆模块（不要和上面混在一起做）

按最小风险顺序：
1. **`PcmPump`**：reader + sender + breadcrumbs + `_stream` 身份。规则："每次 await 之后重新取当前 stream，变了就丢弃并计数"，消灭 `_pcm_sender:647` 的 stale-stream 写入。
2. **`TranslationDispatcher`**：queue + budgets + workers + context + 全部延迟记录。让"provider 为空"和"截止期 vs Provider 故障"的分类只存在于一处。
3. **生命周期自洽**：`start()` 里重置 `_pcm_offset`/`_last_sent_pcm_offset`/`_stream_pushed_seconds`/`stats`；用一把 `asyncio.Lock` 包住 start/stop；`_stopping` 在 `gather` 返回前不清零。（现在 `:432` 的 `if self._running: return` 在 `await` 之前检查，双击启动会起两个 ffmpeg 并让 `self._tasks` 覆盖丢掉第一组任务。）

**明确建议不要动的部分**（这些是真正写对的地方）：`CaptionChunker` 的观测契约、`CueStore`、`TranslationBudgetPolicy`、`RollingContext` 的排序键、`_frontier_time` 环形设计。

---

## 6. 复现方式

```powershell
# 环境（项目要求 3.10+；本机默认 python 是 3.9，会误报）
py -3.10 -m venv .venv-audit
.\.venv-audit\Scripts\python.exe -m pip install -r prototype\hls-companion\companion\requirements.txt
$env:PYTHONPATH = "F:\Projects\LagLingo\prototype\hls-companion"

# 全量测试（正确姿势）
Get-ChildItem prototype\hls-companion\tests\test_*.py | ForEach-Object { & .\.venv-audit\Scripts\python.exe $_.FullName }

# 本次实测脚本
python .scratch\laglingo-audit\soak_chunker.py          # 容器 1h/8h 增长
python .scratch\laglingo-audit\soak_cpu_curve.py        # 8h CPU 劣化曲线
python .scratch\laglingo-audit\soak_attribution.py      # 归因到具体行
python .scratch\laglingo-audit\soak_pipeline.py         # 端到端管线 soak
python .scratch\laglingo-audit\verify_asr_leak.py       # ASR 会话泄漏
node   .scratch\laglingo-audit\verify_poll_fork.js      # 前端轮询链分叉
python .scratch\laglingo-audit\verify_provider_swap.py  # 热切换状态泄漏
pwsh   -File .scratch\laglingo-audit\verify_gop_alignment.ps1   # 分片关键帧对齐
```

---

## 7. 需要真实长跑验收的项（本次无法替代）

本次是**仿真加速 soak + 定向复现**，以下必须在真实直播上跑，才算数：

1. 真实 YouTube/Bilibili 直播连续 **8 小时**，每 30 分钟记录一次 `/api/status` 的容器尺寸与 `us/句`，验证 5.1 的修复确实把曲线拉平。
2. 修好分片对齐后，用真实 1080p60 源观察 30 分钟内 `bufferStalled` / `MEDIA_ERROR` 次数（应从"每几分钟一次"降到接近 0）。
3. 用**错误的 API Key** 故意让 ASR 失败 1 小时，观察进程 FD 数是否保持平稳（修复前应线性增长）。
4. 真实切标签页 20 次，用 DevTools Network 计数确认 `/api/subtitles` 请求速率不随时间上升。
5. 真实中日/中英直播各 1 小时，验证 `sourceOnlyCues` 占比（本次仿真在积压场景下到 33%，真实值待测）。

---

## 8. 一句话总结

**代码不是"逻辑混乱"，而是"主链路设计正确、但被三处没收口的补丁拖住"**：分句器的内存回收被一个 Deepgram 永不满足的条件锁死（64× 劣化）；视频分片被一个注释自认"不受 GOP 约束"的参数切碎了（约 50% 不可解码）；默认 ASR 适配器每次握手失败漏一个连接；前端每切一次标签页就永久多一条轮询链。**这四条恰好一一对应你说的"字幕流延迟、视频流卡顿、Provider 调用有问题、长时跑不稳"**。先修这四条，再删化石，最后才拆模块。
