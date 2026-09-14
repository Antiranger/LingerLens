# LagLingo 全球语言与实时 Provider MVP Spec

**Status:** ready-for-agent

## Problem Statement

LagLingo 已具备 Provider Catalog、实时字幕 Pipeline、DashScope ASR、OpenAI-compatible 文件转写和 OpenAI-compatible/Qwen-MT 翻译，但语言能力仍被当前原型限制：播放器把源语言写死为日语，目标语言只有中英文，ASR 接口只能接收一个语言字符串，翻译模块存在多个不完整的语言名称字典，也没有 Claude 与 Gemini 的正式协议 Adapter。

完整适配所有云厂商会同时引入多套 SDK、鉴权、流重连和语言矩阵，交付面过大。首版必须用最小实现证明一条全球多语言主链路：用户可选择源语言或自动检测、选择大量目标语言，现有 DashScope 继续工作，同时新增一个具备全球 streaming/code-switching 能力的 STT 和一个主流 OpenAI 实时 STT，再补齐 Claude/Gemini 翻译协议。

## MVP Outcome

用户可以在字幕设置中配置源语言策略与目标语言；LagLingo 内部使用 BCP 47；当前 Provider 不支持某项语言能力时，系统在启动前给出明确错误。首版 Provider 组合为：

- **ASR**：现有 DashScope Qwen Realtime、DashScope Task ASR、OpenAI Audio Transcriptions；新增 Deepgram Streaming、OpenAI Realtime Transcription。
- **翻译**：现有 OpenAI-compatible、Qwen-MT；新增 Anthropic Messages、Google Gemini GenerateContent。
- **语言 UI**：可搜索语言选择器，支持名称、母语名称和 tag；显示模式不再绑定“中日/中文”。

首版完成后，Azure、Google Cloud STT、AWS Transcribe、AssemblyAI、Speechmatics 可以沿同一 Adapter seam 独立加入，不再改语言领域模型或 Pipeline 主接口。

## User Stories

1. As a viewer, I want to specify the spoken source language, so that ASR can use the correct language model or hint.
2. As a viewer, I want to select automatic source-language detection when the active ASR supports it, so that I do not need to know the stream language in advance.
3. As a viewer, I want to choose a target language from a broad searchable catalog, so that subtitles are not limited to Chinese or English.
4. As a viewer, I want language names to distinguish scripts and regions, so that `zh-Hans`/`zh-Hant` and `pt-BR`/`pt-PT` are not conflated.
5. As a viewer, I want unsupported language settings rejected before playback starts, so that a Provider does not silently transcribe or translate in the wrong language.
6. As a Deepgram user, I want native realtime PCM streaming and multilingual code-switching, so that global live streams can be captioned with timestamps.
7. As an OpenAI user, I want the current Realtime transcription protocol, so that I can use OpenAI without short-window file uploads.
8. As a Claude or Gemini user, I want first-class translation Provider profiles, so that I do not need an OpenAI-compatible proxy.
9. As a user of existing DashScope and Qwen-MT profiles, I want the migration to preserve my settings and current behavior.
10. As an Arabic or Hebrew subtitle viewer, I want mixed RTL/LTR text to remain readable.

## Implementation Decisions

### Language contract

- Add `langcodes>=3.5,<4` for server-side BCP 47 validation and canonicalization.
- Introduce `SourceLanguagePolicy` with only two MVP modes:
  - `specified`: one canonical `tag`.
  - `detect`: optional canonical `candidates`, optional `preferred`, and `allowCodeSwitching`.
- `auto` is never stored as a language tag.
- Target language is always one explicit canonical BCP 47 tag.
- Extend ASR capabilities only with the fields required by this MVP: supported tags, detection mode (`none|unrestricted|candidates`), maximum candidates, detected-language reporting, code-switching, and preferred sample rate.
- Capabilities are resolved from Provider kind/model presets. Unknown custom models are experimental and cannot claim verified detection support.

### Catalog and UI

- Check in one generated compact `languages.json`; do not hand-maintain display-name dictionaries in Python providers.
- The catalog contains canonical tag, English name, autonym, direction and aliases. It is language identity data, not a claim that every Provider supports every entry.
- Browser display names may be localized with `Intl.DisplayNames`; server validation remains authoritative.
- Replace the fixed target `<select>` with a keyboard-accessible searchable selector. Use the same selector for specified source language and detection candidates.
- Replace “中日双语 / 仅中文 / 仅原文” with “原文+译文 / 仅译文 / 仅原文”.
- Source and translated subtitle lines each use bidi isolation and `dir="auto"`; keep catalog direction as a fallback.
- UI locale remains separate. Full UI translation is not required in this MVP.

### Configuration migration

