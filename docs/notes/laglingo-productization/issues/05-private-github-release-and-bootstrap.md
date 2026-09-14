# 05 — 私有 GitHub 仓库与一键依赖 Bootstrap

**What to build:** 把当前本地原型整理成可安全协作的 Git 仓库，提供 Windows 一键依赖安装/校验入口、标准开源文档和 CI；通过安全与 clean-clone 验证后，在已认证的 `Antiranger` 账号下创建并推送私有 `LagLingo` GitHub 仓库。

**Blocked by:** 01 — Provider Catalog 与本地 OpenAI-compatible ASR; 02 — 全屏字幕浮窗与目标总延迟; 03 — 平台化 Cookie 导入与可复用播放会话; 04 — ASR 与翻译 Usage 计量及费用估算.

**Status:** complete

- [ ] 在初始化 Git 前先做敏感文件清单和 secret scan；`runtime/providers.json`、API Key、Cookie/auth snapshot、control secret、媒体分片、日志、benchmark 数据、临时/缓存/Playwright 产物不得进入提交历史。
- [ ] 扩展 `.gitignore` 覆盖运行时私密配置与所有生成产物，同时保留脱敏示例配置；通过测试证明 release file list 不包含已知敏感路径或密钥模式。
- [ ] 提供根目录 Windows 一键 bootstrap（PowerShell 为主，可由 `.cmd` 调用），检查/提示 Python 3.11+、Node/npm、FFmpeg/ffprobe、Chrome/Edge，安装锁定的 Python/Node 依赖，校验 vendored yt-dlp，并且可重复运行。
- [ ] bootstrap 不静默安装系统级浏览器或修改凭据；缺失系统依赖时输出明确的官方安装指引和失败码。
- [ ] 根 README 准确描述当前产品（不再声称没有后端/字幕），包含快速开始、模型 Provider、本地 Whisper、YouTube/Bilibili Cookie、费用估算、全屏字幕、目标延迟、测试和安全边界。
- [ ] 增加 MIT `LICENSE`、第三方 notices、`CONTRIBUTING.md`、`SECURITY.md`、示例 Provider 配置和最小架构说明；说明 API Key 在本机模型设置中可见这一明确风险。
- [ ] 增加 GitHub Actions CI，覆盖当前 Node 测试、HLS Companion Python/JS 测试、语法检查和无外部 CDN/secret guard；不在 CI 中执行真实直播或需要凭据的测试。
- [ ] 在临时目录做 clean-clone 等价验证：仅使用仓库文件运行 bootstrap 和自动测试，不依赖开发机未声明的 Python 包或私有 runtime 文件。
- [ ] 初始化 Git，提交前再次运行 secret scan、完整自动测试和 `git status` 审核；不得把已有本机密钥先提交再删除。
- [ ] 使用已认证的 GitHub CLI 在 `Antiranger` 下创建私有 `LagLingo` 仓库，设置 `origin`、推送默认分支，并验证仓库 visibility 为 PRIVATE、CI 工作流已上传。
- [ ] 不自动改为 public；最终交付记录私有仓库 URL、默认分支、bootstrap 命令、测试结果和后续公开前检查清单。
