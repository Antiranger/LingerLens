# PROTOTYPE 2 — 登录态、1080p、延迟 HLS/CMAF 浏览器播放器

这是抛弃式原型，用来回答一个具体问题：

> 能否从用户已登录的 YouTube/Bilibili 直播页获取可用媒体，手动选择 1080p/720p，以 FFmpeg stream copy 合流成约 10 秒延迟的本地 fMP4 HLS，并在 Chrome/Edge 的 hls.js 中稳定播放？

本目录现已加入可选的实时字幕链路；仍不包含弹幕、商店发布或正式安装器。

## 当前链路

```text
直播页面 URL
  → 当前版 yt-dlp 探测格式并完整负责直播音视频下载/重试
  → 选择 H.264/AVC + AAC（1080p/720p；支持视频/音频分离输入）
  → yt-dlp 输出连续 MPEG-TS → FFmpeg -c copy
  → 私有滚动 fMP4 HLS
  → Companion 保留约 7 秒完成分片，不立即公开
  → 公开环形 playlist + localhost HTTP
  → 本地打包 hls.js → Chrome/Edge

音频腿 MPEG-TS（非阻塞 tee）
  → 本机 FFmpeg 重采样为 PCM 16k mono
  → 可配置 ASR provider
  → 清洗 / 句尾墙钟对齐 / 翻译 provider
  → `/api/subtitles` → hls.js `playingDate` 对齐 overlay
```

YouTube下载层不再使用 Streamlink 或自制通用 HLS 代理。yt-dlp 独占页面提取、远程 playlist/分片、重试和URL刷新；LagLingo只接收本地 MPEG-TS，并负责额外发布延迟、约30秒公开窗口和旧分片清理。总延迟仍需按真实播放测试校准。

## 目录

- `companion/core.py`：格式归一化、认证边界、FFmpeg 命令、延迟 playlist 发布、环形清理。
- `companion/ytdlp_ingest.py`：当前版 yt-dlp 子进程；完整负责 YouTube 直播下载并输出 MPEG-TS。
- `companion/server.py`：只绑定 loopback 的 HTTP/API 服务，提供字幕与 provider 配置接口。
- `companion/providers/`：ASR/翻译 provider 抽象、百炼 Realtime/任务式 ASR、OpenAI 兼容/Qwen-MT 与 fallback。
- `companion/subtitle_pipeline.py`：音频 tee、TS→PCM、ASR、翻译、句尾墙钟对齐与统计。
- `companion/subtitle_text.py` / `subtitle_store.py` / `context_manager.py`：纯字幕逻辑。
- `web-player/`：独立播放器；`vendor/hls.min.js` 为本地打包的 hls.js 1.7.1。
- `extension/`：最小 MV3 开发扩展，从当前标签对应 Cookie Store 读取平台 Cookie。
- `companion/native_host.py`：Native Messaging → Companion 的敏感小消息桥。
- `companion/control_ipc.py`：带随机认证密钥的本机 named pipe / AF_UNIX 控制通道。
- `scripts/register-native-host.ps1`：Chrome/Edge Windows 开发注册助手。
- `scripts/synthetic-smoke.py`：本地 1080p H.264/AAC CMAF 延迟发布冒烟测试。

## 依赖

- Python 3.10+（本机 yt-dlp 已提示未来应升级到 3.11+）
- `aiohttp`
- 当前版 `yt-dlp`（已在 `vendor/yt-dlp/yt-dlp.exe` 固定并校验；版本 2026.08.19）
- `ffmpeg` / `ffprobe`
- Chrome 或 Edge

安装 Python 依赖：

```bash
python -m pip install -r prototype/hls-companion/companion/requirements.txt
```

## 方式一：开发期浏览器 Profile Cookie

这是最快的登录直播验证方式，不是产品主路径：

```bash
npm run prototype:hls -- --cookies-from-browser chrome
# 或
npm run prototype:hls -- --cookies-from-browser edge
```

然后打开：

```text
http://127.0.0.1:8765/
```

输入直播 URL，点击“探测清晰度”，选择 1080p 或 720p，再启动播放。

