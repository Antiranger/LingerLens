# 平台与取流方式延迟对比（2026-09-12）

## 结论先说

这次用同一条带有“新加坡当前时间”大字的 YouTube 直播做了可重复的墙钟实验，并用 B 站公开直播的画面时钟和 HLS 时间戳做了同类探测。

| 渠道 / 方式 | 本轮实测结果 | 证据强度 |
|---|---:|---|
| YouTube 官方网页播放器 | 约 **28 秒**（一次成功的“跳到直播边缘”截图） | 中：浏览器随后出现播放器错误，未能做完整长窗口 |
| YouTube 直接读取 HLS playlist | 最新分片起点中位 **21.826 秒**，范围 **19.461–24.197 秒**；最新已完成分片末端中位 **16.826 秒** | 高：18 次、每 2 秒、同一个 format 232 |
| YouTube：yt-dlp 提取 URL + FFmpeg 播放 | 3 次画面时钟约 **20–23 秒** | 中高：每次 yt-dlp 提取成功、FFmpeg 解码成功，画面带绝对墙钟 |
| LagLingo 隔离实例（yt-dlp 取流，目标总延迟 15 秒） | 画面实测 **36–37 秒** | 高：同一 URL、独立端口、浏览器真实播放，3 张连续截图 |
| B 站官方网页播放器 | **未测得** | 低：官方页面被交互式人机验证挡住；没有绕过 |
| B 站 HLS（公开 API 返回的 `http_hls`） | 最新分片起点中位 **8.926 秒**，范围 **6.537–11.734 秒**；分片末端中位 **3.374 秒** | 高：18 次、每 2 秒、playlist 带 `PROGRAM-DATE-TIME` |
| B 站：yt-dlp `source-0`（FLV）+ FFmpeg | 画面时钟约 **6–12 秒**（单帧启动测量） | 中：3 次提取/解码成功；FLV 没有可直接换算墙钟的 playlist PDT |

## YouTube：官方播放器、直接 HLS、yt-dlp、LagLingo

### 测试源

