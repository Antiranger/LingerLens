# LingerLens 性能整改实施工作计划（第二轮）

日期：2026-09-17  
读取分支：`Antiranger/LingerLens` / `wip/subtitle-anchor-correction`  
本次固定参考提交：`b7ad4e89db707e7718644a34547a4b1b2595c6ee`（下文简称 **H**）。

本文件是施工设计，不是已经实施或验收的报告。没有修改仓库、访问用户工作区、启动应用或运行产品测试。先完整阅读执行记录，再完整阅读工作单，然后读取三个提交及设计所需的当前接口；不重新审计原九条断言。

**来源口径。** `[仓库]` 是本次读取的提交、代码或文档事实；`[执行记录]` 是用户提供、但本次不能独立复算的本地实验、日志和配置事实；`[方案]` 是本计划提出的设计、数值、阈值或验收合同。后者不是已经测得的性能，也不是未经批准就生效的产品规则。

路径约定：以下代码路径省略前缀 `prototype/hls-companion/` 时，会明确写成 `companion/`、`web-player/` 或 `tests/`。所有行号均指 H；范围用于定位函数，不声称范围中的每一行都会修改。新增文件、符号没有旧行号，标为“新增，拟从第 1 行开始”。被 Git 忽略的文件无法核对函数名、行号和 CLI，本计划不会伪造；相关接线步骤有明确文件交付前置条件。

## 执行总则与覆盖登记

旧计划中“等原 Stop 作者交接”“E1 因文件空闲而优先”的依据已经失效。当前唯一执行者承接本计划全部产品工作；不再等待已经离开的会话。保存还原点的方法可以是已经建立的快照和后续提交，不要求为迎合旧计划再做一次交接。[来源：执行记录 §2.7、§2.12；工作单 §1。]

顶层现象必须单独登记，不能把十条技术缺陷当成全部用户需求：

| 需求 | 当前状态 | 本轮归属 | 允许的结论 |
|---|---|---|---|
| U1 鼠标不跟手／界面卡 | 无条件 disable-gpu 已修、有短时 A/B | 所有改动的不可回退基线 | 保留已验证修复；不外推为所有长时问题已解决 |
| U2 跨屏操作后整机冻结 | 根因未定 | D0 纠正证据措辞；L1 仅被动记录，不触发跨屏实验 | 不结案，不由应用小修代替系统证据 |
| U3 越跑越卡 | 尚无合格的同会话长期测量 | **新增 L1：持续运行退化测量**，当前执行者负责 | 不测就保持未验收；不能从清理十条缺陷推导其已解决 |

每个逻辑修改独立提交。提交前工作树必须可还原，运行时性能测量期间冻结代码与配置。新机制的目的、所有者、终止行为和回滚方案按本文件评审；本次请求是要求设计，不等于批准启动收费长跑、删除 API 或实施新的时钟／会话身份机制。

---

# 5.1 三个已落地提交的复核结论

| 提交 | 正式结论 | 保留什么 | 最小修正 |
|---|---|---|---|
| `3cc2093`，A2 | **需要改：改测试，不撤回产品补丁** | 一个 AbortController、一个计时器覆盖 fetch 与正文；20,000/30,000 ms 预算；无重试 | A2-T：明确等到客户端进入正文读取才驱动截止时间；真正运行 createSerialPoller；补错误路径与资源清理；删除将源码字符串当行为证据的断言 |
| `ce1e47e`，B2 | **需要改：叠加小修，不回退已修好的短预算分配** | 有可尝试兜底才预留；不足预留时跳主；不延长 cue deadline | B2-R：每次尝试前检查原始总 deadline；刷新循环时间；用已有语言能力验证补齐已知资格；区分 budget skip 与真实 provider 错误 |
| `33f3dce`，E1 | **需要改：叠加接口修正，不单独 revert 回五秒重判** | 分类只做一次的方向、publisherStallThreshold 的原下限 | E1-R：删除 recovery 参数的秒数暗码，改为 `upstreamStalled` 布尔值；同步真实调用者和行为测试 |

没有一个提交需要“整体撤回重做”。这不是三项已达到发布验收：A2 的 Electron／Stop 集成证据仍要补，B2 的收益仍要测，E1 仍要纠正接口语义。

## A2：逐个回答三个疑点

**已触发计时器再 clearTimeout 没有问题。** `new Promise` 执行器同步执行，计时器句柄在 finally 使用前已设置；对不再有效的计时器 ID 做清理不会再次触发回调，也不需要另设 `timerFired` 标志。HTML Standard 的计时器操作就是删除 ID 对应记录。

**abort 与 reject 的文案分支差异可以接受。** 当前 catch 把 AbortError 转成超时提示。两种 probe 文案略异，但都是失败而不是成功，也不延长请求预算；不为统一两句文案重写请求层。测试只要求正确的超时类别和路径相关含义，不固定哪个 rejection 必须赢。

**抽取测试是真回归测试，也存在可管理的源码布局耦合。** 它执行出货函数和真实正文流，不是另一份 request 实现；抽取边界变化导致测试显式失败可接受。不能接受的是以 `helper.includes("await response.text()")` 替代正文挂起的行为测试，也不能把手动发第二个请求称作已测试了真实 poller。

另外，服务器 `res.write()` 已发生，不严格等于客户端已经收到响应头并调用 `response.text()`。A2-T 必须握住后者。相关位置：`web-player/player.js:590–643 / request()`；`tests/test_request_timeout.js:23–38 / requestSource()`、`:47–81 / harness()`、`:102–239 / 等待与四个测试`；`web-player/poll-loop.js:24–110 / createSerialPoller()`。

## B2：逐个回答三个疑点及遗漏行

**“可尝试”不是“会成功”。** 禁用、冷却、输入长度是必要的本地排除项，不证明网络、网关、额度或 provider 延迟健康。对于已声明不支持的语言对，仓库已有纯验证函数 `validate_translation_pair()`，应复用它，不为一个已知不支持当前 cue 的兜底牺牲主服务预算。未知／开放世界能力保持可尝试，不探测、不添平行健康状态。[`companion/providers/base.py:469–510`；`fallback.py` 在 ce1e47e 的 diff。]

**把 budget skip 写成 RuntimeError 不会直接修改 health，但会污染“全部 provider 都失败”的叙述。** 不需要新异常类或事件系统；将本次调用内的 `skips` 与真正捕获的 `errors` 分开，最终说明谁未调用、谁实际失败。不得将 skips 用作异常 cause，也不得在只有 skips 时访问 `errors[-1]`。

**预留量循环前计算一次不是当前的核心错误。** 主服务前的这段路径没有 await；预留只作用于 index 0，不应对每一个后续 provider 重复扣两秒。真正已经跨 await 的是后续循环的冷却资格检查：每轮应读取新 `now`。若未来在预留决策和主服务调用之间加入 await，要在主服务调用点重算资格；不是现在就增加锁或不断重算整个链。

**不实现 R≤0 的理由不能成立为链的合同。** 上游在 `subtitle_pipeline.py:1230–1295` 的两次检查只能约束当时的时间；`wait_for` 调度、主服务 await、取消收尾或其他事件循环工作之后，进入兜底时可能已过期。链本身也可被独立调用。最小做法是在每次新 provider 调用前，以请求最初的 D 再检查，且放在 provider 错误处理之外；用现有 `asyncio.TimeoutError` 传播，不导入上层 SubtitlePipeline、不新建与队列过期混淆的异常。

注意区分两个期限：**主服务分到的 D−reserve 到期，应该允许继续兜底；原始 D 到期，才禁止继续新尝试。** 本修正不承诺在不能让出控制权的同步代码中实现硬实时取消。

## E1：按设计重新判断

当前真实调用者先算 `health.active && health.kind === "upstream"`，再把它编码为 0／实测秒数。由于分类阈值至少五秒，旧 `>5` 在这条路径没有新增行为，所以 `33f3dce` 的 E1 不应被记为已经改善实际播放的性能收益。

然而，导出的 `decidePlaybackRecovery()` 仍把参数命名为 `sourceStallSeconds`，`>0` 会误导未来直接传原始秒数的调用者。**显式布尔接口更好，且现在没有文件占用理由阻止同步改调用者。** 传 `upstreamStalled`，而不是笼统的 `sourceHealth.active`，是为了保持“packaging 停滞不阻断追赶”的原行为。

最小骨架（保留其他分支）：

```javascript
// playback-recovery.js
function decidePlaybackRecovery({
  playerBehind, targetDelay = 15, hiddenDelay = 0,
  bufferAhead = 0, upstreamStalled = false, recovered = false,
} = {}) {
  // 原 desiredDelay / behind / buffer 计算不变。
  if (behind === null) return { action: "normal", desiredDelay, playbackRate: 1 };
  if (upstreamStalled === true) return { action: "hold", desiredDelay, playbackRate: 1 };
  // 原 normal / rate / seek 判断不变。
}

// player.js / updatePlaybackRecovery
const isStalled = health.active === true && health.kind === "upstream";
const decision = window.decidePlaybackRecovery?.({
  playerBehind, targetDelay: data.targetDelaySeconds,
  hiddenDelay: data.hiddenMediaSeconds, bufferAhead: ahead,
  upstreamStalled: isStalled,
  recovered: sourceWasStalled && !isStalled,
});
```

不保留 `sourceStallSeconds` 的自动兼容别名；否则又出现两个真值来源。真实调用者、测试和文档同一提交切换。

---

# 5.2 逐项施工计划

W编号是工单标识，不是执行优先级；实际施工顺序与阻塞替代项以§5.3为准。

## 公共验收约定（各项第 5 字段引用）

以下是仓库已有入口，必须在仓库根目录运行；不是本次已经执行的结果。

```powershell
# J：前端行为测试与语法
node scripts/run-hls-js-tests.js
npm run check:js

# P：字幕生命周期
py -3.10 scripts/run-hls-tests.py --only subtitle_pipeline --timeout 90

# V：provider / 预算
py -3.10 scripts/run-hls-tests.py --only providers --timeout 90

# Y：跨模块 Python
py -3.10 scripts/run-hls-tests.py --timeout 180

# C：集成候选的完整门槛
npm run ci
```

现有 runner 自动发现 `test_*.py`／`test_*.js`。新 Python 测试文件须含可执行的 unittest 入口，不以“没有发现任何测试但退出 0”通过。`npm test` 不能替代 C。纯前端小修跑 J，不反复跑慢 Python 或收费长跑；后端和最终集成候选跑 C。数量不固定写死成 564／145，必须报告本次实际发现数、跳过项和结果。

测试中 **D** 表示区别修复前后的用例；**G** 表示原有行为保持用例，旧版本可能已经通过。不得谎称所有新增用例在旧版本都应失败。所有针对旧版本的红绿对照从 `git show <sha>:<path>` 导出到独立临时目录测试，不切换或覆盖正在使用的产品工作树；子进程和 socket 均在 finally 关闭，只停止事先登记的 PID。

## W0 / D0：修正文档证据等级与顶层需求覆盖

### 1. 目标与不变量

承接执行记录 §3.1 的教训，把 U1/U2/U3 的状态、负责人和验证挂到同一张登记表。纠正 M1 和“4.65 秒”中超出证据的叙述，不改原始计数，不重审九条缺陷，不讨论责任归咎。

### 2. 落点

`docs/notes/performance-remediation-execution-record-2026-09-17.md:34–48 / §2.2`、`:116–153 / §3、§3.1`；`performance-investigation-plan-2026-09-17.md / “已录到的那一次”`；`performance-m1-translation-stage-attribution-2026-09-17.md:1–末尾 / §2–6`；`docs/LingerLens-performance-remediation-decision-2026-09-17.md / 统计解释与范围`。对本次未重新逐行定位的旧决策段落，按该标题找位置并在提交记录补确切行号，不编造旧行号。

### 3. 改动形态与数值

保留 49/26/9 及原始记录出处，但改成：75 是已记录的失败事件；49 是记录中的裸超时组，26 是链耗尽组，9 是其中记录为兜底收到过期期限的事件。历史日志与 `/api/status` 的总体失败人群不等价；`_translation_failure_recorded` 还会抑制连续失败日志，不能把日志行数当所有失败次数。

撤回“B2 已消除所有九次及二阶冷却”的结论，改为“修复了一个机制，剩余边界由 B2-R 回归证明；收益和覆盖多少历史事件未验证”。只凭无调用栈的异常名字，不无限扩大为对所有运行形态的准确抛出位置证明。

把三个“排除”改成证据限制：独立采样仍按约五秒节拍执行，不足以排除采样间短停或显示系统冻结；longTask=0 只说明未观测到其定义下的长任务，不排除任务未获调度／页面被限频；currentTime 推进不证明画面真实呈现。统一表述为“4.65 秒 rAF 回调空档”，不升级为已确认 DWM 根因或已确认视频冻结。rVFC 也是呈现给合成器的回调元数据，不是物理屏幕扫描证明。

新增登记至少保留 U1、U2、U3 三项，每项必须填 owner、状态、证据、下一动作或明确不做。每次交接逐行对照上游的现象表：没有承接的条目必须明确写“不做＋理由＋后续归属”，不允许只交接技术缺陷编号；新的用户抱怨追加为新行，不覆盖旧行。无需新建通用需求平台或 CI 文档解析框架。

### 4. 测试清单

D：逐项核对上述四处叙述；旧文档保留“排除／消除”的无条件结论，因此不满足本次证据合同。D：以 U3 查表，必须能落到 L1 的测量和未验收状态；旧交付链没有独立施工项。G：所有历史数字、引用和修改归属仍可追溯。

