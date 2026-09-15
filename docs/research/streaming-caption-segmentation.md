# Low-latency streaming ASR caption segmentation for long run-on speech

> **Scope:** research only. This note reviews primary papers, standards, official vendor documentation, and source code from established open-source projects. It does not propose product-code changes in this task.
>
> **LingerLens context:** Soniox `stt-rt-v5` emits token-level `is_final`, `start_ms`, and `end_ms`; playback trails live media by a target **15 seconds**. A live Twitch diagnosis observed utterances up to **33.9 s**, translation-ready lag of **3.873 s p50 / 9.76 s p95**, and punctuation-only prefix splitting that still left many overlong cues. Those measurements are repository evidence, not external claims.
>
> **Security note:** external pages and repositories were treated as untrusted data. No instructions from external content were executed.

## Executive recommendations

1. **Separate ASR endpointing from caption chunking.** Soniox semantic endpointing should continue to identify natural utterance/turn ends, but it cannot guarantee a caption deadline during 20–35 s uninterrupted speech. A second caption chunker must operate on Soniox's immutable final-token prefix without ending the ASR turn.
2. **Commit only Soniox final lexical tokens.** Append tokens with `is_final=true` once; replace the current non-final tail wholesale on each response. Never translate or permanently schedule non-final tokens. Soniox explicitly guarantees that final tokens never change.[S1][S2]
3. **Make the hard chunk span budget-driven.** For a cue starting at source time `s`, ending at `e`, and becoming displayable `R` seconds after `e`, true-start alignment under playback delay `D` requires `(e-s) + R <= D`. Use:

   ```text
   hard_span = min(6.0 s, max(4.0 s, D - rolling_p95(post_cut_ready_lag) - 1.0 s safety))
   ```

   With LingerLens's observed `D=15` and `p95=9.76`, the defensible initial hard span is **about 4.0 s**. If post-cut p95 falls to 8 s or below, the cap may relax toward **6.0 s**, which also matches DCMP's maximum caption duration.[S23]
4. **Use a hierarchy, not one trigger:** final strong punctuation or a clear word gap first; weak punctuation/gap next; then the best stable word boundary before the time/size deadline; Soniox `finalize` only when the stable frontier itself cannot reach the deadline.
5. **Cut on word timestamps.** The source interval is the first committed lexical token's `start_ms` through the last one's `end_ms`. Never derive speech time from Soniox `<end>`/`<fin>` control-token timestamps. Small inter-cue gaps can be hidden by the renderer; timestamps should remain lexical and truthful.[S3]
6. **Use punctuation as evidence, not as the only gate.** Streaming punctuation models intentionally retain a mutable tail. A punctuation mark is safe for commitment only when it is itself in the stable/final prefix. At the hard time limit, punctuation must yield to a word-boundary cut.[S19]
7. **Translate committed chunks incrementally and speculatively, but expose immutable captions.** Start hidden re-translation as the stable source prefix grows; on chunk close, reuse only an exact-prefix result or issue one final translation. Do not visibly revise a caption after it is scheduled. If visible partial translation is later desired, use LocalAgreement-2 or a zero/small revision window and measure normalized erasure.[S13][S20][S21]
8. **Treat forced finalization as a last-resort safety valve.** Soniox says finalizing every few seconds is acceptable but warns that doing it too often may disconnect.[S4] Prefer a local caption cut over already-final tokens; force Soniox only when final-token progress stalls beyond the hard budget.
9. **If `p95(post_cut_ready_lag) > D - 4 s`, segmentation alone cannot guarantee translated true-start captions.** The system must then reduce downstream latency, show source-only at the deadline, or increase playback delay.

## 1. The problem is three different boundary decisions

### 1.1 Acoustic VAD boundary

A VAD answers whether speech is present. Traditional endpointing waits for trailing non-speech. Deepgram's Nova endpointing documentation is explicit: its `endpointing` control is VAD-based and waits a configured number of milliseconds of silence before returning `speech_final=true`.[S8] Silero VAD likewise exposes a speech-probability threshold, minimum silence, and optional maximum speech duration.[S12]

This is robust and inexpensive, but it fails the run-on case by design: continuous voiced speech contains no qualifying silence. It can also split hesitations that are acoustically quiet but semantically incomplete.

### 1.2 Semantic utterance/turn endpoint

