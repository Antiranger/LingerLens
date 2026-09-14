# 字幕延迟实验 v2

日期：2026-09-05。用户授权：设计并运行新实验。范围为测量可靠性、已复现的 deadline 起点回退、实际浏览器字幕显示；不更换活动模型或扩大字幕产品功能。

## 顺序与控制变量

1. 为 ring 淘汰、翻译延后跨 ring、时间逆序、空 delta 回退各写回归测试，先红后绿。原始文件备份至 backup，保留 before-sha256.json。
2. 独立 Companion 实例、独立媒体目录、真实 Chromium 无头浏览器，避免打断用户其他播放器。复用活动 Provider，不回显私密配置。Twitch ironmouse、720p60、目标播放延迟 15 秒、en→zh-Hans、聊天关闭。源不可用则明确记录，再选择正在直播的英文频道作补充样本，不能标为同源 A/B。
3. 从 start 成功后采样 600 秒；每秒状态、逐 observation 的数字摘要、逐 Cue 服务端时间和浏览器 firstSeen/首次可见 DOM/可见区间。到时停止本实验的 ingest/ASR/翻译；停止后的结果不混入主窗口。

## 记录

- 服务端：generation/item/chunkOrder/cueId、映射音频起止、T0–T5、source ready、状态、Unknown 原因、PCM produced/sent/server frontier、ring 范围、稳定词数量与结束位置、实际 commit 调用及 monotonic 时间。T1 本轮仍明确称为首次 normalized observation 覆盖，不宣称是稳定词齐备；逐 observation 另外记录稳定词推进以检验差异。
- 浏览器：同一 Cue 的首次 source/terminal 收到时刻、对应 playbackWallTime、首次 DOM 可见时刻及 requestAnimationFrame 确认、可见持续时间、暂停/seeking/hidden/readyState/视频解码帧数。使用生产播放器、调度器、轮询和渲染路径，通过实验脚本注入观察器，不修改产品前端。
- 元信息：启动时间、模型 ID、worker/timeout、相关代码 SHA256、实际选择画质、浏览器版本。不得持久化字幕/翻译正文、raw payload、Cookie、密钥、签名 URL。独立实验文件使用白名单数字字段。

## 判定

- 测量：完整覆盖≥95%；活跃期间完整样本持续更新；逐 Cue 分段和与总延迟误差<100ms；缺失按原因计数，不能以零代替无效时间。若未通过，优先从 trace 定位，不能给全场性能排名。
- 体验：以 firstSeen terminal 的 `tStart-playbackWallTime` 衡量句首余量；首次可见 DOM 的播放位置减 tStart 衡量迟到；报告错过句首>0.5 秒的比例、超过窗口被丢弃的比例、从未可见的数量和可见时长。启动时已过期、未到达显示窗口、暂停或 seek 的 Cue 分开列出。
- 对齐：报告 abs drift 分布和最新样本年龄；计数器 residual 不替代音频语义同步验证。没有带已知语音标记的媒体，不宣称绝对语义同步通过。
- 分块因果：使用相同确定性输入验证修复，不将两次不同直播的性能差异归因于该修复。
- 运行：正常完成 600 秒；若源停止、ASR 未启动或播放持续不可用，提前终止并记录阻塞。所有本实验资源 finally 清理。

## 验证与限制

新增四个测试已先失败后通过。现有 Pipeline 57 项、Chunker 15 项通过。进一步通过真实联合采样检验；本轮不是字幕内容质量/WER 测试，最终结论必须保留该限制。无头浏览器的 DOM/rAF 可见只证明渲染路径，不等同于用户实际注视或屏幕像素的语义验收。
