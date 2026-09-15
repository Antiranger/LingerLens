# 可拖拽侧栏 + 打包链路说明（2026-09）

本轮两件事：

1. 实时字幕栏 / 实时聊天栏的边框可以手动拖动调宽（**原型里没有这个交互**，属于新增功能）。
2. 说清「桌面端」和「打包版」到底是不是两个东西，以及怎么让打包版更新变容易。

---

## 1. 结论

- 侧栏宽度可拖，键盘可调，宽度持久化；**不拖的时候与原型逐像素一致**（内联变量一个都不写）。
- 顺带修掉一个真实的响应式 bug：≤1240px 的聊天视图里列宽是**反的**（画面 280px、聊天栏 782px）。
- 新增 `npm run desktop:refresh`（全量刷新）和 `npm run desktop:sync-ui`（只同步前端，秒级）。
- 打包版已重建并**实测**：冻结后的 `lingerlens-backend.exe` 真的服务新前端，拖动在打包版里有效。

---

## 2. 「桌面端」和「打包版」是两个东西吗？

同一个应用、两种交付形态。差别在于**后端从哪来**：

| | `npm run desktop:dev` | `release\win-unpacked`（打包版） |
|---|---|---|
| Electron | `build-desktop\electron\electron.exe .` | 同一份 Electron，复制进 `release` |
| `app.isPackaged` | `false` | `true` |
| 后端 | `.venv-desktop\Scripts\python.exe desktop/companion_entry.py` | `resources\backend\lingerlens-backend.exe` |
| 后端代码来源 | **磁盘上的源码，改完重载就生效** | **PyInstaller 冻结时的快照** |
| 前端来源 | 源码目录 `web-player/` | 冻结时被**复制**进 `_internal\web-player\` |
| 端口 / 令牌 | 随机端口 + 64 位十六进制令牌 | 同左 |

依据（不是猜测）：

- `package.json`：`"desktop:dev": "build-desktop\\electron\\electron.exe ."`，`main` 指向 `desktop/main.cjs`。
- `desktop/main.cjs:60`：`startBackend({ packaged: app.isPackaged, ... })`。
- `desktop/backend.cjs:7-10`：`packaged` 为真跑 `resources\backend\lingerlens-backend.exe`，否则跑 venv 里的 `companion_entry.py`。
- `desktop/companion.spec:10`：`(str(app_root / 'web-player'), 'web-player')` —— 前端是作为 **datas 复制**进去的。
- `desktop/main.cjs:84`：`window.loadURL('lingerlens://app/')`，自定义协议把请求转到后端源并注入 `X-LingerLens-Session`。所以 Electron 窗口里的 HTML/CSS/JS **全部来自后端**，不是从 asar 读的 —— 这正是 `desktop:sync-ui` 能成立的原因。

所以：**改源码不会影响打包版**。这就是「打包版是旧的」的原因。实测打包版此前停在 `2026-09-14 18:12`，`_internal\web-player\` 里连 `fonts.css` 和 396 个字体文件都没有 —— 它早于整个原型移植。

### 怎么让更新变容易

两条路，按改了什么选：

```powershell
# 只改了前端（HTML/CSS/JS/字体）—— 秒级
npm run desktop:sync-ui

