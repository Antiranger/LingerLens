# 字幕嵌入方案 · 重新设计（对齐 / 时长 / 译文就绪）

> 日期：2026-08-30。承接 `docs/subtitle-debugging-handoff.md`。
> 前置：`docs/subtitle-pipeline-implementation-plan.md`（原始规格）。
>
> 本文基于**通读现有实现后的根因定位**，不是再一轮猜测。用户报告的三个症状是**三个独立故障**，
> 分别在服务端时间轴、播放器显示门控、ASR 断句粒度。只修一个不会让体验变好。

---

## 1. 先裁决旧的嫌疑清单

| 旧嫌疑 | 裁决 | 证据 |
|---|---|---|
| **S1 `hls.playingDate` 可能不存在** | ❌ **排除。它存在。** | 我在 `web-player/vendor/hls.min.js` 里查到 `{key:"playingDate",get:function(){return this.streamController.currentProgramDateTime}}`，且解析器含 `#EXT-X-(PROGRAM-DATE-TIME\|…)`。播放器侧的墙钟读数是**可靠的**。 |
| **S3 pcm_offset 与播放时间轴不同源** | ✅ **确认，且比描述的更严重**——见 RC-1，是**结构性**的，不是漂移。 | `server.py:387` / `subtitle_pipeline.py:295` |
| **S2 timingSource 退化成 approx** | ⚠️ 可能，但**不是主因**。即使 timing 完美，RC-3 仍然让中文永远显示"翻译中"。 | `player.js:420` |
| **S6 翻译 3–5s** | ⚠️ 加剧但非主因。 | — |

**结论：播放器的时钟是对的，服务端给出的 cue 时间戳是错的。** 排查方向此前一直反了。

---

## 2. 根因（按危害排序，全部带代码位置）

### RC-1：字幕腿与封装腿**没有共同的时间原点**（→ 完全不对齐，误差无界）

`cue.tEnd = epoch + end_pcm`，其中 `epoch` 取自封装管线首个分片的 PDT，`end_pcm` 是字幕腿解码出的媒体秒数。这个等式成立的前提是"两条腿从同一个字节开始"。**三处都破了**：

**(a) tee 挂得太晚 —— 这是最致命的一条。**
`server.py:318-321` 在 `session.start()` 和 `source_ingest.start()` **之后**才调 `_start_subtitles`，`server.py:387` 才 `attach_audio_tee(sink)`。在这之前泵已经转发的所有字节，字幕腿**完全看不到**。于是 `pcm_offset = 0` 对应的媒体位置是某个未知的 X 秒，而 `pdt_epoch` 对应媒体位置 0。**误差 = X，无界，每次启动都不同。** 单这一条就足以让字幕完全对不上。

**(b) 启动突发（burst）让两条腿进入不同的时间空间。**
yt-dlp 启动时会把直播播放列表窗口里已有的几秒内容一次性吐出，字幕腿的 FFmpeg 以 CPU 速度转 PCM。而 FFmpeg 的 HLS PDT 语义是：首片 PDT = muxer 启动时的系统时钟，之后 `PDT(n) = PDT(0) + Σ 分片时长`——**PDT 是一条"媒体线性"时钟，不是墙钟**。突发期间 PDT 会跑到墙钟前面好几秒。

**(c) 同一次会话里混用了两条时间轴。**
`subtitle_pipeline.py:548-550` 的 `_current_epoch()` 在 `pdt_epoch` 还没就绪时回退到 `_worker_epoch`，而 `_worker_epoch = wall_clock() - pcm_offset`（L295-296）是**真实墙钟**。启动阶段正是 PDT 还没写出来的时候——于是**最早的一批 cue 活在墙钟空间，之后的 cue 活在 PDT 空间**，两者差着整个 burst 的量。

### RC-2：`pcm_offset` 不是时钟，是字节计数器

`_pcm_reader`（L282-306）以 FFmpeg 的产出速度推进 `pcm_offset`。任何突发（启动、网络追赶、`--concurrent-fragments 4` 预取）都让它跑在墙钟前面。

