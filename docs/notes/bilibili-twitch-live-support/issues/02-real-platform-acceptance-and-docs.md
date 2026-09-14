# 02 — 真实平台验收、生命周期与文档

**What to build:** 在 Ticket 01 实现完成后，只做真实 YouTube/Bilibili/Twitch 正在直播验收、跨平台生命周期验证、正式文档更新，以及由真实证据暴露的最小修复。本 Ticket 不新增 Twitch Chat、转码、通用平台框架或其他功能。

**Blocked by:** 01.

**Status:** ready

**Primary ownership:**

- 真实平台验收脚本和脱敏结果
- `prototype/hls-companion/README.md`
- 必要的 UI 支持范围/错误文案
- 仅限验收失败直接要求的生产和回归测试修复

## Acceptance setup

- [ ] 使用项目 vendored yt-dlp 和生产 Companion 路径；不得用手写媒体 server 代替平台提取/下载。
- [ ] 记录 yt-dlp 版本、平台、extractor、format ID、leg 数量、实际 codec/分辨率、是否匿名、目标发布延迟和测试时长；签名 URL、Cookie 和 token 必须脱敏。
- [ ] 测试开始前确认没有旧 yt-dlp/FFmpeg/Companion 残留；结束后再次确认无孤儿进程、Pump、线程和临时 auth/info 文件。
- [ ] 没有当前直播样本、平台风控、地区限制或无法观察广告边界时，记录为 blocked/not observed，不得标为 pass。

## Real Bilibili acceptance

- [ ] 当用户房间 `https://live.bilibili.com/22637261?live_from=71002` 仍在直播时执行生产 probe/start；若已下播，保留其 fixture，并选择另一个当前直播房间完成真实验收。
- [ ] 至少两个正在直播的 Bilibili 房间：一个优先 AVC HLS，另一个包含多个 codec/CDN 候选；记录 Auto 的选择依据。
- [ ] 每个房间验证本地 HLS 有画面和声音、PDT/Media Wall Clock 可用、字幕开启时 ASR 收到 PCM，连续播放至少 10 分钟且无永久 stall/jump。
- [ ] 分别验证字幕开启和关闭；字幕开启不得触发第二份远程音频下载。
- [ ] 验证匿名路径；若公开质量受限，再验证 Bilibili Cookie 路径和临时文件清理，但 Cookie 不得成为所有公开直播的必需条件。
- [ ] 观察现有 `BilibiliDanmakuIngest`：能收到则验证墙钟门控和 Stop；遇到 `-352`、412、token/WSS 失败则记录 degraded，媒体和字幕必须继续。
- [ ] 验证 Bilibili Stop → Start、Bilibili A → Bilibili B 和启动中失败 rollback。

## Real Twitch acceptance

- [ ] 找到至少两个当前正在直播的公开 Twitch 频道，使用标准 `https://www.twitch.tv/<channel>` URL 匿名 probe/start。
- [ ] 每个频道验证 Auto 选择兼容 muxed HLS，本地 HLS 有画面和声音、PDT/Media Wall Clock 可用、字幕从同一输入获得 PCM，连续播放至少 10 分钟。
- [ ] Twitch 消息面板必须显示 unsupported/暂不支持，不得连接 Bilibili Source，也不得影响媒体状态。
- [ ] 若真实直播在当前命令下稳定，不添加 Twitch 特殊 downloader；若出现可复现 native HLS/ad discontinuity 失败，先保存脱敏日志和最小复现，再做唯一必要的 Twitch-only 参数修复并增加回归测试。
- [ ] 如果测试期间观察到 pre-roll/mid-roll 或 `EXT-X-DISCONTINUITY`，确认广告前后媒体继续、PDT 不倒退且本地 fMP4 不永久卡死；不要求移除广告。
- [ ] 验证一个离线频道和一个 Twitch VOD 或 Clip 被明确拒绝为“仅支持正在进行的直播”。
- [ ] 若有可用测试账号，可补一条 Cookie-assisted smoke；匿名公开直播仍是必须通过的主路径。

## Cross-platform lifecycle and YouTube regression

- [ ] 使用真实或生产等价路径执行：YouTube → Bilibili → Twitch → YouTube，每次切换前后检查 Store、字幕 tee、消息 Source、auth lease、yt-dlp/FFmpeg 和 Pump。
- [ ] 每个平台执行 Stop → Start、重复 Stop、页面刷新后状态恢复和 application cleanup。
- [ ] YouTube 当前直播至少 10 分钟回归：双路 selector、媒体声音、字幕 PCM、PDT、可选 live chat 和 Stop 均保持工作。
- [ ] 验证平台消息隔离：YouTube chat 问题不影响媒体；Bilibili 弹幕问题不影响媒体；Twitch unsupported 不被报告为媒体 error。
- [ ] 记录播放 stall/jump、首个本地 playlist 时间、CPU/RSS、进程/线程峰值和最终残留资源；只针对真实异常做最小修复。

## Documentation

- [ ] 更新 HLS Companion README：支持 YouTube/Bilibili/Twitch 正在直播；明确普通视频、VOD、Clip、回放和 upcoming 不支持。
- [ ] 说明唯一 ingest 的两种现有形态：YouTube 双路；Bilibili/Twitch 单路 muxed；字幕 FFmpeg 用 `-vn` 从同一输入提取音频，不会重复下载。
- [ ] 说明格式边界：优先 H.264/AAC stream copy，HEVC/AV1 不转码；BiliLive metadata 可能缺尺寸；Twitch Chat 暂不支持；Twitch 广告不移除。
- [ ] 说明匿名/Cookie 行为、Cookie 文件生命周期、日志脱敏、Bilibili 风控和 Twitch 区域/订阅/广告风险。
- [ ] 记录真实验收矩阵，明确 pass、failed、blocked、not observed；不得用自动化测试替代真实平台结果。

## Final verification

- [ ] 为验收中发现并修复的每个问题增加能在旧实现上失败的 focused test；禁止无证据重构。
- [ ] 运行受影响 focused suites、生产 browser smoke、`npm run test:hls-companion`、完整 `npm run ci`、Python compile、Node syntax、LSP diagnostics 和 scoped `git diff --check`。
- [ ] 最终报告分别回答：Bilibili 媒体是否真实通过、Bilibili 弹幕是否通过、Twitch 媒体是否真实通过、Twitch 广告边界是否观察、YouTube 是否回归通过、是否存在残留资源。
