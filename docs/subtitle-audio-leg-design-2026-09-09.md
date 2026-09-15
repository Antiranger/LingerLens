# 字幕腿方案 B：ASR 专用独立 audio-only 下载腿 — 设计与对齐数学

> 日期：2026-09-09。决策：用户拍板要根治方案（B），放弃小改方案 C（字幕腿改读发布后播放列表）。
> 前置：`docs/subtitle-dropout-rootcause-2026-09-08.md` §3、P2 结论 `docs/perf-mouse-lag-analysis-2026-09-09.md`。

## 问题

字幕腿读**私有 HLS**（视频封装链路的产物）。视频下载腿任何停滞/跳片都直接饿死 ASR——字幕腿对断流零容忍，而视频腿恰恰是最易断的那个（高码率、单连接天花板）。run1/run2 实测：312/455 个样本断流 >5s。

## 方案

**ASR 走自己的 yt-dlp audio-only 腿**（~0.05–0.13 Mbps），与视频腿完全独立：

```
yt-dlp (video+audio legs) → TCP pumps → packaging ffmpeg → 私有 HLS → 播放器   （不变）
yt-dlp (audio-only leg)   → TCP pump  → caption ffmpeg → PCM → ASR → 字幕      （新）
```

- 音频腿码率不到视频腿的 2%，单连接天花板（~3.2 Mbps）对它毫无约束——视频腿停滞时字幕腿继续呼吸。
- 字幕 ffmpeg 的输入从"私有 HLS URL"改为"音频腿 TCP 端点（mpegts）"。
- 无 audio-only 格式的平台（Bilibili FLV 等）回退到现有私有 HLS 字幕腿，行为不变。

## 时间轴对齐（两条腿独立下载，字节原点不再共享）

redesign 文档 Fix A（共享字节原点）对独立腿不成立。改为**连续测量的媒体锚点**（Fix B 的连续版）：

- `V(t)` = 视频腿已封装的私有媒体秒（publisher 已有 `private_media_seconds`）。
- `A(t)` = 字幕腿已解码的 PCM 秒（pipeline 已有 `_pcm_offset`）。
- 采样 `C(t) = V(t) − A(t)`，每秒一次。
- cue 媒体位置 = `end_pcm + C`，`cue.tEnd = pdt_0 + end_pcm + C`。

**为什么对跳片/停滞正确**：跳片使 V 相对源时间少累计 skipped_v，音频腿少累计 skipped_a，两者之差自动进入 C 的采样值——持续跟踪（而非冻结）即可吸收。

**突发免疫**：只在两条腿都接近实时速率时采样（|ΔV/Δt − 1| ≤ 0.5 且 |ΔA/Δt − 1| ≤ 0.5），启动/恢复期的 25 倍速解码被拒绝。滚动窗口取**中位数**（默认 30 个有效样本）。

**残余误差**：采样时刻与说话时刻之间 (alag − vlag) 的变化量 + 分片量化 ≈ ±1s 上限，由用户偏移滑块终校。

**锚点未就绪前**（有效样本 < 3）：不产出 cue（`cuesHeldNoAnchor` 计数），避免一批 cue 落在错误时间轴上。锚点永不就绪会在 status 暴露（`mediaAnchor.samples==0`）。

## 遥测（status.subtitles 新增）

`mediaAnchor: {offset, samples, spread(IQR), drift(最新样本−中位数)}`；`captionSource: "audio-leg" | "private-hls"`。`sourceIngest` 列表追加音频腿快照（含 logTail/legThroughput）。

## 实测修正（2026-09-09 实弹验收）

