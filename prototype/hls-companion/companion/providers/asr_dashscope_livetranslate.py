"""Alibaba Bailian (百炼) Qwen3-LiveTranslate realtime adapter.

``dashscope-livetranslate-realtime`` drives the Model Studio *LiveTranslate*
family: ``qwen3.8-livetranslate-flash-realtime`` (current), ``qwen3.5-...``
(same wire shape as 3.8 for the fields used here) and the legacy ``qwen3-...``.

This is NOT an ASR model with a translation add-on -- it is a simultaneous
interpreter. It transcribes and translates on one session, so the profile
reports ``capabilities.native_translation`` and LingerLens never calls a
separate translation Provider for it.

Protocol (official docs, verified 2026-09-21):

* Endpoint  ``wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=<id>`` -- the
  global realtime host, verified working against the live service on 2026-09-21.
  The docs print a per-workspace host
  (``wss://{WorkspaceId}.{region}.maas.aliyuncs.com/api-ws/v1/realtime``); it is
  an alternative, not a requirement, and ``endpoint_url`` fills the workspace id
  in from the ``workspaceId`` option (or ``DASHSCOPE_WORKSPACE_ID``) when a
  profile points at it.
* Auth      HTTP upgrade header ``Authorization: Bearer <DASHSCOPE_API_KEY>``.
* Session   ``session.update`` with ``output_modalities``/``modalities``,
  ``voice`` and ``translation.language`` (plus ``translation.corpus.phrases``, a
  term map). ``voice`` is not optional in practice: the session the server hands
  back defaults to ``Chelsie``, which this model rejects on its first generated
  turn, and the connection then dies having produced no transcript and no
  translation. 3.8 needs no audio declaration at all: input defaults to 16000 Hz
  PCM, output to 24000 Hz PCM. The 3.5 generation names the same ideas
  ``modalities`` / ``input_audio_format`` / ``sample_rate`` and takes those
  instead.
* Segments  3.8 breaks turns server-side (``audio.input.turn_detection.type =
  speaker_detection``) and generates a response as soon as a turn ends; the
  official docs restrict both VAD and manual ``input_audio_buffer.commit`` modes
  to 3.5, so a 3.8 client only appends audio. The same object is nonetheless
  what the 3.8 session reports back -- ``server_vad`` with
  ``silence_duration_ms: 800`` -- and ``silenceDurationMs`` overrides it here,
  which is the only latency knob this Provider has.
* Events    The source text arrives on
  ``conversation.item.input_audio_transcription.delta`` (a plain ``delta`` to
  concatenate) and is finished by ``.completed``, whose ``transcript`` is the
  whole utterance. The translation streams on ``response.text.delta`` for 3.8
  and ``response.text.text`` for 3.5.
* Caption   The cue is closed by the transcript's ``.completed``, *not* by
  ``input_audio_buffer.speech_stopped``. VAD stop fires while the transcription
  is still incomplete, and a cue cut at the cut point matches no boundary the
  Provider ever aligned, so it would show source text with no translation.
* Shutdown  ``{"type": "session.finish"}`` then wait for ``session.finished``.
  Closing without it loses the last utterance's recognition *and* translation.

Direction is one-way only: the target is a session setting. 3.8 auto-detects the
source language and documents no pin for it (``input_audio_transcription.language``
belongs to 3.5), so ``sourceLanguage`` is only sent to a 3.5 profile.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import uuid
from collections import OrderedDict, deque
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
    SourceLanguagePolicy,
)

# Official language table: 29 languages with audio+text output plus 31 that are
# text-only. LiveTranslate is a text-subtitle model here (output_modalities is
# ["text"]), so the text-output set is what this Profile can serve.
_LANGUAGES = (
    "zh", "en", "ar", "de", "fr", "es", "pt", "id", "it", "ko", "ru", "th",
    "vi", "ja", "tr", "hi", "ms", "nl", "ur", "nb", "sv", "da", "he", "fi",
    "pl", "is", "cs", "fil", "fa",
    "yue", "el", "af", "ast", "be", "bg", "bn", "bs", "ca", "ceb", "et",
    "gl", "gu", "hr", "hu", "jv", "kk", "kn", "ky", "lv", "mk", "ml", "mr",
    "pa", "ro", "sk", "sl", "sw", "tg", "az", "uk",
)
_WORKSPACE_PLACEHOLDER = "<workspace-id>"
# The official docs write the host three different ways across pages and sample
# code; accepting all of them means a pasted URL never has to be hand-edited.
_WORKSPACE_TOKENS = (_WORKSPACE_PLACEHOLDER, "{WorkspaceId}", "{workspace_id}", "{workspaceId}")


def _canonical_tags(tags: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(tag for tag in (canonicalize_tag_or_none(item) for item in tags) if tag)


# How long the server waits for silence before it closes a turn. The service
# states 800 on its own `session.updated`, and that wait is paid once per
# caption; a shorter one releases the cue sooner but splits a speaker who
# pauses into more, smaller captions.
# The server's own answer is 800, which let one utterance run to 36.4 s of speech
# on real Japanese; 500 still ran to 15.8 s. At 300 the model closed 15 of 17 cues
# itself and only 2 needed our deadline (.scratch/qwen-livetranslate-live,
# 2026-09-22), which is the point of this dial: a caption the Provider finished
# carries its own translation, one we cut has to be matched after the fact.
DEFAULT_SILENCE_DURATION_MS = 300

# A boundary per growing packet, and one turn streams a packet every ~0.4s. The
# ledger only ever needs the newest ones -- an older pair belongs to text a cue has
# already taken -- so the oldest fall off rather than growing with the session.
_BOUNDARIES_MAX = 64


def _silence_duration_ms(options: dict[str, Any]) -> int:
    try:
        value = int(float(options.get("silenceDurationMs")))
    except (TypeError, ValueError):
        return DEFAULT_SILENCE_DURATION_MS
    return max(200, min(6000, value))


@register("dashscope-livetranslate-realtime")
class QwenLiveTranslateASRProvider(ASRProvider):
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
        languages = _canonical_tags(_LANGUAGES)
        return ASRCapabilities(
            streaming=True,
            interim_results=True,
            stable_prefix=True,
            server_vad=True,
            # LiveTranslate returns text only; no word/character timestamps are
            # documented for either the transcript or the translation.
            word_timestamps=False,
            hotwords=False,
            context=False,
            languages=languages,
            sample_rates=(16000,),
            manual_commit=False,
            language=ASRLanguageCapabilities(
                supported_tags=languages,
                # Source language is auto-detected unless the profile pins one.
                detection="unrestricted",
                # 3.8's transcription deltas carry no language field, so the
                # profile must not claim a report it cannot make; the pipeline
                # falls back to the specified/preferred language.
                reports_detected_language=False,
                code_switching=True,
                tier="provider_claimed",
            ),
            preferred_sample_rate=16000,
            speaker_labels=False,
            caption_evidence=frozenset(
                {"stable_prefix_snapshot", "utterance_final", "endpoint"}
            ),
            native_translation=self.native_translation,
        )

    @property
    def native_translation(self) -> NativeTranslationCapabilities:
        """Always on: translating *is* what this model does.

        There is no documented way to switch a LiveTranslate session back to
        transcription only, so claiming otherwise would be inventing a contract.
        The target language is the viewer's Target Language, which is what
        ``translation.language`` accepts.
        """
        return NativeTranslationCapabilities(
            enabled=True,
            target_tags=_canonical_tags(_LANGUAGES),
            two_way=False,
            glossary=True,
            reports_source_language=False,
            tier="provider_claimed",
        )

    def set_translation_target(self, target_tag: str | None) -> None:
        super().set_translation_target(target_tag)
        if target_tag and primary_subtag(target_tag) not in _LANGUAGES:
            raise LanguageNotSupportedError(
                f"Qwen LiveTranslate cannot translate into {target_tag}"
            )

    def endpoint_url(self) -> str:
        """``baseUrl`` with the workspace host filled in, when it needs filling.

        Only a profile that points at a ``{WorkspaceId}.maas.aliyuncs.com`` host
        needs an id; the global ``dashscope.aliyuncs.com`` host is used verbatim.
        A placeholder left in place cannot resolve, and an address that cannot
        resolve looks exactly like a Provider that answers with nothing, so this
        says which field to fill rather than failing quietly downstream.
        """
        url = str(self.base_url)
        token = next((item for item in _WORKSPACE_TOKENS if item in url), None)
        if token is None:
            return url
        workspace = str(self.options.get("workspaceId") or "").strip()
        if not workspace:
            workspace = os.environ.get("DASHSCOPE_WORKSPACE_ID", "").strip()
        if not workspace:
            raise ValueError(
                f"Provider {self.id} has a workspace host with no workspace id in "
                f"it: {url}. Either set the workspaceId option in 语音识别 → 高级设置 "
                "(export DASHSCOPE_WORKSPACE_ID works too, and the official sample "
                "reads the same variable), or replace Base URL with the global host "
                "wss://dashscope.aliyuncs.com/api-ws/v1/realtime, which this model "
                "answers on without an id."
            )
        return url.replace(token, workspace)

    async def stream(
        self,
        *,
        policy: SourceLanguagePolicy,
        sample_rate: int,
        hotwords: list[str],
        context: list[str],
    ) -> ASRStream:
        del hotwords, context
        if not self.api_key:
            raise ValueError(f"API key is not configured for provider {self.id}")
        # Raises rather than connecting to a host that cannot exist.
        self.endpoint_url()
        if sample_rate != 16000:
            raise ValueError(f"sample rate {sample_rate} is not supported by provider {self.id}")
        if policy.mode == "specified":
            assert policy.tag is not None
            if primary_subtag(policy.tag) not in _LANGUAGES:
                raise LanguageNotSupportedError(
                    f"source language {policy.tag} is not supported by provider {self.id}"
                )
        stream = _LiveTranslateStream(self, policy, sample_rate)
        await stream.connect()
        return stream


class _Segment:
    __slots__ = ("source", "translation", "anchors")

    def __init__(self) -> None:
        self.source = ""
        self.translation = ""
        self.anchors: tuple[tuple[str, str], ...] = ()


class _LiveTranslateStream(ASRStream):
    def __init__(
        self,
        provider: QwenLiveTranslateASRProvider,
        policy: SourceLanguagePolicy,
        sample_rate: int,
    ) -> None:
        self.provider = provider
        self.policy = policy
        self.sample_rate = sample_rate
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.closed = False
        self._segments: "OrderedDict[str, _Segment]" = OrderedDict()
        # Turns opened by the VAD and not yet given their response. This, not the
        # transcript queue below, is what a response is paired with.
        self._turns: deque[str] = deque()
        # Utterances whose transcript has been seen but whose translation
        # response has not started. Responses are sequential, so the oldest one
        # owns the response currently streaming.
        self._awaiting: deque[str] = deque()
        self._responding: str | None = None
        self._active_item: str | None = None
        self._finished = asyncio.Event()
        self._iter_started = False
        self.drain_seconds = float(provider.options.get("closeDrainTimeoutSeconds", 3.0))

    # -- lifecycle -------------------------------------------------------

    async def connect(self) -> None:
        try:
            self.session = aiohttp.ClientSession()
            url = self.provider.endpoint_url()
            separator = "&" if "?" in url else "?"
            self.ws = await self.session.ws_connect(
                f"{url}{separator}model={self.provider.model}",
                headers={"Authorization": f"Bearer {self.provider.api_key}"},
            )
            await self.ws.send_json(self._session_update())
        except BaseException:
            await self.aclose()
            raise

    def _session_update(self) -> dict[str, Any]:
        options = self.provider.options
        target = {"language": self._target_language()}
        phrases = options.get("phrases")
        if isinstance(phrases, dict) and phrases:
            # Official session.translation.corpus.phrases: {source: target}.
            target["corpus"] = {
                "phrases": {str(k): str(v) for k, v in list(phrases.items())[:1000]}
            }
        pinned = self._pinned_source_language()
        audio = bool(options.get("audioOutput"))
        # Measured against the live service: the session this model starts with
        # carries voice "Chelsie", which it then rejects the moment it generates
        # its first turn ("<400> InternalError.Algo.InvalidParameter: Voice
        # 'Chelsie' is not supported"), and the connection dies with zero
        # transcripts and zero translations -- indistinguishable from "this
        # provider returns nothing". The docs say the default is "Tina"; pinning
        # it is what makes a text-only profile work at all.
        voice = str(options.get("voice") or "").strip() or "Tina"
        # What the service answers on `session.updated` when nothing is sent for
        # turn detection: 800 ms of trailing silence before a turn ends. That wait
        # is the single largest slice of "the caption is a second late" on this
        # profile, and unlike the model's own thinking time it is a parameter.
        # Every field is restated rather than only the one being changed: the
        # merge behaviour is not documented, and a session that lost
        # `create_response` would stop translating -- the same shape of failure
        # the default voice caused.
        silence_ms = _silence_duration_ms(options)
        turn_detection: dict[str, Any] = {
            "type": "server_vad",
            "threshold": 0.5,
            "prefix_padding_ms": 300,
            "silence_duration_ms": silence_ms,
            "create_response": True,
            "interrupt_response": True,
        }
        if self._generation == "3.8":
            # 3.8 generation session schema.
            modality = ["text", "audio"] if audio else ["text"]
            session: dict[str, Any] = {
                # Text-only output: the profile renders subtitles, and audio
                # output is billed on top of the text tokens.
                "output_modalities": modality,
                # Docs name `modalities` a 3.5 field, but it is the one the
                # server actually applies: send only `output_modalities` and the
                # echoed session keeps ["text","audio"]. Both names cost nothing.
                "modalities": modality,
                "voice": voice,
                "translation": target,
                "turn_detection": turn_detection,
            }
            # 3.8 documents no source-language field at all: it recognises the
            # spoken language on its own and reports it on the transcript events.
            # `input_audio_transcription.language` is a 3.5-generation field, so
            # a pinned 3.8 source is dropped here rather than guessed at, and the
            # option says so.
        else:
            # 3.5 generation session schema: different names for the same ideas.
            # Without this branch an adapter written for 3.8 would silently lose
            # the target language on a profile that saved the older model id.
            session = {
                "modalities": ["text", "audio"] if audio else ["text"],
                "voice": voice,
                "sample_rate": self.sample_rate,
                "input_audio_format": "pcm",
                "output_audio_format": "pcm",
                "input_audio_transcription": {
                    "model": "qwen3-asr-flash-realtime",
                    **({"language": primary_subtag(pinned)} if pinned else {}),
                },
                "translation": target,
                "turn_detection": turn_detection,
            }
        return {
            "event_id": f"event_{uuid.uuid4().hex}",
            "type": "session.update",
            "session": session,
        }

    @property
    def _generation(self) -> str:
        """Which session schema this model speaks.

        The two generations use different session field names AND different
        event names, and Alibaba documents them as not interchangeable. Reading
        both event shapes is free; writing the wrong session shape is not.
        """
        return "3.8" if self.provider.model.startswith("qwen3.8") else "3.5"

    def _pinned_source_language(self) -> str | None:
        """Source language to pin, if the profile explicitly asked for one.

        Only a 3.5-generation session can carry it: 3.8 documents no pin and
        auto-detects.
        """
        explicit = self.provider.options.get("sourceLanguage")
        return str(explicit) if explicit else None

    def _target_language(self) -> str:
        target = self.provider.translation_target
        if target:
            return primary_subtag(target)
        # Official default is "en"; saying so explicitly keeps a misconfigured
        # profile from silently translating into a language nobody asked for.
        return str(self.provider.options.get("targetLanguage") or "en")

    async def push_pcm(self, chunk: bytes, pcm_offset: float) -> None:
        del pcm_offset
        if not self.ws or self.ws.closed:
            return
        await self.ws.send_json({
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(chunk).decode("ascii"),
        })

    async def flush(self) -> None:
        # Nothing to send: 3.8 segments turns server-side and documents no
        # client-side commit at all, and a manual ``input_audio_buffer.commit``
        # on a server-VAD session would cut the utterance the Provider is about
        # to translate, which is the one thing the native bus cannot realign.
        return None

    async def commit(self) -> None:
        await self.flush()

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.ws and not self.ws.closed:
            # Official shutdown order. Closing without session.finish discards
            # the final utterance's transcript and translation outright.
            with contextlib.suppress(aiohttp.ClientError, RuntimeError):
                await self.ws.send_json({"type": "session.finish"})
            if self._iter_started and not self._finished.is_set():
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._finished.wait(), timeout=self.drain_seconds)
            with contextlib.suppress(aiohttp.ClientError, RuntimeError):
                await self.ws.close()
        if self.session is not None:
            with contextlib.suppress(Exception):
                await self.session.close()

    # -- events ----------------------------------------------------------

    def __aiter__(self) -> AsyncIterator[ASREvent]:
        return self._events()

    async def _events(self) -> AsyncIterator[ASREvent]:
        self._iter_started = True
        if not self.ws:
            return
        try:
            async for message in self.ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    if message.type == aiohttp.WSMsgType.ERROR:
                        yield ASREvent("error", message=str(self.ws.exception()))
                        return
                    continue
                try:
                    raw = json.loads(message.data)
                except json.JSONDecodeError as exc:
                    yield ASREvent("error", message=str(exc))
                    continue
                for event in self._map_event(raw):
                    yield event
                if raw.get("type") == "session.finished":
                    return
        finally:
            self._finished.set()

    def _next_item(self) -> str | None:
        """The utterance the response that just began belongs to.

        No frame links a response to the input item it translates -- response
        events carry the *assistant* item id, a different id from the transcript
        they were made from -- so the pairing can only be positional.  Turn order
        is the position the Provider actually keeps: one response per VAD turn, in
        the order the turns opened, including the turns that carry nothing.

        Queueing off transcript arrivals was wrong and looked like off-by-one
        subtitles.  A breath opens a turn and gets a response, but its transcript
        never emits a delta, so it never entered the queue; the queue then handed
        that turn's response slot to nothing and every following translation
        landed one line early -- source ``おい、真似すんなよ`` showing an empty
        line while the cue before it carried its Chinese.
        """
        while self._turns:
            item = self._turns.popleft()
            try:
                self._awaiting.remove(item)
            except ValueError:
                pass
            return item
        return None

    def _map_event(self, raw: dict[str, Any]) -> list[ASREvent]:
        event_type = str(raw.get("type") or "")
        if event_type == "error":
            error = raw.get("error", raw)
            message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            return [ASREvent("error", message=message, raw=raw)]
        if event_type == "input_audio_buffer.speech_started":
            item = self._item_id(raw)
            self._turns.append(item)
            return [ASREvent(
                "speech_started", begin_pcm=_seconds(raw.get("audio_start_ms")),
                item_id=item, raw=raw,
            )]
        if event_type == "input_audio_buffer.speech_stopped":
            item = self._item_id(raw)
            end = _seconds(raw.get("audio_end_ms"))
            # Timing only. This marker is deliberately *not* a caption close: the
            # server stops the turn while its own transcript is still arriving,
            # and a cue cut at that moment holds text the Provider never
            # aligned a translation to, so it would keep its source and lose its
            # translation until the deadline. `.completed` closes instead.
            return [ASREvent(
                "speech_stopped", end_pcm=end, item_id=item, raw=raw,
            )]
        if event_type == "conversation.item.input_audio_transcription.delta":
            return self._source_delta(raw)
        if event_type == "conversation.item.input_audio_transcription.text":
            return self._source_snapshot(raw)
        if event_type in {
            "conversation.item.input_audio_transcription.completed",
            "conversation.item.input_audio_transcription.done",
        }:
            return self._source_final(raw)
        if event_type == "response.created":
            self._responding = self._next_item()
            return []
        if event_type == "response.text.delta":
            return self._translation_delta(raw)
        if event_type == "response.audio_transcript.delta":
            # The same running translation under the audio+text modality.
            return self._translation_delta(raw)
        if event_type in {"response.text.text", "response.audio_transcript.text"}:
            return self._translation_snapshot(raw)
        if event_type in {"response.text.done", "response.done", "response.audio_transcript.done"}:
            return self._translation_done(raw)
        if event_type == "conversation.item.input_audio_transcription.failed":
            error = raw.get("error", raw)
            message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            return [ASREvent("error", message=f"transcription failed: {message}", raw=raw)]
        return []

    def _item_id(self, raw: dict[str, Any]) -> str:
        item = raw.get("item_id")
        if item:
            self._active_item = str(item)
            return str(item)
        return self._active_item or "0"

    def _segment(self, item: str) -> _Segment:
        segment = self._segments.get(item)
        if segment is None:
            segment = _Segment()
            self._segments[item] = segment
            while len(self._segments) > 256:
                self._segments.popitem(last=False)
        return segment

    def _settle_boundary(self, segment: _Segment) -> None:
        """Freeze how far the two streams have each reached, as they reached it.

        This protocol states no alignment points, so the ledger used to be able to
        match only a whole closed segment: every caption the chunker cut at its hard
        deadline lost its Chinese (measured on four real captures 2026-09-21 -- 0 of
        5 and 1 of 6 cut cues kept a translation). Both strings here are the
        Provider's own output, and the pair is frozen on either stream growing, so
        whichever text a cue is cut on it has a boundary to land on. Freezing on the
        transcript can only pair it with *less* translation than the model eventually
        writes for that text -- the line trails, and the rest surfaces on the next
        cue -- which is the direction the projection line already assumes. It can
        never hand a cue Chinese the model has not said yet.
        """
        if not segment.source or not segment.translation:
            return
        pair = (segment.source, segment.translation)
        if not segment.anchors or segment.anchors[-1] != pair:
            segment.anchors = (*segment.anchors[-(_BOUNDARIES_MAX - 1):], pair)

    def _source_delta(self, raw: dict[str, Any]) -> list[ASREvent]:
        item = self._item_id(raw)
        segment = self._segment(item)
        # Official instruction for this event is to concatenate `delta`; it is
        # never a replacement of the accumulated text.
        segment.source += str(raw.get("delta") or "")
        if item not in self._awaiting and item != self._responding:
            self._awaiting.append(item)
        return [self._source_event(raw, item, segment.source)]

    def _source_snapshot(self, raw: dict[str, Any]) -> list[ASREvent]:
        """3.5-generation running transcript (``text``, with a tentative ``stash``).

        The ``stash`` is the part the model may still rewrite, so it stays out of
        the caption for the same reason it stays out of the ledger on Soniox.
        """
        item = self._item_id(raw)
        segment = self._segment(item)
        text = str(raw.get("text") or raw.get("transcript") or "")
        if text:
            segment.source = text
        if item not in self._awaiting and item != self._responding:
            self._awaiting.append(item)
        return [self._source_event(raw, item, segment.source)]

    def _source_final(self, raw: dict[str, Any]) -> list[ASREvent]:
        """The Provider's own end of turn, which is where a caption closes.

        ``transcript`` here is the complete utterance, so the cue built from it and
        the segment the ledger holds for this item carry the same string by
        construction rather than by luck. That equality is what the session-backed
        join uses for a caption the Provider finished; ``_settle_boundary`` covers
        the captions it never got to. The translation for this turn is still
        coming, so this stays an interim for the ledger's sake: only the response
        completion closes the segment.
        """
        item = self._item_id(raw)
        segment = self._segment(item)
        text = str(raw.get("transcript") or raw.get("text") or "")
        if text:
            segment.source = text
        self._settle_boundary(segment)
        if item not in self._awaiting and item != self._responding:
            self._awaiting.append(item)
        return [ASREvent(
            "interim",
            text=segment.source,
            language=canonicalize_tag_or_none(raw.get("language")),
            item_id=item,
            raw=raw,
            translation=segment.translation,
            translation_anchors=segment.anchors,
            caption_observation=CaptionObservation(
                "utterance_final", 0, item, stable_text=segment.source
            ),
        )]

    def _source_event(self, raw: dict[str, Any], item: str, text: str) -> ASREvent:
        segment = self._segment(item)
        self._settle_boundary(segment)
        return ASREvent(
            "interim",
            text=text,
            language=canonicalize_tag_or_none(raw.get("language")),
            item_id=item,
            raw=raw,
            # The ledger replaces rather than merges, so any frame that carries an
            # alignment has to carry the translation it belongs to as well.
            translation=segment.translation or None,
            translation_anchors=segment.anchors,
            # The transcript is delivered as a monotone running snapshot. 3.8 has
            # no tentative/tail channel at all (that is the 3.5 ``stash``), so
            # there is nothing here that is explicitly revisable.
            caption_observation=CaptionObservation(
                "stable_prefix_snapshot", 0, item, stable_text=text
            ),
        )

    def _translation_delta(self, raw: dict[str, Any]) -> list[ASREvent]:
        if self._responding is None:
            # A response began before any `response.created` was seen: the oldest
            # untranslated utterance owns it, which is the arrival order.
            self._responding = self._awaiting.popleft() if self._awaiting else self._active_item
        if self._responding is None:
            return []
        segment = self._segment(self._responding)
        segment.translation += str(raw.get("delta") or "")
        self._settle_boundary(segment)
        # Published as it streams, not only when the response finishes: a
        # speaker who never pauses is cut by the caption chunker's hard deadline
        # mid-utterance, and that cue can only be resolved by a partial
        # translation. The event carries no caption evidence, so it never
        # re-segments anything.
        return [ASREvent(
            "translation",
            text=segment.source,
            translation=segment.translation,
            translation_anchors=segment.anchors,
            item_id=self._responding,
            raw=raw,
        )]

    def _translation_snapshot(self, raw: dict[str, Any]) -> list[ASREvent]:
        """3.5 sends its translation as a running ``text`` instead of deltas.

        Written as a replace because it already contains everything said so far;
        appending it to itself is how a later cue ends up with half a sentence
        duplicated.
        """
        if self._responding is None:
            self._responding = self._awaiting.popleft() if self._awaiting else self._active_item
        if self._responding is None:
            return []
        segment = self._segment(self._responding)
        text = self._final_text(raw)
        if text:
            segment.translation = text
        self._settle_boundary(segment)
        return [ASREvent(
            "translation",
            text=segment.source,
            translation=segment.translation,
            translation_anchors=segment.anchors,
            item_id=self._responding,
            raw=raw,
        )]

    def _translation_done(self, raw: dict[str, Any]) -> list[ASREvent]:
        item = self._responding
        self._responding = None
        if item is None:
            return []
        segment = self._segment(item)
        text = self._final_text(raw)
        # The completion is the Provider's own last word, and the live stream
        # shows it can carry punctuation the deltas did not: taking it only when
        # nothing arrived would drop a full stop off a finished subtitle, while
        # taking it unconditionally would erase a text the deltas had already
        # built whenever the completion arrives empty -- which is what 3.8 sends.
        if text and len(text) >= len(segment.translation):
            segment.translation = text
        self._settle_boundary(segment)
        if not segment.source and not segment.translation:
            return []
        # The caption was usually already closed by the transcript; the chunker's
        # item ledger keeps closed items precisely so this second final is
        # deduplicated instead of published again. An item whose transcript never
        # completed is released here, and either way the ledger now knows this
        # segment is finished, which is what lets a waiting cue resolve rather
        # than ride out its deadline.
        return [ASREvent(
            "final",
            text=segment.source,
            translation=segment.translation.strip(),
            translation_anchors=segment.anchors,
            item_id=item,
            raw=raw,
            caption_observation=CaptionObservation(
                "utterance_final", 0, item, stable_text=segment.source
            ),
        )]

    @staticmethod
    def _final_text(raw: dict[str, Any]) -> str:
        """Final translation text from whichever completion event arrived.

        ``response.text.done`` carries ``text``, ``response.audio_transcript.done``
        carries ``transcript``, and ``response.done`` carries neither -- its text
        lives in ``response.output[].content[]``. Reading all three keeps one
        adapter working across the generation split.
        """
        for key in ("text", "transcript"):
            value = raw.get(key)
            if value:
                return str(value)
        response = raw.get("response")
        if isinstance(response, dict):
            for output in response.get("output") or []:
                if not isinstance(output, dict):
                    continue
                for content in output.get("content") or []:
                    if not isinstance(content, dict):
                        continue
                    for key in ("text", "transcript"):
                        if content.get(key):
                            return str(content[key])
        return ""


def _seconds(value: Any) -> float | None:
    return float(value) / 1000.0 if isinstance(value, (int, float)) else None