- Upgrade Provider configuration to version 3.
- Migrate `sourceLanguage: "ja"` to `{ "mode": "specified", "tag": "ja" }`.
- Migrate target `zh` to `zh-Hans`; preserve all Provider profiles, IDs, active/fallback values, keys, prices and other subtitle preferences.
- Persist source/target settings through the existing model/settings ownership seam; start requests may carry the selected subtitle language settings but may not override active Provider identity.

### Pipeline

- Change `ASRProvider.stream(language: str, ...)` to accept `SourceLanguagePolicy` and the negotiated sample rate.
- Make SubtitlePipeline PCM sample rate instance-owned instead of fixed at 16 kHz; MVP supports 16 kHz and 24 kHz.
- Preserve the current Pipeline as the single generic reconnect owner. Adapters own one Provider session and any Provider-mandated rollover, not a second generic retry loop.
- Canonicalize Provider-reported languages at the Adapter edge.
- Each final cue uses its reported dominant language when available; otherwise it falls back to specified/preferred language.
- Translation source language comes from the cue, not one immutable stream-level string.
- For a mixed-language cue, generic LLM Providers receive a mixed-language instruction; Qwen-MT is skipped when its language-pair contract cannot represent the cue.

### STT Providers

- Add `deepgram-streaming` using the official low-level WebSocket protocol with `aiohttp`: binary PCM, `Results`, interim/final mapping, timestamps, detected languages, `Finalize`, keepalive and orderly close.
- Add `openai-realtime-transcription` using the current transcription-session schema, `input_audio_buffer.append`, delta/completed events and `item_id` correlation.
- Provide separate OpenAI presets for low-latency `gpt-live-transcribe` and detected-language `gpt-transcribe`; do not reuse legacy realtime-preview payloads.
- Refresh DashScope model presets and capability metadata without duplicating the existing `dashscope-task-asr` protocol Adapter.
- Do not add Deepgram/AssemblyAI/OpenAI official SDK dependencies when the official low-level protocol is sufficient.

### Translation Providers

- Keep `openai-compatible` and `qwen-mt` unchanged as public kinds.
- Add `anthropic-messages` using `/v1/messages`, `x-api-key`, `anthropic-version` and top-level `system`.
- Add `google-genai` using `models/{model}:generateContent`, `x-goog-api-key`, `systemInstruction` and `contents.parts`.
- Extract one provider-neutral prompt builder used by OpenAI-compatible, Anthropic and Gemini. Qwen-MT continues using `translation_options`.
- Normalize translation usage into non-cached input, cached input, cache-write input and output tokens. Add an optional cache-write price; missing usage or price remains unavailable, never zero.
- Normalize auth/config, request, rate-limit, unavailable and timeout errors so the existing fallback chain can make deterministic decisions.

## Testing Decisions

- Use the existing stable seams: loopback model-settings/providers API, Provider interfaces, SubtitlePipeline behavior and browser-observable UI.
- Every ticket starts with one failing vertical behavior test, then adds only enough implementation to pass it.
- Provider tests use local fake HTTP/WebSocket servers and captured official event fixtures; no cloud key is required for CI.
- Language tests cover canonicalization, migration, invalid policies, candidate limits, region/script distinctions and Provider capability rejection.
- Pipeline tests cover 16/24 kHz clocks, detected-language propagation, mixed-language fallback and failure isolation from playback.
- Translation tests cover exact headers/payloads, response parsing, usage normalization, refusal/empty response and fallback error classes.
- Run one manual live spike for each new STT Provider before marking its preset verified. A Provider may ship as `provider_claimed` without credentials, but documentation must say it was not live-verified.
- Existing provider, server, pipeline, subtitle scheduler, browser and release tests must remain green.

## Acceptance Criteria

- A user can save and restart with specified or detect source policy and a canonical target language.
- The UI exposes a searchable catalog and blocks settings unsupported by the active ASR/translation profile.
- Existing `ja → zh` configurations migrate to `ja → zh-Hans` with no Provider/key loss.
- Deepgram and OpenAI Realtime fake protocol tests produce correct interim/final events and release all resources on stop.
- Claude and Gemini fake API tests translate through the existing Pipeline and attribute usage to the actual fallback Provider.
- Arabic/Hebrew and Latin/CJK source/target combinations render without direction leakage.
- Subtitle failures still cannot stop or backpressure media playback.

## Out of Scope

- Azure Speech, Google Cloud STT, AWS Transcribe, AssemblyAI and Speechmatics Adapters.
- Full application UI localization beyond establishing a separate UI-locale field/seam.
- Speaker diarization UI, speaker identity, OCR, TTS or subtitle export.
- Automatic downloading or running of local ASR models.
- A universal promise that every catalog language is supported or high quality.
- Provider price discovery, tokenizer-based usage guessing or dynamic fetching of Provider language lists.
- A hand-built generic WebSocket protocol editor.

## Source Plan

Detailed research and future Provider expansion remain documented in `docs/global-language-provider-expansion-plan.md` and `docs/research/`. This MVP Spec deliberately narrows that plan to the smallest architecture-complete release.