A semantic endpointer asks whether the speaker appears to have completed a thought. Soniox describes its endpointing as using pauses, intonation, speech patterns, and conversational context, with a hard maximum endpoint delay after speech ends.[S5] AssemblyAI's current streaming turn detector similarly performs a completion check after `min_turn_silence` and force-ends after `max_turn_silence`.[S10] Deepgram Flux combines model end-of-turn confidence (`eot_threshold`, default `0.7`) with a maximum-silence fallback (`eot_timeout_ms`, default `5000`).[S9]

Research systems follow the same direction:

- **Semantic VAD** adds frame-level terminal/non-terminal punctuation prediction and an endpoint class. Its illustrative inference policy uses roughly **300 ms** tail silence after terminal punctuation, **400 ms** after non-terminal punctuation, and **700 ms** when punctuation is unavailable; the paper reports average tail latency dropping from 700 ms to about 327 ms on its internal data.[S15]
- Google's long-form ASR work distills punctuation-derived semantic boundaries from a bidirectional text LM into a streaming RNN-T EOS head, reporting a 3.2% relative WER gain and 60 ms lower median EOS latency than a pause-teacher baseline on a YouTube captioning test.[S16]
- Amazon's two-pass endpoint detector lets a streaming first pass propose an endpoint, then gates it with an arbitrator using acoustic and recognition/text representations; unconditional maximum-pause guardrails bypass the arbitrator.[S17]

Semantic endpointing improves natural turn boundaries, but it is still a **turn detector**, not a guarantee that text will be emitted every four to six seconds during uninterrupted speech.

### 1.3 Caption block boundary

A caption boundary exists to meet a display/readiness contract. It may legitimately occur inside one spoken turn. Standards and research distinguish subtitle blocks from linguistic sentences:

- DCMP recommends no more than two lines and a maximum caption duration of **6 seconds**.[S23]
- BBC recommends no more than two lines for landscape/square video and emphasizes breaks that preserve phrases and grammatical units; its legacy Teletext limit is 37 fixed-width characters per line, while proportional-font systems should measure rendered width rather than rely only on character counts.[S24]
- Subtitle-segmentation research commonly models separate end-of-line and end-of-block decisions, combining formal length constraints with syntactic/semantic boundaries, pauses, and other multimodal evidence.[S27][S28][S29]

**Consequence for LingerLens:** an ASR `<end>` is one high-quality caption candidate, not the only event allowed to create a cue.

## 2. Industrial patterns worth carrying into LingerLens

### 2.1 Stable prefix plus replaceable tail

Soniox has the most useful contract for this case. Its official documentation and SDK examples define:

```text
committed_prefix += tokens where is_final == true   # each returned once
mutable_tail      = tokens where is_final == false  # replace on every response
rendered_text     = committed_prefix + mutable_tail
```

A final token is confirmed and will never change; a non-final token can change, disappear, or be replaced.[S1][S2] AWS offers a similar but word-level `Stable` flag: with partial-result stabilization enabled, only the last few words remain mutable, with `high` stability returning faster at a possible accuracy cost.[S6] Google exposes a result-level `stability` score from 0 to 1 and notes that a UI may show only high-stability results; its transcript normalization is applied only above stability `0.8` or on finals.[S7]

When a provider does not label stable tokens, **LocalAgreement-2** is the established fallback: decode successive input updates and commit their longest common prefix. Whisper-Streaming uses this policy, plus word timestamps and bounded buffer trimming, and reports 3.3 s average latency on its long-form test setup.[S13][S14]

For LingerLens, provider-labeled finality is stronger than inferred LocalAgreement. LocalAgreement remains relevant for incremental **translation**, not for re-proving Soniox token stability.

### 2.2 Periodic partials prevent bursty UX, but do not imply commitment

AssemblyAI retranscribes the whole current turn for each partial and says each new `Turn` message supersedes the prior one. It also emits continuous partials roughly every **3 seconds** during uninterrupted speech.[S10] This is useful for live feedback, but the replacement semantics are unsuitable for immutable scheduling unless a committed prefix is extracted first.

LingerLens's 15-second hidden playback window reduces the need to show unstable text. The better policy is to use the window for speculative work, then expose only immutable source/translation cues.

### 2.3 Progressive endpoint rules plus a deterministic cap

Kaldi's mature online endpoint implementation is a useful non-neural reference. Its default five rules endpoint on:[S11]

