# 仓库设计

这是一份长期有效的设计文档，不是某轮工作的报告。它回答一个问题：**怎么让这个仓库在几年后仍然能被陌生人用起来。**

## 一条硬规则

> `git clone` 之后，照着 README 做，必须得到一个能跑的东西；CI 必须能在干净克隆上通过。

其它所有规则都是这条的推论。2026-09 之前这条不成立，而且没有人发现——因为**本地每一条命令读的都是工作区，从来不是索引**。修复过程见 git 历史 4a1f73a..1edd548。

## 三层划分

| 层 | 判据 | 例子 |
|---|---|---|
| **跟踪** | 别人需要它才能构建、运行、理解、贡献 | 源码、测试、脚本、原型、字体、文档、带校验和的供应商二进制 |
| **忽略** | 可以再生，或者属于某台机器 | `release/`、`build-desktop/`、`runtime/`、`output/`、`.venv*/`、`node_modules/`、cookies、密钥、模型权重 |
| **不进仓库也不忽略** | 不该存在 | 无。任何东西要么跟踪要么忽略；「悬空」就是 bug |

第三条是这次出问题的根源：415 个前端文件里有 408 个既没被忽略、也没被跟踪。它们只是**从来没被 `git add` 过**，所以既不会报错、也不会被注意到。

**判断某个东西该跟踪还是该忽略，只有一条测试**：丢掉它之后，一个陌生人还能不能把项目跑起来？字体能（运行需要它）→ 跟踪。打包产物不能（能再生）→ 忽略。486 MB 的模型 checkpoint 能（那是实验产物）→ 忽略。

## 两道自动门禁

人的注意力会漏，所以边界必须由脚本守。

### `npm run guard:release` — `scripts/release-guard.js`

扫**所有已跟踪文件**，拒绝：私密运行时路径、凭据赋值、媒体/日志/缓存/agent 状态目录。

它守的是「别把秘密提交上去」。因为只扫已跟踪文件，所以它同时也是「你即将提交的东西」的最后一道检查——`git add` 之后跑一次，等于在提交前过了一遍全部内容。

### `npm run guard:tracked` — `scripts/check-repo-completeness.js`

解析项目**引用到的**每一个路径，要求它要么已跟踪、要么被明确忽略：

- `package.json` 的每个 script 和 `main`
- `.github/workflows/` 下的**每一个** workflow
- `web-player/index.html` 的 `<script src>` / `<link href>`
- CSS 里的每一个 `url()`（这条覆盖 396 个字体切片）

它守的是「别漏掉东西」。这道门禁在加上之前报了 **414 个**问题路径；正因为引用关系是有限的、可枚举的，这个失败模式从此不会再悄悄回来。

两道门禁都在 `npm run ci` 和 GitHub CI 里跑。

## 为什么靠 CI 而不是靠自觉

GitHub 上的 `actions/checkout` **本身就是一次干净克隆**，所以「测试在 CI 里跑」这件事，等于每一轮都在自动验证「跟踪的文件是否自足」。

这也解释了这次为什么能藏那么久：本地跑什么都是绿的，因为本地读的是工作区。**唯一能发现「工作区能跑、克隆不能跑」的办法，就是真的去克隆一次。** 所以遇到可疑情况时，`git clone` 到临时目录再跑一遍，是最便宜也最有效的验证手段——本文档里的所有数字都是这么来的。

## 长期运行的建议

**分支与发布**

- `main` 永远保持可发布。破窗一旦允许，门禁就形同虚设。
- 安装包**不进仓库**（`release/` 已忽略）。走 GitHub Releases：`.github/workflows/desktop.yml` 已经在把未签名安装包作为 artifact 产出，签名和分发审查完成前不要发布。
- 供应商二进制（`yt-dlp.exe` 17 MB）留在仓库里，用相邻的 SHA-256 文件固定，并由 `bootstrap.ps1` 和 CI 各自校验一次。更新时连校验和一起改。
- 版本号用 `package.json` 的 `version` + git tag。现在 `engines` 已经明确写死 `node >=22.12.0`，`bootstrap.ps1` 也照此校验。

**二进制资产**

字体（9.32 MB，其中 Noto Sans SC 9.24 MB）**提交进仓库**，理由：