注意：Chromium 正在运行时，`--cookies-from-browser` 可能受浏览器 Cookie 数据库锁定/系统解密策略影响。失败时界面会显示 yt-dlp 的尾部诊断，不会静默降级。

## 方式二：当前标签 Cookie Store + Native Messaging

1. 启动 Companion（不要加 `--cookies-from-browser`）：

   ```bash
   npm run prototype:hls
   ```

2. 在 `chrome://extensions` 或 `edge://extensions` 打开开发者模式，加载：

   ```text
   F:/Projects/LagLingo/prototype/hls-companion/extension
   ```

3. 复制加载后显示的扩展 ID。

4. 在 PowerShell 注册本地 Host：

   ```powershell
   powershell -ExecutionPolicy Bypass -File `
     F:/Projects/LagLingo/prototype/hls-companion/scripts/register-native-host.ps1 `
     -ExtensionId 你的扩展ID
   ```

5. 打开已登录的 YouTube/Bilibili 直播页，点击扩展图标。扩展会：
   - 确定当前标签对应的 Cookie Store；
   - 读取当前平台 allowlist 范围 Cookie（包括 HttpOnly 和可用的 partition 元数据）；
   - 通过 Native Messaging 传给本机 Host；
   - 打开带一次性 `authToken` 的本地播放器页。

6. 在本地播放器中探测并启动。Cookie 快照在开始提取时消费并从内存删除。

## 方式三：在播放器里手动导入 Cookie

不需要关浏览器，也不需要扩展：

1. 在已登录的浏览器里安装任意 Cookie 导出扩展（如 “Get cookies.txt LOCALLY”），在 YouTube 页面导出 `youtube.com` 的 Cookie（Netscape 格式）。
2. 启动 Companion（无需任何 cookie 参数）：`npm run prototype:hls`。
3. 播放器右上角「导入 Cookie」→ 选格式 → 粘贴 → 「导入并授权」。
4. 直接点「探测清晰度」「启动播放」；导入的授权在启动时消费，若启动失败重新导入一次即可。

也支持粘贴请求头格式的 Cookie 串（`SID=xxx; VISITOR_INFO1_LIVE=yyy`），此时选择作用域域名。导入接口仅接受 loopback 且校验同源 Origin，Cookie 只存内存，不落盘、不回显。

卸载注册：

```powershell
powershell -ExecutionPolicy Bypass -File `
  F:/Projects/LagLingo/prototype/hls-companion/scripts/register-native-host.ps1 `
  -ExtensionId 你的扩展ID -Unregister
```

## 安全边界

- Companion 只允许绑定 `127.0.0.1`、`localhost` 或 `::1`。
- Cookie 不通过普通 localhost HTTP API 提交；Native Host 通过带本机随机认证密钥的 Windows named pipe（Unix 上为用户级 AF_UNIX socket）把敏感快照交给 Companion。用户主动在播放器「导入 Cookie」时，同一份快照也可经仅限 loopback、同源校验的 `/api/auth-cookies` 提交（见方式三）。
- Cookie 不写日志、不回显、不放在 yt-dlp/FFmpeg 命令行。
- Native Host 会生成用户私有的 Netscape 临时 Cookie 文件供当前版 yt-dlp 启动；yt-dlp进入下载阶段后立即删除，停止/失败时也会清理。
- Cookie 只接受 YouTube/Google/Bilibili 域名；直播页面 URL 也限制为 YouTube/Bilibili HTTPS。
- YouTube 部分格式可能还要求 PO Token。本原型保留了 `AuthenticationProvider` 边界，但没有伪装已实现 PO Token。
- 开启云端字幕后，音频会从本机发送到用户配置的 ASR 服务（默认示例为阿里云百炼），源文/上下文会发送到翻译服务。这与 Cookie 只在本机流转是两条不同的数据边界；不开启字幕时不会挂载音频 tee。
- API Key 只从服务端 `runtime/providers.json` 或环境变量读取；`/api/providers` 永远脱敏，浏览器与扩展不接触密钥。真实 `runtime/providers.json` 应作为本机私有文件保存，不要提交或共享。