- 5.0 s silence even when no speech was decoded;
- 0.5 s trailing silence when final-state relative cost is strong (`<2`);
- 1.0 s when relative cost is acceptable (`<8`);
- 2.0 s regardless of final-state evidence after speech;
- 20 s total utterance length regardless of all other evidence.

The general pattern is more important than the historical values: **strong decoder/semantic evidence permits a short wait; weaker evidence needs more silence; a hard duration rule prevents unbounded latency.** For captions, the hard rule must be much shorter than Kaldi's 20 s because the display budget is 15 s.

## 3. Recommended LingerLens algorithm

### 3.1 Required stream state

Maintain an ordered ledger of **lexical Soniox tokens**, separate from control tokens:

```text
emitted_tokens       immutable lexical tokens already assigned to caption chunks
pending_final_tokens immutable lexical tokens not yet assigned; append once
latest_nonfinal      replaceable tail from the latest response only
chunk_start_ms       start_ms of the first pending final token
latest_audio_ms      total_audio_proc_ms or local PCM media cursor
```

Ignore `<end>` and `<fin>` as text. They close the ASR utterance but are not lexical timing anchors. The current LingerLens adapter already advertises `stable_prefix=true`, but its normalized event exposes accumulated text and utterance-level begin/end rather than the individual final tokens. A timestamp-aware caption chunker therefore needs access to the token ledger (directly or through normalized committed-token events); splitting a concatenated string cannot recover exact word boundaries.

### 3.2 Budget controller

Define:

```text
D              actual measured playback delay (start with target 15.0 s)
S              safety margin (start 1.0 s)
R95            rolling p95 from selected lexical boundary to cue state done/failed
age            latest_audio_time - chunk_start_time
budget_trigger age + R95 + S >= D
```

Bootstrap `R95` from the live diagnosis (**9.76 s**) until at least 30 locally chunked samples exist. Thereafter maintain at least two metrics:

- `stable_boundary_lag = chunk_commit_wall - boundary_audio_wall`;
- `translation_lag = cue_ready_wall - chunk_commit_wall`;
- `post_boundary_ready_lag = their sum`.

Use `post_boundary_ready_lag` for the service-level guarantee. Use `translation_lag` for making a decision at the current moment. Report p50/p95 and timeout/failure samples, not just successful translation latency.

Initial limits for the observed system:

| Control | Initial value | Rationale |
|---|---:|---|
| Playback budget `D` | 15.0 s actual/target | Product constraint |
| Safety `S` | 1.0 s | Leaves scheduler/network jitter margin |
| Preferred span | 3.0 s | Gives natural candidates before the hard point; similar cadence to industrial continuous partials[S10] |
| Hard span | 4.0 s | `15 - 9.76 - 1 ≈ 4.24`; round down conservatively |
| Relaxed hard span | up to 6.0 s | Only when rolling `R95 <= 8.0 s`; also matches DCMP max duration[S23] |
| Minimum span | 1.2–1.5 s | Avoid unreadable flashes; DCMP minimum is 40 frames (about 1.33 s at 30 fps)[S23] |
| Candidate lookback at hard cut | 1.25 s | Enough to prefer a nearby pause/punctuation without adding unbounded delay |
| Soft word cap | 12 words | At BBC's 160–180 live wpm, 4 s carries about 11–12 words[S24] |
| Hard word cap | 16 words | Safety ceiling for unusually fast speech |
| Lines | 2 | BBC/DCMP recommendation[S23][S24] |
| Fallback width | 36–37 Latin characters per line | Conservative legacy compatibility; measure rendered width for proportional fonts[S24] |
| CJK fallback | 20 grapheme clusters per line, 40 per block | LingerLens starting heuristic, not a cited universal standard; validate against actual font/viewport |

The time budget is authoritative. Word/width caps may cut earlier, but must never defer a cut beyond the hard time trigger.

### 3.3 Boundary hierarchy

Only positions after a **final lexical token** are eligible. Apply these rules in order:

