# 字幕偏移改由源时钟决定 — 测量、证明与设计

> 日期：2026-09-16。修正 `docs/subtitle-audio-leg-design-2026-09-09.md` 的时间轴对齐一节。
> 前置：该文档 §"时间轴对齐" 选择用连续测量的媒体锚点 `C(t) = V(t) − A(t)` 估计偏移。

## 问题

用户报告字幕**提前于正确时间点两三秒**出现，且时好时坏。

锚点估的 `C` 是 `privateMediaSeconds − asrSeconds`，两个都是**各级输出计数器**，不是源位置。它在 600 秒真实直播（TBS NEWS DIG）上的实测：

| 量 | 实测 |
|---|---|
| `privateMediaSeconds − pcm` | 锯齿，min −9.900 / max +9.100，range **19.000 s** |
| 中位数 `windowOffset` | 在 −4.900 … +4.200 之间游走 |
| 修正项 `correction` | 中位 +4.792 |
| **实际生效的 `offset`** | min −0.062 / **max +9.038**，**range 9.100 s** |
| 均值 `priv − pcm` 与真值 `C` 之差 | **−5.725 s** |

`priv` 按整个封装分片跳进（本流 5 s 一片），`pcm` 连续推进，所以两者之差是**锯齿**不是 `C`。它的中心离 `C` 差一个"各级前沿间距"，而那个间距随时间变化（两个下载器落后直播边缘的程度不同），**不是流的属性**。任何中位数／门限／窗口／跳片重置都取不回 `C`。

结论：`offset` 在 9.1 s 区间内摆动，**字幕最多提前约 4.5 s、最多滞后约 4 s**。这就是用户看到的现象。中位数误差只有 −0.099 s，所以这是**间歇性**跑偏，不是固定提前——与"有时两三秒"的描述一致。

## 方案

两条 rendition **本来就带同一条绝对 90 kHz 源时钟**，只是被 yt-dlp 的 ffmpeg 下载器在封装时抹掉了。保住它，`C` 就是一次减法：

```
C = sourcePtsFirst(ASR 音频腿) − sourcePtsFirst(打包视频腿)
```

没有中位数、没有速率门、没有窗口、没有跳片重置、没有积压修正、没有接收延迟项。cue 的媒体位置仍是 `pcm + C`，**双流设计一字未改**。

### 落地改动

| 文件 | 改动 |
|---|---|
| `ytdlp_ingest.py` | 下载器参数加 `ffmpeg_i:-copyts`（保住源时间戳） |
| `media_anchor.py` | 新增 `set_exact_offset(provider)`；`offset` 优先用它；`window_offset`/`spread` 改为按窗口自身样本数判定（`ready` 语义变了，否则空窗口取中位数会抛异常） |
| `server.py` | `_source_clock_offset()` 提供该值并带全部守卫；替换原先那条依据错误的注释 |
| `subtitle_pipeline.py` | status 暴露 `mediaAnchor.exactOffset`，会话是否静默回退可见 |

采样窗口**保留不删**：它是不可信时的回退，并继续提供 `drift`/`spread` 诊断。

## 验证链

每一环都有脚本，且都带自检。

### 1. 源时钟共享（`source-clock.py`、`read-clock.py`）
两条 rendition 差 +0.008 / −0.013 / −0.008 s，跨窗口离散 21 ms。ffmpeg 的 HLS/ADTS 解复用器**早已读到**它（input `start_time = 14665.073s`）。音频 rendition 自身没有传输层时间戳，绝对时间在 ID3 `PRIV` 帧里（`com.apple.streaming.transportStreamTimestamp`）。

### 2. 抹掉它的是 mpegts 封装器的默认输出偏移（`copyts-isolate.py`、`ytdlp-copyts.py`）
| 变体 | 音频腿首个 PES PTS |
|---|---|
| 无参数（= 改动前的应用） | 1.400 被重定基 |
| `--downloader-args "ffmpeg_i:-copyts"` | 15886.403 绝对 |
| `--downloader-args "ffmpeg:-copyts"` | 15901.403 绝对 |

重复的 `--downloader-args` 对同一 downloader key 是 **CONCATENATE 而非 REPLACE**（`copyts-merge.py`：reconnect 串与 `-copyts` 分两条、合一条，都拿到绝对 PTS），所以新增一条参数不会悄悄丢掉重连加固。

### 3. 打包级不受影响（`packaging-pts.py`）
喂 15900 s 绝对输入与喂 1.4 s 基线，fMP4 的 tfdt **逐字节相同**（两条轨都是 `{1:0,2:0}` / `{1:90000,2:48128}` / `{1:180000,2:96256}`），ffprobe `start_time` 都是 0.000000/1.000000/2.000000，`program_date_time` 两组都恰好是"墙钟 + 1.085 s"——**PDT 与绝对 PTS 无关**，渲染器时钟 `pdtEpoch + video.currentTime` 不受影响。