这是文档验收，不假称产品自动化测试能证明文档因果结论。L1 的合成数据测试另验证“周期、空档、趋势”的分类。

### 5. 验收命令与通过条件

`git diff --check`；只改上述文档，不跑慢产品测试。交付含完整三行覆盖表及证据限定，没有新造本地测量结果。

### 6. 回滚

独立提交 `D0-SHA`。回退只恢复旧文档，不能恢复被撤回叙述的证据有效性；不得随代码 revert 自动把旧结论重新标为已验证。

### 7. 前置依赖

无产品文件占用阻塞。引用 H、执行记录与 M1；无需先取得原始日志才能更正文档的推论强度。

### 8. 风险与失败模式

风险是把“限定证据”写成“原始数字不成立”，或把不查系统根因写成它不存在。评审必须分别检查事实行、推论行和验收状态。

## W1 / A2-T：保留 request 修复，补强回归测试

### 1. 目标与不变量

证明收到头后正文挂起会被同一个预算结束，并证明真实串行 poller 能继续。不得改 20,000/30,000 ms 预算、HTTP API、重试、轮询并发或 Stop 状态语义。

### 2. 落点

`tests/test_request_timeout.js:23–239 / requestSource、harness、outcomeWithin、四个测试`；调用 `web-player/poll-loop.js:24–110 / createSerialPoller`。产品 `player.js:590–643 / request()` 不改。新增用于 Stop 的行为 harness 可被 S1 复用，不能复制产品 start/stop 实现。

### 3. 改动形态与数值

保留抽取出货函数，使用唯一的 `request` 起点与紧邻的 `commonBody` 终点；边界不唯一或 vm 编译失败就明确测试失败。删除 `includes("await response.text()")` 这种源码存在性验收。

真实 fetch 包装只用于见证阶段，不替换响应流：

```javascript
async function observedFetch(url, options) {
  lastSignal = options.signal;
  const response = await fetch(origin + url, options);
  return {
    ok: response.ok, status: response.status, headers: response.headers,
    text() {
      bodyReadStarted.resolve();  // 已进入真正的客户端正文读取。
      return response.text();
    },
  };
}
// run 是产品 request；poller 是真正导出的 createSerialPoller。
const poller = createSerialPoller({
  run: async () => { try { await context.request("/api/status"); } catch (e) { seen.push(e); } },
  intervalMs: 1000, hiddenIntervalMs: 1000, isHidden: () => false,
  setTimer: fakeTimers.set, clearTimer: fakeTimers.clear,
});
poller.start();
await bounded(bodyReadStarted.promise, 3000);
fireRequestDeadline();
await bounded(firstRunSettled.promise, 1500);
fireOnlyPollTimer();
// 验证第二次真实 HTTP 请求、单一 in-flight、stop 后零链。
```

3,000 ms 是 loopback 就绪保护，1,500 ms 是已有“到期后有界落定”保护，不替换产品预算；每个测试外层 5,000 ms，清理 socket 最多 1,000 ms。辅助 Promise.race 的保护计时器也在 finally 清除。所有路径关闭服务器、连接、监听器和 poller。模拟时钟不能误触 poller 的 1,000 ms 定时器来冒充 request 的 30,000 ms 定时器。

### 4. 测试清单

| 类别 | 输入 | 期望 | 修复前／旧测试缺口 |
|---|---|---|---|
| D | 真 HTTP 200，头与半正文，客户端已进入 text | 截止后 reject、signal.aborted=true、零请求计时器 | A2 前 request 永久等正文；原测试仅等服务器 write，阶段证据不够严格 |
| D | 真 HTTP 500 半正文 | 同样有界；错误正文不能逃出期限 | A2 前同样挂起 |
| D | 用实际 createSerialPoller 执行前一场景再完整 200 | 第二轮确实发生，最大并发 1，stop 后 armedChains=0 | 旧测试只是手动发第二次 request，未覆盖这个集成合同 |
| G | 完整 200 JSON、完整 500 JSON、非 JSON、网络拒绝 | 正确 payload／错误类别，零残留定时器 | 这些路径可在旧版已经部分通过，不当成新的性能收益 |
| G | probe 与 status，在头尚未到来和正文已开始两阶段驱动时钟 | 分别 20,000、30,000 ms，超时不成功，无 unhandledRejection | 预算保持；红绿对照用 A2 前源码，不切换工作树 |
| G | abort 与 timeout reject 两种先后顺序 | 两者均为正确超时，不固定 probe 的完整文案 | 原实现产品方向正确，补环境差异覆盖 |

### 5. 验收命令与通过条件

J。新增套件须在默认出货源码上全绿；把 `c3cdb00` 的旧 request 导出到临时文件运行正文 D 用例，必须在保护时限内红而不是挂测试进程。另记录 Electron 下实际调用同一 request 的一次受控“头＋半正文”结果；没有 Electron 证据时保留“Node 集成已验收，Electron 未验收”，不启动真实直播替代该证明。

### 6. 回滚

回退 `A2-T-SHA` 仅撤回新增测试／harness，保留 `3cc2093`。不得为一个测试不稳定恢复“只管响应头”的产品缺陷。

### 7. 前置依赖

已有 `3cc2093`；唯一执行者拥有测试文件。Electron 受控测试需要执行环境，缺失只阻塞那一项证据，不阻塞 J 或其他实现。

### 8. 风险与失败模式

抽取边界漂移须 fail closed；将服务端写入当客户端读入会造成假证明；不清测试 timer/socket 会重演残留进程。watchdog 不得按进程名清理。

## W2 / B2-R：逐次期限与本地资格的小修

### 1. 目标与不变量

总期限 D 内才可开始下一次 provider 调用；预算跳过不算 provider 失败；主服务的局部期限到达仍可兜底。不得延长 D、增加重试／竞速、改变翻译队列、并发、上下文或 maxTokens；不自动调“6 秒”配置。

### 2. 落点

`companion/providers/fallback.py:125–约280 / FallbackChain._could_try_any、translate`（ce1e47e diff 定位）；`companion/providers/base.py:469–510 / validate_translation_pair、TranslationLanguageCapabilities`；`companion/subtitle_pipeline.py:1201–1390 / _translation_worker、_mark_translation_failed` 只作分类保持测试，优先不改产品；`tests/test_providers.py / FallbackChain 现有预算测试组`，新增独立测试可命名 `test_providers_deadline_edges.py`，第 1 行起。

### 3. 改动形态与数值

保留既有 reserve 默认 2.0 秒。新 guard 使用严格 `remaining <= 0`，不加 1 ms 宽限、不把剩余预算重置为 2 秒。

```python
# 骨架：把既有 health 更新分支完整留在原位置，不照抄成第二套。
def _eligibility_reason(self, provider, request, now):
    state = self.health[provider.id]
    if state.disabled_reason is not None:
        return "disabled"
    if state.cooldown_until > now:
        return "cooling"
    limit = provider.capabilities.max_input_chars or 0
    if limit > 0 and len(request.source_text) > limit:
        return "input-limit"
    try:
        validate_translation_pair(
            request.meta.source_lang, request.meta.target_lang,
            provider.capabilities.language,
        )
    except LanguageNotSupportedError:
        return "unsupported-pair"  # 本 cue 不适配，不是网络失败。
    return None

async def translate(self, request):
    D = request.deadline_monotonic
    errors, skips = [], []
    for index, provider in enumerate(self.providers):
        now = self.clock()  # 每轮刷新；上一轮可能 await 过。
        if D is not None and D - now <= 0:
            raise asyncio.TimeoutError("translation chain deadline has expired")
            # 在 provider try/except 外：没有给尚未调用的 provider 记故障。
        reason = self._eligibility_reason(provider, request, now)
        if reason is not None:
            skips.append((_name_of(provider), reason))
            continue
        reserve = 0.0
        if index == 0 and D is not None and self.fallback_reserve_seconds > 0:
            if any(self._eligibility_reason(p, request, now) is None
                   for p in self.providers[1:]):
                reserve = self.fallback_reserve_seconds
        if reserve and D - now <= reserve:
            skips.append((_name_of(provider), "not called: fallback budget reservation"))
            continue
        # 仍复用原来的冷却到期恢复行为；资格跳过不增加 health。
        provider_request = (dataclasses.replace(request, deadline_monotonic=D-reserve)
                            if reserve else request)
        try:
            result = await provider.translate(provider_request)
        except asyncio.CancelledError:
            raise
        except ...:
            # 现有真实调用失败的分类/health 逻辑；errors 只保存实际异常。
            ...
        else:
            # 现有成功恢复 health 的逻辑。
            return result
    # 保留“全部禁用/冷却”的可读原因，不制造 errors[-1] 空索引。
    detail = format_existing_errors_and_explicit_skips(errors, skips)
    exc = RuntimeError("translation chain exhausted: " + detail)
    if errors:
        raise exc from errors[-1][1]
    raise exc
```

`_eligibility_reason` 是建议将已知资格收敛到一个纯函数，不增加持久状态。未知语言能力不作额外推断；只使用已有 validator 的拒绝。配置／认证错误真正发生后，原禁用规则仍保持。

此处到期仍用已有 asyncio.TimeoutError，不搬迁或复制 `TranslationDeadlineExpired`：队列阶段由 pipeline 判定并计 deadline；进入链后的预算耗尽是已尝试的 provider 阶段终止，维持当前 failures 口径。日志前缀变化需同步 D0/M1 的未来解析说明，不回写历史日志。

### 4. 测试清单

| 类别 | 输入（确定性时钟） | 期望 | 改动前怎样失败 |
|---|---|---|---|
| D | now=10，D=10 或 9；直接调用链 | 所有 provider.calls=0；health 不变；TimeoutError | ce1e47e 仍可能跳主后调用过期兜底，或单 provider 直接被调用 |
| D | now=10、D=16；主接收 D=14，恢复执行时钟已到 16.001 并失败 | 兜底 calls=0、兜底 health 不变，整体到期 | 旧链会进入已过期兜底；证明上游入口检查不构成后续保证 |
| D | 兜底冷却到 11；主调用前 now=10，失败返回 now=12，D=16 | 兜底现在可尝试 | 旧循环复用 now=10，错误跳过已经恢复的兜底 |
| D | 兜底声明只支持 en→zh，当前 cue ja→zh；主可处理，D-now=1 | 不为这条兜底预留，主得到原 D；本地不适配不污染 health | 旧 helper 未查已知语言对，跳主或白白缩短主预算 |
| D | 主预算跳过，兜底实际失败；以及只有资格跳过 | 日志区分 not called 与 failed；异常 cause 仅来自真实异常；没有 IndexError | 旧最终“all providers failed”包含合成 RuntimeError skip，语义混合 |
| G | R=1、2、2.01、6；两端快速成功／超时；无 deadline；仅单端 | 沿用批准的分配；所有子 deadline≤D | 已修边界保持，不宣称这几项旧 ce1e47e 会红 |
| G | 主在局部 D−2 到期，原 D 尚有 2 秒；外部 CancelledError；多兜底 | 前者继续兜底；取消原样传播；后续不再各扣两秒 | 防止把主局部超时错当 cue 总到期 |
| G | pipeline 入队已过期 vs 链中途过期 | 前者沿旧 deadline/dropped 路径；后者沿现有 attempted failure 路径；不双计 | 防止更改可观测指标的人群口径 |

### 5. 验收命令与通过条件

V、P，集成候选 C。用脚本 provider 和假时钟测试，不调用收费 API。D 用例在 ce1e47e 对应实现上应失败；所有 G 保持。M0/M1 的同配置实跑只能证明收益，不能替代预算合同测试。

### 6. 回滚

独立 `B2-R-SHA`，回退回 ce1e47e 的短预算策略，不回到原反转边界。回退后重新标记中途到期／资格边界未关闭；若要回退 ce1e47e 本身，须另经产品决策。

### 7. 前置依赖

ce1e47e；批准本地已知语言不适配按 cue 跳过的范围。现有主／兜底配置不变；健康规则实际远端错误分类不变。输入字段和 validator 都在仓库，不需密钥。

### 8. 风险与失败模式

误用 D−reserve 判总到期会饿死兜底；重复预留会消耗多级链；语言未知不能判不支持；并发 cue 可改变共享 health，预留不能保证未来资格永久不变。仅有入口 guard 不证明任意 provider 内部的所有 await 都有硬界，报告不得声称“所有期限型冷却误判永久消失”。

## W3 / E1-R：明确上游停滞布尔接口

### 1. 目标与不变量

源分类只在既有 classifier 做一次，恢复策略只消费结果。不得改变正常追赶的 desiredDelay、seek/rate 阈值、缓冲阈值、用户暂停优先权、packaging 与 upstream 的区别；不降低 classifier 的五秒下限。

### 2. 落点

`web-player/playback-recovery.js:190–249 / decidePlaybackRecovery`；`web-player/player.js:978–1059 / updatePlaybackRecovery`；`tests/test_playback_recovery.js:67–121 / recoveryActionFor 与 E1 新增测试`。其他真实调用者随本提交同步迁移，不按静态秒数保留旧暗码。

### 3. 改动形态与数值

采用 5.1 的 `upstreamStalled = false`／`=== true` 骨架，移除 `const stall` 与 recovery 参数 `sourceStallSeconds`。`publisherStallThreshold` 继续 `max(5, ceil(targetDuration)+2)`，5 秒和 2 秒都是保留值，不是本轮调参。packaging.active=true 仍传 false。

