# Realtime Streaming Multilingual Speech-to-Text (STT) Provider Adapters

> **Target File:** `docs/research/realtime-stt-provider-adapters.md`  
> **Date:** 2026-08-31  
> **Scope:** Architectural evaluation, canonical wire protocols, SDK tradeoffs, session lifecycles, VAD/interim semantics, and integration strategy for live streaming STT providers in the LagLingo companion service.
>
> **Verification note:** This is a preliminary subagent research artifact. Primary-source reconciliation found several time-sensitive corrections: current OpenAI Realtime transcription starts with `gpt-live-transcribe` and the current transcription-session schema, while `gpt-transcribe` is the option that returns detected-language output; AssemblyAI Streaming v3 now advertises multilingual/code-switching models and must not be dismissed as English-only; Speechmatics' ordinary realtime examples still configure one language, so automatic realtime code-switching is not assumed without a model-specific contract; the existing LagLingo `dashscope-task-asr` adapter already implements the documented `run-task`/binary audio/`result-generated` flow, so new DashScope models may need presets and capability metadata rather than a new protocol kind. Exact price, latency, and maximum-session-duration claims are benchmark inputs, not implementation contracts. The final implementation plan supersedes priority statements in this report.

---

## 1. Executive Summary & Prioritized Recommendations

LagLingo operates as a low-latency live subtitle companion service built on `aiohttp` and `asyncio`. Audio chunks (typically 16 kHz 16-bit mono PCM, ~100ms–200ms per frame) arrive continuously from live HLS streams. The current ASR abstraction (`companion/providers/base.py`) defines `ASRProvider`, `ASRStream`, and `ASREvent` (`event_type in {"interim", "final", "error"}`).

### Top 5 Recommended Provider Priority

1. **Deepgram Live STT (`deepgram-streaming`, Priority 1 / Native WebSocket):**
   * *Verdict:* **Adopt immediately as Tier-1 primary global provider.**
   * *Rationale:* Clean, modern duplex WebSocket API (`wss://api.deepgram.com/v1/listen`), raw binary PCM framing, explicit `is_final` and `speech_final` flags, millisecond timestamps, code-switching (`language=multi`), low latency Nova-2/Nova-3 models, and predictable keepalives. Zero heavy C-extension SDK required.
   * *Implementation:* Native `aiohttp` WebSocket client.

2. **Alibaba DashScope Fun-ASR / Qwen-Audio Realtime (`dashscope-realtime-asr`, Priority 1 / Native WebSocket):**
   * *Verdict:* **Adopt as Tier-1 primary East Asian (ZH/EN/JA/KO) provider.**
   * *Rationale:* Direct upgrade to existing `asr_qwen_realtime.py`. Modern endpoints support `wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference` with `run-task` / `continue-task` / `finish-task` wire protocol. Supports sentence & word timestamps on `fun-asr-realtime` and `qwen-audio-3.0-asr-flash-streaming`, superior CJK accuracy, and raw binary Opus/PCM streaming.
   * *Implementation:* Native `aiohttp` WebSocket client (refactoring legacy DashScope task/Qwen implementations).

3. **Speechmatics Realtime (`speechmatics-streaming`, Priority 2 / Native WebSocket):**
   * *Verdict:* **Adopt initially for high-quality specified-language realtime transcription.**
   * *Rationale:* The WebSocket protocol (`StartRecognition`, binary audio, `AddPartialTranscript`, `AddTranscript`, `EndOfStream`) and realtime diarization/timestamps are well documented. Automatic realtime code-switching must be enabled only for a specific model whose official contract and live fixture prove it; ordinary v2 examples configure one language.
   * *Implementation:* Native `aiohttp` WebSocket client or official `speechmatics-python`, selected during the adapter vertical slice.

4. **Azure AI Speech Continuous Recognition (`azure-speech-sdk`, Priority 2 / SDK-Backed):**
   * *Verdict:* **Adopt via optional SDK-backed worker adapter.**
   * *Rationale:* Rock-solid continuous recognition and real-time multilingual language identification (`Continuous LID`). However, Microsoft's wire protocol over WebSocket uses proprietary framing (`Path: audio`, binary headers) and the official recommended approach is `azure-cognitiveservices-speech` using `PushAudioInputStream`.
   * *Implementation:* SDK-backed adapter running in an executor thread or dedicated sub-process to isolate binary C++ bindings from the `aiohttp` event loop.