## 质量策略

- 显示 yt-dlp 返回的候选清晰度。
- “自动”只在启动时选不超过 1080p 的最高 H.264/AAC 兼容档。
- 手动选项通常包含 1080p、720p、480p（以直播实际提供为准）。
- 支持常见的 `video-only + audio-only` 选择和 FFmpeg 双输入映射。
- 若只有 VP9/AV1/Opus 等当前输出不兼容组合，选项标为不可用；本原型不会为了“成功”而静默转码。
- 当前测试直播恰好提供 1080p muxed AVC/AAC；因此“独立音视频输入”由单元测试覆盖，仍需另找实际分离格式直播做人测。

## 验证

字幕配置样例位于 `runtime/providers.example.json`。首次启动会生成本机 `runtime/providers.json`；推荐设置 `DASHSCOPE_API_KEY` 后运行。ASR 协议 spike：

```bash
python prototype/hls-companion/scripts/asr-spike.py --audio <16k单声道日语wav>
```

运行全部 Prototype 2 静态/单元测试：

```bash
npm run test:hls-companion
```

运行本地 1080p H.264/AAC → fMP4 HLS 延迟发布/ffprobe 冒烟测试：

```bash
python prototype/hls-companion/scripts/synthetic-smoke.py
```

运行旧 spike 测试：

```bash
npm test
```

## 浏览器冒烟边界

自动测试覆盖字幕浮窗控制器的归一化位置持久化、边界夹取、键盘移动、重置与样式参数，并检查播放器舞台全屏的 DOM 资源合同。仓库当前没有 DOM/Playwright 依赖，因此 Chromium 原生全屏归属仍需人工冒烟：启动媒体后点击 LagLingo 的“全屏”，确认 `document.fullscreenElement` 是 `.player-stage`、字幕仍可见；退出全屏后确认字幕调度继续。

## 已完成的自动验证

- 格式归一化和自动选择可选 1080p AVC/AAC；
- 分离视频/音频映射到 FFmpeg，命令明确使用 `-c copy`；
- fMP4 HLS 使用 `.m4s + init.mp4`；
- 当前版 yt-dlp 完整负责 YouTube 远程下载；LagLingo/FFmpeg 不再直接请求 Googlevideo URL；
- 延迟 publisher 保持下载后额外媒体预算，并把公开回看窗口限制在最近约 30 秒；
- hls.js 由本地文件加载，不依赖 CDN；
- MV3 清单与脚本可解析；
- 当前公开 YouTube 直播探测到实际 `1920×1080 AVC/AAC` 格式；
- 合成 1920×1080 H.264 + AAC 成功生成/发布本地 HLS，ffprobe 识别为 H.264/AAC；
- Chrome 真实浏览器已打开本地播放器并完成初始 UI/控制可访问性检查。

## 仍需人工完成的验收

自动验证不能替代以下真实直播测试：

1. Edge/Chrome 连续播放当前 YouTube 直播至少 30 分钟；
2. Edge 重复同一测试；
3. 实测总延迟是否稳定在约 10 秒，并据 UI 数据把服务端 7 秒调为 6/8 秒；
4. 音画同步是否持续无漂移；
5. 实际 `video-only + audio-only` 1080p 直播是否稳定双输入合流；
6. 使用账号可见的登录受限直播验证 Native Messaging Cookie 路线；
7. 找到合适 Bilibili 直播后验证格式、鉴权和 fMP4 浏览器兼容性。

如果没有登录受限直播或分离 1080p 测试源，不应声称这些条目通过。

## 是否适合下一步包装成扩展 iframe

**接口形态适合**：播放器资源自包含、媒体走 localhost HLS、敏感数据走 Native Messaging，后续可以把同一播放器打包为扩展 viewer 页面并用 extension-origin iframe 覆盖原播放器区域。

**现在还不应正式包装**：先完成 Chrome/Edge 30 分钟、登录受限直播、真实分离音视频 1080p 和延迟校准。通过后再做 iframe 覆盖、原播放器暂停/静音与停止恢复。