### 4. 测试清单

D：输入 upstreamStalled=true、playerBehind=42、bufferAhead=2，必须 hold；旧函数不读布尔值而返回 normal。D：false 加无关原始 sourceStallSeconds=3，必须不因秒数 hold；旧 >0 会 hold。G：省略布尔值、不合法真值如字符串 "true" 不偷偷触发 hold（接口布尔合同）。G：短分片 1 秒阈值两侧 4.9/5/5.1；长分片 6 秒两侧 7.9/8/8.1；未知 targetDuration 仍五秒。G：packaging-only 不 hold、upstream hold；正常 rate/seek/用户暂停结果不变。

另抽取执行真实 `updatePlaybackRecovery`，以调用 spy 观察它传的布尔值并执行真实 policy；不只在测试 helper 里复制生产调用者。原“传 3 秒要 hold”的 D 测试删除或改为明确布尔测试，因为它是在固定不理想的接口。

### 5. 验收命令与通过条件

J。上述两条新的 D 在 33f3dce 上红，其余 G 保持。当前现网路径行为等价是通过条件，不编造帧率改善。

### 6. 回滚

独立 `E1-R-SHA` 包含函数、调用者和测试，必须整体回退，不能只回一个文件。回退后恢复 33f3dce 的隐性秒数合同，问题重新开放；不单独 revert 33f3dce 恢复重复五秒规则。

### 7. 前置依赖

当前唯一作者即可，无旧文件占用阻塞；与 S1/A3/C3 都碰 player.js，顺序提交而非同时写。

### 8. 风险与失败模式

只改 policy 不改 caller 会漏 hold；把 health.active 直接传入会把 packaging 也 hold；兼容两个参数会恢复双重真值来源。真实 caller 行为测试必须覆盖这三类。

## W4 / S1：由当前执行者接管 Stop 后失败启动与跨标签页接入

### 1. 目标与不变量

这是**独立 Stop 集成修复**，归当前唯一执行者，不塞回 A2 的 request()。解除“start 失败一次，以后永久不接入”的锁存；保留 Stop 点击即本地停止、旧会话清理期间不重新 attach、下一次 Start 等 pendingStop 有界结束的行为。不得通过超时后盲目放行旧会话、重启下载腿或同时启动第二套媒体管线来解决。

只在 idle 清标志不够：A 会话停止到 B 会话开始之间，轮询可能从未看到 idle。另一方面，把 start() 的 finally 改成无条件 `stopRequested=false`，在 Stop 未确认时会接回用户刚停止的 A。这两种做法均不采用。

### 2. 落点

`web-player/player.js:695–799 / start、resetStoppedUi、stop`、`:879–976 / destroyPlayer、refreshStatus`；相关局部状态声明处新增明确标记。`companion/core.py:1000–1088 / LiveSession.__init__、start`、`:1110–末尾 / stop、status`。`server.py` 的 start 响应已经带 `session.status()`，无需另起响应结构。新增 `tests/test_stop_session_handoff.js`（第 1 行起）；后端测试放现有 LiveSession／server 测试或新增 `test_media_session_identity.py`（第 1 行起）。

**为什么增加一个身份字段：** 当前 playlistUrl 固定为 `/hls/live.m3u8`，不能区分 A、B；uptime 是时长，不是身份；pdtEpoch 在准备阶段可能没有值；logbook.sessionId 属于后端进程而不是媒体会话。因此本方案申请一个最小的**媒体会话标识**，而不是猜时间或 URL。[来源：core.py / LiveSession.status；server.py / _subtitle_status；logbook.py / session_id。]

### 3. 改动形态与数值

后端 `mediaSessionId`：每次 LiveSession 成功创建新的 packaging 进程时生成 `secrets.token_hex(16)`，即 128 位随机标识、32 个十六进制字符；整个媒体会话稳定，stop 清成 null；新 start 必须新值。128 位用于避免跨后端重启、同一源重开时混淆，不是认证令牌，不参与权限判断。每会话只生成一次，不开定时任务。已有 status 和 start.status 带出同一个值。

前端只增加两类局部事实：**停止屏障对应的会话身份**和**本页操作代数**。没有额外定时器。示意代码省略既有 UI 更新，但实际修改必须保留它们：

```javascript
let observedMediaSessionId = null;
let stoppedMediaSessionId = null;
let uiGeneration = 0;

function stop() {
  ++uiGeneration;                         // 使已在途的旧 status 失效。
  stoppedMediaSessionId = observedMediaSessionId;
  stopRequested = true;
  resetStoppedUi();                       // 仍然立即完成。
  // 沿用原 alreadyStopped 判断；同一 pendingStop 不重复发请求。
  if (pendingStop === null && needServerStop) {
    const claim = uiGeneration;
    pendingStop = request('/api/stop', {})
      .then(() => {
        // 成功响应确认这次服务端停止完成；旧 status 不能复活它。
        if (claim === uiGeneration) {
          ++uiGeneration;
          stopRequested = false;
          observedMediaSessionId = null;
          stoppedMediaSessionId = null;
        }
      })
      .catch(logExistingStopWarning)       // 超时不是“后端已经停止”。
      .finally(() => { pendingStop = null; });
  }
}

async function refreshStatus() {
  const claim = uiGeneration;
  const data = await request('/api/status');
  if (claim !== uiGeneration) return;     // 在改 UI / attach 之前检查。
  const id = data.mediaSessionId ?? null;
  if (stopRequested) {
    if (pendingStop !== null) return;
    const confirmedIdle = data.state === 'idle';
    const differentSession = id !== null && stoppedMediaSessionId !== null
      && id !== stoppedMediaSessionId;
    if (!confirmedIdle && !differentSession) return;
    stopRequested = false;
    stoppedMediaSessionId = null;
  }
  observedMediaSessionId = id;
  // 原状态展示、attach、缓冲和恢复处理。
}

async function start() {
  const claim = ++uiGeneration;
  // 原 UI 准备；原 pendingStop 等待、语言验证和 request('/api/start')。
  // 每次 await 返回后，在产生本页副作用之前检查 claim 是否仍有效。
  // 成功：记录 data.status.mediaSessionId，再解除当前屏障。
  // 失败：报错但不抹掉已停止 A 的身份；将来新 B 的 status 可解除屏障。
  // finally：仅当前 claim 清 busy/sessionAction，不覆盖后来一次 Stop 的 UI。
}
```

上述 `needServerStop`、`logExistingStopWarning` 是对现有代码段的占位，不是另建框架。实现时直接保留原 `alreadyStopped` 条件和 catch 诊断内容。还需在 refreshStatus 入口避免在**本页正在 starting**期间用旧 server truth attach；成功／失败后的下一次正常轮询再接入。停止成功与本页 start 等待相遇时，只有仍有效的 Stop claim 改 UI，不能让完成回调取消当前 Start。

未知身份按保守路径处理：没有确认 idle，也没有可比较的旧／新 ID，就不自动 attach；记录一次“停止状态未确认”，等待一次明确的 Stop 成功或用户 Start 成功。不得把 unknown 当作不同会话。兼容旧后端时必须显示此限制，不能声称能可靠识别新会话。

请求仍用 A2 的 **30,000 ms** 总界；UI 操作代数只做相等比较，不设时间阈值。新增身份字段及这条接入语义需要用户批准。

### 4. 测试清单

| 类别 | 输入／交错 | 期望 | 改动前怎样失败 |
|---|---|---|---|
| D | 先观察 A；Stop 完成；start 在语言验证处抛错；另一标签页开启 B | B 可被下一次有效轮询接入 | 原 stopRequested 只在本页 start 成功清除，永久跳过 attach |
| D | A 的 status 已发出；本页 Stop；旧 running(A) 响应迟到 | 不 attach，不把停止 UI 改回运行 | 原代码只有标志屏障，后续清除标志后旧响应缺乏代数保护 |
| D | Stop 响应正文超时；随后同一 A / 新 B 分别返回，期间没有 idle 样本 | A 不接回；已知不同身份 B 可以接入 | 原代码无法区分 A/B，全部阻断；简单 clear 方案又会错误接 A |
| D | Start 请求在途时再次 Stop，之后旧 Start 成功返回 | 不覆盖最新 Stop UI，不解除最新屏障 | 原成功分支无操作代数检查，会清 stopRequested |
| D | 同源、同 playlistUrl 两次 LiveSession.start | 身份不同；各自 status 身份稳定；stop 后 null | 旧 status 没有可靠媒体会话身份 |
| G | 连续点击 Stop；正文挂起后驱动 30 秒 timer | 本地立即停止；只有一次停止请求；pendingStop 有界清理 | 保持 A2 和 Stop 已有正确行为 |
| G | 普通成功 Start；没有 Stop 的另一标签页启动；idle | 控件和播放沿既有行为；不额外等待一轮固定冷却 | 防止引入新的启动延迟 |
| G | 无 ID 的旧后端、无 idle、停止未确认 | 不猜测新会话，不接回旧画面，说明限制 | 保守兼容路径，不假称旧版本能提供身份 |

JS 测试抽取**真实** start/stop/refreshStatus，给它们可控 deferred 请求、最小 DOM 和 attach spy；后端测试实际调用 LiveSession.status/start 的出货代码，以假进程工厂替代 FFmpeg。新增测试不得另写一个“理想的 Stop 模型”来代替产品函数。

### 5. 验收命令与通过条件

J、Y，最终 C。至少覆盖“Stop 成功”“Stop 超时”“Start 前置失败”“旧响应迟到”四种交错。真实 Electron 中再做一次经批准的正常 Start→Stop→Start，确认没有新旧会话交叠、残留进程；**单元测试不验证 win_job.py 的 12.6s→0.02s 实测声明**。没有该运行证据时记录集成未验收，不抹掉限制。

### 6. 回滚

`S1-SHA` 将 core 的身份字段、前端屏障和测试一起提交、一起回退。回退后回到原 Stop 语义，A2 请求有界仍在，但失败启动锁存重新开放。不得只回身份字段而保留依赖它的前端。

### 7. 前置依赖

3cc2093；用户批准媒体会话 ID 和接入语义；当前唯一执行者拥有 core/player/test 文件。若用户不批准新增身份合同，S1 暂缓，不偷偷改用 uptime、URL 或“等一次 idle”冒充等价修复；继续做 E1-R/A3/C3 或 A1 已批准部分。

### 8. 风险与失败模式

生成 ID 太早可能给失败启动留下假运行身份；太晚又使 start 响应缺字段；过期 finally 会覆盖新操作 UI。测试必须检查字段生成／清理时机及 UI 代数。该前端修复**不证明**后端来自多个客户端的同时 start/stop 已被全局串行化；若交错实验证明服务端操作本身互相拆新会话，应停下 S1 发布，另明确最小后端串行化范围，不能暗中借本项增加通用会话状态机。

## W5 / A1：排空解码器 stderr，并让生命周期闭合

### 1. 目标与不变量

让错误洪流不再堵住字幕 PCM。保留视频／音频分离、PCM 有界背压、独立 ASR、采样格式、切句、翻译与时钟对齐。stderr 永远不混进 stdout/PCM；日志不能对媒体施加反压。**运行中的 PCM 行为不改；停止后只允许丢弃不再交付的管道尾部。**

### 2. 落点

`companion/subtitle_pipeline.py:330–384 / SubtitlePipeline.__init__`、`:404–483 / start`、`:568–624 / stop`、`:638–660 / _pcm_reader`；新增 `_stderr_reader`、`_emit_decoder_diagnostic`，紧邻 PCM reader；已有 `_record_error`、`log_record` 复用。测试在 `tests/test_subtitle_pipeline.py` 或新增 `test_subtitle_pipeline_stderr.py`（第 1 行起）。只消费现有 `companion/logbook.py:29–59、record()`，不改该模块的环大小／格式。另含 `companion/server.py:1199–1212 / _stop_subtitles` 的最小引用释放时机调整；`_teardown_session:1557–1576` 只验证失败传播，不顺手改全部会话架构。

### 3. 改动形态与数值

**读块照用 4 KiB=4096 字节；保留最近 64 KiB=65536 字节，不按行计上限。** 单行可以任意长，行数上限不能约束字节数。4096 让一次读取和截尾成本小；65536 给近端故障上下文，且每个字幕解码器只有一个尾部。它们是本方案常量，不是实测最优值，不新增用户配置。

```python
_STDERR_READ_BYTES = 4096
_STDERR_TAIL_BYTES = 65536

async def _stderr_reader(self, process):
    since_yield = 0
    try:
        while True:                       # 不以 _running 为条件：Stop 期间还要排空。
            block = await process.stderr.read(_STDERR_READ_BYTES)
            if not block:
                break
            self._stderr_tail.extend(block)
            extra = len(self._stderr_tail) - _STDERR_TAIL_BYTES
            if extra > 0:
                del self._stderr_tail[:extra]
                self._stderr_tail_truncated = True
            self._stderr_bytes += len(block)
            since_yield += len(block)
            if since_yield >= _STDERR_TAIL_BYTES:
                since_yield = 0
                await asyncio.sleep(0)     # 连续已有缓冲时也让出事件循环。
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        self._record_error(exc)
        # 故障处理必须结束本字幕解码器；不能吞错后留下一个无人读 stderr 的写者。
        # 使用同一 Stop 所有者接管，或标记故障并立即 kill 本进程。
        # 不能从自身任务 await 包含自身的 gather；不能启动无人持有的 fire-and-forget 清理。
        raise
```

