# 03 — 平台化 Cookie 导入与可复用播放会话

**What to build:** 给 YouTube 和 Bilibili 提供各自清晰的手动 Cookie 导入流程，并修复停止后的会话状态，使用户停止当前直播后可以立即探测和播放另一个直播。

**Blocked by:** None — can start immediately.

**Status:** complete

- [ ] 先在 loopback auth API seam 写失败测试：平台为 Bilibili 时，名称/值多行、Cookie header 和 Netscape 格式都能生成 yt-dlp 可用的 7 字段 Cookie 文件并保留 Bilibili 域名 Cookie。
- [ ] Cookie 导入窗口增加平台选择（YouTube / Bilibili），并根据平台自动设置 `.youtube.com`/Google 或 `.bilibili.com` 作用域与说明，不要求用户理解内部域名下拉。
- [ ] YouTube 继续使用现有关键 Cookie 提示；Bilibili 只把 `SESSDATA` 作为登录关键字段，`bili_jct`、`DedeUserID` 等存在时保留但不伪装成 yt-dlp 必填项。
- [ ] 导入响应返回 platform、accepted names、平台化 missing-critical 信息；前端错误/成功提示不再固定写 YouTube 的 SID/PSID 指引。
- [ ] 持久化认证按平台分开保存或按目标 URL 选择，导入 Bilibili 不会覆盖已保存的 YouTube 快照，反之亦然；旧单快照格式可安全迁移。
- [ ] Cookie 仍不写日志、不回显值、不进入命令行；临时 Netscape 文件权限与首次媒体字节后的清理合同保持不变。
- [ ] 先写失败回归：start URL A → stop → status 为真正 clean idle（无 pageUrl、quality、playlist、error、旧 uptime）→ probe/start URL B 成功；第二次 stop 仍安全。
- [ ] 停止时彻底清理 source ingest、FFmpeg、publisher、subtitle pipeline、cue store、HLS/player attachment 和 session identity，但保留 Provider Catalog、字幕偏好、目标延迟和持久化平台认证。
- [ ] 浏览器状态轮询不再把“idle + 上一 pageUrl”误报为管线错误；Stop 后重新启用 URL、探测和启动控件，并清空旧标题、消息和遥测。
- [ ] 增加 Bilibili 与 YouTube Cookie 并存、重启恢复、URL 选取、重复 Stop 和二次 Start 的红→绿测试；现有 yt-dlp ingest 生命周期测试保持绿色。