1. clone 即可运行、可离线构建、CI 可复现、没有第三方供应链依赖。
2. 「构建时拉取」不是免费的替代方案——它需要把拉取工具也纳入跟踪、在 bootstrap 里加一步、再引入网络依赖，换来的是每次 CI 都可能因上游抖动而失败。
3. 这个产品的形态决定了它优化不掉：一般项目可以对 UI 文案做字体子集化（581 个汉字 → 几十 KB），但 LingerLens 的核心场景就是显示**任意中文**的实时字幕和弹幕，必须全量覆盖。

代价是字体升级时旧 blob 永久留存（每次约 +9.3 MB）。缓解办法就是现在这套：`scripts/fetch-fonts.py` 锁定版本，`--check` 验证每个 `@font-face` 都有文件兜底，极少更新。

字体是 **OFL-1.1**，再分发必须附授权声明——见 `THIRD_PARTY_NOTICES.md` 的 Bundled web fonts 一节。**新增任何第三方二进制或字体时，同一件事必须照做**，否则就是许可证违规。

**重新打包**

- 只改了 `web-player/` 用 `npm run desktop:sync-ui`（秒级）。改了 `desktop/main.cjs`、`desktop/backend.cjs` 或任何 Python 才需要 `npm run desktop:refresh`（`desktop:backend && desktop:pack`）。
- **`desktop:sync-ui` 必须同时写两份**：`build-desktop\backend\lingerlens-backend\_internal\web-player`（重打包读的那份）和 `release\win-unpacked\...\web-player`（正在跑的那份）。`desktop:pack` / `desktop:dist` 是从**冻结产物**里取 web-player 的，不是从源码树取——只写后者的话，下一次重打包会静默把它覆盖回旧 UI。这个坑真的发生过：改完 UI → `sync-ui` → `dist`，安装包里出来的是改动之前的 `index.html` 和 `style.css`，而且一句警告都没有。现在脚本自己写两份，并跳过尚未构建的那一份。
- **`desktop:refresh` 必须先把 dev 实例关掉。** 它会重新解压 Electron 到 `build-desktop\electron\`，而正在运行的 dev 进程锁着那些 DLL，必然失败在 `PermissionError: [Errno 13] ... d3dcompiler_47.dll`。这个报错不提 Electron、也不提 dev 实例，很容易被误读成磁盘或杀软问题。
- **`desktop:dist` 之前也要关掉打包版**，理由同上（它要重写 `release\win-unpacked`）。
- `release\LingerLens-0.1.0-windows-x64-setup.exe` **不会**被 `desktop:refresh` 更新，它是 `npm run desktop:dist` 的产物。

**发行版怎么更新到用户手里**

分发是**两个仓库**：源码在私有的 `Antiranger/LingerLens`，安装包和更新清单在公开的 `Antiranger/LingerLens-releases`。应用里没有任何凭证，它只需要一个匿名的 HTTPS 地址。

更新检查（`desktop/updater.cjs`）**没有用 electron-updater**，是手写的无依赖实现。原因是 `desktop/audit-package.cjs` 用一份显式白名单限制 `app.asar` 里能有几个文件，而 electron-updater 会拖进一串传递依赖——要么构建失败，要么被迫把白名单开个口子。它需要的东西（fetch、版本比较、SHA-256、spawn）全在 Node 标准库里。

**信任锚是清单里的 SHA-256。** 下载到 `<目标>.part`，校验通过才 rename 成正式文件——所以安装程序路径「要么不存在、要么字节已被验证」。校验失败会删掉临时文件并且**不执行**。已在真机实测：清单里的 sha256 与载荷不符时，应用报告 `checksum mismatch` 并且**没有退出**（退出就意味着它去跑安装程序了）。只信任 https，外加 loopback 上的 http——能在 127.0.0.1 上监听的东西本来就能在这台机器上执行代码，放行它换来的是「不用发一次真 release 就能端到端测」。

清单由 `scripts/make-update-manifest.js` 对**刚构建出来的那个文件**求哈希生成，不是人手动填的。`.github/workflows/release.yml` 由 `v*` tag 触发，tag 与 `package.json` 版本不一致就直接失败，然后发到公开仓库。它需要一个 `RELEASES_TOKEN` secret（细粒度 PAT，对 releases 仓库有 Contents 读写）——workflow 自带的 `GITHUB_TOKEN` 只能写当前仓库。

**要判断一份构建是不是当前版**，诊断栏的「复制诊断信息」里带 `构建: 0.1.0 · 2026-09-15 12:37 · packaged`，展开面板右下角也直接显示版本和构建时间。`app.getVersion()` 单独一样分不出两个 0.1.0，所以取的是**可执行文件自己的 mtime**——这正是 2026-09-15 那次「为什么发行版看起来没有诊断栏」的答案：快捷方式指向的是 09-08 的安装版。

**还没做完的**：`RELEASES_TOKEN` 还没配，第一个 release 还没发，所以真实的更新链路尚未跑通一次。在那之前用户仍然只能手动重装，只是应用会自己发现新版本并一键下载安装了。

**贡献者路径**

- `README.md` 的 quick start 必须是新人实际会走的那条路，且必须与 `bootstrap.ps1`、`package.json engines` 三处一致。这三处不一致过一次（README 说 18+、engines 说 22.12+），已在 1edd548 修掉。
- `CONTEXT.md` 是领域术语表（什么是 Language Tag、Target Language、UI Locale）。改领域概念时同步更新它，不要让术语在代码里各自漂移。

## 诊断：出问题时看哪里

**诊断栏（用户可见的报错出口）**

诊断住在**顶栏里**，不是另起一行：`.topbar-status` 原本是 `flex: 1` 的一片空白（状态徽章和时间右边、语言切换左边那一大块），控件就填在那儿。收起时占 0 额外高度 —— 实测顶栏仍是 71px，`.hero` 紧接着顶栏，文档高度在收起/警告/展开/再收起四种状态下完全相同。摘要在状态徽章旁边，回答的正是「为什么变红了」。

只有 error 级记录会把明细**自动拉下来**；warn 和 info 只更新摘要与计数，不抢屏幕。明细用 `position: absolute; top: calc(100% + var(--bw))` 挂在 `.topbar` 下方 —— `.topbar` 是 sticky，本身就是定位上下文，所以明细横跨整幅顶栏宽度（实测 2545px）并且**覆盖内容而不是把页面推下去**。`calc` 里那个 `var(--bw)` 不能省：`top: 100%` 是顶栏的 padding box，不含它 3px 的下边框，省掉就会把分隔线盖住。

展开后是：一句人话交代（黄色 callout）+ 可滚动的原始记录列表 + 「复制诊断信息」。复制出来的是一份纯文本报告（时间、界面语言、会话状态、目标延迟、页面、UA + 每条记录一行），用户可以直接贴进 issue。

记录有三条来源，合成同一条时间线：

1. **渲染进程事件** —— `player.js` 里所有用户可见的报错都经过 `showError()`，所以那**一个**钩子就覆盖了绝大部分故障（`diagnosticsBar?.push("error", "ui", …)`）。会话级错误另有一条（`updateStallOverlay`，source `"session"`）。
2. **后端 `/api/logs`** —— `companion/logbook.py` 的环形缓冲，容量 500，`GET /api/logs?afterSeq=N` 返回 `{records, maxSeq, dropped, sessionId}`。渲染进程每秒轮询一次（隐藏时 5 秒），`seq` 去重，`sessionId` 变化即视为后端换进程并重置游标。
3. **`ffmpegLogTail`** —— 不再单独处理：后端在会话出错时把 `core.py:1137` 那 6 行 FFmpeg 尾部输出**作为 source `"media"` 的 error 记录**发进同一本账。

摘要选哪一条是刻意的：`logbook.lead()` 优先挑「能给出人话交代」的那条。后端会把 FFmpeg 的进度行也标成 error，按时间取最新会让 `frame= 12 fps=0 …` 顶掉真正的 `FFmpeg exited with code 1`。这条规则有测试守着。

环形缓冲会淘汰旧记录，所以 `seq` 出现缺口就是真丢了行。客户端只在**会话中途**报这个缺口（`records[0].seq > afterSeq + 1`）；首轮不报，那只是页面比后端晚开。实测这个缺口在挂起标签页时很容易出现：轮询降到 5 秒，500 条写满只是时间问题。

`logbook.py` 在写入端做清洗，而不是指望每个调用点自觉：CR/LF 拍平、**URL 全部替换成 `<REDACTED_URL>`**（签名过的流地址在有效期内等同于凭证）、控制字符剔除、单条上限 400 字符。级别会被夹到 info/warn/error 三值，未知级别降级成 `info` 而不是丢弃——一本会凭空发明 error 的日志会训练读者忽略它。500 分支只记异常类型和 aiohttp 的路由模板，不记异常文本、更不记 `request.path`，所以 `/_private-hls/<token>/…` 的私有令牌不可能漏进 UI。

UI 文案 25 个 key 覆盖 zh-CN / en / ja / de / ru。zh-CN 走两条路：静态节点由 `i18n.js` 的 BINDINGS 在初始化时从 DOM 捕获，动态的（级别标签、人话提示、复制报告的表头）在 `ZH_DYNAMIC` 里。**漏掉 `ZH_DYNAMIC` 会让中文用户看到英文**，因为 `t()` 在 zh-CN 分支上先查 `DICT.en` 再回退到 fallback。

**开发日志文件 `LINGERLENS_BACKEND_LOG`**

上面那本账是给 UI 看的，而且**只在内存里**：环形 500 条，窗口一崩、页面一刷就没了，靠「出问题时记得点复制」在一场长会话里是不牢靠的。这个开关把证据落到磁盘上：

| 取值 | 行为 |
|---|---|
| 不设 / `0` / `off` | 什么都不写（默认） |
| `1` / `on` / `true` | 只转发到主进程 stderr |
| 其它任意值 | 当作文件路径；**父目录自动创建**，终端同时 tee 一份 |

一条命令就可以带着它启动，目录和带时间戳的文件名都不用手管：

```
npm run desktop:dev:log          # 额外参数照常转发，例如 -- --remote-debugging-port=9222
```

**一个文件里有两股流**，刻意不合并成同一种格式：

1. **后端 stderr** —— Python / FFmpeg 打印什么就是什么。
2. **渲染进程的诊断时间线** —— 一行一条记录，形如
   `2026-09-15 14:31:27  [error] ui  <message>`，所以能直接 grep。

第 2 股走的是既有的自定义协议拦截（`protocol.handle` 里 `/api/app-update` 旁边那条 `/api/diagnostics`）。渲染进程在沙箱里没有文件系统，加 preload 或 IPC 都要**扩大它的权限**，而复用这条已有通道一点权限都不用加。渲染进程先 `GET /api/diagnostics` 问开没开，没开就一条都不发；`POST` 返回**真正落盘的行数**，渲染进程只有拿到这个数才推进自己的游标（用插入序而不是数组下标或时间戳——环形缓冲会淘汰，后端记录的时间戳还可能落在已发出的记录之前）。

**级别 token 不翻译**（永远是 `error` / `warn` / `info`）：这份文件是拿来 grep 和转交的。给人读、要贴进 issue 的是「复制诊断信息」，那一份才翻译。

文件里的行是**插入序**而不是时间序，所以偶尔和复制报告的顺序不一样。实测同一个故障，两边长这样：

```
文件       16:24:25  [error] ui       yt-dlp format probe failed …
           16:24:25  [warn]  request  rejected POST /api/probe: …