1. **Immediate natural close:** after minimum span, close on final terminal punctuation (`. ! ? … 。！？`) or a confirmed speaker change.
2. **Preferred-span close:** at/after 3.0 s, close on the latest final weak punctuation (`; : , — 、；：，`) or a lexical inter-word gap of at least **300 ms**.
3. **Size close:** when the soft word or rendered-width cap is reached, choose the best eligible boundary in the preceding **1.0 s**; prefer terminal punctuation, speaker change, ≥300 ms gap, weak punctuation, then ≥150 ms gap.
4. **Budget/hard close:** at the budget trigger or hard span, choose the latest eligible boundary within the preceding **1.25 s** using the same priority. If none exists, cut after the latest final lexical token at or before the hard point.
5. **Overshoot accounting:** if Soniox delivers final tokens in a batch whose timestamps cross the hard point, cut at the last token ending before the hard point. If no such token exists, cut after the first final token and record `hard_cap_overshoot_ms`.

The 150/300 ms gap values are engineering starting points, informed by the 300/400/700 ms tiering used in Semantic VAD rather than universal constants.[S15] They should be tuned from boundary-quality and latency measurements, not treated as language-independent truth.

This hierarchy is deliberately deterministic. A learned segmenter can later replace the candidate priority: direct streaming-ST segmentation research predicts split/non-split decisions per ASR word from bounded textual history/future plus aligned acoustic features.[S31] The deadline and fallback rules should remain outside that model.

### 3.4 Pseudocode

```python
def on_soniox_result(result):
    append_once(pending_final_tokens, lexical_final_tokens(result))
    latest_nonfinal = lexical_nonfinal_tokens(result)  # replace, do not append

    while pending_final_tokens:
        start = pending_final_tokens[0].start_ms
        stable_end = pending_final_tokens[-1].end_ms
        age = latest_audio_ms - start
        hard_ms = adaptive_hard_span_ms()       # initially 4000
        deadline_due = age + p95_translation_ms + safety_ms >= actual_delay_ms

        boundary = None
        if terminal_or_speaker_boundary_after_minimum():
            boundary = that_boundary
        elif age >= preferred_span_ms:
            boundary = latest_weak_or_300ms_gap_boundary()
        if soft_size_cap_reached():
            boundary = best_boundary_in_lookback(1000) or latest_final_boundary()
        if deadline_due or age >= hard_ms:
            boundary = best_boundary_in_lookback(1250) or latest_final_at_or_before(hard_ms)

        if boundary is None:
            break

        chunk = pop_through(boundary)
        commit_caption_chunk(chunk)  # immutable source text and lexical timestamps
        start_hidden_or_final_translation(chunk)
```

`actual_delay_ms` should be the measured media delay, not merely the configured value. If actual delay temporarily drops, the controller must become more aggressive.

### 3.5 Soniox endpoint controls are quality controls, not the chunk deadline

LingerLens's built-in Soniox profile currently uses `maxEndpointDelayMs=700` and `endpointSensitivity=0.3`. The 700 ms setting is close to Soniox's allowed minimum of 500 ms and limits delay **after an actual end of speech**; it does not cut a continuously spoken 20–35 s turn.[S5]

Recommended experiment order:

1. Keep endpoint detection enabled and keep sensitivity near **0.3** while introducing local final-token chunking.
2. Do not lower `maxEndpointDelayMs` further to solve run-ons. Compare **700 ms versus 1000–1500 ms** on false splits and endpoint lag; Soniox's own responsive example uses 1500 ms, sensitivity 0.3, and latency adjustment level 2.[S5]
3. Prefer the setting with fewer semantically bad `<end>` splits, because the local caption chunker already enforces the display deadline. Endpoint latency can use the remaining playback budget.
4. Treat `endpointLatencyAdjustmentLevel` as a measured tuning dimension, starting at **1**, then trying **2** if endpoint lag remains material. Higher/aggressive settings may reduce recognition accuracy.[S5]

This separates two controls that otherwise fight each other: Soniox optimizes natural utterance boundaries; LingerLens optimizes caption readiness.

## 4. Punctuation restoration and semantic segmentation

### 4.1 What punctuation can safely do

Final Soniox punctuation tokens are immutable under the same `is_final` contract as other tokens, so they are strong caption boundary evidence.[S1] A separate punctuation model is useful only when the provider omits or delays punctuation, or for final display cleanup.

CT-Transformer was designed to freeze partial punctuation output with controllable delay.[S19] FunASR's online implementation carries cache state across chunks and retains a tail until it finds a period/question mark; under a long cache it can promote a comma to a sentence end to bound state.[S19] This supports two rules:

- carry punctuation-model state across caption chunks;
- do not use punctuation from the mutable tail as an irreversible boundary.

