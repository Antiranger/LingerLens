# LagLingo Provider、播放器与开源交付 Spec

**Status:** ready-for-agent

## Problem Statement

LagLingo 已经具备直播下载、本地延迟播放、实时 ASR 与翻译字幕的主链路，但当前配置和播放器行为仍以单机原型为中心：模型设置只能覆盖少数固定记录，无法保存和切换多个供应商；本地 Whisper 一类 OpenAI-compatible ASR 服务无法接入；翻译 usage 已从供应商返回却没有累积计费；字幕在浏览器原生视频全屏时消失，也不能拖动或定制；延迟输入表达的是内部发布预算而不是用户理解的总直播延迟；Bilibili 手动 Cookie 导入缺少平台化校验；停止后服务端仍保留上一直播的会话身份，导致页面把正常停止误判成错误；仓库也尚未完成 Git、依赖安装、密钥隔离和 GitHub 私有仓库交付。

这些问题使用户难以维护多个模型、无法判断费用、无法稳定使用全屏字幕，也阻止项目以安全、可复现的方式发布到 GitHub。

## Solution

把模型设置升级为本机 Provider Catalog：ASR 与翻译都可以新增、编辑、删除和选择多个持久化 Provider Profile，模型设置窗口成为唯一的 Provider 选择入口，播放器外层不再重复显示无用的 ASR/翻译下拉栏。现有百炼协议继续支持，同时新增 OpenAI-compatible Audio Transcriptions 适配，使用户可以连接已经部署好的 Whisper、faster-whisper、Speaches、Xinference 等服务。

在 Provider Profile 中保存 ASR 每秒单价，以及翻译输入、输出、缓存输入的每百万 token 单价。Subtitle Pipeline 累积供应商返回的 usage，只有 usage 和对应价格完整时才估算费用；界面分别展示 ASR 与翻译的用量、费用和合计。

播放器使用 LagLingo 自己的舞台全屏入口，使视频和字幕浮窗一起进入全屏。字幕浮窗可拖动，位置按播放器比例持久化，并允许调整透明度、字号和中外文颜色。直播延迟改为用户可理解的“目标总延迟（秒）”，使用数值输入，必须大于 10 秒，默认 15 秒；内部可以在播放器追赶距离和服务端发布预算之间分配，但不能把单一内部发布延迟冒充总延迟。

Cookie 导入增加明确的平台选择和 Bilibili 流程。YouTube 与 Bilibili 使用各自的关键 Cookie 校验；根据 yt-dlp 当前 Bilibili extractor，`SESSDATA` 是登录态判定的关键 Cookie，其他 Bilibili Cookie 可保留并传给 yt-dlp，但不得伪装为必填项。停止操作需要彻底结束当前媒体/字幕资源并清空会话身份，使用户可以继续探测和播放另一个直播。

最后完成开源交付准备：清除和忽略密钥、Cookie、媒体与运行时产物，提供 Windows 一键依赖安装/校验入口、准确文档、许可证、贡献与安全说明、CI，并创建归属 `Antiranger` 的私有 GitHub 仓库。私有仓库先用于安全审查，公开可见性转换由仓库所有者后续决定。

## User Stories

