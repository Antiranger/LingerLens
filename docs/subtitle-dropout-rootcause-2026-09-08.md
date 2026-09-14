# 字幕长时间掉线根因分析（2026-09-08，xKtKV9wSPQk）

> 症状：观看 YouTube 直播（マリオ40周年 Direct 同時視聴，日语）期间，字幕两次出现 1 分钟以上的完全中断；视频播放基本正常。用户怀疑"延时不够"。
> 结论：**不是延时不够。是源下载停滞约 74 秒，而字幕腿完全不在延迟缓冲的保护区内；恢复后的回填音频又在 ASR 重连窗口被静默丢弃，造成永久的 73.7 秒 cue 时间轴空洞。**
> 证据快照：`output/diagnostics-20260908/`（status-snap.json / subs-snap.json / subs2.json / status-now.json）。

---

## 1. 会话配置（实测，status-snap.json）

- 画质：`1080p60-avc1-muxed-301`，**muxed 单腿**，估计码率 ~7.2 Mbps。
- 目标延迟：用户已调到 **42 秒**（`targetDelaySeconds: 42`，hiddenMediaSeconds 30）——"加延时"已经做过了，视频因此得救，字幕没有。
- ASR：Soniox `stt-rt-v5`（适配器 `companion/providers/asr_soniox_realtime.py`）。
- 翻译：bailian / OpenAI 兼容网关。

## 2. 第二次掉线（22:11–22:14）的完整证据链

| 证据 | 数值 | 含义 |
|---|---|---|
| cue 时间轴空洞 | 最后一条 cue tEnd=媒体 1158.0s，下一条 tStart=媒体 1231.6s，**空洞 73.7s** | 这 74 秒的媒体内容没有产生任何 cue |
| `wall − pdtEpoch − privateMediaSeconds` | 停滞后 **+76s**，之后 15 分钟从 +76 缓慢收敛到 −15 | 媒体管线停滞 ~74s（墙钟照走、媒体不走）；随后以 ~1.07–1.16x **缓慢回填**（非跳片） |
| `pcmOffset ≈ privateMediaSeconds`，`pcmDropped = 0` | 全程成立 | 字幕腿解码了全部回填音频，PCM 队列无溢出丢弃 |
| `asrReconnects = 1` | 全程恰好 1 次 | 一次 ASR 会话中断 + 立即重连成功 |
| VOD 音频能量（2580–2940s 逐秒 RMS） | 空洞窗口 −20~−30dB，与周围 cue 密集时段一致 | 主播在说话，**排除"源本身静音"**（用户亦现场确认） |

### 机制链

1. **触发**：~22:11，yt-dlp 对 7.2 Mbps muxed 1080p60 的下载停滞约 74 秒。项目自己的代码注释已记录：单连接 googlevideo 实测上限 ~3.2 Mbps（`ytdlp_ingest.py` command() 注释），高码率 muxed rendition 在吞吐抖动时无法跟上直播边缘。`--concurrent-fragments 4` 只在落后时加速回填，不能防止停滞。
2. **字幕立即全灭**：字幕腿 ffmpeg 读的是**私有 HLS 播放列表**（`server.py:685` → `pipeline.start(self._private_hls_url(), ...)`），位于延迟发布器**之前**。源一停，封装 ffmpeg 停产 → 私有播放列表不增长 → 字幕腿零音频 → ASR 零事件 → 零 cue。**42 秒延迟只保护播放器**（发布窗口 30s + hls.js 缓冲 ≈ 33–60s 跑道），字幕腿的停滞容忍度是**零**。
3. **恢复后空洞不补**（关键）：停滞 ~74s 后 ASR 会话断了一次（asrReconnects=1，同一网络事件或 Soniox 空闲断开）。按设计铁律"重连期间丢弃音频、绝不补时间戳"，`subtitle_pipeline.py:_pcm_sender` 在 `_stream is None` 时**静默丢弃 PCM（无计数）**。回填的 74 秒音频被字幕腿 ffmpeg 以 CPU 速度（25 倍速以上）解码，恰好在重连窗口内全部冲过 sender 被丢弃 → cue 时间轴上留下与停滞区间精确对应的永久空洞。
   - 备选机制（同一修复方向）：Soniox 服务端对 25 倍速涌入的音频静默丢弃。两者都与全部计数器一致；客户端丢无从计数，服务端丢无从观测——见 §4 观测性缺口。
4. **第一次掉线**：留存数据无法确证（cue 存储 120s 滚动已裁剪；wall−media 总账只有 ~76s ≈ 一次大停滞，故第一次不是同级源停滞）。候选：同家族较小事件、或早期翻译失败簇（16 次 TimeoutError——但失败会降级显示原文，不会全灭）。**如实记录为未确证**。

## 3. 修复方向（按性价比）

### A. 源侧：消除停滞（最有效）
- 高码率直播**优先选分离 v+a 双腿格式**：`ytdlp_ingest.py` 模块 docstring 明确记录双腿方案"measured: zero mid-stream skips"。muxed 301（7.2 Mbps）是本次最脆弱的选择。
- 或在画质选择 UI 上对"单腿高码率 muxed"标注掉线风险。

### B. 字幕腿：给它停滞容忍度（结构性）
- 字幕腿改读**发布后**的播放列表（或私有+发布拼接的保留窗口），让 ASR 与播放器共享同一份缓冲：停滞 ≤ 延迟时对字幕完全隐形。
- 代价：字幕就绪时间整体后移延迟量——但这正是本产品"延迟即计算预算"的立论，方向一致。

### C. 回填路径：消灭 25 倍速突发 + 丢弃可见
- `_pcm_sender` 按 ~1.0–1.2x 节流发送（或重连期间暂停字幕腿 ffmpeg、会话恢复后再消费积压），避免回填突发冲垮重连窗口/服务端。
- `_stream is None` 丢弃必须计数上报（现静默）；`asrReconnects` 需区分"会话结束"与"重连失败次数"。

### D. 观测性（让下次事故一行读出）
- `/api/status` 暴露 `sourceIngest.logTail`（yt-dlp stderr 已在采集 `ytdlp_ingest.py:log_tail`，含 "skipping N segments" / fragment retries，只是没接出来）。
- 暴露 `sourceStallSeconds = wall − pdtEpoch − privateMediaSeconds`（本次就是用它一眼定位的）。
- 暴露 caption 腿与封装腿的位置差（`priv − pcmOffset`）。

### E. 对用户当前配置的建议
- 42 秒延迟对字幕**没有任何帮助**（字幕腿在延迟之前），可以降回舒适值；它只增加观看滞后。
- 观看高码率 1080p60 直播时，优先选分离格式或 720p，比加延时有效得多。

## 4. 快速复现/验证命令

```powershell
# 停滞探测器（会话运行中）：>5s 持续增长即为源停滞
curl -s http://127.0.0.1:8765/api/status | python -c "import json,sys,time; d=json.load(sys.stdin); print(time.time()-d['pdtEpoch']-d['privateMediaSeconds'])"

# cue 时间轴空洞扫描
curl -s http://127.0.0.1:8765/api/subtitles | python -c "import json,sys; cs=sorted(json.load(sys.stdin)['cues'],key=lambda c:c.get('tStart') or 0); [print('GAP %.1fs @ %.1f'%(c['tStart']-p,p)) for c,p in zip(cs[1:],[x['tEnd'] for x in cs[:-1]]) if c.get('tStart') and p and c['tStart']-p>10]"
```