# 改过后端 Python（路由、API、管线）—— 分钟级
npm run desktop:refresh      # = desktop:backend && desktop:pack
```

`desktop:sync-ui` 之所以快，是因为 PyInstaller 用的是 COLLECT（不是 onefile），`_internal\web-player\` 是**磁盘上的真实文件目录**，覆盖即可，不用重新冻结、不用跑 electron-builder。

**它的硬边界**：它**不能新增或删除后端路由**。`companion/server.py` 编译进了冻结归档，路由表是冻结时的。所以「新加一个 script 标签 + 新加一条 `add_get`」这种改动仍然必须 `desktop:refresh`，否则新资源 404。脚本会自己发现这件事：

```
WARNING: backend Python changed after the frozen build was made.
Routes and API behaviour in the packaged app are still the old ones.
A brand-new asset URL will 404 until you run: npm run desktop:refresh
```

本轮就是这种情况（`/pane-resizer.js` 是新路由），所以老老实实跑了全量 `desktop:refresh`。

> 自查记录：`sync-web-player.js` 我最初没写 `--dry-run` 就对着真实构建跑了一次，把前端覆盖进了打包版而其后端仍是旧的 —— 一个「新前端 + 旧路由」的不一致状态。因为 `pane-resizer.js` 这条路由不在旧后端里，那时打包版的拖动其实是坏的。已补 `--dry-run`，并跑完 `desktop:refresh` 把打包版修成一致。打包版是可再生产物（`/release/` 在 `.gitignore` 里），所以没有丢失任何不可恢复的东西。

---

## 3. 可拖拽侧栏

### 3.1 硬约束：默认值必须还是原型

原型 `prototype/redesign/style.css` 里侧栏是死宽 `320px`（≤1240px 时 280px）。新交互不能改变这个默认观感，所以：

- CSS 把宽度提成变量，**默认值就是原型数字**：
  - `.workbench { --pane-left-w: 320px; --pane-right-w: 320px; }`
  - `grid-template-columns: var(--pane-left-w) minmax(0, 1fr) var(--pane-right-w)`
  - `@media (max-width: 1240px) { .workbench { --pane-left-w: 280px; --pane-right-w: 280px; } }`
- **JS 只在用户真的拖过之后才写内联变量**。没拖过 → 一个内联属性都不写 → 几何完全由 CSS 决定 → 与原型一致。
- 复位（双击 / Enter）= 删掉内联变量 + 删掉存储键，把宽度还给 CSS。

这条「静止时零写入」有专门的回归测试，因为它是最容易被后人无意破坏的性质：

```js
// test_pane_resizer.js
test("PaneResizer writes nothing at rest so the prototype layout is untouched", ...)
  assert.deepEqual(harness.workbench.styles, {});
```

### 3.2 手柄为什么能骑在边框上

手柄是 `.pane` 的绝对定位子元素，靠 `right: -8px`（左栏）/ `left: -8px`（右栏）把 12px 的命中区骑在 3px 边框上，向外让 8px、向内留 4px。高亮条精确覆盖那 3px，不额外增粗。

这里有一个真实的层叠问题：`.stage-col` 在 DOM 里排在 `#paneSubtitles` 之后，**绘制顺序在后**，所以默认情况下舞台会赢下边框外侧那 8px 的命中测试；而 `#emptyState` 还带 `z-index: 10`。只加 3px 的边框本身能拖，但手感很差。解决办法是给三栏各自建立层叠上下文：

```css
.workbench { position: relative; z-index: 0; }  /* 整个网格一个层叠上下文 */
.pane      { position: relative; z-index: 2; }  /* 侧栏压在舞台之上 */
.stage-col { position: relative; z-index: 1; }  /* 把 #emptyState 的 z-index:10 关在舞台列内部 */
```

三栏之间本来就不重叠，所以这对画面没有任何视觉影响 —— 但它是 3 条新的「已记录偏差」（见 §5）。

实测确认手柄 12px 全宽都可命中，没有死像素。

### 3.3 行为

| 操作 | 结果 |
|---|---|
| 拖边框 | 宽度跟手，松手落盘 |
| 双击 / Enter | 复位到原型默认值 |
| `←` `→` | ±16px；按住 Shift ±64px |
| `Home` / `End` | 直接到最小 / 最大 |
| 拖动中 `Esc` | 退回**这次拖动之前**用户选的宽度 |
| 窗口缩放 | 显示宽度重新收敛，但**不覆盖用户选的值** |

最后一条容易做错：早期实现把收敛后的值写回存储，于是把窗口拖窄再拖宽，用户原来的宽度就永久丢了。现在 `requested`（用户要求，持久化）和 `applied`（当前视口下实际渲染）是分开的，有测试盯着：

```js
test("a reflow compresses for a narrow viewport without forgetting the chosen width", ...)
```

边界不是写死的常数：`max = min(560, 工作台宽度 − 另一侧宽度 − 320)`，保证画面永远留得住 320px。

### 3.4 无障碍

手柄是标准 splitter 写法：`role="separator"` + `aria-orientation="vertical"` + `tabindex="0"` + `aria-controls` + `aria-valuemin/max/now`。`aria-valuenow` 跟着实际宽度走。

可见文案（`title` 工具提示和 `aria-label`）**都进了 i18n**。为此给 `i18n.js` 加了两个新 kind —— `titleAttr` 和 `ariaLabel` —— 否则日/德/俄用户会看到中文。现在 5 种语言都有译文。

### 3.5 顺带修掉的真实 bug

**≤1240px 的聊天视图列宽是反的。** 原来这一条：

