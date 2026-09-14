# 统一实时 ASR 证据契约

实时适配器把供应商字段映射为三类证据：稳定文字增量（以后不改）、临时文字（可改，禁止进入最终字幕）、结束/端点（只表示服务端检测到边界）。Soniox `<end>`、Deepgram `speech_final`、Qwen `completed`、Speechmatics `EndOfUtterance`、AssemblyAI 双结束标志都在 Adapter 内转换；CaptionChunker 不读取厂商字段。

端点只释放当前已稳定的文字，不提前关闭 item；Qwen 的 speech_stopped 可能先于 completed。最终文字到达后按时间和已发文字 reconciliation，避免漏字和重复。实时模式先输出稳定句末，再对达到 4 秒的现有标点边界使用窄保护；不等待整段结束标记才显示。

翻译是显示门槛：Cue 在翻译成功前为 pending/translating，前端只接受 state=done 且非空 `zh`。失败或空结果在同一总 deadline 内最多尝试三次（首次加两次重试）；耗尽后标记 failed 且隐藏，不展示原文。不同 cue 由 worker pool 并行，单条重试不会阻塞其它 cue。

已核对的 Provider 语义：Soniox stable token + `<end>`；Qwen `text`/`stash` + completed；Deepgram `is_final`/`speech_final`；Speechmatics Partial/AddTranscript/EndOfUtterance；AssemblyAI `word_is_final`/`end_of_turn && turn_is_formatted`；DashScope `sentence_end`。文档与实验依据见 `.scratch/soniox-endpoint-comparison/report.md`。