**这个设计本身是对的**——我们要的就是媒体位置而不是墙钟。但它必须**锚在同样是媒体空间的参考上**。RC-1(c) 把墙钟塞进来，等式就废了。

附带问题：L301-304 队列满时丢最旧的 PCM，`_pcm_offset` 照常推进（时间轴语义正确），但**丢弃没有计数**，ASR 静默漏音频。

### RC-3：显示门控**不等译文**（→「中文全是翻译中」）

`player.js:420`：
```js
const active = [...subtitleCues.values()].filter((cue) => cueStart(cue) <= t && t <= cue.tEnd + cue.hold)…
```
`cueStart = cue.tStart ?? cue.tEnd`（L406-409）。`tStart` 是**这句话开始说**的时刻，而翻译要等这句话**说完**、ASR 出 final 之后才发起。任何长于管线延迟（约 2–4s）的句子，播放头都会在译文存在之前就到达 `tStart` → `player.js:423` 渲染 `"翻译中…"`。

**每一个长句都必然全程显示日文 + 「翻译中」**，译文落地时 cue 已经快过期了。这与用户描述逐字吻合。

这正是原规格 §0 修正 1 明令禁止的做法。上一轮会话把 `[tEnd, tEnd+hold]` **改回**了 `[tStart, tEnd+hold]`（debug handoff §4.4），因为用户抱怨"长句要等到最后才出字幕"。**那个抱怨是真的，但改错了层**——见 RC-4。

### RC-4：ASR 断句粒度太粗（→「一大段话等到最后才出现」）

`silence_duration_ms=600` 的服务端 VAD，意味着只有静音满 600ms 才算一句话结束。主播连续说话时会产生一句 10–20 秒的"句子"，对应**一个** ASR final。

`subtitle_pipeline.py:408` 用 `split_with_timing` 按 80 字符切分并线性内插时间戳，产出多个 cue——**但它们是在整段话说完的同一瞬间才被创建的**。第一个子 cue 的 `tStart` 在诞生时就已经是过去时。

**把锚点挪到 `tStart` 救不了这个**：cue 生成时播放头已经越过它，于是要么不显示，要么瞬间进入又退出显示窗口——**这就是「一闪而过」**。

真正的修法在 ASR 层：**把句子切短**（2–5 秒），而不是在显示层补偿。

### RC-5：hold 太短 + 单 cue 抢占 → 闪烁

- `calculate_hold`（`subtitle_text.py:143`）= clamp(1.2, 0.06·len, 8.0)。10 个中文字 → **1.2 秒**。
- `player.js:420` 在所有活跃 cue 里只取 `tEnd` 最大的一个。RC-1/RC-4 造成的"一批 cue 时间戳挤在一起"会让它们互相瞬间顶替。
- **没有最小驻留时间，没有防抖迟滞。** 任何正经字幕渲染器都要保证一条字幕上屏后至少可读 1–1.5 秒，绝不允许被立刻顶掉。

### RC-6：cue 生命周期用错了时钟，且轮询游标会饿死客户端

- `subtitle_store.py:58` `_prune` 用 `time.time()`（墙钟）去比 `t_end`（PDT 空间）。PDT 落后墙钟时，cue 会在播放头到达之前被删掉——**无声失败**。`player.js:390` 的 `Date.now()/1000 - 125` 同病。
- `player.js:387` `subtitleSince = Math.max(subtitleSince, cue.tEnd)`。一旦有 cue 带着 burst 造成的"未来"时间戳到达，`since` 会跳到未来；服务端查询是 `tEnd > since OR revision > sinceRevision`（`subtitle_store.py:120`），而 `sinceRevision` 也同步推进了 → **后续正常 cue 一条都取不回来**。真实的饿死路径。

---

## 3. 重新设计

四条原则，对应四组修复。**顺序不能调**——A/B 不做完，其它都是在错误的时间轴上打磨。

