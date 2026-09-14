# LagLingo Bilibili / Twitch 正在直播媒体支持 Spec

**Status:** ready-for-agent  
**Scope:** 在不重构现有媒体架构的前提下，让 LagLingo 支持正在进行的 Bilibili Live 和 Twitch 频道直播；YouTube 既有能力不得回归

## 1. Problem Statement

LagLingo 当前真正跑通的是 YouTube。Bilibili URL 虽然能被 vendored yt-dlp 的 `BiliLive` extractor 正确识别，但 LagLingo 的质量筛选器仍假设所有平台都像 YouTube 一样提供明确的 `height/fps/acodec` 和可配对的独立 AAC 音轨。真实 Bilibili Live 经常返回以下形态：

```text
ext=fmp4
protocol=m3u8_native
vcodec=avc
acodec=null
height=null
width=null
fps=null
```

这里的 `acodec=null` 表示 yt-dlp 没有填写音频元数据，不表示媒体中没有音频。对用户提供的直播间 `https://live.bilibili.com/22637261?live_from=71002`，直接检查真实媒体可确认它是单路 HLS，内含 H.264 1280×720@30 视频和 AAC 48 kHz 双声道音频。当前 `build_quality_options()` 因缺少高度和独立音轨而丢弃全部格式，最终错误地返回：

```text
No usable video/audio qualities were returned by yt-dlp
```

即使放宽质量筛选，BiliLive 的 `ext=fmp4` 还会触发 yt-dlp 的不常见扩展名安全保护，阻止当前 stdout ingest。因此需要一个仅限可信 `BiliLive` 提取结果的窄输出修复，不能全局关闭 yt-dlp 的扩展名安全检查。

Twitch 当前尚未进入 LagLingo 的 URL、Cookie、平台识别和消息路由边界。yt-dlp 的 `TwitchStreamIE` 已能负责 GraphQL 播放令牌、Usher HLS 和直播状态；LagLingo 需要接受其单路 muxed HLS，并确保 Twitch 不会误走 Bilibili 弹幕实现。

## 2. Outcome

完成后，用户可以在 LagLingo 中输入：

```text
https://live.bilibili.com/<room-id>
https://www.twitch.tv/<channel>
```

当页面对应一场正在进行的公开直播时，系统能够：

1. 使用 vendored yt-dlp 完成页面提取和直播媒体获取；
2. 提供可选择的浏览器兼容 H.264/AAC 清晰度；
3. 将媒体送入现有本地 FFmpeg delayed fMP4 HLS 管线；
4. 继续产生 `EXT-X-PROGRAM-DATE-TIME`，保持 Media Wall Clock 合同；
5. 从同一份输入媒体中提取音频，送入现有字幕 FFmpeg 和 ASR 管线；
6. Bilibili 弹幕继续最佳努力运行，失败不影响媒体和字幕；
7. Twitch Chat 明确显示为暂不支持，不阻止 Twitch 媒体和字幕；
8. Stop、切换平台和启动失败后不留下 yt-dlp、FFmpeg、TCP Pump、Cookie 文件或旧会话回调。

## 3. Scope Boundary

### 3.1 支持

- 正在进行的 YouTube 直播：保持现有行为。
- 正在进行的 Bilibili Live 房间。
- 正在进行的 Twitch 频道直播。
- 公开直播匿名访问。
- Bilibili 和 Twitch 可选 Cookie；公开直播不得因为没有 Cookie 被拒绝。
- Bilibili 现有 `DANMU_MSG` Source，继续作为媒体之后启动的可降级能力。
- 字幕、翻译、本地发布延迟、回看窗口和 Media Wall Clock 继续复用现有实现。

### 3.2 不支持