5. **OpenAI Realtime Transcription (`openai-realtime-transcribe`, Priority 3 / Native WebSocket):**
   * *Verdict:* **Adopt as specialized Tier-2 provider; defer as default.**
   * *Rationale:* The current transcription-session workflow uses `gpt-live-transcribe` for incremental low-latency deltas; `gpt-transcribe` is the specialized realtime option when detected-language output is needed. Audio is base64-encoded in JSON `input_audio_buffer.append` events. It is valuable for users already in the OpenAI ecosystem, but its lack of word timestamps requires LagLingo's approximate/VAD timing fallback.
   * *Implementation:* Native `aiohttp` WebSocket client following the current official transcription-session schema.

### Secondary / Excluded Providers

* **Google Cloud Speech-to-Text v2:** *Verdict: Defer / Secondary SDK-backed.* Pure gRPC bidirectional streaming (`StreamingRecognize`). Requires heavy `google-cloud-speech` SDK and gRPC C-core libraries. 305-second streaming limit requires complex stream stitching logic.
* **AWS Transcribe Streaming:** *Verdict: Defer / Secondary.* Requires AWS EventStream binary framing with AWS SigV4 signed headers per chunk, or HTTP/2 bidirectional streaming. Python SDK (`amazon-transcribe`) is dated and adds heavy `awscrt` C dependencies.
* **AssemblyAI Streaming (v3):** *Preliminary verdict superseded.* Current v3 documentation and current Universal model materials include multilingual transcription/code-switching. The authoritative implementation plan places it in the first native-WebSocket batch, gated by model-specific official capability presets and revision-event tests.

---

## 2. Detailed Provider Specifications

### 2.1 Deepgram Live STT (`deepgram-streaming`)