### 原则一：只有一条时钟，而且它是被**测量**出来的，不是假设出来的

#### Fix A：让两条腿共享字节原点（消除 RC-1a）

把 tee 挂到**任何字节流动之前**：`YtDlpLiveIngest(..., audio_tee=sink)`，在 `_TcpPump` 构造时就装好 sink。

`handle_start` 重排为：

```
构建 provider → 创建 SubtitlePipeline → await pipeline.start() 拿到 sink
  → YtDlpLiveIngest(url, selector, auth_args, audio_tee=sink)
  → source_ingest.start()      # 泵开始监听，但还没有客户端
  → session.start()            # 封装 ffmpeg 连上 TCP，字节这才开始流动
```

**这一步是精确的，不是近似的**：现有 `_TcpPump._run` 只在有客户端连接后才读 yt-dlp 的 stdout（`ytdlp_ingest.py:60-74` 的设计本意是靠 OS 管道背压节流）。所以封装 ffmpeg 连上的那一刻，就是两条腿共同的字节 0。

同时：字幕启动失败仍然**不得**让播放失败（保留现有的 try/except 语义），但失败要显式上报。

#### Fix B：把 `pdt_epoch` 换成**连续测量的媒体锚点偏移 C**（消除 RC-1b/c、RC-2）

彻底删掉 `_worker_epoch` 和墙钟回退。改成：

```
cue.tEnd = pdt_0 + end_pcm + C
```

- `pdt_0`：私有播放列表首个分片的 PDT（已有，`core.py:634`）。
- `end_pcm`：字幕腿的媒体秒数。
- `C`：两条腿的**常量**差（各自 ffmpeg 的起始丢弃 + 视频腿/音频腿的 A/V 起点差）。

**C 是测量出来的，每秒采样一次：**

- `DelayedPlaylistPublisher` 新增 `private_media_seconds`（见过的全部分片时长之和）。
- 管线同时刻采样 `(pcm_offset, private_media_seconds)`。
- `C_sample = private_media_seconds + 0.5 * target_duration − pcm_offset`
  （`+0.5·targetDuration` 补偿"分片只在写完后才可见"的锯齿量化，取中值）。
- 取**前 20 个样本的中位数**后冻结；之后继续采样但只用于上报漂移。

**为什么这个方法对 burst 免疫**：突发期间两个计数器以**同样的超实时速率**一起前进，差值不变。这正是它优于任何墙钟锚定的地方。

残余误差约 ±0.5s（分片量化），由偏移滑块吸收。目标 <1.5s，达标。

`/api/status.subtitles` 必须上报：`mediaAnchorC`、`mediaAnchorSamples`、`mediaAnchorSpread`（样本 IQR）、`mediaAnchorDrift`（当前样本 − 冻结值）。**`mediaAnchorSpread` 持续 > 1.0s 就说明 Fix A 没生效**，这是自检信号。

#### Fix C：修正 VAD 时间戳的过冲（缓解 RC-2/S4）

`_handle_asr_event`（L372-380）用事件到达瞬间的 `_last_sent_pcm_offset` 作为语音边界，包含了一整个 WS 往返 + 服务端缓冲的延迟。

- `speech_started` → `pending_start = _last_sent_pcm_offset − vad_event_lag`，`vad_event_lag` 默认 0.3s，可配。
- `speech_stopped` → 保持现有的 `− silence_duration_seconds` 扣除。
- 跑完 Fix J 的 spike 后，**优先用事件自带的音频偏移字段**（Realtime 形状的事件通常带 `audio_start_ms` / `audio_end_ms`）；有就用，`timingSource="asr"`。
- `status()` 增加 `timingSourceCounts`（asr/vad/approx 三者计数）——分布里 `approx` 占多数就是红灯。

### 原则二：绝不在译文没就绪时把 cue 摆上屏——而是**保证时间表能被满足**

#### Fix D：显示门控 = 译文就绪（消除 RC-3）

