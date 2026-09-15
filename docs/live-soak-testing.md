# 长时真实直播实测

一场 1–2 小时的测试要同时回答三件不同的事，它们需要**三种不同的仪器**。把其中任何一件塞给另一种仪器，结论都会是错的。

| 你想知道 | 用什么 | 产出 |
|---|---|---|
| 上游堵了多久、恢复得多快 | `soak:monitor`（后端时间序列） | `samples.jsonl` / `summary.json` |
| 字幕翻译多快、被丢了多少条 | `soak:monitor` | `cues.jsonl` |
| **字幕有没有真的出现在屏幕上、准不准时** | `soak:onscreen`（CDP 采样真实 DOM） | `onscreen.jsonl` |
| 出过什么错、Python 说了什么 | 应用诊断栏 / `LINGERLENS_BACKEND_LOG` | 日志文件 |

**诊断日志不是测量工具。** 它记的是事件（出错才写一行），一场顺利的直播可能只有几十行。延迟曲线、堵塞时长、字幕上屏时刻都不在里面。它回答的是"出过什么错"，不是"表现如何"。

## 跑一场

**① 启动应用，必须带调试端口**

```powershell
$env:LINGERLENS_BACKEND_LOG = "$PWD\output\soak\app.log"
.\release\win-unpacked\LingerLens.exe --remote-debugging-port=9222
```

`start-lingerlens.cmd` **不带**这个参数，所以用脚本启动的话上屏采样接不上。

**② 另开两个窗口**

```powershell
# 后端时间序列（自己起隔离 Companion，不碰上面那个实例）
npm run soak:monitor -- --url "<直播链接>" --seconds 5400 --interval 10 `
    --providers-file "$env:APPDATA\lingerlens\runtime\providers.json" `
    --output output\soak\20260915-1800

# 屏幕上的字幕（接到 ① 那个窗口）
npm run soak:onscreen -- --seconds 5400 --output output\soak\20260915-1800
```

**③ 跑完分析**

```powershell
npm run soak:analyze -- output\soak\20260915-1800
npm run soak:onscreen -- --analyze output\soak\20260915-1800
```

结果全在 `output/soak/` 下，`.gitignore:33` 覆盖，不会被提交。

## 故意的破坏性动作

顺利跑完一场，三份数据都可能是空的，那时分不清"没问题"和"没测到"。所以中途至少制造一次故障：

- **断网 30–60 秒**，然后恢复 → 看 `sourceStallSeconds` 爬升、恢复时单样本的媒体跳变量、以及播放器多久回到目标延迟
- 中途**切换一次清晰度** → 看会话是否重建、字幕是否断档
- 用一条**需要登录**的直播 → 验证失败路径有明确提示

## 两个已知的坑

**单实例锁。** 打包版和 dev 版共用同一个 `userData`（都叫 `lingerlens`），所以**不能同时开**——后启动的那个会立刻退出，而且它是在打印完 `DevTools listening on ...` 之后才退的，看起来像启动成功了。要并行跑，给其中一个加 `--user-data-dir=<临时目录>`。

**媒体时钟缺席。** 上屏采样靠 `mediaClock.playingWallTime()` 给字幕计时。没有会话在跑时它是 `null`，此时采样器**拒绝计算延迟**并明说"timed samples 0"，而不是拿自己的秒表凑一个看起来合理的数字。看到 `timed samples 0` 就说明那一场根本没播起来，不是字幕没问题。

## 三个采样器各自的边界

- `soak-live-monitor.py` 起的是**源码树 Companion**，不经过 Electron 播放器。它测后端管线，测不到画面。它需要一份**带 API key** 的 providers 文件。
- `soak-onscreen-sampler.py` 读的是**真实 DOM**（`.subtitle-cue-row` 里的 `.subtitle-src` / `.subtitle-zh`）加上那一刻的媒体时钟，所以它测的是"屏幕上有什么"。它**不**测后端生产了什么。
- 两个都测不了的东西：字幕的**语义正确性**（翻译对不对）。那要人看。

## 判读

正常的：
- 全部采样 `state: running`，媒体时间/墙钟推进比接近 1.0
- `sourceStallSeconds` 中位数个位数秒，没有长尾
- 字幕上屏延迟 P95 在一个采样周期量级，没有"晚于自己 tEnd"的条目

要警惕的：
- `sourceStallSeconds` 出现 >30 秒的长尾，且恢复后本地延迟没有回落
- `translationFailures` / `translationDropped` 在恢复突发之后集中上升——恢复瞬间涌入的旧字幕会带着已经过期的截止时间一起到达
- 上屏报告里 `never rendered` 比例高，或出现 `appeared only after its own end`
- 空窗比例高（`blank samples`）：可能是没字幕，也可能是字幕被隐藏了，两者用户看到的是一样的

上一份真实的一小时报告在 `docs/notes/live-monitor-20260912/one-hour/analysis.md`，可以作为输出长什么样的参照。
