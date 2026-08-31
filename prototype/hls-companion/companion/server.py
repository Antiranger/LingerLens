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
    from .providers import create_asr, create_translation  # type: ignore[import-not-found]
    from .providers.base import StreamMeta  # type: ignore[import-not-found]
    from .providers.config import load_config, masked_config, model_settings_view, update_config, update_model_settings  # type: ignore[import-not-found]
    from .providers.fallback import FallbackChain  # type: ignore[import-not-found]
    from .subtitle_pipeline import SubtitlePipeline  # type: ignore[import-not-found]
    from .subtitle_store import CueStore  # type: ignore[import-not-found]
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
except ImportError:  # Direct script execution.
    from control_ipc import ControlServer  # type: ignore[import-not-found]
    from ytdlp_ingest import YtDlpLiveIngest  # type: ignore[import-not-found]
    from companion.providers import create_asr, create_translation  # type: ignore[import-not-found]
    from companion.providers.base import StreamMeta  # type: ignore[import-not-found]
    from companion.providers.config import load_config, masked_config, model_settings_view, update_config, update_model_settings  # type: ignore[import-not-found]
    from companion.providers.fallback import FallbackChain  # type: ignore[import-not-found]
    from companion.subtitle_pipeline import SubtitlePipeline  # type: ignore[import-not-found]
    from companion.subtitle_store import CueStore  # type: ignore[import-not-found]
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

ROOT = Path(__file__).resolve().parents[1]
WEB_PLAYER = ROOT / "web-player"
DEFAULT_RUNTIME = ROOT / "runtime" / "media"
DEFAULT_PROVIDERS = ROOT / "runtime" / "providers.json"
# How long an /api/probe extraction may be reused by /api/start. Live manifests
# are signed and the live edge keeps moving, so this stays far below any expiry.
PROBE_INFO_TTL_SECONDS = 90.0


CRITICAL_LOGIN_COOKIES = (
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
)


