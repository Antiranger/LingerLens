# 弹幕代理复测：2026-09-07

## 已取得的真实证据
- 使用 HTTP 代理 http://127.0.0.1:7890 调用生产 TwitchChatIngest，频道 guanweiboy。
- 首轮观测累计接收：10 秒 8 条，20 秒 15 条，30 秒 19 条；重连 0，lastError=null；正常调用 stop 退出。
- YouTube 同时通过相同代理调用项目内置 yt-dlp，地址 2SPSYjb-K7U；进程退出码 0，提示没有 live_chat 轨道。
- 单独提取元数据确认 is_live=false、live_status=post_live、availability=public，subtitles 和 automatic_captions 均为空。只能证明这个地址在测试时已结束且本次没有返回聊天轨道，不能据此证明 YouTube 接收器可用或平台没有聊天记录。

## 本轮修复及验证
- Twitch 收到首条 PRIVMSG 后使用了不存在的 clock.wall_to_pts 和不兼容的 LiveMessage 构造、store.add 参数。改为共享 add_with_result，只有新消息计数和回调，支持同帧多条与去重。
- server 调用不存在的 _proxy_url；移除此调用，由接收器接受显式代理或读取系统/环境代理。当前 Python getproxies 未检测到本地代理，因此本轮真实测试显式传入用户给出的端口。正式运行自动继承代理仍需验证。
- 撤回之前忽略 .part 文件的改动：没有证据证明它修复 NotADirectoryError，且可能漏掉尚未结束的直播文件。补齐 HTML 分析所需 re import。
- 新增 test_twitch_chat_ingest.py：同帧 100 条消息重复投递两遍，验证全部 100 条存储、只回调 100 次、代理参数传入、停止任务回收。通过。

## 纠正旧报告
- Bilibili Cookie 修复此前只用于 WebSocket，未传入 _resolve_connection 的 HTTP 请求；此前声称初始化已携带 Cookie 不成立。Cookie 是否过期未验证。
- Twitch 状态字典之前没有 connected 字段，零错误不能证明 WebSocket 已打开。
- 此报告不宣称三平台完成、不宣称高吞吐 CPU 压测已通过。Bilibili 鉴权、YouTube 活跃直播样本和端到端高负载验证仍待完成。

## 新的 YouTube 活跃直播复测
- URL: https://www.youtube.com/watch?v=kxinVW3goKc
- 代理: http://127.0.0.1:7890；未调用翻译或 ASR。
- 原始下载观察 40 秒：识别出 live_chat，持续写入 .json.part，解析 21 条。
- 修复后生产接收器第一轮 40 秒接收 21 条，0 重连，但退出存在 Windows 管道残留。
- 补齐仅针对本接收器拥有的子进程树清理后，再跑完整 40 秒：10 秒 0 条、20 秒 3 条、30 秒 24 条、40 秒 52 条；0 重连、0 接收错误，退出码 0，无管道析构异常。
- 接收 Python 进程 CPU 时间 0.203125 秒 / 40.05 秒（约单核 0.51%），不含 yt-dlp 和前端；不是全链路压测。
- 代码：显式代理参数、持续读取 .part、忽略未合并 Frag 文件、保留跨读取的半行 JSON、每次最多读 1 MiB、行长限制 4 MiB、合并 stderr 并禁用下载进度输出、临时目录删除前终止子进程树。
- 窄测试：YouTube 相关 4 项以及 live_messages.YouTubeSourceTests 1 项通过。
- 结果 JSON: .scratch/youtube-kxin-probe/production-result.json
- 仍未宣称正式播放器代理配置已完整接入或 Bilibili 已修复。

