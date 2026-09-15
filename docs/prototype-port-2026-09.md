# 桌面端 1:1 移植 `prototype/redesign` 设计原型

日期：2026-09-15
涉及源码：`prototype/hls-companion/web-player/`（`index.html`、`style.css`、`i18n.js`、
`ui-bootstrap.js`、`player.js`、`live-messages-client.js`、`subtitle-window-controller.js`）、
`prototype/hls-companion/companion/server.py`、`scripts/fetch-fonts.py`、两个测试文件。

---

## 1. 一句话结论

桌面端网页播放器的**渲染结果**现在是 `prototype/redesign/` 原型的 1:1 复刻：
94 组对应元素 × 42 个计算样式属性逐一比对，**0 处无法解释的差异**（68 处差异逐条
记录理由，见 §4）。功能一条没少：503 个 Python 测试 + 75 个 JS 测试 + 真实
Chromium 冒烟测试全绿，`npm run ci` 退出码 0。

## 2. 用户提出的问题 → 实际发现

| 用户的判断 | 核实结果 |
|---|---|
| "排版其实是有问题的" | **属实，而且是结构性的。** 03 板块被做成了三层可折叠 `<details>`（原型是三列并排的 `.sgroup` 网格）；04 板块被做成了 `<details>` + `<dl>` 列表（原型是四列彩色卡片墙 `.telemetry-grid` + `.tcard`）。这两处是肉眼最明显的偏差。 |
| "有的地方是做错了的" | **另有三处。** ① 顶栏缺本地时钟（`#topClock`）；② 缺 LIVE 徽章（`.live-chip`）；③ 全屏键被放到画面右上角悬浮，原型是控制条右簇的最后一个按钮。 |
| "1:1 复刻排版" | 已按原型逐条照抄声明（每段迁移规则都标注了原型行号），并加了自动化比对工具做客观验证（§3.1）。 |
| "它相应的功能也得复制" | 原型有而旧版没有的功能已补：滚动揭示动画、顶栏时钟、LIVE 徽章、弹幕随机撞色（改为按 id 散列的确定性配色）、原文在上/译文在下的双语字幕顺序、原文颜色选择器（此前是**死控件**，见 §5）。 |

## 3. 验证证据

> 本节所有数字都来自本机实测，不是文档转述。

### 3.1 计算样式比对（最强证据）

`.scratch/lingerlens-audit/style-parity.py`：用真实 Chromium 同视口（2000×1100）
同时加载原型与桌面端，对 94 组对应元素读取 42 个计算样式属性（盒模型、边框、
字体族/字号/字重/行高/字距、颜色、阴影、网格列、对齐、变换……）后逐项比对。

```
compared 94 element pairs across 42 properties
accepted deviations: 68 (across 25 pairs, each with a recorded reason)
UNEXPLAINED mismatches: 0 (across 0 pairs)
```

工具是**回归门禁**：任何未登记的差异都会让它退出码非 0。已登记的 68 处全部在
`ACCEPTED` 表里写明了理由（§4），不是"忽略清单"。

顺带查出并修掉两个真实缺陷：

- **延迟输入框变宽、出现双层边框。** 通用表单规则用了四个独立 `:not()`，
  特异性累加到 `(0,4,1)`，压过了 `.delay-input input { width: 64px; border: none }`。
  改成单条多参数 `:not(a, b, c, d)`（特异性 `(0,1,1)`）后恢复正常。旧版 CSS 里
  同一个 bug 就存在，只是没人量过。
- **`.dialog-body` 间距** 24px（原型 12px）。

### 3.2 字体（用户选择"连中文也内置"）

`scripts/fetch-fonts.py` 从 `@fontsource` 5.3.0 拉取并固化，全部本地同源、零外部请求：

```
files      : 396 个 woff2，9.32 MB
  archivo-black: 1        jetbrains-mono: 3        noto-sans-sc: 392
fonts.css  : 365,478 字节，396 个 @font-face，外部 URL 0 个
```

Noto Sans SC 保留 @fontsource 的 **unicode-range 分片**（98 片/字重 × 4 字重）：
浏览器只下载页面真正用到的那几片（几十 KB），而不是一次性 2.3 MB 全量。

真实 Chromium 里确认三族字体都已加载并生效：
`document.fonts.check('900 24px "Archivo Black"')`、`'400 15px "Noto Sans SC"'`、
`'700 12px "JetBrains Mono"'` **全部为 true**。

`server.py` 新增两条路由，并由桌面后端（带会话令牌中间件）实测：

