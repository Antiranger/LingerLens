# 02 — 将 Speaker Attribution 收口到单一有序 Worker

**What to build:** 让每条 final cue 的 PCM 复制、声纹前向和 gallery mutation 通过一个 FIFO worker 完成，消除 extractor 并发和状态乱序，同时保持字幕主流程非阻塞。

**Blocked by:** None — can start immediately

**Status:** complete — verified 2026-08-31

- [x] final cue 入队时复制对应 PCM，job 不依赖稍后可能被 prune 的 timeline；缺少 begin PCM 的 approx cue 保持 UNKNOWN并计数。
- [x] 单 worker 严格按 ASR final FIFO mutation gallery；timestamp inversion 超过 100 ms 时只记录诊断，不等待或回滚。
- [x] 同一 extractor 不被并发调用。
- [x] bounded queue 默认容量 32；满时丢最旧 queued job、把该 cue 决定为 UNKNOWN，且不阻塞 ASR/翻译/cue 创建。
- [x] stop 按“停止接收 → 最多 drain 2 秒 → 取消并完成剩余 UNKNOWN”的契约工作。
- [x] `wait_for_diarization` 能稳定等待 queue 和 active job 完成。
- [x] 现有无 diarizer、late-fix、关闭会话和字幕主流程行为保持不变。

**Verification:** `python -m unittest discover -s tests -p test_subtitle_pipeline.py -v` — 24 tests passed; LSP syntax diagnostics clean for implementation and test files.