A final/offline punctuation pass may revise punctuation **inside a not-yet-displayed cue**, but must not move its lexical timestamps or join it with an adjacent scheduled cue. In LingerLens's delayed pipeline, punctuation cleanup has until the cue's playback deadline; after publication, text stability takes priority over cosmetic correction.

### 4.2 Semantic score under a hard constraint

Research subtitle segmenters show that pure character counting is fast but linguistically poor. Better approaches predict line/block boundaries or use punctuation likelihood from a masked LM while enforcing a maximum length and two-line layout.[S27][S28] For live operation, run such semantic scoring only over the bounded set of already-final candidate boundaries. It may choose *which* final word to cut after; it may not wait past the deadline.

## 5. Incremental translation and revision policy

### 5.1 Recommended default: hidden speculation, immutable publication

For each open caption chunk:

1. Feed previous committed source/translation pairs as rolling context.
2. Optionally start a hidden translation when either (a) at least four new final lexical tokens arrived, (b) a candidate terminal boundary appeared, or (c) 1.0–1.5 s elapsed since the last speculative request.
3. Tag every request with the exact source-token range/hash.
4. If the chunk closes on the exact same token range, reuse the completed speculative result. Otherwise discard it and translate the committed chunk once.
5. Publish only the committed source and its final translation. Do not let a later context update rewrite an already scheduled cue.

This converts the 15-second playback delay into computation time without exposing flicker. It also prevents an out-of-order speculative response from overwriting a newer chunk.

### 5.2 If visible partial translation is required later

Re-translation can match or outperform specialized streaming MT even with few revisions, but it costs more computation and can visibly flicker.[S20] Use one of these stability controls:

- **LocalAgreement-2:** display only the longest common prefix of two successive translations; keep the remainder hidden.[S13][S22]
- **Hold-n:** suppress the last 2–3 target tokens from each update; this is simpler but language-pair dependent.[S22]
- **Revision window:** allow changes only in the last `RW` displayed target tokens. Revision-controllable beam search reports that `RW=0` can eliminate flicker with a minor quality loss, while `RW=3` approached unconstrained beam-search quality in its experiments.[S21]
- **Biased re-translation:** bias decoding toward the previous output and add a wait policy. Evaluate with normalized erasure (NE), the average number of intermediate target tokens deleted per final target token.[S20]

For LingerLens's ordinary captions, use **revision window 0 after cue publication**. Before publication, hidden output may revise freely. If a mutable preview is ever shown, start with `RW=3`, update at no more than 1 Hz, visually distinguish the mutable suffix, and set an acceptance target such as `NE <= 0.2`; that NE threshold is a product target, not a literature standard.

### 5.3 Chunk context versus chunk independence

A forced source chunk may end mid-clause, especially for Japanese-to-Chinese/English translation where reordering is common. Preserve quality by passing:

- the last 1–2 committed source chunks;
- their final translations;
- an explicit instruction that the current input may continue a sentence and the translator must output only the current chunk's content.

Do not concatenate the prior translation into the new cue. Context is for disambiguation and consistency, not display duplication.

## 6. Forced endpoint tradeoffs

There are three materially different operations:

| Operation | ASR context | Accuracy risk | Deadline effect | Recommendation |
|---|---|---|---|---|
| **Local cut on final tokens** | Preserved | Lowest; no token revision | Immediate chunk for translation | Default hard-cap mechanism |
| **Soniox `finalize`** | Session stays open; pending tokens become final | Higher if fired during speech; Soniox recommends about 200 ms silence after speech and warns against excessive frequency[S4] | Flushes a stalled non-final tail | Last resort |
| **Close/reconnect ASR** | Lost/reset | Highest; may lose in-flight audio, language/speaker/context continuity | Forces a boundary but adds reconnect delay | Do not use for caption pacing |

A local cut can reduce translation quality because the chunk may be semantically incomplete, but rolling context contains that damage. Provider finalization can additionally harm recognition by freezing an uncertain word and, if repeated too frequently, can destabilize the connection.

Concrete fallback policy:

```text
normal: never call finalize merely because 4 s elapsed; first cut available final tokens locally
consider finalize only when:
  open chunk age >= hard_span + 0.75 s
  AND no new final lexical token for >= 0.75 s
  AND uncommitted stable text is insufficient to make a useful chunk
  AND last finalize was >= 4 s ago
```