- cue **只有** `state ∈ {done, failed}` 才可显示。`src` / `translating` **永不上屏**。删掉 `"翻译中…"` 这个渲染分支。
- 显示窗口锚回 `tEnd`：`[tEnd, tEnd + hold]`。**这是原规格 §0 修正 1，不要再改回 tStart。** RC-4 的抱怨由 Fix F 解决，不由这里解决。
- 译文晚于播放头到达时（"迟到 cue"）：
  - `catchUp: "show"`（默认）：迟到 ≤ `maxLateSeconds`（默认 2.0s）仍然显示，从当前时刻起显示 `hold` 秒。轻微失步好过缺字幕。
  - `catchUp: "drop"`：丢弃并计数。
  - 迟到超过 `maxLateSeconds`：一律丢弃。
- 翻译超时/失败 → `state="failed"`，**只显示原文**（这是一个明确的降级结果，不是"还在等"）。
- 计数上报：`lateCues` / `droppedLateCues` / `sourceOnlyCues`。

#### Fix E：把延迟预算闭环起来 —— 这是本产品的核心命题，目前**完全没有接线**

整个产品的立论是"把 5–7 秒观看延迟当作 ASR+翻译的计算预算"。但系统从未测量过预算是否够用，也从未据此调整过。

**测量**：对每条 cue 记录 `readyLag` = 译文完成的墙钟 − 该句音频末尾进入管线的墙钟。
后者可从 `t_asr_final_wall − (pcm_offset_now − end_pcm)` 估出（稳态下 pcm 以 1x 前进）。
维护 p50 / p95 滚动窗口（最近 60 条）。

**预算余量** = 当前总观看延迟 − p95(readyLag)。总观看延迟已在遥测里有（`hiddenMediaSeconds + 浏览器落后边缘`）。

**动作**：
- 遥测行显示「字幕就绪延迟 p50/p95」和「预算余量」。
- 余量 < 0.5s 持续 30 秒 → 明确可执行的提示：**「字幕来不及，建议把『下载后额外延迟』调到 N 秒」**，并给一键应用。
- 可选 `autoDelay` 开关：自动抬高 `publishDelay`（上限可配，默认 12s）使余量 ≥ 1.5s；负载回落后按 0.5s/分钟缓慢回收。这就是 bailian handoff §21 的 Adaptive Delay，**是本产品最有价值的单个特性**。

**实现代价极低**：`DelayedPlaylistPublisher._tick` 每次迭代都重新读 `self.publish_delay`（`core.py` 发布判据里直接引用），所以**直接改这个属性就能实时生效**。加一个 `POST /api/publish-delay` 即可，不需要重启会话。

### 原则三：cue 的粒度要匹配"阅读"，而不是匹配 VAD

#### Fix F：把句子切短（消除 RC-4）

- `silence_duration_ms`：600 → **400**（官方对快速断句的建议值）。我们有预算，但 15 秒一句对字幕来说无论多少预算都不可用。
- 新增**硬性句长上限** `maxUtteranceSeconds`（默认 6.0）：当 `pcm_offset − pending_vad_start > maxUtteranceSeconds` 时，主动发 `input_audio_buffer.commit` 强制切句。
  ⚠️ Fix J 的 spike 要确认 qwen3-asr-flash-realtime 在 server_vad 模式下是否接受手动 commit（第三方实现里有 `supportsManualCommit` 的迹象，需实测）。不支持就退而求其次：只降 `silence_duration_ms`，并把上限逻辑记为已知限制。
