# LingerLens 使用指南

[文档目录](../README.md) · 基线 0.1.0 · 更新日期 2026-09-29

## 运行

Windows 默认安装目录为 `%LOCALAPPDATA%/Programs/lingerlens`，用户数据另存于 `%APPDATA%/LingerLens`。0.1.1 起采用安装向导，支持选择安装目录。

首次运行时没有已配置的模型、API Key 或导入的 Cookie，请在模型设置中添加自己的连接。升级或重装会保留这台电脑之前保存的用户数据，因此开发电脑上看到旧配置不代表它包含在安装包中。需要录制空白界面时，请使用独立的新用户数据目录；不要把用户数据目录或其中的备份加入发布包。

桌面构建支持 Windows x64、macOS arm64 和 x64。可下载的安装包见 [Releases](https://github.com/Antiranger/LingerLens/releases)：Windows 运行 EXE；Mac 按芯片选择 DMG，打开后拖入「应用程序」。预览包尚未签名，Mac 包尚未公证；请使用 `SHA256SUMS.txt` 核对下载文件。

安装包包含 Electron、Python、FFmpeg/ffprobe、yt-dlp、字体和日语词典，无需额外安装开发工具。云服务账户、API 费用，以及可选本地 Whisper 服务和模型权重需自行准备。Mac 数据目录为 `~/Library/Application Support/LingerLens`。Mac 更新时下载新版 DMG 覆盖应用；内置安装更新器仅适用于 Windows。

浏览器开发模式需要 Python 3.11+、Node.js 22.12+、FFmpeg/ffprobe、Chrome 或 Edge：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1 -Python .\.venv\Scripts\python.exe
.\start-lingerlens.cmd -Prototype -Python .\.venv\Scripts\python.exe
```

打开 `http://127.0.0.1:8765/`。Bootstrap 加 `-CheckOnly` 只检查；不加 `-Prototype` 时启动已有桌面 EXE。不要向局域网或反向代理暴露 Companion。

## 配置模型

在「连接与密钥」中新建并选择配置，使用服务方准确的模型 ID、地址和鉴权。识别与翻译默认分开；启用服务原生双语时可由一个会话提供两者。翻译协议包括 OpenAI 兼容、Qwen-MT、Anthropic Messages、Google Gemini；识别适配器包括 DashScope、Soniox、Deepgram、OpenAI、AssemblyAI、火山引擎、ElevenLabs、Speechmatics 和腾讯。协议已实现不等于每个模型、语言、账户都验证通过。

本地 Whisper 兼容识别需要自行运行服务，常见地址为 `http://127.0.0.1:8000/v1`，程序会请求 `/audio/transcriptions`。是否支持自动检测、混合语言和具体字幕语言由所选服务决定；五种界面语言不代表每个识别服务都支持五种语言。费用缺数据时显示不可估算，不按零处理。

## 播放直播与 Cookie

粘贴 YouTube、哔哩哔哩房间或 Twitch 频道 HTTPS 链接，探测后优先选 H.264/AVC 视频和 AAC 音频。目标延迟为 11–60 秒，默认 15 秒，实际延迟还受平台和网络影响。使用播放器全屏按钮让字幕一起全屏。公开直播先不导入 Cookie；导入窗口支持平台请求头、表格或 Netscape 导出，哔哩哔哩登录需要 `SESSDATA`。Cookie 不能绕过 DRM、付费、地区限制或反爬。

## 看懂延迟数字

**本地分片延迟**表示最新完整本地视频段到当前画面的距离，不含平台本身的延迟及尚未完成的视频段。缺少测量时显示 **—**。**译文到达余量**为正表示提前收到，为负表示已经迟到；只统计近期前台正常播放时的新译文，暂停、拖动、加速追赶或数据过期时旧建议失效。按钮如写着**将目标延迟调到 19 秒**，点击后总目标就是 19 秒。建议来自近期观察，不能保证之后每条字幕都按时到达。

## 数据、隐私和排错

云端识别发送音频，翻译发送字幕和有限上下文；服务原生双语可能由同一服务接收音频和翻译指令。桌面数据通常在 `%APPDATA%/LingerLens`，密钥会在「连接与密钥」中显示，共享屏幕时不要打开，也不要发布运行目录、签名直播 URL 或原始诊断记录。更新器校验清单中的大小和 SHA-256，但不验证发布者签名；下载渠道不存在时播放仍可继续。

EXE 不存在时使用 `-Prototype`；缺模块要用启动 Companion 的同一个 Python 安装依赖；端口占用时关闭对应 Companion 或换端口，不要结束所有 Python；403/无清晰度检查账户、地区和 Cookie；无字幕检查识别配置；无译文检查翻译路线、额度和目标语言。

## 构建与验证

```powershell
py -3.11 -m venv .venv-desktop
.\.venv-desktop\Scripts\python.exe -m pip install -r desktop/requirements-build.txt
npm ci --ignore-scripts
npm run ci
npm run desktop:test
git diff --check
```

这些是本地 fixture 检查，不能证明真实直播、账户、计费或翻译质量。另见[开发](../DEVELOPMENT.md)、[贡献](../../CONTRIBUTING.md)和[发布](../RELEASING.md)。
