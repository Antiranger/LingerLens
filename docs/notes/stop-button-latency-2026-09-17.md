# Stop 按钮为什么慢：一次实测归因与修法（2026-09-17）

用户报告：播放中按「停止」，要等十几秒界面才停。这篇记录的是实测数字、根因、
修法边界，以及怎么在没有真实直播的情况下回归。

## 1. 实测：钱花在哪里

用真实链路复现（真实 `CompanionApplication`、真实 yt-dlp、真实直播 HLS、真实
ffmpeg，无桩件），脚本 `.scratch/stop-latency-20260917/repro_stop_latency.py`
包住 `handle_stop` 里的每一段等待后逐项计时：

| 场景 | `/api/stop` | 明细 |
| --- | --- | --- |
| 纯媒体（无字幕无弹幕） | **9.79s** | `source_ingest.stop` 9.78s |
| 同上，拆到 stop 内部 | 6.48s | `log0.join(3)` 3.00s + `log1.join(3)` 3.00s + `pump0.stop` 0.43s + 其余 ≈ 0.05s |
| 带真实字幕管线 | 3.39s | `subtitle_pipeline.stop` 3.34s（ASR 收尾 drain 2.5s 上限）+ 媒体 0.02s |

关键点：**顺序拆解把互不相干的等待相加**。媒体腿、字幕管线、弹幕三条腿之间没有
依赖，旧代码却是 `await 消息 → await 字幕 → await 源 → await 会话`。

## 2. 根因：yt-dlp 的进程是一棵树，我们只杀了它的根

直播 HLS 由 yt-dlp 委托给**外部 ffmpeg**下载，所以本项目 `Popen` 拿到的进程是
ffmpeg 的父进程，不是握着媒体管道的那个：

```
python(yt-dlp).exe  →  ffmpeg.exe -i <live m3u8> -f mpegts -
```

Windows 上 `Popen.terminate()` 只是 `TerminateProcess` 那一个 PID。ffmpeg 孙进程
活着，并且继承着 stdout/stderr 句柄——于是所有阻塞在管道上的读取线程都读不到
EOF，只能各自把超时耗完（`_TcpPump.stop` 的 3s、`_read_log` 线程 join 的 3s）。

实测证据（`.scratch/stop-latency-20260917/probe_tree_shutdown.py`）：

```
descendants before stop: [(245028,'yt-dlp.exe'), (365516,'ffmpeg.exe')]
A terminate()+wait(8) -> 0.01s timed_out=False     # 父进程立刻就死了
LEAKED ffmpeg grandchildren: [365516]              # 孙进程还在，管道还开着
```

这也解释了为什么旧代码给 `wait()` 留 8s 却几乎从不超时：父进程死得很快，慢的是
后面那些等在管道上的 join。

## 3. 修法

1. **让内核拥有这棵树**（`companion/win_job.py`，新增）：每条下载腿 spawn 时立即
   加入一个 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 的 Job Object。`stop()` 里一次
   `TerminateJobObject` 结束父进程与所有孙进程，管道随之关闭，读取线程拿到 EOF
   而不是等到超时。Windows 8+ 支持嵌套 job，所以这与桌面壳自己的 job 不冲突。
   Job 不可用时回退到原来的有界等待，不阻塞开播。
2. **不再把等待叠加**（`server.py`）：`_teardown_session()` 把三个互不相干的腿
   `asyncio.gather` 起来，Stop / 换台 Start / Start 失败回滚 / 进程退出四条路径
   共用同一份顺序。唯一必须保留的顺序是 `request_stop()` 先于任何下载腿关闭，
   否则它们的 EOF 会让打包 ffmpeg 看起来像"直播中断"。
3. **超时预算重排**（`ytdlp_ingest.py`）：先 `pump.shutdown()`（关闭监听、不 join），
   再杀整棵树，然后才 join，且把 8s/3s 的等待降到 1s/1s/0.5s——它们现在是兜底，
   不再是主要开销。
4. **ASR 收尾有界**（`subtitle_pipeline.py`）：`flush()`/`aclose()` 各自加
   `wait_for` 上限（2s），原先只有一个 2.5s 的 drain 上限；一个不回包的 WebSocket
   曾经可以无限拖住 Stop。
5. **界面把停止当本地动作**（`web-player/player.js`）：点击立刻 `destroyPlayer()`
   + 回到「已停止」，`/api/stop` 在后台跑。新增 `stopRequested` 挡住状态轮询——
   后端清理期间它仍报 `state=running` 且带 `playlistUrl`，不挡的话轮询会把刚被
   停掉的直播重新 attach 回来（实测：画面会回来）。下一次 Start 会等
   `pendingStop` 落定。

## 4. 实测结果

| 场景 | 修前 | 修后 |
| --- | --- | --- |
| `/api/stop`（纯媒体） | 9.79s | **0.02s** |
| `/api/stop`（带真实字幕管线） | 3.39s | 2.36s（ASR 收尾上限不变，媒体腿已并行） |
| 点击到界面停止（真实 App，CDP 实测） | 等整个往返 | **3.0ms**（click handler 返回） |
| 停止后 3 秒内画面 | — | 未重新 attach、`currentTime` 零推进 |

真实 App 内的测量（`verify_app_stop.py`，连的是用户正在跑的那个窗口）：

```
media before the click: {'paused': False, 'currentTime': 3.116, 'readyState': 4}
>>> CLICK HANDLER RETURNED IN 3.0 ms (/api/stop requests issued: 1)
    immediate UI: {label: 停止, busy: null, spinnerHidden: true, sessionActive: false, paused: true}
verdict: STAYED STOPPED
server after stop: {"state": "idle", "uptimeSeconds": 0}
```

## 5. 回归怎么保护

* `tests/test_ytdlp_ingest.py::ProcessTreeOwnershipTests`：用真实的"父进程 + 持有
  管道的子进程"复现孙进程问题。把 `TerminateJobObject` 关掉后这个用例会**挂住**
  （读取线程永远等不到 EOF）——这正是它要防的病，不是断言松紧问题。
* `tests/test_web_assets.js`：断言 `stop()` 在 `resetStoppedUi()` 之后才发
  `/api/stop`、`stopRequested` 存在、`start()` 会等 `pendingStop`。
* `tests/test_browser_smoke.py`：后端 stop 故意慢 1 秒，断言点击**当帧**就已是
  已停止状态（不再出现「正在停止」+ 转圈）。

## 6. 边界

* 2.36s 仍是带字幕时的后端清理时间，主要由 ASR 收尾上限（等 Provider 吐出最后
  一个 final，不丢最后一句）构成，这是**有意保留**的：界面已经不等它了。若将来
  要把这个服务端时间也压下去，起点是那个上限，而不是再动管道等待。
* 用户在停止后不再关心最后一句是否上屏，所以这里没有为 Stop 引入"短 drain"模式；
  Provider 换台（Start 路径）走的仍是同一个 `stop()` 契约。
* `.scratch/stop-latency-20260917/backend-backup-before-refresh/` 是重建冻结后端
  前的备份（旧后端），确认新版没问题后可删。
