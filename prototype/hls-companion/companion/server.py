#!/usr/bin/env python3
"""Local HTTP companion for LingerLens HLS Prototype 2."""

from __future__ import annotations

import argparse
import asyncio
import copy
import errno
import json
import os
import secrets
import stat
import sys
import tempfile
import threading
import time
import urllib.parse
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aiohttp import web

MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

try:
    from . import logbook  # type: ignore[import-not-found]
    from .control_ipc import ControlServer  # type: ignore[import-not-found]
    from .ytdlp_ingest import YtDlpLiveIngest  # type: ignore[import-not-found]
    from .languages import LanguageNotSupportedError, canonicalize_target_tag, catalog_entries, language_catalog  # type: ignore[import-not-found]
    from .providers import create_asr, create_translation  # type: ignore[import-not-found]
    from .providers.base import SourceLanguagePolicy, StreamMeta, provider_currency, validate_source_policy, validate_translation_pair  # type: ignore[import-not-found]
    from .providers.config import load_config, masked_config, model_settings_view, update_config, update_model_settings  # type: ignore[import-not-found]
    from .providers.fallback import FallbackChain  # type: ignore[import-not-found]
    from .providers.native_session import NativeSessionTranslation, NativeTranslationBus  # type: ignore[import-not-found]
    from .subtitle_pipeline import SubtitlePipeline  # type: ignore[import-not-found]
    from .subtitle_store import CueStore  # type: ignore[import-not-found]
    from .capture_clock import CaptureClock  # type: ignore[import-not-found]
    from .auth_lease import AuthLease, AuthLeaseManager, SessionAuthLease  # type: ignore[import-not-found]
    from .live_messages import LiveMessage, LiveMessageStore  # type: ignore[import-not-found]
    from .message_translator import MessageTranslator  # type: ignore[import-not-found]
    from .youtube_chat_ingest import YouTubeChatIngest  # type: ignore[import-not-found]
    from .bilibili_danmaku_ingest import BilibiliDanmakuIngest  # type: ignore[import-not-found]
    from .twitch_chat_ingest import TwitchChatIngest  # type: ignore[import-not-found]
    from .core import (
        AuthenticationProvider,
        BrowserCookieSnapshot,
        DevelopmentBrowserProfileFallback,
        LiveSession,
        ProbeInfoSnapshot,
        YtDlpProbe,
        build_ffmpeg_command,
        build_quality_options,
        normalize_imported_cookies,
        parse_header_cookies,
        parse_name_value_lines,
        parse_netscape_cookies,
        select_quality,
        selected_inputs,
        validate_page_url,
    )
    from .media_anchor import MediaAnchor  # type: ignore[import-not-found]
    from .source_timeline import PTS_HZ, signed_pts_delta  # type: ignore[import-not-found]
except ImportError:  # Direct script execution.
    from companion import logbook  # type: ignore[import-not-found]
    from control_ipc import ControlServer  # type: ignore[import-not-found]
    from ytdlp_ingest import YtDlpLiveIngest  # type: ignore[import-not-found]
    from companion.languages import LanguageNotSupportedError, canonicalize_target_tag, catalog_entries, language_catalog  # type: ignore[import-not-found]
    from companion.providers import create_asr, create_translation  # type: ignore[import-not-found]
    from companion.providers.base import SourceLanguagePolicy, StreamMeta, provider_currency, validate_source_policy, validate_translation_pair  # type: ignore[import-not-found]
    from companion.providers.config import load_config, masked_config, model_settings_view, update_config, update_model_settings  # type: ignore[import-not-found]
    from companion.providers.fallback import FallbackChain  # type: ignore[import-not-found]
    from companion.subtitle_pipeline import SubtitlePipeline  # type: ignore[import-not-found]
    from companion.subtitle_store import CueStore  # type: ignore[import-not-found]
    from companion.capture_clock import CaptureClock  # type: ignore[import-not-found]
    from companion.auth_lease import AuthLease, AuthLeaseManager, SessionAuthLease  # type: ignore[import-not-found]
    from companion.live_messages import LiveMessage, LiveMessageStore  # type: ignore[import-not-found]
    from companion.message_translator import MessageTranslator  # type: ignore[import-not-found]
    from companion.youtube_chat_ingest import YouTubeChatIngest  # type: ignore[import-not-found]
    from companion.bilibili_danmaku_ingest import BilibiliDanmakuIngest  # type: ignore[import-not-found]
    from companion.twitch_chat_ingest import TwitchChatIngest  # type: ignore[import-not-found]
    from core import (  # type: ignore[import-not-found]
        AuthenticationProvider,
        BrowserCookieSnapshot,
        DevelopmentBrowserProfileFallback,
        LiveSession,
        ProbeInfoSnapshot,
        YtDlpProbe,
        build_ffmpeg_command,
        build_quality_options,
        normalize_imported_cookies,
        parse_header_cookies,
        parse_name_value_lines,
        parse_netscape_cookies,
        select_quality,
        selected_inputs,
        validate_page_url,
    )
    from companion.media_anchor import MediaAnchor  # type: ignore[import-not-found]
    from companion.source_timeline import PTS_HZ, signed_pts_delta  # type: ignore[import-not-found]

ROOT = Path(__file__).resolve().parents[1]
WEB_PLAYER = ROOT / "web-player"
DEFAULT_RUNTIME = ROOT / "runtime" / "media"
DEFAULT_PROVIDERS = ROOT / "runtime" / "providers.json"
# How long an /api/probe extraction may be reused by /api/start. Live manifests
# are signed and the live edge keeps moving, so this stays far below any expiry.
PROBE_INFO_TTL_SECONDS = 90.0
DEFAULT_TARGET_DELAY_SECONDS = 15.0
MIN_TARGET_DELAY_SECONDS = 11.0
PLAYER_LIVE_SYNC_SECONDS = 12.0
# Measured startup variance for a live source: yt-dlp first bytes + ffmpeg
# probe + first complete segment can take 15-25s on a slow/high-bitrate
# stream. 8s made subtitle startup a race that silently dropped subtitles
# exactly on the streams most likely to be slow (2026-09-08 perf experiment).
PRIVATE_HLS_READY_TIMEOUT_SECONDS = 30.0

# A healthy live stream can legitimately be quiet for one segment interval.
# The supervisor waits beyond the same segment-scaled threshold used by the
# browser before rebuilding the *whole* session. Rebuilding one leg alone would
# give the subtitle mapper a new clock and could shift every later cue.
SESSION_RECOVERY_POLL_SECONDS = 2.0
SESSION_RECOVERY_MAX_ATTEMPTS = 3
SESSION_RECOVERY_BACKOFF_SECONDS = (2.0, 5.0, 10.0)
SESSION_RECOVERY_STARTUP_GRACE_SECONDS = PRIVATE_HLS_READY_TIMEOUT_SECONDS + 15.0
SESSION_START_RETRY_BACKOFF_SECONDS = (1.0, 3.0)

# The desktop launcher may seed these variables with the Windows system proxy.
# Each probe/start request then chooses one explicit mode.  Restoring this
# snapshot lets "system proxy" work while still making "direct" remove a
# proxy selected for an earlier session.
_PROXY_ENV_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
_INITIAL_PROXY_ENV = {name: os.environ.get(name) for name in _PROXY_ENV_NAMES}

# Without `-copyts`, yt-dlp's ffmpeg downloader lets its mpegts muxer re-base
# each leg's output to the muxer's own default origin -- measured at exactly
# 1.400s for video and 1.3787s for audio (one AAC frame earlier, 1024/48000).
# That artifact is a constant of the muxer, not of the stream, so a first PTS
# below this threshold means the leg has no absolute clock at all.
#
# The threshold deliberately does NOT test for "hours": a stream that began
# minutes ago legitimately reports an absolute origin of a few hundred seconds
# (measured 216s live on a freshly started ANNnewsCH stream, tracking wall clock
# across two launches 50s apart). Gating on magnitude rather than on the rebase
# artifact would silently disable the exact offset for every young stream.
MPEGTS_REBASE_ORIGIN = 2.0

# The two legs are launched by the same start() call, milliseconds apart, and
# each HLS reader begins at a live-window boundary, so their origins can differ
# only by a few segment durations. Measured: 0.014s between the media leg's own
# two pumps, 5.006s and 5.016s between the ASR leg and the video leg, and
# 15.023s when a leg was deliberately started 12s late.
#
# The bound guards the one case that would otherwise look perfectly valid: the
# source clock is 33 bits at 90kHz and wraps every 26.5 hours, so a wrap landing
# between the two legs' first packets leaves one leg reporting ~95443s and the
# other ~0 -- both "absolute", both plausible, and their difference wrong by
# 26.5 hours. Refusing an implausible skew falls back to the sampled window,
# which is a safe answer, whereas the subtraction would not be.
SOURCE_CLOCK_MAX_LEG_SKEW = 600.0


SUPPORTED_AUTH_PLATFORMS = {
    "youtube": {
        "defaultDomain": ".youtube.com",
        "critical": (
            "SID",
            "HSID",
            "SSID",
            "APISID",
            "SAPISID",
            "LOGIN_INFO",
            "__Secure-1PSID",
            "__Secure-3PSID",
            "__Secure-1PSIDTS",
            "__Secure-3PSIDTS",
            "__Secure-1PSIDCC",
            "__Secure-3PSIDCC",
        ),
    },
    "bilibili": {
        "defaultDomain": ".bilibili.com",
        "critical": ("SESSDATA",),
    },
    "twitch": {
        "defaultDomain": ".twitch.tv",
        "critical": (),
    },
}


