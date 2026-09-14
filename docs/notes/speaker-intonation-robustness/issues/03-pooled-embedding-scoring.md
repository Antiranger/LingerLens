# 03 — 用 Pooled Embedding 和可标定 Scorer 稳定跨语调分数

**What to build:** 在当前模型外增加可关闭的有效语音预处理、多窗聚合和确定性的 profile scorer，并通过固定 benchmark 选择默认策略，而不是继续拍脑袋调整单一 cosine 阈值。

**Blocked by:** 01 — 建立 CREMA-D Mini Ground-Truth Benchmark

**Status:** complete — verified 2026-08-31 (real calibration/evaluation pending CREMA-D audio)

- [x] embedder 上层产生包含 normalized vector、有效时长、speech ratio、window count 和 quality flags 的 observation。
- [x] baseline 模式保持整句单窗；候选模式按 spec 实现首尾能量裁剪、最大 +12 dB gain、2.5s/1.25s 窗和 normalized-mean，medoid 可用于对照。
- [x] 有效语音少于 1 秒或没有可用窗口时返回 insufficient，不调用 speaker gallery。
- [x] raw profile scorer 严格采用 spec 的 center、best 和 second-best 组合公式。
- [x] benchmark `pooled-raw` 模式使用 calibration pair EER 固定阈值并将参数/选择写入报告；真实 evaluation gate 尚未运行，因此生产默认保持 `baseline`，不声称效果已验证。
- [x] 未加入固定中心化/AS-Norm；raw scorer 是唯一启用路径。
- [x] 不实现在线滑动均值，pooled benchmark 的阈值只来自 calibration split。

**Verification:** speaker attribution 29 passed; subtitle pipeline 25 passed; benchmark 5 passed; synthetic benchmark smoke passed; implementation/config/benchmark LSP diagnostics clean. CREMA-D 音频缺失，故 same-p5/false-split 改善及 false-merge 发布容差未做真实验证。