随 proc 创建登记一个名为 `subtitle-stderr-reader` 的任务，尾部随每次解码器生命周期重置。总内存上界只声称**业务保留尾部为 64 KiB**，不把 asyncio 的 StreamReader 缓冲和操作系统管道也算成这 64 KiB。

**尾部给谁看：给现有诊断栏／`/api/logs` 的读者。** 不新增 status 大字段、不建文件轮转、不持续输出每个读取块。最多在该解码器生命周期内发两份快照：第一次捕获到错误数据的摘要，以及异常退出／Stop 时的末尾摘要。每份最多一条摘要＋最后六条完整非空行；每条继续受现有 logbook **400 字符**与 URL 脱敏限制。两份最多 14 条记录，不会因洪流挤满 500 条环。64 KiB 原始尾部仍只在会话内。

解码用 UTF-8 `errors='replace'`；若截尾切开第一行，导出时丢弃这一不完整行，避免半截签名 URL 绕过以协议头匹配的脱敏。整个尾部无完整行时只报字节数和“超长诊断已省略”，不输出任意切片。检测到 stderr 文本不自动等于致命错误；第一次摘要用 warn，真正解码／读取失败用 error，正常停止尾部用 info。没有 stderr 时不新增空日志。

**需要一并批准的最小生命周期调整。** H 的 stop 并非全程五秒：flush/aclose 共享一次五秒，后面 ASR gather 又给一次五秒，最后 kill 后仍有无界 wait。新增读者不能照搬“先取消全部消费者、再等待进程”的顺序。因此本项包含局部 stop 顺序修正，不伪称只加一个任务便完成验收：

1. Stop 入口取一个绝对期限 `D_stop = monotonic()+5.0`，只用于**本字幕管道**的关闭，不宣称整个后端 Stop 五秒。
2. 立即结束本解码器生产：terminate，正常退出宽限最多 **1.0 秒**（原有值），否则 kill。这个回收与 ASR flush/aclose 并行消耗同一个 D_stop，不另续五秒。
3. 保留 stderr 读者到写者退出。对 stdout，先有界停止原 PCM 交付任务，再由一个只在 teardown 存在的临时排空任务按 4096 字节丢弃尾部；不能两个读者同时读 stdout，也不能把尾部继续送给正在关闭的 ASR。这是进程 PIPE 回收所需，不改变运行时背压。
4. flush、aclose、ASR final 消费、进程回收、两个管道读者均只拿 `max(0, D_stop-now)`；没有 `max(0.1, ...)`，没有新的完整期限。剩余为零就不启动下一段宽限。现有最多约两秒的适配器 closeDrain 配置不改；有时间时仍保留 tail-final drain。
5. 对本模块创建的等待任务用 `asyncio.wait(..., timeout=remaining)` 取 done/pending，再显式取消／回收；不要以 `wait_for` 的 timeout 等同硬墙钟界——它会等待被取消协程的收尾。任务引用、异常和进程句柄都要由当前 stop 所有者持有；读者故障走同一收尾入口，排除 current_task 的自等待。
6. `server._stop_subtitles` 不再在 await 前做 `pipeline, self.subtitle_pipeline = ..., None`；改为先保留 `pipeline=self.subtitle_pipeline`，`await pipeline.stop()` 成功且确认清理完成后，才在引用仍指向该 pipeline 时清空。`_stop_impl` 不能在残留存在时返回成功；抛出现有 RuntimeError，经 `_teardown_session` 阻止新 start。否则底层虽记了失败，上层已经遗忘资源，所谓“阻止覆盖”没有落点。
7. D_stop 到了仍有未退出任务／进程，**验收失败**：保留待清理句柄和明确诊断，禁止宣称已清完或继续新会话覆盖它们。操作系统或第三方协程不合作不能靠“多等无限久”隐藏；由获准的外部 kill switch 只处理登记的实例。不得 `self._tasks.clear()` 后遗忘 pending 对象。

这不是新增全局 TimeoutManager。仅调整现有字幕 stop 的任务顺序与同一绝对期限。若评审不接受这部分生命周期范围，A1 停在设计阶段，不能交付一个运行时排空却在停止时重新死锁的半修复。

### 4. 测试清单

| 类别 | 输入 | 期望 | 改动前怎样失败 |
|---|---|---|---|
| D | 实际本地子进程输出 0.1s PCM→1 MiB stderr→0.1s PCM | PCM 达 0.2s；子进程和读者都可退出 | 原无人读 stderr 时停在 0.1s |
| D | 数 MiB 无换行 stderr；间插 PCM；尾部含分片 UTF-8 | 尾部≤65536字节；PCM推进；事件循环其他任务可运行 | 原管道堵塞；按行无界实现会失去内存界 |
| D | stderr reader 抛错、解码器仍想写 | 同一故障所有者结束该解码器并留下诊断 | 吞错实现留下活写者；不能以任务异常被捕获就通过 |
| D | Stop 时 stdout 与 stderr 都有积压，terminate 无效而 kill 有效 | 无双读、无等待管道 EOF 的死锁；所有句柄回收 | 原取消消费者后等进程可能卡住；kill 后无界 await 没有失败出口 |
| D | 假时钟：flush 已消耗 4.8s，后面步骤需要更多 | 剩余只许用 0.2s；总预算没有第二份五秒 | H 的后续 ASR gather 获得新五秒，最低0.1也会续预算 |
| D | pipeline.stop 以清理未完成报错，随后请求 Start | server仍持有原pipeline；Start失败而不是覆盖未回收实例 | 原_stop_subtitles在await前清空引用，后续可能遗忘残留 |
| D | 尾部从一个带凭据 URL 的中间开始，或单行超过64KiB | 省略截断首行／超长行；诊断无凭据片段 | 朴素截尾后直接 log 的方案会绕过完整 URL 脱敏 |
| G | 正常小错误、无错误；启动失败；重复 Stop | 原 PCM/ASR 结果不变；日志有界；幂等，无残留 | 已有正常路径仍应成立 |
| G | close 时才产出的 tail final，在共享期限内可结束 | 尾句不丢；不改变原切句／翻译顺序 | 防止过早取消 ASR 管理任务 |

真实管道测试由测试本身登记其 PID，并有独立 **10 秒**硬退出保险（高于本模块五秒＋测试准备余量）；假时钟测试不真的等五秒。测试超时不能遗留其合成子进程。

### 5. 验收命令与通过条件

P、Y、C。D/G 全通过；合成 stderr 洪流的 PCM 进度、尾部最大长度、任务数、进程退出码和收尾耗时均写入测试结果。全程未修改真实直播源和 provider 配置。真实长跑后验证停止清理，归 L1 的退出验收；没有实际运行不声称零残留。

### 6. 回滚

`A1-SHA` 包含读者、诊断、局部 stop 顺序和 server 引用释放时机，整体回退。回到 c3cdb00 的生命周期并恢复未修 stderr 缺陷；A2/B2/S1 不回退。若 A1 运行异常先用已批准的精确 PID 清理本次实例，不能在仍有旧进程时只回源文件后启动新实例。

### 7. 前置依赖

c3cdb00 已入库；用户批准 stderr 读者、诊断暴露方式、共享期限内的局部收尾形态；当前唯一作者。它不依赖收费翻译、原始七十五条日志或 M0 两小时结果。

### 8. 风险与失败模式

主要风险不是4096或65536的选择，而是取消次序、stdout未排空、当前任务自等待、协程拒绝取消和诊断反压。任何一个导致进程仍活、句尾丢失或原 PCM 行为变化即不通过。五秒是期望合同和退出门槛，不是对任意 OS/第三方恶意协程的数学保证；达不到就报告失败，不能清空引用冒充回收成功。

**A1 收尾所有者的落地补充。** 不用回调里无主地 `create_task(stop())`。在 pipeline 内保留一个 `_stop_task`：公开 `stop()` 复用并 await 同一个 `_stop_impl()` 任务；该任务不放进自己会取消／gather 的 worker 列表。stderr 任务 done callback 只提取异常、记录、立即 kill 自己仍活着的 decoder，并登记／取得这个 `_stop_task`。start 仅在旧 `_stop_task` 已完成且无 pending 资源后才重置它。示意：

```python
async def stop(self):
    task = self._ensure_stop_task()       # 同一次生命周期最多一个。
    await asyncio.shield(task)           # 调用者取消不遗弃清理所有权。

def _on_stderr_done(self, task):
    if task.cancelled():
        return
    error = task.exception()             # 明确消费异常，不产生未检索异常。
    if error is not None and not self._stopping:
        self._record_error(error)
        if self._process is not None and self._process.returncode is None:
            self._process.kill()         # 仅自己的字幕解码器，不碰下载腿。
        self._ensure_stop_task()         # 有引用、后续 stop 必须 await，不是无主任务。
```

`_ensure_stop_task`/`_stop_impl` 仅是把既有 stop 的唯一所有者写清，不引入独立监督服务；done callback 的 kill 异常也必须记录并交给同一收尾结果。测试覆盖两次同时 stop、读者失败和 server stop 相遇，以及一个等待 stop 的调用者被取消时清理仍完成。

## W6 / A3：接回原补丁，只纠正日志与事实的关系

### 1. 目标与不变量

纠正“日志先说未打扰，随后同一轮却自动暂停”。不改播放／暂停条件、3 秒可见阈值、10 秒缓冲恢复阈值、用户手动暂停优先权、五个 stall 标志的行为。`tests/test_web_assets.js` 在可接受修改范围内；**可改测试文件，不代表只做源码字符串断言足以验收。**

### 2. 落点

`web-player/player.js:1088–1180 / updateStallOverlay`，尤其 `starving`、`quietSourceLogged` 和 `video.pause()` 前后；原补丁同时触及 `tests/test_web_assets.js`。新增行为用例可放 `tests/test_stall_diagnostics.js`（第1行起）。要取得原文件：`.scratch/laglingo-audit/fix-plan/pending-diagnostic-gating.patch`，执行记录给出的大小2590字节、player14+/3−、测试6+作为来源标识，不当作内容证明。

### 3. 改动形态与数值

优先审阅并适配原补丁，不因为本计划另造第二份竞争补丁。接受原补丁必须满足以下行为合同；若原补丁不同，就最小修改其日志分支：

```javascript
const starving = ahead < STALL_VISIBLE_BUFFER_SECONDS; // 仍是3。
if (stalled !== quietSourceLogged) {
  quietSourceLogged = stalled;
  if (stalled) {
    // 只陈述此刻观察，不预告同一轮尚未发生的副作用。
    diagnosticsBar?.push('warn', 'source',
      `源静默 ${Math.round(health.stallSeconds)} 秒（${health.kind}），当前播放缓冲 ${Math.round(ahead)} 秒`);
  }
}
// 原 banner 分支与 return 顺序保留。
if (!video.paused && !stallAutoPauseSuppressed) {
  autoPausedForStall = true;
  video.pause();
  diagnosticsBar?.push('warn', 'source', '缓冲不足，本次已自动暂停等待补充');
}
```

这是可以直接落地的保守文案形态：不必区分未来会不会打扰播放；实际发生自动暂停后才说暂停。沿用 transition gating，不新增每秒心跳日志；一次 source quiet 转换最多一条源事实记录，一次真正自动暂停最多一条动作记录。自动恢复的既有逻辑不动。若原补丁保留“本轮未触发自动暂停”的条件文案也可以，但必须在实际不会进入暂停分支时才写，不用日志推动状态。

`git apply --check` 只验证适用性；正向能应用、反向不能应用并不证明行为“修复前红、修复后绿”。原执行侧已验证的结论保留其真实范围，本轮另补行为测试。

### 4. 测试清单

D：upstream stalled、ahead=2.9、video.paused=false、suppressed=false；真实函数应调用 pause 一次，诊断不得声称未打扰，并应记录动作。旧函数会先打印矛盾文案。G：ahead=3.0、10.0，无自动暂停；G：相同 stalled 状态连续三次刷新，只记录一次转换。G：already paused 或用户 suppression=true，不假称本次自动暂停，也不再 pause。G：autoPaused=true 且 ahead 从9.9到10.0，即使源仍 stalled，也按旧规则 resume 一次。G：error 状态沿既有错误提示和日志，不新增自动恢复。

每条对实际 `updateStallOverlay` 提供最小 DOM、`bufferAhead`、video 与 diagnostics spies，并比较修复前后 pause/play 调用序列；日志断言检查**执行输出**，不检查源码中有没有一句话。原行为保持用例修复前可以绿。

### 5. 验收命令与通过条件

J；补丁应用前先 `git apply --check`，应用后 `git diff --check`。D 在原函数上红，G 的播放副作用完全相同。纯前端日志改动不跑收费长跑或慢 Python。

### 6. 回滚

独立 `A3-SHA`，回退日志和配套测试，不动 S1/E1-R 的其他 player 逻辑。回退后日志可信度缺陷重新开放，不能把它误写成播放暂停机制回退。

### 7. 前置依赖

取得原 patch；当前唯一作者；player.js 与 S1/E1-R/C3 顺序写。缺 patch 时先做 C3/R1/L1 工具，不声称已经审阅或批准这个2590字节文件。

### 8. 风险与失败模式

把诊断条件写成暂停条件会无意改变行为；持续每秒输出会淹没诊断；手动暂停被描述为自动暂停会制造新假证据。动作 spy、重复轮询与 suppression 用例必须覆盖。

## W7 / C3：删除虚假入口，保留旧数据兼容

### 1. 目标与不变量

