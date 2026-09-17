# M1：翻译失败发生在哪一段（阶段归因）

日期：2026-09-17。依据：[决策文档](../LingerLens-performance-remediation-decision-2026-09-17.md) §5.4 的 M1、§5.3 的 C2 门槛。
方法：只读本地日志与配置，**未修改产品代码、未联网、未运行直播**。

---

## 1. M1 要回答的问题

> 期限主要**已在排队时**消耗，还是 **provider 服务阶段**占满，还是出现**短预算兜底边界**？
> 对原因不清楚的项写明"没有足够阶段证据"，而不是把 provider 总耗时等同于连接握手成本。

---

## 2. 先修正一个此前的错误分类

我先前把 75 条失败按关键词分成"57 TimeoutError / 13 maxTokens / 9 deadline"。**那个口径是错的**：`RuntimeError: all translation providers failed: A: TimeoutError; B: ...` 这类行的细节里含 "TimeoutError"，被错误地并进了裸超时。

按**异常来源**重新分（裸 `TimeoutError` = 管道自己的 `wait_for` 到期；`RuntimeError` = 链把 provider 逐个耗尽）：

| 日志 | 裸 TimeoutError<br>（管道 wait_for 到期） | RuntimeError<br>（链耗尽） | 其中 fallback 收到已过期 deadline |
|---|---:|---:|---:|
| 09-16 12:38–14:48（130 分钟） | 17 | 17 | 2 |
| 09-16 16:24–17:02（38 分钟） | 24 | 8 | 6 |
| 09-16 17:05–17:26（21 分钟） | 7 | 1 | 1 |
| 09-17 01:35 | 1 | 0 | 0 |
| **合计** | **49** | **26** | **9** |

**主因不是"某个 provider 坏了"，而是"整条链没能在该 cue 的预算内跑完"（在已记录的 75 行里占 65%）。**

**这 75 是什么、不是什么。** 75 是**已被记录下来的失败事件行数**，不是失败总数，也不等于 `/api/status` 的失败人群：

- `subtitle_pipeline.py:1360` 对**每一次** provider 阶段失败都累加 `translation_failures`，但 `:1364` 只在 `_translation_failure_recorded` 为假时才写日志，而这个标志只在**一次成功翻译之后**（`:1333`）才被复位。**连续失败因此被折叠成一行。** 所以这 75 行是 provider 阶段失败次数的**下界**，真实次数无法从这批日志还原。
- 队列等待造成的到期走 `:1337` 的独立分支，**只加计数、不写日志**（见 §3），完全不在 75 里。
- 因此下文所有 49/26/9 都是**对这 75 行记录的分类**，不是对全部失败人口的分类，也不能与 `/api/status` 的 `translationFailures` 互相充当分子或分母。

---

## 3. 决定性的结构事实：预算在**入队时**分配，而队列等待的到期**不写日志**

`translation_budget.py:59-78`：

```python
deadline = now + provider_timeout_seconds          # 配置里 = 6 秒
if playback_delay_seconds is not None and audio_end_wall is not None:
    age = max(0.0, wall_clock() - audio_end_wall)  # 这段音频结束至今多久
    playback_window = max(0.0, target_delay - age)
    deadline = min(deadline, now + playback_window)
```

即 **`预算 = min(6 秒, target_delay − age)`，且在 cue 入队时就定死**。所以队列等待是**花在这笔预算里的**——`translationWorkers = 4`，源一旦停顿再成串补齐，排在前面的 cue 会把后面 cue 的剩余时间吃掉。

而这条路径**在开发日志里没有痕迹**：

- `subtitle_pipeline.py:1237` 在"等不到空闲 worker"时抛 `TranslationDeadlineExpired`；
- 该类**子类化 `asyncio.TimeoutError`**（`:182`），worker 在 `:1337` 单独 `isinstance` 捕获它 → 只累加 `translation_deadline_expired` 并 `_drop_translation_cue`，**不进 `_mark_translation_failed`，也就不会打印**；
- 日志里唯一那行 `translation failed, showing source text only: …` 出自 `:1370`，只覆盖 provider 阶段。

**结论：日志里的 75 条全部是 provider 阶段的失败；队列等待造成的到期是一条静默的、日志中不可见的失败人群。**

> **因此，"排队 vs provider"的拆分无法从这些日志得出——没有足够阶段证据。** 能给出它的计数器（`translationDeadlineExpired`）存在于 `/api/status`，但这些会话从未被记录。这正好是 M0 要补的分母。

---

## 4. 已经确立的（都有出处）

1. **49/75 是管道自己的 `wait_for` 到期**（`subtitle_pipeline.py:1292`）。链还在跑，cue 的预算先用完了。这条路径计入 attempts 与 failures。
2. **26/75 是链把 provider 逐个耗尽**。其中：
   - **9 次**明确记录 **fallback（`translation-1` / label `deepseek`）收到的是已过期 deadline**；
   - **12 次**（只出现在最早的 12:37 那次会话）是 fallback 撞 `maxTokens` 截断（`finish_reason=length`）。
