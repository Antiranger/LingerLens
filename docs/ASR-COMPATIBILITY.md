# ASR subtitle compatibility audit

Audit date: 2026-09-30. This audit exercises the shipped adapters, `SubtitlePipeline`, `CaptionChunker`, translation workers and `CueStore`. Most checks use recorded-format protocol fixtures and a local HTTP service. Qwen Audio 3.1 was additionally tested with a 9.09-second locally synthesized English sample against the real service; that narrow check does not certify other languages, regions or accounts. The committed fixture contains only synthetic speech and word times, without credentials.

## What seven seconds means

`HARD_DEADLINE_SECONDS = 7.0` bounds how long an open caption waits **after stable lexical evidence becomes available**. It is a local publication deadline, not a command that forces every service to finish an utterance, and not a universal maximum duration for every resulting cue.

- Stable tokens/prefixes can be released without waiting for the vendor's endpoint. The same shared chunker receives normalized evidence from each adapter.
- A mutable partial is not confirmed text. The realtime chunker does not turn it into a subtitle just because seven seconds elapsed. Final-only services may therefore miss the playback budget when they delay their final result.
- A single untimed final covering a long utterance cannot be split into correctly timed words. Services with sentence timestamps preserve those sentence ranges; services without onset timestamps use an explicitly approximate interval.
- Provider-native bilingual captions keep provider segment identities. A local cut can consume native translation only at a trusted source/translation boundary. Arrival order and character ratios do not establish that correspondence. Unaligned fragments retain source text; an explicitly configured fallback translator can translate them separately.
- The HTTP transcription adapter sends bounded audio windows (default UI preset: three seconds; validated maximum: six seconds). This can cut speech mid-word. Its bounded pending queue deliberately drops old windows under sustained overload; it does not guarantee lossless transcription when requests cannot keep up.

## Adapter matrix

These are local integration results. A protocol appearing here does not certify every custom model, region, source language, quota or live network condition.

| Settings entry | Internal kind | Evidence usable before utterance completion | Timing used by subtitles | Seven-second behavior |
| --- | --- | --- | --- | --- |
| Soniox, bilingual | `soniox-realtime` | Stable lexical token deltas | Lexical timestamps mapped through audio breadcrumbs | Releases stable evidence; native translation requires trusted boundaries or fallback |
| Soniox, recognition | `soniox-realtime-transcribe` | Same token adapter, translation disabled | Same lexical timeline | Stable evidence can release without an endpoint |
| 千问 / Qwen, bilingual | `dashscope-livetranslate-realtime` | Stable source prefix | Server speech-start/stop plus sent-audio frontier | Source can release; a cut is not automatically an aligned native translation |
| 千问 / Qwen, recognition | `dashscope-qwen-realtime` | Stable source prefix; mutable stash excluded | Server speech boundaries | Stable evidence can release without an endpoint |
| 千问 / Qwen Audio 3.1 | `dashscope-qwen-realtime` (streaming model) | Mutable hypotheses; confirmed words at finalization | Sentence and lexical word timestamps | Shared chunker uses confirmed word boundaries; mutable words remain uncommitted |
| 阿里云 / Alibaba | `dashscope-task-asr` | Mutable sentence hypotheses; final sentence is authoritative | Sentence begin/end and confirmed word timestamps | Waits for final; seven seconds cannot certify a mutable hypothesis |
| OpenAI, segments | `openai-audio-transcriptions` | Completed HTTP windows | Window offset plus returned segment ranges, or whole window | Audio windows are bounded before the request; service latency remains unbounded by the chunker |
| OpenAI, realtime | `openai-realtime-transcription` | Mutable deltas; final is authoritative | Server speech boundaries; approximate fallback if absent | Waits for final when only mutable deltas exist |
| Deepgram | `deepgram-streaming` | `is_final` token intervals; `speech_final` is a separate endpoint | Word timestamps | Stable intervals can release before the utterance endpoint |
| AssemblyAI | `assemblyai-streaming` | Words marked `word_is_final`; other words remain mutable | Word timestamps | Stable words can release; final formatting is reconciled |
| 豆包 / Doubao | `volcengine-sauc` | Mutable hypotheses; definite utterances are authoritative | Utterance timestamps | Waits for definite text |
| ElevenLabs | `elevenlabs-scribe-realtime` | Mutable partial; committed text is authoritative | Approximate interval between commits; delayed word metadata is not retroactively used for cues | Waits for committed text; no exact word alignment claim |
| Speechmatics | `speechmatics-realtime` | Mutable partial; `AddTranscript` is authoritative | Transcript interval | Waits for authoritative transcript |
| 腾讯 / Tencent | `tencent-asr` | Mutable slices; final slice/sentence is authoritative | Sentence/slice timestamps | Waits for final text |

## Defects fixed

Untimed commits previously restarted at the stream origin. A three-commit ElevenLabs replay produced `0–3`, `0–8`, `0–13` seconds, even though the recognized phrases were distinct. The existing matrix checked only `end >= start`, allowing this error to pass.