## Bilibili 房间 30858592 复测
- 已保存 30 个 Cookie，含 SESSDATA，导入记录无过期项；实际 /x/web-interface/nav 返回 HTTP 200 / code=-101 / isLogin=false。说明服务端未认可当前登录态，不等于证明 Cookie 日期到期，也不能确定 -352 唯一原因。
- 请求 Accept-Encoding: identity 后，房间初始化返回 code=0、live_status=1。此前 aiohttp 默认压缩路径出现 ClientPayloadError，本轮身份编码下可解析，不能泛化归因为 IP 风控。
- getDanmuInfo 返回 HTTP 200 / code=-352，无 token。空鉴权 WebSocket 无消息；已修正为明确错误状态和已有历史接口降级，避免不断重建空鉴权连接。
- 生产接收器降级实测 40 秒：10 秒 18 条，20 秒 31 条，30 秒 46 条，40 秒 51 条，回调数与接收数一致、0 重连，正常退出。包含初次历史窗口，不能称 51 条全为启动后新增；计数持续增加证明可收后续更新。
- 状态 polling / connected=false，明确没有通过 WebSocket 长连接验收。历史接口只有最近 10 条，高峰可能漏消息，不满足最高吞吐目标。
- Cookie 现在确实传入 room_init 与 getDanmuInfo 两个 HTTP 请求；鉴权包 code=0 才进入 connected=true。
- 测试：test_bilibili_chat_auth.py 2 项、BilibiliSourceTests 1 项通过。原模拟接口仍使用 POST 获取 token，已修正为与真实 GET 路径一致。
- 待解决：取得服务端认可的登录会话后重测 getDanmuInfo 与长连接；不能把轮询降级当作三平台最终完成。

## 请求循环专项检查
- Twitch 正常断线原来没有 sleep，现统一经过 1 / 1.5 / 2.25 ... 最大 10 秒退避；连接稳定 30 秒才重置。
- B 站成功轮询仍为请求完成后等 1 秒；失败改为 2 / 4 / 8 ... 最大 20 秒，成功恢复 1 秒。去重缓存固定最多 500 项，按到达顺序移除最旧项。
- 前端重复 startPolling 原来可重复启动；现增加运行标记和轮次号，停止前的未完成请求不会重启旧循环。默认 500 ms 请求本地服务，afterSeq 增量获取，DOM 和缓存最多 500 条。
- 专项测试：Twitch 2 项、B 站鉴权/退避 3 项、前端 7 项通过。
- 这不是长时间全平台 CPU 压测结论，正式播放器代理贯通仍待验证。

## 2026-09-20 WBI 修复及三平台复测

- 根因确认：B 站当前网页播放器对 `getDanmuInfo` 请求加入 `web_location=0.0` 和 WBI 参数 `w_rid`、`wts`。原接收器只发送 `id`、`type`，所以服务端返回 HTTP 200 / 业务码 `-352`。
- 修复位置：`prototype/hls-companion/companion/bilibili_danmaku_ingest.py`。接收器现在按网页端规则签名请求；Cookie 仍只作为可选的 HTTP/WebSocket 会话信息，不需要为了公开房间硬编码或索取用户 Cookie。
- 回归测试：新增 `test_danmu_info_uses_wbi_signature_and_web_location`，与原 B 站鉴权测试共 5 项全部通过；BilibiliSourceTests 1 项通过。
- 真实 B 站房间 `https://live.bilibili.com/6`：15 秒内 `connected=true`、`state=running`、重连 0、正常停止并清理。该房间这段观测期没有产生新的弹幕，因此不能把 0 条写成“弹幕接口失败”；它证明 WBI 请求和 WebSocket 鉴权已通过。
- 真实 Twitch 频道 `twitchplayspokemon`：20 秒内保持 `connected=true`、重连 0、收到 8 条，正常停止。
- 真实 YouTube 直播 `https://www.youtube.com/watch?v=kxinVW3goKc`（HTTP 代理 `127.0.0.1:7890`）：35 秒内保持 `connected=true`、重连 0、收到 4,838 条，正常停止，拥有的 yt-dlp 子进程树已回收。
- 三个平台的真实测试均未调用翻译或 ASR；B 站这次验证重点是 `-352` 的鉴权链路，弹幕数量受房间当时活跃度影响。
- 修复后重新构建了安装包和免安装版；打包版依赖检查、桌面测试 25 项和桌面 Python 测试 6 项通过。随后用打包版反复探测 B 站时，出口 IP 触发了 B 站 `-412 request was banned`，所以没有把这次被限流后的打包版探测写成通过；源代码版在触发限流前已完成 WebSocket 鉴权成功的真实复测。