- 普通视频、VOD、直播回放、Clips、开播前等待页和已结束直播。
- Twitch Chat、Twitch IRC 或 EventSub。
- Twitch 广告移除或规避。
- HEVC、AV1 到 H.264 的实时转码。
- 为 Bilibili/Twitch 再下载一份独立音频。
- 任意 yt-dlp 网站的通用支持。
- 新的媒体插件系统、平台策略框架或第二套 ingest 生命周期。
- 每次 Start 前额外运行 FFprobe；FFprobe 只作为开发和真实验收工具。

不在支持范围内的 URL 必须返回可操作错误，不得静默回退到普通视频下载：

```text
仅支持正在进行的直播 / Only currently ongoing live streams are supported
```

## 4. Minimal Architecture Decision

“Muxed ingest”只描述输入媒体中已经同时包含视频和音频，不代表新建一套实现。继续使用唯一的 `YtDlpLiveIngest`：

```text
YouTube selector = video+audio
  → 2 个 yt-dlp process
  → 2 个 TCP Pump
  → packaging FFmpeg 分别读取视频和音频
  → 音频 Pump 同时 tee 给字幕管线

Bilibili/Twitch selector = muxed
  → 1 个 yt-dlp process
  → 1 个 TCP Pump
  → packaging FFmpeg 从 0:v:0 和 0:a:0 取流
  → 同一个 Pump tee 给字幕 FFmpeg
  → 字幕 FFmpeg 使用 -vn 只解码音频为 PCM
```

现有代码已经根据 selector 中是否存在 `+` 决定一条或两条 ingest leg，并且 `_audio_pump()` 在单路时返回唯一 Pump。因此实现不得引入新的公开抽象；只需让质量选择为 Bilibili/Twitch 产生单个 format selector，并让该格式稳定输出现有管线要求的 MPEG-TS 字节。

### 4.1 不变量

- 媒体只下载一次；字幕和包装共享同一份字节。
- 字幕 tee 必须在 Pump 创建时安装，继续观察媒体 byte zero。
- 包装和字幕任何一方失败不得造成第二份远程媒体连接。
- YouTube 的双路下载、并发 HLS fragment 和认证租约行为不得改变。
- 平台特化只允许出现在 URL/Cookie allowlist、格式解释、必要的 yt-dlp 参数和消息 Source 路由。
- 没有真实失败证据时，不增加平台配置对象、动态策略注册或多级 fallback。

## 5. URL、平台与直播状态合同

### 5.1 URL allowlist

只接受 HTTPS：

- YouTube：现有域名保持不变；
- Bilibili：`live.bilibili.com/<numeric-room-id>`；
- Twitch：`twitch.tv/<channel>` 或 `www.twitch.tv/<channel>`。

Bilibili query string 可以保留，但 host 必须是 `live.bilibili.com`，路径必须以数字 room ID 开始；现有对普通 `bilibili.com/video/...` 的宽松接受不得作为本 Spec 的支持合同。不得因为加入 Twitch 而接受所有 `twitch.tv` 内容。Twitch VOD、Clip、目录、分类和用户设置 URL 必须在 probe 后或 URL 规范化阶段拒绝。

### 5.2 平台识别

Server 必须明确返回 `youtube | bilibili | twitch | null`，不得继续使用“不是 YouTube 就是 Bilibili”的二分支。消息启动也必须显式分支：

```text
youtube  → YouTubeChatIngest
bilibili → BilibiliDanmakuIngest
twitch   → unsupported/disabled message state
```

Twitch 不得实例化 Bilibili Source。

### 5.3 当前直播

成功 Start 必须满足 yt-dlp 提取结果表示正在直播：

```text
is_live == true
或 live_status == "is_live"
```

`is_upcoming`、`was_live`、`post_live`、offline、rerun（当 `is_live` 不为 true）、普通 VOD 和 Clip 均不得进入媒体 ingest。

## 6. Quality Normalization Contract

继续使用现有 `QualityOption`、`select_quality()` 和 `SelectedInputs`，不得建立第二套质量模型。允许对字段语义作最小扩展，使单路 muxed 格式能被正确表达。

