# Handoff：字幕链路问题排查（交接给下一个会话）

> 日期：2026-08-29。写给你：接手修复实时字幕的下一个模型。
> 前任会话（我）实现了整条字幕管线并做了三轮修复，但**用户判定字幕仍有严重问题**。本文是诚实的交接：实现地图、已验证的部分、用户症状、以及我自己审出的全部嫌疑点（含两处从未实测的关键假设）。
>
> 前置阅读：`docs/subtitle-pipeline-implementation-plan.md`（原始规格，含 §0 三条修正）→ 本文。
>
> **⚠️ 2026-08-30 更新：根因已定位，落地请以 `docs/subtitle-alignment-redesign.md` 为准。**
> 本文 §5 的嫌疑清单中，**S1（`hls.playingDate` 不存在）已排除**——该属性确实存在于 vendored hls.js。
> 真正的三个独立故障是：tee 挂载时机导致两条腿无共同字节原点、显示门控不等译文、ASR 断句粒度过粗。

---

## 1. 当前状态一句话

播放链路（yt-dlp 双腿 → FFmpeg 封装 → 延迟发布 → hls.js 播放）**稳定可用**；登录 Cookie 导入/持久化**可用**；ASR/翻译供应商配置 UI **可用**；**字幕时间轴与画面不同步、出现点/持续时长不符合预期**——这是当前唯一但核心的问题。

## 2. 实现地图（本轮新增/修改的全部文件）

### 服务端 Python（`prototype/hls-companion/companion/`）

| 文件 | 职责 | 关键位置 |
|---|---|---|
| `providers/base.py` | ASR/翻译抽象（能力位、事件、请求/结果） | 全文件，纯数据类 |
| `providers/asr_qwen_realtime.py` | kind `dashscope-qwen-realtime`：`qwen3-asr-flash-realtime` 的 OpenAI Realtime 形状 WS。`_map_event`（L100）把服务端事件映射为 ASREvent；`speech_started/stopped`、`transcription.text`（text+stash）、`transcription.completed`（transcript） | |
| `providers/asr_dashscope_task.py` | kind `dashscope-task-asr`：任务式协议（begin_time/end_time/words） | `_map_event` L99 |
| `providers/mt_openai_compat.py` | OpenAI Chat Completions 翻译。**顶层 `enable_thinking: false` 只发给 DashScope 主机（按 host 匹配，不是子串匹配），且不由配置项控制**；其他主机只有在配置了 `options.reasoningEffort` 时才附加 `reasoning_effort`（用户当前用的是 `http://127.0.0.1:8045/v1` + `gemini-3.7-flash-low`，替补 `deepseek-flash` 配了 `none`） | `build_payload` |
| `providers/mt_qwen_mt.py` | Qwen-MT 翻译（translation_options） | |
| `providers/fallback.py` | FallbackChain：连续失败 N 次冷却 60s | |
| `providers/config.py` | providers.json 加载/校验/脱敏/原子写；`update_model_settings`（模型设置 UI 的后端，ASR 固定 qwen3-asr-flash-realtime） | |
| `subtitle_text.py` | 纯函数：NFKC 清洗、噪声标签删除、重复标点折叠、丢弃规则、去重（前缀<3）、≤80 字切分、字符比例内插时间、hold=clamp(1.2, 0.06*len, 8.0) | |
| `subtitle_store.py` | Cue（id/tStart/tEnd/hold/src/zh/state/lang/timingSource/revision）+ 120s 有界存储 + `tEnd>since OR revision>sinceRevision` 查询 | |
| `context_manager.py` | 滚动上下文（pairs+seconds 双限）+ 固定 system prompt + HISTORY/CURRENT user prompt | |
| `subtitle_pipeline.py` | 核心。见下表 | |
| `core.py` | `DelayedPlaylistPublisher.pdt_epoch`（L588/634：私有 playlist 首个分片 PDT）；Cookie 解析器（Netscape/header/名称值多行，含 DevTools 多列裁剪） | |
| `ytdlp_ingest.py` | `_TcpPump` tee（sendall 后调用、吞异常计数）；`attach/detach_audio_tee`（音频腿=双腿时 pumps[1]） | |
| `server.py` | 全部 API；`_start_subtitles`（播放启动成功后挂 tee）；`_authentication`（token > 持久化快照 > 浏览器）；`handle_import_cookies`（导入+落盘 `runtime/auth-snapshot.json`） | |

