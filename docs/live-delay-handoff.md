# LagLingo 直播延迟链路分析与交接文档

> 用途：把"端到端延迟从哪来、现在多少、怎么减"的全部已知事实交给新的分析对话。
> 所有数字均来自真实直播源实测，不是估算。日期：2026-08-29。
> 项目根目录：`F:/Projects/LagLingo`，本文涉及的原型在 `prototype/hls-companion/`。

---

## 1. 项目背景与目标

LagLingo 是一个"延迟跟读"语言学习工具的原型（Prototype 2，代号 hls-companion）。核心诉求：

- 把 YouTube/Bilibili 直播下载到本机，**稳定地**以落后直播边缘约 10 秒的方式播放（原目标 ~10s）。
- 媒体只在本机 127.0.0.1 流转；登录 Cookie 只通过 Native Messaging 命名管道传递，不走 HTTP。
- **硬性策略约束：不做静默转码**。只接受源就是 H.264/AAC 的格式，FFmpeg 一律流拷贝（`-c copy`）。需要转码的组合在 UI 标灰而不是偷偷转。

### 管线架构（每段的责任边界）

```
主播 → YouTube平台 → yt-dlp下载 → FFmpeg分片(CMAF/fMP4) → 延迟发布器 → localhost HTTP → 浏览器hls.js → 用户
        ①不可控      ②acquisition   ③stream-copy封装    ④产品主动延迟   ⑤静态文件     ⑤追逐/缓冲
```

| 组件 | 文件 | 职责 |
|---|---|---|
| yt-dlp 采集 | `companion/ytdlp_ingest.py` + `vendor/yt-dlp/yt-dlp.exe` (2026.08.19) | 拥有 YouTube 提取/下载/重试的完整职责；stdout 输出 muxed MPEG-TS |
| FFmpeg 封装 | `companion/core.py` `build_ffmpeg_command()` | 只做流拷贝封装：TS → CMAF/fMP4 HLS，私有目录输出 |
| 延迟发布器 | `companion/core.py` `DelayedPlaylistPublisher` | 把已完成分片按住 `publishDelaySeconds` 后公开；公开窗口 30 秒滚动 |
| HTTP 服务 | `companion/server.py` | 127.0.0.1 独占；静态播放器 + `/hls/*` + `/api/*` |
| 浏览器播放器 | `web-player/player.js` + `web-player/vendor/hls.min.js` (1.7.1) | hls.js MSE 播放，1s 轮询 `/api/status` 自动 attach |
| 扩展 | `prototype/hls-companion/extension/` | 仅 Native Messaging cookie 桥，不参与播放 |

### FFmpeg 关键参数（core.py:378-404）

- `-hls_time 1`（目标 1 秒分片，**但流拷贝只能在关键帧切，实际不生效**，见 §3.3）
- `-hls_list_size 45` + `-hls_delete_threshold 30` + `delete_segments`（私有目录磁盘有界）
- `-hls_segment_type fmp4`，init.mp4 绝对路径
- `-bsf:a aac_adtstoasc`（YouTube 的 ADTS AAC 进 fMP4 必须，无损；缺了会只产一个 0.033s 分片就退出）

---

## 2. 当前实测结论（一句话）

**在"流拷贝 + 绝不跳帧"约束下，端到端延迟实测约 18–29 秒，延迟本身稳定不漂移。**
原 10 秒目标在不转码的前提下不可达；压到 ~10s 只有转码强制关键帧一条路。

## 3. 延迟逐段分解（全部实测数字）

总延迟 = 主播此刻 → 用户屏幕此刻。测试源：Al Jazeera English 24/7 直播（`gCNeDWCI0vo`），1080p H.264 + AAC。

### ① YouTube 平台自身延迟（约 5–15s，不可控）

yt-dlp 拿到的"直播边缘"已经是 YouTube 的边缘，不是真实世界。主播推流、平台转码分发都要时间。这段由主播的延迟设置（普通/低延迟/超低延迟）决定，客户端无法影响。

### ② yt-dlp 下载 + 源安全缓冲（约 5s，保险余量）

- yt-dlp 按 YouTube 分片逐片下载，下完一片才有数据，天然落后一个分片级别。
- 项目额外故意保留 **5 秒源安全库存**，FFmpeg 加 `-re` 按媒体速率消费本地缓存。这是修 403/跨 host 冻结时加的保险：网络抖动、403 重试、CDN 换 host 时管线不会空转卡死。
- 历史教训（findings.md）：没有这个缓冲时，FFmpeg 9 会因 `Cannot reuse HTTP connection for different host` 在 26–38 秒后冻结。

### ③ FFmpeg 分片完成时间（0–5s，被源关键帧绑架）