- P2：用 `transcription.text` 的 `text`（已确认前缀）在长句**说话过程中**就产出 cue。存储模型要为此预留（cue 可被后续 final 替换/合并）。
  - **✅ 已实现（2026-08-31）**：采的是**追加式**而非替换/合并——见 `subtitle_pipeline._handle_interim`。前缀每新增长出一批完整分句（`。！？`）就立刻作为独立 cue 发出（tStart 链式：首句 = VAD 起点，后续 = 前一切点；tEnd = 确认瞬间的音频位置，实测事件滞后 0.04–0.10s）；final 到达时只补发未发出的尾巴（`_handle_final` 对账）。仅在 utterance 超过 `prefixSplitAfterSeconds`(3.0s) 后激活，短句行为不变。验证数据（同日倒计时直播 JInec6ORhIk，160s 采样）：21 个 utterance 中 6 个 ≥9.2s（最长 30.2s），前缀切分可将 32 个分句平均提前 8.4s 发出；短句的前缀确认均发生在 final 之后（提前量 0.00s），证明门限设计正确。监控计数：`prefixCues`/`finalTails`/`finalAbsorbed`/`splitConflicts`/`prefixRewrites`。

#### Fix G：不要把一个 final 切成多个**独立排期**的 cue（消除 RC-4 的次生问题）

`split_with_timing` 的字符比例内插是**伪造时间戳**。改为：

- **一个 ASR final = 一个 cue。** 切分只用于**渲染换行**（中文 ≤ 24 字/行，最多 2 行），不产生新的排期单元。
- 文本确实过长（> 40 中文字）说明 Fix F 的句长上限失效 —— **计数上报** `overlongCues`，用更长的 `hold` 承载，而不是编造子时间戳。
- `split_with_timing` 保留在 `subtitle_text.py` 但仅供渲染层使用，单测相应改写。

### 原则四：渲染必须稳定可读

#### Fix H：客户端改成真正的 cue 调度器（消除 RC-5）

- 维护**有序、不重叠**的显示队列。新 cue 与在显 cue 重叠时，新 cue **排队**，在显 cue 至少驻留 `minDwell`（默认 1.2s）后才被替换——**绝不瞬间顶替**。
- `minDwell` 1.0s ≤ hold ≤ `maxDwell` 7.0s。
- 相邻 cue 间隔 < 300ms 时**不清屏**（避免频闪），直接换文本。
- 保留 100ms `setInterval` 节拍（不要用 rAF——标签页隐藏时 rAF 会停）。
- 加 120ms CSS 淡入淡出，替换不刺眼。
- 排版：中文行在上（大号），原文行在下（小号、降透明度），由 `mode` 控制。

#### Fix I：轮询游标改用**单调序列号**（消除 RC-6）

- 服务端每次 cue 新增/更新递增全局 `seq`；`Cue.seq` 随之更新。
- 接口改为 `GET /api/subtitles?afterSeq=N`，返回 `seq > N` 的全部 cue，附 `maxSeq`。
- **彻底不再让时钟参与轮询游标。**
- 保留期裁剪：服务端按**最新 cue 的 tEnd** 为基准做 120s 窗口（不是 `time.time()`）；客户端同理按已知最大 `tEnd` 裁剪，不用 `Date.now()`。

### Fix J：把从未做过的实测补上

`docs/subtitle-debugging-handoff.md` §3 说明：原规格 §2.1 的四个 ⚠️ **至今没实测**，而现在 key 已配置。**这是第一步，不是可选项。**

`scripts/asr-spike.py` 必须回答：
1. `transcription.completed` 是否携带 `begin_time`/`end_time` 或 `audio_start_ms`/`audio_end_ms`？→ 决定 Fix C 走哪条路
2. `speech_started` / `speech_stopped` 的确切事件名与相对 `completed` 的到达顺序？
3. server_vad 模式下是否接受手动 `input_audio_buffer.commit`？→ 决定 Fix F 的句长上限能否实现
4. 长会话空闲超时的实际行为

### Fix K：一个能挡住这整类 bug 的黄金测试

这才是"上一轮全绿的测试没拦住线上全错"的解药。

`scripts/subtitle-alignment-smoke.py`（离线，**不需要 API key**）：

