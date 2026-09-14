#!/usr/bin/env python3
"""Local HTTP companion for LagLingo HLS Prototype 2."""

from __future__ import annotations

import argparse
import asyncio
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
    from .control_ipc import ControlServer  # type: ignore[import-not-found]
    from .ytdlp_ingest import YtDlpLiveIngest  # type: ignore[import-not-found]
    from .languages import LanguageNotSupportedError, canonicalize_target_tag, catalog_entries, language_catalog  # type: ignore[import-not-found]
    from .providers import create_asr, create_translation  # type: ignore[import-not-found]
    from .providers.base import SourceLanguagePolicy, StreamMeta, validate_source_policy, validate_translation_pair  # type: ignore[import-not-found]
    from .providers.config import load_config, masked_config, model_settings_view, update_config, update_model_settings  # type: ignore[import-not-found]
    from .providers.fallback import FallbackChain  # type: ignore[import-not-found]
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
    from .recovery_policy import RecoveryPolicy  # type: ignore[import-not-found]
except ImportError:  # Direct script execution.
    from control_ipc import ControlServer  # type: ignore[import-not-found]
    from ytdlp_ingest import YtDlpLiveIngest  # type: ignore[import-not-found]
    from companion.languages import LanguageNotSupportedError, canonicalize_target_tag, catalog_entries, language_catalog  # type: ignore[import-not-found]
    from companion.providers import create_asr, create_translation  # type: ignore[import-not-found]
    from companion.providers.base import SourceLanguagePolicy, StreamMeta, validate_source_policy, validate_translation_pair  # type: ignore[import-not-found]
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
    from companion.recovery_policy import RecoveryPolicy  # type: ignore[import-not-found]

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
        self.control = ControlServer(self.handle_control) if enable_native_control else None
        self.source_ingest: YtDlpLiveIngest | None = None
        # P3-B: dedicated tiny audio-only download leg feeding the subtitle
        # pipeline, independent of the video packaging path's health.
        self.asr_audio_ingest: YtDlpLiveIngest | None = None
        self._asr_audio_leg = False
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
        self.recovery_policy = RecoveryPolicy()

    def routes(self) -> web.Application:
        app = web.Application(client_max_size=4 * 1024 * 1024)
        app["companion"] = self
        app.router.add_get("/", self.index)
        app.router.add_get("/favicon.ico", self.favicon)
        app.router.add_get("/player.js", self.static_file)
        app.router.add_get("/ui-bootstrap.js", self.static_file)
        app.router.add_get("/language-selector.js", self.static_file)
        app.router.add_get("/subtitle-scheduler.js", self.static_file)
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

    async def status(self, _: web.Request) -> web.Response:
        status = self.session.status()
        ingest_snapshot = self.source_ingest.snapshot() if self.source_ingest else None
        status["sourceDelaySeconds"] = 0.0
        ingest_list = [dict(ingest_snapshot, role="media")] if ingest_snapshot else []
        if self.asr_audio_ingest is not None:
            ingest_list.append(dict(self.asr_audio_ingest.snapshot(), role="asr-audio"))
        status["sourceIngest"] = ingest_list
        if ingest_snapshot:
            decision = self.recovery_policy.decide(
                stall_seconds=ingest_snapshot.get("sourceIdleSeconds"),
                process_running=bool(ingest_snapshot.get("running")),
                source_error=ingest_snapshot.get("sourceError"),
            )
            status["sourceRecovery"] = {
                "state": decision.state,
                "action": decision.action,
                "reason": decision.reason,
                "stallSeconds": ingest_snapshot.get("sourceIdleSeconds"),
            }
        else:
            status["sourceRecovery"] = {"state": "idle", "action": "none", "reason": "no-session", "stallSeconds": None}
        subtitle_status = self._subtitle_status()
        message_status = self._messages_status()
        status["subtitles"] = subtitle_status
        status["mediaClock"] = self._media_clock_status()
        status["liveMessages"] = message_status
        status["usage"] = self._usage_status(subtitle_status, message_status)
        status["targetDelaySeconds"] = self.target_delay_seconds
        status["estimatedTotalDelaySeconds"] = round(
            float(status.get("sourceDelaySeconds") or 0.0)
            + float(status.get("hiddenMediaSeconds") or 0.0)
            + PLAYER_LIVE_SYNC_SECONDS,
            3,
        )
        status.pop("publishDelaySeconds", None)
        if ingest_snapshot and ingest_snapshot.get("sourceError") and status.get("state") == "running":
            status["state"] = "error"
            status["error"] = ingest_snapshot["sourceError"]
        return web.json_response(status, headers={"Cache-Control": "no-store"})

    async def handle_subtitles(self, request: web.Request) -> web.Response:
        try:
            after_seq = int(request.query.get("afterSeq", "0"))
        except ValueError as exc:
            raise ValueError("invalid subtitle cursor") from exc
        if after_seq < 0:
            raise ValueError("invalid subtitle cursor")
        pipeline_status = self._subtitle_status()
        return web.json_response(
            {
                "now": time.time(),
                "pdtEpoch": pipeline_status.get("pdtEpoch"),
                "cues": [cue.to_dict() for cue in self.subtitle_store.query(after_seq=after_seq)],
                "maxSeq": self.subtitle_store.max_seq,
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
        """Reject cross-site imports: loopback host plus same-origin Origin check."""
        origin = request.headers.get("Origin")
        if origin:
            origin_host = urllib.parse.urlparse(origin).hostname or ""
            if origin_host not in {"127.0.0.1", "localhost", "::1"}:
                raise web.HTTPForbidden(text=json.dumps({"error": "Cross-origin cookie import is not allowed"}), content_type="application/json")
        host_header = request.headers.get("Host", "")
        if host_header.startswith("["):
            host_name = host_header.split("]", 1)[0].lstrip("[")
        else:
            host_name = host_header.split(":", 1)[0]
        if host_name.lower() not in {"127.0.0.1", "localhost", "::1"}:
            raise web.HTTPForbidden(text=json.dumps({"error": "Only loopback requests may import cookies"}), content_type="application/json")

    async def handle_get_model_settings(self, request: web.Request) -> web.Response:
        self._require_local_request(request)
        self.providers_config = load_config(self.providers_path)
        return web.json_response(model_settings_view(self.providers_config), headers={"Cache-Control": "no-store"})

    async def handle_update_model_settings(self, request: web.Request) -> web.Response:
        self._require_local_request(request)
        settings = await request.json()
        if not isinstance(settings, dict):
            raise ValueError("model settings must be an object")
        self.providers_config = update_model_settings(self.providers_path, settings)
        return web.json_response(model_settings_view(self.providers_config), headers={"Cache-Control": "no-store"})

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
        self._apply_request_proxy(body.get("proxy"))
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
        body = await request.json()
        self._apply_request_proxy(body.get("proxy"))
        url = validate_page_url(str(body.get("url") or ""))
        auth = self._authentication(body, consume=True)
        probe_snapshot: ProbeInfoSnapshot | None = None
        auth_lease: SessionAuthLease | None = None
        pending_subtitle_pipeline: SubtitlePipeline | None = None
        try:
            # A new start owns a new generation. Fully dismantle the previous
            # sidecars, media process, stores and auth consumers before assigning
            # the next session lease.
            await self._stop_messages()
            await self._stop_subtitles()
            self.session.request_stop()
            if self.source_ingest:
                await asyncio.to_thread(self.source_ingest.stop)
                self.source_ingest = None
            await asyncio.to_thread(self.session.stop)
            self.private_hls_token = None
            if self.auth_lease:
                self.auth_lease.force_close()
                self.auth_lease = None

            auth_lease = SessionAuthLease(auth)
            self.auth_lease = auth_lease
            # The UI always probes before it can offer a quality to start, so
            # the extraction this used to repeat is normally seconds old. Live
            # manifests carry signed, expiring URLs, hence the short TTL.
            info = self._fresh_probe_info(url)
            if info is None:
                probe_consumer = auth_lease.acquire("start_probe")
                try:
                    info = await asyncio.to_thread(self.probe.extract, url, probe_consumer)
                finally:
                    probe_consumer.release()
                self.info_cache[url] = (time.monotonic(), info)
            live_message_request = body.get("liveMessages") or {}
            if live_message_request.get("enabled", True) and not bool(info.get("is_live")):
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
            await asyncio.gather(*start_tasks)
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
        except Exception:
            await self._stop_messages()
            await self._stop_subtitles()
            self.session.request_stop()
            if self.source_ingest:
                await asyncio.to_thread(self.source_ingest.stop)
                self.source_ingest = None
            await asyncio.to_thread(self.session.stop)
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
        return web.json_response({"ok": True, "quality": asdict(quality), "status": status})

    @staticmethod
    def _apply_request_proxy(value: Any) -> None:
        proxy = str(value or "").strip()
        if not proxy:
            return
        parsed = urllib.parse.urlparse(proxy)
        if parsed.scheme not in {"http", "https", "socks5"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("代理地址必须是 http(s)://host:port 或 socks5://host:port")
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
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
        await self._stop_messages()
        await self._stop_subtitles()
        self.session.request_stop()
        if self.source_ingest:
            await asyncio.to_thread(self.source_ingest.stop)
            self.source_ingest = None
        await asyncio.to_thread(self.session.stop)
        if self.auth_lease:
            self.auth_lease.force_close()
            self.auth_lease = None
        return web.json_response({"ok": True, "status": self.session.status()})

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
        asr_provider = create_asr(asr_config)
        translation_providers = [create_translation(item) for item in translation_configs]
        translation_pricing = {
            item["id"]: {
                "input": item.get("pricePerMillionInputTokensCny"),
                "cachedInput": item.get("pricePerMillionCachedInputTokensCny"),
                "cacheWrite": item.get("pricePerMillionCacheWriteTokensCny"),
                "output": item.get("pricePerMillionOutputTokensCny"),
            }
            for item in translation_configs
        }
        primary_translation = FallbackChain(translation_providers) if len(translation_providers) > 1 else translation_providers[0]
        subtitle = {**config.get("subtitle", {}), **request}
        source_policy = SourceLanguagePolicy.from_json(
            subtitle.get("sourceLanguage", {"mode": "specified", "tag": "ja"})
        )
        target_language = canonicalize_target_tag(subtitle.get("targetLanguage", "zh-Hans"))
        # Pre-start capability check: reject language settings the active
        # profiles cannot honor instead of failing silently mid-stream.
        validate_source_policy(source_policy, asr_provider.capabilities.language)
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
            # The two yt-dlp legs are independently extracted. Their first
            # MPEG-TS PTS values are not a shared epoch (each leg may start at
            # a different live-window boundary), so subtracting those first
            # samples produces a fixed but potentially large subtitle skew.
            # Use the continuously measured MediaAnchor instead; it samples
            # both legs only while they advance at real time and re-anchors
            # after skips.
            source_pts_mapper = None
        else:
            ingest_status = lambda: self.source_ingest.snapshot() if self.source_ingest else {}  # noqa: E731
            media_anchor = None
            anchor_probe = None
            source_pts_mapper = None

        pipeline = SubtitlePipeline(
            asr_provider=asr_provider,
            translation_provider=primary_translation,
            fallback_translation_provider=translation_providers[1] if len(translation_providers) > 1 else None,
            translation_pricing_by_provider=translation_pricing,
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
            source_pts_mapper=source_pts_mapper,
            hold_minimum=float(subtitle.get("holdSecondsMin", 1.2)),
            hold_maximum=float(subtitle.get("holdSecondsMax", 7.0)),
            hold_seconds_per_char=float(subtitle.get("holdSecondsPerChar", 0.06)),
            silence_duration_ms=int(asr_config.get("options", {}).get("turnDetection", {}).get("silenceDurationMs", 400)),
            translation_workers=int(subtitle.get("translationWorkers", 4)),
            translation_timeout_seconds=float(translation_configs[0].get("options", {}).get("timeoutSeconds", 6)),
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

    def _current_private_media_seconds(self) -> float | None:
        publisher = self.session.publisher if self.session else None
        return publisher.private_media_seconds if publisher is not None else None

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
        pipeline, self.subtitle_pipeline = self.subtitle_pipeline, None
        if pipeline:
            await pipeline.stop()
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
            print(f"[LagLingo] Native control IPC unavailable: {error}", file=sys.stderr)

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
        await self._stop_messages()
        await self._stop_subtitles()
        self.session.request_stop()
        if self.source_ingest:
            await asyncio.to_thread(self.source_ingest.stop)
            self.source_ingest = None
        await asyncio.to_thread(self.session.stop)
        if self.auth_lease:
            self.auth_lease.force_close()
            self.auth_lease = None
        if self.control is not None:
            await asyncio.to_thread(self.control.stop)
        with self.auth_lock:
            self.auth_snapshots.clear()


@web.middleware
async def errors(request: web.Request, handler: Any) -> web.StreamResponse:
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except (ValueError, RuntimeError, asyncio.TimeoutError) as error:
        return web.json_response({"error": str(error)}, status=400)
    except Exception as error:  # Prototype boundary: return diagnostics, never secrets.
        print(f"[LagLingo] Unexpected request failure: {type(error).__name__}: {error}", file=sys.stderr)
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
    print(f"[LagLingo] Prototype 2 player: http://{args.host}:{args.port}/")
    print(f"[LagLingo] Target total live delay: {DEFAULT_TARGET_DELAY_SECONDS:g}s")
    print("[LagLingo] Cookies are accepted only through Native Messaging or the explicit development browser fallback.")
    try:
        web.run_app(app, host=args.host, port=args.port, print=None, handle_signals=True)
    except OSError as error:
        if getattr(error, "winerror", None) == 10048 or getattr(error, "errno", None) == errno.EADDRINUSE:
            print(
                f"[LagLingo] Port {args.port} is already in use; another Companion is probably still running.",
                file=sys.stderr,
            )
            print(
                "[LagLingo] Close the other instance (or run: taskkill /IM python.exe /F) or start this one with --port 8766.",
                file=sys.stderr,
            )
            return 1
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