class CompanionApplication:
    def __init__(self, args: argparse.Namespace, *, enable_native_control: bool = True):
        self.args = args
        self.probe = YtDlpProbe()
        self.session = LiveSession(args.runtime_dir)
        self.info_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self.auth_snapshots: dict[str, list[dict[str, Any]]] = {}
        self.auth_lock = threading.Lock()
        self._session_lock = asyncio.Lock()
        self.control = ControlServer(self.handle_control) if enable_native_control else None
        self.source_ingest: YtDlpLiveIngest | None = None
        # P3-B: dedicated tiny audio-only download leg feeding the subtitle
        # pipeline, independent of the video packaging path's health.
        self.asr_audio_ingest: YtDlpLiveIngest | None = None
        self._asr_audio_leg = False
        # The (audio, video) source-clock origin pair the exact offset was
        # latched from; a later mismatch means a leg re-based and the exact
        # offset is refused for the rest of the session.
        self._source_clock_origins: tuple[float, float] | None = None
        self._source_clock_logged = False
        # R1: why this session's source clock was declared unusable, if it was.
        # None means "not refused" -- which covers both "trustworthy" and "not
        # measured yet", and the sampled fallback depends on that difference.
        self._exact_mapping_refused: str | None = None
        self.providers_path = args.providers_file
        self.providers_config = load_config(self.providers_path)
        self.auth_file = Path(args.providers_file).parent / "auth-snapshot.json"
        self.persisted_auth: dict[str, list[dict[str, Any]]] = {}
        self.persisted_auth = self._load_persisted_auth()
        self.subtitle_pipeline: SubtitlePipeline | None = None
        self.subtitle_store = CueStore()
        self.subtitle_last_error: str | None = None
        self.target_delay_seconds = DEFAULT_TARGET_DELAY_SECONDS
        self.message_store = LiveMessageStore()
        self.message_ingest: Any = None
        self.message_translator: MessageTranslator | None = None
        self.message_last_error: str | None = None
        self.message_translate_enabled = False
        self.message_generation = 0
        self.auth_lease: SessionAuthLease | None = None
        self.private_hls_token: str | None = None
        # The supervisor replays only this non-secret start description. Cookie
        # values stay in the short-lived in-memory auth fields below; they are
        # never copied into status, logs, or the replay body.
        self._active_start_body: dict[str, Any] | None = None
        self._recovery_auth_cookies: list[dict[str, Any]] | None = None
        self._recovery_auth_browser: str | None = None
        self._recovery_task: asyncio.Task[None] | None = None
        self._recovery_in_progress = False
        self._recovery_attempts = 0
        self._recovery_state = "idle"
        self._recovery_reason: str | None = None
        self._recovery_error: str | None = None
        # Low-rate subtitle timing snapshots make a real live delay traceable
        # without flooding the diagnostics ring or recording caption content.
        self._last_subtitle_diag_monotonic = 0.0

    def routes(self) -> web.Application:
        app = web.Application(client_max_size=4 * 1024 * 1024, middlewares=[local_request_guard])
        app["companion"] = self
        app.router.add_get("/", self.index)
        app.router.add_get("/favicon.ico", self.favicon)
        app.router.add_get("/player.js", self.static_file)
        app.router.add_get("/ui-bootstrap.js", self.static_file)
        app.router.add_get("/language-selector.js", self.static_file)
        app.router.add_get("/subtitle-scheduler.js", self.static_file)
        app.router.add_get("/subtitle-render-loop.js", self.static_file)
        app.router.add_get("/subtitle-window-controller.js", self.static_file)
        app.router.add_get("/media-clock.js", self.static_file)
        app.router.add_get("/poll-loop.js", self.static_file)
        app.router.add_get("/quality-preference.js", self.static_file)
        app.router.add_get("/live-messages-client.js", self.static_file)
        app.router.add_get("/workbench-controller.js", self.static_file)
        app.router.add_get("/pane-resizer.js", self.static_file)
        app.router.add_get("/playback-recovery.js", self.static_file)
        app.router.add_get("/i18n.js", self.static_file)
        app.router.add_get("/control-bar.js", self.static_file)
        app.router.add_get("/diagnostics-log.js", self.static_file)
        app.router.add_get("/style.css", self.static_file)
        app.router.add_get("/fonts.css", self.static_file)
        app.router.add_get("/fonts/{slug}/{name}", self.font_file)
        app.router.add_get("/vendor/{name}", self.static_file)
        app.router.add_get("/hls/{name}", self.hls_file)
        app.router.add_get("/_private-hls/{token}/{name}", self.private_hls_file)
        app.router.add_get("/api/status", self.status)
        app.router.add_get("/api/subtitles", self.handle_subtitles)
        app.router.add_get("/api/live-messages", self.handle_live_messages)
        app.router.add_post("/api/live-messages/settings", self.handle_live_message_settings)
        app.router.add_get("/api/messages", self.handle_live_messages)
        app.router.add_get("/api/messages/status", self.handle_messages_status)
        app.router.add_post("/api/target-delay", self.handle_target_delay)
        app.router.add_get("/api/providers", self.handle_get_providers)
        app.router.add_post("/api/providers", self.handle_update_providers)
        app.router.add_get("/api/languages", self.handle_languages)
        app.router.add_get("/api/model-settings", self.handle_get_model_settings)
        app.router.add_post("/api/model-settings", self.handle_update_model_settings)
        app.router.add_post("/api/auth-cookies", self.handle_import_cookies)
        app.router.add_post("/api/probe", self.handle_probe)
        app.router.add_post("/api/start", self.handle_start)
        app.router.add_post("/api/stop", self.handle_stop)
        app.router.add_get("/api/logs", self.handle_logs)
        app.on_startup.append(self.startup)
        app.on_cleanup.append(self.cleanup)
        return app

    async def index(self, _: web.Request) -> web.FileResponse:
        return web.FileResponse(WEB_PLAYER / "index.html", headers={"Cache-Control": "no-store"})

    async def favicon(self, _: web.Request) -> web.Response:
        return web.Response(status=204)

    async def static_file(self, request: web.Request) -> web.FileResponse:
        name = request.match_info.get("name") or Path(request.path).name
        base = WEB_PLAYER / "vendor" if request.path.startswith("/vendor/") else WEB_PLAYER
        path = (base / name).resolve()
        if base.resolve() not in path.parents or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": "no-store, max-age=0"})

    async def font_file(self, request: web.Request) -> web.FileResponse:
        """Serve the vendored UI webfonts.

        The binaries are produced by scripts/fetch-fonts.py and live under
        web-player/fonts/<family-slug>/<subset>-<weight>.woff2. Both path
        segments are validated and the resolved path must stay inside that
        directory, so the route cannot be used to read anything else.
        """
        slug = request.match_info["slug"]
        name = request.match_info["name"]
        if (
            not name.endswith(".woff2")
            or Path(name).name != name
            or Path(slug).name != slug
        ):
            raise web.HTTPNotFound()
        base = (WEB_PLAYER / "fonts").resolve()
        path = (base / slug / name).resolve()
        if base not in path.parents or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": "no-store, max-age=0"})

    async def hls_file(self, request: web.Request) -> web.FileResponse:
        name = request.match_info["name"]
        if not name.endswith((".m3u8", ".m4s", ".mp4")) or Path(name).name != name:
            raise web.HTTPNotFound()
        path = self.session.public_dir / name
        if not path.exists():
            raise web.HTTPNotFound()
        headers = {"Cache-Control": "no-store, max-age=0"} if name.endswith(".m3u8") else {"Cache-Control": "private, max-age=30"}
        return web.FileResponse(path, headers=headers)

    async def private_hls_file(self, request: web.Request) -> web.FileResponse:
        """Serve the active pre-delay HLS only to its unguessable FFmpeg URL."""
        token = self.private_hls_token
        if token is None or not secrets.compare_digest(request.match_info["token"], token):
            raise web.HTTPNotFound()
        name = request.match_info["name"]
        if not name.endswith((".m3u8", ".m4s", ".mp4")) or Path(name).name != name:
            raise web.HTTPNotFound()
        path = self.session.private_dir / name
        if not path.is_file():
            raise web.HTTPNotFound()
        headers = {"Cache-Control": "no-store, max-age=0"} if name.endswith(".m3u8") else {"Cache-Control": "private, max-age=30"}
        return web.FileResponse(path, headers=headers)

    async def status(self, request: web.Request) -> web.Response:
        status = self.session.status()
        # The renderer states its playhead with this poll, once a second. It is
        # the only trustworthy source for it: hls.js fetches segments 6-15s ahead
        # of the playhead (measured 2026-09-18), so request paths cannot stand in
        # for it. A poll without the parameter leaves the last value in place.
        if self.subtitle_pipeline is not None:
            self.subtitle_pipeline.set_viewer_wall_time(request.query.get("playhead"))
        ingest_snapshot = self.source_ingest.snapshot() if self.source_ingest else None
        # The local re-packaged PDT is not the broadcaster's capture clock.
        # Neither a missing measurement nor the 12s player setting proves zero.
        status["sourceDelaySeconds"] = None
        # The content position the subtitle anchor samples for the video side,
        # next to privateMediaSeconds (the packaged counter) so the difference
        # between them -- the packaging backlog -- is readable from status.
        # Reuses the snapshot above rather than taking a second one.
        status["videoContentSeconds"] = self._video_content_seconds(ingest_snapshot)
        ingest_list = [dict(ingest_snapshot, role="media")] if ingest_snapshot else []
        if self.asr_audio_ingest is not None:
            ingest_list.append(dict(self.asr_audio_ingest.snapshot(), role="asr-audio"))
        status["sourceIngest"] = ingest_list
        status["sessionRecovery"] = self._recovery_status()
        # `sessionRecovery` is deliberately separate from source telemetry. It
        # reports the bounded whole-session supervisor above; it never promises
        # that a single download leg was restarted. Rebuilding one leg alone
        # would reset its source clock and could shift later subtitle cues.
        subtitle_status = self._subtitle_status()
        message_status = self._messages_status()
        now_monotonic = time.monotonic()
        if now_monotonic - self._last_subtitle_diag_monotonic >= 5.0:
            self._last_subtitle_diag_monotonic = now_monotonic
            logbook.record(
                "info",
                "subtitle-timing",
                json.dumps(
                    {
                        "backlog": subtitle_status.get("translationBacklog"),
                        "queueP95": subtitle_status.get("translationQueueDelayP95"),
                        "providerP95": subtitle_status.get("translationProviderDelayP95"),
                        "processingP95": subtitle_status.get("translationProcessingP95"),
                        "readyP95": subtitle_status.get("translationSuccessReadyLagP95"),
                        "totalReadyP95": subtitle_status.get("totalReadyDelayP95"),
                        "viewerLead": subtitle_status.get("viewerLeadSeconds"),
                        "anchor": subtitle_status.get("anchorCorrection"),
                        "anchorOffset": (subtitle_status.get("mediaAnchor") or {}).get("offset")
                        if isinstance(subtitle_status.get("mediaAnchor"), dict)
                        else None,
                        "workers": subtitle_status.get("translationWorkersAlive"),
                        "nativeWaiting": subtitle_status.get("nativeTranslationWaiting"),
                        "nativeUnaligned": subtitle_status.get("nativeTranslationUnaligned"),
                        "nativeNoTranslation": subtitle_status.get("nativeSegmentClosedWithoutTranslation"),
                        "nativeFallback": subtitle_status.get("nativeFallbackUsed"),
                        "latePatched": subtitle_status.get("lateTranslationPatched"),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
        status["subtitles"] = subtitle_status
        status["mediaClock"] = self._media_clock_status()
        status["liveMessages"] = message_status
        status["usage"] = self._usage_status(subtitle_status, message_status)
        status["targetDelaySeconds"] = self.target_delay_seconds
        status["estimatedTotalDelaySeconds"] = None
        status.pop("publishDelaySeconds", None)
        if ingest_snapshot and ingest_snapshot.get("sourceError") and status.get("state") == "running":
            status["state"] = "error"
            status["error"] = ingest_snapshot["sourceError"]
        if status.get("state") == "idle" and self._recovery_state == "failed":
            status["state"] = "error"
            status["error"] = self._recovery_error or "直播会话恢复失败，请重新开始播放"
        return web.json_response(status, headers={"Cache-Control": "no-store"})

    async def handle_logs(self, request: web.Request) -> web.Response:
        """Poll the diagnostic ring.

        ``afterSeq`` is parsed leniently on purpose: a UI that loses its cursor
        (or sends garbage) gets the whole ring back instead of a 400, because
        this route is how the user finds out what went wrong elsewhere.
        """
        return web.json_response(
            logbook.snapshot(request.query.get("afterSeq")),
            headers={"Cache-Control": "no-store"},
        )

    async def handle_subtitles(self, request: web.Request) -> web.Response:
        try:
            after_seq = int(request.query.get("afterSeq", "0"))
        except ValueError as exc:
            raise ValueError("invalid subtitle cursor") from exc
        if after_seq < 0:
            raise ValueError("invalid subtitle cursor")
        pipeline_status = self._subtitle_status()
        pipeline = self.subtitle_pipeline
        if pipeline is not None and request.query.get("playhead") is not None and request.query.get("mediaSessionId") and request.query.get("mediaSessionId") == self.session.media_session_id:
            pipeline.set_viewer_wall_time(request.query.get("playhead"))
        return web.json_response(
            {
                "now": time.time(),
                "pdtEpoch": pipeline_status.get("pdtEpoch"),
                "mediaSessionId": self.session.media_session_id,
                "cues": [cue.to_dict() for cue in self.subtitle_store.query(after_seq=after_seq)],
                "maxSeq": self.subtitle_store.max_seq,
                # Recognized but not yet released: the player may draw this, but it
                # is not a cue and never reaches the subtitle list or an export.
                "draft": pipeline.caption_draft() if pipeline is not None else None,
                "drafts": getattr(pipeline, "caption_drafts", lambda: [])(),
                "stats": pipeline_status,
            },
            headers={"Cache-Control": "no-store"},
        )

    async def handle_live_messages(self, request: web.Request) -> web.Response:
        try:
            after_seq = int(request.query.get("afterSeq", request.query.get("cursor", "0")))
        except ValueError as exc:
            raise ValueError("invalid live-message cursor") from exc
        if after_seq < 0:
            raise ValueError("invalid live-message cursor")
        self.message_store.flush_pending()
        return web.json_response(
            {
                "messages": self.message_store.query(after_seq=after_seq),
                "maxSeq": self.message_store.max_seq,
                "translate": self.message_translate_enabled,
                "stats": self._messages_status(),
            },
            headers={"Cache-Control": "no-store"},
        )

    handle_messages = handle_live_messages

    async def handle_live_message_settings(self, request: web.Request) -> web.Response:
        if self.message_ingest is None:
            raise RuntimeError("no active live-message session")
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("translate"), bool):
            raise ValueError("translate must be a boolean")
        enabled = bool(body["translate"])
        if enabled and self.message_translator is None:
            self.message_translator = await self._create_message_translator()
        self.message_translate_enabled = enabled
        if self.message_translator:
            self.message_translator.set_enabled(enabled)
        return web.json_response(
            {"translate": enabled, "stats": self._messages_status()},
            headers={"Cache-Control": "no-store"},
        )

    async def handle_messages_status(self, _: web.Request) -> web.Response:
        return web.json_response(self._messages_status(), headers={"Cache-Control": "no-store"})

    async def handle_target_delay(self, request: web.Request) -> web.Response:
        body = await request.json()
        seconds = self._target_delay(body.get("seconds"))
        publisher = self.session.publisher
        if publisher is None:
            raise RuntimeError("no active live session")
        self.target_delay_seconds = seconds
        publisher.publish_delay = self._publisher_delay(seconds)
        status = self.session.status()
        status.pop("publishDelaySeconds", None)
        return web.json_response({"ok": True, "targetDelaySeconds": seconds, "status": status}, headers={"Cache-Control": "no-store"})

    @staticmethod
    def _target_delay(value: Any) -> float:
        if value is None:
            return DEFAULT_TARGET_DELAY_SECONDS
        try:
            seconds = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("target total delay must be a number of seconds") from exc
        if not MIN_TARGET_DELAY_SECONDS <= seconds <= 60.0:
            raise ValueError("target total delay must be between 11 and 60 seconds")
        return seconds

    @staticmethod
    def _publisher_delay(target_delay: float) -> float:
        return max(0.0, target_delay - PLAYER_LIVE_SYNC_SECONDS)

    async def handle_import_cookies(self, request: web.Request) -> web.Response:
        self._require_local_request(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("cookie import must be an object")
        platform = str(body.get("platform") or "youtube").lower()
        platform_config = SUPPORTED_AUTH_PLATFORMS.get(platform)
        if platform_config is None:
            raise ValueError("platform must be youtube, bilibili, or twitch")
        domain = str(body.get("domain") or platform_config["defaultDomain"])
        if body.get("netscape"):
            raw = parse_netscape_cookies(str(body["netscape"]))
        elif body.get("lines"):
            raw = parse_name_value_lines(str(body["lines"]), domain)
        elif body.get("header"):
            raw = parse_header_cookies(str(body["header"]), domain)
        else:
            raise ValueError("Provide either a Netscape cookies.txt export or a cookie header")
        cookies = [
            cookie
            for cookie in normalize_imported_cookies(raw)
            if self._platform_for_cookie_domain(cookie.get("domain")) == platform
        ]
        if not cookies:
            raise ValueError("No usable YouTube/Google/Bilibili/Twitch cookies were found in the import")
        token = secrets.token_urlsafe(24)
        with self.auth_lock:
            self.auth_snapshots[token] = cookies
        persisted = self._persist_auth(platform, cookies)
        names = sorted({cookie["name"] for cookie in cookies})
        missing_critical = [name for name in platform_config["critical"] if name not in names]
        return web.json_response(
            {
                "ok": True,
                "platform": platform,
                "authToken": token,
                "accepted": len(cookies),
                "skipped": len(raw) - len(cookies),
                "persisted": persisted,
                "names": names,
                "missingCritical": missing_critical,
            },
            headers={"Cache-Control": "no-store"},
        )

    def _require_local_request(self, request: web.Request) -> None:
        require_local_request(request)

    async def handle_get_model_settings(self, request: web.Request) -> web.Response:
        self._require_local_request(request)
        self.providers_config = load_config(self.providers_path)
        return web.json_response(model_settings_view(self.providers_config, path=self.providers_path), headers={"Cache-Control": "no-store"})

    async def handle_update_model_settings(self, request: web.Request) -> web.Response:
        self._require_local_request(request)
        settings = await request.json()
        if not isinstance(settings, dict):
            raise ValueError("model settings must be an object")
        self.providers_config = update_model_settings(self.providers_path, settings)
        return web.json_response(model_settings_view(self.providers_config, path=self.providers_path), headers={"Cache-Control": "no-store"})

    async def handle_get_providers(self, _: web.Request) -> web.Response:
        self.providers_config = load_config(self.providers_path)
        return web.json_response(masked_config(self.providers_config), headers={"Cache-Control": "no-store"})

    async def handle_languages(self, _: web.Request) -> web.Response:
        """Language catalog plus the effective capabilities of active Providers.

        The catalog is language identity data; whether the active ASR and
        translation profiles support a tag/pair is reported separately so the
        UI can block unsupported settings before playback starts.
        """
        config = load_config(self.providers_path)
        asr_record = self._provider_record(config["asr"], config["asr"]["active"])
        translation_record = self._provider_record(config["translation"], config["translation"]["active"])
        asr_provider = create_asr(asr_record)
        native = asr_provider.capabilities.native_translation
        if native.enabled:
            # The Profile translates on its own session, so the effective
            # translation capability the UI must validate against is the one the
            # session-backed Provider will assert -- not the idle LLM profile's.
            translation_provider = NativeSessionTranslation(
                NativeTranslationBus(),
                provider_id=f"{asr_provider.id}:native",
                label=f"{asr_provider.label}（Provider 内置翻译）",
                model=asr_provider.model,
                target_tags=native.target_tags,
            )
        else:
            translation_provider = create_translation(translation_record)
        subtitle = config.get("subtitle", {})
        asr_language = asr_provider.capabilities.language
        translation_language = translation_provider.capabilities.language
        return web.json_response(
            {
                "catalogVersion": language_catalog().get("version"),
                "cldrVersion": language_catalog().get("cldrVersion"),
                "languages": catalog_entries(),
                "asr": {
                    "providerId": asr_provider.id,
                    "model": asr_provider.model,
                    "label": asr_provider.label,
                    "language": {
                        "supportedTags": list(asr_language.supported_tags) if asr_language.supported_tags is not None else None,
                        "detection": asr_language.detection,
                        "maxCandidates": asr_language.max_candidates,
                        "reportsDetectedLanguage": asr_language.reports_detected_language,
                        "codeSwitching": asr_language.code_switching,
                        "tier": asr_language.tier,
                        "detectionTags": list(asr_language.detection_tags) if asr_language.detection_tags is not None else None,
                    },
                    "sampleRates": list(asr_provider.capabilities.sample_rates),
                    "preferredSampleRate": asr_provider.capabilities.preferred_sample_rate,
                },
                "translation": {
                    "providerId": translation_provider.id,
                    "model": translation_provider.model,
                    "label": translation_provider.label,
                    # True when the ASR Profile translates on its own session
                    # (Soniox translation, Qwen LiveTranslate). The UI uses it to
                    # say that no translation model is being called.
                    "native": bool(native.enabled),
                    "language": {
                        "sourceTags": list(translation_language.source_tags) if translation_language.source_tags is not None else None,
                        "targetTags": list(translation_language.target_tags) if translation_language.target_tags is not None else None,
                        "openWorldPrompting": translation_language.open_world_prompting,
                        "tier": translation_language.tier,
                    },
                },
                "defaults": {
                    "sourceLanguage": SourceLanguagePolicy.from_json(
                        subtitle.get("sourceLanguage", {"mode": "specified", "tag": "ja"})
                    ).to_json(),
                    "targetLanguage": canonicalize_target_tag(subtitle.get("targetLanguage", "zh-Hans")),
                },
            },
            headers={"Cache-Control": "no-store"},
        )

    async def handle_update_providers(self, request: web.Request) -> web.Response:
        patch = await request.json()
        if not isinstance(patch, dict):
            raise ValueError("provider update must be an object")
        target_language = None
        subtitle_patch = patch.get("subtitle")
        if isinstance(subtitle_patch, dict) and "targetLanguage" in subtitle_patch:
            target_language = canonicalize_target_tag(subtitle_patch["targetLanguage"])
            pipeline = self.subtitle_pipeline
            if pipeline is not None and pipeline.translation_provider is not None:
                validate_translation_pair(
                    pipeline.source_policy.fallback_language,
                    target_language,
                    pipeline.translation_provider.capabilities.language,
                )
        # Construct replacements before committing selection; configuration errors
        # must not leave the UI claiming a model that cannot be instantiated.
        current = load_config(self.providers_path)
        replacement = None
        chat_replacement = None
        if "translation" in patch:
            group = dict(current["translation"], **patch["translation"])
            ids = [group["active"]] + [i for i in group.get("fallback", []) if i != group["active"]]
            providers = [create_translation(self._provider_record(group, i)) for i in ids]
            replacement = FallbackChain(providers) if len(providers) > 1 else providers[0]
        if "chatTranslation" in patch:
            record = self._provider_record(current["translation"], patch["chatTranslation"].get("active"))
            chat_replacement = create_translation(record)
        self.providers_config = update_config(self.providers_path, patch)
        if replacement is not None and self.subtitle_pipeline is not None:
            self.subtitle_pipeline.translation_provider = replacement
            self.subtitle_pipeline.fallback_translation_provider = None
        if self.message_translator is not None and (chat_replacement is not None or target_language is not None):
            import dataclasses
            meta = self.message_translator.meta
            if target_language is not None:
                meta = dataclasses.replace(meta, target_lang=target_language)
            pricing = None
            if chat_replacement is not None:
                prepared = self._prepare_message_translator(self.providers_config, meta)
                pricing = prepared.pricing_by_provider
            self.message_translator.reconfigure(chat_replacement or self.message_translator.translation_provider, meta, pricing)

        if target_language is not None and self.subtitle_pipeline is not None:
            self.subtitle_pipeline.update_target_language(target_language)
        return web.json_response(masked_config(self.providers_config), headers={"Cache-Control": "no-store"})

    async def handle_probe(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._apply_request_proxy(body.get("proxy"), body.get("proxyMode"))
        url = validate_page_url(str(body.get("url") or ""))
        auth = self._authentication(body, consume=False)
        try:
            # Bound the whole worker call as well as yt-dlp's own timeout. This
            # protects the HTTP request if a Windows subprocess or pipe refuses
            # to return after the child-level timeout.
            info = await asyncio.wait_for(asyncio.to_thread(self.probe.extract, url, auth), timeout=20)
        finally:
            auth.close()
        options = build_quality_options(info)
        self.info_cache[url] = (time.monotonic(), info)
        if not options:
            platform = self._platform_for_url(url)
            message = "没有浏览器兼容的 H.264 直播清晰度" if platform in {"bilibili", "twitch"} else "No usable video/audio qualities were returned by yt-dlp"
            raise web.HTTPUnprocessableEntity(text=json.dumps({"error": message}), content_type="application/json")
        return web.json_response(
            {
                "title": info.get("title"),
                "channel": info.get("channel") or info.get("uploader"),
                "platform": self._platform_for_url(url),
                "isLive": bool(info.get("is_live")),
                "qualities": [asdict(option) for option in options],
            }
        )

    async def handle_start(self, request: web.Request) -> web.Response:
        # The operation owns the lock even if an HTTP caller disconnects.
        # Cancellation cannot abandon a to_thread spawn and let Stop race it.
        return await asyncio.shield(self._session_operation(self._start_session, request))

    async def _session_operation(self, operation: Any, *args: Any, **kwargs: Any) -> Any:
        async with self._session_lock:
            return await operation(*args, **kwargs)

    async def _start_session(self, request: web.Request) -> web.Response:
        body = await request.json()
        try:
            return await self._start_session_body(body)
        except asyncio.CancelledError:
            raise
        except (web.HTTPException, ValueError, LanguageNotSupportedError):
            raise
        except (OSError, RuntimeError, asyncio.TimeoutError) as first_error:
            # A failure before a stable session exists has no browser poll to
            # trigger the supervisor. Retry the complete start twice with a
            # fresh probe. This covers an FFmpeg launch failure or a downloader
            # that dies during the first segment without turning validation
            # errors into a slow retry loop.
            replay = self._replayable_start_body(body)
            last_error: BaseException = first_error
            for delay in SESSION_START_RETRY_BACKOFF_SECONDS:
                await asyncio.sleep(delay)
                try:
                    response = await self._start_session_body(
                        copy.deepcopy(replay), automatic=True, force_probe=True
                    )
                    self._active_start_body = replay
                    self._recovery_state = "monitoring"
                    self._ensure_recovery_monitor()
                    logbook.record("info", "media", "启动阶段故障已通过整场重试恢复")
                    return response
                except asyncio.CancelledError:
                    raise
                except (web.HTTPException, ValueError, LanguageNotSupportedError):
                    raise
                except (OSError, RuntimeError, asyncio.TimeoutError) as error:
                    last_error = error
                    logbook.record("warn", "media", f"启动阶段整场重试失败：{type(error).__name__}: {error}")
            self._recovery_auth_cookies = None
            self._recovery_auth_browser = None
            raise last_error

    async def _start_session_body(
        self,
        body: dict[str, Any],
        *,
        automatic: bool = False,
        force_probe: bool = False,
    ) -> web.Response:
        """Start one complete media/subtitle/chat session from a JSON body.

        Manual starts and supervisor starts share this path so a recovery gets
        the same auth, provider, cleanup, and timestamp handling as the first
        start. ``automatic`` only changes ownership bookkeeping; it never
        restarts an individual download leg.
        """
        if not automatic:
            await self._stop_recovery_monitor()
            self._recovery_attempts = 0
            self._recovery_state = "idle"
            self._recovery_reason = None
            self._recovery_error = None
        self._apply_request_proxy(body.get("proxy"), body.get("proxyMode"))
        url = validate_page_url(str(body.get("url") or ""))
        auth = self._authentication_for_recovery() if automatic else self._authentication(body, consume=True)
        # Keep a normalized in-memory copy available if a pre-session failure
        # needs the fresh-probe retry above. It is cleared after all retries or
        # when the user stops the session; it never enters the request/status.
        self._remember_recovery_auth(auth)
        probe_snapshot: ProbeInfoSnapshot | None = None
        auth_lease: SessionAuthLease | None = None
        pending_subtitle_pipeline: SubtitlePipeline | None = None
        try:
            # A new start owns a new generation. Fully dismantle the previous
            # sidecars, media process, stores and auth consumers before assigning
            # the next session lease.
            await self._teardown_session()
            self.private_hls_token = None

            auth_lease = SessionAuthLease(auth)
            self.auth_lease = auth_lease
            # The UI always probes before it can offer a quality to start, so
            # the extraction this used to repeat is normally seconds old. Live
            # manifests carry signed, expiring URLs, hence the short TTL.
            info = None if force_probe else self._fresh_probe_info(url)
            if info is None:
                probe_consumer = auth_lease.acquire("start_probe")
                try:
                    info = await asyncio.to_thread(self.probe.extract, url, probe_consumer)
                finally:
                    probe_consumer.release()
                self.info_cache[url] = (time.monotonic(), info)
            live_message_request = body.get("liveMessages") or {}
            # Automatic recovery must reject an ended source even when chat was
            # disabled, otherwise it could keep retrying a stale VOD URL forever.
            # Preserve the existing manual API contract for callers that use the
            # media path without the optional live-message sidecar.
            if (live_message_request.get("enabled", True) or automatic) and not bool(info.get("is_live")):
                raise ValueError("仅支持正在直播 / Live messages only support a currently ongoing live stream")
            options = build_quality_options(info)
            quality = select_quality(options, str(body.get("qualityId") or "auto"), int(body.get("maxHeight") or 1080))
            inputs = selected_inputs(info, quality)
            target_delay = self._target_delay(body.get("targetDelaySeconds"))
            publish_delay = self._publisher_delay(target_delay)
            format_selector = quality.videoFormatId + (f"+{quality.audioFormatId}" if quality.audioFormatId else "")
            # Validate and construct providers before starting playback. Audio
            # begins only after the private HLS has a complete first segment,
            # which supplies both the decoder input and its PDT epoch.
            subtitle_request = body.get("subtitles") or {}
            if subtitle_request.get("enabled"):
                try:
                    pending_subtitle_pipeline = await self._prepare_subtitles(info, subtitle_request)
                except LanguageNotSupportedError:
                    # Unsupported language settings are rejected before
                    # playback starts (spec US-5): the request fails with an
                    # actionable 400 instead of silently transcribing or
                    # translating in the wrong language.
                    raise
                except Exception as error:
                    # A subtitle failure must never fail playback; report it.
                    self.subtitle_last_error = f"{type(error).__name__}: {error}"
                    logbook.record("error", "asr", f"subtitles unavailable: {self.subtitle_last_error}")
                    pending_subtitle_pipeline = None
            # Hand the extraction result to yt-dlp so the download leg does not
            # repeat it. Both the cookie file and this snapshot are secrets with
            # the same lifetime, so they are torn down by one callback once
            # every leg has reached its download stage.
            asr_audio_leg = pending_subtitle_pipeline is not None and self._asr_audio_leg
            probe_snapshot = ProbeInfoSnapshot(
                info,
                [quality.videoFormatId, *([quality.audioFormatId] if quality.audioFormatId else [])],
            )

            media_consumer = auth_lease.acquire("media_ingest")
            asr_consumer = auth_lease.acquire("asr_audio_ingest") if asr_audio_leg else None

            def release_start_secrets() -> None:
                try:
                    media_consumer.release()
                finally:
                    probe_snapshot.close()

            def release_asr_secrets() -> None:
                if asr_consumer is not None:
                    asr_consumer.release()

            selected_format = next(
                (item for item in info.get("formats") or [] if str(item.get("format_id") or "") == quality.videoFormatId),
                {},
            )
            self.source_ingest = YtDlpLiveIngest(
                url,
                format_selector,
                media_consumer.yt_dlp_args(),
                auth_cleanup=release_start_secrets,
                info_json_path=probe_snapshot.path,
                selected_protocol=str(selected_format.get("protocol") or "") or None,
                leg_role="media",
            )
            if asr_audio_leg and asr_consumer is not None:
                # P3-B: the ASR leg downloads its own tiny audio-only rendition
                # so video-leg stalls/skips can no longer starve subtitles.
                # It re-extracts (no probe snapshot) because live manifests
                # fluctuate; its own consumer keeps its cookie file alive
                # until its extraction passes. Started late (right before the
                # caption ffmpeg connects) to keep its startup backlog small.
                self.asr_audio_ingest = YtDlpLiveIngest(
                    url,
                    self._asr_audio_leg_selector(info) or "234/233/ba[protocol^=m3u8]/worst[protocol^=m3u8]",
                    asr_consumer.yt_dlp_args(),
                    auth_cleanup=release_asr_secrets,
                    selected_protocol="m3u8_native",
                    leg_role="audio",
                )
            # Both independent legs only spawn their downloader/pump here;
            # start them together so audio extraction never waits for the
            # packaging session to finish initializing.
            start_tasks = [asyncio.to_thread(self.source_ingest.start)]
            if self.asr_audio_ingest is not None:
                start_tasks.append(asyncio.to_thread(self.asr_audio_ingest.start))
            # to_thread cannot stop its thread when a sibling raises. Await
            # both outcomes before rollback so no late spawn escapes cleanup.
            started = await asyncio.gather(*start_tasks, return_exceptions=True)
            for result in started:
                if isinstance(result, BaseException):
                    raise result
            # The ingest owns acquisition on independent per-format legs and
            # exposes them as localhost MPEG-TS TCP endpoints; the packaging
            # ffmpeg reads those instead of a single muxed pipe.
            command = build_ffmpeg_command(
                inputs,
                self.session.private_dir,
                input_urls=self.source_ingest.input_urls(),
            )
            capture_clock = CaptureClock()
            try:
                # LiveSession.start() begins by stopping the previous session.
                # Pass the clock through its public boundary so the publisher,
                # message store, and status API all retain the same instance.
                await asyncio.to_thread(
                    self.session.start,
                    url,
                    inputs,
                    publish_delay,
                    command,
                    capture_clock=capture_clock,
                )
                self.target_delay_seconds = target_delay
            except Exception:
                self.session.request_stop()
                await asyncio.to_thread(self.source_ingest.stop)
                self.source_ingest = None
                if self.asr_audio_ingest is not None:
                    await asyncio.to_thread(self.asr_audio_ingest.stop)
                self.asr_audio_ingest = None
                await self._stop_subtitles()
                await self._stop_messages()
                raise

            if pending_subtitle_pipeline is not None:
                try:
                    self.private_hls_token = secrets.token_urlsafe(32)
                    if self.asr_audio_ingest is not None:
                        # The audio leg was started together with the media
                        # leg above. It is already extracting while the
                        # private-HLS epoch is being established.
                        await pending_subtitle_pipeline.start(
                            self.asr_audio_ingest.input_urls()[0],
                            None,
                            input_format="mpegts",
                        )
                        media_epoch = await self._wait_for_private_hls()
                        pending_subtitle_pipeline.set_media_epoch(media_epoch)
                    else:
                        media_epoch = await self._wait_for_private_hls()
                        await pending_subtitle_pipeline.start(self._private_hls_url(), media_epoch)
                    self.subtitle_pipeline = pending_subtitle_pipeline
                except Exception as subtitle_error:
                    self.subtitle_last_error = f"{type(subtitle_error).__name__}: {subtitle_error}"
                    logbook.record("error", "asr", f"subtitle pipeline did not start: {self.subtitle_last_error}")
                    self.private_hls_token = None
                    if self.asr_audio_ingest is not None:
                        # Never started or already dead: stop() still fires the
                        # auth cleanup that releases the consumer and snapshot.
                        await asyncio.to_thread(self.asr_audio_ingest.stop)
                        self.asr_audio_ingest = None
                    await pending_subtitle_pipeline.stop()

            # Media is the primary success contract; live messages start after it.
            messages_request = body.get("liveMessages") or {}
            if messages_request.get("enabled", True):
                try:
                    await self._start_messages(
                        url=url,
                        info=info,
                        message_request=messages_request,
                        auth_lease=auth_lease,
                        clock=capture_clock,
                    )
                except Exception as msg_error:
                    self.message_last_error = f"{type(msg_error).__name__}: {msg_error}"
                    logbook.record("error", "chat", f"live messages unavailable: {self.message_last_error}")
        except Exception:
            await self._teardown_session()
            if auth_lease is not None:
                auth_lease.force_close()
                if self.auth_lease is auth_lease:
                    self.auth_lease = None
            if probe_snapshot is not None:
                probe_snapshot.close()
            raise
        finally:
            # The session retains normalized auth until stop/replacement. Media
            # releases its temporary cookie file after the first bytes; chat may
            # only start (or reconnect) afterwards and must still acquire its own.
            if auth_lease is None:
                auth.close()
            if auth_lease is not None and not self.source_ingest:
                auth_lease.force_close()
                if self.auth_lease is auth_lease:
                    self.auth_lease = None
                if probe_snapshot is not None:
                    probe_snapshot.close()
        status = self.session.status()
        status.pop("publishDelaySeconds", None)
        status["targetDelaySeconds"] = self.target_delay_seconds
        self._remember_recovery_auth(auth)
        if not automatic:
            self._active_start_body = self._replayable_start_body(body)
            self._recovery_state = "monitoring"
            self._ensure_recovery_monitor()
        elif self._recovery_state != "failed":
            self._recovery_state = "monitoring"
        return web.json_response({"ok": True, "quality": asdict(quality), "status": status})

    def _replayable_start_body(self, body: dict[str, Any]) -> dict[str, Any]:
        """Copy only the non-secret controls needed for an automatic retry."""
        replay = copy.deepcopy(body)
        replay.pop("authToken", None)
        # Cookie values are accepted only through the imported auth-token path;
        # never retain an accidentally supplied raw cookie field in a replay.
        replay.pop("cookies", None)
        return replay

    def _remember_recovery_auth(self, provider: AuthenticationProvider) -> None:
        cookies = getattr(provider, "cookies", None)
        self._recovery_auth_cookies = [dict(item) for item in cookies] if cookies else None
        browser = getattr(provider, "browser", None)
        self._recovery_auth_browser = str(browser) if browser else None

    def _authentication_for_recovery(self) -> AuthenticationProvider:
        if self._recovery_auth_cookies:
            return BrowserCookieSnapshot(self._recovery_auth_cookies)
        if self._recovery_auth_browser:
            return DevelopmentBrowserProfileFallback(self._recovery_auth_browser)
        # Persisted platform cookies are selected from the original URL. This
        # also covers a normal restart after the temporary imported snapshot was
        # successfully written to the user's private auth file.
        return self._authentication(self._active_start_body or {}, consume=False)

    def _recovery_status(self) -> dict[str, Any]:
        return {
            "state": self._recovery_state,
            "attempts": self._recovery_attempts,
            "maxAttempts": SESSION_RECOVERY_MAX_ATTEMPTS,
            "reason": self._recovery_reason,
            "error": self._recovery_error,
        }

    @staticmethod
    def _session_recovery_reason(session_status: dict[str, Any], ingest_snapshot: dict[str, Any] | None) -> str | None:
        """Classify a failure that warrants rebuilding the complete session."""
        if session_status.get("state") == "error":
            return str(session_status.get("error") or "媒体封装进程已退出")
        if not ingest_snapshot:
            return "下载器状态消失"
        if ingest_snapshot.get("sourceError"):
            return str(ingest_snapshot["sourceError"])
        if ingest_snapshot.get("running") is False:
            return "直播下载进程已退出"
        clock_reason = ingest_snapshot.get("sourceClockReason")
        if ingest_snapshot.get("sourceClockValid") is False and clock_reason not in (None, "no-legs", "no-pts-probe"):
            return f"source-clock-invalid:{clock_reason}"

        try:
            target_duration = max(1, int(float(session_status.get("targetDuration") or 0)))
        except (TypeError, ValueError):
            target_duration = 1
        threshold = max(5, target_duration + 2)
        idle = ingest_snapshot.get("sourceIdleSeconds")
        if idle is not None:
            try:
                if float(idle) > threshold:
                    return f"直播下载连续 {float(idle):.1f} 秒没有媒体数据"
            except (TypeError, ValueError):
                pass
        if not session_status.get("playlistReady"):
            uptime = float(session_status.get("uptimeSeconds") or 0)
            if uptime > SESSION_RECOVERY_STARTUP_GRACE_SECONDS:
                return "媒体封装长时间没有产生可播放分片"
            return None
        publisher_stall = session_status.get("sourceStallSeconds")
        try:
            if publisher_stall is not None and float(publisher_stall) > threshold:
                return f"媒体分片连续 {float(publisher_stall):.1f} 秒没有推进"
        except (TypeError, ValueError):
            pass
        return None

    def _ensure_recovery_monitor(self) -> None:
        if self._recovery_task is None or self._recovery_task.done():
            self._recovery_task = asyncio.create_task(self._recovery_loop(), name="lingerlens-session-recovery")

    async def _stop_recovery_monitor(self) -> None:
        task = self._recovery_task
        if task is None or task is asyncio.current_task():
            return
        self._recovery_task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _recovery_loop(self) -> None:
        try:
            while self._active_start_body is not None:
                await asyncio.sleep(SESSION_RECOVERY_POLL_SECONDS)
                if self._recovery_in_progress or self._active_start_body is None:
                    continue
                session_status = self.session.status()
                ingest_snapshot = self.source_ingest.snapshot() if self.source_ingest else None
                reason = self._session_recovery_reason(session_status, ingest_snapshot)
                if not reason and self.asr_audio_ingest is not None:
                    audio = self.asr_audio_ingest.snapshot()
                    # Audio can be intentionally backpressured while the viewer
                    # pauses. Only terminal failures or a measured invalid clock
                    # trigger recovery, never ASR idle time alone.
                    audio_check = {k: v for k, v in audio.items() if k in ("running", "sourceError", "sourceClockValid", "sourceClockReason")}
                    reason = self._session_recovery_reason({"state": "running", "playlistReady": True}, audio_check)
                if not reason and self._exact_mapping_refused:
                    reason = f"subtitle-clock-invalid:{self._exact_mapping_refused}"
                if reason:
                    await self._recover_session(reason)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._recovery_state = "failed"
            self._recovery_error = f"恢复监控异常：{type(error).__name__}: {error}"
            logbook.record("error", "media", self._recovery_error)
        finally:
            if self._recovery_task is asyncio.current_task():
                self._recovery_task = None

    async def _recover_session(self, reason: str) -> None:
        if self._recovery_in_progress or self._active_start_body is None:
            return
        self._recovery_in_progress = True
        self._recovery_state = "reconnecting"
        self._recovery_reason = reason
        self._recovery_error = None
        body = copy.deepcopy(self._active_start_body)
        try:
            # Repeated apparently successful starts must not reset the budget
            # and create an infinite fail/restart cycle. Manual Start resets it.
            for attempt in range(self._recovery_attempts + 1, SESSION_RECOVERY_MAX_ATTEMPTS + 1):
                self._recovery_attempts = attempt
                if attempt > 1:
                    await asyncio.sleep(SESSION_RECOVERY_BACKOFF_SECONDS[attempt - 2])
                if self._active_start_body is None:
                    return
                try:
                    # A recovery always re-probes. Signed media URLs and live
                    # status can both expire while a stale /probe result still
                    # sits inside the normal 90-second cache.
                    await self._session_operation(
                        self._start_session_body,
                        copy.deepcopy(body),
                        automatic=True,
                        force_probe=True,
                    )
                    logbook.record("info", "media", f"直播会话已自动恢复（第 {attempt} 次）：{reason}")
                    self._recovery_state = "monitoring"
                    self._recovery_error = None
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    detail = f"第 {attempt} 次失败：{type(error).__name__}: {error}"
                    self._recovery_error = detail
                    logbook.record("warn", "media", f"直播会话自动恢复失败：{detail}")
            # Stop a dead session after the bounded attempts. Keeping its old
            # processes around would make a later manual start race them.
            try:
                await self._session_operation(self._stop_session_after_recovery_failure)
            except Exception as error:
                # A decoder that still holds a live task or process refuses to
                # report a clean teardown. That is its contract, and it must not
                # read as a fresh supervisor crash: the loop would otherwise die
                # here and every later status poll would keep echoing it.
                logbook.record(
                    "error", "media", f"直播会话恢复后清理未完成：{type(error).__name__}: {error}"
                )
            self._recovery_state = "failed"
            self._recovery_error = f"直播会话无法自动恢复（已尝试 {SESSION_RECOVERY_MAX_ATTEMPTS} 次）：{reason}"
            logbook.record("error", "media", self._recovery_error)
        finally:
            self._recovery_in_progress = False

    async def _stop_session_after_recovery_failure(self) -> None:
        # Ownership is released even when the teardown refuses to finish. Left
        # set, the supervisor would immediately re-detect the same dead session
        # and burn another bounded round of attempts on top of handles it was
        # just told not to stack a decoder onto.
        try:
            await self._teardown_session()
        finally:
            self._active_start_body = None
            self._recovery_auth_cookies = None
            self._recovery_auth_browser = None

    @staticmethod
    def _apply_request_proxy(value: Any, mode: Any = None) -> None:
        """Apply the selected network mode without carrying stale proxy state.

        ``direct`` clears all proxy variables, ``system`` restores the values
        present when this backend started, and ``manual`` validates and applies
        the address supplied by the UI.  A legacy request with only ``proxy``
        keeps the old manual behavior; an empty legacy value means direct.
        """
        selected = str(mode or ("manual" if str(value or "").strip() else "direct")).strip().lower()
        if selected == "system":
            for name in _PROXY_ENV_NAMES:
                original = _INITIAL_PROXY_ENV.get(name)
                if original is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = original
            return
        if selected == "direct":
            for name in _PROXY_ENV_NAMES:
                os.environ.pop(name, None)
            return
        if selected != "manual":
            raise ValueError("代理模式必须是 direct、system 或 manual")
        proxy = str(value or "").strip()
        parsed = urllib.parse.urlparse(proxy)
        if parsed.scheme not in {"http", "https", "socks5"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("代理地址必须是 http(s)://host:port 或 socks5://host:port")
        for name in _PROXY_ENV_NAMES:
            os.environ[name] = proxy

    def _fresh_probe_info(self, url: str) -> dict[str, Any] | None:
        """Return a recent /api/probe extraction, or None if it is too old.

        A live extraction contains signed manifest URLs and a live edge that
        moves; reusing a stale one trades a slow start for a failed one.
        """
        cached = self.info_cache.get(url)
        if not cached:
            return None
        cached_at, info = cached
        if time.monotonic() - cached_at > PROBE_INFO_TTL_SECONDS:
            self.info_cache.pop(url, None)
            return None
        return info

    async def handle_stop(self, _: web.Request) -> web.Response:
        return await asyncio.shield(self._session_operation(self._stop_session))

    async def _stop_session(self) -> web.Response:
        """End the session in one round trip, with the legs torn down in parallel.

        See ``_teardown_session``: the ordering constraint that matters lives
        there, and the legs it starts together are disjoint from one another.
        """
        await self._stop_recovery_monitor()
        await self._teardown_session()
        self._active_start_body = None
        self._recovery_auth_cookies = None
        self._recovery_auth_browser = None
        self._recovery_state = "idle"
        self._recovery_attempts = 0
        self._recovery_reason = None
        self._recovery_error = None
        return web.json_response({"ok": True, "status": self.session.status()})

    async def _stop_source_ingest(self) -> None:
        ingest, self.source_ingest = self.source_ingest, None
        if ingest is not None:
            await asyncio.to_thread(ingest.stop)

    async def _prepare_subtitles(self, info: dict[str, Any], request: dict[str, Any]) -> SubtitlePipeline:
        """Validate subtitle settings and build a pipeline without starting audio."""
        config = load_config(self.providers_path)
        self.providers_config = config
        # Model settings is the only provider-selection boundary. Start payloads
        # carry subtitle language/display preferences only; always use the
        # persisted active catalog records.
        asr_id = config["asr"]["active"]
        translation_id = config["translation"]["active"]
        asr_config = self._provider_record(config["asr"], asr_id)
        translation_configs = [self._provider_record(config["translation"], translation_id)]
        translation_configs.extend(
            self._provider_record(config["translation"], provider_id)
            for provider_id in config["translation"].get("fallback", [])
            if provider_id != translation_id
        )
        from companion.providers.readiness import validate_asr_start
        validate_asr_start(asr_config)
        asr_provider = create_asr(asr_config)
        subtitle = {**config.get("subtitle", {}), **request}
        source_policy = SourceLanguagePolicy.from_json(
            subtitle.get("sourceLanguage", {"mode": "specified", "tag": "ja"})
        )
        target_language = canonicalize_target_tag(subtitle.get("targetLanguage", "zh-Hans"))
        # Pre-start capability check: reject language settings the active
        # profiles cannot honor instead of failing silently mid-stream.
        validate_source_policy(source_policy, asr_provider.capabilities.language)

        # Provider-side translation (Soniox translation, Qwen LiveTranslate): the
        # ASR Profile translates on its own session, so no translation model is
        # called at all. The bus is the seam -- the Adapter writes each
        # utterance's translation into it and the session-backed Provider
        # resolves cues from it, which reuses the pipeline's existing worker,
        # deadline and cue-state machinery instead of adding a second path.
        native_translation_bus: NativeTranslationBus | None = None
        native = asr_provider.capabilities.native_translation
        if native.enabled:
            native_fallback = None
            fallback_configs = []
            if asr_config.get("options", {}).get("nativeTranslationFallback") is True:
                fallback_configs = list(translation_configs)
                candidates = [create_translation(item) for item in fallback_configs]
                native_fallback = FallbackChain(candidates) if len(candidates) > 1 else candidates[0]
            native_translation_bus = NativeTranslationBus()
            asr_provider.set_translation_target(target_language)
            translation_providers = [
                NativeSessionTranslation(
                    native_translation_bus,
                    provider_id=f"{asr_id}:native",
                    label=f"{asr_provider.label}（Provider 内置翻译）",
                    model=asr_provider.model,
                    target_tags=native.target_tags,
                    fallback=native_fallback,
                )
            ]
            translation_configs = [asr_config]
            translation_pricing = {
                item["id"]: {
                    "input": item.get("pricePerMillionInputTokensCny"),
                    "cachedInput": item.get("pricePerMillionCachedInputTokensCny"),
                    "cacheWrite": item.get("pricePerMillionCacheWriteTokensCny"),
                    "output": item.get("pricePerMillionOutputTokensCny"),
                    "currency": provider_currency(item.get("kind"), item.get("currency")),
                } for item in fallback_configs
            }
        else:
            translation_configs = [self._provider_record(config["translation"], translation_id)]
            translation_configs.extend(
                self._provider_record(config["translation"], provider_id)
                for provider_id in config["translation"].get("fallback", [])
                if provider_id != translation_id
            )
            translation_providers = [create_translation(item) for item in translation_configs]
            translation_pricing = {
                item["id"]: {
                    "input": item.get("pricePerMillionInputTokensCny"),
                    "cachedInput": item.get("pricePerMillionCachedInputTokensCny"),
                    "cacheWrite": item.get("pricePerMillionCacheWriteTokensCny"),
                    "output": item.get("pricePerMillionOutputTokensCny"),
                    # The stored field names say Cny for historical reasons; this is
                    # what those numbers are actually denominated in.
                    "currency": provider_currency(item.get("kind"), item.get("currency")),
                }
                for item in translation_configs
            }
        primary_translation = FallbackChain(translation_providers) if len(translation_providers) > 1 else translation_providers[0]
        validate_translation_pair(
            source_policy.fallback_language,
            target_language,
            primary_translation.capabilities.language,
        )
        if getattr(asr_provider, "requires_api_key", False) and not asr_config.get("_apiKey"):
            # A missing credential is reported before playback starts; the
            # outer start handler still treats it as a subtitle-only failure.
            raise ValueError(f"API key is not configured for ASR provider {asr_provider.id}")
        sample_rate = asr_provider.capabilities.preferred_sample_rate or int(
            asr_config.get("options", {}).get("sampleRate", 16000)
        )
        self.subtitle_store = CueStore()
        self.subtitle_last_error = None

        asr_audio_selector = self._asr_audio_leg_selector(info)
        self._asr_audio_leg = asr_audio_selector is not None
        if asr_audio_selector is not None:
            # P3-B audio-leg mode: cue suppression follows the AUDIO leg's
            # health; a video-leg stall must no longer silence subtitles.
            ingest_status = lambda: self.asr_audio_ingest.snapshot() if self.asr_audio_ingest else {}  # noqa: E731
            media_anchor: MediaAnchor | None = MediaAnchor()
            anchor_probe = self._current_private_media_seconds
            # The two yt-dlp legs ARE independently extracted, but that does not
            # make their timestamps incomparable -- it makes their origins
            # different, which is exactly what a subtraction measures. Both
            # YouTube live renditions carry one absolute 90kHz clock, and
            # `-copyts` (ytdlp_ingest.command) keeps it on both legs instead of
            # letting each leg's mpegts muxer re-base to its own 1.4s origin.
            # Measured over a 600s live session: ptsFirst(asr-audio) -
            # ptsFirst(media video) held at +5.006s with a range of 0.000s.
            #
            # This replaces the sampled anchor as the primary offset. The window
            # median it differences is `privateMediaSeconds - pcm`, and that
            # quantity is a sawtooth (packaged segments land whole while PCM
            # advances smoothly) whose centre sits a stage-frontier gap away from
            # C -- measured mean -0.719s against a true C of +5.006s. No median,
            # gate, window or reset recovers C from it, and in the same session
            # the offset it produced swung across a 9.1s range: cues up to ~4.5s
            # early and up to ~4s late. The source-clock subtraction has none of
            # that, so the anchor is kept only as the fallback and as the
            # diagnostic it is still good for.
            self._source_clock_origins = None
            # A new session owns a new clock: the probes were rebuilt with the
            # legs, so a refusal recorded against the previous one says nothing
            # about this one. Kept in one place with the origin reset because the
            # two must move together -- dropping the origins while keeping the
            # refusal would leave the sampled fallback disabled for the rest of
            # the application's life after a single discontinuity.
            self._reset_source_clock()
            media_anchor.set_exact_offset(self._source_clock_offset)
            # R1: an untrusted source clock must not silently become the sampled
            # window instead. The two are different statements -- "not measured
            # yet" keeps the old fallback, "measured and not trustworthy" does
            # not -- and the anchor asks this provider which one it is.
            media_anchor.set_sampled_fallback_allowed(self._sampled_fallback_allowed)
            source_pts_mapper = None
        else:
            ingest_status = lambda: self.source_ingest.snapshot() if self.source_ingest else {}  # noqa: E731
            media_anchor = None
            anchor_probe = None
            source_pts_mapper = None

        # Some native ASR profiles (for example Qwen LiveTranslate) use a
        # provider-specific string for turn detection, while the shared
        # caption chunker only understands the optional dictionary used by the
        # OpenAI-shaped profiles.  Do not let a catalog value from one
        # protocol crash subtitle startup for every other profile.
        turn_detection = asr_config.get("options", {}).get("turnDetection")
        if not isinstance(turn_detection, dict):
            turn_detection = {}

        pipeline = SubtitlePipeline(
            asr_provider=asr_provider,
            translation_provider=primary_translation,
            fallback_translation_provider=translation_providers[1] if len(translation_providers) > 1 else None,
            translation_pricing_by_provider=translation_pricing,
            asr_currency=provider_currency(asr_config.get("kind"), asr_config.get("currency")),
            cue_store=self.subtitle_store,
            meta=StreamMeta(
                info.get("title"),
                info.get("channel") or info.get("uploader"),
                str(info.get("categories", [""])[0] or "") if info.get("categories") else None,
                source_policy.fallback_language or "und",
                target_language,
            ),
            source_policy=source_policy,
            sample_rate=sample_rate,
            ingest_status=ingest_status,
            media_anchor=media_anchor,
            anchor_probe=anchor_probe,
            video_backlog=self._current_video_stage_backlog if anchor_probe is not None else None,
            source_pts_mapper=source_pts_mapper,
            hold_minimum=float(subtitle.get("holdSecondsMin", 1.2)),
            hold_maximum=float(subtitle.get("holdSecondsMax", 7.0)),
            hold_seconds_per_char=float(subtitle.get("holdSecondsPerChar", 0.06)),
            silence_duration_ms=int(turn_detection.get("silenceDurationMs", 400)),
            translation_workers=int(subtitle.get("translationWorkers", 4)),
            # Session-backed translation waits for the Provider's own translation
            # to be generated after the utterance is committed, so its budget is
            # the Profile's own setting rather than a translation model's HTTP
            # timeout.
            translation_timeout_seconds=(
                float(asr_config.get("options", {}).get("nativeTranslationTimeoutSeconds", 15.0))
                if native_translation_bus is not None
                else float(translation_configs[0].get("options", {}).get("timeoutSeconds", 6))
            ),
            native_translation_bus=native_translation_bus,
            context_pairs=int(translation_configs[0].get("options", {}).get("contextPairs", 10)),
            context_seconds=float(translation_configs[0].get("options", {}).get("contextSeconds", 90)),
            playback_delay_seconds=lambda: self.target_delay_seconds,
        )
        return pipeline

    @staticmethod
    def _asr_audio_leg_selector(info: dict[str, Any]) -> str | None:
        """Return the yt-dlp format selector for the dedicated ASR audio leg.

        None means the platform is muxed-only (bilibili/twitch) — subtitles
        then keep reading the private HLS exactly as before.

        The selector is deliberately NOT a concrete format id from the probe:
        live manifests fluctuate between extractions (observed: audio-only
        renditions 233/234 present in one probe, gone from the next), so the
        audio leg re-extracts and picks the best available HLS audio itself.
        """
        extractor = str(info.get("extractor_key") or info.get("extractor") or "").lower()
        if extractor in {"bililive", "twitchstream"}:
            return None
        return "234/233/ba[protocol^=m3u8]/worst[protocol^=m3u8]"

    @staticmethod
    def _video_content_seconds(ingest_snapshot: dict | None) -> float | None:
        """Video-leg content position from an ingest snapshot, if it reports PTS.

        Packaging cannot run ahead of the pump that is furthest behind, so the
        binding extent is the smallest one.
        """
        extents = [
            float(leg["sourcePtsLast"]) - float(leg["sourcePtsFirst"])
            for leg in ((ingest_snapshot or {}).get("legThroughput") or [])
            if leg.get("sourcePtsFirst") is not None and leg.get("sourcePtsLast") is not None
        ]
        return min(extents) if extents else None

    def _current_video_stage_backlog(self) -> float | None:
        """Received media the video leg's packaging stage is still holding.

        ``privateMediaSeconds`` counts what FFmpeg has already *packaged*, so it
        trails the media this leg has received by whatever the packaging stage
        has not cut and written yet -- measured 0.7-6.0s on a live 5s-segment
        stream, and structurally non-zero because a segment cannot be counted
        until it is complete. The media anchor differences two such stage-output
        counters, so without this term every cue would be short by it, i.e.
        seconds early.
        """
        try:
            ingest = self.source_ingest.snapshot() if self.source_ingest else None
        except Exception:  # noqa: BLE001 - a probe must never break the anchor
            ingest = None
        content = self._video_content_seconds(ingest)
        packaged = self._current_private_media_seconds()
        if content is None or packaged is None:
            return None
        return content - packaged

    def _current_private_media_seconds(self) -> float | None:
        publisher = self.session.publisher if self.session else None
        return getattr(publisher, "private_media_seconds", None)

    def _reset_source_clock(self) -> None:
        """Forget everything the previous session's PTS clock taught us.

        Called once when a session's subtitle pipeline starts. Both halves belong
        together: the latched origin pair, and the refusal recorded against a
        clock that no longer exists.
        """
        self._source_clock_origins = None
        self._source_clock_logged = False
        self._exact_mapping_refused = None

    def _leg_clock_state(self) -> tuple[str, str | None]:
        """("trusted" | "unmeasured" | "untrusted", reason) for the two legs.

        One trustworthy leg is not enough: the exact offset is a difference
        between the two, so a pair is only as good as its weaker clock.

        The middle state is the one that matters. A leg that has not started, has
        no pumps, or carries no PTS probe cannot have broken a clock it never had,
        so that is "unmeasured" and the sampled window stays in charge. A leg that
        reports no validity AT ALL is not unmeasured -- an absent statement is not
        a positive one, and treating it as one is how a reset gets modulo'd into
        looking like a small, plausible distance.
        """
        for ingest in (self.asr_audio_ingest, self.source_ingest):
            if ingest is None:
                return ("unmeasured", "no-session")
            # Ask the LEG, through the same property its /api/status projection is
            # built from. Reading a snapshot dict here, or an attribute the ingest
            # does not have, is how the first version of this refused every real
            # session while its own tests passed against fakes that declared the
            # field the real object lacked.
            state = getattr(ingest, "source_clock_state", None)
            if state is None:
                return ("untrusted", "clock-validity-not-reported")
            valid, reason = state
            if valid:
                continue
            if reason in ("no-legs", "no-pts-probe"):
                return ("unmeasured", reason)
            return ("untrusted", reason or "clock-validity-not-reported")
        return ("trusted", None)

    def _sampled_fallback_allowed(self) -> bool:
        """False once this session has been shown to be untrustworthy.

        Deliberately NOT "the exact offset is currently None": a session that has
        simply not measured its first PTS yet is still free to use the sampled
        window, which is the behaviour everything had before this existed.
        """
        return self._exact_mapping_refused is None

    def _refuse_exact_mapping(self, reason: str) -> None:
        """Stop publishing an exact position for the rest of the session.

        Recorded once per transition, not per tick: this runs on every anchor
        tick and the status poll runs every second.
        """
        if reason != self._exact_mapping_refused:
            self._exact_mapping_refused = reason
            logbook.record(
                "warn",
                "media",
                f"源时钟不可用于精确对齐，字幕改用保守路径：{reason}",
            )
        return None

    def _source_clock_offset(self) -> float | None:
        """C = (ASR leg's source origin) - (packaging video leg's source origin).

        Both legs are separate yt-dlp processes, so their PES PTS are only
        comparable because `-copyts` keeps the source's own timestamps instead
        of letting each leg's mpegts muxer re-base to its own 1.4s origin.
        Content at source time T is then at pcm = T - A0 on the subtitle leg and
        at privateMediaSeconds = T - V0 in the packaged playlist, so the mapping
        the anchor needs is exactly A0 - V0.

        None means "not measurable on the source clock", never "zero", and the
        two ways of being unmeasurable are kept apart because the anchor treats
        them differently:

        NOT MEASURED YET -- the sampled window stays in charge, as it always was:
          * no session, or
          * a leg that has not reported its first PTS yet.

        REFUSED FOR THIS SESSION -- the sampled window is switched off too, so no
        position is published rather than a position known to be suspect:
          * a leg whose own probe gave up on the clock (a transport-stream
            discontinuity, a reset that is not a wrap, or a reverse jump it
            cannot attribute),
          * a leg whose first PTS is at the mpegts muxer's re-base origin, which
            after a wrap is exactly what a genuine beginning-of-clock looks like,
            so it is unconfirmable rather than resolvable, or
          * two origins further apart than the guard even after the difference is
            taken modulo one 33-bit period.

        A leg that re-based MID-session is the one case that returns None without
        refusing: `_pcm_offset` is monotonic across a decoder restart, so the
        fresh subtraction is meaningless, but the sampled window is precisely the
        mechanism that re-converges after a re-base.
        """
        audio_first = self._leg_origin(self.asr_audio_ingest, 0)
        video_first = self._leg_origin(self.source_ingest, 0)
        state, reason = self._leg_clock_state()
        if state == "untrusted":
            return self._refuse_exact_mapping(f"source-clock-invalid:{reason}")
        if state == "unmeasured":
            # No session, no legs, or nothing measured yet. Not a refusal: the
            # sampled window stays in charge, exactly as it was before this
            # existed.
            return None
        if audio_first is None or video_first is None:
            # A leg has not reported its first PTS yet. Still "not measured".
            return None
        if audio_first < MPEGTS_REBASE_ORIGIN or video_first < MPEGTS_REBASE_ORIGIN:
            # A first origin inside the mpegts muxer's own re-base window. After
            # a wrap the source legitimately looks like this too, and two numbers
            # alone cannot tell the ~1.4s rebase artifact from a genuine
            # beginning-of-clock, so this is declared unconfirmable rather than
            # resolved by taking a modulus. Relaxing this guard would be
            # inventing evidence.
            return self._refuse_exact_mapping("origin-rebase-or-wrap-ambiguous")
        # The clock is 33 bits wide, so the difference has to be taken modulo one
        # period: an origin of 3s and an origin of (W - 2s) are five seconds
        # apart, not 26.5 hours. Because of that, the bound below is no longer
        # what catches a wrap -- it catches a genuine mismatch between two legs.
        offset = signed_pts_delta(
            round(audio_first * PTS_HZ), round(video_first * PTS_HZ)
        ) / PTS_HZ
        if abs(offset) > SOURCE_CLOCK_MAX_LEG_SKEW:
            return self._refuse_exact_mapping("implausible-origin-skew")
        if self._source_clock_origins is None:
            # Latch the first pair that was valid. Later reads must match it.
            self._source_clock_origins = (audio_first, video_first)
            if not self._source_clock_logged:
                self._source_clock_logged = True
                logbook.record(
                    "info",
                    "subtitle-clock",
                    f"字幕源时钟已锁定：audio-video={offset:.3f}s",
                )
        elif (audio_first, video_first) != self._source_clock_origins:
            # A leg re-based mid-session. The origins move but `_pcm_offset` does
            # not restart with them, so a fresh subtraction no longer describes
            # where pcm 0 sits. The raw pair changing is what makes it invalid;
            # the offset itself is computed once, from the latched pair, so a wrap
            # crossing during the session cannot move it.
            return None
        return signed_pts_delta(
            round(self._source_clock_origins[0] * PTS_HZ),
            round(self._source_clock_origins[1] * PTS_HZ),
        ) / PTS_HZ

    @staticmethod
    def _leg_origin(ingest: Any, index: int) -> float | None:
        """One pump's first PES PTS, or None if that leg has not reported one."""
        first = getattr(ingest, "source_pts_first", None) if ingest is not None else None
        if not first or index >= len(first):
            return None
        value = first[index]
        return float(value) if value is not None else None

    async def _wait_for_private_hls(self) -> float:
        """Wait for one complete private segment and its authoritative PDT."""
        deadline = time.monotonic() + PRIVATE_HLS_READY_TIMEOUT_SECONDS
        playlist = self.session.private_dir / "live.m3u8"
        init_segment = self.session.private_dir / "init.mp4"
        while time.monotonic() < deadline:
            publisher = self.session.publisher
            epoch = publisher.pdt_epoch if publisher is not None else None
            if epoch is not None and playlist.is_file() and init_segment.is_file():
                try:
                    lines = playlist.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    lines = []
                for line in lines:
                    name = line.strip()
                    if name.endswith(".m4s") and Path(name).name == name:
                        if (self.session.private_dir / name).is_file():
                            return float(epoch)
            process = self.session.process
            if process is not None and process.poll() is not None:
                raise RuntimeError("packaging FFmpeg stopped before private HLS became ready")
            await asyncio.sleep(0.05)
        raise asyncio.TimeoutError("private HLS did not become ready for subtitles")

    def _private_hls_url(self) -> str:
        token = self.private_hls_token
        if token is None:
            raise RuntimeError("private HLS session token is unavailable")
        host = str(self.args.host)
        authority = f"[{host}]" if ":" in host and not host.startswith("[") else host
        return f"http://{authority}:{int(self.args.port)}/_private-hls/{token}/live.m3u8"

    async def _stop_subtitles(self) -> None:
        pipeline = self.subtitle_pipeline
        try:
            if pipeline is not None:
                await pipeline.stop()
                # Forget the pipeline only once its teardown has actually
                # finished. Clearing the reference BEFORE the await let a failed
                # teardown hide an unreaped decoder behind a None attribute, so
                # the next Start had nothing to refuse on and simply started a
                # second one; a failing stop() now propagates out of
                # _teardown_session instead.
                if self.subtitle_pipeline is pipeline:
                    self.subtitle_pipeline = None
        finally:
            # Everything below is independent of the subtitle pipeline, so it
            # still runs on that failure path rather than being skipped by it.
            audio_ingest, self.asr_audio_ingest = self.asr_audio_ingest, None
            if audio_ingest is not None:
                await asyncio.to_thread(audio_ingest.stop)
            self.private_hls_token = None
            self._asr_audio_leg = False

    def _subtitle_status(self) -> dict[str, Any]:
        if self.subtitle_pipeline:
            return self.subtitle_pipeline.status()
        return {
            "running": False,
            # Why the pipeline never started (prepare/start failure). Without
            # this the failure is invisible: playback continues and subtitles
            # silently never appear.
            "startError": self.subtitle_last_error,
            "asrSeconds": 0.0,
            "asrUsage": {"seconds": 0.0},
            "asrEstimatedCostCny": None,
            "asrEstimateReason": "ASR usage and pricing unavailable while subtitles are not running",
            "translationUsage": {
                "calls": 0,
                "unknownUsageCalls": 0,
                "nonCachedInputTokens": 0,
                "cachedInputTokens": 0,
                "cacheWriteInputTokens": 0,
                "outputTokens": 0,
                "totalTokens": 0,
                "byProvider": {},
            },
            "translationEstimatedCostCny": None,
            "translationEstimateReason": "translation usage unavailable while subtitles are not running",
            "totalEstimatedCostCny": None,
            "totalEstimateReason": "subtitle usage unavailable while subtitles are not running",
            "estimatedCostCny": None,
            "translationBacklog": 0,
            "pdtEpoch": self.session.publisher.pdt_epoch if self.session.publisher else None,
        }

    def _media_clock_status(self) -> dict[str, Any]:
        publisher = self.session.publisher
        if publisher is None:
            return {"available": False, "captureWallTime": None, "pdtEpoch": None, "targetDuration": None}
        return publisher.capture_clock.snapshot()

    def _messages_status(self) -> dict[str, Any]:
        ingest = self.message_ingest.status() if self.message_ingest else {}
        store = self.message_store.stats()
        translator = self.message_translator.status() if self.message_translator else {}
        session_platform = self._platform_for_url(self.session.page_url)
        unsupported_twitch = False
        state = ingest.get("state") or ("idle" if self.message_ingest is None else "connecting")
        return {
            "state": state,
            "platform": ingest.get("platform") or session_platform,
            "received": store["received"],
            "textMessages": store["textMessages"],
            "translated": store["translated"],
            "translationFailed": store["translationFailed"],
            "translationSkipped": store["translationSkipped"],
            "reconnects": int(ingest.get("reconnects") or 0),
            "pendingClock": store["pendingClock"],
            "lastError": self.message_last_error or ingest.get("lastError"),
            "translate": self.message_translate_enabled,
            "targetLanguage": self._message_target_language(),
            "translationState": (
                "disabled" if not self.message_translate_enabled
                else "degraded" if self.message_last_error and self.message_translator is None
                else "running"
            ),
            "translationBacklog": int(translator.get("backlog") or 0),
            "translationFailureReasons": translator.get("failureReasons", {}),
            "translationAttemptFailures": translator.get("attemptFailureReasons", {}),
            "translationLastFailure": translator.get("lastFailureReason"),
            "translationUsage": translator.get("translationUsage", {}),
            "translationEstimatedCostCny": translator.get("translationEstimatedCostCny"),
        }

    def _message_target_language(self) -> str:
        config = self.providers_config or load_config(self.providers_path)
        return canonicalize_target_tag(config.get("subtitle", {}).get("targetLanguage", "zh-Hans"))

    async def _stop_messages(self) -> None:
        self.message_generation += 1
        ingest, self.message_ingest = self.message_ingest, None
        if ingest:
            await ingest.stop()
        trans, self.message_translator = self.message_translator, None
        if trans:
            await trans.stop()
        self.message_translate_enabled = False
        self.message_store = LiveMessageStore()

    def _prepare_message_translator(self, config: dict[str, Any], meta: StreamMeta) -> MessageTranslator | None:
        translation_id = config["chatTranslation"]["active"]
        translation_configs = [self._provider_record(config["translation"], translation_id)]
        translation_providers = [create_translation(item) for item in translation_configs]
        pricing = {
            item["id"]: {
                "input": item.get("pricePerMillionInputTokensCny"),
                "cachedInput": item.get("pricePerMillionCachedInputTokensCny"),
                "cacheWrite": item.get("pricePerMillionCacheWriteTokensCny"),
                "output": item.get("pricePerMillionOutputTokensCny"),
                "currency": provider_currency(item.get("kind"), item.get("currency")),
            }
            for item in translation_configs
        }
        primary_translation = FallbackChain(translation_providers) if len(translation_providers) > 1 else translation_providers[0]
        return MessageTranslator(
            store=self.message_store,
            translation_provider=primary_translation,
            pricing_by_provider=pricing,
            meta=meta,
            max_queue_size=30,
            concurrency=2,
            timeout_seconds=3.0,
        )

    async def _create_message_translator(self) -> MessageTranslator:
        config = load_config(self.providers_path)
        self.providers_config = config
        meta = StreamMeta(None, None, None, "und", self._message_target_language())
        translator = self._prepare_message_translator(config, meta)
        if translator is None:
            raise RuntimeError("live-message translation provider unavailable")
        await translator.start()
        return translator

    async def _start_messages(
        self,
        url: str,
        info: dict[str, Any],
        message_request: dict[str, Any],
        auth_lease: SessionAuthLease,
        clock: CaptureClock,
    ) -> None:
        self.message_last_error = None
        self.message_store = LiveMessageStore(clock=clock, max_messages=500, retention_seconds=180, pending_limit=1000)
        self.message_generation += 1
        generation = self.message_generation
        platform = self._platform_for_url(url)
        if not platform:
            return
        self.message_translate_enabled = bool(message_request.get("translate", False))
        if self.message_translate_enabled:
            try:
                config = load_config(self.providers_path)
                self.providers_config = config
                meta = StreamMeta(
                    info.get("title"),
                    info.get("channel") or info.get("uploader"),
                    str(info.get("categories", [""])[0] or "") if info.get("categories") else None,
                    "und",
                    self._message_target_language(),
                )
                self.message_translator = self._prepare_message_translator(config, meta)
                if self.message_translator:
                    await self.message_translator.start()
                    self.message_translator.set_enabled(True)
            except Exception as error:
                self.message_translator = None
                self.message_last_error = f"Translator error: {type(error).__name__}: {error}"
                logbook.record("error", "translation", self.message_last_error)

        def on_message(message: LiveMessage) -> None:
            if generation != self.message_generation:
                return
            if self.message_translate_enabled:
                if message.translation_state != "pending":
                    self.message_store.update_translation(message.id, state="pending")
                if self.message_translator:
                    self.message_translator.enqueue(message)
                else:
                    self.message_store.update_translation(message.id, state="failed")

        if platform == "twitch":
            self.message_ingest = TwitchChatIngest(
                url,
                self.message_store,
                clock=clock,
                on_message=on_message,
                on_error=lambda err: setattr(self, "message_last_error", err),
            )
            await self.message_ingest.start()
            return
        if platform == "youtube":
            self.message_ingest = YouTubeChatIngest(
                url,
                self.message_store,
                clock=clock,
                auth_lease=auth_lease,
                on_message=on_message,
            )
        else:
            self.message_ingest = BilibiliDanmakuIngest(
                url,
                self.message_store,
                clock=clock,
                auth_lease=auth_lease,
                on_message=on_message,
            )
        await self.message_ingest.start()

    @staticmethod
    def _provider_record(section: dict[str, Any], provider_id: str) -> dict[str, Any]:
        for provider in section.get("providers", []):
            if provider.get("id") == provider_id:
                return provider
        raise ValueError(f"unknown provider id: {provider_id}")

    def handle_control(self, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("action") != "authSnapshot":
            raise ValueError("Unsupported control action")
        cookies = body.get("cookies")
        if not isinstance(cookies, list) or len(cookies) > 500:
            raise ValueError("Invalid cookie snapshot")
        token = secrets.token_urlsafe(24)
        with self.auth_lock:
            self.auth_snapshots[token] = cookies
        return {"ok": True, "authToken": token}

    def _load_persisted_auth(self) -> dict[str, list[dict[str, Any]]]:
        """Reload per-platform snapshots and migrate the legacy single snapshot."""
        try:
            raw = json.loads(self.auth_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        platforms: dict[str, list[dict[str, Any]]] = {}
        if isinstance(raw, dict) and isinstance(raw.get("platforms"), dict):
            for platform in SUPPORTED_AUTH_PLATFORMS:
                cookies = raw["platforms"].get(platform)
                if isinstance(cookies, list):
                    normalized = normalize_imported_cookies([dict(item) for item in cookies if isinstance(item, dict)])
                    if normalized:
                        platforms[platform] = normalized
            return platforms
        if isinstance(raw, dict) and isinstance(raw.get("cookies"), list):
            cookies = normalize_imported_cookies([dict(item) for item in raw["cookies"] if isinstance(item, dict)])
            if cookies:
                platform = self._platform_for_cookie_domain(cookies[0].get("domain"))
                if platform:
                    platforms[platform] = cookies
                    self.persisted_auth = platforms
                    self._write_persisted_auth()
        return platforms

    @staticmethod
    def _platform_for_cookie_domain(domain: object) -> str | None:
        bare = str(domain or "").lower().lstrip(".")
        if bare == "bilibili.com" or bare.endswith(".bilibili.com"):
            return "bilibili"
        if bare == "twitch.tv" or bare.endswith(".twitch.tv"):
            return "twitch"
        if bare in {"youtube.com", "google.com"} or bare.endswith((".youtube.com", ".google.com")):
            return "youtube"
        return None

    @staticmethod
    def _platform_for_url(url: object) -> str | None:
        host = (urllib.parse.urlparse(str(url or "")).hostname or "").lower()
        if host == "live.bilibili.com":
            return "bilibili"
        if host in {"twitch.tv", "www.twitch.tv"}:
            return "twitch"
        if host in {"youtube.com", "youtu.be"} or host.endswith(".youtube.com"):
            return "youtube"
        return None

    def _persist_auth(self, platform: str, cookies: list[dict[str, Any]]) -> bool:
        """Merge one platform snapshot into the user-private local auth file."""
        self.persisted_auth[platform] = cookies
        return self._write_persisted_auth()

    def _write_persisted_auth(self) -> bool:
        try:
            self.auth_file.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({"version": 2, "platforms": self.persisted_auth}, ensure_ascii=False, indent=2) + "\n"
            fd, temporary = tempfile.mkstemp(prefix=".auth-snapshot.", dir=self.auth_file.parent)
            try:
                if os.name != "nt":
                    os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(payload)
                os.replace(temporary, self.auth_file)
                if os.name != "nt":
                    self.auth_file.chmod(0o600)
            except BaseException:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise
        except OSError:
            return False
        return True

    def _authentication(self, body: dict[str, Any], consume: bool) -> AuthenticationProvider:
        auth_token = body.get("authToken")
        if auth_token:
            token = str(auth_token)
            with self.auth_lock:
                cookies = self.auth_snapshots.pop(token, None) if consume else self.auth_snapshots.get(token)
            if cookies is None:
                raise web.HTTPUnauthorized(text=json.dumps({"error": "Authentication snapshot expired"}), content_type="application/json")
            return BrowserCookieSnapshot(cookies)
        # Select the persisted login matching the target URL. Imports for the
        # other platform remain intact and are never merged into this request.
        platform = self._platform_for_url(body.get("url"))
        if platform and self.persisted_auth.get(platform):
            return BrowserCookieSnapshot(self.persisted_auth[platform])
        browser = body.get("cookiesFromBrowser") or self.args.cookies_from_browser
        return DevelopmentBrowserProfileFallback(str(browser) if browser else None)

    async def startup(self, _: web.Application) -> None:
        if self.control is None:
            return
        try:
            await asyncio.to_thread(self.control.start)
        except PermissionError as error:
            # A stale/other Native Messaging pipe must not take down the HTTP
            # player or its localhost model APIs. Cookie IPC can be restored by
            # closing the old Companion and restarting later.
            print(f"[LingerLens] Native control IPC unavailable: {error}", file=sys.stderr)
            logbook.record(
                "warn",
                "desktop",
                f"Native Messaging cookie bridge unavailable ({type(error).__name__}); restart the Companion to retry",
            )

    def _usage_status(
        self,
        subtitle: dict[str, Any] | None = None,
        live_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        subtitle = subtitle if subtitle is not None else self._subtitle_status()
        live_status = live_status if live_status is not None else self._messages_status()
        subtitle_usage = subtitle.get("translationUsage", {})
        live_usage = live_status.get("translationUsage", {})
        subtitle_cost = subtitle.get("translationEstimatedCostCny")
        live_cost = live_status.get("translationEstimatedCostCny")
        return {
            "subtitle": subtitle_usage,
            "liveMessage": live_usage,
            "total": {
                "calls": int(subtitle_usage.get("calls") or 0) + int(live_usage.get("calls") or 0),
                "nonCachedInputTokens": int(subtitle_usage.get("nonCachedInputTokens") or 0) + int(live_usage.get("nonCachedInputTokens") or 0),
                "cachedInputTokens": int(subtitle_usage.get("cachedInputTokens") or 0) + int(live_usage.get("cachedInputTokens") or 0),
                "outputTokens": int(subtitle_usage.get("outputTokens") or 0) + int(live_usage.get("outputTokens") or 0),
            },
            "estimatedCostCny": round(float(subtitle_cost) + float(live_cost), 9)
            if subtitle_cost is not None and live_cost is not None else None,
        }

    async def cleanup(self, _: web.Application) -> None:
        await self._stop_recovery_monitor()
        await asyncio.shield(self._session_operation(self._stop_session))
        if self.control is not None:
            await asyncio.to_thread(self.control.stop)
        with self.auth_lock:
            self.auth_snapshots.clear()

    async def _teardown_session(self) -> None:
        """Dismantle every sidecar, media child and auth consumer of the session.

        Shared by Stop, Start-replacing-a-session, the failed-Start rollback and
        application shutdown so the four paths cannot drift apart. The one order
        that matters is kept: ``request_stop`` is claimed before any download
        leg closes, otherwise their EOF makes the packaging FFmpeg look like a
        live failure to a concurrent status poll.
        """
        self.session.request_stop()
        # Independent legs: sequential teardown only ever added their waits up.
        outcomes = await asyncio.gather(
            self._stop_messages(),
            self._stop_subtitles(),
            self._stop_source_ingest(),
            return_exceptions=True,
        )
        try:
            await asyncio.to_thread(self.session.stop)
        finally:
            lease, self.auth_lease = self.auth_lease, None
            if lease is not None:
                lease.force_close()
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome


def require_local_request(request: web.Request) -> None:
    """Protect every local route against rebinding and cross-origin control.

    Loopback is a bind boundary, not a browser-origin boundary: another site
    can submit a simple POST without CORS permission. A different localhost
    port is also a different origin. Non-browser clients (including FFmpeg and
    the desktop proxy) may omit Origin; desktop requests additionally require
    their private session token.
    """
    def origin_tuple(value: str) -> tuple[str, str | None, int]:
        parsed = urllib.parse.urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or parsed.username is not None
                or parsed.password is not None or parsed.path or parsed.query or parsed.fragment
                or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError("invalid local origin")
        return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        target = origin_tuple(f"{request.scheme}://{request.headers.get('Host', '')}")
        origin = request.headers.get("Origin")
        if origin is not None and origin_tuple(origin) != target:
            raise ValueError("different origin")
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            raise ValueError("cross-site request")
    except ValueError:
        raise web.HTTPForbidden(
            text=json.dumps({"error": "Only same-origin loopback requests are allowed"}),
            content_type="application/json",
        ) from None


@web.middleware
async def local_request_guard(request: web.Request, handler: Any) -> web.StreamResponse:
    require_local_request(request)
    return await handler(request)


def _request_target(request: web.Request) -> str:
    """Name the failing route without echoing its path.

    ``request.path`` can contain the private-HLS path token, which is the
    credential that keeps FFmpeg's copy of the delayed stream private. The
    route template (``/_private-hls/{token}/{name}``) identifies the failing
    endpoint without carrying the token into a UI-visible log.
    """
    try:
        return str(request.match_info.route.resource.canonical)
    except (AttributeError, RuntimeError):
        return "unmatched-route"


@web.middleware
async def errors(request: web.Request, handler: Any) -> web.StreamResponse:
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except (ValueError, RuntimeError, asyncio.TimeoutError) as error:
        # This branch used to answer 400 silently. A rejected request is
        # exactly what the user needs to see when the UI shows nothing.
        logbook.record("warn", "request", f"rejected {request.method} {_request_target(request)}: {error}")
        return web.json_response({"error": str(error)}, status=400)
    except Exception as error:  # Prototype boundary: return diagnostics, never secrets.
        print(f"[LingerLens] Unexpected request failure: {type(error).__name__}: {error}", file=sys.stderr)
        # Type name only: an unexpected exception's text can embed the URL or
        # credential that made it fail.
        logbook.record(
            "error",
            "request",
            f"unhandled {type(error).__name__} for {request.method} {_request_target(request)}",
        )
        return web.json_response({"error": "Unexpected companion failure"}, status=500)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PROTOTYPE 2: localhost delayed HLS/CMAF companion")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--publish-delay", type=float, default=3.0, help=argparse.SUPPRESS)
    parser.add_argument("--cookies-from-browser", choices=["chrome", "edge"])
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--providers-file", type=Path, default=DEFAULT_PROVIDERS)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Prototype 2 only binds to loopback")
    if not 1 <= args.port <= 65535:
        parser.error("--port is invalid")
    return args


def main() -> int:
    args = parse_args()
    companion = CompanionApplication(args)
    app = companion.routes()
    app.middlewares.append(errors)
    print(f"[LingerLens] Prototype 2 player: http://{args.host}:{args.port}/")
    print(f"[LingerLens] Target total live delay: {DEFAULT_TARGET_DELAY_SECONDS:g}s")
    print("[LingerLens] Import Cookies in the player, through Native Messaging, or the explicit development browser fallback.")
    try:
        web.run_app(app, host=args.host, port=args.port, print=None, handle_signals=True)
    except OSError as error:
        if getattr(error, "winerror", None) == 10048 or getattr(error, "errno", None) == errno.EADDRINUSE:
            print(
                f"[LingerLens] Port {args.port} is already in use; another Companion is probably still running.",
                file=sys.stderr,
            )
            print(
                "[LingerLens] Close the other Companion instance, or start this one with --port 8766.",
                file=sys.stderr,
            )
            return 1
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