1. As a LagLingo user, I want to save multiple ASR providers, so that I can switch between cloud and local recognition without re-entering every field.
2. As a LagLingo user, I want to save multiple translation providers, so that I can keep separate OpenAI-compatible endpoints and models.
3. As a LagLingo user, I want each provider to have a recognizable name, so that model identifiers and vendors are not confused.
4. As a LagLingo user, I want to choose the active ASR and translation providers inside model settings, so that the main subtitle controls remain simple.
5. As a LagLingo user, I want saved provider profiles to survive a Companion restart, so that setup is a one-time operation.
6. As a local-model user, I want to connect an OpenAI-compatible Whisper endpoint, so that audio can stay on my own machine.
7. As a local-model user, I want blank or dummy credentials to be supported when the configured local ASR endpoint permits them, so that a cloud-only authentication assumption does not block localhost services.
8. As a user of existing Bailian ASR, I want current Qwen/Fun-ASR behavior to remain available, so that the new catalog does not regress working providers.
9. As a user editing a provider, I want the currently stored API key to be visible, so that I can inspect and replace it directly.
10. As a security-conscious user, I want visible keys confined to the loopback, same-origin settings route and never logged, so that direct display does not expand beyond the local UI.
11. As a user, I want to enter the ASR price per second, so that LagLingo can estimate recognition cost for any vendor.
12. As a user, I want to enter translation input, output, and cached-input token prices, so that different model billing structures are represented.
13. As a user, I want translation usage to come from the provider response rather than guessed tokenization, so that displayed costs are evidence-based.
14. As a user, I want incomplete pricing or missing usage to display as unavailable, so that zero is not confused with free usage.
15. As a user, I want ASR cost, translation cost, and total cost shown separately, so that I can understand where spend comes from.
16. As a user, I want subtitles to remain visible in full screen, so that full-screen viewing does not disable translation.
17. As a user, I want to drag the subtitle window, so that it does not cover faces, game UI, or important text.
18. As a user, I want subtitle position and style to persist, so that every new stream uses my preferred layout.
19. As a user, I want to adjust subtitle opacity, size, and source/translation colors, so that subtitles remain readable on different content.
20. As a keyboard user, I want subtitle customization and full-screen controls to remain accessible, so that drag behavior is not the only interaction path.
21. As a viewer, I want to enter a target total live delay directly, so that the setting matches the delay I experience.
22. As a viewer, I want the target delay to reject values of 10 seconds or less and default to 15 seconds, so that playback and subtitle buffering retain a safe budget.
23. As a viewer, I want actual measured total delay to remain visible separately from the target, so that target and reality are not conflated.
24. As a Bilibili user, I want to paste Bilibili DevTools name/value rows or a Cookie header, so that login-restricted live formats can be probed through yt-dlp.
25. As a Bilibili user, I want the importer to tell me whether `SESSDATA` is missing, so that the message reflects yt-dlp’s actual login check rather than YouTube fields.
26. As a user of both platforms, I want importing one platform’s cookies not to silently destroy the other platform’s saved authentication, so that I can switch platforms without repeated setup.
27. As a viewer, I want Stop to end only the current live session, so that I can immediately probe and start another URL.
28. As a viewer, I want repeated Stop calls to be safe, so that UI retries cannot corrupt session state.
29. As a contributor, I want one documented bootstrap command, so that I can reproduce the environment without reverse-engineering the developer’s machine.
30. As a repository owner, I want secrets and generated media excluded before the first push, so that private credentials never enter Git history.
31. As a contributor, I want CI to run the established Python and Node tests, so that changes are checked consistently.
32. As the repository owner, I want the first remote to be private under my GitHub account, so that I can review security and licensing before making the project public.

## Implementation Decisions

- Introduce a versioned Provider Catalog schema with separate ASR and translation profile lists and one active profile ID per type. Profiles own stable IDs, user labels, protocol kind, model, base URL, API key, protocol options, and pricing.
- Model settings is the single ownership boundary for provider CRUD and active selection. The outer subtitle control area removes ASR and translation provider selectors; starting subtitles uses the active catalog records.
- Provider deletion must preserve catalog validity: the active provider cannot be removed until another profile is selected, and each provider type must retain at least one valid record.
- Existing version-1 provider configuration is migrated without losing working Bailian records, fallback order, API keys, or subtitle preferences.
- The model-settings GET response may return raw API keys because the user explicitly requires direct display. This exception is limited to the loopback, same-origin model-settings route, uses `Cache-Control: no-store`, never appears in general provider/status APIs, logs, errors, or extension messages, and is documented as local-screen exposure.
- Add an OpenAI-compatible ASR protocol kind using `POST /v1/audio/transcriptions` (base URL configurable). It sends multipart audio with model and language, requests timestamp-capable JSON where supported, and parses plain JSON or verbose segment responses.
- LagLingo does not bundle or launch Whisper itself. The user supplies an already running compatible service such as Speaches/faster-whisper-server, Xinference, LocalAI, or another compatible implementation.
- Because OpenAI-compatible transcription servers do not share the current DashScope WebSocket protocol, the adapter implements an `ASRStream` by accumulating PCM into bounded VAD/short-window WAV requests. It emits final events and real segment timing when returned; it must not fabricate interim/stable-prefix capabilities.
- ASR API keys are optional for profiles that intentionally target a local service; Authorization is sent only when configured. Remote endpoint validation and documentation must make this choice explicit.
- ASR pricing is CNY per audio second. Translation pricing is CNY per one million non-cached input tokens, cached input tokens, and output tokens.
- Translation usage normalizes common OpenAI-compatible fields: `prompt_tokens`/`completion_tokens` plus `prompt_tokens_details.cached_tokens`; compatible `input_tokens`/`output_tokens` naming may also be accepted at the boundary.
- Translation cost is calculated as non-cached input × input rate + cached input × cached-input rate + output × output rate. Usage and cost are accumulated per actual result provider so fallback traffic is attributed correctly.
- If usage or a required price is absent, the corresponding estimate is `null`/unavailable, not zero. No local tokenizer guess is introduced.
- Subtitle rendering remains wall-clock scheduled. A separate subtitle-window controller owns normalized position, scale, opacity, source color, translation color, reset, and local persistence.
- Full screen is requested on the player stage rather than the raw video element. The LagLingo full-screen control is the supported path, and native video-only full screen is suppressed where browser controls permit it.
- The draggable handle is interactive, but the rest of the subtitle layer must not intercept normal video controls. Position is clamped after drag, resize, and full-screen transitions.
- Replace the internal-facing publication-delay selector with `targetDelaySeconds`, a numeric user input with minimum 11 seconds and default 15 seconds. The system may split this target between service-side unpublished media and browser live-sync distance, but actual measured total delay remains separately reported.
- Live start and live retuning validate the same target-delay range. Existing subtitle-budget recommendations update the user’s target rather than exposing an implementation-only publisher value.
- Stopping a live session clears page URL, quality, playlist identity, timestamps, errors, HLS attachment, source ingest, subtitle pipeline, and transient cues while preserving saved providers, subtitle preferences, target delay, and imported authentication.
- Cookie import becomes platform-aware. YouTube keeps its existing critical-cookie guidance. Bilibili treats `SESSDATA` as the login-critical field because that is what the current yt-dlp extractor checks; `bili_jct`, `DedeUserID`, and other valid cookies are preserved when present but are not falsely required.
- Imported authentication is persisted/merged by supported platform or selected by target URL, so a Bilibili import does not silently overwrite the saved YouTube snapshot and vice versa.
- Open-source bootstrap is Windows-first because the current runtime, Native Messaging helper, vendored yt-dlp binary, and primary test environment are Windows. Cross-platform installers are not required by this delivery.
- Use the MIT license for the initial repository, include third-party notices, accurate installation/configuration/security/contribution documentation, and a minimal CI workflow running established tests.
- The first GitHub remote is a private repository named `LagLingo` under the authenticated `Antiranger` account. Creation/push occurs only after secret scanning and clean-clone bootstrap verification pass.