If any silence of about **200 ms** is observed, prefer firing at that point, matching Soniox's manual-finalization guidance.[S4] Record forced-final rate, subsequent low-confidence tokens, reconnects, and word-boundary errors. If forced finalization exceeds roughly one event per 20 seconds in sustained speech, the solution is fighting the provider rather than segmenting captions; this rate is a proposed operational alarm, not a Soniox limit.

## 7. Word-timestamp scheduling details

For committed tokens `t[a:b]`:

```text
source_start = first lexical token.start_ms
source_end   = last lexical token.end_ms
cue duration = source_end - source_start
```

Soniox timestamps are included by default for every recognized word/subword.[S3] Preserve these invariants:

- merge subword tokens into display words, but keep the first subtoken start and last subtoken end;
- attach punctuation to the preceding/following display word without replacing lexical end time with a control token time;
- do not interpolate multiple scheduled cue times from character counts;
- if the renderer needs two lines, line-wrap inside the same timed cue;
- if two chunks have a tiny gap, leave timestamps truthful and let the display scheduler avoid a visible blank under about 200–300 ms;
- if timestamps overlap slightly, clamp the later display start for rendering only and retain raw times for diagnostics.

OpenAI Whisper's official subtitle writer demonstrates the same separation: word timestamps determine cue start/end, while line width/count and words-per-line determine wrapping; a long word gap or line-count limit creates a subtitle break.[S30]

## 8. Revision and stability telemetry

Track at least:

- final-token lag: token `is_final` arrival wall time minus `end_ms` audio wall time;
- stable frontier age and maximum non-final-tail duration;
- chunk span p50/p95/max;
- cut reason (`endpoint`, `terminal_punct`, `speaker`, `gap`, `size`, `budget`, `hard_word`, `forced_finalize`);
- hard-cap overshoot p95/max;
- translation commit-to-ready and boundary-to-ready p50/p95, including failures/timeouts;
- percent ready by true-start playback deadline;
- forced-final count/rate, reconnects after force, and final-token confidence near forced cuts;
- visible revision count and normalized erasure if mutable previews are enabled;
- rendered lines, measured pixel width, words, characters/graphemes, and display duration.

The primary acceptance metric should be:

```text
P(cue state is done|failed before playback reaches cue.source_start)
```

Target **>=99%** after excluding ASR/translation service outages, and report the outage-inclusive value separately. The 99% target is a proposed SLO, not a standard.

## 9. What not to do

- Do not wait only for Soniox `<end>` during 20–35 s run-on speech.
- Do not treat semantic endpoint-delay tuning as a maximum utterance-duration control.
- Do not permanently emit Soniox non-final text because it contains attractive punctuation.
- Do not split text by characters and fabricate sub-cue timestamps.
- Do not send Soniox `finalize` every 3–4 seconds as the normal path.
- Do not measure translation latency only on successful requests.
- Do not expose unrestricted re-translation revisions in the caption region.
- Do not use one universal Unicode character count for Latin and CJK proportional fonts; rendered width and grapheme count are safer.

## 10. Sources

All links below are primary/first-party unless marked otherwise.

### Official vendor documentation and SDK source