1. **选择器必须自解析，不能绑定 probe 结果**：同一直播相邻两次 extraction 的 formats 列表会波动（233/234 时有时无）。音频腿改为通用选择器 `234/233/ba[protocol^=m3u8]/worst[protocol^=m3u8]` 自行再抽取；最后一级兜底是 144p muxed（~0.3 Mbps），因为**该流实测连 233/234 都可能整个不存在**。
2. **锚点采样节奏 2.5s**（原设计 1s）：视频腿 privateMediaSeconds 按 1s 分片量子化推进，1s 采样下 ΔV∈{0,1,2,3} 几乎全被速率门拒绝（实测 samples=0 永不就绪）。2.5s 节奏把量化平滑进 [0.4,1.6] 通带；stall(0x) 与 burst(25x) 仍被拒绝。容差 0.5→0.6。
3. **验收数据**（720p60 muxed + 音频腿，ja→zh-Hans，~4 分钟）：audio-leg ~0.14 Mbps 独立运行；锚点 10 样本收敛 offset=13.7s（音频腿起播位置差）；cue media_pos 与封装边缘差 <2s；pcmDropped=0；asrReconnects=0；视频腿 wall−pdt−priv=+4s（对照修复前 muxed 301 的 +2582s）。
5. **P1 根因深挖（同日）**：当前 yt-dlp 对 `is_live` HLS **无条件委派给外部 ffmpeg 下载器**（`HlsFD.can_download` 硬拒绝 is_live），`--downloader m3u8:native`/`--concurrent-fragments` 对直播全部无效。真正的吞吐天花板是**走代理的 ffmpeg HLS demuxer**：代理破坏连接复用（"Cannot reuse HTTP connection for different host"），实测 1080p60 跌到 0.55x、720p60 跌到 0.8x。本机 googlevideo 可直连，`ytdlp_ingest.py` 已默认不再向 ffmpeg 转发代理（`LINGERLENS_FFMPEG_PROXY=1` 可恢复），并加了 `-reconnect` 系列保活。修复后实测：**1080p60 muxed 301 wall−pdt−priv 稳定在 −3s**（修复前同画质 +2582s）。
6. **跳片重锚定实弹命中**：~11 分钟时一次网络抖动导致视频腿跳片 ~35s，锚点跳变检测触发、窗口重置、~10s 内重收敛（offset 13.3→7.9，spread 1.35）；全程字幕 pcmDropped=0、asrReconnects=0——视频腿故障对字幕完全隐形，方案 B 的设计目标达成。
7. **并行启动（当日晚间修正）**：用户指出首开变慢。改为音频腿抽取/ASR 连接/锚点收敛与私有 HLS 等待**并行**，epoch 改为迟到绑定（`set_media_epoch`，pending cue 在 epoch 落地后 flush）。实测（674lr89Qfj4）：管线 t+13s 开始吃音频（原方案要等 ~25s），首 cue ~t+65s（其中 probe 抽取占 ~25s）。
8. **代理冤案平反（重要）**：晚场实测发现 "Cannot reuse HTTP connection for different host" 在**直连模式下依然存在**——真凶是 ffmpeg 9.0 HLS demuxer 对 googlevideo 每分片换主机名（rr4→rr8→rr11）不复用连接，与代理无关。白天 1080p60 直连 1.0x 是低负载窗口的侥幸；晚高峰 ffmpeg demuxer 只能跑 ~0.8x（1080p）/ ~1.01x（音频腿，码率小有余量）。**结论：媒体腿的根治需要换掉 ffmpeg HLS 下载路径**（自写并发 HLS 下载器，或 patch yt-dlp 放开 is_live 的 native 限制）——见任务计划 P5。音频腿因码率小不受影响，字幕独立性不受影响。
4. 已知残余：锚点 spread(IQR) ~2.0s 高于设计目标 1.0s，源于分片量化残余——单条 cue 可能 ±1s 抖动，由用户偏移滑块终校。

## 改动清单

| 文件 | 改动 |
|---|---|
| `companion/media_anchor.py` | **新增** MediaAnchor：速率门控采样、滚动中位数、spread/drift |
| `companion/subtitle_pipeline.py` | `start()` 接受 `input_format`（TCP 原始流需 `-f mpegts`）+ `anchor`/`anchor_probe`；新增 `_anchor_sampler` 任务；`_materialize_cue` 在音频腿模式下经锚点映射 |
| `companion/server.py` | `_pick_asr_audio_format()`（audio-only、优先 m3u8 协议、最低码率）；handle_start 起第二个 YtDlpLiveIngest（独立 auth consumer）；探针快照纳入 ASR 格式；status 接线 |
| `tests/test_media_anchor.py` | **新增** 单元黄金测试 |
| `tests/test_subtitle_pipeline.py` | 集成黄金测试：脚本化视频腿（含停滞+跳片）+ 桩 ASR → cue 误差 ≤300ms |

## 明确不做

- 不动播放器显示逻辑、不改 bilibili/twitch 路径、不实现发布端 VTT（redesign §7）。
- `_pcm_sender` 不节流（音频腿回填上限 = 直播窗口 ~15s @0.13Mbps，冲击有界；观测到问题再议）。
