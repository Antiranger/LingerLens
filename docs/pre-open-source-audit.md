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
| `docs/audit-2026-09-core-algorithm-and-stability.md` | 一段 traceback 里带着作者机器上的 Python 安装盘符路径 |

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

## 3. 许可证合规 —— 已于 2026-09-15 补齐

这一节比上面都重要。**下面标「跟踪」的三项，一旦仓库公开就立刻触发**，跟发不发安装包无关。

### 3.1 已修复

| # | 组件 | 原问题 | 处理 |
|---|---|---|---|
| **G1** | FFmpeg 9.0.1 | GPLv3，且 GPLv3 §6 要求的对应源码没有提供 | **换成 LGPL 构建**。产品只用 `-c copy`，从不编码，所以 LGPL 构建功能等价——已用真实媒体链路验证（H.264+AAC MPEG-TS → `-c copy` + `aac_adtstoasc` → fMP4 EVENT）。GPL 义务随之消失 |
| **G2** | `yt-dlp.exe` 内含 **mutagen** GPL-2.0-or-later | 零声明 | 完整清单已写入声明（含 `requests` 2.34.2、`certifi` MPL-2.0、`cffi` **MIT-0**、`pycryptodomex` **Unlicense AND BSD-2**），许可证文本随 exe 一起发布，并写明**由本项目**给出的三年源码书面要约（上游自己的要约不能替我们履行） |
| **G3** | 396 个字体缺 OFL 全文 | OFL 条件 2 要求文本随副本一起走 | `OFL.txt` 放在**字体目录里**，同时进 `licenses/` |
| **G4** | `hls.js` 缺 Apache-2.0 全文 | §4(a) | `LICENSE.hls.js` 放在**同目录**。顺带发现上游 LICENSE 里还有两条**本仓库完全没有**的署名：`Copyright (c) 2017 Dailymotion`，以及 `mp4-generator.js`/`exp-golomb.ts` 派生自 videojs-contrib-hls（`Copyright (c) 2013-2015 Brightcove`）——Apache-2.0 §4 要求这两条 |
| G5/G6 | OpenSSL 3.5.7、libffi | 从未被提及 | 已列入声明，文本进 `licenses/` |
| G7 | UniDic 三许可未声明选择 | 接收方可按 LGPL/GPL 理解 | 明确**选择 BSD-3-Clause 分支** |
| G10 | `fugashi` 版本写 1.4.0，实际 1.5.2 | 声明漂移 | 已改正，并加 CI 门禁防止再漂移 |

### 3.2 门禁

`npm run guard:licences` 在**每次 CI** 中运行：任何被再分发的东西没在声明里点名、或某个必需的许可证文本缺失/内容不对，直接失败。
`npm run guard:licences:frozen` 额外遍历冻结进桌面后端的每一个 Python 发行包，并检查许可证文本是否**真的贴在组件旁边**——`companion.spec` 曾经只把 `yt-dlp.exe` 打进去，源码树里加的许可证文件根本到不了构建产物，正是这道检查抓出来的。两个 workflow 都接上了。

### 3.3 仍未解决

- **`elevate.exe`**（electron-builder 的 NSIS 组件）：上游仓库没有 LICENSE，二进制里也没有许可文本。**无法确认**，已在声明里如实标注而不是猜。
- **`typing-extensions` 4.16.0**：确实被打进 `yt-dlp.exe`，但 yt-dlp 自己的 `THIRD_PARTY_LICENSES.txt` 漏了它（那个版本不定义 `__version__`）。我们补上了 PSF-2.0 的说明。
- **Unicode/CLDR 许可证版本**：`companion/data/languages.json` 由 langcodes 生成，具体适用哪个版本的 Unicode 许可证没有确定。
- yt-dlp 自带的 `THIRD_PARTY_LICENSES.txt` 是**跨构建变体的超集**，列了本 Windows 构建里并不存在的 Readline/ncurses/Tornado 等。多声明是安全方向，保持原样，但在声明里写明了这个事实。


---

## 4. 仓库卫生