- **[S1] Soniox, Real-time transcription.** Final tokens never change; non-final tokens are provisional and replaced as more audio arrives. <https://soniox.com/docs/stt/rt/real-time-transcription>
- **[S2] Soniox Python SDK README and official example.** Append final tokens once; clear/replace non-final tokens per event. <https://github.com/soniox/soniox-python/blob/main/README.md> and <https://github.com/soniox/soniox-python/blob/main/examples/soniox_client/realtime_example.py>
- **[S3] Soniox token/timestamp API types.** Token-level `start_ms`, `end_ms`, `is_final`; timestamps are relative to audio start and included by default. <https://raw.githubusercontent.com/soniox/soniox-python/main/src/soniox/types/common.py>, <https://raw.githubusercontent.com/soniox/soniox-python/main/src/soniox/types/realtime.py>, and <https://soniox.com/docs/stt/concepts/timestamps>
- **[S4] Soniox, Manual finalization.** Pending audio/tokens are finalized while the stream stays open; wait about 200 ms silence after speech when possible; every few seconds is acceptable, but too-frequent finalization may disconnect. <https://soniox.com/docs/stt/rt/manual-finalization>
- **[S5] Soniox, Endpoint detection.** Semantic endpointing and controls: `max_endpoint_delay_ms` 500–3000 (default 2000), sensitivity -1 to 1, latency adjustment 0–3. <https://soniox.com/docs/stt/rt/endpoint-detection> and official SDK type validation at <https://raw.githubusercontent.com/soniox/soniox-python/main/src/soniox/types/realtime.py>
- **[S6] AWS Transcribe, Streaming and partial results.** Natural speech segments, word/punctuation `Stable`, and low/medium/high stability tradeoff. <https://docs.aws.amazon.com/transcribe/latest/dg/streaming-partial-results.html>
- **[S7] Google Cloud Speech-to-Text v2 RPC reference.** Interim stability, immutable `is_final`, word offsets, and normalization only for stability >0.8/finals. <https://docs.cloud.google.com/speech-to-text/docs/reference/rpc/google.cloud.speech.v2>
- **[S8] Deepgram, Endpointing (Nova).** VAD/silence-based endpointing and `speech_final`; sample uses 300 ms endpointing. <https://developers.deepgram.com/docs/endpointing>
- **[S9] Deepgram, Flux quickstart.** Model-integrated EOT, `eot_threshold` default 0.7, `eot_timeout_ms` default 5000, eager endpoint tradeoffs. <https://developers.deepgram.com/docs/flux/quickstart>
- **[S10] AssemblyAI, Turn Detection.** Semantic completion plus min/max silence presets, superseding whole-turn partials, and continuous partials about every 3 s. <https://www.assemblyai.com/docs/streaming/turn-detection>
- **[S32] Speechmatics Realtime output/API.** Partials typically under 500 ms; immutable finals with configurable `max_delay` 0.7–4 s; explicit `ForceEndOfUtterance`. <https://docs.speechmatics.com/speech-to-text/realtime/output> and <https://docs.speechmatics.com/api-ref/realtime-transcription-websocket>

### Established open-source implementations

- **[S11] Kaldi online endpoint source.** Five-rule endpoint defaults combining trailing silence, decoder relative cost, non-silence, and a 20 s hard cap. <https://github.com/kaldi-asr/kaldi/blob/master/src/online2/online-endpoint.h> and <https://github.com/kaldi-asr/kaldi/blob/master/src/online2/online-endpoint.cc>
- **[S12] Silero VAD source.** Threshold 0.5, min silence 100 ms, min speech 250 ms, optional maximum speech duration; prefer a nearby silence before aggressive cutting. <https://github.com/snakers4/silero-vad/blob/master/src/silero_vad/utils_vad.py>
- **[S13] UFAL Whisper-Streaming source.** LocalAgreement committed-prefix buffer, word timestamps, 1 s overlap tolerance, 1–5 word de-duplication, 15 s default buffer trim. <https://github.com/ufal/whisper_streaming/blob/main/whisper_online.py>
- **[S14] UFAL Whisper-Streaming README.** LocalAgreement-2, recommended segment trimming, and reported 3.3 s long-form latency. <https://github.com/ufal/whisper_streaming/blob/main/README.md>
- **[S19] FunASR CT-Transformer source.** Online punctuation cache/tail behavior and bounded cache fallback. <https://github.com/modelscope/FunASR/blob/main/funasr/models/ct_transformer/model.py>
- **[S30] OpenAI Whisper subtitle writer source.** Word-timestamp cue boundaries, max width/count/words, and >3 s gap break. <https://raw.githubusercontent.com/openai/whisper/main/whisper/utils.py>

### Papers