```
GET /fonts.css                              -> 200, 365478 bytes, 396 @font-face
GET /fonts/noto-sans-sc/4-400.woff2         -> 200, 2300 bytes
GET /fonts/archivo-black/latin-400.woff2    -> 200, 18604 bytes
GET /fonts.css 不带令牌                      -> 403
GET /fonts/../companion/server.py           -> 404
GET /fonts/noto-sans-sc/..%2f..%2fserver.py -> 404
GET /fonts/noto-sans-sc/x.txt               -> 404
```

### 3.3 测试

| 项目 | 结果 |
|---|---|
| `py -3.10 scripts/run-hls-tests.py` | `PASS=48 | tests run=503`，退出码 0 |
| `node scripts/run-hls-js-tests.js` | `pass 75 / fail 0` |
| `test_browser_smoke.py`（真实 Chromium） | `Companion production-routing browser smoke passed` |
| `npm run ci` | `EXIT=0`（release guard + compileall + node --check + 两套测试 + 根测试） |

冒烟测试顺带证明了：**0 个未捕获页面错误**（`assert not errors_seen`）——
即所有 115 个 JS 依赖的元素 id、5 个选择器、`.asr-section`/`.translation-section`/
`.button-label` 全部存活。

### 3.4 多语言绑定

03/04 板块的 DOM 被重建，i18n 绑定必须跟着改；绑定漏了会**静默失效**
（标签一直显示中文，不报错）。`.scratch/lingerlens-audit/i18n-probe.py` 在 5 种语言下
逐个读取 32 个受绑定标签：

```
PASS all 32 watched labels change across all 5 locales
```

### 3.5 DOM 契约全量核对

前面的 id 检查只覆盖了 `el(id)` 这类按 id 查找。但 i18n 的 107 条绑定、
JS 里 38 处选择器字面量、以及若干 `data-*` 属性**漏了都是静默失效**（标签不翻译、
按钮不响应、不报错），所以必须全量对一遍，而不是抽查。

`.scratch/lingerlens-audit/dom-contract-probe.py` 在真实 Chromium 里驱动 **10 个 UI 状态**
（待机、三个播放条弹窗、模型设置对话框、Cookie 对话框、两个语言组合框（含键盘高亮）），
对每个状态跑一遍全量选择器：

```
UI states exercised   : 10
element ids           : 117 checked, 0 missing
JS selector literals  : 38 checked, 0 unresolved, 3 allowed-empty
i18n bindings         : 107 checked, 0 unresolved
data-* contracts      : 0 missing
PASS every id, JS selector, i18n binding and data attribute resolves
```

3 处"允许为空"是设计使然，且各有理由（写在脚本的 `ALLOWED_EMPTY` 里）：
`.language-native-fallback`（`language-selector.js` 加载时主动 `innerHTML = ""` 替换掉它）、
`.timeline-empty-state`（只在时钟未知且列表为空时写入，静态的 `.timeline-empty` 占着那个位置）、
`:active`（是 `rail.matches()` 的伪类查询，不是待解析的选择器）。

### 3.6 Python 侧接口面

`server.py` 的 `routes()` 逐条比对：**新增 2 条静态路由，删除 0 条**，
`/api/*` 端点一字未动。

```
routes before: 35   routes now: 37      (total registered routes in routes())
added  : /fonts.css, /fonts/{slug}/{name}
removed: (none)
/api/*  : 16, unchanged
```

> 勘误（2026-09 补）：本节原先写「33 → 35，14 个 `/api/*`」。当时的计数漏算了
> `/hls/{name}`、`/_private-hls/{token}/{name}` 与两条 `/api/messages*` 别名。
> 现在按 `app.router.add_*` 在 `routes()` 内的实际行数重数：上一轮结束时 37 条
> （其中 16 条 `/api/*`），本轮加了 `/pane-resizer.js` 后为 38 条。上面的数字已更正。

### 3.7 真实 Electron 窗口

用 OS 级截图抓了正在运行的 Electron 窗口（`output/prototype-port/10-electron-window.png`）。
窗口 CSS 视口经 `GetDpiForWindow` 实测为 **1707×918**（物理 2560×1377，DPI 144 = 1.5×）。

截图能确证的事：

- 顶栏出现了**新的本地时钟**（`00:14:17`）、`未启动` 状态徽章、Archivo Black 的
  `LingerLens` 字标 → 新的 `index.html` 与本地字体在真实 Electron 渲染进程里生效，
  即 `/fonts.css` + `/fonts/**` 通过了会话令牌中间件。