## Testing Decisions

- Tests verify observable behavior through three agreed public seams rather than private helpers:
  1. **Loopback HTTP API** for provider CRUD/persistence, active selection, Cookie import, target delay, start/stop/restart, and status.
  2. **Provider interfaces plus Subtitle Pipeline status** for OpenAI-compatible ASR events, usage normalization, fallback attribution, and literal cost calculations.
  3. **Browser player behavior** for stage full screen, subtitle-window persistence/clamping/customization, removal of outer provider selectors, and reusable playback controls.
- Every implementation ticket follows red → green vertical slices: add one failing behavior test at its listed seam, implement only enough to pass, then continue to the next behavior. Do not bulk-write all tests before implementation.
- Existing provider, server, subtitle-pipeline, cue-scheduler, web-asset, core, ingest, and synthetic playback tests are prior art and must remain green.
- Cost tests use worked literal examples independent of implementation formulas and cover cached, non-cached, output, missing-usage, missing-price, and fallback-provider cases.
- Local ASR tests use a fake HTTP transcription server and deterministic WAV/PCM samples; they do not require a real GPU or downloadable Whisper model.
- Full-screen behavior receives both a controller-level automated test and a real Chromium smoke because native full-screen DOM ownership cannot be proven by regex asset tests alone.
- Session tests cover start → stop → clean idle → probe/start a different URL, plus idempotent stop.
- Release verification includes a secret scan, clean-clone bootstrap smoke, full test suite, and GitHub visibility check.

## Out of Scope

- Bundling, downloading, or supervising a local Whisper/faster-whisper model runtime inside LagLingo.
- Automatically discovering prices from vendor websites or billing APIs.
- Guessing translation token usage when the provider does not return usage.
- Supporting arbitrary proprietary ASR streaming protocols beyond existing DashScope and the new OpenAI-compatible transcription endpoint.
- Speaker diarization, OCR, TTS, subtitle export, or multi-language auto-detection redesign.
- Bypassing DRM, premium entitlements, regional restrictions, or platform anti-bot controls.
- Guaranteeing exact wall-clock equality between target and measured delay on every platform; the UI reports the measured result.
- A macOS/Linux one-click installer in this delivery.
- Making the GitHub repository public automatically. The initial repository remains private pending owner review.

## Further Notes

- Official OpenAI Audio Transcriptions documentation defines multipart `POST /audio/transcriptions` with file, model, language, response format, optional VAD/chunking, and timestamp-capable responses. Xinference and the faster-whisper ecosystem expose compatible endpoints, but compatibility varies; the adapter must degrade gracefully when optional fields are absent.
- The existing OpenAI-compatible translation provider already returns the response `usage` object, but the Subtitle Pipeline currently records only latency and discards usage. This is the narrow metering insertion point.
- The current ASR provider base already exposes `price_per_second_cny`, and pipeline status already estimates one combined ASR cost. The new work should deepen that existing seam instead of creating a second accounting system.
- The current Bilibili yt-dlp extractor defines login as the presence of the `SESSDATA` cookie on `api.bilibili.com`. No evidence supports making `bili_jct` or `DedeUserID` mandatory for yt-dlp login detection.
- The current stop symptom is consistent with stale session identity: media processes stop, but `pageUrl` remains in status, and the browser treats idle + prior URL + no playlist as an error. The contract is clean idle state, not a UI-only suppression.
- The local directory is not yet a Git repository. GitHub CLI is authenticated as `Antiranger` with repository/workflow scopes, so repository initialization and private publication are feasible after the security gate.
