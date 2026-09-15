# 开源前审计（2026-09-15）

这份文档回答一个问题：**如果今天把仓库公开，会漏出什么、会违反什么。**

审计方法是三路并行、各自独立：git 全历史逐 blob 扫描、跟踪文件身份信息扫描、第三方许可证清点。下面每一条都写了证据，也写了**没查到什么**——因为一份只说"没发现问题"的报告没有价值，得让人知道那份否定结论覆盖了多少。

本文档**不复制任何真实的敏感值**。

---

## 结论摘要

| 类别 | 结果 |
|---|---|
| 真实密钥 / 令牌 / 私钥 | **全历史 0 个**（947/947 blob 全查，0 个不可达对象） |
| 身份信息（跟踪文件） | 5 处，**已修** |
| 身份信息（git 历史） | 2 类，**只能靠重写历史清除** |
| 许可证合规 | **3 个阻断项**，其中 2 个在跟踪文件里，公开源码即触发 |
| 会破坏克隆的硬编码路径 | **0 个** |

---

## 1. 密钥与凭据：干净

全历史 947 个 blob **全部读过**（546 个按文本读，401 个二进制按字节匹配）。对象库共 1274 个对象，**0 个不可达**——所以「枚举可达对象」就等于「枚举全部」，没有任何东西能靠 amend / rebase / reset 藏起来。

**确认不存在**（在整个对象库里查过，不是"没看"）：

- `.env`、`credentials`、非 example 的 `providers.json`、`auth-snapshot.json`、`control.secret`、任何 cookie 导出、`runtime/**`、`prototype/runtime/`、`.pem`/`.pfx`/`.p12`、`.npmrc`
- `sk-ant-…`、`xox[baprs]-…`、`gh[opsu]_…`、`AKIA/ASIA…`、`AIza…`、`LTAI…`、`AKID…`、JWT、`Bearer …`
- 任何私钥块、任何 `Cookie:` / `Set-Cookie:` 头
- **任何含非空 `apiKey`/`token`/`client_secret`/`password` 值的 JSON** —— 所有真实密钥一律只以**环境变量名**出现（`DASHSCOPE_API_KEY`、`OPENAI_API_KEY`、`TENCENT_SECRET_KEY` …）

`tests/release-guard.test.js` 里的 `sk-proj-…` 是给 release guard 喂的**故意构造的假密钥**；测试里那些 `fake-…key`、`legacy-…key` 同理。别把它们当问题。

---

## 2. 身份信息

### 2.1 跟踪文件里 —— 已修（5 处）

| 文件 | 内容 |
|---|---|
| `docs/live-delay-solution.md` | 本机 FFmpeg 安装路径里的 Windows 用户名 |
| `docs/speaker-intonation-postmortem-and-replan.md` | 一次性临时工作目录路径，**同时泄漏了一个会话 UUID 和会话 slug** |
| `docs/notes/progress.md` ×2 | `%TEMP%` 下的交接文件路径 |
| `docs/audit-2026-09-core-algorithm-and-stability.md` | 一段 traceback 里的 `D:\<python>\lib\...` |

已全部替换为中性写法（`%TEMP%/…`、`<python>/lib/…`、"本机独立安装的那份"），**含义未变**。

### 2.2 git 历史里 —— 只能重写

| | 说明 |
|---|---|
| **提交身份** | 全部 39 个 commit 的 author 与 committer 都是同一个真实个人邮箱。**这是本次审计最实质的一条**：任何人 clone 之后 `git log` 就能看到。改文件没用。 |
| **已删除的 `.scratch` 日志（4 个 blob）** | 内含带签名的 googlevideo 地址，**嵌入了作者的真实公网 IP** 以及 `ei`/`bui`/`spc`/`xpc` 会话令牌。签名已过期（`expire` 早于当前时间），**但公网 IP 不会过期**。 |
| **已删除的 `progress.md` / `task_plan.md`** | 同样的家目录路径。 |

**关键认识：「不发布某个文件夹」不等于「它不在仓库里」。** git 是内容寻址的，任何提交过的东西都能从 pack 里逐字节还原，即使 HEAD 里早已删除。上面那 4 个 `.err` 就是这么被找出来的——它们不在当前版本里，但 `git cat-file blob <sha>` 照样完整读出。

所以在公开之前清除它们**必须重写历史**；而既然要重写，抹具体字符串和删整个文件夹是同样的代价。**不要为了这个去删 `docs/`** —— 那既解决不了问题，又白扔 411k 字的技术记录。

---

## 3. 许可证合规 —— 真正的阻断项

这一节比上面都重要。**下面标「跟踪」的三项，一旦仓库公开就立刻触发**，跟发不发安装包无关。

### 3.1 阻断项