- 配置 `-hls_time 1` 只是"愿望"；`-c copy` 不解码，**只能在关键帧处切分**。
- 实测该流关键帧间隔约 5 秒：公开播放列表 EXTINF 为 `5.0 / 3.0 / 1.0` 混合，`#EXT-X-TARGETDURATION: 5`。
- 一个分片必须等到下一个关键帧出现才能封口产出。分片时长 = 源 GOP，由 YouTube 编码器决定，客户端配置改不了。

### ④ 发布延迟（默认 2s，唯一主动设计的产品延迟）

- `DelayedPlaylistPublisher` 把已完成分片按住 2 秒再写进公开播放列表，吸收 ③ 的产出节奏不均。
- 播放器 UI 下拉可选 0/2/3/5 秒（`web-player/index.html` `#publishDelay`）。
- 公开窗口固定约 30 秒滚动，磁盘保留 30 个公开分片 + 6 个防竞态陈旧分片（已验证有界）。

### ⑤ 浏览器追逐距离（11–22s，当前最大头）

- hls.js 直播模式铁律：播放头必须坐在边缘后 N 个**完整分片**处，默认 N=3。
- 原因：播放 = 消费缓冲区。贴边坐（< 1 个分片）时，播到缓冲区尽头，下一个分片还没发布/下载完 → 缓冲区饿死 → 超过 `liveMaxLatencyDuration` 被强制 seek 到边缘 → 用户看到跳帧。
- 注意按**分片个数**而非秒计算：分片 1s 时 N=3 只落后 3s；本分片被 ③ 逼成 ~5s，N=3 就是 11–22s（实测 `lag` 在 11.15–21.97s 波动，因为分片时长不一）。
- 当前配置（修复后，`player.js`）：`liveSyncDurationCount: 3`，`liveMaxLatencyDurationCount: 8`，`maxBufferLength: 30`，`lowLatencyMode: false`。

### 汇总表

| 段 | 时长 | 性质 | 可否压缩 |
|---|---|---|---|
| ① 平台 | 5–15s | 外部 | 否 |
| ② 下载+安全缓冲 | ~5s | 保险余量 | 可，但削弱抗 403/抖动能力 |
| ③ 分片完成 | 0–5s | 源 GOP 决定 | 只有转码能改 |
| ④ 发布延迟 | 2s（可调 0） | 产品设计 | 可，零风险 |
| ⑤ 浏览器追逐 | 11–22s | = 3 × 分片时长 | 个数可降到 2；秒数只有转码能降 |

②③ 时间上有重叠不单加；端到端实测 **18–29s**（不含①，因为测量基准是公开播放列表边缘）。

---

## 4. 本次会话修复的两个浏览器播放 bug（延迟话题的直接前因）

这两个 bug 于 2026-08-29 修复，是项目**第一次真实浏览器端到端播放验证**（此前只验证到 API/分片生成层，progress.md 明确记录"30-minute Chrome/Edge playback 未验证"）。

### Bug 1：自动播放被拦截且被静默吞掉（症状：进度条一直加载但永不播放）

- 根因链：`<video>` 无 `muted` → `MANIFEST_PARSED` 里异步 `video.play()` 被自动播放策略拒（`NotAllowedError`，即使模拟过用户点击也复现）→ `.catch(() => {})` 吞掉 → 状态栏只看服务器状态显示"延迟播放中"（不看 `video.paused`）→ hls.js 持续缓冲，用户看到"加载正常但不播"。
- 修复：`index.html` 加 `muted autoplay`；`player.js` 新增 `attemptAutoplay()`（被拒时显示明确提示 + 页面点击重试）；状态文本按 `video.paused` 区分"延迟播放中"/"就绪待播放"。
- 已知行为变化：首次开播是**静音**的（浏览器限制），用户需在播放器上取消静音。

### Bug 2：播放头被强拖到直播边缘反复跳帧（症状：7s→13s→18s 前跳，不能正常播）

- 实测证据（1s 采样 × 60s）：播放头距边缘延迟稳定衰减 4.5→2.5→0.5→0.01s → 缓冲区饿死（4 秒 stall）→ 超阈值被强制 seek **+6s** × 2 次。
- 根因：旧配置按**秒**贴边：`liveSyncDuration: 2` / `liveMaxLatencyDuration: 5`。2s < 一个 5s 分片，播放头永远停在最后一个分片内部，播到边缘即饿死 → 强拖 → 死循环。用户往回拖进度条能播（缓冲区里有数据），正好印证。
- 修复：改按**分片数**追逐（见 §3.⑤ 配置）。修复后 45s 复测：0 跳跃、0 卡顿、1.01 s/s 匀速、lag 稳定 11–22s。
- 证据截图：`output/playwright/player-autoplay-fixed.png`、`output/playwright/player-livesync-fixed.png`。

---

## 5. 减小延迟的选项（按性价比排序）