The pipeline now remembers the last completed audio frontier, retains an item's onset while it receives updates, and resets that frontier for a new recognition generation. The replay produces `0–3`, `3–8`, `8–13` seconds and records `timingSource=approx`. These ranges can include silence and transmission delay; they are not acoustic or word boundaries. Vendor timestamps and VAD remain preferred. A reconnect fixture verifies that reused item IDs move to the new session's `80–83` second interval instead of inheriting the old `40–43` interval.

The desktop starts without a translation profile. Newly added Soniox profiles previously enabled native translation fallback by default, causing session startup to require a nonexistent translator. New profiles now leave fallback disabled. Existing saved options are preserved; users can enable fallback after configuring their translator.

## Settings changes

Protocol names are short vendor names, with the existing bilingual/recognition groups retained. Chinese uses 千问、腾讯、豆包、谷歌. OpenAI's two ASR transports keep short realtime/segment qualifiers because they require different requests.

Every ASR protocol has a model selector populated from models handled by its adapter. Selecting a model fills the editable model ID. Custom models remain possible. Tencent selection also updates `engineModelType`, the option actually sent to its service. Changing the model does not overwrite the profile name, endpoint, credentials or unrelated options.

Qwen's bilingual picker initially offers the existing 3.5 subtitle timing preset. Previous live investigations found cross-segment source timing with 3.8, so its existing experimental warning remains. This does not mean 3.8 cannot connect: a fresh 2026-09-30 test using the saved account and a locally synthesized English sample returned three English/Chinese captions through the actual adapter, native translation ledger, caption chunker and store. Their audio ranges were `0.00–0.50`, `1.24–3.936` and `4.568–8.40` seconds, without API errors. An older failure was the server's unsupported default `Chelsie` voice; the adapter already pins a working voice and text output. This short sample does not certify continuous Japanese speech, long native segments or precise timing across every language. All five UI locales translate the selector and its guidance.

## Verification

- The expanded provider matrix contains 16 tests and covers all 13 settings entries, including actual adapter → pipeline → store execution, repeated speech, known timestamp ranges, HTTP window offsets, duplicate final observations and reconnect origins.
- Stable evidence is replayed through Soniox, Qwen recognition, Qwen bilingual, Deepgram and AssemblyAI and released at the local deadline without a provider endpoint. Soniox tokenizer fixtures retain the final incomplete whitespace word rather than claiming it is ready.
- Mutable hypotheses from eight adapter paths remain unpublished after the deadline. Recognition-only WebSocket paths also run through the actual translation worker and retain their cue ordering.
- Existing native translation tests cover trusted/untrusted anchors, missing translations, delayed translation, source-only outcomes, fallback, whole-segment correspondence and generation isolation.
- Full Python suite: 913 tests passed; the optional `streamlink_ingest` file was skipped because Streamlink was unavailable. Both browser smoke files passed using installed Chrome. Player suite: 258 tests passed. Root suite: 52 tests passed.
- An isolated source backend and Electron DOM check verifies Chinese protocol names, fresh Soniox fallback disabled, Tencent model/engine synchronization, Qwen 3.5 selection, and switching all five UI locales. It does not modify the installed application's user profile.

## Official references consulted

- [Alibaba Cloud realtime translation](https://www.alibabacloud.com/help/en/model-studio/qwen3-5-livetranslate-flash-realtime): 3.5/3.8 have different session fields, event contracts and voice configuration. The app's timing recommendation is based on its recorded investigations, rather than the vendor's general model recommendation.
- [ElevenLabs realtime API](https://elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime): realtime partial/committed events and optional delayed timestamp metadata are distinct.
- [Google model catalog](https://ai.google.dev/gemini-api/docs/models): `gemini-2.5-flash` is a text-capable model ID offered by the existing Google translation protocol. This audit does not measure its live translation latency.

Live acceptance is still needed for the less-used providers with the intended account, language pair, region, real speech and sustained network conditions. These local checks establish integration behavior and make the limits explicit; they do not replace that acceptance.

## Caption display and history repair

Without speaker labels, newer recognition chunks replace older overlapping chunks in the same generation at their onset. Distinct speaker labels retain true overlaps; exact equal starts retain their independent rows. Browser regression checks verify that a long older row cannot push newer undiarized captions below the video while all three cues remain in history.

Temporary translation timeouts, unavailable services and rate limits receive up to five background retries, delayed by 2, 5, 15, 30 and 60 seconds, with a fresh 30-second request budget per attempt (or the configured timeout when larger). Live translation workers continue independently. Successful repairs advance the original cue revision and polling sequence. Existing translations cannot be overwritten. Authentication/request failures, target-language changes, stopped playback and evicted history do not keep retrying obsolete work. Very late results enrich history; the renderer retains its stale-caption limits.

Qwen 3.1 request fields and word timestamps follow [client events](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-streaming-client-events) and [server events](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-streaming-server-events). Selecting it under the Qwen connection uses task-ASR; the old Realtime transport still handles `qwen3-asr-flash-realtime`. Official endpoints switch between `/api-ws/v1/inference` and `/api-ws/v1/realtime`; custom gateway endpoints remain untouched.
