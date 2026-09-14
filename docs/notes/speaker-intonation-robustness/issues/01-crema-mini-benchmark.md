# 01 — 建立 CREMA-D Mini Ground-Truth Benchmark

**What to build:** 提供一条快速、可重复的离线命令，从固定 CREMA-D mini manifests 运行冻结的 legacy speaker attribution，得到后续所有改动共享的可信基线。

**Blocked by:** None — can start immediately

**Status:** complete — verified 2026-08-31 (real CREMA-D evaluation not run: audio absent)

- [x] 固定选择 20 位 actor、每位 12 条音频；calibration/evaluation 各 10 位且不重叠，缺失样本时明确失败。
- [x] 提交确定性的 dataset、pair 和 session manifests；运行时不随机换样本。
- [x] 仓库只提交脚本、manifests、许可证说明、报告 schema 和无音频示例，不提交第三方音频。
- [x] benchmark 支持固定 120 positive + 120 negative pairs/split，以及 10 个 24-utterance sessions/split。
- [x] 指标严格采用 spec 定义的 false split、false merge、UNKNOWN、churn、B-cubed F1 和 attribution latency。
- [x] `legacy-baseline` 使用 benchmark 内冻结的 pre-change gallery snapshot（threshold 0.4、整句单窗、raw cosine、EMA 0.2、5 exemplars、旧短句/LRU 行为），并输出 config/manifest fingerprint。
- [x] 一条命令可生成 calibration/evaluation JSON 和控制台摘要；全部合成一个 speaker 会因 false merge/B-cubed precision 失败。

**Verification:** `python -m pytest tests/test_crema_mini_benchmark.py -q` — 4 passed; manifest prepare smoke passed; collapsed fixture false merge `0.9`, B-cubed precision `0.1`; missing audio exits `2` and writes no report; LSP diagnostics clean. Official actor sex metadata checked against CREMA-D `VideoDemographics.csv`. Real ground-truth evaluation remains unrun until CREMA-D audio is downloaded.