### 6.1 通用字段语义

- `acodec == "none"`：已知该格式没有音频。
- `acodec == null/""/"unknown"`：音频元数据未知，不得自动等价为无音频。
- `vcodec == "none"`：已知该格式没有视频。
- `separateAudio=true`：只有在实际选择了独立 `audioFormatId` 时才成立。
- 单个 format selector 表示 muxed 输入；`audioFormatId` 为 null。
- `requiresTranscode=true` 的格式不得被 Auto 选择。

### 6.2 YouTube

保持现有逻辑：优先 H.264 视频配 AAC 音轨；需要时使用两个 selector 和两个 ingest leg。不得为了支持其他平台改变现有 YouTube 格式排序、格式 ID、最大高度和 downloader 行为。

### 6.3 Bilibili Live

对 `extractor_key == "BiliLive"` 或等价 extractor 标识：

- AVC/H.264 的 HLS/fMP4 格式即使缺少 `height/width/fps/tbr/acodec`，仍可生成 muxed `QualityOption`；
- 不要求存在独立 AAC 格式；
- `format_note` 或 Bilibili 逻辑质量名称用于可见 label；未知分辨率必须显示为未知，不得伪造为 720p/1080p；
- 同一逻辑清晰度、codec 和 protocol 的 CDN 镜像只暴露一个稳定选项；
- Auto 优先级为兼容 AVC HLS，其次才考虑兼容 AVC FLV 回退；HEVC 标记不兼容且不得 Auto 选择；
- 如果所有格式都是 HEVC 或无法形成可用的 AVC 选项，返回“没有浏览器兼容的 H.264 直播清晰度”，不得静默转码。

BiliLive 的 `acodec` 未知可以进入 Start，但 packaging FFmpeg 必须能读取 `0:v:0` 和 `0:a:0`。若真实输入缺少任一流，Start 失败并清理会话，错误不得继续伪装成“无质量”。

### 6.4 Twitch

对 `TwitchStream` extractor：

- 使用 yt-dlp 返回的 HLS Source/chunked 和转码 rendition；
- 只将 H.264/AAC 或音频元数据未知但已被 Twitch extractor 标记为 muxed HLS 的格式视为候选；
- Auto 在 `maxHeight` 内优先 Source/最高分辨率，然后最高帧率和码率；
- AV1/HEVC 格式不进入 Auto；
- Twitch 只生成单个 selector 和单条 ingest leg。

## 7. yt-dlp Ingest Contract

### 7.1 唯一 ingest

继续使用 `YtDlpLiveIngest`。它必须同时支持：

- selector `video+audio`：两个进程/两个 Pump；
- selector `muxed`：一个进程/一个 Pump。

现有 start、first-byte credential cleanup、日志脱敏、Stop/kill/join 和 TCP Pump 生命周期继续作为唯一实现。

### 7.2 BiliLive fMP4 窄修复

vendored yt-dlp 会因 BiliLive 提取结果的 `ext=fmp4` 拒绝 stdout 输出。实现必须满足：

- 只对已经由可信 `BiliLive` extractor 返回、且 protocol 为 HLS 的选中格式生效；
- 只规范化供 yt-dlp 下载使用的临时 probe/output metadata 或等价的输出提示，使其产生 MPEG-TS；
- 不修改原始 URL allowlist；
- 不全局允许任意扩展名；
- 不关闭 yt-dlp 的 unsafe-extension 防护；
- 不修改 vendored yt-dlp 可执行文件；
- 临时 info JSON 继续使用 owner-only 权限并按现有认证租约清理；
- 如果该窄修复不能稳定产生 MPEG-TS，应返回平台媒体错误，而不是回退到重复下载音频或增加转码。

### 7.3 Downloader 参数

默认先复用当前已验证的 yt-dlp 重试、proxy、`--no-live-from-start` 和输出到 stdout 的生命周期。

