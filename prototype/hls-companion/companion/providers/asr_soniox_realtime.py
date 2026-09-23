"""Soniox real-time STT adapter (``soniox-realtime``).

Implements the official raw WebSocket protocol without the Soniox SDK:
https://soniox.com/docs/stt/api-reference/websocket-api

The first text frame configures ``stt-rt-v5`` and 16 kHz mono PCM. Audio uses
binary frames. Soniox returns mutable non-final tokens plus final tokens; the
special final tokens ``<end>`` and ``<fin>`` mark an utterance boundary and a
manual-finalization boundary respectively. The adapter exposes the accumulated
final prefix as LingerLens's interim text, and emits one final cue at each boundary.

Translation (``stt-rt-v5``): adding a ``translation`` block to the first frame
makes the same session emit translated text, and the official token stream then
tags every token with ``translation_status``:

    "original"     spoken text
    "translation"  its translation
    "none"         spoken text outside the configured language pair

Translated tokens carry no ``start_ms``/``end_ms`` -- they are "generated after
their spoken tokens and follow the same sequence" -- so this adapter never
mixes them into the source timeline. It accumulates the confirmed translation
and republishes the running total on the events that already carry the source,
which is what SubtitlePipeline's session-backed translation resolves against.
That sequence is also the only alignment on offer, so every time speech resumes
after a translation chunk the adapter freezes the pair it implies into
``ASREvent.translation_anchors``: the Provider's own statement of how much
source the translation so far renders. See ``_track_translation_anchor``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import Counter
from typing import Any, AsyncIterator

import aiohttp

from . import register
from ..languages import LanguageNotSupportedError, canonicalize_tag_or_none, primary_subtag
from .base import (
    ASRCapabilities,
    ASREvent,
    ASRLanguageCapabilities,
    ASRProvider,
    ASRStream,
    CaptionObservation,
    NativeTranslationCapabilities,
    RecognitionToken,
    SourceLanguagePolicy,
)

_SUPPORTED = (
    "af", "ar", "az", "be", "bg", "bn", "bs", "ca", "cs", "cy", "da", "de",
    "el", "en", "es", "et", "eu", "fa", "fi", "fr", "gl", "gu", "he", "hi",
    "hr", "hu", "id", "it", "ja", "kk", "kn", "ko", "lt", "lv", "mk", "ml",
    "mr", "ms", "nl", "no", "pa", "pl", "pt", "ro", "ru", "sk", "sl", "sq",
    "sr", "sv", "sw", "ta", "te", "th", "tl", "tr", "uk", "ur", "vi", "zh",
)
_END_TOKENS = {"<end>", "<fin>"}
_TRANSLATED = "translation"


def _canonical_tags(tags: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(tag for tag in (canonicalize_tag_or_none(item) for item in tags) if tag))


def _hint(tag: str) -> str:
    return primary_subtag(tag)


def _translation_mode(options: dict[str, Any]) -> str | None:
    """Official ``translation.type`` from a profile's saved options, if any.

    Profile options are camelCase everywhere else in this catalog, so the mode
    is saved as ``translationType`` and translated to the wire value here. Any
    other value -- including the empty string the UI writes for "off" -- means
    the profile stays a plain transcription profile.
    """
    mode = options.get("translationType")
    return mode if mode in {"one_way", "two_way"} else None


def _translation_config(
    options: dict[str, Any], policy: SourceLanguagePolicy, target_tag: str | None
) -> dict[str, Any] | None:
    """Build the first-frame ``translation`` block, or None to stay transcript-only.

    One-way needs only the Target Language the viewer chose. Two-way needs both
    sides; the source side falls back to the specified Source Language so a
    two-way profile still works when only the viewer's target is known.
    """
    mode = _translation_mode(options)
    if mode is None:
        return None
    if mode == "one_way":
        return {"type": "one_way", "target_language": _hint(target_tag)} if target_tag else None
    language_a = options.get("translationLanguageA") or policy.tag or target_tag
    language_b = options.get("translationLanguageB") or target_tag
    if not language_a or not language_b:
        return None
    return {"type": "two_way", "language_a": _hint(language_a), "language_b": _hint(language_b)}


@register("soniox-realtime")
class SonioxRealtimeASRProvider(ASRProvider):
    requires_api_key = True

    def __init__(self, config: dict[str, Any]):
        self.id = config["id"]
        self.label = config.get("label", self.id)
        self.model = config["model"]
        self.base_url = str(config["baseUrl"])
        self.api_key = config.get("_apiKey", config.get("apiKey", ""))
        self.options = config.get("options", {})
        self.price_per_second_cny = config.get("pricePerSecondCny")

    @property
    def capabilities(self) -> ASRCapabilities:
        if self.model == "stt-rt-v5":
            languages = _canonical_tags(_SUPPORTED)
            language = ASRLanguageCapabilities(
                supported_tags=languages,
                detection="unrestricted",
                reports_detected_language=True,
                code_switching=True,
                tier="provider_claimed",
            )
        else:
            languages = ()
            language = ASRLanguageCapabilities(
                supported_tags=None,
                detection="none",
                reports_detected_language=False,
                tier="experimental",
            )
        return ASRCapabilities(
            streaming=True,
            interim_results=True,
            stable_prefix=True,
            server_vad=True,
            word_timestamps=True,
            hotwords=False,
            context=True,
            languages=languages,
            sample_rates=(16000,),
            # Soniox accepts ``finalize``, but it is a transport boundary rather
            # than a linguistic one. Using it for the six-second caption cap can
            # split Japanese inside a word (for example お｜誕生日) and the next
            # utterance may overlap the forced boundary. Let endpoint detection
            # and stable token evidence provide safe caption cuts instead.
            manual_commit=False,
            language=language,
            preferred_sample_rate=16000,
            speaker_labels=bool(self.options.get("enableSpeakerDiarization", False)),
            caption_evidence=frozenset({"stable_token_delta", "token_snapshot", "utterance_final", "endpoint"}),
            native_translation=self.native_translation,
        )

    @property
    def native_translation(self) -> NativeTranslationCapabilities:
        """Whether this profile asked Soniox to translate on the same session.

        Only ``stt-rt-v5`` is documented for realtime translation, and only when
        the saved options actually carry a ``translation`` block -- an unset
        option must leave the profile a plain transcription profile rather than
        silently switching billing and output on.
        """
        mode = _translation_mode(self.options)
        if self.model != "stt-rt-v5" or mode is None:
            return NativeTranslationCapabilities()
        return NativeTranslationCapabilities(
            enabled=True,
            # One-way always lands in the viewer's Target Language, so the
            # documented Soniox language set is the honest target contract.
            # Two-way picks the *other* side of the pair per utterance, which is
            # not a target contract at all -- leave it open.
            target_tags=_canonical_tags(_SUPPORTED) if mode == "one_way" else None,
            two_way=mode == "two_way",
            glossary=True,
            tier="provider_claimed",
        )

    def set_translation_target(self, target_tag: str | None) -> None:
        super().set_translation_target(target_tag)
        if target_tag and primary_subtag(target_tag) not in _SUPPORTED:
            raise LanguageNotSupportedError(
                f"Soniox realtime translation cannot target {target_tag}"
            )

    async def stream(
        self,
        *,
        policy: SourceLanguagePolicy,
        sample_rate: int,
        hotwords: list[str],
        context: list[str],
    ) -> ASRStream:
        del hotwords
        if not self.api_key:
            raise ValueError(f"API key is not configured for provider {self.id}")
        if sample_rate not in self.capabilities.sample_rates:
            raise ValueError(f"sample rate {sample_rate} is not supported by provider {self.id}")
        if policy.mode == "detect" and self.capabilities.language.detection == "none":
            raise LanguageNotSupportedError("this Soniox model cannot auto-detect the source language")
        stream = _SonioxStream(self, policy, sample_rate, context)
        await stream.connect()
        return stream


@register("soniox-realtime-transcribe")
class SonioxTranscribeOnlyASRProvider(SonioxRealtimeASRProvider):
    """The same Soniox protocol with translating pinned off.

    It exists as its own dropdown entry because the two things it can do are
    billed and behave differently: on the bilingual entry the caption path pairs
    each sentence with the translation Soniox returns on the same session, on
    this one it gets a transcript and a second model does the translating. The
    option is dropped rather than checked so a card cannot claim native
    translation while the wire request never asked for it.
    """

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.options = {**self.options, "translationType": ""}


class _SonioxStream(ASRStream):
    def __init__(
        self,
        provider: SonioxRealtimeASRProvider,
        policy: SourceLanguagePolicy,
        sample_rate: int,
        context: list[str],
    ):
        self.provider = provider
        self.policy = policy
        self.sample_rate = sample_rate
        self.context = context
        self.session: aiohttp.ClientSession | None = None
        self.ws: Any = None
        self.closed = False
        self._utterance = 1
        self._final_tokens: list[dict[str, Any]] = []
        self._emitted_final_token_keys: set[tuple[object, ...]] = set()
        self._emitted_lexical_tokens = 0
        self._utterance_start: float | None = None
        self._reset_final_cache()
        # Provider-side translation. Soniox returns transcribed and translated
        # tokens in one ordered stream and gives the translated ones no
        # timestamps, so the confirmed translation is accumulated as running
        # text and handed out in the same sequence it arrived: everything since
        # the last utterance boundary is the open utterance's translation.
        self._translation_final = ""
        self._translation_stash = ""
        # Settled (source, translation) prefix pairs for the open utterance, and
        # the pair waiting to settle. See ``_settle_translation_anchor``.
        self._translation_anchors: list[tuple[str, str]] = []
        self._translation_pending: tuple[str, str] | None = None
        self._audio_bytes_sent = 0
        self._last_audio_at = time.monotonic()
        self._keepalive_task: asyncio.Task | None = None
        self.keepalive_seconds = float(provider.options.get("keepAliveSeconds", 5.0))
        # Graceful-EOF state: after the adapter sends the official empty stop
        # frame the server flushes its remaining final tokens and then closes;
        # the event iterator keeps draining until ``finished=true``. ``aclose``
        # waits for that drain only for a bounded window so stop/cancel is
        # always finite and idempotent.
        self._iter_started = False
        self._drain_done = asyncio.Event()
        self._release_done = False
        self._force_close_task: asyncio.Task | None = None
        self.drain_seconds = float(provider.options.get("closeDrainTimeoutSeconds", 2.0))

    def _config(self) -> dict[str, Any]:
        options = self.provider.options
        hints: list[str] = []
        strict = False
        if self.policy.mode == "specified":
            assert self.policy.tag is not None
            hints = [_hint(self.policy.tag)]
            strict = bool(options.get("languageHintsStrict", True))
        elif self.policy.candidates:
            hints = list(dict.fromkeys(_hint(tag) for tag in self.policy.candidates))
            strict = bool(options.get("languageHintsStrict", False))

        config: dict[str, Any] = {
            "api_key": self.provider.api_key,
            "model": self.provider.model,
            "audio_format": "pcm_s16le",
            "sample_rate": self.sample_rate,
            "num_channels": 1,
            "enable_language_identification": bool(options.get("enableLanguageIdentification", True)),
            "enable_endpoint_detection": bool(options.get("enableEndpointDetection", True)),
        }
        if bool(options.get("enableSpeakerDiarization", False)):
            # Official first-frame option; per-token string "speaker" labels
            # come back on every token when enabled.
            config["enable_speaker_diarization"] = True
        if hints:
            config["language_hints"] = hints
            config["language_hints_strict"] = strict
        translation = _translation_config(
            options, self.policy, self.provider.translation_target
        )
        if translation is not None:
            # Same session, same socket: Soniox transcribes AND translates, and
            # tags every returned token with which of the two it is.
            config["translation"] = translation
        terms = [item for item in (options.get("translationTerms") or []) if isinstance(item, dict)]
        if self.context or terms:
            context: dict[str, Any] = {}
            if self.context:
                context["terms"] = self.context
            if terms and translation is not None:
                # Official context.translation_terms: [{source, target}].
                context["translation_terms"] = [
                    {"source": str(item.get("source", "")), "target": str(item.get("target", ""))}
                    for item in terms
                    if item.get("source") and item.get("target")
                ]
            if context:
                config["context"] = context
        for source, target in (
            ("maxEndpointDelayMs", "max_endpoint_delay_ms"),
            ("endpointSensitivity", "endpoint_sensitivity"),
            ("endpointLatencyAdjustmentLevel", "endpoint_latency_adjustment_level"),
            # Forces non-final tokens to finalize after the given span even
            # without an endpoint; caps how long a draft can linger.
            ("maxNonFinalTokensDurationMs", "max_non_final_tokens_duration_ms"),
        ):
            if source in options and options[source] is not None:
                config[target] = options[source]
        return config

    async def connect(self) -> None:
        self.session = aiohttp.ClientSession()
        try:
            self.ws = await self.session.ws_connect(self.provider.base_url)
            await self.ws.send_json(self._config())
        except BaseException:
            await self.aclose()
            raise
        if self.keepalive_seconds > 0:
            self._keepalive_task = asyncio.create_task(
                self._keepalive(), name=f"soniox-keepalive-{self.provider.id}"
            )

    async def push_pcm(self, chunk: bytes, pcm_offset: float) -> None:
        del pcm_offset
        if not self.ws or self.ws.closed:
            return
        self._last_audio_at = time.monotonic()
        self._audio_bytes_sent += len(chunk)
        try:
            await self.ws.send_bytes(chunk)
        except BaseException:
            self._audio_bytes_sent -= len(chunk)
            raise

    async def flush(self) -> None:
        if self.ws and not self.ws.closed:
            await self.ws.send_json({"type": "finalize"})

    async def commit(self) -> None:
        await self.flush()

    async def _keepalive(self) -> None:
        try:
            while self.ws and not self.ws.closed:
                if time.monotonic() - self._last_audio_at >= self.keepalive_seconds:
                    with contextlib.suppress(aiohttp.ClientError, RuntimeError):
                        await self.ws.send_json({"type": "keepalive"})
                    self._last_audio_at = time.monotonic()
                await asyncio.sleep(min(1.0, self.keepalive_seconds / 2))
        except asyncio.CancelledError:
            raise

    def __aiter__(self) -> AsyncIterator[ASREvent]:
        return self._events()

    async def _events(self) -> AsyncIterator[ASREvent]:
        self._iter_started = True
        try:
            if not self.ws:
                return
            async for message in self.ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    try:
                        raw = json.loads(message.data)
                    except json.JSONDecodeError as exc:
                        yield ASREvent("error", message=str(exc))
                        continue
                    for event in self._map_event(raw):
                        yield event
                    if raw.get("finished"):
                        return
                elif message.type == aiohttp.WSMsgType.ERROR:
                    yield ASREvent("error", message=str(self.ws.exception()))
                    return
        finally:
            # Only signal the drain: resource release is owned by aclose() so
            # the official empty stop frame can still be sent afterwards.
            self._drain_done.set()

    async def _release(self) -> None:
        """Close WebSocket + session once; safe from aclose and _events."""
        if self._release_done:
            return
        self._release_done = True
        if self.ws is not None:
            with contextlib.suppress(aiohttp.ClientError, RuntimeError):
                await self.ws.close()
        if self.session is not None:
            with contextlib.suppress(Exception):
                await self.session.close()

    async def _force_close_after_drain_window(self) -> None:
        """Bounded fallback: if the server never finishes the session, close
        the WebSocket when the drain window expires so stop/cancel stays
        finite. The normal path is _events()'s finally-release on finished."""
        try:
            await asyncio.wait_for(self._drain_done.wait(), timeout=self.drain_seconds)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        await self._release()

    def _map_event(self, raw: dict[str, Any]) -> list[ASREvent]:
        if raw.get("error_code") or raw.get("error_message"):
            error_type = raw.get("error_type")
            message = raw.get("error_message") or error_type or str(raw.get("error_code"))
            return [ASREvent("error", message=str(message), raw=raw)]

        raw_tokens = raw.get("tokens")
        # Soniox control/status messages may carry a numeric ``tokens`` field
        # (for example a count) instead of the token array used by transcript
        # messages. Treat those messages as having no lexical tokens; they
        # must never terminate the caption pipeline with ``int is not
        # iterable``.
        if not isinstance(raw_tokens, list):
            raw = dict(raw)
            raw["tokens"] = []
        source_tokens, translation_tokens = _split_translation(raw["tokens"])
        translation_before = self._translation_final
        utterance_before = self._utterance
        self._absorb_translation(translation_tokens)
        tokens = self._project_token_clock({**raw, "tokens": source_tokens})
        events: list[ASREvent] = []
        non_final: list[dict[str, Any]] = []
        new_final_tokens: list[dict[str, Any]] = []
        for token in tokens:
            text = str(token.get("text", ""))
            if text in _END_TOKENS:
                if self._final_tokens:
                    events.extend(self._finish_utterance(raw, token, new_final_tokens))
                continue
            if token.get("is_final"):
                key = _token_key(token)
                if key in self._emitted_final_token_keys:
                    continue
                self._emitted_final_token_keys.add(key)
                if self._utterance_start is None:
                    self._utterance_start = _seconds(token.get("start_ms"))
                if len(self._final_tokens) >= 8192:
                    return [ASREvent("error", message="ASR utterance exceeded the 8192-token safety bound")]
                self._final_tokens.append(token)
                new_final_tokens.append(token)
            else:
                non_final.append(token)

        stable_delta = self._update_final_cache()
        if self._utterance == utterance_before:
            # An utterance that closed inside this frame already claimed the
            # running translation; the tokens left over belong to the next one.
            self._track_translation_anchor(
                source_advanced=bool(new_final_tokens),
                translation_advanced=self._translation_final != translation_before,
            )
        if self._final_tokens or non_final:
            text = (self._final_text + "".join(str(token.get("text", "")) for token in non_final)).strip()
            if text:
                self._emitted_lexical_tokens += len(stable_delta)
                languages = self._final_languages.copy()
                speakers = self._final_speakers.copy()
                confidence_sum, confidence_count = self._final_confidence
                for token in non_final:
                    language = canonicalize_tag_or_none(token.get("language"))
                    if language:
                        languages[language] += 1
                    if token.get("speaker") is not None:
                        speakers[str(token["speaker"])] += 1
                    if isinstance(token.get("confidence"), (int, float)):
                        confidence_sum += float(token["confidence"])
                        confidence_count += 1
                language = languages.most_common(1)[0][0] if languages else None
                speaker = speakers.most_common(1)[0][0] if speakers else None
                begin = self._utterance_start if self._utterance_start is not None else _token_start(non_final)
                end = _token_end(non_final)
                if end is None:
                    end = self._final_end_pcm
                events.append(ASREvent(
                    "interim",
                    text=text,
                    begin_pcm=begin,
                    end_pcm=end,
                    language=language,
                    item_id=str(self._utterance),
                    speaker=speaker,
                    confidence=round(confidence_sum / confidence_count, 4) if confidence_count else None,
                    raw=raw,
                    translation=self._open_translation,
                    translation_stash=self._translation_stash,
                    translation_anchors=tuple(self._translation_anchors),
                    # Mutable Soniox pieces are tokenizer output, not complete
                    # lexical snapshots. Publish only the immutable lexical
                    # prefix; an empty delta still suppresses the legacy mutable
                    # prefix path in SubtitlePipeline.
                    caption_observation=CaptionObservation(
                        "stable_token_delta",
                        0,
                        str(self._utterance),
                        tokens=tuple(stable_delta),
                        begin_pcm=begin,
                        end_pcm=end,
                        language=language,
                        speaker=speaker,
                    ),
                ))
        if raw.get("finished") and self._final_tokens:
            events.extend(self._finish_utterance(raw, None, new_final_tokens))
        if translation_tokens and not any(event.type == "final" for event in events):
            # Translation can advance without new source tokens. Preserve its
            # latest snapshot; the native bus joins only complete source segments.
            events.append(ASREvent(
                "translation",
                item_id=str(self._utterance),
                translation=self._open_translation,
                translation_stash=self._translation_stash,
                translation_anchors=tuple(self._translation_anchors),
                raw=raw,
            ))
        return events

    def _reset_final_cache(self) -> None:
        self._processed_finals = 0
        self._final_text = ""
        self._final_languages: Counter[str] = Counter()
        self._final_speakers: Counter[str] = Counter()
        self._final_confidence = (0.0, 0)
        self._final_end_pcm: float | None = None
        self._lexical_tail: list[dict[str, Any]] = []

    def _update_final_cache(self) -> list[RecognitionToken]:
        """Append stable evidence once; only an unfinished whitespace word waits.

        The complete provider segment remains available for final reconciliation,
        but status frames and new deltas do not tokenize or recount its history.
        """
        new = self._final_tokens[self._processed_finals:]
        self._processed_finals = len(self._final_tokens)
        self._final_text += "".join(str(token.get("text", "")) for token in new)
        delta: list[RecognitionToken] = []
        confidence_sum, confidence_count = self._final_confidence
        for token in new:
            language = canonicalize_tag_or_none(token.get("language"))
            if language:
                self._final_languages[language] += 1
            if token.get("speaker") is not None:
                self._final_speakers[str(token["speaker"])] += 1
            if isinstance(token.get("confidence"), (int, float)):
                confidence_sum += float(token["confidence"])
                confidence_count += 1
            end = _seconds(token.get("end_ms"))
            if end is not None:
                self._final_end_pcm = end
            text = str(token.get("text", ""))
            if not text or text in _END_TOKENS:
                continue
            piece = bool(language and primary_subtag(language) in {"zh", "ja", "ko"})
            boundary = self._lexical_tail and (
                text[:1].isspace() or str(self._lexical_tail[-1].get("text", ""))[-1:].isspace()
            )
            if self._lexical_tail and (piece or boundary):
                delta.append(_merge_lexical_token(self._lexical_tail, True))
                self._lexical_tail = []
            if piece:
                delta.append(_merge_lexical_token([token], True, is_piece=True))
            else:
                self._lexical_tail.append(token)
        self._final_confidence = (confidence_sum, confidence_count)
        return delta

    def _project_token_clock(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """Convert Soniox response time onto the PCM bytes sent to this session."""
        tokens = raw.get("tokens") or []
        try:
            processed_ms = float(raw.get("total_audio_proc_ms"))
        except (TypeError, ValueError):
            return tokens
        sent_ms = self._audio_bytes_sent * 1000.0 / (self.sample_rate * 2)
        correction_ms = max(0.0, processed_ms - sent_ms)
        if correction_ms == 0:
            return tokens
        projected: list[dict[str, Any]] = []
        for source in tokens:
            token = dict(source)
            for field in ("start_ms", "end_ms"):
                try:
                    token[field] = max(0.0, float(source[field]) - correction_ms)
                except (KeyError, TypeError, ValueError):
                    pass
            projected.append(token)
        return projected

    @property
    def _open_translation(self) -> str:
        """Confirmed translation received since the last utterance boundary."""
        return self._translation_final

    def _absorb_translation(self, tokens: list[dict[str, Any]]) -> None:
        """Accumulate ``translation_status == "translation"`` tokens.

        Only confirmed pieces are kept. A non-final translated token may still be
        rewritten, and a cue that has already been shown cannot be taken back, so
        the tentative tail is published separately and never resolved against.
        """
        for token in tokens:
            text = str(token.get("text", ""))
            if not text:
                continue
            if token.get("is_final"):
                self._translation_final += text
                # A confirmed piece supersedes whatever tentative tail preceded it.
                self._translation_stash = ""
            else:
                self._translation_stash += text

    def _track_translation_anchor(self, *, source_advanced: bool, translation_advanced: bool) -> None:
        """Follow the Provider's own ordering between speech and translation.

        Soniox states that transcription and translation chunks follow each
        other, and gives a translated token no timestamp and no id for the words
        it renders. So the only alignment that exists is which chunk came last:
        once speech arrives after a translation chunk, the pair recorded at that
        chunk's end is the Provider saying "what I just translated is exactly
        this much source". A frame that carried both is no evidence of an order,
        so it only moves the candidate forward.
        """
        if translation_advanced:
            if self._translation_final:
                self._translation_pending = (self._final_text, self._translation_final)
            return
        if source_advanced:
            self._settle_translation_anchor()

    def _settle_translation_anchor(self) -> None:
        """Freeze the waiting pair as an anchor the pipeline may attribute to."""
        pending, self._translation_pending = self._translation_pending, None
        if pending is None or not pending[0]:
            return
        if not self._translation_anchors or self._translation_anchors[-1][1] != pending[1]:
            self._translation_anchors.append(pending)

    def _take_translation(self) -> str:
        """Claim the open utterance's translation and start a fresh bucket.

        Clearing rather than advancing a cursor keeps a multi-hour session from
        holding every translation it has ever produced.
        """
        text = self._translation_final.strip()
        self._translation_final = ""
        self._translation_stash = ""
        self._translation_pending = None
        self._translation_anchors = []
        return text

    def _finish_utterance(
        self,
        raw: dict[str, Any],
        endpoint_token: dict[str, Any] | None,
        finalized_delta: list[dict[str, Any]],
    ) -> list[ASREvent]:
        # The utterance closing is the Provider's last word on where its
        # translation ends and new speech begins; claim both before the buckets
        # reset, since ``_take_translation`` clears them.
        self._settle_translation_anchor()
        anchors = tuple(self._translation_anchors)
        translation = self._take_translation()
        tokens = self._final_tokens
        self._final_tokens = []
        self._emitted_final_token_keys.clear()
        emitted_lexical_tokens = self._emitted_lexical_tokens
        self._emitted_lexical_tokens = 0
        self._reset_final_cache()
        item_id = str(self._utterance)
        self._utterance += 1
        begin = self._utterance_start if self._utterance_start is not None else _token_start(tokens)
        # ``<end>`` / ``<fin>`` are control boundaries, not spoken words.
        # Live Soniox responses may put 0 in their timestamp fields; using that
        # value made every cue end before it began, and the generic pipeline then
        # clamped tEnd to tStart (zero-duration subtitles). Lexical token timing
        # is the authoritative speech span. Only use a boundary timestamp when
        # no lexical token has an end timestamp at all.
        end = _token_end(tokens)
        if end is None and endpoint_token is not None:
            end = _seconds(endpoint_token.get("end_ms"))
        self._utterance_start = None
        text = "".join(str(token.get("text", "")) for token in tokens).strip()
        language = _dominant_language(tokens)
        events: list[ASREvent] = []
        if begin is not None:
            events.append(ASREvent(
                "speech_started", begin_pcm=begin, item_id=item_id, raw=raw,
            ))
        if end is not None:
            events.append(ASREvent(
                "speech_stopped", end_pcm=end, item_id=item_id, raw=raw,
            ))
        if text:
            events.append(ASREvent(
                "final", text=text, begin_pcm=begin, end_pcm=end,
                language=language, item_id=item_id,
                speaker=_dominant_speaker(tokens), confidence=_mean_confidence(tokens),
                raw=raw,
                translation=translation,
                translation_anchors=anchors,
                caption_observation=CaptionObservation(
                    "utterance_final", 0, item_id,
                    tokens=tuple(_lexical_tokens(tokens, True)[emitted_lexical_tokens:]),
                    stable_text=text, begin_pcm=begin, end_pcm=end,
                    language=language,
                    speaker=_dominant_speaker(tokens),
                ),
            ))
        return events

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            await asyncio.gather(self._keepalive_task, return_exceptions=True)
            self._keepalive_task = None
        if self.ws and not self.ws.closed:
            # The official protocol uses an empty frame to stop recording, then
            # emits tail finals and finished=true. When an event consumer exists,
            # wait a bounded interval for that drain before releasing resources.
            with contextlib.suppress(aiohttp.ClientError, RuntimeError):
                await self.ws.send_bytes(b"")
        if self._iter_started and not self._drain_done.is_set():
            try:
                await asyncio.wait_for(self._drain_done.wait(), timeout=self.drain_seconds)
            except asyncio.TimeoutError:
                pass
        await self._release()


def _split_translation(
    tokens: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate the unified token stream by ``translation_status``.

    Soniox returns transcription and translation in one array. Translated tokens
    carry no timestamps and belong to the target language, so they must never
    reach the source-token clock, the caption chunker, or language detection.
    """
    source: list[dict[str, Any]] = []
    translated: list[dict[str, Any]] = []
    for token in tokens:
        if isinstance(token, dict) and token.get("translation_status") == _TRANSLATED:
            translated.append(token)
        else:
            source.append(token)
    return source, translated


def _token_key(token: dict[str, Any]) -> tuple[object, ...]:
    return (
        token.get("text"), token.get("start_ms"), token.get("end_ms"),
        token.get("language"), token.get("speaker"),
    )


def _closed_lexical_prefix(tokens: list[dict[str, Any]]) -> list[RecognitionToken]:
    """Expose immutable CJK pieces immediately; close whitespace words once.

    A stable provider piece may end inside a word. Caption Chunker owns the
    linguistic decision, so the Adapter must not wait for a Japanese phrase.
    """
    lexical = _lexical_tokens(tokens, True)
    if lexical and tokens:
        language = canonicalize_tag_or_none(tokens[-1].get("language"))
        if language and primary_subtag(language) in {"zh", "ja", "ko"}:
            return lexical
    return lexical[:-1]


def _lexical_tokens(tokens: list[dict[str, Any]], provider_stable: bool) -> list[RecognitionToken]:
    result: list[RecognitionToken] = []
    current: list[dict[str, Any]] = []
    for token in tokens:
        text = str(token.get("text", ""))
        if not text or text in _END_TOKENS:
            continue
        language = canonicalize_tag_or_none(token.get("language"))
        if language and primary_subtag(language) in {"zh", "ja", "ko"}:
            if current:
                result.append(_merge_lexical_token(current, provider_stable))
                current = []
            result.append(_merge_lexical_token([token], provider_stable, is_piece=True))
            continue
        if current and (text[:1].isspace() or str(current[-1].get("text", ""))[-1:].isspace()):
            result.append(_merge_lexical_token(current, provider_stable))
            current = []
        current.append(token)
    if current:
        result.append(_merge_lexical_token(current, provider_stable))
    return result


def _merge_lexical_token(tokens: list[dict[str, Any]], provider_stable: bool, *, is_piece: bool = False) -> RecognitionToken:
    text = "".join(str(token.get("text", "")) for token in tokens)
    if not is_piece:
        text = text.strip()
    confidences = [
        float(token["confidence"])
        for token in tokens
        if isinstance(token.get("confidence"), (int, float))
    ]
    return RecognitionToken(
        text=text,
        begin_pcm=_token_start(tokens),
        end_pcm=_token_end(tokens),
        provider_stable=provider_stable,
        is_piece=is_piece,
        language=_dominant_language(tokens),
        speaker=_dominant_speaker(tokens),
        confidence=sum(confidences) / len(confidences) if confidences else None,
    )


def _seconds(value: Any) -> float | None:
    return float(value) / 1000.0 if isinstance(value, (int, float)) else None


def _token_start(tokens: list[dict[str, Any]]) -> float | None:
    return next((_seconds(token.get("start_ms")) for token in tokens if _seconds(token.get("start_ms")) is not None), None)


def _token_end(tokens: list[dict[str, Any]]) -> float | None:
    return next((_seconds(token.get("end_ms")) for token in reversed(tokens) if _seconds(token.get("end_ms")) is not None), None)


def _dominant_language(tokens: list[dict[str, Any]]) -> str | None:
    counts: Counter[str] = Counter()
    for token in tokens:
        language = canonicalize_tag_or_none(token.get("language"))
        if language:
            counts[language] += 1
    return counts.most_common(1)[0][0] if counts else None


def _dominant_speaker(tokens: list[dict[str, Any]]) -> str | None:
    """Most frequent token-level speaker label, rendered as a string.

    The official diarization field is ``speaker`` (a string), not ``spk``.
    Counter ties keep first-seen order so the choice is deterministic.
    """
    counts: Counter[str] = Counter()
    for token in tokens:
        speaker = token.get("speaker")
        if speaker is not None:
            counts[str(speaker)] += 1
    return counts.most_common(1)[0][0] if counts else None


def _mean_confidence(tokens: list[dict[str, Any]]) -> float | None:
    """Mean lexical-token confidence; control tokens carry none and are
    excluded rather than counted as 0."""
    values = [
        float(token["confidence"])
        for token in tokens
        if isinstance(token.get("confidence"), (int, float))
    ]
    return round(sum(values) / len(values), 4) if values else None
