# 性能卡顿（鼠标不跟手）run4 分析结论 — 2026-09-09

> 数据：`.scratch/perf-monitor/run4.jsonl`（674 样本 / 30.0 min）、`run4-gpu.csv`（PDH GPU Engine 计数器）、`watch4.jsonl`（有头浏览器逐 10s 播放器遥测，30.9 min）。
> 对照：run1/run2（部分数据、与 run3 时间重叠）、run3（无效——会话 57s 死亡后跑了 30 分钟尸体）。
> 背景：监测期间会话为 1080p60 muxed（format 301），正处于 P1 慢性掉队状态。

## TL;DR

1. **视频解码全程软解**：两块 GPU（Intel luid 0x22F03 的 `Video Codec 0`、NVIDIA 的 `VideoDecode`/`Video Codec` 全部 11+61 个实例）在 30 分钟内 avg=0.0 / max=0.0，而 watch 浏览器确实在解码播放（62259 帧）。1080p60 AVC 软解是浏览器 CPU 的主来源。
2. **DWM 压力与浏览器视频合成强相关**：corr(browser_cpu, dwm_cpu)=**0.73**；dwm avg 22.4% / P95 55.3% / max 63.9%。DWM 高占用是鼠标不跟手的经典强相关项。
3. **不是全局 CPU 耗尽**：sys CPU avg 25.8% / P95 35%。是局部热点（浏览器 ~2.3 核 + DWM 突发）拉低输入响应。
4. **run3 的 11.7GB browser RSS 不是播放器泄漏**：monitor 把系统里所有 chrome/msedge/firefox/electron 聚合为 "browser"（88–96 个进程）。run4 同一播放器在跑时聚合 RSS 只有 8.7GB，run1/run2 反而 11.5–11.8GB——该指标被用户自己的浏览器主导，不能归因于 LagLingo 播放器。
5. **断流防护实战有效**：run4 仅 2 次 >5s 断流（6.8s / 5.3s，t+1056 与 t+1597），watch4 在 t+1051/t+1601 出现对应横幅与自动暂停（2 个 paused 样本）并自动恢复——字幕掉线修复 + stall overlay 行为符合设计。对照修复前的 run1/run2：312/455 个样本 >5s 断流、max 424s/1213s。

## 播放器侧（watch4）

- 播放速率 P10/P50/P90 = **0.40/0.55/0.71x**——全程被 P1 供料不足拖住（单腿 301 实测 ~0.55x），bufferAhead 常态 0.5–2.3s，min 0.0s。
- 丢帧 45/62259 = **0.072%**，软解本身跟得上（在 0.55x 供料下）。
- 注意：0.55x 供料意味着软解负载只有满速的 ~55%；若 P1 修复后满速 1080p60 软解，browser CPU P95 228%（2.3 核）还会近乎翻倍——**软解问题必须在满速工况下重新评估**。

## 浏览器 CPU / RSS 轨迹（run4）

| t+ | browser RSS | pids | browser CPU | dwm | sys |
|---|---|---|---|---|---|
| 0s | 6803MB | 91 | 97.9 | 37.5 | 31.8 |
| 399s | 7195MB | 88 | 81.9 | 20.8 | 23.2 |
| 798s | 7123MB | 88 | 63.0 | 17.5 | 21.2 |
| 1199s | 8051MB | 96 | 73.7 | 10.6 | 25.4 |
| 1602s | 8254MB | 88 | 63.8 | 1.1 | 20.5 |

RSS 30 分钟 +1.4GB 但进程数在 88–96 间波动（用户自己的浏览器活动），无法归因；DWM 随时间下降（后期 watch 窗口可能被遮挡/用户活动减少）。

## 结论的边界（诚实声明）

- watch_player 用 Playwright 捆绑 Chromium（非用户日常浏览器）。该浏览器走了纯软解；用户真实浏览器（Chrome/Edge 正式版）通常能对 avc1 走硬解——**用户机器上是否也软解，本次数据无法判定**。验证方法：用户真实浏览器开 `chrome://media-internals` 播放时看 `kVideoDecoderName`（`MojoVideoDecoder`/`D3D11VideoDecoder` = 硬解，`FFmpegVideoDecoder` = 软解）。
- "browser" 角色聚合了用户全部浏览器，RSS/CPU 不能单独归因到 LagLingo 播放器进程。
- run4 供料只有 0.55x，所有 decode/compositing 负载数字是**下限**。

## 行动项

1. **P1 优先**：供料修到 1.0x（分离 v+a 双腿）是后续一切性能测量的前提，否则播放器永远处于欠载工况。
2. **硬解验证**：让用户在真实浏览器播放时查 media-internals；若也软解，优先排查浏览器 GPU 设置，比改代码收益大。
3. **monitor.py 改进（下次实验）**：给 watch_player 的 Chromium 单独打标（如按 `--user-data-dir` 命令行匹配 PID 树），把 "watch-browser" 与 "user-browser" 分开统计；采集每进程 RSS Top-5。
4. **画质默认策略**：在硬解确认前，UI 推荐档应考虑 720p60/1080p30 以降低软解压强（与 P1 的 UI 改动同处）。
