# 01 — Provider Catalog 与本地 OpenAI-compatible ASR

**What to build:** 把模型设置升级成可保存、编辑、删除和选择多个 ASR/翻译 Provider Profile 的本机目录，并让已经运行的 OpenAI-compatible Whisper/faster-whisper 服务能通过同一字幕链路工作。模型设置直接显示当前保存的 API Key；播放器外层删除 ASR/翻译选择栏，启动时使用目录中的 active Provider。

**Blocked by:** None — can start immediately.

**Status:** complete

- [ ] 先在 loopback model-settings API seam 写失败测试：创建两个 ASR Profile 和两个翻译 Profile 后，GET 能按稳定 ID 完整返回，active 选择和 Profile 内容在 Companion 重启后保持。
- [ ] 先写失败测试：现有 version-1 百炼配置迁移后仍保留 active、fallback、密钥、模型参数和字幕偏好，不需要用户重新录入。
- [ ] 模型设置支持 Provider 的新增、编辑、删除和 active 切换；禁止删除当前 active Profile，且 ASR/翻译各至少保留一个有效 Profile。
- [ ] ASR Profile 支持现有 DashScope Qwen Realtime、DashScope Task ASR，以及新的 OpenAI-compatible Audio Transcriptions kind；翻译 Profile 支持多个 OpenAI-compatible 记录并继续保留现有 Qwen-MT/fallback 能力。
- [ ] 模型设置 GET/POST 只允许 loopback + same-origin，返回 `Cache-Control: no-store`；按用户要求返回并直接显示 API Key，但普通 `/api/providers`、状态、日志、异常和扩展消息仍不得回显密钥。
- [ ] 新 OpenAI-compatible ASR 使用可配置 Base URL、模型、语言、可选 API Key 和短窗/VAD 参数，请求 multipart `/v1/audio/transcriptions`，兼容 `json`/`verbose_json` 文本及 segment 时间戳。
- [ ] 新 ASR 通过 bounded PCM→WAV 分窗实现现有 `ASRStream` 接口；只声明真实能力，不伪造 interim、stable-prefix、server-VAD 或 word timestamps，且请求失败不得阻塞播放主链路。
- [ ] 用本地 fake transcription HTTP server 做红→绿测试，覆盖有/无 Authorization、multipart 字段、segment timing、纯文本响应、错误响应和关闭/flush。
- [ ] 模型设置窗口能选择 Provider kind、名称、模型、Base URL、Key 与协议参数；外层字幕设置中的 `asrProvider` 和 `translationProvider` 控件及对应请求字段被删除。
- [ ] 启动字幕时服务端仅从 active Catalog 记录创建 ASR 与翻译链，保存后下一次直播自动使用新 active Provider。
- [ ] 更新 Provider 示例配置和本地 Whisper 接入文档，明确 LagLingo 不负责安装或启动 Whisper 服务，并给出 Speaches/Xinference/其他 OpenAI-compatible endpoint 的配置示例。
- [ ] 现有 provider/server/pipeline/web tests 全部保持绿色。
