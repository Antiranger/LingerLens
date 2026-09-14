# 01 — Bilibili / Twitch 正在直播媒体实现

**What to build:** 以最小改动让现有 HLS Companion 支持正在进行的 Bilibili Live 和 Twitch 频道直播。复用唯一的 `YtDlpLiveIngest`、现有单 selector/单 Pump 分支、packaging FFmpeg、字幕 tee、认证租约和 Media Wall Clock；不新建平台框架、不重复下载音频、不实现 Twitch Chat。

**Blocked by:** None.

**Status:** ready

**Primary ownership:**

- `prototype/hls-companion/companion/core.py`
- `prototype/hls-companion/companion/ytdlp_ingest.py`
- `prototype/hls-companion/companion/server.py`
- `prototype/hls-companion/extension/service-worker.js`
- `prototype/hls-companion/extension/manifest.json`
- `prototype/hls-companion/web-player/index.html`
- `prototype/hls-companion/web-player/player.js`
- 直接相关 Python/Node/browser tests

## Red tests and URL boundary

- [ ] 先加入用户房间真实 BiliLive probe shape 的脱敏 fixture：`ext=fmp4`、HLS、`vcodec=avc`，但尺寸、fps、码率和 `acodec` 缺失；证明当前 `build_quality_options()` 返回空。
- [ ] 加入 BiliLive 多 CDN、AVC/HEVC、HLS/FLV fixture；测试 Auto 只选择兼容 AVC，不将 HEVC 静默转码。
- [ ] 加入 TwitchStream muxed HLS fixture；测试当前 URL allowlist 是红灯，并覆盖 Source/最高 `maxHeight` 排序。
- [ ] URL 验证只新增正在直播所需的 `twitch.tv/<channel>` 边界；Bilibili 保持只接受 live room。Twitch VOD、Clip、目录和任意其他 yt-dlp 网站必须拒绝。
- [ ] 扩展和网页文案从 YouTube/Bilibili 更新为 YouTube/Bilibili/Twitch；仅加入对应页面匹配，不扩大 host permissions 到无关域。

## Minimal quality normalization

- [ ] 保留现有 `QualityOption` 和 YouTube 逻辑；不要新增通用平台策略类。
- [ ] `acodec="none"` 才表示确定无音频；BiliLive/Twitch muxed HLS 的 null/unknown 音频 metadata 不得触发独立音轨要求。
- [ ] BiliLive 即使没有 `height` 也能以 `quality/format_note/format_id` 生成稳定单 selector 选项；未知分辨率在 UI 如实显示，不能伪造。
- [ ] BiliLive CDN 镜像按逻辑质量、codec 和 protocol 去重；Auto 优先 AVC HLS，兼容 AVC FLV 仅作必要回退，HEVC 不进入 Auto。
- [ ] Twitch 使用 yt-dlp 返回的 muxed HLS metadata，Auto 在 `maxHeight` 内优先兼容 Source/最高分辨率、fps 和码率；AV1/HEVC 不进入 Auto。
- [ ] `selected_inputs()` 对 muxed option 生成 `audio_url=None`、`separateAudio=false`；YouTube 双路 option 保持原行为。

## One-leg ingest and BiliLive output

- [ ] 复用 `YtDlpLiveIngest.selectors` 现有行为：YouTube `v+a` 创建两路；Bilibili/Twitch 单 format ID 创建一路。不得新建 Bilibili/Twitch ingest 生命周期。
- [ ] 单路 Pump 同时送入 packaging FFmpeg，并在构造时安装字幕 tee；现有字幕 FFmpeg 使用 `-vn` 从同一 muxed MPEG-TS 中提取 PCM。不得发起第二个远程音频请求。
- [ ] 为可信 `BiliLive` HLS 选中格式实现最窄的 `ext=fmp4` stdout 修复，使 vendored yt-dlp 能输出 MPEG-TS；不得全局关闭 unsafe-extension 保护、修改 yt-dlp 可执行文件或允许任意扩展名。
- [ ] 保持 proxy、retry、`--no-live-from-start`、日志脱敏、first-byte secret cleanup 和 Stop/join 行为。平台专用参数只在测试证明必要时添加。
- [ ] Twitch 先复用现有下载命令；只有 focused test 或真实媒体证据证明 `m3u8:native` 失败，才添加 Twitch-only downloader 参数，不建设可配置策略系统。
- [ ] packaging FFmpeg 对单路输入映射 `0:v:0` 和 `0:a:0`；缺少视频或音频时返回明确格式错误并执行完整 rollback。

## Platform routing, auth and messages

- [ ] Server 平台识别改为明确的 `youtube | bilibili | twitch | null`；所有二分支调用点逐一检查。
- [ ] Twitch 加入独立 Cookie allowlist/import 选项，公开直播匿名可用时不得要求 auth-token；临时文件继续由 `SessionAuthLease` 管理。
- [ ] Cookie、Twitch playback token/signature、Bilibili/Twitch 签名媒体 URL和 info JSON不得进入日志、status 或响应。
- [ ] 消息 Source 显式路由：YouTube → YouTube source，Bilibili → Bilibili source，Twitch → unsupported。Twitch 绝不能实例化 `BilibiliDanmakuIngest`。
- [ ] Twitch 请求开启 live messages 时，媒体仍成功启动，前端内联显示“Twitch 聊天暂不支持”，不弹全局错误。
- [ ] Bilibili 弹幕仍在媒体成功后最佳努力启动；其风控/WSS/解压错误不得回滚媒体或字幕。

## Errors and lifecycle

- [ ] Probe/Start 区分：URL 类型不支持、当前未直播、extractor/风控失败、无兼容 AVC、选中 URL 过期、yt-dlp 未产生媒体、muxed 输入缺流和 packaging FFmpeg 失败。
- [ ] offline、upcoming、post-live、Twitch VOD/Clip 使用统一可操作的“仅支持正在进行的直播”提示。
- [ ] 测试 Start/Stop/rollback 的两路→单路、单路→单路、单路→两路；重复 Stop 和 application cleanup 幂等。
- [ ] 所有失败路径清理 yt-dlp/FFmpeg、Pump listener/readers、字幕 tee、Cookie/info JSON、auth lease、消息 Source 和旧 generation callback。

## Verification

- [ ] 增加/更新 `test_core.py`、`test_ytdlp_ingest.py`、server/live-message tests、web asset tests 和必要的 production browser smoke。
- [ ] 测试 BiliLive fMP4 窄修复不会影响非 BiliLive 的不安全扩展名。
- [ ] 测试单 Pump byte zero 同时到达包装路径和字幕 tee；现有 YouTube 两 Pump、认证租约和 shutdown tests 保持绿色。
- [ ] 运行 focused Python/Node tests、Python compile、Node syntax、生产 browser smoke、LSP diagnostics、scoped `git diff --check` 和完整 `npm run ci`。
- [ ] 本 Ticket 的完成只表示实现和确定性自动化完成；不得因为 fixture/CI 通过而声称 Bilibili/Twitch 真实平台已经验收。
