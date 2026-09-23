# Provider reference

This is a protocol and configuration reference, not a promise that every provider account works. Verify region, model, quota, billing, service terms and supported language capabilities with the provider. Fake HTTP/WebSocket fixtures prove request contracts only; they do not prove real account or gateway success.

## Recognition

Implemented adapters include DashScope task/realtime ASR, Soniox Realtime, Deepgram Streaming, OpenAI Realtime Transcription and Audio Transcriptions, AssemblyAI Streaming, Volcano Engine, ElevenLabs Scribe Realtime, Speechmatics Realtime and Tencent Cloud ASR. Provider-native bilingual sessions currently include DashScope LiveTranslate and Soniox when translation is enabled.

DashScope LiveTranslate answers on the global realtime host, `wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=<id>` — verified against a real account on 2026-09-21 with an API key and nothing else. The per-workspace host the provider docs print, `wss://<workspaceId>.<region>.maas.aliyuncs.com/api-ws/v1/realtime`, is an alternative some accounts are told to use; **业务空间 ID / WorkspaceId** is optional and the adapter only substitutes it when the address itself carries a placeholder (an unreplaced one fails fast and says which field to fill). What is not optional is `voice`: the session the server starts carries `Chelsie`, Qwen 3.8 rejects that voice on its first generated turn and drops the connection, so the adapter pins a voice it accepts (`Tina`, configurable) even for text-only subtitles. Its caption language comes from 字幕与弹幕设置 → 目标语言, and LiveTranslate 3.8 detects the spoken language itself (a source-language pin reaches only a 3.5 model).

## Translation

Implemented protocols include OpenAI-compatible `/chat/completions`, Qwen-MT, Anthropic Messages and Google Gemini `generateContent`. The app sends only the current caption plus bounded continuity context defined by the pipeline; it does not claim that a provider preserves data beyond its own terms.

## Language support

The UI catalog has five locales. ASR language detection, code switching and target-language coverage are provider-specific. Use the `/api/languages` capability response and the settings dialog. A language appearing in the catalog means it can be selected as a label; it does not mean every ASR adapter recognizes it.

## Credentials and cost

Use environment variables or the local provider settings file. Do not commit `runtime/providers.json`, API keys, Cookie snapshots or signed stream URLs. Usage and price fields are optional; missing data is reported as unavailable rather than zero. Never paste real credentials into tests or issues.
