# 开源前检查报告（2026-09-20）

仓库当前仍是私有的 `Antiranger/LingerLens`。本轮检查以当前工作区为准，先在 `.scratch/open-source-readiness-20260920/` 保存了工作树快照、SHA-256 清单、初始 Git 状态和验证日志；已有未提交的 Provider/字幕工作已保留。

## 已完成

- 补齐简体中文、英语、日语、德语、俄语的项目首页和完整使用指南，并加入文档索引、服务参考、开发、发布、支持、行为准则和变更记录。
- README 的运行命令与实际桌面/浏览器入口对齐，明确 Windows x64 预览版、五种界面语言与 Provider 语言能力的区别。
- 开发 Companion 的所有路由增加同源 loopback 请求保护，覆盖跨站读取和简单 POST 控制请求；新增回归测试。
- 更新器拒绝带凭据的 URL、非法/越界版本标识、非安全整数大小及超长分块，并避免刷新/下载并发状态复用；新增回归测试。
- 发布工作流改为在独立下载仓库创建 **draft**，需要维护者审阅后手动发布；源代码仓库不会因标签推送而自动公开。
- release guard 覆盖 `.scratch`、归档/环境/凭据/ Cookie 路径，并扫描大型文本和文档中的 Provider token；冻结依赖许可证检查改为未构建时失败；文档链接和五语文件检查加入 CI。

## 验证证据

- CI 组成命令已按当前工作树复跑：HLS Companion 发现并运行 66 个 Python 测试文件、780 项测试，浏览器侧 22 个文件、207 项测试；根 Node 测试 36 项通过。Playwright 与 Streamlink 的可选路径按环境规则跳过时会明确标记。
- `npm run guard:licences:frozen`：通过，检查 13 个冻结 Python distribution、原生库和随附许可证文本。
- 同源路由专项测试：3 个通过；更新器和 release guard 专项测试：36 个通过。
- `npm audit`：0 个已知漏洞；`pip-audit` 对构建依赖：未发现已知漏洞；`git diff --check`：通过。
- Gitleaks 扫描 155 个提交发现 8 个 generic-api-key 命中，均位于性能脚本的 `key="rvfc_gap_*"` / `key="raf_gap_*"` 指标名，不是确认的凭据；发布前仍应由维护者复核历史扫描。

## 发布前仍需维护者完成

1. 复核完整 diff 和当前未提交 Provider 工作，再决定拆分提交和版本号。
2. 解决 `THIRD_PARTY_NOTICES.md` 已记录但尚未确定的 `elevate.exe` 许可证问题；在此之前不要分发安装包。
3. 安装 Playwright 依赖后完成真实浏览器 UI/全屏/语言切换烟测；另行完成真实直播、Cookie、ASR、翻译、账单、断网、睡眠恢复和干净安装/升级验证。
4. 对历史中的任何真实密钥执行撤销/轮换，并由维护者确认 GitHub Secret Scanning 和发布仓库权限。
5. 在发布工作流生成 draft 后，检查安装包、manifest、SHA-256、第三方声明和对应源码交付说明，再手动发布。macOS 与 Android 仍不在当前发布范围。