### `subtitle_pipeline.py` 关键函数

| 位置 | 函数 | 说明 |
|---|---|---|
| L282 | `_pcm_reader` | FFmpeg stdout 按 3200B（100ms）切块；**首个字节时锚定 worker_epoch = wall_clock − pcm_offset**；chunk_start 作为偏移 |
| L312 | `_pcm_sender` | `_last_sent_pcm_offset = chunk起点 + 块时长`（块末） |
| L331 | `_asr_manager` | 断线指数退避重连（0.5→8s），重连期间丢弃音频 |
| L372 | `_handle_asr_event` | speech_started→pending_start=last_sent；speech_stopped→pending_end=last_sent−silenceDuration；final→`_handle_final` |
| L384 | `_handle_final` | 清洗→去重→异常窗口抑制→**时间来源三选一**：①event.begin/end_pcm（"asr"）②VAD pending 对（"vad"）③begin=None,end=last_sent（"approx"）→ epoch+t 计算 tStart/tEnd |
| L449 | `_translation_worker` | 串行；积压>2→上下文砍到2对；>4→fallback provider；>8→丢最旧；deadline 超时放弃；记录翻译延迟（last/avg 0.7·0.3 滚动） |
| L577 | `status()` | 返回 pdtEpoch/workerEpoch/**epochDeltaSeconds**/asrSeconds/费用/teeDropped/backlog/degradeLevel/lastError/翻译延迟 |

### 前端（`web-player/`）

| 位置 | 说明 |
|---|---|
| `player.js` L397 `playingWallClock` | 优先 `hls?.playingDate`；否则 LEVEL_UPDATED 缓存的 fragments 里找包含 currentTime 的分片，`programDateTime/1000 + (currentTime−frag.start)` |
| L406 `cueStart` | `cue.tStart ?? cue.tEnd` |
| L411 `renderSubtitle` | 100ms 节拍；显示条件 `cueStart(cue) ≤ t ≤ tEnd+hold`；取 tEnd 最大者；state="src" 时中文行显示「翻译中…」 |
| L381 `refreshSubtitles` | 500ms 轮询 `/api/subtitles?since=&sinceRevision=`；本地 Map；>125s 清理 |
| `index.html` | 字幕控制条（开关/模式/字号/±3s 偏移滑块存 localStorage）、模型设置对话框、Cookie 导入对话框、遥测（含「翻译延迟」行） |

### 测试（全部通过，`npm run test:hls-companion`）

core(6) / ytdlp_ingest(6) / providers(10) / subtitle_text(8) / subtitle_store(4) / context_manager(3) / subtitle_pipeline(6) / server_providers(6) / web_assets(4)。注意 `test_control_ipc.py` 从聚合命令中移除（命名管道环境冲突，已知问题）。

## 3. 凭据现状（重要：spike 现在能跑了）

- `runtime/providers.json` 已存在：ASR=百炼 qwen3-asr-flash-realtime（**apiKeyConfigured: true**），翻译=用户本地网关 `http://127.0.0.1:8045/v1` `gemini-3.1-flash-lite`（key 已配）。
- `runtime/auth-snapshot.json` 已存在：导入的 YouTube 登录 Cookie（持久化，重启自动加载）。
- **这两个文件是用户真实凭据，禁止打印内容、禁止提交、禁止出现在任何 HTTP 响应里。**
- 原计划 §2.1 的四个 ⚠️（completed 是否带 begin/end_time、空闲超时、热词字段、域名）**至今没有实测过**——当时无 key。现在有了，第一步就该跑 `scripts/asr-spike.py` 补上这些事实。