删除无消费者的 `options.language` 编辑器和 `hotwordsEnabled` 模板／示例项，不把它们“实现出来”。语言只有既有全局 SourceLanguagePolicy；实际 vocabulary/vocabularyId 的热词功能、其他隐藏但有效的 options、完整 catalog 保存与凭据保护不变。

### 2. 落点

`web-player/player.js:1390–1440 / asrOptionFields`、`:1450–1470及后续 / handleProviderInput`（后者主要加行为保持测试，原则上不改）；`companion/providers/config.py:15–35 / BUILTIN_ASR_PROVIDERS`；`runtime/providers.example.json:24 / options.hotwordsEnabled`（位置为工作单已知事实）；`docs/notes/performance-audit-2026-09-17-options.md / language、hotwordsEnabled 两行`。新增 `tests/test_provider_option_ui.js` 或扩展实际 provider UI 测试；后端扩展现有 provider/config 测试。

### 3. 改动形态与数值

**language：删除编辑项，不保留一个明知不生效的可编辑／置灰输入。** 在全局语言设置处仍显示唯一有效来源；旧 catalog 中的键继续原样加载、保存、忽略。兼容的意思是旧数据可读取和 round-trip，不是每个历史无效键都必须永久有编辑框。

**hotwordsEnabled：删除模板与示例那一行，不转换为 vocabulary/vocabularyId。** 布尔值不能推导词表字符串、词表数组或云端资源 ID，自动替换会凭空改变语义。仅在受影响文档行注明真正消费者是已有 vocabulary/vocabularyId。不改用户 runtime/providers.json，不运行批量迁移。

无新阈值、超时或默认值。openai 转写的分窗与 requestTimeout 等原默认值保持；不改 reasoningEffort、contextSeconds、tmPairs 等其他选项。

### 4. 测试清单

| 类别 | 输入 | 期望 | 改动前怎样失败 |
|---|---|---|---|
| D | 执行实际 asrOptionFields(openai-audio-transcriptions) 并构成编辑 DOM | 无 language 输入；有效 window/timeout 输入仍在 | 旧 UI 生成可编辑却无效的语言输入 |
| D | 读取实际 Python 内置模板、解析示例 JSON | 不再产生 hotwordsEnabled | 旧模板和示例持续写入死键 |
| G | 旧 catalog 含 language=en、hotwordsEnabled=false 和一个其他隐藏有效键；编辑 label 后完整保存／重读 | 所有旧 options round-trip 保留；新模板不再新增死键 | 防止删除 UI 时重新构造 options 丢掉旧键 |
| G | 旧 options.language=en 或fr，全局指定ja | 实际 ASR stream 仍按ja | 这是既有行为，不假称旧版本会红 |
| G | 真实支持热词的适配器收到 vocabulary/vocabularyId | handshake/能力行为不变 | 防止用死布尔项清理误伤有效功能 |

测试执行渲染函数并检查生成 DOM／解析后的标签属性，不用在 player.js 源文件中查字符串作为行为证据。后端使用已有 fake stream，不联网。

### 5. 验收命令与通过条件

J、V，后端／集成候选 C。受影响两行配置表同步更新；旧用户文件未被修改，无新增配置默认值，所有 G 保持。

### 6. 回滚

`C3-SHA` 独立回退。恢复旧编辑器和模板承诺，但不需要恢复用户配置，因为从未迁移它。不要用回滚恢复以前已经删除的 enableThinking。

### 7. 前置依赖

用户认可删除入口的产品选择；当前唯一作者。无需原始性能日志或远端 API。与其他 player 改动顺序提交。

### 8. 风险与失败模式

最大风险是保存逻辑删掉“没有 UI 的其他 options”，不是死键继续留在旧 JSON。round-trip 测试必须用未知/隐藏键；不能为了测试好看只使用空 options。

## W8 / D1：删除整个无消费者的 sourceRecovery 投影，而不是只去掉 action

### 1. 目标与不变量

停止发布无人执行的恢复承诺。**建议删除 sourceRecovery 整块及仅为它存在的 RecoveryPolicy 调用／接线**，不只删 action 后仍留下 `state='reconnecting'`。保留 sourceIngest、sourceIdleSeconds、sourceError、源/缓冲分类、已有错误与诊断；不接通自动重连、不重启下载腿。

这是 API 面收缩，不是性能收益承诺。仓库内零消费者不等于仓库外零消费者：**在用户确认可以删除该字段前，本项阻塞。** 不以“只有一个消费者可能存在”为由制造一个永久兼容层。

### 2. 落点

`companion/server.py:320–360 / CompanionApplication.status 中的 sourceRecovery 构造`；同文件 `:61–105 / RecoveryPolicy` 两套导入与 `:176–212 / CompanionApplication.__init__` 中的 `self.recovery_policy` 初始化。`companion/recovery_policy.py:36–40 / RecoveryDecision`、`:43–83 / RecoveryPolicy`。删除其仅为该输出存在的专属测试，替换为真实 status API 合同测试；存在独立使用的 utility 时只保留真实使用部分，不为凑删除量扩大范围。

### 3. 改动形态与数值

```python
# status(): 保留 ingest_snapshot / sourceIngest 和其他状态组装。
# 删除整个 if ingest_snapshot: decision=...; sourceRecovery=...
# 以及 else 下的 idle sourceRecovery。
# 不再为构造已删除的块而调用 recovery_policy.decide()。
```

确认所有产品引用只剩这条接线后，删除无用的 RecoveryPolicy 成员、两处 import，以及确实只服务此块的模块／类。**这一步是变更闭合检查，不是重新审计整个恢复系统。** 不调整现有前端5秒下限、分片缩放、缓冲3/10秒或后端其他故障时间。没有新增兼容期限、定时器或重试。

需要用户给出的单个决定是：“是否有仓库外脚本／客户端依赖 `/api/status.sourceRecovery`，是否批准删除整块？”若存在，先取得具体访问字段与兼容要求，本设计暂停重出，不自作主张删除或新增永不使用的执行动作。

### 4. 测试清单

D：实际 status handler 对 running/idle/error 返回 JSON，均不再含 sourceRecovery；旧 handler 会含。D：让旧 recovery_policy.decide 的 spy 调用即失败，执行新 status 应不调用；旧 handler 对有 ingest 的请求会调用。G：同一输入的 sourceIngest、state/error、targetDelay、subtitles、mediaClock 除批准删除块外保持。G：源持续静默／错误时，无新的 ingest.start/restart 调用，用户 Stop/Start 流程保持。

删除只是在断言旧 `action` 字符串的单元测试不算证明安全，须保留实际 HTTP JSON 和“不发生重连”的副作用检查。

### 5. 验收命令与通过条件

Y、C。直接脚本启动与包导入路径都可用，无悬空 import；返回结构只发生批准的一项收缩，未触及消费者仍使用的 sourceIngest 及原因字段。

### 6. 回滚

`D1-SHA` 单独回退，恢复 sourceRecovery 旧诊断投影但**不会执行其 action**。不能因回滚而新增执行消费者。若外部脚本因此报错，先回此提交，其他性能修复保持。

### 7. 前置依赖

用户明确确认外部 API 兼容；当前唯一作者；server.py 与 A1/R1/S1 顺序修改。没有答复就跳到 R1 已批准的离线实现、L1 工具或其他已批准项，不让 D1 阻塞全部施工。

### 8. 风险与失败模式

外部客户端即使不在 GitHub 也可能读取这个块；用户手动诊断也可能依赖它的内容。删除后必须仍能通过 sourceIngest 和既有日志读出事实，但不另造等价虚假投影。若返回块实际上有新的产品消费者，该“整块删除”设计失效，而非强行说原零消费者结论永久成立。

## W9 / R1：受信源时钟的回绕展开；无法证明的映射明确停用

### 1. 目标与不变量

保持 `C=A0−V0` 的精确语义，修复可确认的33位回绕，而不是放宽偏移守卫。区分两件事：**两腿首次 PTS 分别在边界两侧**，影响起始偏移；**已有会话的后续 PTS 跨边界**，影响 sourcePtsLast−sourcePtsFirst 等进度。后者不会自动改变已经锁存的 A0/V0，不能把两者写成同一个触发机制。

不改媒体字节、FFmpeg 参数、两腿拓扑、ASR PCM 时钟、视频/字幕切句、已锁存原点；不通过重启下载腿修复。原 `MPEGTS_REBASE_ORIGIN=2.0` 和 `SOURCE_CLOCK_MAX_LEG_SKEW=600.0` 的防错目的必须保留。**不能把一次重置或不可信原点，取模后伪装成合法近距离。**

### 2. 落点

`companion/source_timeline.py:12–69 / PTS_HZ、MpegTsPtsProbe.__init__、_packet`；`companion/ytdlp_ingest.py:378–408 / start 中 pts_probe 创建`、`:433–450 / _record_pts`、`:512–565 / snapshot`；`companion/server.py:128–143 / 原点/偏斜常量`、`:1112–1170 / _source_clock_offset、_leg_origin`；`companion/media_anchor.py:65–99 / set_exact_offset、exact_offset`、`:157–201 / ready、offset`。测试新增 `test_source_clock_wrap.py`（第1行起），并扩展已有 source_timeline/media_anchor/server 对应测试。

### 3. 改动形态与数值

**推荐方案：在已有 PTS probe 内做整数展开，在已有精确偏移入口做受守卫的模差。** 不引入全局 epoch 管理器，不把生产媒体重写成新的时钟。

```python
PTS_MODULUS = 1 << 33                 # 8,589,934,592 ticks，协议位宽。
PTS_HZ = 90_000                       # 保持既有频率。
WRAP_EDGE_TICKS = 600 * PTS_HZ         # 复用既有600s守卫，不重新调参。
REORDER_TICKS = 100 * PTS_HZ           # 保持旧probe对小幅倒退的容忍窗口。

def signed_pts_delta(a_ticks, v_ticks):
    half = PTS_MODULUS // 2
    return (a_ticks - v_ticks + half) % PTS_MODULUS - half
```

回绕周期由常量直接相除：约 **95,443.717689秒 = 26.512144小时**。不是从“应用启动了多久”猜当前源是否到边界。

**Probe 的 O(1) 状态。** 保留原始首次 tick、上一个可信 raw tick、累计 wrap_ticks 和一个 clock_valid/invalid_reason；不新增每包无限历史。现有 `_record_pts` 的128项诊断上限保留。判断顺序：

```python
# 在已选中 media PID 的 PES PTS 路径内；输出仍是 float 秒。
raw = _decode_pts(payload[9:14])
if this_packet_has_discontinuity_indicator:
    invalidate_clock('transport-discontinuity')
    return None
if previous_raw is not None:
    if raw < previous_raw:
        is_forward_wrap = (
            previous_raw >= PTS_MODULUS - WRAP_EDGE_TICKS
            and raw <= WRAP_EDGE_TICKS
        )
        if is_forward_wrap:
            wrap_ticks += PTS_MODULUS
        elif previous_raw - raw <= REORDER_TICKS:
            return None               # 保留原小倒退处理，不倒推时钟。
        else:
            invalidate_clock('non-wrap-clock-reset')
            return None
    elif raw - previous_raw > PTS_MODULUS // 2:
        # 刚越界后收到边界前的迟到包，不得再加一轮或直接当新时钟。
        late_previous_epoch = (previous_raw <= WRAP_EDGE_TICKS
            and raw >= PTS_MODULUS - WRAP_EDGE_TICKS)
        backwards = previous_raw + PTS_MODULUS - raw
        if late_previous_epoch and 0 <= backwards <= REORDER_TICKS:
            return None                 # 沿既有小倒退规则忽略，不更新previous_raw。
        invalidate_clock('ambiguous-reverse-wrap-or-reset')
        return None
previous_raw = raw
return (raw + wrap_ticks) / PTS_HZ
```

adaptation field 的 discontinuity flag 在现有解析跳过 adaptation 字段前取出；仅针对所选媒体 PID 使用，不把别的 PID 的标记污染本时钟。标记 invalid 后，后续媒体PID的取数先检查 clock_valid 并返回None，在本会话内不能自行把后续几包恢复成“可信”；只能新的完整会话重新建立所有权。`_TcpPump` 可以提供其已有 probe 的只读状态，ingest 聚合为每腿的时钟有效性，server 在取偏移前检查。诊断增添 `sourceClockValid`／`sourceClockReason` 的小字段时，仅表达这件事实，不推送原始包或无限历史。

**首原点模差。** 保持缺值与2秒 rebase 守卫；对受信的两腿，将直接存储的首次 PTS 转成整数 tick（不要从六位小数的 status 回读），算 signed_pts_delta。绝对结果仍不得超过600秒。首次锁存的是原始原点对和经过验证的 offset；之后原始原点变化仍使映射无效，而不是重新锁存：

```python
if not both_leg_clocks_valid:
    return invalidate_exact_mapping('source-clock-invalid')
if audio_first is None or video_first is None:
    return None                        # 尚无测量，不伪造零。
if min(audio_first, video_first) < 2.0:
    return invalidate_exact_mapping('origin-rebase-or-wrap-ambiguous')
a = round(audio_first * 90_000)
v = round(video_first * 90_000)
offset_ticks = signed_pts_delta(a, v)
if abs(offset_ticks) > 600 * 90_000:
    return invalidate_exact_mapping('implausible-origin-skew')
# 原始(a_first,v_first)不变的锁存检查保留。
return offset_ticks / 90_000
```