| # | 组件 | 许可证 | 现状 |
|---|---|---|---|
| **G1** | FFmpeg / ffprobe 9.0.1 | **GPLv3**（不是 LGPL） | 许可证全文已随附；但 **GPLv3 §6 要求的「对应源码」没有提供**——现在只有一个指向第三方**二进制** zip 的链接，那不是源码要约。仅安装包触发。 |
| **G2** | `yt-dlp.exe` 内含 **mutagen** | **GPL-2.0-or-later** | **零声明**。整个 `yt-dlp.exe` 只标了 Unlicense，而 PyInstaller 模块表里能读到 `mutagen` 的约 40 个子模块。GPLv2 §1/§3 义务触发。**跟踪在 git 里** |
| **G3** | 396 个 `.woff2` 字体 | OFL-1.1 | OFL 条件 2 要求**随附许可证全文**；现在只有一句"OFL-1.1"和链接，**链接不是副本**。**跟踪在 git 里** |
| **G4** | `hls.min.js` | Apache-2.0 | §4(a) 要求随附许可证副本；缺失。且该文件里还打包了一个**未列出的 MIT 组件**（regenerator-runtime）。**跟踪在 git 里** |

### 3.2 其他缺口

- **OpenSSL 3.5.7**（Apache-2.0）与 **libffi**（MIT）随 PyInstaller 一起发布，从未被提及。
- **UniDic 词典**（260 MB）是三许可 GPL/LGPL/BSD，**BSD 分支可以只用署名义务**，但文档没有声明选择哪一支——不声明的话接收方可以合理地按 LGPL/GPL 理解。
- `fugashi` 文档写 1.4.0，实际冻结的是 1.5.2。
- **`elevate.exe`**（electron-builder 的 NSIS 组件）无署名文件，上游仓库没有 LICENSE，许可证**无法确认**。
- 许可证声明文件虽然随安装包发布了，但**产品里没有任何入口能看到它**（`Menu.setApplicationMenu(null)`，UI 里没有"关于"）。
- **没有任何 CI 步骤校验「冻结进 `_internal` 的包都被声明覆盖了」**，所以 `requirements-build.txt` 可以随便漂移。

### 3.3 好消息

- UniDic 词典**是可以再分发的**（三许可里有 BSD），最初担心的"日文词典不能随包发布"不成立。
- 三个字体包**都没有 Reserved Font Name 条款**，文件名重命名不构成改名。
- 跟踪的二进制只有 `yt-dlp.exe`、`hls.min.js` 和字体，都是正当 vendoring，没有杂物。
- 模型文件一个都没随包发布（`.onnx`/`.pt` 只存在于已忽略的 `.scratch/`）。

---

## 4. 仓库卫生

- **会破坏克隆的硬编码路径：0 个。** 所有启动脚本用 `$PSScriptRoot` / `%~dp0` / 相对路径；三个 workflow 都是 `windows-latest`。
- 文档里有 21 行 `F:\Projects\LagLingo` 之类的绝对路径，**都是文档里的示例，不影响克隆**，但其中 7 个指向 `.scratch/`、`.playwright-cli/` 的链接在克隆里是死链。
- 历史里有约 1.2 MB 的 `.scratch/` 媒体碎片（MPEG-TS），现在已忽略但仍在 pack 里。属于整洁问题，不是泄漏。
- 仓库 `.git` 28.4 MB，`garbage: 0`。

---

## 5. 这份审计没查到什么

诚实说明覆盖边界：

- **远端未拉取。** 审计跑在本地 pack 上；`origin/main` 与本地一致，但没有 fetch，远端独有历史未覆盖。
- **396 个字体和 4 个媒体 blob 只做了字节匹配**，没有解码/转码；`yt-dlp.exe` 只做字节匹配，没有作为程序审计。
- **`elevate.exe` 的许可证没查出来**（上游无 LICENSE，二进制内无许可文本）。
- **字体 woff2 的 `name` 表里是否保留了 OFL 元数据没验证**（需要 fontTools + brotli 解 Brotli 压缩的 name 表，环境里没装，也没有为此安装东西）。即使有，单独放一个 `OFL.txt` 仍然是更稳妥的做法。
- **git 之外的东西没查**：磁盘上的活 `.scratch/`、`runtime/providers.json`、未提交的 `.env`、shell 历史。
- **没有执行任何历史重写。**

---

## 6. 开源前必须做的（按优先级）

1. **提交身份止血。** 立刻把 `user.email` 换成 GitHub 的 noreply 地址，新提交不再累积真实邮箱。**现在只有 39 个提交、一个贡献者，是重写历史最便宜的时刻。**
2. **补 G2/G3/G4 的许可证文本与声明** —— 这三项公开源码即触发，跟发不发安装包无关。
3. **G1：** FFmpeg 要么给出对应源码/书面要约，要么换成 LGPL-only 构建（如 BtbN 的 lgpl 变体）并删掉 GPL 声明。
4. **重写历史**，一次清掉：提交邮箱、4 个 `.err` blob、已删除文档里的家目录路径。
5. 给 UniDic 加一句"选择 BSD 分支"；修正 fugashi 版本号。
6. 给 CI 加一道门禁：冻结进 `_internal` 的发行包若未在 `THIRD_PARTY_NOTICES.md` 中声明，直接失败。
7. 给产品加一个能看到许可证的入口（"关于"），别让声明文件只能靠翻安装目录找到。