1. 合成 60s H.264/AAC 测试流，音轨在 t=5,15,25,35,45s 处各放一个 1kHz 蜂鸣。
2. 用**桩 ASR provider**：检测到蜂鸣就产出一条 final，文本为 `MARK-<n>`，并按各 provider 形状分别模拟"带时间戳"和"仅 VAD 事件"两种。
3. 完整跑真实管线（tee → ffmpeg → 时间轴 → CueStore）。
4. **断言 `cue.tEnd` 映射到的 PDT 与真实标记媒体时间误差 ≤ 300ms。**
5. 额外断言：`mediaAnchorSpread < 0.5s`；无 cue 落在墙钟空间。

同时补：桩翻译 provider（可注入固定延迟）驱动的 Fix D/E 测试——验证"译文没就绪就不上屏""迟到 cue 按策略处理""readyLag 统计正确"。

---

## 4. 改动清单

| 文件 | 改动 |
|---|---|
| `companion/ytdlp_ingest.py` | `YtDlpLiveIngest.__init__(..., audio_tee=None)`；`_TcpPump(label, tee=None)` 构造期装 sink；PCM/TS 丢弃计数 |
| `companion/server.py` | `handle_start` 重排（Fix A）；`/api/subtitles` 改 `afterSeq`；新增 `POST /api/publish-delay`；status 增 `mediaAnchor*` / `timingSourceCounts` / `readyLagP50P95` / `budgetMarginSeconds` / `lateCues` |
| `companion/core.py` | `DelayedPlaylistPublisher.private_media_seconds`；确认 `publish_delay` 可实时改（加注释锁定该语义） |
| `companion/subtitle_pipeline.py` | **删** `_worker_epoch` 与墙钟回退；新增 `MediaAnchor`（C 的采样/中位数/冻结/漂移）；VAD 过冲修正；句长上限；一 final 一 cue；readyLag 统计；迟到策略 |
| `companion/subtitle_store.py` | `Cue.seq` + 全局序列号；`query(after_seq=)`；保留期基准改为最新 cue 的 tEnd |
| `companion/subtitle_text.py` | `split_with_timing` 降级为渲染用换行（不再产出排期单元）；`calculate_hold` 加 `minimum` 下限 1.2 → 由调用方传 `minDwell` |
| `companion/providers/asr_qwen_realtime.py` | 按 spike 结果提取事件里的音频偏移字段；`silence_duration_ms` 默认 400；手动 commit 支持 |
| `web-player/player.js` | 显示门控只认 `done`/`failed`；cue 调度器 + minDwell + 防抖；`afterSeq` 轮询；预算余量提示与一键调延迟 |
| `web-player/index.html` `style.css` | 淡入淡出、两行排版、诊断遥测行 |
| `scripts/asr-spike.py` | 补齐四个 ⚠️ 的实测输出 |
| `scripts/subtitle-alignment-smoke.py` | **新增**，黄金对齐测试 |
| `tests/` | 新增 `test_media_anchor.py`、`test_cue_scheduler.js`；改写 `test_subtitle_text.py` 的切分用例；`test_subtitle_pipeline.py` 增迟到/就绪门控用例 |

---

## 5. 实施顺序与验收

**不要并行做。每一步都有独立的、可观测的验收信号。**

| # | 内容 | 验收 |
|---|---|---|
| **1** | Fix J：跑 spike，把四个 ⚠️ 的答案写回本文 §3 Fix C/F | 拿到真实事件序列；确定 `timingSource` 能不能达到 `"asr"` |
| **2** | Fix K：先写黄金测试（此时它**应该失败**） | 测试跑通并**红**——量化出当前误差有多大（预期数秒） |
| **3** | Fix A + Fix B：字节原点 + 媒体锚点 C | 黄金测试**转绿**（误差 ≤ 300ms）；真实直播里 `mediaAnchorSpread < 0.5s` |
| **4** | Fix I + Fix C：序列号游标 + VAD 过冲修正 | `timingSourceCounts` 里 `approx` 占比 < 5%；长跑 10 分钟不丢 cue |
| **5** | Fix D + Fix G + Fix H：就绪门控 + 一 final 一 cue + 调度器 | **浏览器实测：不再出现「翻译中」；无闪烁；单条字幕驻留 ≥ 1.2s** |
| **6** | Fix F：句长上限 + `silence_duration_ms=400` | 长段独白被切成 2–5 秒的句子；`overlongCues ≈ 0` |
| **7** | Fix E：readyLag 统计 + 预算余量 + 一键/自动调延迟 | 遥测显示 p95 与余量；把翻译换成慢 provider 能触发提示并自愈 |
| **8** | 真实直播 10 分钟人测 | 中文字幕与口型误差 < 1.5s（滑块一次校准）；`teeDropped==0`；无 RSS 增长 |

