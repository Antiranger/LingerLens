# LagLingo Language and Provider Context

LagLingo turns live-stream audio into time-aligned source and translated subtitles. Its language coverage is determined by the configured ASR and translation Providers, while LagLingo owns language identity, user intent, capability validation, and subtitle presentation.

## Language

**Language Tag**:
A canonical BCP 47 identifier for a language, script, and optional regional variety, such as `ja`, `zh-Hant`, or `pt-BR`.
_Avoid_: Language code, locale when referring only to subtitle language

**Source Language Policy**:
The user's instruction for recognizing spoken audio: either a specified Language Tag or a detection policy with optional candidates, preference, and code-switching intent.
_Avoid_: Auto language, source locale

**Detected Language**:
A canonical Language Tag reported or inferred for recognized speech by the active ASR Provider. It may apply to one cue rather than the entire live stream.
_Avoid_: Selected language

**Target Language**:
The canonical Language Tag explicitly chosen by the user for translated subtitles.
_Avoid_: Output locale, UI language

**UI Locale**:
The language and regional conventions used by LagLingo's controls, labels, and messages. It is independent of Source Language Policy and Target Language.
_Avoid_: Target language

## Providers

**Provider Profile**:
A persisted configuration selecting one external or local model, its protocol Adapter, credentials, options, pricing, and model capability preset.
_Avoid_: Model when referring to the saved connection as a whole

**Language Capability**:
A Provider Profile's declared ability to accept specified languages, detect from candidates, report detected languages, handle code-switching, or translate a language pair.
_Avoid_: Supported languages when the detection or pair semantics are unspecified

**Support Tier**:
The evidence level for a language or language pair: LagLingo-verified, Provider-claimed, or experimental best-effort.
_Avoid_: Supported as an unqualified binary claim

**Adapter**:
The Provider-specific implementation that maps LagLingo's PCM, language policies, events, translation instructions, usage, and errors to one external protocol or official SDK.
_Avoid_: Universal Provider, protocol shim, platform chat connector

## Live Timeline

**Media Wall Clock**:
The single epoch-seconds timeline carried by LagLingo's local HLS `PROGRAM-DATE-TIME`. Video frames, Subtitle Cues, and Live Messages are aligned on this clock and formatted in the viewer's local time zone for display.
_Avoid_: System clock when referring to media position, video currentTime

**Playback Wall Time**:
The Media Wall Clock time of the frame currently shown by the player.
_Avoid_: Current time, live time

**Capture Media Cursor**:
The latest media position acquired by the Companion, expressed on the Media Wall Clock and used to timestamp newly received Live Messages.
_Avoid_: Receive time, server now

**ASR Utterance**:
A Provider-detected span of speech that may contain one or many caption-sized units and ends only when the Provider reports an endpoint or finalization.
_Avoid_: Sentence, Subtitle Cue

**Stable Token**:
Provider-confirmed recognition text with its original audio timestamps. Evidence may be a lexical word or an explicitly marked raw tokenizer piece; stability does not imply a safe caption boundary. The shared Caption Chunker evaluates linguistic boundaries across available pieces without inventing internal timestamps.
_Avoid_: Interim word, guaranteed sentence boundary, policy-committed token, final sentence

**Policy-Committed Token**:
A lexical token from mutable ASR output that LagLingo irrevocably accepts after its configured agreement rule. It is stable for LagLingo publication but may still differ from a later Provider final and therefore carries lower evidence confidence than a Stable Token.
_Avoid_: Provider-final token, guaranteed-correct token

**Caption Chunk**:
A sequence of accepted recognition evidence submitted at a supported sentence/clause boundary and scheduled as one Subtitle Cue. An unfinished expression may span nearby ASR Utterances from a compatible speaker, but the speaker label never grants indefinite continuity. Finalized residuals have a bounded continuation deadline measured in caller-supplied monotonic time; deadline expiry preserves the residual at its original audio range and does not claim a complete clause. Different speakers retain independent, possibly overlapping ranges. Six seconds is a soft reference, not a hard cap. Open recognition is not forcibly cut by advancing input audio.
_Avoid_: ASR Utterance, visual line wrap

**Subtitle Cue**:
A scheduled Caption Chunk with source text, optional Target Language translation, and a readiness state.
_Avoid_: ASR Utterance, visual line wrap

**Translation Continuity Context**:
Preceding Caption Chunk source text with optional completed translations, supplied immediately when a translation starts. A pending translation never blocks the next request: its source text remains usable context. Each request translates only its current chunk; context excludes current/future items and other generations.
_Avoid_: Unordered completion history, transcript backlog

**Live Message**:
A read-only text event received during an ongoing YouTube or Bilibili live stream and mapped to the Media Wall Clock.
_Avoid_: Comment when referring to recorded-video comments, archived chat

**Live Message Source**:
The platform protocol boundary that connects to one ongoing live stream and emits normalized Live Messages. It is separate from model Provider Adapters.
_Avoid_: Adapter, Provider, danmaku API

**Follow Mode**:
A timeline-list state that keeps the item corresponding to Playback Wall Time visible until the viewer manually scrolls away; the viewer explicitly returns to resume following.
_Avoid_: Auto-scroll when the paused state matters