- 字幕栏是 `SUBS 实时字幕` + `跟随当前`（JS 写入的跟随按钮文案）、
  `等待字幕就绪…` 虚线空状态 → 新的面板头结构生效。
- 开播卡是 `打开一场直播 / 粘贴链接 → 准备 → 选清晰度 → 启动` 的新文案与排版。
- 实测字幕栏右边界在 CSS x≈342，等于 `16 + 320 + 边框`，
  即 **3 栏布局**（若命中 `≤1240` 断点应是 299）。桌面端 localStorage 里
  `lingerlens.workbenchView = "split"`，与之一致。

**一处必须说明的截图缺陷**：该位图里画面右侧（聊天栏所在区域）与顶栏/板块头右段
是黑的。这是本机 GPU 合成窗口被 BitBlt 抓帧的已知失真 —— 这台机器挂着两块显卡
（RTX 5070 Ti + Radeon）和两块虚拟显示器（Todesk / GameViewer），
硬件加速图层的捕获本就不可靠。判据是上一条实测的栏宽：布局确实是 3 栏，
聊天栏只是没被抓进帧里。同一套 CSS/JS 在 Playwright 的
`07-1440x900.png` / `09-1000x900.png` 里都完整渲染，
本节截图只用来证明"桌面壳确实在跑新代码"，不作为像素基准。

## 4. 与原型的 68 处已登记差异

分三类，全部有据可查（`style-parity.py` 的 `ACCEPTED` 表）：

**A. 计算值不同、像素相同（无所谓）** —— 例如 `.tab` 用 `display:flex` 而原型是
`block`（只有一个文本节点，两者都居中）；`.ctl-btn` 继承了全局 `button` 的
`padding/gap`，但外层是固定 38×38 的 `display:grid` + `place-items:center`，被完全吸收；
`.dialog-head { gap }` 与 `justify-content: space-between` 等价。

**B. 本应用特有的健壮性守卫（原型没有）** —— `.stage { min-height: 280px }`
（防止拿到分辨率前画面塌陷）；`.setup-feedback { min-height: 21px }`
（预留一行，卡片不跳动）；`.pane-badge { white-space: nowrap }`；
`.check { position: relative }`（把透明的原生 input 铺在自绘方块上，
键盘、指针、Playwright 都命中真实控件）。

**C. 有真实数据的必然结果** —— 模型设置对话框宽 900px 而原型 720px：
原型里是两行假占位卡，这里是真的双列 Provider 表单，720px 会挤。
统计卡片数量 15 vs 8（多了运行时间、用量、延迟分位数等真实遥测）。

对照表在 `output/prototype-port/` 下的截图（`00-prototype-2000.png` 与
`01-idle-2000.png` / `02-live-2000.png`）可直接肉眼复核。

## 5. 顺带修掉的功能缺陷

1. **原文颜色选择器是死控件。** `--subtitle-source-color` 被
   `subtitle-window-controller.js` 写入，但旧 CSS 从头到尾没有消费它 ——
   用户改"原文颜色"没有任何效果。现在 `.subtitle-src` 真正使用该变量，
   默认值也改为原型的 `#FFD23F`。
2. **双语字幕顺序与惯例相反。** 旧版译文在上、原文在下；原型（也是通行做法）
   是原文在上、译文在下。已调整生成模板与字号比例（原文 1em / 译文 1.13em，
   对应原型 15px/17px）。
3. **默认字幕位置会溢出画面。** 移到原型位置（`bottom: 12%`）后，
   两行字幕的下沿会被舞台裁掉；默认中心改为 `y: 0.82`，
   一至两行正好落在控制条上方。
4. **弹幕配色。** 按原型补上约 1/3 的撞色（柠檬绿/黄），
   用消息 id 的散列而非随机数 —— 同一条消息重放时颜色不跳。

## 6. 改动的测试断言（逐条说明，均为设计性断言）

行为/接口类断言**一条未动**。只有描述"旧设计长什么样"的断言被改写，
且每条都改成了对**新设计的更强断言**，不是放宽：

