# YouTube 实时聊天空白：启动认证生命周期缺陷

日期：2026-09-07
直播间：https://www.youtube.com/watch?v=0QhfyMcP_mA

## 现场证据

读取用户正在运行的 8765 服务，视频 running、playlistReady=true、媒体时钟 available=true；聊天 connecting、received=0、pendingClock=0、lastError=null。因此消息尚未进入存储，并不是前端因翻译失败隐藏消息。

独立接收器带现有 YouTube Cookie 测试同一直播间 40 秒，两组（继承当前代理 / 显式 Clash 7890）都收到并发布 18 条消息。之前的 kxinVW3goKc 匿名请求遇到机器人检查，使用现有 Cookie 则成功。当前证据不支持 Cookie 过期或代理是本次空白的主因。

## 根因与复现

视频下载首批数据后释放 media_ingest 认证消费者；handle_start 的 finally 又释放认证初始引用。当弹幕 asyncio 后台任务随后第一次 acquire 时，会话认证已关闭。YouTube 的 acquire 还放在 try 之外，异常使任务退出，状态一直留在 connecting。

新增 test_chat_can_acquire_auth_after_media_releases_and_start_returns，经过真实 /api/start 处理器，模拟媒体先释放认证、弹幕稍后申请。修复前稳定失败：session auth expired before the chat task starts。修复后通过，并验证再次 acquire（重连）与 /api/stop 清理。

## 最小修改

- server.py：成功启动后的会话保留内存认证引用，直到停止、替换或退出时 force_close。媒体/弹幕各自临时 Cookie 文件仍由各消费者独立清理。
- youtube_chat_ingest.py：认证申请纳入异常处理；进程启动先报告 connecting，接收到消息再报告 running 并清除旧错误。
- player.js：聊天栏显示接收状态、错误和等待媒体时钟数量，避免失败被“收到 0”掩盖。
- 两份相关测试补充认证启动时序、重连可用性、关闭认证异常可见性。

没有增加网络请求、轮询频率、并发、定时器或数据缓存。新增常驻内容只是当前会话已有认证信息的内存引用。没有修改认证文件或 Provider 设置。

## 正式链路实测

隔离服务 8796，调用真实 /api/start，使用用户已存 Cookie 和默认网络设置；真实视频下载与 HLS 封装开启，ASR 和翻译关闭，不产生模型调用。

| 启动后 | 已接收并发布 | 按采集时钟后退 15 秒已到显示时间 |
| --- | ---: | ---: |
| 20 秒 | 2 | 0 |
| 40 秒 | 18 | 2 |
| 60 秒 | 61 | 18 |
| 80 秒 | 89 | 61 |
| 90 秒 | 89 | 73 |

全程重连 0，pendingClock 0，无接收错误。浏览器实际播放约 15 秒延迟的 HLS，并在 chatTimelineList 看到真实弹幕，包括“マジでこの家地図いる”“クオリティ上がってきとる”等。不是只验证回调计数。

首批新消息需要完成 YouTube 聊天初始化，再等本地视频播放到对应时间；20 秒时收到消息但尚未显示符合延迟播放契约。不能通过提前显示弹幕来掩盖接收器故障。

原始统计：.scratch/youtube-display-e2e-result.json。
可复现脚本：.scratch/youtube-display-e2e.py（读取本机认证，不输出 Cookie；90 秒后停止隔离服务）。

## 回归验证与边界

- test_live_messages.py：12 项通过，包含先失败后通过的启动路径测试。
- test_youtube_chat_streaming.py：2 项通过。
- test_server_providers.py：24 项通过。
- test_live_messages_client.js：7 项通过。
- test_web_assets.js：8 项通过；player.js 语法通过。

本次只验证 YouTube 这一直播间的 90 秒链路，不代表长期在线和所有网络情况均无故障。用户原有 8765 服务未重启，其内存仍是旧后端逻辑；需要通过 start-laglingo.cmd 重启并重新启动直播后加载修复。