```css
@media (max-width: 1240px) {
  .workbench, .workbench.view-subtitles, .workbench.view-chat {
    grid-template-columns: 280px minmax(0, 1fr);
  }
```

三个视图共用同一条 `280px` 首列。但聊天视图里 `.stage-col` 是 `order: -1`，于是**画面被放进 280px 那一列，聊天栏吃掉剩下的 782px**。1100px 视口实测：

```
修复前  1100x900/chat   tracks=280px 782px     画面=280   聊天=782
修复后  1100x900/chat   tracks=782px 280px     画面=782   聊天=280
1280x900/chat（未受影响）tracks=922px 320px     画面=922   聊天=320
```

修法是把三个视图拆开，聊天视图保持「画面在前」：

```css
.workbench, .workbench.view-subtitles { grid-template-columns: var(--pane-left-w) minmax(0, 1fr); }
.workbench.view-chat { grid-template-columns: minmax(0, 1fr) var(--pane-right-w); }
```

已加回归断言，防止再被合并回去。截图见 `output/pane-resize/07-narrow-chat-view-fixed.png`。

---

## 4. 验证证据（全部本地实跑，当前工作树）

| 检查 | 命令 | 结果 |
|---|---|---|
| 计算样式 1:1 | `.scratch/lingerlens-audit/style-parity.py` | 94 组元素 × 42 属性，**无法解释的差异 0**，已记录偏差 71 |
| DOM 契约 | `.scratch/lingerlens-audit/dom-contract-probe.py` | 119 id / 39 选择器 / 111 i18n 绑定，**0 未解析**；10 个 UI 状态 |
| 多语言 | `.scratch/lingerlens-audit/i18n-probe.py` | **35** 个标签 × 5 语言全部切换（含新的 `title` / `aria-label`） |
| 拖动端到端 | `.scratch/lingerlens-audit/pane-resize-probe.py` | **44 项全过**（真实指针事件、持久化、键盘、夹取、三种视图、两个断点、0 未捕获错误） |
| 打包版实测 | `.scratch/lingerlens-audit/verify-packaged-ui.py` | **9 项全过**：真令牌握手 + 打包版里真拖到 460px 并落盘 |
| 侧栏单元测试 | `node --test prototype/hls-companion/tests/test_pane_resizer.js` | 18/18 |
| 浏览器冒烟 | `prototype/hls-companion/tests/test_browser_smoke.py` | PASS，0 未捕获错误 |
| 全量 CI | `npm run ci` | **EXIT=0**：Python 48 文件 / 503 测试；JS 93 通过 0 失败；根 13 通过 0 失败；release guard 通过 |

`pane-resize-probe.py` 里最关键的一条是「静止时」检查，它把「不拖就等于原型」变成了可执行断言：

```
at rest: no inline --pane-left-w      == ""
at rest: tracks                       == "320px 1322px 320px"
at rest: nothing stored               == None
handle hit area: no dead pixels       == []
```

### 4.1 测试替身发现的两个自身 bug

写测试时的两个真实缺陷（不是测试写错）：

1. `paintMeasured()` 在「没有用户宽度」时也调用了 `apply()`，于是初始化就会写内联变量 —— 直接破坏 §3.1 的硬约束。是 `assert.deepEqual(workbench.styles, {})` 抓到的。
2. `Escape` 分支先把 `dragging` 置空、再去读 `dragging.origin`，恒为 `undefined`，退回逻辑实际失效。

两个都在 `pane-resizer.js` 里修掉了。

---

## 5. 新增的 3 条已记录样式偏差

`.workbench` / `.pane` / `.stage-col` 的 `position: static → relative`（§3.2 的层叠上下文）。三栏不重叠，画面无变化；`style-parity.py` 的 `ACCEPTED` 表里各自写了理由。除此之外 94 组元素全部与原型一致。

---

## 6. 改动清单

**新增**
- `prototype/hls-companion/web-player/pane-resizer.js` —— 纯函数（`clampPaneWidth` / `paneWidthBounds` / `paneWidthFromDrag` / `readPaneWidths` / `writePaneWidths`）+ `createPaneResizer` 控制器
- `prototype/hls-companion/tests/test_pane_resizer.js` —— 18 个测试，含一个诚实的 DOM 替身
- `scripts/sync-web-player.js` —— 前端同步（支持 `--dry-run`，带冻结过期检测）
- `.scratch/lingerlens-audit/` 下 4 个验证脚本：`layout-probe.py`、`pane-resize-probe.py`、`verify-packaged-ui.py`、`capture-pane-resize.py`
- `output/pane-resize/` 7 张截图