**不能回避的歧义。** 合法回绕后最初的0–2秒，与已知的约1.4秒 rebase 伪原点，单看两条首次数值可能无法区分。也存在数值上恰好像回绕、却未携带 discontinuity 标记的上游重置。这里**不放开2秒守卫、不承诺仅凭模数解决全部时钟歧义**。需要更多证据时，索取两腿边界前后的实际 PES PTS／discontinuity 标记及同时段原点、会话身份；若首次就从边界后开始且没有可信前史，要另给可验证的源时间依据，不能造出来。

**本方案对歧义的具体产品选择：宁可不发布不可信对齐字幕，不无声退回已知可能偏差的采样映射。** 在 MediaAnchor 的 exact provider 接口旁增加一个只读 `sampled_fallback_allowed` 判断（默认 true，其他既有使用者不变）；精确 provider 先刷新可信性，再判断是否允许 sampled fallback。仅 R1 检出的时钟失效／歧义将它置 false，并通过既有诊断环在状态转换时记录一次。`ready=False`、`offset=None`，使用既有256条待决上限与10秒 grace处理未映射 finals；不积压无限字幕、不暂停视频、不重启腿。普通启动尚无 PTS 的旧采样回退行为不改；“未测到”与“已判不可信”必须分开。

这是必须经用户批准的**时钟失效行为**，不是悄悄改字幕展示政策。若用户要求所有0–2秒起始场景都必须自动精确显示，当前输入不足，本项不能以上述保守退化冒充那个要求已经满足，需要先提供额外源时间证据并重出该分支。

### 4. 测试清单

| 类别 | 输入 | 期望 | 改动前怎样失败 |
|---|---|---|---|
| D | A0=3s，V0=W−2s，W=2^33/90000 | offset=+5s，误差≤1 tick；不走 sampled fallback | 旧巨大差值守卫返回None |
| D | 上述两腿互换 | offset=−5s；同一锁存语义 | 旧返回None |
| D | 真正编码的TS/PES包，PTS依次 M−90000、M−45000、0、45000、90000 | 展开后连续递增；每步0.5s；首值不重置；extent连续 | 旧probe在边界接受小raw，extent跳负／进度失真 |
| D | 同一序列经过 _TcpPump/_record_pts 与 server/anchor 消费 | 原点稳定、last−first正确、映射连续；保留历史≤128 | 仅改 offset 的半方案不能让后续 extent 连续 |
| D | 明确 discontinuity、非回绕大重置、锁存后原点改变 | 时钟失效且有一次诊断；ready/offset不使用错误采样回退；不自动重启 | 旧错误路径仅None后可能静默回退 |
| D | 一个可疑首原点1.4s，另一个在边界附近；采样窗口已ready | 不靠取模接受；明确不可确认，不继续发布假精确位置 | 朴素modulo补丁会得到一个“很小所以正确”的假偏移 |
| G | A0=105、V0=100；小幅PTS倒退；普通missingPTS启动 | 同侧+5保持；原小倒退规则保持；普通尚无测量的行为不改 | 既有正常功能保持 |
| G | 刚过回绕后收到前一epoch、相距小于100秒的迟到包，再收到正常新包 | 忽略迟到包，不额外加M、不将正常时钟永久标坏；后续继续单调 | 防止对原本允许的小幅PTS重排引入新回归 |
| G | 多轮完整回绕的合成单调序列；限600s边界；一腿时钟无效 | 计数展开无累计整数误差；仍守偏斜界；不把单腿有效当双腿有效 | 不需要真实等26.5小时才测试 |

一 tick 是 **1/90000秒**，来自协议分辨率，不要求六位小数 JSON 文本逐字相等。回放使用真实 probe 和 anchor，不复制数学公式在测试里自证。对“信息不足”的歧义输入，正确结果是明示不可确认，而不是假装测试了一个必然可解的精确映射。

### 5. 验收命令与通过条件

Y、C；跨模块回放 D/G 全通过；正常同侧对齐与字幕已有守卫不回归。保存本次新增输入、期望映射、失效诊断和无重启证明。可确认回绕路径通过才允许说“该类回绕已修”；**全部全天对齐保证还取决于允许的源时钟合同与歧义处理批准**，不能由一次两小时长跑替代。

### 6. 回滚

`R1-SHA` 将 probe、ingest、server、anchor 的消费者一起回退，恢复旧拒绝巨大偏移和旧采样回退；已知回绕缺陷重新开放。不得只回 probe 留下把raw当展开值的消费者，也不得回退 copyts／已解决锚点提交。

### 7. 前置依赖

用户批准时钟展开与不可信映射停止发布的规则；A1/S1 的生命周期安全已关闭后做集成，但离线机制测试可提前。无需真实26.5小时运行、收费 provider 或修改注册表。新版本若已经有等价的可信时钟/epoch字段，直接复用，本方案里的新增字段删除，不重复建设。

### 8. 风险与失败模式

把普通逆序包误判回绕、把重置误判连续、两腿处于不同不相容源时钟、消费者一半用raw一半用展开值，都会生成看似精确的错误字幕。2秒歧义不能靠放宽守卫解决；600秒窗不是“时钟同源”的证明，只是原守卫。任何已知反例让合法／非法事件数值不可区分，应停止自动接受该类输入并追加来源证据，而不是扩大阈值。失效后必须有界丢弃／标记未映射，不能无限等待，也不能当全天保证已经通过。

## W10 / L1：独立的“越跑越卡”测量、判定与归属

### 1. 目标与不变量

回答 **GPU 修复之后，在同一会话、固定配置中，用户可感知表现是否随运行时长恶化**。先测是否还存在、再选择修复机制，这个顺序正确；不能先把“增长”归给B1/C1/C2，也不能把不同直播会话的失败密度差异当成同会话漂移。

归属：**L1 是与 R1 同级的独立工作包，当前唯一执行者负责采集、分析和退出验收**。它是声称 U3 已关闭／“本批所有性能抱怨已解决”的前置门，**不是阻止 A2/B2 等明确正确性补丁交付的门**。R1 负责离散时钟边界，L1 负责持续时间关联；两者互相引用，不互相替代。

不得重新启用 disable-gpu，不安排跨屏操作，不改变 MPO/注册表/显示适配器，不重启下载腿。观察期间固定代码、模型、语言、画质、目标延迟、窗口布局和声音状态；不强制 GC、不自动静音隐藏窗口、不用探测去扰动播放。

### 2. 落点

复用现有 `.scratch/laglingo-audit/subtitle-lead/lag-measure.py` 及 `.scratch/live-caption-onset-v1/browser-observer.js`。前者已按执行记录补过 M0；后者是可复用参考，**第68行的 video.muted=true 不带入**。这两个文件未在 Git 中：**当前函数名、其余行号与CLI未读取；执行者须先提供文件及一条新格式样本，再将下述字段映射填写到施工记录，不能凭本计划猜已有参数名。**

新增、纳入 Git 的离线分析器：`prototype/hls-companion/scripts/analyze-duration-degradation.py`（第1行起），符号 `normalize_record`、`build_windows`、`fit_duration_trend`、`classify_duration_effect`；新增 `prototype/hls-companion/tests/test_duration_degradation.py`（第1行起）。分析器不需要接触密钥或修改产品。

产品数据直接取现有 `/api/status`：`companion/subtitle_pipeline.py / status()` 的计数与阶段指标；`companion/ytdlp_ingest.py:512–565 / snapshot` 的字节、PTS和idle；`companion/core.py / LiveSession.status` 的运行时长与媒体进度。session ID 优先用 S1 的明确媒体会话身份，pdtEpoch/provider/language 继续作为交叉校验，不单独当万能会话键。

### 3. 改动形态、全部数值与理由

#### L1-a：先校准采样器，不先开两小时空跑

先取得当前 M0 文件，验证它不会覆盖旧 `lag-samples.jsonl`／`tree-samples.jsonl`，没有后台无限任务，hidden/rAF-null/rVFC-null 时仍能输出。工具可用与“已经采集过”分开：执行记录说 M0 尚未正式采集，当时应用idle且窗口hidden。

对运行中的批准实例做 **60秒轻量基线＋60秒开启完整采样** 的前置扰动检查，窗口位置、音量、画质不变。独立读进程CPU，不改变GPU设置。这两分钟是发现明显采样开销，不是有统计说服力的性能A/B。若完整采样使进程树CPU中位额外增加 **0.05核**以上，或帧间隔指标明显新增 **10%**以上的恶化，则先减小采样开销再做长跑；这两个值是保守的仪器停机门槛，不是产品SLA。

本地 status 请求和浏览器采集各设 **2秒**工具侧超时、各最多一个 in-flight；超时写 unknown/timeout 记录，不阻塞下一次计划采样，不重试扩增并发。2秒小于5秒采样节拍，防止诊断链自己无限等待；不修改产品request的20/30秒。

#### L1-b：固定条件的首轮运行

| 参数 | 值 | 理由 |
|---|---:|---|
| 正式会话观察时长 | **120分钟／7200秒** | 比现有12/24分钟长得多，能检出小时尺度累积；不假装覆盖全天 |
| 热身 | **前10分钟／600秒** | 记录但不用于稳态趋势拟合，避免启动缓冲／缓存填充主导斜率 |
| 基础采样 | **5秒** | 复用既有采样节拍；对约95秒周期每周期约19点；避免高频进程扫描 |
| 趋势窗口 | **5分钟／300秒**，不重叠 | 每窗约60点，包含约3个95秒周期，削弱取样相位影响 |
| 正式拟合范围 | **第10–120分钟** | 22个窗口，计划1320条5秒记录；7200秒全程约1440条 |
| 早／晚对照 | **10–20分钟 vs 110–120分钟** | 使用两端稳态窗口，不把最初启动阶段拿来比较 |
| 输出硬上限 | **256 MiB／一次实验** | 高于约千余条聚合记录的合理需求；超限停采并报告，不覆盖／轮转旧证据 |
| 仪器硬终止保险 | 正式开始后 **7260秒** | 7200秒观察加60秒退出余量；不让工具无限运行 |

同源不等于同输入：直播内容、字幕密度、聊天速率、源停顿仍可能变化。必须记录这些工作负载／源状态；报告先给**全部固定配置时间**的结果，再给源健康／可见状态分层结果，不能悄悄删掉最差时段后宣布没退化。

#### L1-c：字段与归一化

每条记录保留外部 `monotonic` 和 wall time，媒体会话身份、代码SHA、脱敏后的配置指纹、进程角色＋PID＋创建时间、页面可见性。机密值不写采样；指纹应来自本次固定的非机密配置，而不是将密钥当成需要流转的资料。

**观众表现：** rAF 间隔分布、rVFC 间隔分布及 metadata.mediaTime/presentedFrames/expectedDisplayTime、距最后回调的时长、video.currentTime、paused、readyState、前向缓冲、丢帧数／总帧数的区间增量、longTask。零回调窗口不能只写 p95=null 然后被过滤掉；它必须同时有 callbackCount=0 和 lastCallbackAge，作为空档事件记录。rVFC元数据描述合成相关呈现，不宣称屏幕实际扫描或DWM根因。

**资源：** renderer/GPU/backend 等角色CPU（100%=1核）、JS heap、DOM节点、各角色进程内存（能取 private bytes 就同时保存；仅有RSS不能称为“已测全部泄漏”）。内存看五分钟低位／最小值及趋势，不只看GC锯齿峰顶；不强制GC改变实验。

**字幕与翻译：** translationAttempts/Failures/DeadlineExpired/Dropped/ProviderFailures/sourceOnlyCues 的差值，backlog、workersAlive、PCM/ASR进度、queue/provider/成功ready lag指标及 latencyWindowSamples、latencySampleAgeSeconds。现有阶段统计是滚动窗口，**不能平均多个 p95 得到全程p95**。分析器将它标为“滚动p95读数的五分钟中位值”，而非原始 cue 的p95。缺原始逐cue时延就明确缺失。

**下载与源：** 每腿 forwardedBytes、sourcePtsFirst/Last、sourceIdleSeconds、privateMediaSeconds和源错误，按自己相邻样本算 `ΔforwardedBytes/Δmonotonic`。不直接拿 snapshot 里的 bytesPerSecond 作长跑回归，因为其他读者也会更新其计算基线。idle也可能受下游反压影响，不能单凭它把锅给网络。

**计数率：** 同一会话、同一配置的区间 `Δfailures/Δattempts` 单独给出，分母为零不算。队列到期和dropped并非与失败互斥的所有人群，分别报告，不能相加构造“总失败率”。停止／切换会话的尾部在途任务单列；五秒失败可对应前一窗attempt，所以趋势率优先用较长的五／十分钟窗，并标明不是精确的逐cue队列归属。

#### L1-d：数据质量门

正式拟合范围至少有 **95%有效基础记录，即1320条计划中至少1254条**；每个参与趋势窗口至少 **54/60条有效记录（90%）**；22个窗口中至少20个合格，且早、晚两个十分钟范围各有两个合格窗口。两个规则要同时满足。达不到即 **证据不足**，不是零增长。

会话身份、代码或配置改变就拆段，不能拼成120分钟；计数器回零、PID重用、sleep／采样时钟跳变也拆段。R1的明确clock-invalid/discontinuity事件或源PTS跨边界后未能连续映射，标为离散时钟事件并划分前后分析段，不以一条全段斜率宣称平滑泄漏；原始整段和该事件仍完整报告，不能静默删去。显示相关结论按 hidden／可确认可见／遮挡未知分层：不把blur当hidden，不把遮挡unknown当无遮挡。没有足够同状态窗口，就不能给该层“未退化”的结论。

