# LagLingo 启动与对齐原型实验（2026-09-09）

## 结论

1. 本地 TCP pump 的 `BufferedReader.read(65536)` 会等待大块数据。在一个保持生产进程运行、只先写入 1316 字节的真实子进程实验中，旧路径在 750 ms 内没有转发任何字节；使用 `read1(65536)` 后三轮均在 46–63 ms 转发。
2. 真实 FFmpeg MPEG-TS → TCP pump → PCM 实验中，改用 `read1` 并将已知 MPEG-TS 输入的 `analyzeduration/probesize` 降到 `500000/32768`，首个 PCM 约 0.484–0.485 秒；同一素材正常探测约 4.875 秒。PCM 总字节数和 SHA-256 完全一致，decoder 与 producer 都正常退出。
3. “源 PDT + 固定 offset”不能单独解决跳片后的对齐。测试素材在源时间 3 秒处插入 6 秒空洞，项目现有 `_VIDEO_SETTS` 封装会压缩该空洞；一次固定 offset 的跳片前最大误差为 0 秒，跳片后为 6 秒。
4. 当前运行实例的 `/api/status` 曾显示 `mediaAnchor.spread=9.95` 秒、`drift=-9.65` 秒，因此 handoff 中的 1–2 秒抖动不能视为当前运行时保证。

## 已落地的最小改动

- `_TcpPump` 优先调用 `read1`，普通 reader 仍回退到 `read`。
- 音频腿字幕 ffmpeg 使用有限的 MPEG-TS 探测窗口。
- 媒体腿和 ASR 音频腿在启动阶段并发 spawn；异常路径显式停止已启动的音频腿。
- 增加了 pump 的 `read1` 回归测试。

## 未落地的假设

没有把源 PDT/分片身份方案接入生产。HLS 规范也明确指出不同 Variant/Rendition 的相同 Media Sequence 不能直接证明内容匹配；而本项目还会重建时间线。因此它需要实际源分片级关联或内容/PTS 证据，不能仅凭一次 offset 冒险替换 MediaAnchor。

## 真实直播 PTS 验证

使用当前运行实例的同一鉴权上下文，对 Weather News 直播做了两次短抓取。第二次抓取在视频 format `269` 和音频 format `234` 上得到：

- 视频 PTS：`9883.600` 到 `9895.600`
- 音频 PTS：`9882.605256` 到 `9894.842178`
- 首包差：约 `0.994744` 秒
- 两条腿均连续产生约 12 秒媒体
- 两类 HLS manifest 都带 `PROGRAM-DATE-TIME`；本次清单首 PDT 相差约 1 秒

第一次抓取得到的首包差约为 `-1.000` 秒。两次结果说明源 PTS/PDT 具备可用的共同时间关系，独立腿不是天然无法对齐。

但视频腿在保留原始 PTS 直接重新封装时出现了非单调时间戳，FFmpeg 报 `Error submitting a packet to the muxer: Invalid argument`。这证明生产实现必须保留“源 PTS → 私有 HLS 连续时间”的分段映射，不能只给字幕加一个固定 offset。这个映射尚未接入生产。

## 验证

- `test_ytdlp_ingest.py`: 16 passed
- `test_media_anchor.py`: 9 passed
- `test_subtitle_pipeline.py`: 72 passed
- `test_server_providers.py`: 27 passed
- 三个模块 `py_compile`: passed
- decoder A/B: output bytes and SHA-256 identical
- source PTS prototype: real captured audio TS parsed in 80 PES samples; piecewise gap mapping tests 2 passed

当前运行中的 companion 未重启，因此新代码尚未进入那个正在播放的进程；磁盘代码已经完成并通过上述验证。

## Piecewise subtitle-to-video timing and performance (2026-09-09)

The real captured TS files were parsed with `MpegTsPtsProbe`. Video first PTS was 9883.600000s and audio first PTS was 9882.605256s, giving a measured initial delta of 0.994744s. After applying that delta, each audio PTS was compared with the nearest video PTS: median error 0.008522s, p95 error 0.016244s, and max error 0.190478s across 80 audio samples. The max is packet cadence quantization; it does not represent a multi-second timeline drift.

A six-second synthetic source gap was then mapped with `PiecewiseTimeline`: source [1000,1003] maps to private [0,3], and source [1009,+inf] maps to private [3,+inf]. The mapping keeps the private playback clock continuous: source 1009.25 maps to private 3.25 and source 1012 maps to private 6.0.

The hot-path prototype performed 2,000,000 mappings in 0.356s (~5.62M mappings/s) on this machine, with no wait, sleep, network operation, or buffering. This is a benchmark of the mapping operation only; it is not a claim about total startup time.

Conclusion: source-PTS plus piecewise source-to-private mapping can place subtitle timestamps on the same private HLS timeline without adding startup delay. Production still needs the mapper wired into audio PTS extraction and HLS segment metadata; the current `MediaAnchor` path remains an approximation.