- YouTube 的 `m3u8:native` 与 fragment concurrency 行为保持不变。
- Bilibili 使用能稳定把选中 HLS/FLV 规范化为 MPEG-TS 的最小参数集合。
- Twitch 先使用现有命令形态；只有确定性的测试或真实直播证据证明 native HLS 在 Twitch discontinuity/广告处失败，才增加 Twitch-only downloader 参数。
- 不实现运行时可配置 downloader 策略系统。

### 7.4 Packaging 与字幕

单路输入的 packaging FFmpeg 必须：

```text
-map 0:v:0
-map 0:a:0
-c copy
```

字幕 tee 继续把同一 muxed MPEG-TS 输入送入：

```text
-f mpegts -i pipe:0 -vn -ac 1 -ar <rate> -f s16le pipe:1
```

不新增 demux 代码，不将视频字节交给 ASR Provider；字幕 FFmpeg 负责提取和解码音频。

## 8. Authentication and Security

### 8.1 Cookie allowlist

- YouTube/Google 与 Bilibili 现有 allowlist 保持不变；
- Twitch 只允许 `twitch.tv` 及其子域 Cookie；
- Twitch 公开直播匿名可用时不得强制 Cookie；
- UI 可以提供 Twitch Cookie 导入，但不得把“存在 auth-token”作为公开直播 Start 前置条件；
- 临时 Cookie 文件必须继续由 `SessionAuthLease` 的短命 consumer 管理。

### 8.2 Secrets

下列内容不得进入日志、状态、API 响应或测试快照：

- Cookie 值；
- Twitch OAuth/auth-token；
- Twitch playback token/signature；
- Bilibili 签名媒体 URL；
- Twitch Usher 签名 manifest URL；
- yt-dlp 临时 info JSON 内容。

错误和 status 只允许输出已脱敏的平台、format ID、进程退出码和非敏感原因。

## 9. Live Message Contract

### 9.1 Bilibili

现有 `BilibiliDanmakuIngest` 保持最佳努力：

- 媒体成功之后启动；
- 匿名优先，Cookie 可选；
- 风控、412、`-352`、WebSocket、token 或解压失败只更新 `liveMessages` 状态；
- 不回滚媒体、ASR 或字幕；
- Stop/切流继续关闭 WSS、heartbeat、reconnect 和旧 generation callback。

### 9.2 Twitch

本 Spec 不实现 Twitch Chat。Twitch Start 时：

- 不创建 `YouTubeChatIngest` 或 `BilibiliDanmakuIngest`；
- `liveMessages` 返回明确的 `unsupported` 或等价稳定状态；
- UI 显示“Twitch 聊天暂不支持”，而不是 error toast；
- 用户请求 `liveMessages.enabled=true` 也不得使媒体 Start 失败。

## 10. Errors and Status

Probe/Start 至少区分：

1. URL 平台不支持；
2. URL 不是允许的直播页面类型；
3. 当前没有正在直播；
4. yt-dlp extractor/网络/区域/平台风控失败；
5. 没有兼容 AVC 选项；
6. 选中格式媒体 URL 缺失或过期；
7. yt-dlp 未产生任何媒体字节；
8. muxed 输入缺少视频或音频；
9. packaging FFmpeg 失败；
10. 直播消息 unsupported/unavailable/error。

不得继续把所有 Bilibili 失败折叠为 `No usable video/audio qualities...`。YouTube 既有错误语义除必要的统一“正在直播”提示外不得回归。

`/api/status` 的媒体状态可以增加平台和选中 format ID/leg 数量等非敏感诊断，但不得为了本 Spec 建立新的状态 API。

## 11. Lifecycle Contract

每次 Start B 必须先完整停止 A，无论平台是否相同：

```text
stop messages
stop subtitles / detach tee
stop yt-dlp ingest and join log/pump threads
stop packaging FFmpeg and publisher
release auth lease and temporary files
clear old session state
start new session
```

