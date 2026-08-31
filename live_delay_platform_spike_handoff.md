# Handoff：YouTube / B站直播延迟能力验证 Spike

## 任务目的

在接入 ASR、翻译模型和字幕之前，先验证浏览器中的 YouTube Live 与 B站直播能否让一个观看标签页长期稳定落后直播前沿 `5 秒`或`10 秒`。

本次只验证播放器和双时间线，不做 AI、音频识别、字幕或后端。

## 核心问题

对每个平台分别回答：

1. 网页直播的 `<video>` 是否提供可回退的 `seekable` 时间范围？
2. 是否能设置 `video.currentTime = liveEdge - D`？
3. 能否连续播放并长期保持 `D = 5s` 或 `D = 10s`？
4. 平台会不会自动跳回直播前沿？
5. 暂停/恢复、清晰度切换、播放器重载后能否恢复目标延迟？
6. 如果不能，失败原因是 DVR 不可用、可回退范围不足，还是平台播放器主动纠正？

其中：

```text
liveEdge = video.seekable.end(video.seekable.length - 1)
actualDelay = liveEdge - video.currentTime
```

不要把平台本身“主播到观众”的网络延迟当成这里的 `actualDelay`。本实验只测播放器距离当前可播放 Live Edge 的相对差值。

## 建议实验结构

为同一直播打开两个标签页：

```text
Source Tab：保持在 Live Edge，用作参照
Viewer Tab：人为落后 D 秒，用户实际观看
```

本 Spike 不需要采集 Source Tab 音频，但要观察两个标签页能否同时持续播放，尤其注意后台标签页暂停、休眠或被丢弃的问题。

## 实现要求

优先做一个最小 Chrome/Edge Manifest V3 扩展，而不是只在 DevTools 临时粘贴脚本。代码应方便在 YouTube 和 B站真实直播间重复测试。

最低功能：

- 找到当前页面主要的 `<video>`；
- 每秒读取一次：
  - `video.currentTime`
  - `video.paused`
  - `video.playbackRate`
  - `video.readyState`
  - `video.seekable.length`
  - 最后一个 seekable range 的 start/end
  - `actualDelay`
- 可选择目标延迟 `5s` 或 `10s`；
- 点击按钮后执行一次初始 seek；
- 延迟进入允许区间后不要频繁 seek；
- 检测明显漂移，并记录平台是否自动跳回 Live Edge；
- 在页面上显示一个简易调试面板；
- 将带时间戳的观测数据输出到 console，最好支持导出 JSON/CSV。

初始控制策略保持简单：

```text
targetDelay = 5s 或 10s
deadband = ±1s

actualDelay 在目标 ±1s 内：不干预
偏差超过 2s：记录一次 drift，并 seek 到 liveEdge - targetDelay
```

不要一开始使用频繁 `playbackRate` 调节，也不要每帧 seek。先观察平台的自然行为。

## 测试矩阵

每个平台至少测试：

| 平台 | 目标延迟 | 建议时长 |
|---|---:|---:|
| YouTube Live | 5 秒 | 30 分钟 |
| YouTube Live | 10 秒 | 30 分钟 |
| B站直播 | 5 秒 | 30 分钟 |
| B站直播 | 10 秒 | 30 分钟 |

如果条件允许，选择两种直播：

- 普通直播；
- 明显采用低延迟/超低延迟的直播。

每组额外测试：

1. 正常连续播放；
2. 暂停 10 秒后恢复；
3. 手动点击“直播”或拖到最前沿后重新设定延迟；
4. 切换一次清晰度；
5. 把 Source Tab 放到后台，观察是否停止前进；
6. 检查浏览器 Memory Saver 是否导致 Source Tab 被 discarded。

## 需要记录的指标

```text
platform
targetDelay
actualDelay（每秒）
seekableWindowLength
initialSeekSucceeded
driftCorrectionCount
autoJumpToLiveCount
videoReloadCount
bufferingCount
sourceTabStoppedCount
manualRecoveryRequired
failureReason
```

建议额外计算：

```text
actualDelay P50 / P95
与目标延迟的平均绝对误差
处于 target ±1s 的时间比例
最长连续稳定时间
```

## 判定标准

### 可直接支持

满足：

- 有足够的 seekable 历史范围；
- 初始回退成功；
- 30 分钟内大部分时间保持在 `target ±1s`；
- 不需要频繁 hard seek；
- 清晰度切换或短暂停后可以自动恢复；
- 无明显影响观看的卡顿。

### 有条件支持

例如：

- 只有部分直播支持 DVR；
- 5 秒可行但 10 秒经常被平台拉回；
- 需要暂停积累缓冲，而不能直接 seek；
- 页面或播放器重载后必须重新建立延迟；
- 后台 Source Tab 容易被浏览器休眠。

需要明确列出适用条件和恢复策略。

### 不可直接支持

例如：

- `seekable` 范围始终不足；
- 设置 `currentTime` 无效；
- 平台持续强制跳回 Live Edge；
- 需要解析直播地址、自建 MSE/媒体环形缓冲才能实现。

此时不要继续写复杂延迟控制器，只记录证据并提出下一层替代方案。

## 本轮明确不做

- 百炼 ASR；
- `tabCapture` 音频发送；
- 翻译模型；
- 字幕 Overlay；
- TimelineMapper；
- 本地 companion；
- 自己解析 HLS/DASH/FLV；
- 自建媒体缓冲；
- 自动延迟推荐算法。

## 交付物

下一位 Agent 应交付：

1. 可加载到 Chrome/Edge 的最小 MV3 测试扩展；
2. 安装与操作说明；
3. YouTube 和 B站分别如何选择测试直播；
4. 页面调试面板和日志导出能力；
5. 实际运行后的测试记录；
6. 一份简短结论：

```text
YouTube：5s / 10s 分别是可直接支持、有条件支持还是不可支持
B站：5s / 10s 分别是可直接支持、有条件支持还是不可支持
双标签页 Source/Viewer 是否能连续稳定工作
第一版应支持哪些平台和延迟档位
```

## 执行原则

- 不要沿用旧 handoff 中“YouTube DVR 一定可用”或“B站应该类似”的假设；
- 先检查仓库现状，再实现最小实验；
- 使用标准 `HTMLMediaElement` API，尽量少依赖平台私有对象；
- 平台差异应隔离为 adapter，不要把 YouTube/B站判断散落在各处；
- 先取得真实运行证据，再决定后续架构；
- 若 Agent 无法亲自完成 30 分钟人工运行，应把扩展、操作步骤和结果记录模板准备完整，明确哪些结论仍需用户实测。