阶段时延辅助结论要求该读数的 `latencyWindowSamples≥30` 且 `latencySampleAgeSeconds≤30`；这是防止拿空样本或陈旧读数拟合，不把重复采到同一个滚动窗口当新独立cue。失败比例的早／晚窗各至少100次attempt才报告比例差的解释；不足时保留计数和宽区间，不能靠它判定没有翻译退化。观众丢帧比例的早／晚窗各至少3000个实际总帧；不足不把比例小写成已证明稳定。

#### L1-e：回归对象与决策规则

对22个五分钟窗口的指标 `y_w`，以**会话单调运行分钟数**为x，拟合 Theil–Sen 斜率（两点斜率的中位数），避免一两个极端窗支配普通直线。以残差连续块做重采样：每块 **3个窗口=15分钟**，**1000次**，固定随机种子 **20260917**，给95%区间。这是本次分析的工程不确定度估计，不是对复杂直播非平稳过程的无条件统计保证。

```python
blocks = build_windows(records, seconds=300, warmup_seconds=600)
beta = median_pairwise_slopes(blocks.time_minutes, blocks.metric)
alpha = median(blocks.metric - beta * blocks.time_minutes)
residual = blocks.metric - (alpha + beta * blocks.time_minutes)
# 重采样连续残差块，重建 alpha+beta*x+residual* 后重拟合。
# 不随机打散原始5秒样本，也不把块拼接当新的真实时间顺序。
ci_low, ci_high = residual_block_bootstrap(
    blocks, block_count=3, repeats=1000, seed=20260917,
)
```

为避免把“未显著”写成“没有”，每项用三种状态：

- **检出实质增长候选：** 晚窗−早窗达到下表门槛；斜率×110分钟也达到门槛；斜率区间下界>0。它证明时间相关的候选，不直接证明原因。
- **在本窗口未检出达到门槛的增长：** 数据质量合格；早晚差与斜率上界×110分钟均低于门槛；没有被聚合掩盖的恶化空档。允许报告有限的负结果。
- **证据不足／非单调异常：** 其余情形，包括区间跨零但上界很宽、晚窗样本少、只有离散停顿。不能强行二分。

以下全是**预先登记的工程筛选阈值**，不是仓库测得的故障定义；用户另有容忍值应在采集前改，而非看完结果后改门槛：

| 指标 | 实质增长门槛 | 用途／理由 |
|---|---|---|
| 可见层 rVFC间隔p95读数 | 早期值的20%与**一个基线视频帧间隔**取较大者 | 适配不同帧率；一个帧周期是可解释的变化，不写死60fps |
| rAF间隔p95读数 | 早期值的20%与**8ms**取较大者 | 避免微小计时噪声；只代表页面回调表现 |
| 丢帧／总帧区间比例 | **增加1个百分点** | 使用比例而非累计丢帧随时间必然增长 |
| JS heap低位或进程内存低位 | 早期值20%与**32MiB**取较大者 | 筛出可观资源累积；不是泄漏证明，也不保证更小泄漏不存在 |
| DOM节点低位 | 早期值10%与**200个节点**取较大者 | 抑制小范围UI波动；需再定位实际保留对象 |
| 单角色／进程树CPU | **增加0.25核** | 可解释的持续负载变化；不是整机CPU百分比 |
| 翻译backlog | **增加3个cue**，并持续到最后两个五分钟窗 | 区分瞬时源补齐突发与持续积压 |
| queue/provider/成功ready lag读数 | 早期值25%与**0.5秒**取较大者 | 对6秒级预算是有意义的变化，但必须有新鲜样本 |

单独报告每分钟 **>500ms回调／呈现空档**及 **>5000ms长空档**、最长空档和buffer耗尽事件。一次5秒空档值得保留并检查，但不是“随时间增长”的自动证据；空档不能因为没有回调而被p95筛掉。

**95秒周期单列。** 用五秒CPU序列拟合固定95秒的sin/cos项与线性时间项，对照早晚振幅、均值以及去周期后的时间斜率；95秒来自已知观察，不在几十个频率中挑一个最显著的来宣称发现。CPU周期稳定而用户指标无实质增长，支持“周期但不累积”的描述；若去周期后仍有斜率，则纯周期解释不够。CPU与帧间隔的低相关不构成任何系统原因的严格排除。

“前十分钟 vs 后十分钟”因此只是可读对照，不是唯一判据；全段窗口趋势、事件和条件分层必须一起交付。

#### L1-f：假设空间、否证条件与不能越界的结论

| 假设 | 支持它所需的形状 | 什么证据使它不足／被否证（限定本次运行） |
|---|---|---|
| (a) U3 已完全随 GPU 修复消失 | 修复后同配置长跑中，主要用户指标满足上述有限负结果，资源不显著累积 | 修复后仍出现可重复、条件相同的实质时间增长，就否证“GPU修复解释全部”。一次通过只支持本两小时／本配置，不证明所有机器和全天 |
| (b) 真实对象／资源泄漏 | heap/DOM/私有内存低位持续增长，并有对应持有对象或集合的额外证据 | 已测对象低位有界、主要用户指标仍恶化，使这些对象的泄漏解释不足；不能用JSheap稳定否证未测的GPU／native泄漏 |
| (c) 约95秒周期而不增长 | 振幅和均值稳定、去周期后的斜率接近零，观众指标没有累积恶化 | 均值/振幅随时间增加，或去周期后仍有实质用户指标增长，否证“只是稳定周期” |
| (d) 字幕／翻译队列累积 | backlog及queue delay持续升、deadline/drop增，和症状时间对齐 | 队列长度与延迟稳定、有新鲜样本而UI持续恶化，限制队列累积解释；静态p95陈旧不能算稳定证据 |
| (e) PTS回绕或重置 | 事件发生时raw PTS跨边界／discontinuity，offset或extent离散改变 | 症状时PTS远离边界、源时钟映射连续，否证该次事件的回绕解释；不能因应用才运行半小时就排除源接近回绕 |
| (f) 下载腿随时间劣化 | 在类似画质负载下，字节／源PTS推进落后，idle和缓冲问题累积，先于下游症状 | 源交付与PTS进度持续正常而下游仍增长，使下载腿解释不足。下游反压也会影响源idle，只有时间相关不能区分网络与下游责任 |

首轮若检出增长，第二轮只重复同样固定条件确认，不同时改配置来“解决”。第二轮仍只说明可重复相关；具体内存保留、provider阶段或下载段有对应证据，才升级相关修复包。没有握手成本证据，C2仍不升级。

#### L1-g：安全终止和输出

独立 watchdog 必须先 dry-run 核对 **PID＋创建时间＋角色＋所有权**；不能按进程名杀。`--attach` 到用户自启实例不自动赋予结束它的权限：默认只停止本次采样器；需要在无响应时关闭应用，必须在运行前明确批准那几个PID/进程树。用户其他Python服务器不动。

每5秒采样的 driver 应写独立心跳。心跳连续 **15秒**未更新（3个节拍）标记仪器异常；若到 **30秒**仍未恢复，按已批准所有权清单终止**实验**，记录未完成。不能为凑够两小时在严重卡死后继续扩大触发条件。watchdog自身不依赖页面事件循环；退出时关闭本次CDP连接、HTTP临时资源和文件，列出应退出/应保留的PID及实际结果。

交付物：时间戳命名的原始JSONL、配置/代码/会话清单、仪器完整性记录、每指标早晚值/斜率/区间/门槛/结论、95秒周期结果、空档事件列表、六种假设的证据状态、退出清理结果。旧9月16日唯一原始文件绝不覆盖。

### 4. 测试清单

| 类别 | 输入 | 期望 | 改动前怎样失败 |
|---|---|---|---|
| D | 7200秒的合成记录，固定均值＋95秒正弦CPU，帧表现稳定 | 判周期存在，不判随时长实质增长 | 旧流程没有持续时间分析器；只比峰值或端点可能误报 |
| D | 同样周期叠加足够大的线性资源／帧退化，后段超过预注册阈值 | 斜率、早晚和区间共同检出增长候选 | 旧流程没有相应判据，无法形成可执行结论 |
| D | 中间一次PTS边界阶跃，其余稳定 | 报离散事件，归R1候选，不伪装平滑泄漏结论 | 单一全程回归可能把阶跃当渐增 |
| D | hidden=true、rVFC/rAF无回调、p95=null；另有可见5秒无回调 | 不崩溃；hidden分层；可见空档仍报告，不将null填0 | 原分析缺失；忽略null会把最严重窗口“分析没了” |
| D | source/config/session改变、计数回零、PID重用 | 拆段，不能凑成一次120分钟通过 | 没有分段的端点比较会混淆群体与时间 |
| D | 只12/24分钟记录、覆盖不足、早晚分母不足或陈旧阶段p95 | 证据不足，不输出“没退化” | 原已有短时数据不能满足本合同 |
| D | 输出达到256MiB／超出7260秒；采样请求不返回 | 实验有界退出、unknown样本可追溯、无残留；旧证据保留 | 无此边界的采样器会越界／挂住；以收到文件后的实际旧实现作红绿对照 |
| G | 已修M0唯一文件名、gapP50=null打印、正常attach | 保持修复，不覆盖旧样本，不复制静音行为 | 执行记录说这些已修；不得谎称都应在当前M0上红 |
| G | 同一数据与固定seed重复分析；全部指标稳定且窄区间 | 结果可重现；只给两小时范围内的有限负结论 | 不利用随机重跑挑选有利结果 |

分析器测试是即时生成约1440条合成记录，不实际跑两小时。对仪器源文件未读到的部分，只能先规定行为合同；取得文件后再填写哪个旧函数会红，不能编造一次已经运行的对照。

### 5. 验收命令与通过条件

离线分析器通过 Y/C 自动发现的 `test_duration_degradation.py`，并支持以下**新增命令接口**（不是声称现有工具已有这些flags）：

```powershell
py -3.10 prototype/hls-companion/scripts/analyze-duration-degradation.py `
  --input <本次唯一JSONL路径> --manifest <本次实验清单JSON> `
  --output <本次分析JSON路径>
```

采样器CLI先以实际 `--help` 确认，再把120分钟、5秒、输出前缀、attach所有权写成最终命令，记录在执行单；本计划不猜其参数名。离线全绿仅证明工具；真实L1通过还需完整数据质量、预注册判据和退出证明。

允许的结论示例：“在H之后指定修复提交、该源/画质/配置的两小时会话中，未检出超过预注册门槛的随时长增长；不覆盖全天、未知遮挡层和未测资源。”不能写“证明没有泄漏／越跑越卡彻底根治”。若检出增长，L1作为测量已完成，但U3修复验收未通过。

### 6. 回滚

采样接线和分析器分别保存 `L1-instrument-SHA/快照`、`L1-analysis-SHA`，因为前者被Git忽略时不能假造提交。禁用采样即回到原产品；回退分析器不会删除任何原始证据。退出不得停止未授权的用户播放器；也不为使结果变好而回退已定案GPU修复。

### 7. 前置依赖

当前M0脚本、一条新格式样本、其CLI和watchdog/dry-run记录；S1/A1等运行安全项达到可测状态；用户批准两小时占用与可能的ASR/翻译费用，以及允许终止的实例范围。先完成工具校准与假数据测试，再申请／执行真实运行。用户暂不愿跑时继续其他已批准小修，L1维持“工具就绪、真实未验收”。

### 8. 风险与失败模式

采样本身消耗CPU、窗口隐藏引起回调停顿、直播负载变化、滚动p95被当原始分布、累计计数天然增长、实例切换没有拆段，都可能制造假退化或假稳定。报告必须包含覆盖/分层/新鲜度，不只展示一条拟合线。统计区间不适合该非平稳数据时应标记不可解释，不能借更多小数制造确定性。

**用户不跑长测时的边界：** 可以交付经过单测的A1/B2/E1等补丁；不能宣称U3已验证消失、全天稳定已保证、95秒CPU周期已归因、固定GPU代价已经解释所有时间相关症状，也不能借“我们没测到”把U3再次从工作表删掉。

---

# 5.3 顺序、阻塞后的接续动作与并行边界

**默认施工顺序：D0 → A2-T → A1 → B2-R → S1 → E1-R → A3 → R1 → C3 → D1。** L1的资料接收、字段映射和离线分析器可从D0之后开始；真实L1运行放在A1/B2-R/S1通过、仪器已校准且代码冻结之后，不必等D1的外部API答复。

这里保留工作单“A1下一个资源修复”的方向：A2的产品死等已经修好；先补可靠的回归工具，随后关闭已确认的stderr阻塞和它必需的生命周期。B2-R与S1必须在真实长跑前完成，避免测量期间仍有可避免的预算／启停状态歧义。E1是明确的小接口修正，可以在某项等待批准时提前，不再因“文件空闲”获得设计豁免。