class CompanionApplication:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.probe = YtDlpProbe()
        self.session = LiveSession(args.runtime_dir)
        self.info_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self.auth_snapshots: dict[str, list[dict[str, Any]]] = {}
        self.auth_lock = threading.Lock()
        self.control = ControlServer(self.handle_control)
        self.source_ingest: YtDlpLiveIngest | None = None
        self.providers_path = args.providers_file
        self.providers_config = load_config(self.providers_path)
        self.auth_file = Path(args.providers_file).parent / "auth-snapshot.json"
        self.persisted_auth: list[dict[str, Any]] | None = self._load_persisted_auth()
        self.subtitle_pipeline: SubtitlePipeline | None = None
        self.subtitle_store = CueStore()
        self.subtitle_last_error: str | None = None

    def routes(self) -> web.Application:
        app = web.Application(client_max_size=4 * 1024 * 1024)
        app["companion"] = self
        app.router.add_get("/", self.index)
        app.router.add_get("/favicon.ico", self.favicon)
        app.router.add_get("/player.js", self.static_file)
        app.router.add_get("/subtitle-scheduler.js", self.static_file)
        app.router.add_get("/style.css", self.static_file)
        app.router.add_get("/vendor/{name}", self.static_file)
        app.router.add_get("/hls/{name}", self.hls_file)
        app.router.add_get("/api/status", self.status)
        app.router.add_get("/api/subtitles", self.handle_subtitles)
        app.router.add_post("/api/publish-delay", self.handle_publish_delay)
        app.router.add_get("/api/providers", self.handle_get_providers)
        app.router.add_post("/api/providers", self.handle_update_providers)
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
        return web.FileResponse(path)

    async def hls_file(self, request: web.Request) -> web.FileResponse:
        name = request.match_info["name"]
        if not name.endswith((".m3u8", ".m4s", ".mp4")) or Path(name).name != name:
            raise web.HTTPNotFound()
        path = self.session.public_dir / name
        if not path.exists():
            raise web.HTTPNotFound()
        headers = {"Cache-Control": "no-store, max-age=0"} if name.endswith(".m3u8") else {"Cache-Control": "private, max-age=30"}
        return web.FileResponse(path, headers=headers)

    async def status(self, _: web.Request) -> web.Response:
        status = self.session.status()
        ingest_snapshot = self.source_ingest.snapshot() if self.source_ingest else None
        status["sourceDelaySeconds"] = 0.0
        status["sourceIngest"] = [ingest_snapshot] if ingest_snapshot else []
        status["subtitles"] = self._subtitle_status()
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

    async def handle_publish_delay(self, request: web.Request) -> web.Response:
        """Live-tune the publisher's delay budget (redesign Fix E).

        DelayedPlaylistPublisher._tick re-reads ``publish_delay`` every
        iteration, so this takes effect immediately without a session restart.
        """
        body = await request.json()
        try:
            seconds = float(body.get("seconds"))
        except (TypeError, ValueError) as exc:
            raise ValueError("publish delay must be a number of seconds") from exc
        if not 0.0 <= seconds <= 60.0:
            raise ValueError("publish delay must be between 0 and 60 seconds")
        publisher = self.session.publisher
        if publisher is None:
            raise RuntimeError("no active live session")
        publisher.publish_delay = seconds
        return web.json_response({"ok": True, **publisher.snapshot()}, headers={"Cache-Control": "no-store"})

    async def handle_import_cookies(self, request: web.Request) -> web.Response:
        self._require_local_request(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("cookie import must be an object")
        if body.get("netscape"):
            raw = parse_netscape_cookies(str(body["netscape"]))
        elif body.get("lines"):
            raw = parse_name_value_lines(str(body["lines"]), str(body.get("domain") or ".youtube.com"))
        elif body.get("header"):
            raw = parse_header_cookies(str(body["header"]), str(body.get("domain") or ".youtube.com"))
        else:
            raise ValueError("Provide either a Netscape cookies.txt export or a cookie header")
        cookies = normalize_imported_cookies(raw)
        if not cookies:
            raise ValueError("No usable YouTube/Google/Bilibili cookies were found in the import")
        token = secrets.token_urlsafe(24)
        with self.auth_lock:
            self.auth_snapshots[token] = cookies
        persisted = self._persist_auth(cookies)
        names = sorted({cookie["name"] for cookie in cookies})
        missing_critical = [name for name in CRITICAL_LOGIN_COOKIES if name not in names]
        return web.json_response(
            {
                "ok": True,
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

    async def handle_get_model_settings(self, _: web.Request) -> web.Response:
        self.providers_config = load_config(self.providers_path)
        return web.json_response(model_settings_view(self.providers_config), headers={"Cache-Control": "no-store"})

    async def handle_update_model_settings(self, request: web.Request) -> web.Response:
        settings = await request.json()
        if not isinstance(settings, dict):
            raise ValueError("model settings must be an object")
        self.providers_config = update_model_settings(self.providers_path, settings)
        return web.json_response(model_settings_view(self.providers_config), headers={"Cache-Control": "no-store"})

    async def handle_get_providers(self, _: web.Request) -> web.Response:
        self.providers_config = load_config(self.providers_path)
        return web.json_response(masked_config(self.providers_config), headers={"Cache-Control": "no-store"})

    async def handle_update_providers(self, request: web.Request) -> web.Response:
        patch = await request.json()
        if not isinstance(patch, dict):
            raise ValueError("provider update must be an object")
        self.providers_config = update_config(self.providers_path, patch)
        return web.json_response(masked_config(self.providers_config), headers={"Cache-Control": "no-store"})

    async def handle_probe(self, request: web.Request) -> web.Response:
        body = await request.json()
        url = validate_page_url(str(body.get("url") or ""))
        auth = self._authentication(body, consume=False)
        try:
            info = await asyncio.to_thread(self.probe.extract, url, auth)
        finally:
            auth.close()
        options = build_quality_options(info)
        self.info_cache[url] = (time.monotonic(), info)
        if not options:
            raise web.HTTPUnprocessableEntity(text=json.dumps({"error": "No usable video/audio qualities were returned by yt-dlp"}), content_type="application/json")
        return web.json_response(
            {
                "title": info.get("title"),
                "channel": info.get("channel") or info.get("uploader"),
                "platform": info.get("extractor_key") or info.get("extractor"),
                "isLive": bool(info.get("is_live")),
                "qualities": [asdict(option) for option in options],
            }
        )

    async def handle_start(self, request: web.Request) -> web.Response:
        body = await request.json()
        url = validate_page_url(str(body.get("url") or ""))
        auth = self._authentication(body, consume=True)
        probe_snapshot: ProbeInfoSnapshot | None = None
        try:
            # The UI always probes before it can offer a quality to start, so
            # the extraction this used to repeat is normally seconds old. Live
            # manifests carry signed, expiring URLs, hence the short TTL.
            info = self._fresh_probe_info(url)
            if info is None:
                info = await asyncio.to_thread(self.probe.extract, url, auth)
                self.info_cache[url] = (time.monotonic(), info)
            options = build_quality_options(info)
            quality = select_quality(options, str(body.get("qualityId") or "auto"), int(body.get("maxHeight") or 1080))
            inputs = selected_inputs(info, quality)
            publish_delay = max(0.0, min(float(body.get("publishDelaySeconds") or self.args.publish_delay), 60.0))
            await asyncio.to_thread(self.session.stop)
            await self._stop_subtitles()
            if self.source_ingest:
                await asyncio.to_thread(self.source_ingest.stop)
            format_selector = quality.videoFormatId + (f"+{quality.audioFormatId}" if quality.audioFormatId else "")
            # Redesign Fix A: the subtitle tee must be installed on the audio
            # pump *at construction*, before any byte can flow, so both legs
            # share byte 0. Order: pipeline (returns sink) -> ingest(sink) ->
            # ingest.start() -> session.start() (packaging ffmpeg connects;
            # bytes start flowing through both legs simultaneously).
            audio_tee = None
            subtitle_request = body.get("subtitles") or {}
            if subtitle_request.get("enabled"):
                try:
                    audio_tee = await self._prepare_subtitles(info, subtitle_request)
                except Exception as error:
                    # A subtitle failure must never fail playback; report it.
                    self.subtitle_last_error = f"{type(error).__name__}: {error}"
                    audio_tee = None
            # Hand the extraction result to yt-dlp so the download leg does not
            # repeat it. Both the cookie file and this snapshot are secrets with
            # the same lifetime, so they are torn down by one callback once
            # every leg has reached its download stage.
            probe_snapshot = ProbeInfoSnapshot(info)

            def release_start_secrets() -> None:
                try:
                    auth.close()
                finally:
                    probe_snapshot.close()

            self.source_ingest = YtDlpLiveIngest(
                url,
                format_selector,
                auth.yt_dlp_args(),
                auth_cleanup=release_start_secrets,
                audio_tee=audio_tee,
                info_json_path=probe_snapshot.path,
            )
            await asyncio.to_thread(self.source_ingest.start)
            # The ingest owns acquisition on independent per-format legs and
            # exposes them as localhost MPEG-TS TCP endpoints; the packaging
            # ffmpeg reads those instead of a single muxed pipe.
            command = build_ffmpeg_command(
                inputs,
                self.session.private_dir,
                input_urls=self.source_ingest.input_urls(),
            )
            try:
                await asyncio.to_thread(self.session.start, url, inputs, publish_delay, command)
            except Exception:
                await asyncio.to_thread(self.source_ingest.stop)
                self.source_ingest = None
                await self._stop_subtitles()
                raise
        finally:
            if not self.source_ingest:
                auth.close()
                if probe_snapshot is not None:
                    probe_snapshot.close()
        return web.json_response({"ok": True, "quality": asdict(quality), "status": self.session.status()})

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
        await self._stop_subtitles()
        if self.source_ingest:
            await asyncio.to_thread(self.source_ingest.stop)
            self.source_ingest = None
        await asyncio.to_thread(self.session.stop)
        return web.json_response({"ok": True, "status": self.session.status()})

    async def _prepare_subtitles(self, info: dict[str, Any], request: dict[str, Any]):
        """Build providers + pipeline and return its tee sink (redesign Fix A).

        Called BEFORE the yt-dlp ingest is constructed so the sink is present
        at pump construction time and observes the stream's byte 0.
        """
        config = load_config(self.providers_path)
        self.providers_config = config
        asr_id = request.get("asrProviderId") or config["asr"]["active"]
        translation_id = request.get("translationProviderId") or config["translation"]["active"]
        asr_config = self._provider_record(config["asr"], asr_id)
        translation_configs = [self._provider_record(config["translation"], translation_id)]
        translation_configs.extend(
            self._provider_record(config["translation"], provider_id)
            for provider_id in config["translation"].get("fallback", [])
            if provider_id != translation_id
        )
        asr_provider = create_asr(asr_config)
        translation_providers = [create_translation(item) for item in translation_configs]
        primary_translation = FallbackChain(translation_providers) if len(translation_providers) > 1 else translation_providers[0]
        subtitle = {**config.get("subtitle", {}), **request}
        source_language = str(subtitle.get("sourceLanguage") or "ja")
        target_language = str(subtitle.get("targetLanguage") or "zh")
        self.subtitle_store = CueStore()
        self.subtitle_last_error = None

        def media_clock():
            """Packaging-leg media position for the subtitle MediaAnchor."""
            publisher = self.session.publisher
            if publisher is None:
                return None
            seconds = publisher.private_media_seconds
            if seconds <= 0:
                return None
            return (seconds, publisher.target_duration)

        pipeline = SubtitlePipeline(
            asr_provider=asr_provider,
            translation_provider=primary_translation,
            fallback_translation_provider=translation_providers[1] if len(translation_providers) > 1 else None,
            cue_store=self.subtitle_store,
            meta=StreamMeta(
                info.get("title"),
                info.get("channel") or info.get("uploader"),
                str(info.get("categories", [""])[0] or "") if info.get("categories") else None,
                source_language,
                target_language,
            ),
            source_language=source_language,
            pdt_epoch=lambda: self.session.publisher.pdt_epoch if self.session.publisher else None,
            media_clock=media_clock,
            ingest_status=lambda: self.source_ingest.snapshot() if self.source_ingest else {},
            hold_minimum=float(subtitle.get("holdSecondsMin", 1.2)),
            hold_maximum=float(subtitle.get("holdSecondsMax", 7.0)),
            hold_seconds_per_char=float(subtitle.get("holdSecondsPerChar", 0.06)),
            silence_duration_ms=int(asr_config.get("options", {}).get("turnDetection", {}).get("silenceDurationMs", 400)),
            max_utterance_seconds=float(subtitle.get("maxUtteranceSeconds", 0.0)),
            prefix_split_enabled=bool(subtitle.get("prefixSplitEnabled", True)),
            prefix_split_after_seconds=float(subtitle.get("prefixSplitAfterSeconds", 3.0)),
            translation_workers=int(subtitle.get("translationWorkers", 4)),
            translation_timeout_seconds=float(translation_configs[0].get("options", {}).get("timeoutSeconds", 6)),
            context_pairs=int(translation_configs[0].get("options", {}).get("contextPairs", 10)),
            context_seconds=float(translation_configs[0].get("options", {}).get("contextSeconds", 90)),
        )
        sink = await pipeline.start()
        self.subtitle_pipeline = pipeline
        return sink

    async def _stop_subtitles(self) -> None:
        if self.source_ingest:
            self.source_ingest.detach_audio_tee()
        pipeline, self.subtitle_pipeline = self.subtitle_pipeline, None
        if pipeline:
            await pipeline.stop()

    def _subtitle_status(self) -> dict[str, Any]:
        if self.subtitle_pipeline:
            return self.subtitle_pipeline.status()
        return {
            "running": False,
            "asrSeconds": 0.0,
            "estimatedCostCny": 0.0,
            "teeDropped": self.source_ingest.tee_snapshot()["teeDropped"] if self.source_ingest else 0,
            "translationBacklog": 0,
            "degradeLevel": 0,
            "lastError": self.subtitle_last_error,
        }

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

    def _load_persisted_auth(self) -> list[dict[str, Any]] | None:
        """Reload a previously imported cookie snapshot after a restart."""
        try:
            raw = json.loads(self.auth_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict) or not isinstance(raw.get("cookies"), list):
            return None
        return normalize_imported_cookies([dict(item) for item in raw["cookies"] if isinstance(item, dict)]) or None

    def _persist_auth(self, cookies: list[dict[str, Any]]) -> bool:
        """Save the imported snapshot to a user-private local file (best effort)."""
        self.persisted_auth = cookies
        try:
            self.auth_file.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({"version": 1, "cookies": cookies}, ensure_ascii=False, indent=2) + "\n"
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
        # Fall back to the last imported snapshot so restarts keep the login
        # without the player having to re-import.
        if self.persisted_auth:
            return BrowserCookieSnapshot(self.persisted_auth)
        browser = body.get("cookiesFromBrowser") or self.args.cookies_from_browser
        return DevelopmentBrowserProfileFallback(str(browser) if browser else None)

    async def startup(self, _: web.Application) -> None:
        try:
            await asyncio.to_thread(self.control.start)
        except PermissionError as error:
            # A stale/other Native Messaging pipe must not take down the HTTP
            # player or its localhost model APIs. Cookie IPC can be restored by
            # closing the old Companion and restarting later.
            print(f"[LagLingo] Native control IPC unavailable: {error}", file=sys.stderr)

    async def cleanup(self, _: web.Application) -> None:
        await self._stop_subtitles()
        if self.source_ingest:
            await asyncio.to_thread(self.source_ingest.stop)
            self.source_ingest = None
        await asyncio.to_thread(self.session.stop)
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
    parser.add_argument("--publish-delay", type=float, default=3.0, help="Additional post-download publication delay. Most of the ~15s total budget now comes from the player deliberately sitting behind the live edge (player.js liveSyncDurationCount), which is what actually buys rebuffer headroom")
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
    print(f"[LagLingo] Server-held media delay: {args.publish_delay:g}s (target total near 10s)")
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
