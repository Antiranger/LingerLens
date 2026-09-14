# Windows 桌面版

桌面版复用现有网页播放器与 Python Companion，通过 Electron 提供独立窗口。
用户安装后双击 LagLingo 即可使用，无需控制台或本地网址。

## 用户依赖与数据

安装包随附 Electron、Python 运行时及依赖、FFmpeg/ffprobe、yt-dlp、语言数据和日语词典。
不会修改系统 PATH，不要求用户安装开发工具；直播和云模型仍需网络。
用户自行提供云模型 Key。可选本地 Whisper 服务及模型不随包安装。
桌面版使用 Cookie 手动导入；现有 Chrome/Edge Native Messaging 扩展目前仅用于开发者浏览器模式。

配置、Cookie 和媒体缓存保存在 Electron 的用户数据目录（Windows 通常为 `%APPDATA%/LagLingo`）。
网页偏好保存在固定 `laglingo://app` 来源中，不受后台动态端口影响。
不自动读取开发目录中的密钥或 Cookie；首次使用需重新配置。升级和默认卸载保留用户数据。
关闭窗口会停止直播、ASR、翻译和后台；重复启动只唤起已有窗口。

## 构建（开发者）

需要 Windows x64、Python 3.11 和 Node.js 22.12+，首次构建联网下载依赖。

```powershell
py -3.11 -m venv .venv-desktop
.\.venv-desktop\Scripts\python.exe -m pip install -r desktop/requirements-build.txt
npm ci --ignore-scripts
npm run desktop:backend
npm run desktop:pack
npm run desktop:dist
```

如果使用 uv，可用 `uv venv --python 3.11 .venv-desktop` 和
`uv pip install --python .venv-desktop/Scripts/python.exe -r desktop/requirements-build.txt`。

`desktop:backend` 校验固定 SHA-256 后下载 Electron 和 FFmpeg，并检查仓库内 yt-dlp 的校验和，
使用 PyInstaller onedir 打包。`desktop:pack` 生成可直接打开的应用目录；
`desktop:dist` 生成当前用户安装的 NSIS 安装包。两者均不会上传 GitHub。
`npm run desktop:dev` 使用 .venv-desktop 和源码；开发模式仍需要 PATH 上有 FFmpeg。
开发解释器可通过 `LAGLINGO_PYTHON` 指定。

## 验证与发布

```powershell
npm run desktop:test
$env:LAGLINGO_SMOKE_OUTPUT = "$PWD/output/desktop-smoke"
& .\release\win-unpacked\LagLingo.exe --smoke-test
```

烟测使用随包 FFmpeg 生成 H.264/AAC 的 fMP4 HLS，验证实际解码播放、窗口、
状态/语言/模型设置接口与偏好持久化，写入截图和结果后退出。
烟测不会连接直播或云模型。还需验证实际直播、声音、字幕同步、全屏、网络中断和睡眠恢复。
干净 Windows 上安装/升级、代码签名和第三方二进制对应源码交付在公开发布前验证。

源码、锁文件和构建脚本进入 Git；`.venv-desktop`、`build-desktop`、`release` 与
`output` 均忽略。NSIS 包仅从显式 Electron 文件清单及构建后的后台目录取文件，
不复制整个仓库，尤其不收录私人 docs、.scratch 或实际 runtime。

若构建机器使用代理，请在当前终端配置 HTTPS_PROXY；首次下载失败时可以重试，
校验通过的压缩包会在 build-desktop 中复用。GitHub 的 Windows desktop build 工作流
只能手动触发，生成构建附件，不自动公开发布。

## 内部生命周期

桌面主进程通过 stdin 私下交付每次启动的随机会话令牌。后台绑定 loopback 动态端口，
就绪信息由 stdout 返回，主进程通过受限自定义协议转发，令牌不交给页面或放进 URL。
桌面模式关闭旧 Native Messaging 控制管道，避免影响已运行的开发服务。
stdin 关闭或 stop 消息触发正常清理；Windows Job Object 在后台异常退出时回收其子进程。
超时强退只定位本应用持有的 PID 树。页面禁用 Node 集成，启用隔离和沙箱。