**中途的现实检查（第 5 步之后就该做）**：把翻译从本地网关（`127.0.0.1:8045` / `gemini-3.1-flash-lite`，实测 3–5s）换成 `https://dashscope.aliyuncs.com/compatible-mode/v1` + `qwen3.5-flash`（关思考，0.4–1.2s）。**这是配置变更不是代码变更**，但它决定了预算余量是 +3s 还是 −1s。3–5 秒的翻译延迟会吃掉几乎全部预算。

---

## 6. 铁律（延续，不要为了修字幕破坏）

1. **tee 绝不阻塞/影响播放**：sink 全链路非阻塞、异常吞掉并计数。Fix A 改构造顺序时尤其要守住。
2. **凭据不出服务端**：`runtime/providers.json`、`runtime/auth-snapshot.json` 的内容永不进任何 HTTP 响应/日志/命令行，禁止打印，禁止提交。
3. **`cue.tEnd` 的语义（句子说完的墙钟）不变**；要改的是**时间来源的正确性**和**显示门控**。
4. ASR 重连期间丢弃音频、绝不补时间戳；`pcm_offset` 照常推进。
5. **不新增 Python 依赖**（只有 aiohttp）。
6. UI 改动跑 `tests/test_web_assets.js`；管线改动跑对应单测；全量 `npm run test:hls-companion`。
7. `test_control_ipc.py` 的命名管道冲突是已知环境问题，不要在新测试里复用命名管道。

---

## 7. P2 终局：把字幕做成 HLS 字幕轨（correct-by-construction）

值得记下来，但**现在不要做**。

发布器已经把分片私有持有 `publishDelay` 秒。它完全可以在发布分片 N 的同时，写出对应的 `sub_N.vtt`（只收录此刻已 `done` 的 cue），再补一个带 `#EXT-X-MEDIA:TYPE=SUBTITLES` 的 master playlist。hls.js 会用原生 TextTrack 渲染——**客户端零时钟运算、零轮询、零闪烁逻辑**，而且"等译文就绪"变成结构性保证。

- **前提**：Fix B 必须先做完。VTT 方案同样需要知道每条 cue 的 PDT，只是把这个知识用在了写文件而不是查表上。时间轴不对，VTT 一样错。
- **代价**：需要 master playlist（现在播放器直接加载媒体播放列表）；`X-TIMESTAMP-MAP` 写错会复现同一类 bug；**已发布的 VTT 无法回填迟到的译文**（现在的 overlay 可以原地替换）。

所以：**先按 §3 修好 overlay 路径**（保留原地替换与样式自由），把时间轴验证到位；VTT 作为之后的加固项。

---

## 8. 给落地会话的第一条命令

```powershell
# 1. 先实测，别先改代码（key 已配置在 runtime/providers.json）
python prototype/hls-companion/scripts/asr-spike.py --audio <16k单声道日语wav>
#    造音频：ffmpeg -i prototype/hls-companion/runtime/media/private/seg_000000001.m4s -ac 1 -ar 16000 a.wav

# 2. 再写黄金测试，确认它现在是红的（量化当前误差）
python prototype/hls-companion/scripts/subtitle-alignment-smoke.py

# 3. 然后才动 ytdlp_ingest.py / server.py 的启动顺序
```