| 文件:行 | 旧断言 | 新断言 | 理由 |
|---|---|---|---|
| `test_web_assets.js:328` | `.workbench-container` | `.workbench` | 类名回到原型 |
| `:329` | `minmax(300px,340px) …` | `320px minmax(0,1fr) 320px` | 原型的固定侧栏宽 |
| `:330` | `.workbench-deck` 宽度 | `.deck-wide` `calc(100vw - 32px)` | 原型的通栏公式 |
| `:331` | `@media (max-width:1679px)` | `@media (max-width:1240px)` | 原型只有 1240 断点 |
| `:333` | `.workbench-pane` | `.pane` | 类名回到原型 |
| `:334` | `.timeline-list` | `.timeline` | 类名回到原型 |
| `:338` | 侧栏面板头 `flex-direction: column` | 删除 | 原型是单行 52px 面板头 |
| `:341` | 全屏键 class 精确串 | `player-control-surface` 在 class 列表中 + **必须位于 `.ctl-cluster` 内** | 按钮移位；新断言更强 |
| `:360-363` | `--paper:#f6f4ef`、`--card:#fdfcf9` | `#F4F0E6`、`#FFFDF6` + 三个字体栈 + `fonts.css` 无外部 URL | 设计令牌换回原型 |
| `:366-368` | `.language-controls {`、`.subtitle-controls {` 存在 CSS 规则 | 三个分组类必须出现在 HTML 的 `.sgroup` 上 | i18n 真正依赖的是 DOM 类名，不是 CSS 规则；新断言更准 |
| `:370` | `.telemetry-groups {` | `.telemetry-grid {` + `.tcard` 四列 + 三种配色 | 结构变了 |
| `test_browser_smoke.py:253` | 工作台标题左缘 == 画面左缘 | 01 板块通栏（比 shell 宽、左侧出血到 16px） | 原型没有列对齐技巧，但通栏是真的 |
| `:255-258` | 侧栏高度 == 画面高度 | 侧栏顶边 == 画面顶边，且侧栏**更高**（画面列还装着 NOW 面板） | 原型布局的真实不变量 |
| `:265-268` | 全屏键在画面右上 60×70 角内 | 全屏键在控制条右簇（画面右下象限） | 按钮移位 |
| `:367-370` | 聊天面板控制行在标题行下方 | 面板头固定 52px 单行，标题与控制在同一行且都在行内 | 原型是单行头 |
| `test_subtitle_window_controller.js` | 默认 `y:0.76 opacity:0.9 #ffd9e2` | `y:0.82 opacity:0.92 #FFD23F` | 默认值改为原型的位置与配色，注释里引了原型行号 |

## 7. 尚未做 / 残留风险

1. **真机长时间实测仍未跑。** 原型移植是纯前端改动，不触及上一轮修好的
   ASR/翻译/HLS 管线；但"8 小时真实直播"这类验收依然没做（见
   `docs/fix-and-removal-report-2026-09.md` §8）。
2. **打包版仍是旧的。** `release/win-unpacked` 里是 2026-09-14 18:12 的构建，
   **不含**本次改动，也不含 `/fonts` 路由。要出包需
   `npm run desktop:backend && npm run desktop:pack`（前置条件上一轮已验证齐全）。
3. **`.active-cue` 是死选择器**，旧 CSS 里就死了，只被 `test_web_assets.js:336`
   一条弱断言吊着。本次未动，属于既有问题。
4. **`web-player/` 下多数 JS 未被 git 跟踪**（`i18n.js`、`ui-bootstrap.js`、
   `control-bar.js`、`live-messages-client.js`、`fonts.css` 与 396 个字体文件都是
   untracked）。这不是本次引入的，但提交前需要确认是否要一并 `git add`。
5. **`min-width: 1080px`**（原型设定）只在 `≤1240px` 媒体查询里放开；
   1280×800 及以上完全正常，390px 由既有小屏兜底覆盖（冒烟测试量过 `#url` 不溢出）。

## 8. 复现命令

```powershell
# 样式 1:1 比对（回归门禁，退出码非 0 即出现未登记差异）
py -3.10 .scratch\lingerlens-audit\style-parity.py

# 截图（原型 + 桌面端并排，含实时/分栏/对话框/多种宽度）
py -3.10 .scratch\lingerlens-audit\visual-compare.py

# 多语言绑定 + DOM 契约全量核对
$env:PYTHONIOENCODING="utf-8"
py -3.10 .scratch\lingerlens-audit\i18n-probe.py
py -3.10 .scratch\lingerlens-audit\dom-contract-probe.py

# 桌面后端确实在服务字体（含令牌与目录穿越）
py -3.10 .scratch\lingerlens-audit\verify-desktop-fonts.py

# 字体资产完整性
py -3.10 scripts\fetch-fonts.py --check

# 全量
npm run ci
```
