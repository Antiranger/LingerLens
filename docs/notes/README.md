# 工程记录（历史，不改写）

本目录是 2026-09 期间的工作记录：调研报告、spec、逐次会话日志、验收结果。它们记录的是**当时**的事实，所以**正文与目录名都保留撰写时的项目名 `LagLingo`**，不随 2026-09-15 的全仓库改名一起改写。改写它们等于让历史记录谎报当时的情况。

`docs/` 根目录下的设计文档（如 [`repository-design.md`](../repository-design.md)）是长期有效的文档，已随改名更新，**不属于**本目录的例外。

## 读到旧名时怎么换算

| 记录里写的 | 今天是什么 |
|---|---|
| `LagLingo` / `laglingo` / `LAGLINGO` | `LingerLens` / `lingerlens` / `LINGERLENS` |
| `laglingo://app`（网页来源与自定义协议） | `lingerlens://app` |
| `LAGLINGO_BACKEND_LOG`、`LAGLINGO_PYTHON`、`LAGLINGO_UPDATE_URL`、`LAGLINGO_SMOKE_OUTPUT`、`LAGLINGO_TEST_BACKEND`、`LAGLINGO_TEST_REEXEC`、`LAGLINGO_FFMPEG_PROXY` | 同名，前缀改为 `LINGERLENS_` |
| `laglingo-backend`（PyInstaller 冻结目录与后端二进制） | `lingerlens-backend` |
| `Antiranger/LagLingo`、`Antiranger/LagLingo-releases` | `Antiranger/LingerLens`、`Antiranger/LingerLens-releases` |
| `start-laglingo.cmd` | `start-lingerlens.cmd` |
| `licenses/MIT-LagLingo.txt`、包内 `LICENSE-LagLingo.txt` | `MIT-LingerLens.txt`、`LICENSE-LingerLens.txt` |
| `#laglingo-delay-spike`（扩展面板 DOM id）、`laglingo-delay-spike` | `#lingerlens-delay-spike` |
| `window.LagLingo*`、`window.__laglingo*`（含浏览器 smoke 测试用的钩子） | `window.LingerLens*`、`window.__lingerlens*` |
| `com.laglingo.hls_companion`（Chrome 原生消息主机名） | `com.lingerlens.hls_companion` |
| `\\.\pipe\laglingo_hls_companion_v2`（Companion 控制 IPC） | `\\.\pipe\lingerlens_hls_companion_v2` |
| `localStorage` 里的 `laglingo.*` / `laglingo_*` 键 | `lingerlens.*` / `lingerlens_*`（键名换了，旧偏好不会被读取） |
| `%APPDATA%/LagLingo`、安装目录 `%LOCALAPPDATA%/Programs/laglingo` | `%APPDATA%/LingerLens`、`%LOCALAPPDATA%/Programs/lingerlens` |

改名后的第一次安装是**并排安装**，不是升级：`appId` 变了，NSIS 的升级身份随之改变，所以旧版需要手动卸载（用户设置在那个 `%APPDATA%` 目录里，不随卸载删除，但也不会被新版读取）。

## 为什么这些记录留在这里

按 [`repository-design.md`](../repository-design.md) 的划分，`docs/` 根目录放设计文档，`docs/notes/` 放工作记录。这些记录的价值在于「当时发生了什么、当时是怎么判断的」，所以它们不做术语同步，也不追认后来的改动。