## 4. 用户报告的症状（按时间顺序）

1. **插入点错**：一大段话的字幕不在第一句出现的时间点出现，而是等到最后一句才出现。
2. **持续时长错**：期望整句说话期间字幕一直在（句首→句尾），而不是某个固定窗口。
3. **翻译延迟 3–5 秒**：问链路是否吃得消。
4. 我做了显示窗口 rework（`[tEnd, tEnd+hold]` → `[tStart, tEnd+hold]`，即 cueStart）+ 翻译延迟遥测后，用户重启实测，结论是「**字幕完全都是问题**」（未给更细描述）。Cookie 持久化被确认没问题。

## 5. 嫌疑点清单（我自己审出的，按优先级）

### S1（最高）：`hls.playingDate` 可能根本不存在
规格文档断言 hls.js 内建 `hls.playingDate`，但我**从未在真浏览器里验证过**（当时无环境）。若 hls.js 1.7.1 没有该属性，`playingWallClock()` 永远走 fallback 分支。fallback 依赖 LEVEL_UPDATED 的 `details.fragments`——需要确认该数组在 1.7.1 的字段名/时间域（`frag.start` 是 playlist 时间轴，与 `video.currentTime` 同域）以及 `programDateTime` 是否被解析（public playlist 由 publisher 重写，含 PDT 行）。
**验证**：浏览器 console `hls.playingDate`（一行）；或直接看 console.warn 什么都没打——当前代码静默 fallback。
**若不存在**：把 fallback 修成主路径，或换成 `details.fragments` 倒数第 N 片 + `edge` 对齐。

### S2：tStart 大面积为 null（VAD 事件名/时序不符假设）→ 我的 rework 实际没生效
若真实服务端事件的类型名与 `_map_event` 假设不符（如 `input_audio_buffer.speech_started` 实际是别的名字），或 completed 先于 speech_stopped 到达，则 `_handle_final` 走 "approx"：begin=None，`cueStart` 回退 tEnd → **症状与用户描述完全一致（整段说完才出现）**。
**验证**：跑 spike（现在有 key）抓真实事件序列；或在 `_handle_final` 里对 timingSource 计数（status 加 `timingSourceCounts`）看分布。

### S3：pcm_offset 超前/滞后于播放时间轴（结构性，最隐蔽）
tee 在 yt-dlp 写出字节时即复制，字幕腿 FFmpeg 以 CPU 速度转 PCM；而封装腿按实时消费。yt-dlp `--concurrent-fragments 4` 会**预取**，导致字幕腿的媒体时间可能领先封装腿数秒 → cue 时间戳整体偏"未来" → 字幕晚出现。反之若字幕腿 FFmpeg 启动 probe 慢则偏"过去"。
**验证**：`/api/status.subtitles` 已有 `pdtEpoch/workerEpoch/epochDeltaSeconds`——**读它**。理想 delta 是小常数（±2s 内）；若随时间漂移或很大，就是 S3。修法方向：用 `epochDeltaSeconds` 做一次性服务端重锚，或按 TS 包 PCR/PTS 对齐（原计划 P2 的自动标定，可能不得不提前做）。

### S4：事件延迟导致 last_sent 过冲
speech_started 事件经 WS 网络往返后才被处理，此刻 `_last_sent_pcm_offset` 已比真实语音起点晚 0.2–1s（若 pcm_queue 积压则更糟）。tStart 偏晚。
**验证**：spike 里同时打本地时间戳对比事件流；修法：push_pcm 时记录发送时刻表，事件回溯取"事件内 audio_start_ms/位置"（Realtime 形状的事件里通常带 audio_start 毫秒偏移——映射时没提取，**这是明确的改进点**）。