测试源是 [YouTube SGT 时钟直播](https://www.youtube.com/watch?v=i49vZLWd9mk)。视频画面本身显示新加坡当前时间（GMT+8），所以每次截图都可以用“电脑截图时间 − 画面时钟”得到绝对画面延迟，不依赖播放器进度条或 PTS 的任意起点。

### 官方网页播放器

在官方 `youtube.com/watch` 页面点击“跳到直播边缘”后得到的有效截图：

- 截图文件时间：`2026-09-12 02:55:13`（SGT）
- 画面时钟：`02:54:45`
- 绝对画面延迟：约 **28.3 秒**

这是一条成功播放的单点证据，不应被写成官方播放器的稳定 P50。后续新浏览器会话曾先从约 60 秒旧画面开始，随后显示“出了点问题，请刷新或稍后重试”，因此本轮没有把失败会话的旧画面混入统计。

证据截图：

- [官方 YouTube 有效截图](/F:/Projects/LagLingo/.playwright-cli/page-2026-09-11T18-55-13-304Z.png)

### 直接 HLS playlist

用 yt-dlp 只做一次 format 提取，然后对 format `232` 的 HLS manifest 每 2 秒取样 18 次。YouTube 该 playlist 只在首个列出的分片给出 `PROGRAM-DATE-TIME`，脚本用该时间加上所有 `EXTINF` 时长推导最新分片起点和末端。

- 最新分片**起点**延迟：中位 **21.826 秒**，最小 **19.461 秒**，最大 **24.197 秒**。
- 最新已完成分片**末端**延迟：中位 **16.826 秒**。
- media sequence 从 `2968960` 增长到 `2968971`，说明 playlist 在持续推进。
- HLS target duration 为 **5 秒**。

这说明“源端 HLS 落后约 20–25 秒”在这条直播上可以重复观察到，并不是某一次下载卡了几分钟。它是直播源编码、分片完成、CDN 发布安全窗口的总和。

脚本和原始摘要：[youtube_hls_compare.py](/F:/Projects/LagLingo/.scratch/youtube_hls_compare.py:1)

### yt-dlp 实际取流路径

先让 yt-dlp 提取同一个 HLS URL，再由 FFmpeg 从直播边缘解码一帧，画面时钟读数如下：

| FFmpeg 完成时间（SGT） | 画面时钟 | 约延迟 |
|---|---:|---:|
| 03:21:31 | 03:21:08 | 23 秒 |
| 03:21:38 | 03:21:18 | 20 秒 |
| 03:21:45 | 03:21:23 | 22 秒 |

3 次 yt-dlp 提取均返回成功，FFmpeg 均返回成功。这与 playlist 统计的 20–25 秒完全同量级，说明 yt-dlp 没有额外制造“几十秒”的取流延迟。

脚本和截图目录：[ytdlp_extract_frame_probe.py](/F:/Projects/LagLingo/.scratch/ytdlp_extract_frame_probe.py:1)、[ytdlp-clock-frame-20260912-032138.jpg](/F:/Projects/LagLingo/.scratch/ytdlp-clock-frame-20260912-032138.jpg)

另有一次直接运行 `yt-dlp -o -` 的短管道收到 0 字节，stderr 为 FFmpeg `Error opening input ... Error number -138`。这是该命令没有给 FFmpeg 显式代理/请求参数造成的打开失败，不是“下载速度为 0”的证据；不要把它解释成网络吞吐测量。真正按 LagLingo 的取流路径跑隔离实例时，媒体正常推进。

### LagLingo 隔离实例的真实画面

在端口 `39990` 启动了独立 Companion，使用同一个 YouTube URL、`targetDelaySeconds=15`、关闭字幕和直播聊天。状态在画面开始播放时显示：

- `targetDelaySeconds = 15`
- `hiddenMediaSeconds = 3`
- `privateMediaSeconds` 已正常增长
- yt-dlp 版本为 vendored `2026.08.19`

浏览器真实播放截图：

| 截图时间（文件名 UTC，换算为 SGT） | 画面时钟 | 画面实测延迟 |
|---|---:|---:|
| 03:30:59 | 03:30:22 | 37 秒 |
| 03:31:28 | 03:30:51 | 37 秒 |
| 03:31:41 | 03:31:05 | 36 秒 |

这组数据直接回答了“15 秒是不是还要再加到源端 20 多秒”：在当前实现和当前源上，答案是**会叠加到约 36–37 秒**。近似关系是：

```text
现场真实时间 → YouTube 源 HLS：约 22 秒
YouTube 源 HLS → LagLingo 本地目标：约 15 秒
现场真实时间 → LagLingo 画面：约 22 + 15 = 37 秒
```

这不是网络卡顿；隔离 bench 的媒体推进率为 **0.960×**（50 秒窗口，短于 1.0 的原因是启动/分片量化），没有报错；同一窗口的 source idle P50 **2.3 秒**、最大 **5.0 秒**。此前约 100 秒真实窗口测得媒体推进 **1.008×**、无持续停顿。也就是说，主要问题是**源边缘已经旧了 + 我们又按 15 秒目标在源边缘后面保留了播放器安全距离**，不是本机下载速度把画面拖成 37 秒。

证据截图：[laglingo-clock-frame](/F:/Projects/LagLingo/.playwright-cli/page-2026-09-11T19-31-41-406Z.png)

## B 站：官方页面、HLS、yt-dlp/FLV

测试房间是 [B 站公开直播房间 1890957747](https://live.bilibili.com/1890957747)，画面左上角自带当前新加坡时间，适合做墙钟测量。

### 官方 B 站页面

直接打开官方直播页时出现“请在下图依次点击……”的人机验证，播放器无法进入可测状态。没有绕过验证，所以官方 B 站画面延迟本轮标记为**未测得**，不能用 HLS 或 FLV 数字代替。

### B 站 HLS

通过公开 `getRoomPlayInfo` API 取得 HLS URL，playlist 含逐分片 `PROGRAM-DATE-TIME`。18 次采样、每 2 秒：

- 最新分片起点中位 **8.926 秒**，范围 **6.537–11.734 秒**。
- 最新分片末端中位 **3.374 秒**，范围 **0.537–7.293 秒**。
- target duration **7 秒**。

所以这条 B 站源的 HLS 发布边缘明显比本次 YouTube 源新，不能把 YouTube 的 20–25 秒直接套到 B 站。

脚本：[bilibili_hls_compare.py](/F:/Projects/LagLingo/.scratch/bilibili_hls_compare.py:1)

### B 站 yt-dlp

对同一房间运行 vendored yt-dlp，返回 `is_live=true`、extractor `BiliLive`，只返回两个 `https` 的 `flv` format（`source-0`、`source-1`），没有 HLS format。用 `source-0` 提取 URL 并把 yt-dlp 返回的请求头交给 FFmpeg，三次单帧结果约为 **6–12 秒**。

这只能作为方向性比较：FLV 本身没有 HLS playlist 那样的墙钟 PDT，单帧还会受首个关键帧和连接启动时间影响。因此本轮可以确认“yt-dlp 在 B 站拿到的是 FLV，画面大致在 6–12 秒量级”，但不能据此承诺 FLV 永远比 HLS 快固定几秒。

脚本：[bilibili_ytdlp_frame_probe.py](/F:/Projects/LagLingo/.scratch/bilibili_ytdlp_frame_probe.py:1)

## 对现有实现的含义

1. **YouTube 的 20–25 秒主要来自源端 HLS，不是 yt-dlp 下载变慢。** 官方播放器单点约 28 秒，直接 HLS 约 22 秒，yt-dlp+FFmpeg 约 20–23 秒；三者同一个量级。
2. **LagLingo 当前“目标 15 秒”不是绝对现场延迟。** 当前代码把 15 秒拆成服务端约 3 秒隐藏 + 浏览器约 12 秒 live-sync；它作用在已发布的源边缘之后。对于本次 YouTube，现场到 LagLingo 的实测是 36–37 秒。
3. **B 站这条源明显更接近直播边缘。** HLS 最新分片起点约 9 秒，yt-dlp 返回的 FLV 单帧约 6–12 秒；因此不能因为 YouTube HLS 是 22 秒，就认为所有平台都是 22 秒。
4. **当前 B 站 Auto 选择逻辑会优先带 `· HLS` 的选项。** 如果产品目标是绝对现场延迟，应把“源端延迟”和“本地播放器缓冲”分开展示，并在同一房间做持续 FLV/HLS 对照后再决定是否改变默认传输；本轮 FLV 的墙钟测量还不足以支持无条件切换。

## 复现与清理

本轮没有停止或修改用户正在运行的 Electron 实例；只使用了独立 `39990` Companion、临时 Playwright 会话和 `.scratch` 诊断文件。实验结束时应停止隔离 Companion，并关闭临时浏览器会话。