复制报告   16:24:25  [警告] request  rejected POST /api/probe: …
           16:24:25  [错误] ui       yt-dlp format probe failed …
```

后端记录要等下一次轮询（1 秒，页面隐藏时 5 秒）才进渲染进程，所以它的**插入序**晚于它自己的**时间戳**。这不是 bug，反而有信息量：插入序说的是"客户端什么时候知道的"，而每行都带时间戳，要按时间读自己排一下就行。改成时间序得让客户端分页大小和后端上限永远一致——为一个纯观感的问题背一个不变量不划算。

**三种失败都必须出声**，这是这块设计的重点：

- 要了文件路径但目录不存在——以前 `appendFileSync` 抛错被吞掉、退回终端，而 Electron 在 Windows 上是 GUI 子系统程序，那个终端是个黑洞：可以安静地跑一整场实测、产出 0 字节、没有任何提示。现在父目录自动创建。
- 路径本身写不进去（权限、盘符、路径指向一个目录）——错误记在日志对象上，经 `GET /api/diagnostics` 回到 UI，诊断栏亮 warn 并说明原因。
- 跑到一半文件消失（磁盘满、被清理）——第一次写失败就置 `enabled: false` 并记下原因，不会继续假装在记。

一个反直觉的 Windows 细节值得记下来：**「路径是个目录」既不能靠 `openSync(p, 'a')` 也不能靠 `appendFileSync(p, '')` 探出来**——两者都会成功返回，因为零字节的追加根本没走到 write，只有真正的写入才报 `EISDIR`。所以 `devlog.cjs` 额外做了一次 `statSync().isDirectory()`。这个坑是我自己的测试抓出来的：测试断言目录路径必须失败，结果它"成功"了。

**它接不住什么，以及为什么两股流都要开。** 实测（`.scratch/lingerlens-audit/where-do-failures-go.py`：驱动真实 UI 粘贴一个失效链接再点「准备」）——这类故障**一个字节都不会进 stderr**：

```
backend log before: 195 bytes
probe settled after 11s: 'yt-dlp format probe failed (exit 1): ... This video is unavailable'
backend log after:  195 bytes  (delta 0)      ← 文件一个字都没多
```

因为它压根没经过 Python：`yt-dlp` 的失败由后端记进 logbook，`/api/logs` 轮询送回渲染进程，`showError()` 把它推上诊断栏。**stderr 那条路只接得住 Python 异常和 FFmpeg 输出。** 所以完整现场 = 这个文件（两股流）+ 复制报告。

**要判断渲染进程内部发生了什么**，仍然只有两条路：窗口里 `Ctrl+Shift+I` 开 DevTools，或者加 `--remote-debugging-port` 用 CDP 直接问渲染进程。

## 不变量

上面所有规则可以压缩成一句可检查的话：

> **仓库里不存在「既没被跟踪、也没被明确忽略」的文件。**

用 `git ls-files --others --exclude-standard` 一查即知，空输出就是健康。这个不变量成立时，「工作区能跑、克隆不能跑」就不可能发生——因为二者内容相同。它是靠 `guard:tracked` 维持的；那个脚本加进来时报了 414 个问题路径。

## 已知的未决问题

1. **`.archive/`**（陈旧的源码快照）按设计不跟踪，只留 `README.md` 说明原因。别再往里加东西。
2. **git 历史曾经有 5.3 GB 垃圾**（106 个中断的 `tmp_obj_*` 写入 + 883 MB 不可达松散对象），来自被中断的大文件 `git add`。已用 `git gc --prune=now` 清到 28.6 MB。注意 `git clone` 的本地硬链接会把这堆垃圾一起复制，所以本地 clone 显示 5.3 GB 而远程 clone 只有 59 MB——**判断「别人克隆要多久」必须用 `git clone --no-local`，或者看 `git count-objects -vH` 的 `size-pack`。**
3. **日文/俄文的字体**靠系统字体（Yu Gothic / Segoe UI），只打包了 Noto Sans SC。中日双语项目里两种语言的字形来源不一致，值得明确是有意还是遗漏。
4. **`.scratch/` 里 9,500 多个文件仍在磁盘上但已整体忽略**，其中含 486 MB 的模型 checkpoint。它们不会被提交，但也不会被备份。想留着就自己复制出去。
5. **`docs/notes/` 现在承载了原来散在 `.scratch/` 的 88 份工程记录**（调研报告、spec、逐次会话日志）。它是「工作记录」，不是「设计文档」；`docs/` 根目录才是设计文档。新记录请往 `docs/notes/` 放，不要再开 `.scratch/*.md`。

## 这台机器上的测量陷阱

记在这里，因为已经骗过我两次：

- **OS 级截图不可信。** 这个 Electron 窗口用 `CopyFromScreen` 截出来，凡是 GPU 合成的图层都是黑的——聊天栏整块都在里面。用它判断「某个面板在不在」会得出完全错误的结论。
- **窗口物理像素 ≠ CSS 像素，而且不是简单的 DPI 倍数。** 这个窗口 `GetWindowRect` = 2575 物理像素，但渲染进程报 `innerWidth` = 2560、`devicePixelRatio` = 1.5。按 1.5 去除会得到 1707 这个错误的 CSS 宽度，进而误判断点。
- **正确做法**：给应用加 `--remote-debugging-port` 起一个实例，用 CDP 连上真实渲染进程直接问。脚本见 `.scratch/lingerlens-audit/inspect-live-window.py`（该目录已忽略；这段方法本身要保留，已记在此处）。