| 方案 | 省多少 | 改动量 | 代价/风险 |
|---|---|---|---|
| A. 发布延迟 2→0 | ~2s | UI 下拉直接选 | 几乎无风险，本来就是余量 |
| B. 追逐 3→2 个分片（`liveSyncDurationCount`） | ~5s | player.js 一行 | 对分片迟到敏感，网络抖动时可能偶发 buffering；需实测验证 |
| C. 源安全缓冲 5→3s | ~2s | core.py | 403/断流恢复余量变薄，**不建议**（有历史故障教训） |
| D. 挑关键帧更密的源/清晰度 | 最多 ~10s | 探测逻辑 | GOP 由 YouTube 编码器定；同一直播不同 rendition 的 GOP 可能不同，可探测但不可控 |
| E. **转码强制 1–2s 关键帧**（结构性） | 总延迟可到 ~10s | 大 | 1080p60 实时重编码吃 CPU/GPU、质量有损、违背不静默转码策略；若做必须做成显式用户选项（如"低延迟模式（转码）"开关） |
| F. LL-HLS partial segments | 理论最优 | 很大 | FFmpeg 不支持产出 LL-HLS parts，基本排除 |

**E 展开**：`-c copy` 换成重编码 + `-g`/`-force_key_frames` 强制 1–2s 关键帧后，③ → ~2s，⑤ → 3×2=6s，总延迟 ≈ 5+2+2+6 ≈ 10–15s。这是唯一能把延迟压到原目标附近的路线。

## 6. 一个重要视角：这个产品的正确指标可能不是"延迟最小"

对延迟跟读/语言学习场景，用户要的是 **"稳定比直播慢 N 秒"**，而不是总延迟最小。只要延迟不漂移、不跳帧，20s 和 10s 对学习体验没有本质区别。当前配置本质上是**用延迟数字换延迟稳定性**——这是上一轮修复跳帧时刻意做的权衡。新分析应明确：优化目标到底是"总延迟小"还是"延迟方差小"，两者结论不同。

## 7. 已验证 / 未验证清单

**已验证（实测）**：
- yt-dlp 120s+ 连续下载无 403（720p 与 1080p60 分离音视频两条路径）
- 公开窗口 30s 有界、磁盘清理生效、停止后无 yt-dlp 残留进程
- 静音自动播放成功、播放头稳定、45s 无跳帧无卡顿

**未验证（progress.md 明确记录，新对话可继续）**：
- 30 分钟长时浏览器播放（内存/hls.js 缓冲行为/分片序号回绕）
- 账号受限直播（会员限定等）的 cookie 全链路
- Bilibili 直播全链路（代码路径存在但未实测）
- 延迟长时间稳定性（lag 是否慢漂移）
- 上述 A/B 选项的实际效果与副作用

## 8. 如何复现测量（新对话可直接照做）

```bash
# 1. 启动 Companion（Python 3.10 + aiohttp，根目录）
cd F:/Projects/LagLingo/prototype/hls-companion
python -m companion.server --port 8765 --runtime-dir runtime/repro

# 2. 找一个当前在播的直播并启动会话
curl -X POST http://127.0.0.1:8765/api/start -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=<LIVE_ID>","qualityId":"auto","publishDelaySeconds":2}'

# 3. 轮询直到 playlistReady=true
curl http://127.0.0.1:8765/api/status
```

浏览器侧测量（Playwright，全局已装 `@playwright/cli`，playwright 在其嵌套 node_modules）：
打开 `http://127.0.0.1:8765/`，每秒采样 `video.currentTime` / `readyState` / `seekable.end(last) - currentTime`（=lag）。判定：相邻秒 delta > 2.5 = 跳帧；0 ≤ delta < 0.3 且未暂停 = 卡顿；lag 曲线 = 追逐距离。

关键观测点：
- 公开播放列表 `runtime/<dir>/public/live.m3u8` 的 EXTINF 分布 = 源 GOP 实情
- `/api/status` 的 `hiddenMediaSeconds`（④+③ 的服务器侧延迟）
- 页面遥测"估计总延迟"= sourceDelay + hidden + 浏览器落后边缘

## 9. 关键文件索引

| 内容 | 位置 |
|---|---|
| 本延迟分析 | `docs/live-delay-handoff.md`（本文件） |
| 逐次会话工作日志 | `progress.md`（含本次两个 bug 修复记录） |
| 历史技术结论（403、代理、yt-dlp 选型） | `findings.md` |
| FFmpeg 命令 + 延迟发布器 | `prototype/hls-companion/companion/core.py` |
| yt-dlp 采集 | `prototype/hls-companion/companion/ytdlp_ingest.py` |
| HTTP 服务 | `prototype/hls-companion/companion/server.py` |
| 浏览器播放器（两处修复点） | `prototype/hls-companion/web-player/player.js`、`index.html` |
| 测试入口 | 根目录 `npm run test:hls-companion` |