| 项 | 默认顺序 | 前置条件／阻塞者 | 能否并行 | 被阻塞时具体做什么 |
|---|---:|---|---|---|
| D0覆盖与证据措辞 | 0 | 当前文档基线 | 可与只读资料整理并行 | 无产品阻塞；先完成三项顶层登记 |
| A2-T | 1 | 3cc2093；测试 harness | 同一作者，不与其他测试文件写入冲突；产品不改 | 先写独立行为fixture；无需等原日志或收费API |
| A1 | 2 | 用户批准尾部与生命周期；subtitle_pipeline由唯一作者持有 | 不与B2/S1/R1集成测试或产品写入同时进行 | 做B2-R已批准部分；否则E1-R，再C3；L1离线工具继续，不跑真实长测 |
| B2-R | 3 | 批准本地资格与逐次期限合同；V/P测试 | 不能另开第二作者；无收费依赖 | 做S1已批准部分；否则E1-R/A3，保留B2边界未关闭状态 |
| S1 | 4 | 用户批准媒体会话身份／停止屏障；A2-T | 与E1/A3/C3串行修改player；与R1串行改core/server接口 | 做E1-R、已取得patch的A3；不把“等原Stop作者”列为依赖 |
| E1-R | 5 | 当前单作者；调用者同提交迁移 | 可以替代任何等批准的工作，但不同时写player | 本身无旧占用阻塞；若发现新外部调用合同则转A3/C3 |
| A3 | 6 | 收到原2590字节patch、确认内容 | 与其他player改动串行 | 不等无期限：转R1离线回放、C3或L1分析器 |
| R1 | 7 | 用户批准展开与歧义失效行为；时钟合同 | 离线fixture可提前；产品与server/anchor相关修改串行 | 转C3、已批准D1或L1工具；全天时钟验收继续开放 |
| C3 | 8 | 用户接受删除虚假入口、不迁移旧数据 | 与player相关修改串行 | 转D1已批准范围或L1工具 |
| D1 | 9 | 用户确认外部sourceRecovery依赖及删除批准 | 不阻塞已完成小修或L1 | 暂停D1，交付其他合格提交，不新增自动重连或假兼容层 |
| L1工具／仪器校准 | D0之后 | 当前M0源文件、样本、CLI、watchdog | 离线分析可独立；仍不得两个作者同时写同一工作树 | 缺仪器文件时先完成纳入Git的假数据分析器和其他代码修复；字段接线不得伪造 |
| L1真实120分钟 | 安全项完成、稳定构建之后 | 用户授权时间/费用/PID；完整采样；冻结版本 | **不**与构建切换、压测、其他性能实验并行；不热改配置 | 用户不跑则明确U3未验收，先交付单测通过的代码；不重做GPU A/B凑结论 |

唯一产品作者保持不变。“可并行”指可以在不修改同一运行环境的前提下整理只读资料、写隔离分析；不是允许另一个模型在工作树同时写入。真实长跑期间不同时改构建，即使改的是所谓“空闲文件”。

## 实施前需要批准的设计清单

这不是要求用户逐个回复才能得到本文件；它是执行者开始改动前的许可登记：A1的读者和局部收尾、B2-R本地资格语义、S1的媒体会话身份、C3删除入口、D1整块API删除、R1不可信时钟的失效行为、L1的运行费用和允许退出的实例。批准一项就执行该项；未批准项按上表跳过，不能默认“规划模型写了就等于用户已同意”。

---

# 5.4 明确不做、不回答的部分，以及需要的最小材料

## 本轮不做／不作结论

| 不做／不回答 | 为什么可接受 | 因此不能宣称什么 |
|---|---|---|
| 不重审原九条断言、不重新扫描88个选项 | 已有审计及复核是输入；本轮只检查新提交与施工接口 | 不声称本次给全仓正确性认证 |
| 不重新统计四份原始日志，不从缺失工作区猜文件不存在 | 接受执行记录/M1的已知数字；数值不可复算与其不成立是两回事 | 不声称本次独立复算49/26/9，也不把日志计数当全失败人口 |
| 不把M1当成排队／网关／网络握手的完整归因 | 现有日志缺静默到期群体与逐阶段分母；关键词和异常名前缀不能承担全部因果 | 不直接改6秒、并发、上下文、maxTokens或主网关 |
| 不实现C2持久HTTP session、B1增量token或四个休眠C1适配器回收 | 当前没有对应收益／可达性证据，改变生命周期或协议语义代价更高 | 不声称这些潜在缺陷已消失；出现相关L1证据时另升优先级 |
| 不做跨屏触发、MPO/注册表/显示适配器实验 | 风险覆盖整台机器，且不是本批小修的依赖 | 不结案U2，不把rAF/rVFC解释成物理显示或DWM根因 |
| 不重开disable-gpu作长跑对照，不用GPUFeatureStatus作健康判据 | 已定案的正常路径不可回退；L1要查修复后是否另有增长 | 不以十分钟GPU实验推导U3已解决 |
| 不按进程名清理，不替用户决定可杀哪些后台进程 | 必须按已授权实例与PID/创建时间匹配 | 不把“登记过PID”等同“获准结束用户进程” |
| 不重启下载腿，不接RecoveryPolicy.action | 签名清单和原点重置风险已知；本轮不改恢复单位 | 不把清除卡顿表象等同修复根因 |
| 不认证未读取的A3 patch和M0脚本内容，不猜其函数/CLI/行号 | 它们在忽略文件内，仓库阅读不能取得；下表明确要原件 | 不声称补丁已经适配当前H或M0仪器已通过真实性检查 |
| 不替用户回答sourceRecovery的仓库外消费者 | 外部依赖无法从仓库“搜不到”推为零 | D1没有批准前不删API |
| 不用纯模差认定所有0–2秒原点／无标记重置都是真回绕 | 输入存在歧义，2秒防错守卫不可凭想象移除 | R1不能无条件承诺每种源全天自动精确对齐 |
| 不先跑真实26.5小时来确认已知数学边界 | 离线TS/PES与真实probe即可验证算法；真实全天是后续运行合同 | 两小时L1或离线R1不等于全天运行试验已做 |
| 不在用户拒绝长测时补写“长期稳定”结论 | 正确性小修可以独立交付，U3有自己的未验收状态 | 不宣称越跑越卡已彻底消失 |

## 哪一步需要用户提供什么

以下不是要求一次上传整个工作区。只在对应步骤用最小材料解除阻塞：

| 步骤 | 需要的材料 | 不需要的东西 |
|---|---|---|
| A3适配 | `.scratch/laglingo-audit/fix-plan/pending-diagnostic-gating.patch` 原件，以及它最后一次验证对应的源码SHA | 不必提供所有scratch日志或整份工作区 |
| M0/L1接线 | 当前 `.scratch/laglingo-audit/subtitle-lead/lag-measure.py`；若它引用外部浏览器注入文件，则连同该文件（包括实际在用的browser-observer版本）；**一条新格式样本**；实际 `--help` 输出 | 不需要先跑两小时才能交一条样本 |
| L1安全 | 实际watchdog/kill-switch脚本、dry-run结果、PID＋创建时间＋角色＋所有权清单；脚本名未在已读材料给出，不编造 | 不需要机器级注册表或显示设置变更 |
| L1结果 | 本次唯一JSONL、清单、仪器完整性与退出记录；确切文件名由本次时间戳产生 | 不用新分母补旧会话的75条日志 |
| 只有M1旧数字发生争议／要做更细归因时 | 四份 `.scratch/dev-logs/dev-20260916-123749.log`、`dev-20260916-162253.log`、`dev-20260916-170423.log`、`dev-20260917-013209.log`；对应会话脱敏配置快照 | 当前修预算合同不需要密钥、真实网关访问或重跑收费API |
| D1 | 用户确认有无外部 `/api/status.sourceRecovery` 消费者；有则给实际字段访问片段／预期合同 | 不要求为回答它重新搜整个仓库 |
| R1歧义输入 | 两腿同一会话的首/末PTS、边界前后PES与discontinuity标记、会话/阶段时间；必要时短TS片段及生成参数；缺可信前史则明确缺失 | 不需要签名URL、Cookie、API key，不需要重启腿制造样本 |

所有配置或媒体诊断先遮掉API key、token、Cookie和签名URL；不要上传仍可使用的完整认证清单。没有这些材料时，本计划把“可以按代码施工的部分”与“仪器接线／外部合同尚未核对的部分”分开，而不是假装所有细节都看过。

---

# 5.5 失效条件与停止规则

## 需要重新发布整份顺序／范围的情况

1. **代码或所有权基线变化。** H之后出现新的媒体身份、清理所有者、期限合同或其他作者重新写入，导致本计划的前置条件不再成立；先绑定新SHA和差异，不盲叠补丁。
2. **用户运行目标变化。** 全天自动精确对齐变为立即发布条件，R1升到安全小修之后；用户明确允许另一种短预算质量取舍，B2策略重新决策；不能仍按低风险清理队列优先。
3. **L1得到可重复的反向优先级证据。** 在固定版本/配置下检出具体的队列累积、特定适配器保留或连接建立成本，足以改变当前投入顺序。只有“CPU高”或“越久越卡”的笼统相关，不足以直接授权C2或B1重构。
4. **顶层需求再度丢失。** 后续交付表只列技术编号而无U1/U2/U3的状态/证据/负责人，文档验收不通过，不能据它宣布整批收工。
5. **安全控制失效。** 没有可还原点、唯一作者不成立、实验退出不能按已授权实例执行，产品写入／真实实验停止；这不是让其他只读整理也永远停住。

## 需要撤回相应候选或重出具体机制的情况

| 证据 | 动作 |
|---|---|
| A2-T发现产品补丁仍在Electron转发路径上正文无界、abort留残留或poller不继续 | 不能以Node四项绿宣称验收；保留可证明正确的边界，重出该环境的最小修正，不回到原无界实现 |
| B2-R缩短了无可用兜底时的主期限、将主局部D−2错当总D、或给未调用端计入health | 拒收B2-R；先回叠加提交。若同配置实跑证明跳主损害用户认可的按时译文/质量，再单独重新审批ce1e47e策略 |
| E1出现不能同步迁移的真实外部数值调用者 | 暂停直接删除参数的接口方案，先确定兼容合同；不能静默同时支持两个真值来源 |
| S1无法稳定区分新旧会话、迟到回调仍覆盖新Stop、或服务端并发操作会拆掉新会话 | S1不得发布；前者修身份/代数，后者明确重出后端最小串行化范围，不能称前端修完即全局安全 |
| A1仍阻塞PCM、丢tail final、进程/任务未回收，或靠扩大五秒/吞异常才“通过” | 撤回A1候选；不能降低退出检查或清空引用。必要的生命周期事实改变时重出设计 |
| A3改变了pause/play调用序列，或C3保存时丢旧catalog隐藏键 | 回退各自小提交；不能用“只是UI/配置”豁免行为回归 |
| D1出现外部客户端依赖该块 | 回退D1并重新确定API兼容；不回其他修复，不执行action来补偿 |
| R1把重置当回绕、正常同侧误差超过1tick、raw/展开消费者混用，或用户不接受歧义时停用字幕映射 | R1机制或其失效策略重出；不放宽2/600秒守卫掩盖，也不宣称全场景已覆盖 |
| L1覆盖、可见性、身份、配置或采样开销门失败；原始时延被错误地平均p95；拟合被少数异常支配 | 该次运行结论作废，保留原始数据并说明不足；不得填0、删坏窗或调整门槛凑“没退化” |

所有回退默认使用独立提交 `git revert --no-edit <本项实际SHA>`；本计划里的 `A1-SHA` 等是**执行后应登记的占位名，不是假造现有提交**。相互依赖的消费者必须一并回退或先反向回退后续依赖，不能机械回一个文件留下不相容接口。

**不构成作废证据的事情：** 一次没复现、单次时延变好、测试发现数变化、无法取得gitignored原件本身。它们不自动推翻原审计，也不自动证明本计划正确。

---

## 依据索引（全部仓库引用固定于H；本地原件未独立复算）

- 主任务与设计来源：`docs/notes/performance-remediation-execution-record-2026-09-17.md`，特别是§3、§3.1；`docs/notes/performance-remediation-guidance-request-2026-09-17.md`，特别是§2、§3.7、§5。
- 已提交实现：`33f3dce41a2dc2faf1c9195140f69fd10358efa3`、`ce1e47e3df802b066c63a5c734f5413b9cf4a560`、`3cc2093f7cc72cc5a0ae22cdeba48ee839b33c6f`的实际diff；Stop交接基线`c3cdb00`。
- 阶段归因：`docs/notes/performance-m1-translation-stage-attribution-2026-09-17.md`；与其对应的`subtitle_pipeline.py / _translation_worker、_mark_translation_failed`。
- 前端运行合同：`player.js / request、start、stop、refreshStatus、updatePlaybackRecovery、updateStallOverlay、asrOptionFields`；`poll-loop.js / createSerialPoller`；`playback-recovery.js / decidePlaybackRecovery`。
- 生命周期与诊断：`subtitle_pipeline.py / start、stop、_pcm_reader`；`logbook.py / Logbook、record、_one_line`；`core.py / LiveSession`；`server.py / CompanionApplication.status、_source_clock_offset`。
- 时钟与吞吐：`source_timeline.py / MpegTsPtsProbe`；`ytdlp_ingest.py / _TcpPump、_record_pts、snapshot`；`media_anchor.py / MediaAnchor`。
- 翻译资格：`providers/fallback.py / FallbackChain`；`providers/base.py / validate_translation_pair`；配置清理：`providers/config.py / BUILTIN_ASR_PROVIDERS`。
- 实际测试入口：`package.json`，`scripts/run-hls-tests.py`，`scripts/run-hls-js-tests.js`。
- 通用运行语义参考：WHATWG HTML Standard 的 Timers；Python 3.10 官方 `asyncio` Coroutines and Tasks（wait/wait_for取消语义）；WICG `HTMLVideoElement.requestVideoFrameCallback()` specification（合成相关元数据语义）。这些参考不证明本仓库已经通过任何实测。

**交付状态：本文件完成施工设计；产品代码、测试和真实长跑均未由本次回答执行。**