3. **那 9 次的机制是精确的**：`providers/http.py:42-49` 的 `request_timeout()` 在 `deadline − now ≤ 0` 时**不发请求**，直接
   `raise asyncio.TimeoutError("translation deadline has expired")`。所以这 9 条 = **兜底被叫到的时候，请求已经过期，它连发都没发。** 这 9 行**记录为**兜底收到已经过期的期限。**限制：**这些日志只有异常名、没有调用栈，所以它证明的是"记录里出现了这句话"，不是"所有运行形态下这句话都一定在这个位置抛出"。
   - **附带的二阶伤害**：这个"未发出请求"的 TimeoutError 被链当成 provider 失败计入 `consecutive_failures`，**可能让一个本来健康的兜底进入冷却**——这是从代码路径读出的可能性，不是这批日志测到的效果；它由 B2-R 的回归用例来证伪或确认。
4. **当前配置**（非密钥字段）：主 = `bailian-qwen35-flash`，label `gemini-3.7-flash-low`，指向**本机网关**，`timeoutSeconds = 6`，`maxTokens = 256`；兜底 = `translation-1`（`deepseek-flash`，真实远端 API），`timeoutSeconds = 6`，`maxTokens = 1024`，`reasoningEffort = none`；`fallback_reserve_seconds` 默认 2.0。
   → **整条链总共只有 6 秒**，主服务被截到 4 秒，兜底 2 秒。
5. **主服务的失败形态是超时**：16:24 与 17:04 两次会话共 9 次链耗尽，其中 8 次把主服务记为 `TimeoutError`（唯一例外是 16:44:11，那次只列了兜底 `deepseek: ProviderRefusalError`，主服务当时应在冷却）。而它指向的是**本机网关**。
6. **失败与源停顿在时间上重合**：16:22 那次会话 38 分钟内有 **33 条** `直播源已停 N 秒（upstream）`，缓冲一度降到 0；32 次失败绝大部分落在停顿最密的 16:36–16:55。
7. **`maxTokens` 改动的效果是混合的**：`finish_reason=length` 从 12 次降到 0 次，但同期**裸超时占该会话失败的比例上升**：17/34（50%）→ 24/32（75%）→ 7/8（88%）。**这两件事可能相关**——兜底获得更多生成空间就更慢，于是从"截断"变成"超时"。这是假设，不是结论；见 §6。

---

## 5. 决策输出

**C2（HTTP session 复用）不升级。** 按 §5.3 的门槛，"只有连接建立成本被拆出且足以影响按期完成时"才批准。本次证据**没有**任何一项测量到连接建立：`http.py:70` 每次新建 session 是事实，但它的耗时占比在这批数据里完全不可见。**不能把未知成本直接归给 C2。** C2 维持暂缓。

**最小该动的环节（候选，不是决定）：不是连接，是"6 秒预算 vs 配置的 provider"。** 依据：全部 75 条失败都发生在 provider 阶段或预算到期，没有一条指向连接建立；而主服务是一个本机网关，在 6 秒总额里只分到 4 秒。

按 §5.4 的规则，下面每一项都**另作产品选择**，不由本报告自动执行：更换/修正主服务、调整 `timeoutSeconds`、调整 `fallback_reserve_seconds`、调整 `maxTokens`、增加并发或放宽字幕期限。

**B2 已落地（`ce1e47e`），但它修的是一个机制，不是这 9 次事件。** 原稿在这里写过"恰好命中第 3 条里的 9 次，并且顺带消除了兜底被误判冷却这条误判"——**该结论已撤回**：那 9 行是历史记录，修复之后没有任何测量重新跑过同样的边界；"消除了"是断言而不是结果，而且它把"日志里出现过的 9 行"当成了"当时真实发生的全部该类事件"。正确表述是：B2 修好了一条预算分配路径，**剩余边界由 B2-R 的回归用例证明**，而它挽回了多少历史事件、带来多少收益，**本轮未验证**。

---

## 6. 这份报告不能证明什么

- **不能给出排队与 provider 的阶段占比。** 队列等待的到期静默无日志，相关计数器未被记录。见 §3。
- **不能把"裸超时占比上升"直接归因于 `maxTokens` 改动。** 各会话的直播源、画质、时长与失败密度都不同（0.26/0.83/0.38 次每分钟），样本也只有 4 段。要确认只能做同源同配置的前后对照。
- **不能把主服务超时归因于本机网关本身。** 只知道它超时，不知道它慢在哪一段。
- **不能据此宣称"翻译失败率"是多少。** 缺分母（总 cue 数 / attempts），且队列到期那条路径根本不在日志里。§2 的 75 是**计数**，不是比率。
- **不能证明 §4.6 的时间重合是因果。** 只有时间上的比邻，没有逐 cue 的关联。

---

## 7. 复算方式

```powershell
# 按异常来源分类（注意：不要用关键词匹配，RuntimeError 的细节里也含 TimeoutError）
Get-ChildItem .scratch\dev-logs\*.log | ForEach-Object {
  $l = Get-Content $_.FullName
  "{0}: bare={1} chain={2} expired={3}" -f $_.Name,
    ($l | Where-Object { $_ -match 'showing source text only: TimeoutError\s*$' }).Count,
    ($l | Where-Object { $_ -match 'RuntimeError: all translation providers failed' }).Count,
    ($l | Where-Object { $_ -match 'deadline has expired' }).Count
}
```

配置读取（密钥字段必须遮掉）：`%APPDATA%\lingerlens\runtime\providers.json`。

**四个日志文件与配置都在工作区之外或 `.scratch/` 内（Git 忽略），不在仓库里。**