### 4. 原点稳定性（`source-clock-live.py`，600 s 真实直播）
```
media/video  ptsFirst distinct: 1   27881.400
media/audio  ptsFirst distinct: 1   27881.413778
asr-audio    ptsFirst distinct: 1   27886.406067
A0 − V0      n=596   min=+5.006  p50=+5.006  max=+5.006  range 0.000
Am0 − V0     n=596   min=+0.014  p50=+0.014  max=+0.014  range 0.000
```
`Am0 − V0 ≈ 0` 说明媒体腿自己的两个泵从同一分片边界起播，所以 `V0` 是 `privateMediaSeconds` 的正确零点。

独立旁证：两条腿**最新收到**的 PTS 之差 `A_last − V_last` 中位 −0.125 s。若两条腿各有各的时钟，这个值会是一个任意大常数；它接近 0，说明两串 PTS 是同一时钟上可直接比较的绝对位置。

### 5. 内容级证明 —— 它是**真的**，不只是**稳的**（`content-lag-proof.py`）
稳定性不等于正确性：任何一侧原点的固定记账错误同样稳。两条腿下的是同一条 rendition，所以把它们的解码音频做互相关，就能**不依赖任何时间元数据**测出真实内容延迟：

| 变体 | PES 原点预测 | 波形相关实测 | 峰值/底噪 |
|---|---|---|---|
| 同 rendition、同时启动 | +0.000 s | **−0.000 s** | 427 |
| 媒体腿晚 12 秒启动 | −15.023 s | **−15.023 s** | 221 |
| 应用真实选择器 | +5.016 s | **+5.016 s** | 365 |

三次吻合到 **0.000 秒**。

### 6. 应用层验收（`source-clock-live.py` + `cue-closure.py`，480 s 直播）
```
exactOffset  distinct=1   5.008
offset       distinct=1   5.008   range=0.000s     <- 实际生效的映射值
Cwin（旧路径）5.07 -> 0.15 一路漂移（旧代码正是靠它）
pdtEpoch     distinct=1
127 条 cue 全部落在已封装时间轴内
```
`offset` 从 t=3.0 s 起就等于 `exactOffset` 并保持恒定，比等窗口收敛更早可用。

## 守卫（全部只回退，不给错值）

`_source_clock_offset()` 在以下情况返回 `None`，锚点回退到采样窗口（它会自行重新收敛）：

1. **某条腿还没有 PTS。**
2. **某条腿的首个 PTS 落在 mpegts 封装器的重定基原点**（视频 1.400 / 音频 1.3787 = 早一个 AAC 帧）。此时相减得 ≈0，是**自信地错**。
   > 这里我第一版写错了：判据写成"小时量级"。绝对时钟**不等于**小时——一条刚开播几分钟的流合法地报几百秒。在刚开播的 ANNnewsCH 上实测到 216 s，且两次相隔 50 s 的启动分别报 216.4 / 266.5（跟着墙钟走），证明它确实是绝对、且 `-copyts` 生效。按量级判会把**所有新流**的精确偏移悄悄关掉。判据改为识别封装器那个常量。
3. **两原点相距超过 600 s**（`SOURCE_CLOCK_MAX_LEG_SKEW`）。源时钟是 90 kHz 下 33 bit，每 **26.5 小时**回绕一次；回绕正好落在两条腿首个包之间时，一条报 ~95443 s、另一条报 ~0，两个都"绝对"、都过守卫，相减会错 26.5 小时。实测合法偏差：0.014 / 5.006 / 5.016 / 15.023 s，600 s 的界不会误伤。
4. **会话中途某条腿重定基。** `_pcm_offset` 跨解码器重启是单调的（不随之重置），所以新的相减不再描述 pcm 0 落在打包时间轴的哪里。首次合法的配对会被**闩存**，之后不匹配即拒绝。

## 已知边界

- **会话中途的腿重启会退化为采样锚点**，本次 600 s 运行未触发（0 次重定基、0 次状态错误）。要做到跨重启仍精确，需要把 `(pcm 插入点, 新原点)` 成对事件化捕获——那是额外机制，未做。退化的方向是安全的（回到今天的行为），不是错的。
- **非 HLS / 混流平台**（bilibili、twitch）不建音频腿，`media_anchor` 为 `None`，不涉及本改动。
- `main` 未改动；改动在 `wip/subtitle-anchor-correction`。

## 复现

```
python .scratch/laglingo-audit/subtitle-lead/packaging-pts.py                        # 风险点 A/B
python .scratch/laglingo-audit/subtitle-lead/copyts-merge.py                         # 参数合并
python .scratch/laglingo-audit/subtitle-lead/content-lag-proof.py <live-url> 45      # 内容级证明
python .scratch/laglingo-audit/subtitle-lead/source-clock-live.py <live-url> 600     # 应用层
python .scratch/laglingo-audit/subtitle-lead/source-clock-verdict.py                 # 判定
python .scratch/laglingo-audit/subtitle-lead/cue-closure.py                          # cue 闭包
```
长跑必须配独立看门狗：`python .scratch/laglingo-audit/watchdog.py --deadline-epoch <epoch> --pids-file <pids> --driver-pid <pid>`（只杀显式 PID 树，拒绝命令行匹配）。

测试：`py -3.10 scripts/run-hls-tests.py` → **537 passed**（新增 13）。