- **[S15] Shi et al., “Semantic VAD: Low-Latency Voice Activity Detection for Speech Interaction,” Interspeech 2023.** <https://www.isca-archive.org/interspeech_2023/shi23c_interspeech.pdf>
- **[S16] Huang et al., “Semantic Segmentation with Bidirectional Language Models Improves Long-form ASR,” Interspeech 2023.** <https://www.isca-archive.org/interspeech_2023/huang23b_interspeech.pdf>
- **[S17] Raju et al., “Two-Pass Endpoint Detection for Speech Recognition,” ASRU 2023 / arXiv:2401.08916.** <https://arxiv.org/pdf/2401.08916>
- **[S18] Goyal & Garera, “Building Accurate Low Latency ASR for Streaming Voice Search,” ACL Industry 2023.** Joint CTC EOS detection reduced latency about 1.3 s/46.64% versus independent VAD in a large-scale deployed voice-search setting, with a WER tradeoff. <https://aclanthology.org/2023.acl-industry.26.pdf>
- **[S20] Arivazhagan et al., “Re-translation versus Streaming for Simultaneous Translation,” IWSLT 2020.** Re-translation, biased search, wait-k, content delay, and normalized erasure. <https://aclanthology.org/2020.iwslt-1.27/> and <https://aclanthology.org/2020.iwslt-1.27.pdf>
- **[S21] Chen et al., “Improving Stability in Simultaneous Speech Translation: A Revision-Controllable Decoding Approach,” 2023.** Revision-window beam pruning; zero-revision and small-window tradeoffs. <https://arxiv.org/pdf/2310.04399>
- **[S22] Polák et al., “Incremental Blockwise Beam Search for Simultaneous Speech Translation with Controllable Quality-Latency Tradeoff,” Interspeech 2023.** Local agreement and hold-n for incremental output. <https://www.isca-archive.org/interspeech_2023/polak23_interspeech.html>
- **[S25] Macháček, Dabre & Bojar, “Turning Whisper into Real-Time Transcription System,” IJCNLP-AACL 2023.** LocalAgreement-2 and timestamp-aware buffer trimming. <https://aclanthology.org/2023.ijcnlp-demo.3/> and <https://arxiv.org/pdf/2307.14743>
- **[S26] Chen et al., “Controllable Time-Delay Transformer for Real-Time Punctuation Prediction and Disfluency Detection,” ICASSP 2020.** <https://arxiv.org/abs/2003.01309>
- **[S27] Karakanta, Negri & Turchi, “Point Break: Surfing Heterogeneous Data for Subtitle Segmentation,” CLiC-it 2020.** Subtitle line/block constraints and semantic/syntactic segmentation. <https://aclanthology.org/2020.clicit-1.27.pdf>
- **[S28] Ponce, Etchegoyhen & Ruiz, “Unsupervised Subtitle Segmentation with Masked Language Models,” ACL 2023.** Punctuation-likelihood boundary scoring under maximum length/two-line constraints. <https://aclanthology.org/2023.acl-short.67.pdf>
- **[S29] Karakanta et al., “Evaluating Subtitle Segmentation for End-to-end Generation Systems,” LREC 2022 / arXiv:2205.09360.** Formal and syntactic/semantic subtitle constraints, EOL/EOB distinction. <https://arxiv.org/pdf/2205.09360>
- **[S31] Iranzo-Sánchez et al., “Direct Segmentation Models for Streaming Speech Translation,” EMNLP 2020.** Per-word split decisions with bounded text future/history and aligned acoustic features. <https://aclanthology.org/2020.emnlp-main.206.pdf>

### Standards and official caption guidance

- **[S23] DCMP Captioning Key — Text.** Logical line division, 40-frame minimum, 6 s maximum, preferably no more than two lines. <https://dcmp.org/learn/597-captioning-key---text>
- **[S24] BBC Subtitle Guidelines.** Prepared/live guidance, 160–180 wpm live rate, no more than two lines for landscape/square video, legacy 37-character Teletext width and proportional-font caveat. <https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles>
- **[S33] EBU Tech 3370, EBU-TT Part 3 Live Subtitling.** Live subtitle timing, authoring delay, ordered document sequences, and processing/improver nodes. <https://tech.ebu.ch/publications/tech3370> and <https://tech.ebu.ch/docs/tech/tech3370.pdf>
- **[S34] W3C WebVTT.** A caption cue is a text segment associated with a time interval; line breaks and cue regions are presentation features, not endpoint algorithms. <https://www.w3.org/TR/webvtt1/>

## 11. Evidence gaps and stopping point

- The arXiv API endpoint failed repeatedly; canonical arXiv/ACL/ISCA paper pages and PDFs were retrieved through web fetch instead.
- Soniox documentation search exposed an error string for `max_non_final_tokens_duration_ms`, but no authoritative field description/range was found in the official SDK type model. This note therefore does **not** recommend relying on that field.
- BBC's page is very large and focused fetches returned broad page content; exact 37-character and 160–180 wpm statements were separately recovered from the same official page's search index. DCMP provides the firmer numeric duration limit.
- The concrete LingerLens thresholds above are starting policies derived from the cited mechanisms plus LingerLens's measured delay/latency distribution. They require live replay evaluation across languages, speech rates, and translation providers before being treated as production defaults.
