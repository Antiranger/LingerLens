# .archive — 归档的历史快照

**这里的任何文件都不是源码，不要读、不要引用、不要修改。**

## 为什么存在

`.scratch/` 与 `.planning/` 下曾经散落着 7 组实验留下的源码快照，共 32 个与线上源码同名的文件。它们造成两个实际问题：

1. 一次搜索 `subtitle_pipeline.py` 会返回 **8 份内容互相矛盾的结果**，而其中只有一份是真正在运行的代码。这是"逻辑看起来很乱"的一个真实来源。
2. 它们当时**被 git 追踪**，所以 `git grep` 也会命中它们。

于是把它们集中到这里，并**移出 git 追踪**（`/.archive/*` 已加入 `.gitignore`，仅本 README 例外）。文件字节一个都没删——只是不再参与搜索和版本控制。

## 权威源码在哪里

```
prototype/hls-companion/companion/          ← 唯一的源码
prototype/hls-companion/companion/providers/
prototype/hls-companion/tests/
prototype/hls-companion/web-player/
```

判断方法：只有满足上面路径的文件才是代码。**本目录下的同名文件一律是过时快照。**

## 目录对照

每个快照都保留了它原本的相对路径，因此来历是可追溯的：

| 归档位置 | 原位置 | 属于哪次实验 |
|---|---|---|
| `source-snapshots/.planning/speaker-intonation-implementation/backup-before-replan/` | `.planning/…` | 说话人语调实现重规划前的备份 |
| `source-snapshots/.scratch/caption-residual-lifecycle/baseline/` | `.scratch/…` | 字幕残片生命周期实验基线 |
| `source-snapshots/.scratch/clause-segmentation-v2/before/` | `.scratch/…` | 分句 v2 对照基线 |
| `source-snapshots/.scratch/live-pipeline-latency-v2/backup/` | `.scratch/…` | 直播管线延迟实验备份 |
| `source-snapshots/.scratch/startup-sync-experiment/backup-20260909-115026/` | `.scratch/…` | 启动同步实验（2026-09-09 11:50:26） |
| `source-snapshots/.scratch/subtitle-fixes-2026-09-05/backup-before-fixes/` | `.scratch/…` | 2026-09-05 字幕修复前 |
| `source-snapshots/.scratch/translation-no-predecessor-wait/before/` | `.scratch/…` | 翻译不等前句实验基线 |

各实验的脚本、报告与结果数据**仍留在原处**（`.scratch/<实验名>/` 下的 `*.py`、`*.md`、`*.json`、`*.log`），这里只收了源码副本。

## 什么情况下才该动这里

只有在需要**对比历史行为**时（例如"某次重构前后分句结果差异在哪"）才值得翻。日常开发、排查、重构都不需要。

如果确认永远用不到，可以整体删除本目录——它们不是构建产物依赖，也不是运行时依赖。