**修改**
- `web-player/style.css` —— 宽度变量 + `.pane-resizer` + 修 ≤1240 聊天视图列序
- `web-player/index.html` —— 两个手柄 + `pane-resizer.js` script 标签
- `web-player/player.js` —— 实例化 `createPaneResizer`
- `web-player/i18n.js` —— `titleAttr` / `ariaLabel` 两个 kind + 4 条绑定 + 3 个 key × 4 语言
- `companion/server.py` —— `add_get("/pane-resizer.js", ...)`（路由表 38 条，其中 16 条 `/api/*`；本轮**新增 1 条、删除 0 条，`/api/*` 一个字没动**）
- `tests/test_web_assets.js`、`tests/test_live_messages.py` —— 见 §7
- `package.json` —— `desktop:refresh`、`desktop:sync-ui`、`check:js` 加新文件

**重新生成**
- `release/win-unpacked`（`desktop:refresh`，EXIT=0）

---

## 7. 改掉的测试断言

只改「把设计写死」的断言，每一条都换成**更强**的断言，没有为了变绿而放松任何检查；行为/API/ARIA/路由断言一条都没动。

| 位置 | 原来 | 现在 |
|---|---|---|
| `test_web_assets.js` | `grid-template-columns:\s*320px\s+minmax\(0,\s*1fr\)\s+320px` | 分别钉住 `--pane-left-w: 320px`、`--pane-right-w: 320px` 和消费它们的轨道（默认值 + 机制一起钉） |
| 同上 | （无） | `.view-chat` 必须是 `minmax(0,1fr) var(--pane-right-w)`，且 ≤1240 块内也一样 —— 防止列序反转回归 |
| 同上 | （无） | 手柄数量 = 2、`role="separator"`、`aria-orientation`、`aria-controls`、静止透明、≤899 隐藏 |
| `test_live_messages.py` | 3 条规范路由 | 4 条（加入 `/pane-resizer.js`） |

---

## 8. 残留风险

1. **git 只跟踪了 415 个前端文件里的 7 个。** `web-player/` 下只有 `index.html`、`player.js`、`style.css`、`playback-recovery.js`、`subtitle-scheduler.js`、`subtitle-window-controller.js`、`vendor/hls.min.js` 在版本控制里；`i18n.js`、`ui-bootstrap.js`、`control-bar.js`、`workbench-controller.js`、`pane-resizer.js`、`live-messages-client.js`、`fonts.css` 和 396 个字体文件**都不在**。`.gitignore` 并没有排除它们 —— 它们只是从来没被 `git add` 过（全仓仅 167 个跟踪文件）。**后果：现在从零 clone 出来的仓库，界面是坏的。** 这是本轮之前就存在的问题，但本轮又往上加了新文件。要不要把 9.3MB 字体纳入版本控制、或者改成构建时拉取，是仓库策略决定，我没有替你选。
2. `sync-web-player.js` 是「镜像」语义：源里删掉的文件会在目标里删掉。它只认 `release\win-unpacked\resources\backend\_internal\web-player` 这一个路径，且要求目标已有 `index.html`，否则拒绝运行。
3. 拖动宽度存在 `localStorage["lingerlens.paneWidths"]`，而桌面版和源码版的 origin 不同（桌面版是 `lingerlens://app/`），所以两边的宽度偏好互相独立。
4. 与之前几轮相同、仍未做的长时实测：8 小时真实直播、GOP 修复后的 MEDIA_ERROR 计数、错误 API key 跑 1 小时、20 次标签页切换、真实日/中直播测 `sourceOnlyCues`。

---

## 9. 复现命令

```powershell
$env:PYTHONIOENCODING="utf-8"

node --test prototype/hls-companion/tests/test_pane_resizer.js
py -3.10 .scratch\lingerlens-audit\pane-resize-probe.py
py -3.10 .scratch\lingerlens-audit\style-parity.py
py -3.10 .scratch\lingerlens-audit\dom-contract-probe.py
py -3.10 .scratch\lingerlens-audit\i18n-probe.py
py -3.10 .scratch\lingerlens-audit\capture-pane-resize.py
py -3.10 -u .scratch\lingerlens-audit\verify-packaged-ui.py

npm run ci

# 打包更新
npm run desktop:sync-ui           # 只改前端
npm run desktop:refresh           # 改了后端
```