- **会破坏克隆的硬编码路径：0 个。** 所有启动脚本用 `$PSScriptRoot` / `%~dp0` / 相对路径；三个 workflow 都是 `windows-latest`。
- 文档里有 21 行 `F:\Projects\LingerLens` 之类的绝对路径，**都是文档里的示例，不影响克隆**，但其中 7 个指向 `.scratch/`、`.playwright-cli/` 的链接在克隆里是死链。
- 历史里曾有约 1.2 MB 的 `.scratch/` 媒体碎片（MPEG-TS）。属于整洁问题而非泄漏，已在 2026-09-15 的历史重写中一并清除。
- 仓库 `.git` 28.4 MB，`garbage: 0`。

---

## 5. 这份审计没查到什么

诚实说明覆盖边界：

- **远端未拉取。** 审计跑在本地 pack 上；`origin/main` 与本地一致，但没有 fetch，远端独有历史未覆盖。
- **396 个字体和 4 个媒体 blob 只做了字节匹配**，没有解码/转码；`yt-dlp.exe` 只做字节匹配，没有作为程序审计。
- **`elevate.exe` 的许可证没查出来**（上游无 LICENSE，二进制内无许可文本）。
- **字体 woff2 的 `name` 表里是否保留了 OFL 元数据没验证**（需要 fontTools + brotli 解 Brotli 压缩的 name 表，环境里没装，也没有为此安装东西）。即使有，单独放一个 `OFL.txt` 仍然是更稳妥的做法。
- **git 之外的东西没查**：磁盘上的活 `.scratch/`、`runtime/providers.json`、未提交的 `.env`、shell 历史。
- **当时没有执行任何历史重写。** 上面第 1–5 节的结论都基于重写之前的仓库状态；重写本身与其验证记录见第 6 节。

---

## 6. 开源前必须做的（按优先级）

1. ~~**提交身份止血。**~~ **已完成** —— `user.email` 已换成 GitHub noreply 地址，新提交不再累积真实邮箱。
2. ~~**补 G2/G3/G4 的许可证文本与声明。**~~ **已完成**，并加了 CI 门禁。
3. ~~**G1：** FFmpeg 换成 LGPL 构建。~~ **已完成。**
4. ~~**重写历史。**~~ **已完成（2026-09-15）。**

### 重写做了什么，以及怎么验的

用 `git filter-repo` 一次完成三件事：

| 操作 | 结果 |
|---|---|
| `--mailmap` 换掉提交身份 | 43/43 提交现在都是 GitHub noreply 地址 |
| `--invert-paths --path .scratch` 整条路径删除 | 含真实公网 IP 的 4 个 `.err`、约 1.2 MB 媒体碎片、全部 scratch 中间产物一起消失 |
| `--replace-text` 擦除机器路径 | `C:\Users\<user>\`、`D:/Users/<user>/`、`D:\<python>\`、被泄漏的 IP |

**验证方式是从远端全新克隆**（`git clone --no-local`），也就是别人实际会拿到的东西：

```
commits 43 · tracked files 765 · identities 只有 noreply
MSI-NB           0 commit(s)        (重写前 39)
154.36.135.209   0 commit(s)
huxy2223         0 commit(s)
.scratch/        0 commit(s) / 0 objects
unreachable      0
```

**内容完整性**：新旧的 HEAD 文件列表完全相同（765 = 765），逐文件比对 765 个里只有 1 个内容不同——就是本文档自己那次有意的措辞修改。其余 764 个字节级一致。

**备份**：重写前做了完整的 mirror clone，放在仓库外的 `LingerLens-backup-pre-rewrite-<时间戳>`。确认无误后可以删掉；删掉之后旧历史就真的没有了。

**一个诚实的说明**：force-push 只是让远端的分支指向新历史。GitHub 在一段时间内仍可能保留旧的不可达对象，**在旧历史被克隆过的地方也仍然存在**（本地那份备份就是）。这个仓库是私有的、只有一个贡献者，所以实际暴露面就是这些副本。要彻底抹掉，需要联系 GitHub Support 做 GC 并删除所有旧副本。

### 仍然未解决的

- 给产品加一个能看到许可证的入口（"关于"），别让声明文件只能靠翻安装目录找到。
- `elevate.exe` 的许可证需要向 electron-builder 上游确认。
- Unicode/CLDR 的许可证版本需要确定。
- **远端仓库仍是私有的**，公开的 releases 仓库也已收回成私有。真要发布时，两步都需要显式操作。