必须覆盖：

- 两路 YouTube → 单路 Bilibili；
- 单路 Bilibili → 单路 Twitch；
- 单路 Twitch → 两路 YouTube；
- 单路会话启动中失败；
- 重复 Stop；
- application cleanup；
- yt-dlp 或 FFmpeg 提前退出。

任何路径都不得遗留进程、Pump listener、reader thread、Cookie/info JSON 文件、字幕 tee 或消息 Source。

## 12. Acceptance Criteria

### 12.1 Deterministic automation

- BiliLive fixture 在缺少尺寸和音频 codec 元数据时仍产生 muxed AVC 质量；
- BiliLive CDN 镜像去重、HEVC 不进入 Auto；
- BiliLive fMP4 窄修复不会影响非 BiliLive 或不安全扩展名；
- Twitch channel URL 可通过 allowlist，VOD/Clip/非 Twitch URL 被拒绝；
- Twitch fixture 产生单路 muxed 质量；
- Twitch messages 明确 unsupported，绝不实例化 Bilibili Source；
- 单路 Pump 同时向包装路径和字幕 tee 转发 byte zero；
- 双路 YouTube 测试保持绿色；
- 单路/双路 Stop、失败 rollback 和 auth cleanup 均无残留；
- Python/Node focused tests、生产浏览器 smoke 和完整 `npm run ci` 通过。

### 12.2 Real Bilibili

当房间仍在直播时，用户提供的 `22637261` 必须：

- probe 成功；
- Auto 选择 AVC HLS；
- 本地 HLS 有画面和声音；
- PDT/Media Wall Clock 可用；
- 开启字幕时 ASR 收到 PCM；
- 连续播放至少 10 分钟；
- Stop 后没有残留进程/线程/临时文件。

另测试至少一个正在直播的 Bilibili 房间。Bilibili 弹幕验收单独记录，不得阻塞媒体通过。

### 12.3 Real Twitch

至少两个正在直播的公开 Twitch 频道必须：

- 匿名 probe/start 成功；
- 本地 HLS 有画面和声音；
- 字幕能够从同一 muxed 输入取得音频；
- PDT/Media Wall Clock 可用；
- 每个连续播放至少 10 分钟；
- 如果观察到广告/discontinuity，广告前后媒体能够继续或给出明确失败证据；
- Stop 后无残留。

另验证一个离线频道和一个 VOD/Clip URL被明确拒绝。没有可用直播样本或未观察到广告边界时，必须记录为未覆盖，不得标为已通过。

### 12.4 YouTube regression

至少执行一次当前 YouTube 直播 smoke，确认双路 selector、字幕、PDT 和 Stop 没有回归。

## 13. Implementation Guardrails

- 优先最小正确修改，不重构已工作的 YouTube 路径。
- 先写能复现当前 Bilibili probe 失败和 Twitch 边界缺失的测试。
- 不为未来平台设计抽象。
- 不添加重复音频下载。
- 不添加运行时 FFprobe。
- 不静默转码或降质。
- 不让消息支持阻止媒体成功。
- 自动化通过与真实平台验收分开记录。
- 实施中如果现有单路 Pump 不能满足要求，必须先提供可复现证据，再扩大设计。

## 14. Primary Files

预期最小修改范围：

- `prototype/hls-companion/companion/core.py`
- `prototype/hls-companion/companion/ytdlp_ingest.py`
- `prototype/hls-companion/companion/server.py`
- `prototype/hls-companion/extension/service-worker.js`
- `prototype/hls-companion/extension/manifest.json`
- `prototype/hls-companion/web-player/index.html`
- `prototype/hls-companion/web-player/player.js`
- 直接相关 Python/Node/browser tests
- `prototype/hls-companion/README.md`

不得因本 Spec 顺手重构 Provider、SubtitlePipeline、播放器设计系统、LiveMessageStore 或 MediaClock。