### S5：显示窗口 rework 的边界
- 多 cue 重叠时取 tEnd 最大者——若长句被切成多片且时间内插有误（`split_with_timing` 只在 begin_pcm 非 None 时内插），片间可能重叠/断档。
- `subtitleSince` 游标用 tEnd 推进 + 服务端 `tEnd>since OR revision>sinceRevision`——cue 更新（revision+1）会重发全量？不，只发 revision 变化的，OK；但 `subtitleSince` 取 max(tEnd) 后，之后新 final 的 tEnd 必然更大，不会漏。低嫌疑。

### S6：翻译 3–5s 吃预算
结构上已处理（串行+降级+deadline+原文占位）。用户本地网关慢。建议换百炼 qwen3.5-flash（关思考 0.4–1.2s）。UI 已显示实测「翻译延迟」。这不是主 bug，但影响长句句首的译文就绪。

### S7（低）：偏移滑块方向
`manualOffset` 加在播放头侧（`t = wall + offset`），语义是"字幕晚 N 秒出现 = 滑块正方向"。用户校准时若直觉相反会困惑。可在 UI 标注。

## 6. 建议的排查顺序（从证据开始，别先改代码）

1. **跑 spike**（10 分钟，现在有 key）：`python prototype/hls-companion/scripts/asr-spike.py --audio <16k单声道wav>`（可用 `ffmpeg -i runtime/media/private/seg_0.m4s -ac 1 -ar 16000 a.wav` 造音频）。回答：completed 是否带 begin/end_time？VAD 事件确切类型名和顺序？→ 直接裁决 S2，可能顺带给出 S4 的 audio_start 字段。
2. **浏览器一行验证** `hls.playingDate` → 裁决 S1。
3. **直播中读 `/api/status`**：记录 `subtitles.epochDeltaSeconds` 随时间变化 + `/api/subtitles` 里 cue 的 `timingSource` 分布 → 裁决 S3/S2 的运行时证据。
4. 三者定位后再动代码。改完跑 `npm run test:hls-companion` + 真实直播 5 分钟（`teeDropped==0`、无 RSS 增长、cue 连续）。

## 7. 铁律（不要为了修字幕破坏这些）

1. **tee 绝不能阻塞/影响播放**：sink 全链路非阻塞、异常吞掉计数；改 `_TcpPump` 时保住这条。
2. **凭据不出服务端**：providers.json / auth-snapshot.json 内容永不进任何 HTTP 响应/日志/命令行。
3. cue 的 `tEnd` 语义（句子说完的墙钟）别改——它是锚点体系的一部分；要改的是**显示窗口**与**时间来源质量**。
4. ASR 重连期间丢弃音频、绝不补时间戳。
5. 不新增 Python 依赖（只有 aiohttp）。
6. `runtime/` 下真实凭据文件不进版本控制。
7. UI 改动跑 `tests/test_web_assets.js`；管线改动跑对应单测。

## 8. 快速命令

```powershell
# 全部测试
npm run test:hls-companion

# 启动（登录态已持久化，无需 cookie 参数）
npm run prototype:hls

# 关键诊断接口
curl http://127.0.0.1:8765/api/status           # subtitles.epochDeltaSeconds / pdtEpoch / workerEpoch
curl "http://127.0.0.1:8765/api/subtitles"      # cue 的 timingSource / tStart 分布
curl http://127.0.0.1:8765/api/model-settings   # 当前供应商配置（脱敏）

# ASR 协议 spike（现在可跑）
python prototype/hls-companion/scripts/asr-spike.py --audio <wav>
```

## 9. 对用户当前配置的提醒

翻译走的是本地网关（127.0.0.1:8045，gemini-3.1-flash-lite），实测 3–5s。若排查后要压缩字幕-译文就绪延迟，优先建议在「模型设置」里换 `https://dashscope.aliyuncs.com/compatible-mode/v1` + `qwen3.5-flash`（同 key 已配，关思考 ~0.4–1.2s），这是配置变更不是代码变更。