* **Protocol & Endpoint:** `wss://api.deepgram.com/v1/listen?model=nova-3&encoding=linear16&sample_rate=16000&channels=1&interim_results=true&smart_format=true&endpointing=300`
  * Primary Doc: [Deepgram Live Audio Streaming Reference](https://developers.deepgram.com/reference/speech-to-text/listen-streaming)
  * Low-Level Protocol: [Deepgram Websockets Protocol](https://developers.deepgram.com/docs/lower-level-websockets)
* **Authentication:** HTTP Header `Authorization: Token <API_KEY>` during WebSocket upgrade handshake.
* **Audio Framing & Codecs:** Raw binary WebSocket frames containing 16-bit linear PCM (or raw Opus). Preferred chunk size: 20ms–100ms (e.g. 640–3200 bytes for 16kHz mono).
* **Session Lifecycle & Keepalive:**
  * Client sends `{"type": "KeepAlive"}` if audio pauses for >8 seconds.
  * Flush/finalize on demand: `{"type": "Finalize"}`.
  * Orderly termination: `{"type": "CloseStream"}`.
* **Interim / Final / VAD Events:**
  * Server sends `{"type": "Results", "is_final": bool, "speech_final": bool, "channel": {"alternatives": [{"transcript": "...", "confidence": 0.98, "words": [...]}]}}`.
  * `is_final=false` maps directly to LagLingo `ASREvent(event_type="interim")`.
  * `is_final=true` (or `speech_final=true`) maps to `ASREvent(event_type="final")`.
  * Utterance boundary detection via `UtteranceEnd` events when `utterance_end_ms` is set.
* **Language Handling:**
  * Single language: `language=en`, `language=ja`, `language=zh`.
  * Multilingual / Code-switching: `language=multi` or `model=flux-general-multi` ([Deepgram Multilingual Code-Switching](https://developers.deepgram.com/docs/multilingual-code-switching)).
* **Timestamps:** Millisecond start and end times included on both alternative and word levels (`start`, `duration`).
* **Max Stream Duration:** Default session limit is up to 4 hours per connection before reconnect is required.
* **Diarization:** Supported via query parameter `diarize=true`. Word objects include `speaker: int`.
* **Licensing & Dependencies:** Zero SDK dependencies required; pure `aiohttp.ClientSession.ws_connect`.

### 2.2 Alibaba DashScope Realtime ASR (`dashscope-realtime-asr`)

* **Protocol & Endpoint:** `wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference` (or `ap-southeast-1` for Singapore).
  * Primary Doc: [Aliyun Fun-ASR Realtime WebSocket API](https://help.aliyun.com/zh/model-studio/fun-asr-realtime-websocket-api)
  * Server Events: [DashScope Server-Side Events](https://help.aliyun.com/en/model-studio/fun-asr-server-events)
* **Authentication:** Handshake header `Authorization: Bearer <API_KEY>`.
* **Audio Framing & Codecs:**
  * Control frames: JSON text.
  * Data frames: Raw binary audio (PCM 16kHz/8kHz or Opus).
* **Session Lifecycle:**
  1. Client connects and sends `run-task` action with `model: "fun-asr-realtime"` or `"qwen-audio-3.0-asr-flash-streaming"`.
  2. Server responds with `task-started`.
  3. Client continuously sends binary audio frames.
  4. Client sends `finish-task` JSON frame to finalize.
  5. Server responds with `task-finished`.
* **Interim / Final Events:**
  * Server pushes `result-generated` events containing sentence status: `sentence_end=false` (interim) and `sentence_end=true` (final).
  * Sentences include start/end millisecond timestamps and word-level timestamps.
  * Note: `qwen3-asr-flash-realtime` uses OpenAI-compatible Realtime buffer events (`input_audio_buffer.append`) but currently lacks granular timestamps; `fun-asr-realtime` and `qwen-audio-3.0-asr-flash-streaming` are preferred for live subtitles.
* **Language Handling:** Exceptional CJK accuracy, automatic Chinese/English/Cantonese/Japanese/Korean recognition.
* **Max Stream Duration:** Unlimited stream duration for real-time models with proper keepalive.

### 2.3 Speechmatics Realtime (`speechmatics-streaming`)

* **Protocol & Endpoint:** `wss://eu2.rt.speechmatics.com/v2` (or regional endpoints).
  * Primary Doc: [Speechmatics Realtime API Reference](https://docs.speechmatics.com/api-ref/realtime-transcription-websocket)
* **Authentication:** Handshake header `Authorization: Bearer <API_KEY>`.
* **Session Flow:**
  1. Client sends `StartRecognition` JSON payload with audio format (`audio_format: {"type": "raw", "encoding": "pcm_s16le", "sample_rate": 16000}`) and transcription config (`transcription_config: {"language": "en", "operating_point": "enhanced", "enable_partials": true}}`).
  2. Server responds with `RecognitionStarted`.
  3. Client sends binary audio frames or `AddAudio` frames.
  4. Server responds with `AddPartialTranscript` (interim) and `AddTranscript` (final).
  5. Termination: Client sends `EndOfStream`; server returns `EndOfTranscript`.
* **Diarization & Code-switching:** Real-time speaker diarization natively supported via `diarization_config: {"type": "speaker"}`. Outputs `speaker` tag with each segment.
* **Timestamps:** High-precision float seconds `start_time` and `end_time` on every word and transcript chunk.
* **Dependencies:** Zero external dependencies; implemented cleanly via `aiohttp`.

### 2.4 Azure AI Speech Service (`azure-speech-sdk`)

* **Protocol & Transport:** Proprietary WebSocket subprotocol with binary header framing (`x-timestamp`, `x-requestid`, `Content-Type: audio/x-wav`).
  * Primary Doc: [Azure Speech SDK Continuous Recognition](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-recognize-speech)
  * Push Stream API: [PushAudioInputStream Reference](https://learn.microsoft.com/en-us/python/api/azure-cognitiveservices-speech/azure.cognitiveservices.speech.audio.pushaudioinputstream)
* **Recommended Client:** Official Python SDK `azure-cognitiveservices-speech`. Direct WebSocket reverse engineering is discouraged because Microsoft frequently updates telemetry, auth token rotations, and header framing.
* **Audio Ingestion Pattern:**
  * Initialize `speechsdk.audio.PushAudioInputStream()`.
  * Connect stream to `speechsdk.audio.AudioConfig(stream=push_stream)`.
  * Feed incoming PCM chunks via `push_stream.write(chunk)`.
  * Attach callbacks: `recognizer.recognizing.connect(...)` (interim) and `recognizer.recognized.connect(...)` (final).
  * Call `recognizer.start_continuous_recognition_async()`.
* **Language Handling & Continuous LID:**
  * Supports `AutoDetectSourceLanguageConfig` with up to 10 candidate languages and `LanguageIdMode: Continuous`.
* **Limitations & Caveats:** SDK is a C++ wrapped binary wheel. Running blocking SDK callbacks inside an `asyncio` loop requires bridging callbacks into `asyncio.Queue` via `loop.call_soon_threadsafe`.

### 2.5 OpenAI Realtime Transcription (`openai-realtime-transcribe`)

* **Protocol & Endpoint:** OpenAI Realtime WebSocket using a transcription session (`session.type = "transcription"`); current official guidance starts with `gpt-live-transcribe`, while `gpt-transcribe` is used when detected-language output is required.
  * Primary Doc: [OpenAI Realtime Transcription Guide](https://platform.openai.com/docs/guides/realtime-transcription)
* **Authentication:** `Authorization: Bearer <API_KEY>` during the WebSocket connection; follow the current Realtime reference rather than retaining legacy beta headers without verification.
* **Audio Ingestion & Wire Semantics:**
  * Audio is transmitted as base64-encoded PCM16; the current official example uses 24 kHz in JSON events:
    `{"type": "input_audio_buffer.append", "audio": "<base64_data>"}`.
  * Server VAD: `session.turn_detection: {"type": "server_vad"}` emits `input_audio_buffer.speech_started` and `conversation.item.input_audio_transcription.completed`.
* **Pros & Cons:**
  * *Pros:* Direct access to state-of-the-art multilingual understanding; can combine transcription and instant translation in a single duplex session.
  * *Cons:* Base64 framing adds ~33% network overhead; cost ($0.06/min audio) is an order of magnitude higher than Deepgram ($0.0043/min) or DashScope; token rate limits restrict concurrency.

### 2.6 Google Cloud Speech-to-Text v2 (`google-cloud-speech`)

* **Protocol & Transport:** Bidirectional streaming RPC over gRPC (`google.cloud.speech.v2.SpeechClient.streaming_recognize`).
  * Primary Doc: [Google Cloud STT Streaming Guide](https://docs.cloud.google.com/speech-to-text/docs/v1/transcribe-streaming-audio)
* **Audio & Framing:** First message contains `StreamingRecognitionConfig`; subsequent messages contain `audio_content` byte chunks (max 15 KB per request).
* **Limitations:**
  * Streaming sessions are strictly capped at 305 seconds (~5 minutes). Applications must implement transparent channel rotation (opening a new gRPC stream before the 5-minute timeout and cross-fading audio chunks).
  * Pulls `grpcio`, `protobuf`, and `google-auth`, significantly increasing container/bundle footprint.

### 2.7 AWS Transcribe Streaming

* **Protocol & Transport:** HTTP/2 bidirectional streaming or WebSocket over port 8443 (`wss://transcribestreaming.<region>.amazonaws.com:8443/stream-transcription-websocket`).
  * Primary Doc: [Amazon Transcribe Streaming Guide](https://docs.aws.amazon.com/transcribe/latest/dg/streaming-setting-up.html)
* **Framing & Complexity:** Uses AWS EventStream binary encoding (headers, CRC32, payload) and SigV4 query string signing or per-frame chunk signing. The official Python SDK (`amazon-transcribe`) relies on AWS CRT (C Common Runtime) and has slower update cycles for multilingual identification features.

### 2.8 AssemblyAI Streaming (v3)

* **Protocol & Transport:** `wss://api.assemblyai.com/v2/realtime/ws`.
  * Primary Doc: [AssemblyAI Streaming Documentation](https://www.assemblyai.com/docs/speech-to-text/streaming)
* **Evaluation:** High quality for English podcast/conversation transcription with simple WebSocket framing, but lacks native automatic multi-language code-switching on live streams compared to Deepgram Nova-3 and Speechmatics.

---

## 3. Protocol Comparison Matrix

| Provider | Transport Protocol | Recommended Client | Audio Framing | Latency | Language & Code-Switching | Speaker Diarization | Max Stream Duration | Fit Score |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Deepgram** | WebSocket (WSS) | Native `aiohttp` | Raw Binary PCM16 | ~150–300ms | 30+ languages, `multi` code-switch | Yes (`diarize=true`) | 4 hours | **9.5 / 10** |
| **Aliyun DashScope** | WebSocket (WSS) | Native `aiohttp` | Raw Binary PCM/Opus | ~200–400ms | Top-tier CJK + auto multi | Sentence speaker IDs | Unlimited | **9.2 / 10** |
| **Speechmatics** | WebSocket (WSS) | Native `aiohttp` | Raw Binary PCM16 | ~200–350ms | 50+ languages, multi code-switch | Yes (real-time stream) | Utterance limits | **9.0 / 10** |
| **Azure AI Speech** | WSS (custom) | `azure-cognitiveservices-speech` | Stream Buffer | ~250–500ms | Continuous LID (10 langs) | Multi-channel / Conversation | Continuous | **8.5 / 10** |
| **OpenAI Realtime** | WebSocket (WSS) | Native `aiohttp` | JSON Base64 PCM16 | ~300–600ms | 50+ languages | No native live diarize | Session based | **7.5 / 10** |
| **Google Cloud STT** | gRPC (Bi-di) | `google-cloud-speech` | gRPC Byte chunks | ~200–400ms | Alternative language codes | Yes | 305 seconds (Hard cap) | **6.5 / 10** |
| **AWS Transcribe** | HTTP/2 or WSS | `amazon-transcribe` | AWS EventStream binary | ~300–600ms | Multi-language ID | Channel / Speaker | Session based | **6.0 / 10** |
| **AssemblyAI** | WebSocket (WSS) | Native `aiohttp` / SDK | Raw Binary PCM16 | ~300–500ms | Primarily English / single lang | Utterance end | Session based | **6.0 / 10** |

---

## 4. Architectural Evolution for LagLingo Provider Seam

### 4.1 Current Seam Inspection (`companion/providers/base.py`)
The current base classes define:
```python
class ASREvent:
    event_type: str  # "interim" | "final" | "error"
    text: str
    start_time: float | None
    end_time: float | None
    raw: dict[str, Any]

class ASRStream(ABC):
    async def feed_pcm(self, pcm_data: bytes) -> None: ...
    async def close(self) -> None: ...
    async def __aiter__(self) -> AsyncIterator[ASREvent]: ...
```

### 4.2 Proposed Contract Enhancements
To support rich live broadcast subtitling across the shortlisted providers without breaking existing callers, expand `ASREvent` and `ASRStream`:

1. **Speaker and Diarization Metadata on `ASREvent`:**
   ```python
   speaker_id: str | None = None
   confidence: float | None = None
   words: list[dict[str, Any]] | None = None  # [{word, start, end, confidence}]
   language: str | None = None                # Detected language code
   ```
2. **Audio Flushing & Keepalive:**
   Add explicit `flush()` or `keepalive()` methods to `ASRStream` so the companion audio pipeline can keep long-running streams warm during silent broadcast intermissions.
3. **Stream Health & Auto-Reconnection:**
   Wrap provider streams in a reconnecting proxy wrapper (`ReconnectingASRStream`) to handle transient WebSocket drops and the Google 305s limitation transparently.
4. **Native Protocol vs. SDK Adapters:**
   * **Native Protocol Kinds (`deepgram`, `dashscope-realtime`, `speechmatics`, `openai-realtime`):** Implement entirely using `aiohttp.ClientSession.ws_connect`. No extra pip dependencies; lightweight, memory-efficient, and easy to run in embedded companion environments.
   * **SDK-Backed Adapters (`azure-speech`):** Keep behind optional extras (e.g. `pip install laglingo[azure]`) with worker thread event loop synchronization.

---

## 5. Primary References and Citations

1. **Deepgram Live Streaming WebSocket API:**  
   https://developers.deepgram.com/reference/speech-to-text/listen-streaming
2. **Deepgram Low-Level WebSocket Implementation & Framing:**  
   https://developers.deepgram.com/docs/lower-level-websockets
3. **Deepgram Multilingual Code-Switching:**  
   https://developers.deepgram.com/docs/multilingual-code-switching
4. **Alibaba Cloud Model Studio Fun-ASR / Qwen Realtime WebSocket API:**  
   https://help.aliyun.com/zh/model-studio/fun-asr-realtime-websocket-api
5. **Alibaba Cloud Model Studio Server-Side Realtime Events:**  
   https://help.aliyun.com/en/model-studio/fun-asr-server-events
6. **Speechmatics Realtime WebSocket API Reference:**  
   https://docs.speechmatics.com/api-ref/realtime-transcription-websocket
7. **Microsoft Azure Speech SDK Push Audio Stream (Python):**  
   https://learn.microsoft.com/en-us/python/api/azure-cognitiveservices-speech/azure.cognitiveservices.speech.audio.pushaudioinputstream
8. **Microsoft Azure Continuous Language Identification:**  
   https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-identification
9. **OpenAI Realtime Audio Transcription Guide:**  
   https://platform.openai.com/docs/guides/realtime-transcription
10. **Google Cloud Speech-to-Text Streaming gRPC API:**  
    https://docs.cloud.google.com/speech-to-text/docs/v1/transcribe-streaming-audio
11. **AWS Transcribe Streaming WebSocket & HTTP/2 Specification:**  
    https://docs.aws.amazon.com/transcribe/latest/APIReference/API_streaming_StartStreamTranscription.html
12. **AWS Transcribe Streaming Async Python SDK Repository:**  
    https://github.com/awslabs/amazon-transcribe-streaming-sdk
13. **AssemblyAI Real-Time Streaming Speech-to-Text Guide:**  
    https://www.assemblyai.com/docs/speech-to-text/streaming
