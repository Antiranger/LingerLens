#!/usr/bin/env python3
"""Core of LingerLens Prototype 2: probe, select, remux, and delay-publish live HLS."""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import urllib.parse
from datetime import datetime
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
import sys
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
_COMPANION_DIR = Path(__file__).resolve().parent
if str(_COMPANION_DIR) not in sys.path:
    sys.path.insert(0, str(_COMPANION_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from .capture_clock import CaptureClock  # type: ignore[import-not-found]
except ImportError:
    try:
        from companion.capture_clock import CaptureClock  # type: ignore[import-not-found]
    except ImportError:
        from capture_clock import CaptureClock  # type: ignore[import-not-found]
try:
    from . import logbook  # type: ignore[import-not-found]
except ImportError:
    try:
        from companion import logbook  # type: ignore[import-not-found]
    except ImportError:
        import logbook  # type: ignore[import-not-found,no-redef]
VENDORED_YT_DLP = ROOT / "vendor" / "yt-dlp" / "yt-dlp.exe"
SUPPORTED_COOKIE_SUFFIXES = ("youtube.com", "google.com", "bilibili.com", "twitch.tv")
VIDEO_CODEC_PREFIXES = ("avc1", "avc", "h264")
AUDIO_CODEC_PREFIXES = ("mp4a", "aac")


def executable(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Required executable not found on PATH: {name}")
    return path


def validate_page_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Only HTTPS YouTube/Bilibili/Twitch live page URLs are accepted")
    hostname = parsed.hostname.lower().rstrip(".")
    parts = [part for part in parsed.path.split("/") if part]
    if hostname == "youtu.be" or hostname == "youtube.com" or hostname.endswith(".youtube.com"):
        return url
    if hostname == "live.bilibili.com" and parts and parts[0].isdigit():
        return url
    if hostname in {"twitch.tv", "www.twitch.tv"} and len(parts) == 1 and re.fullmatch(r"[A-Za-z0-9_]+", parts[0]):
        return url
    raise ValueError("Only ongoing YouTube lives, Bilibili live rooms, and Twitch channels are accepted")


def validate_media_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("yt-dlp returned an unsupported media URL")
    return url


def compatible_video(codec: str | None) -> bool:
    value = (codec or "").lower()
    return value not in {"", "none"} and value.startswith(VIDEO_CODEC_PREFIXES)


def compatible_audio(codec: str | None) -> bool:
    value = (codec or "").lower()
    return value not in {"", "none"} and value.startswith(AUDIO_CODEC_PREFIXES)


def has_video(item: dict[str, Any]) -> bool:
    return (item.get("vcodec") or "none") != "none"


def has_audio(item: dict[str, Any]) -> bool:
    return (item.get("acodec") or "none") != "none"


def codec_family(codec: str | None) -> str:
    return (codec or "none").split(".", 1)[0].lower()


def safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class QualityOption:
    qualityId: str
    label: str
    width: int | None
    height: int | None
    fps: float | None
    videoCodec: str
    audioCodec: str
    separateAudio: bool
    requiresTranscode: bool
    estimatedBitrate: int | None
    videoFormatId: str
    audioFormatId: str | None


@dataclass(frozen=True)
class SelectedInputs:
    quality: QualityOption
    video_url: str
    audio_url: str | None
    video_headers: dict[str, str]
    audio_headers: dict[str, str]


class AuthenticationProvider:
    """Marker boundary for future browser-cookie and PO-token implementations."""

    def yt_dlp_args(self) -> list[str]:
        return []

    def close(self) -> None:
        return None


class DevelopmentBrowserProfileFallback(AuthenticationProvider):
    def __init__(self, browser: str | None):
        if browser not in {None, "chrome", "edge"}:
            raise ValueError("Development browser fallback must be chrome or edge")
        self.browser = browser

    def yt_dlp_args(self) -> list[str]:
        return ["--cookies-from-browser", self.browser] if self.browser else []


class BrowserCookieSnapshot(AuthenticationProvider):
    """Materialize extension cookies briefly because the installed yt-dlp is CLI-only."""

    def __init__(self, cookies: Iterable[dict[str, Any]]):
        self._path: Path | None = None
        self.cookies = [dict(cookie) for cookie in cookies]
        rows = [self._netscape_row(cookie) for cookie in self.cookies]
        rows = [row for row in rows if row]
        if not rows:
            return
        fd, raw_path = tempfile.mkstemp(prefix="lingerlens-cookies-", suffix=".txt")
        self._path = Path(raw_path)
        try:
            os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
                output.write("# Netscape HTTP Cookie File\n")
                output.writelines(rows)
        except BaseException:
            os.close(fd)
            self.close()
            raise

    @staticmethod
    def _netscape_row(cookie: dict[str, Any]) -> str | None:
        domain = str(cookie.get("domain") or "").strip().lower()
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if not domain or not name or "\t" in name or "\t" in value or "\n" in value or "\r" in value:
            return None
        bare_domain = domain.lstrip(".")
        if not any(bare_domain == suffix or bare_domain.endswith(f".{suffix}") for suffix in SUPPORTED_COOKIE_SUFFIXES):
            return None
        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        path = str(cookie.get("path") or "/").replace("\t", "")
        secure = "TRUE" if cookie.get("secure") else "FALSE"
        expires = int(safe_float(cookie.get("expirationDate")) or 0)
        return f"{domain}\t{include_subdomains}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n"

    def yt_dlp_args(self) -> list[str]:
        return ["--cookies", str(self._path)] if self._path else []

    def close(self) -> None:
        if self._path:
            try:
                self._path.unlink(missing_ok=True)
            finally:
                self._path = None

    def __enter__(self) -> "BrowserCookieSnapshot":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class ProbeInfoSnapshot:
    """Materialize a probe result so the live yt-dlp can skip re-extraction.

    /api/probe already paid for a full YouTube extraction; without this the
    download leg pays for it a second time before its first byte. The file
    holds signed media URLs, so it gets the same treatment as the cookie
    snapshot: owner-only permissions, never logged, and unlinked as soon as
    every leg has reached its download stage.
    """

    def __init__(self, info: dict[str, Any], selected_format_ids: Iterable[str] = ()):
        self._path: Path | None = None
        fd, raw_path = tempfile.mkstemp(prefix="lingerlens-info-", suffix=".json")
        self._path = Path(raw_path)
        selected = {str(value) for value in selected_format_ids}
        snapshot = info
        extractor = str(info.get("extractor_key") or info.get("extractor") or "").lower()
        if extractor == "bililive" and selected:
            snapshot = dict(info)
            snapshot["formats"] = [dict(item) for item in info.get("formats") or []]
            for item in snapshot["formats"]:
                if (
                    str(item.get("format_id") or "") in selected
                    and str(item.get("protocol") or "").startswith("m3u8")
                    and str(item.get("ext") or "").lower() == "fmp4"
                ):
                    # Trusted BiliLive HLS metadata sometimes calls the stream
                    # extension fmp4. yt-dlp's stdout safety check rejects that
                    # uncommon extension before --hls-use-mpegts can normalize
                    # the bytes. Hint only the selected trusted HLS format as mp4;
                    # the downloader still emits MPEG-TS and unsafe checks remain on.
                    item["ext"] = "mp4"
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
                json.dump(snapshot, output)
        except BaseException:
            self.close()
            raise

    @property
    def path(self) -> str | None:
        return str(self._path) if self._path else None

    def close(self) -> None:
        if self._path:
            try:
                self._path.unlink(missing_ok=True)
            finally:
                self._path = None


def _imported_domain_allowed(domain: str) -> bool:
    bare = domain.strip().lower().lstrip(".")
    return any(bare == suffix or bare.endswith(f".{suffix}") for suffix in SUPPORTED_COOKIE_SUFFIXES)


def parse_netscape_cookies(text: str) -> list[dict[str, Any]]:
    """Parse a Netscape cookies.txt export into snapshot-shaped cookie dicts."""
    cookies: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, _flag, path, secure, expires, name, value = parts
        if not name or "\t" in name or "\n" in value or "\r" in value:
            continue
        try:
            expiration = int(float(expires))
        except ValueError:
            expiration = 0
        cookies.append(
            {
                "domain": domain,
                "path": path or "/",
                "name": name,
                "value": value,
                "secure": secure.strip().upper() == "TRUE",
                "expirationDate": expiration,
            }
        )
    return cookies


def parse_header_cookies(header: str, domain: str = ".youtube.com") -> list[dict[str, Any]]:
    """Parse a `name=value; name2=value2` request header into cookie dicts."""
    cookies: list[dict[str, Any]] = []
    for part in header.split(";"):
        if "=" not in part:
            continue
        name, _, value = part.strip().partition("=")
        name = name.strip()
        value = value.strip()
        if not name or "\t" in name or "\n" in value or "\r" in value:
            continue
        cookies.append(
            {
                "domain": domain if domain.startswith(".") else f".{domain}",
                "path": "/",
                "name": name,
                "value": value,
                "secure": True,
                "expirationDate": int(time.time()) + 30 * 86400,
            }
        )
    return cookies


def parse_name_value_lines(text: str, domain: str = ".youtube.com") -> list[dict[str, Any]]:
    """Parse pasted `name<TAB>value`, `name=value`, or `name value` rows.

    This matches both the short ``Name<TAB>Value`` form and the full cookie
    table rows that Chromium/Firefox copy (name, value, domain, path, expiry,
    and flags).  Keeping the original domain matters for YouTube: some login
    cookies belong to ``google.com`` rather than ``youtube.com``.
    """
    cookies: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            fields = line.split("\t")
            if len(fields) < 2:
                continue
            name, value = fields[0], fields[1]
            row_domain = fields[2].strip() if len(fields) >= 3 else domain
            row_path = fields[3].strip() if len(fields) >= 4 else "/"
            row_expiry = fields[4].strip() if len(fields) >= 5 else ""
            # The exact flag columns differ between browsers and locales.  A
            # short two-column paste is treated as secure as before; a full
            # row keeps an explicit secure/true marker when one is present.
            row_flags = [field.strip().lower() for field in fields[5:]]
            row_secure = len(fields) < 6 or any(
                flag in {"secure", "true", "yes", "✓", "是", "ja", "да"}
                for flag in row_flags
            )
        elif "=" in line:
            name, _, value = line.partition("=")
            row_domain, row_path, row_expiry, row_secure = domain, "/", "", True
        else:
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            name, value = parts[0], parts[1]
            row_domain, row_path, row_expiry, row_secure = domain, "/", "", True
        name = name.strip()
        value = value.strip()
        if not name or name.lower() in {"name", "名称", "名前"} or "\t" in name or "\n" in value or "\r" in value:
            continue
        expiration = 0
        if row_expiry and row_expiry.lower() not in {"session", "session cookie", "会话"}:
            try:
                expiration = int(float(row_expiry))
            except ValueError:
                try:
                    expiration = int(datetime.fromisoformat(row_expiry.replace("Z", "+00:00")).timestamp())
                except ValueError:
                    expiration = 0
        cookies.append(
            {
                "domain": row_domain or domain,
                "path": row_path or "/",
                "name": name,
                "value": value,
                "secure": row_secure,
                "expirationDate": expiration,
            }
        )
    return cookies


def normalize_imported_cookies(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter imports to allowlisted platform domains and deduplicate entries."""
    seen: set[tuple[str, str, str]] = set()
    accepted: list[dict[str, Any]] = []
    for cookie in cookies:
        domain = str(cookie.get("domain") or "").strip()
        name = str(cookie.get("name") or "")
        path = str(cookie.get("path") or "/")
        value = str(cookie.get("value") or "")
        if not domain or not name or not _imported_domain_allowed(domain):
            continue
        if "\t" in name or "\t" in value or "\n" in value or "\r" in value:
            continue
        key = (domain.lower(), path, name)
        if key in seen:
            continue
        seen.add(key)
        accepted.append(
            {
                "domain": domain,
                "path": path,
                "name": name,
                "value": value,
                "secure": bool(cookie.get("secure")),
                "expirationDate": int(safe_float(cookie.get("expirationDate")) or 0),
            }
        )
    return accepted


# The probe asks yt-dlp for these fields and nothing else. It used to ask for the
# whole info document, which on a DVR live stream carries every media fragment
# since the broadcast started: 88,456 fragments / 153MB / 16.2s on a two-hour
# stream (2026-09-19), against a hard 15s deadline that the same stream still met
# at twenty minutes old. No code here reads `fragments`, so that payload was
# carried across the pipe only to be discarded. Everything below is read:
# build_quality_options and _build_muxed_live_quality_options (height, width, fps,
# vcodec, acodec, tbr, abr, format_id, protocol, ext, quality, format_note),
# best_aac_audio (abr, tbr, protocol, format_id), selected_inputs (format_id, url,
# http_headers), ProbeInfoSnapshot (extractor_key, formats[].ext), server.py
# (title, channel, uploader, categories, is_live) and the live gate below
# (is_live, live_status).
PROBE_INFO_FIELDS = (
    "id", "title", "channel", "uploader", "categories", "is_live", "live_status",
    "extractor", "extractor_key", "webpage_url", "duration", "http_headers",
)
PROBE_FORMAT_FIELDS = (
    "format_id", "url", "ext", "protocol", "vcodec", "acodec", "height", "width",
    "fps", "tbr", "abr", "vbr", "audio_channels", "quality", "format_note",
    "filesize", "http_headers",
)


def _projection(fields: tuple[str, ...], prefix: str = "") -> str:
    """An output template that prints exactly `fields` as one JSON value."""
    return "%(" + prefix + ".{" + ",".join(fields) + "})j"


def parse_probe_output(text: str) -> dict[str, Any]:
    """The info dict from the probe's two projected JSON lines.

    yt-dlp prints one line per requested projection, so the metadata object comes
    first and the format list second. A single full document is still accepted,
    which keeps an older invocation working.
    """
    top: dict[str, Any] | None = None
    formats: list[dict[str, Any]] | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            if "formats" in value:
                return value
            top = top or value
        elif isinstance(value, list) and formats is None:
            formats = value
    if top is None:
        raise RuntimeError("yt-dlp returned invalid JSON")
    # A missing format list is not an error here: the caller gates on `is_live`
    # first, and an empty list already fails as "no compatible quality".
    return {**top, "formats": formats or []}


class YtDlpProbe:
    def __init__(self, yt_dlp: str | None = None):
        if yt_dlp:
            self.yt_dlp = yt_dlp
        elif VENDORED_YT_DLP.is_file():
            self.yt_dlp = str(VENDORED_YT_DLP)
        else:
            self.yt_dlp = executable("yt-dlp")

    def extract(self, page_url: str, auth: AuthenticationProvider | None = None) -> dict[str, Any]:
        validate_page_url(page_url)
        command = [
            self.yt_dlp,
            "--no-config",
            "--no-playlist",
            "--no-warnings",
            "--skip-download",
            "--no-live-from-start",
            "--socket-timeout",
            "15",
            "--retries",
            "1",
            "--extractor-retries",
            "1",
            "--print",
            _projection(PROBE_INFO_FIELDS),
            "--print",
            _projection(PROBE_FORMAT_FIELDS, "formats.:"),
            *([] if auth is None else auth.yt_dlp_args()),
            page_url,
        ]
        try:
            completed = subprocess.run(
                # The desktop entry keeps its own stdin open for the stop
                # control pipe. yt-dlp never needs stdin; do not let it
                # inherit that pipe or compete with the parent reader.
                command, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=15, check=False,
            )
        except subprocess.TimeoutExpired as error:
            # yt-dlp exceeding 15s says nothing about the proxy in particular:
            # it is the slowest thing on this path, so name the timeout itself.
            raise RuntimeError("读取直播信息超时：yt-dlp 15 秒内没有返回。请重试，或检查网络与系统代理。") from error
        if completed.returncode != 0:
            tail = "\n".join(completed.stderr.strip().splitlines()[-8:])
            raise RuntimeError(f"yt-dlp format probe failed (exit {completed.returncode}): {tail or 'no diagnostic'}")
        info = parse_probe_output(completed.stdout)
        if info.get("is_live") is not True and info.get("live_status") != "is_live":
            raise RuntimeError("仅支持正在进行的直播 / Only currently ongoing live streams are supported")
        return info


def best_aac_audio(formats: list[dict[str, Any]]) -> dict[str, Any] | None:
    audio = [item for item in formats if has_audio(item) and not has_video(item) and compatible_audio(item.get("acodec"))]
    known = max(audio, key=lambda item: safe_float(item.get("abr") or item.get("tbr")) or 0, default=None)
    if known:
        return known
    # Current yt-dlp can expose YouTube HLS audio renditions with an unknown
    # codec in probe JSON even though the selected live output is AAC. Prefer
    # the high/default HLS audio rendition and verify the actual codec in FFmpeg.
    hls_audio = [
        item
        for item in formats
        if not has_video(item)
        and str(item.get("protocol") or "").startswith("m3u8")
        and str(item.get("format_id") or "") in {"233", "234"}
    ]
    return max(hls_audio, key=lambda item: str(item.get("format_id")), default=None)


def build_quality_options(info: dict[str, Any]) -> list[QualityOption]:
    formats = [item for item in info.get("formats") or [] if item.get("url")]
    extractor = str(info.get("extractor_key") or info.get("extractor") or "").lower()
    if extractor in {"bililive", "twitchstream"}:
        return _build_muxed_live_quality_options(formats, extractor)
    audio = best_aac_audio(formats)
    candidates: list[tuple[QualityOption, dict[str, Any], dict[str, Any] | None]] = []

    for item in formats:
        if not has_video(item):
            continue
        height = int(item["height"]) if item.get("height") else None
        if not height:
            continue
        width = int(item["width"]) if item.get("width") else None
        fps = safe_float(item.get("fps"))
        muxed = has_audio(item)
        paired_audio = None if muxed else audio
        audio_codec = item.get("acodec") if muxed else (paired_audio or {}).get("acodec")
        if not muxed and not paired_audio:
            continue
        video_codec = str(item.get("vcodec") or "none")
        audio_codec = str(audio_codec or "none")
        assumed_youtube_hls_aac = bool(paired_audio and str(paired_audio.get("format_id") or "") in {"233", "234"})
        requires_transcode = not (compatible_video(video_codec) and (compatible_audio(audio_codec) or assumed_youtube_hls_aac))
        if assumed_youtube_hls_aac:
            audio_codec = "aac (verified on ingest)"
        suffix = "muxed" if muxed else "separate"
        fps_suffix = str(int(fps)) if fps and fps > 30 else ""
        quality_id = f"{height}p{fps_suffix}-{codec_family(video_codec)}-{suffix}-{item.get('format_id')}"
        tbr = safe_float(item.get("tbr")) or 0
        if paired_audio:
            tbr += safe_float(paired_audio.get("abr") or paired_audio.get("tbr")) or 0
        option = QualityOption(
            qualityId=quality_id,
            label=f"{height}p{fps_suffix or ''} · {'AVC/AAC copy' if not requires_transcode else 'incompatible'} · {suffix}",
            width=width,
            height=height,
            fps=fps,
            videoCodec=video_codec,
            audioCodec=audio_codec,
            separateAudio=not muxed,
            requiresTranscode=requires_transcode,
            estimatedBitrate=round(tbr * 1000) if tbr else None,
            videoFormatId=str(item.get("format_id") or ""),
            audioFormatId=str(paired_audio.get("format_id")) if paired_audio else None,
        )
        candidates.append((option, item, paired_audio))

    # Keep the strongest compatible rendition for each visible height/fps/mux shape.
    deduped: dict[tuple[int | None, int, bool, bool], QualityOption] = {}
    for option, _, _ in candidates:
        key = (option.height, round(option.fps or 0), option.separateAudio, option.requiresTranscode)
        previous = deduped.get(key)
        if not previous or (option.estimatedBitrate or 0) > (previous.estimatedBitrate or 0):
            deduped[key] = option
    return sorted(
        deduped.values(),
        key=lambda option: (
            option.requiresTranscode,
            -(option.height or 0),
            -(option.fps or 0),
            not option.separateAudio,
            -(option.estimatedBitrate or 0),
        ),
    )


def _build_muxed_live_quality_options(formats: list[dict[str, Any]], extractor: str) -> list[QualityOption]:
    candidates: dict[tuple[Any, ...], QualityOption] = {}
    for item in formats:
        protocol = str(item.get("protocol") or "").lower()
        is_hls = protocol.startswith("m3u8")
        is_flv = extractor == "bililive" and (protocol in {"http", "https"} or str(item.get("ext") or "").lower() == "flv")
        if not is_hls and not is_flv:
            continue
        video_codec = str(item.get("vcodec") or "none")
        if video_codec.lower() == "none":
            continue
        raw_audio_codec = item.get("acodec")
        audio_known_absent = str(raw_audio_codec or "").lower() == "none"
        audio_codec = str(raw_audio_codec or "unknown")
        compatible = compatible_video(video_codec) and not audio_known_absent and (compatible_audio(audio_codec) or audio_codec.lower() in {"", "unknown"})
        if extractor == "twitchstream" and not is_hls:
            compatible = False
        height = int(item["height"]) if item.get("height") else None
        width = int(item["width"]) if item.get("width") else None
        fps = safe_float(item.get("fps"))
        tbr = safe_float(item.get("tbr"))
        note = str(item.get("format_note") or item.get("quality") or item.get("format_id") or "未知清晰度")
        resolution = f"{height}p" if height else "未知分辨率"
        transport = "HLS" if is_hls else "FLV"
        source = " · Source" if extractor == "twitchstream" and note.lower() in {"source", "chunked"} else ""
        option = QualityOption(
            qualityId=f"{extractor}-{item.get('format_id')}",
            label=f"{note} · {resolution} · {transport}{source} · {'AVC/AAC copy' if compatible else 'incompatible'}",
            width=width,
            height=height,
            fps=fps,
            videoCodec=video_codec,
            audioCodec=audio_codec,
            separateAudio=False,
            requiresTranscode=not compatible,
            estimatedBitrate=round(tbr * 1000) if tbr else None,
            videoFormatId=str(item.get("format_id") or ""),
            audioFormatId=None,
        )
        if extractor == "bililive":
            key = (item.get("quality") or note, codec_family(video_codec), transport)
        else:
            key = (item.get("format_id") or note, codec_family(video_codec), transport)
        previous = candidates.get(key)
        if previous is None or (option.estimatedBitrate or 0) > (previous.estimatedBitrate or 0):
            candidates[key] = option
    return sorted(candidates.values(), key=lambda option: option.qualityId)


def select_quality(options: list[QualityOption], quality_id: str, max_height: int = 1080) -> QualityOption:
    compatible = [option for option in options if not option.requiresTranscode and (option.height or 0) <= max_height]
    if quality_id == "auto":
        if not compatible:
            raise RuntimeError("No browser-compatible H.264/AAC stream-copy quality is available")
        return max(
            compatible,
            key=lambda option: (
                " · Source" in option.label,
                " · HLS" in option.label,
                option.height or 0,
                option.fps or 0,
                option.estimatedBitrate or 0,
            ),
        )
    for option in options:
        if option.qualityId == quality_id:
            if option.requiresTranscode:
                raise RuntimeError("The selected quality requires transcoding; Prototype 2 refuses silent quality loss")
            return option
    raise ValueError(f"Unknown qualityId: {quality_id}")


def selected_inputs(info: dict[str, Any], quality: QualityOption) -> SelectedInputs:
    formats = {str(item.get("format_id")): item for item in info.get("formats") or []}
    video = formats.get(quality.videoFormatId)
    audio = formats.get(quality.audioFormatId) if quality.audioFormatId else None
    if not video or not video.get("url") or (quality.separateAudio and (not audio or not audio.get("url"))):
        raise RuntimeError("Selected yt-dlp media URLs are missing or expired")
    common_headers = info.get("http_headers") or {}
    return SelectedInputs(
        quality=quality,
        video_url=validate_media_url(str(video["url"])),
        audio_url=validate_media_url(str(audio["url"])) if audio else None,
        video_headers={str(k): str(v) for k, v in (video.get("http_headers") or common_headers).items()},
        audio_headers={str(k): str(v) for k, v in ((audio or {}).get("http_headers") or common_headers).items()},
    )


def ffmpeg_headers(headers: dict[str, str]) -> str:
    # Never place cookies on FFmpeg's command line. yt-dlp normally returns
    # short-lived signed media URLs plus non-sensitive request headers.
    allowed = {"User-Agent", "Referer", "Origin"}
    clean = []
    for key, value in headers.items():
        if key.title() in allowed and "\r" not in value and "\n" not in value:
            clean.append(f"{key}: {value}")
    return "\r\n".join(clean) + ("\r\n" if clean else "")


# The yt-dlp MPEG-TS pipe routinely contains timestamp discontinuities: its
# internal ffmpeg HLS reader skips expired live segments (audio holes of ~5s),
# re-extraction rewinds the PTS, and long-running streams re-base PTS by hours.
# With -c copy those jumps land in fMP4 tfdt and shatter the MSE timeline
# (hls.js stalls, then force-seeks). setts rebuilds a continuous timeline per
# track: sane per-packet deltas are preserved, any discontinuity collapses to
# one frame duration. Stream copy only; no transcoding. Commas inside the
# expressions must stay escaped (\,) for FFmpeg's filter-chain parser.
_VIDEO_SETTS = (
    "setts="
    "dts=if(eq(N\,0)\,DTS\,PREV_OUTDTS+if(between(DTS-PREV_INDTS\,1\,3*PREV_OUTDURATION)\,DTS-PREV_INDTS\,PREV_OUTDURATION)):"
    "pts=if(eq(N\,0)\,PTS\,PREV_OUTDTS+if(between(DTS-PREV_INDTS\,1\,3*PREV_OUTDURATION)\,DTS-PREV_INDTS\,PREV_OUTDURATION)+PTS-DTS):"
    # Live-TS discontinuities can yield negative packet durations; the fMP4
    # muxer treats one as fatal and kills the whole session (2026-09-09:
    # "Packet duration: -1 ... out of range" at media 57s). Re-stamp them.
    "duration=if(lt(DURATION\,0)\,PREV_OUTDURATION\,DURATION)"
)
_AUDIO_SETTS = (
    "setts="
    "ts=if(eq(N\,0)\,PTS\,PREV_OUTPTS+if(between(PTS-PREV_INPTS\,1\,3*PREV_OUTDURATION)\,PTS-PREV_INPTS\,PREV_OUTDURATION)):"
    "duration=if(lt(DURATION\,0)\,PREV_OUTDURATION\,DURATION)"
    ",aac_adtstoasc"
)


# Private-window list size: must hold the public window (180s) plus the largest
# publish delay (~48s) plus burst headroom.
PRIVATE_HLS_LIST_SIZE = 250
PRIVATE_HLS_DELETE_THRESHOLD = 60


def hls_output_args(private_dir: Path, list_size: int = PRIVATE_HLS_LIST_SIZE) -> list[str]:
    """The single source of truth for how LingerLens packages its private HLS.

    Every packager -- production, the synthetic smoke test, and the subtitle
    alignment smoke test -- must call this. They previously hand-rolled their
    own argument lists, which is exactly how the smoke test came to package a
    friendlier stream than production (``independent_segments`` and no
    ``split_by_time``) and therefore could never observe the mid-GOP segment
    defect that made live playback stutter.

    The caller MUST run FFmpeg with ``cwd=private_dir``: ``init.mp4`` is a
    relative name, and FFmpeg resolves it against the process working
    directory, not against the playlist path.
    """
    return [
        "-f",
        "hls",
        "-hls_time",
        # A lower bound only. FFmpeg refuses to cut between keyframes, so the
        # delivered segment length is the source GOP length whenever the GOP is
        # longer than this. `split_by_time` used to force a 1s cut and therefore
        # produced segments that began mid-GOP, which no MSE player can decode
        # until the next keyframe -- measured on a 2s-GOP source as 20 segments
        # instead of 10, i.e. roughly half of them undecodable. Segment length
        # now follows the GOP, and the player's delay is expressed in seconds
        # rather than segment counts, so a longer segment cannot silently
        # multiply playback latency.
        "1",
        "-hls_list_size",
        str(list_size),
        "-hls_delete_threshold",
        str(PRIVATE_HLS_DELETE_THRESHOLD),
        "-hls_segment_type",
        "fmp4",
        "-hls_fmp4_init_filename",
        # Keep the name relative so EXT-X-MAP stays relative and the same private
        # playlist can be read through the Companion's loopback HTTP endpoint on
        # Windows.
        "init.mp4",
        "-hls_flags",
        # independent_segments is advertised because the publisher now only ever
        # cuts on keyframes; the previous flags cut mid-GOP while omitting the
        # tag, so the playlist described a stream that did not exist.
        "delete_segments+program_date_time+temp_file+independent_segments",
        "-hls_segment_filename",
        str(private_dir / "seg_%09d.m4s"),
        str(private_dir / "live.m3u8"),
    ]


def build_ffmpeg_command(
    inputs: SelectedInputs,
    private_dir: Path,
    ffmpeg: str | None = None,
    input_urls: list[str] | None = None,
    pipe_input_count: int = 0,
    pipe_format: str | None = None,
) -> list[str]:
    ffmpeg = ffmpeg or executable("ffmpeg")
    command = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin"]
    urls = input_urls or [inputs.video_url, *([inputs.audio_url] if inputs.audio_url else [])]
    if pipe_input_count:
        urls = [f"pipe:{index}" for index in range(pipe_input_count)]
    headers_by_input = [inputs.video_headers, *([inputs.audio_headers] if inputs.audio_url else [])]
    for url, headers in zip(urls, headers_by_input):
        if not url:
            continue
        local_tcp = url.startswith("tcp://")
        header_block = ffmpeg_headers(headers)
        if header_block and not pipe_input_count and not local_tcp:
            command.extend(["-headers", header_block])
        if local_tcp:
            # Local ingest pump: raw MPEG-TS over localhost TCP. HTTP headers
            # and reconnect options do not apply to this input.
            #
            # FFmpeg's default probe (5s of media / 5MB) is pure dead time at
            # startup here: the ingest is always H.264 + AAC in MPEG-TS, which
            # 2s of media identifies with room to spare. Anything larger just
            # delays the first published segment.
            command.extend(["-f", "mpegts", "-analyzeduration", "2000000", "-probesize", "4000000"])
        elif not pipe_input_count:
            command.extend(["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"])
        elif pipe_format:
            command.extend(["-f", pipe_format])
        command.extend(["-i", url])
    command.extend(["-map", "0:v:0"])
    command.extend(["-map", "0:a:0" if pipe_input_count else ("1:a:0" if inputs.audio_url else "0:a:0")])
    command.extend(
        [
            "-c",
            "copy",
            "-bsf:v",
            _VIDEO_SETTS,
            "-bsf:a",
            _AUDIO_SETTS,
            "-max_interleave_delta",
            "0",
        ]
    )
    command.extend(hls_output_args(private_dir))
    return command


@dataclass
class Segment:
    name: str
    duration: float
    program_date_time: str | None
    discovered_at: float


class DelayedPlaylistPublisher:
    # The public playlist must stay long enough that a player deliberately
    # sitting well behind the live edge (see liveSyncDuration in
    # player.js) can also stall for a while without the segment it is about to
    # request being trimmed out from under it -- that turns a brief rebuffer
    # into a hard 404 stall.
    def __init__(
        self,
        private_dir: Path,
        public_dir: Path,
        publish_delay: float,
        window_seconds: float = 180,
        startup_buffer_seconds: float = 12,
        capture_clock: CaptureClock | None = None,
    ):
        self.private_dir = private_dir
        self.public_dir = public_dir
        self.capture_clock = capture_clock or CaptureClock()
        # The release criterion in _tick re-reads this internal allocation on
        # every iteration, so the target-total-delay API can retune it live.
        self.publish_delay = publish_delay
        self.window_seconds = window_seconds
        # Do not expose a playlist until it contains enough released media for
        # hls.js to start at its configured distance behind the public edge.
        # Without this gate a 15s target starts around 3–5s and never corrects
        # itself, because a count-based live edge cannot seek before sequence 0.
        self.startup_buffer_seconds = startup_buffer_seconds
        self.pending: dict[str, Segment] = {}
        self.published: deque[Segment] = deque()
        self.seen_names: set[str] = set()
        # Sum of durations of every segment ever seen on the private playlist,
        # published or not. Live-message capture uses this media position.
        self._media_seconds_total = 0.0
        # Wall time of the newest segment seen on the private playlist. When
        # the source stalls this stops advancing: the simplest possible
        # stall detector, exposed as sourceStallSeconds in snapshot().
        self._last_new_segment_at = time.monotonic()
        self.target_duration = 1
        self.media_sequence = 0
        self.pdt_epoch: float | None = None
        self._private_edge_wall_time: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def private_media_seconds(self) -> float:
        """Media seconds seen on the private playlist (both legs' reference)."""
        with self._lock:
            return self._media_seconds_total

    def start(self) -> None:
        self.public_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, name="hls-delay-publisher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "publishDelaySeconds": self.publish_delay,
                "publishedSegments": len(self.published),
                "pendingSegments": len(self.pending),
                "hiddenMediaSeconds": round(sum(segment.duration for segment in self.pending.values()), 3),
                "privateMediaSeconds": round(self._media_seconds_total, 3),
                "privateEdgeWallTime": self._private_edge_wall_time,
                "sourceStallSeconds": round(max(0.0, time.monotonic() - self._last_new_segment_at), 1),
                "targetDuration": self.target_duration,
                "playlistReady": (self.public_dir / "live.m3u8").exists(),
                "pdtEpoch": self.pdt_epoch,
            }

    def _run(self) -> None:
        while not self._stop.wait(0.2):
            try:
                self._tick()
            except (OSError, ValueError):
                continue

    def _tick(self) -> None:
        playlist = self.private_dir / "live.m3u8"
        if not playlist.exists():
            return
        init_source = self.private_dir / "init.mp4"
        init_public = self.public_dir / "init.mp4"
        if init_source.exists() and not init_public.exists():
            self._atomic_copy(init_source, init_public)
        parsed = self._parse_playlist(playlist.read_text(encoding="utf-8", errors="replace"))
        if not parsed:
            return
        now = time.monotonic()
        with self._lock:
            # Follow the playlist's actual timestamps, including gaps. A sum
            # of all observed durations cannot represent a skipped segment.
            edge: float | None = None
            for item in parsed:
                if item.program_date_time:
                    edge = self._parse_program_date_time(item.program_date_time)
                if edge is not None:
                    edge += item.duration
            self._private_edge_wall_time = edge
            parsed_names = {item.name for item in parsed}
            for item in parsed:
                if self.pdt_epoch is None and item.program_date_time:
                    self.pdt_epoch = self._parse_program_date_time(item.program_date_time)
                if item.name not in self.seen_names:
                    self.seen_names.add(item.name)
                    self.pending[item.name] = Segment(item.name, item.duration, item.program_date_time, now)
                    self._media_seconds_total += item.duration
                    self._last_new_segment_at = now
            # FFmpeg keeps a finite private playlist/window. Once a pending name
            # has fallen out of that playlist and its file is gone, it can never
            # be published; retaining it would make every 200ms tick slower.
            for name in list(self.pending):
                if name not in parsed_names and not (self.private_dir / name).exists():
                    self.pending.pop(name, None)
            retained_names = parsed_names | set(self.pending) | {segment.name for segment in self.published}
            self.seen_names.intersection_update(retained_names)
            self.target_duration = max(1, math.ceil(max(item.duration for item in parsed)))
            self.capture_clock.update(
                pdt_epoch=self.pdt_epoch,
                completed_private_media_seconds=self._media_seconds_total,
                target_duration=self.target_duration,
                monotonic_time=now,
            )
        # Keep roughly publish_delay seconds of completed media private. This
        # uses media duration rather than wall-clock file age, so startup and
        # bursty playlist refreshes preserve the intended delay budget.
        ordered_pending = sorted(self.pending.values(), key=lambda segment: segment.name)
        hidden_duration = sum(segment.duration for segment in ordered_pending)
        releasable: list[Segment] = []
        for segment in ordered_pending:
            if hidden_duration - segment.duration < self.publish_delay:
                break
            releasable.append(segment)
            hidden_duration -= segment.duration
        changed = False
        for segment in releasable:
            source = self.private_dir / segment.name
            if not source.exists():
                # Keep it only while FFmpeg still advertises it; otherwise the
                # cleanup above removes the permanently unavailable segment.
                continue
            self._atomic_copy(source, self.public_dir / segment.name)
            self.published.append(segment)
            self.pending.pop(segment.name, None)
            changed = True
        if changed:
            self._trim_window()
            published_duration = sum(segment.duration for segment in self.published)
            playlist_exists = (self.public_dir / "live.m3u8").exists()
            if playlist_exists or published_duration >= self.startup_buffer_seconds:
                self._write_public_playlist()

    def _trim_window(self) -> None:
        duration = sum(segment.duration for segment in self.published)
        while len(self.published) > 3 and duration - self.published[0].duration >= self.window_seconds:
            old = self.published.popleft()
            duration -= old.duration
            self.media_sequence += 1
        # Prototype retention is intentionally a little wider than the public
        # playlist. Keeping stale files avoids playlist/segment races while
        # remaining bounded on disk.
        retained_names = {segment.name for segment in self.published}
        files = sorted(self.public_dir.glob("seg_*.m4s"), key=lambda path: path.name)
        stale = [path for path in files if path.name not in retained_names]
        for path in stale[:-6]:
            path.unlink(missing_ok=True)

    def _write_public_playlist(self) -> None:
        if not self.published:
            return
        self.target_duration = max(1, math.ceil(max(segment.duration for segment in self.published)))
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:7",
            f"#EXT-X-TARGETDURATION:{self.target_duration}",
            f"#EXT-X-MEDIA-SEQUENCE:{self.media_sequence}",
            '#EXT-X-MAP:URI="init.mp4"',
        ]
        for segment in self.published:
            if segment.program_date_time:
                lines.append(f"#EXT-X-PROGRAM-DATE-TIME:{segment.program_date_time}")
            lines.extend([f"#EXTINF:{segment.duration:.6f},", segment.name])
        target = self.public_dir / "live.m3u8"
        temporary = target.with_suffix(".m3u8.tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temporary, target)

    @staticmethod
    def _parse_program_date_time(value: str) -> float | None:
        # FFmpeg writes ISO-8601 with a colon-less UTC offset (e.g.
        # "2026-08-30T12:00:00.000+0800"); Python <=3.10 fromisoformat rejects
        # that, which silently nulls pdt_epoch — normalize before parsing.
        normalized = value.strip().replace("Z", "+00:00")
        if len(normalized) >= 5 and normalized[-5] in "+-" and normalized[-3] != ":":
            normalized = normalized[:-2] + ":" + normalized[-2:]
        try:
            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            return None

    @staticmethod
    def _parse_playlist(text: str) -> list[Segment]:
        segments: list[Segment] = []
        duration: float | None = None
        program_date_time: str | None = None
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith("#EXTINF:"):
                duration = float(line.split(":", 1)[1].split(",", 1)[0])
            elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
                program_date_time = line.split(":", 1)[1]
            elif line and not line.startswith("#") and duration is not None:
                name = Path(urllib.parse.urlparse(line).path).name
                if name.endswith(".m4s"):
                    segments.append(Segment(name, duration, program_date_time, 0))
                duration = None
                program_date_time = None
        return segments

    @staticmethod
    def _atomic_copy(source: Path, destination: Path) -> None:
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)


class LiveSession:
    def __init__(self, runtime_dir: Path):
        # FFmpeg runs with ``cwd`` set to the private output directory so its
        # relative init filename is resolved alongside the HLS segments. Keep
        # the entire output tree absolute; a relative ``--runtime-dir`` would
        # otherwise be resolved a second time under that cwd and make FFmpeg
        # fail with "No such file or directory" before the first segment.
        self.runtime_dir = Path(runtime_dir).expanduser().resolve()
        self.private_dir = self.runtime_dir / "private"
        self.public_dir = self.runtime_dir / "public"
        self.process: subprocess.Popen[bytes] | None = None
        self.publisher: DelayedPlaylistPublisher | None = None
        self.quality: QualityOption | None = None
        self.page_url: str | None = None
        self.started_at: float | None = None
        # Identity of the current MEDIA session, or None when there is none.
        # The page needs it to tell "the session I stopped" from "a session that
        # started afterwards": playlistUrl is the same string for every session,
        # uptimeSeconds is a duration rather than an identity, pdtEpoch can be
        # absent while the session is still being prepared, and logbook's
        # sessionId belongs to the backend process and survives every restart of
        # the media session inside it.
        self.media_session_id: str | None = None
        self.log_tail: deque[str] = deque(maxlen=30)
        self._log_thread: threading.Thread | None = None
        self.error: str | None = None
        # Distinguish an expected child-process exit during stop/cleanup from
        # an FFmpeg failure while the live session is still active.
        self._stop_requested = False
        self.ingests: list[Any] = []
        self.source_process: subprocess.Popen[bytes] | None = None
        self.capture_clock: CaptureClock | None = None

    def start(
        self,
        page_url: str,
        inputs: SelectedInputs,
        publish_delay: float,
        command_override: list[str] | None = None,
        ingests: list[Any] | None = None,
        source_process: subprocess.Popen[bytes] | None = None,
        capture_clock: CaptureClock | None = None,
    ) -> None:
        previous_source = self.source_process
        if source_process is previous_source:
            self.source_process = None
        self.stop()
        shutil.rmtree(self.runtime_dir, ignore_errors=True)
        self.private_dir.mkdir(parents=True, exist_ok=True)
        self.public_dir.mkdir(parents=True, exist_ok=True)
        self._stop_requested = False
        command = command_override or build_ffmpeg_command(inputs, self.private_dir)
        stdin_target: Any = subprocess.PIPE if ingests and len(ingests) == 1 else (source_process.stdout if source_process else None)
        try:
            process = subprocess.Popen(
                command,
                # FFmpeg writes a relative hls_fmp4_init_filename against cwd,
                # while all input URLs and segment/playlist paths remain absolute.
                cwd=self.private_dir,
                stdin=stdin_target,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=False,
            )
        except OSError as error:
            # A missing/unlaunchable FFmpeg is the one media failure with no
            # other trace: nothing started, so no log thread and no exit code.
            logbook.record("error", "media", f"FFmpeg could not be started: {type(error).__name__}: {error}")
            raise
        if source_process and source_process.stdout:
            source_process.stdout.close()
        self.process = process
        self.quality = inputs.quality
        self.page_url = page_url
        self.started_at = time.monotonic()
        self.error = None
        logbook.record(
            "info",
            "media",
            f"FFmpeg packaging started for quality {inputs.quality.qualityId} ({inputs.quality.label})",
        )
        self.source_process = source_process or previous_source
        self.capture_clock = capture_clock or self.capture_clock or CaptureClock()
        self.publisher = DelayedPlaylistPublisher(
            self.private_dir,
            self.public_dir,
            publish_delay,
            capture_clock=self.capture_clock,
        )
        self.publisher.start()
        self.ingests = ingests or []
        if self.ingests:
            if len(self.ingests) != 1 or process.stdin is None:
                raise RuntimeError("Prototype Streamlink ingest currently supports one muxed HLS input")
            self.ingests[0].start(process.stdin)
        self._log_thread = threading.Thread(target=self._read_log, name="ffmpeg-log", daemon=True)
        self._log_thread.start()
        # Generated LAST, so a start that failed at any earlier step cannot leave
        # a live-looking identity behind, and late enough that the /api/start
        # response carries it (start() is synchronous, so nothing observes a
        # status in between). 128 bits of randomness rather than a counter or a
        # timestamp: the page compares it against what it saw before a Stop, and
        # it must not repeat across a backend restart or a re-open of the same
        # source. It is not a credential -- it grants nothing and is reported
        # only to a localhost client.
        self.media_session_id = secrets.token_hex(16)

    def _fail(self, message: str) -> None:
        """Enter the error state once, and make it visible in the UI log.

        ``status()`` recomposes this same message on every poll, so the record
        is emitted at the state change (not per snapshot) -- otherwise a stalled
        UI would be told the same failure forever. The FFmpeg tail that
        ``/api/status`` exposes as ``ffmpegLogTail`` is copied into the log too,
        because that payload is only read while a client is polling.
        """
        if self.error == message:
            return
        self.error = message
        logbook.record("error", "media", message)
        for line in list(self.log_tail)[-6:]:
            logbook.record("error", "media", line)

    def _read_log(self) -> None:
        if not self.process or not self.process.stderr:
            return
        for raw_line in self.process.stderr:
            clean = raw_line.decode("utf-8", "replace").strip()
            if clean:
                self.log_tail.append(clean)
        code = self.process.poll()
        if code is not None and not self._stop_requested:
            detail = self.log_tail[-1] if self.log_tail else "no FFmpeg diagnostic"
            if code == 0:
                self._fail(f"FFmpeg stopped unexpectedly before the live session ended: {detail}")
            else:
                self._fail(f"FFmpeg exited with code {code}: {detail}")

    def request_stop(self) -> None:
        """Mark the packaging child as intentionally stopping before inputs close.

        The server owns the yt-dlp input legs separately from this session.  It
        must be able to claim the stop before closing those legs; otherwise
        their EOF can make FFmpeg exit cleanly and look like a live failure to
        a concurrent status poll.
        """
        self._stop_requested = True

    def stop(self) -> None:
        self.request_stop()
        for ingest in self.ingests:
            ingest.stop()
        self.ingests = []
        if self.publisher:
            self.publisher.stop()
            self.publisher = None
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        if self.process and self.process.stderr:
            self.process.stderr.close()
        if self._log_thread and self._log_thread is not threading.current_thread():
            self._log_thread.join(timeout=1)
        self.process = None
        self.source_process = None
        self.capture_clock = None
        self.quality = None
        self.page_url = None
        self.started_at = None
        self.media_session_id = None
        self.error = None
        self.log_tail.clear()
        self._log_thread = None
        shutil.rmtree(self.runtime_dir, ignore_errors=True)

    def status(self) -> dict[str, Any]:
        exit_code = self.process.poll() if self.process else None
        running = bool(self.process and exit_code is None)
        if self.process and exit_code is not None and not self.error and not self._stop_requested:
            detail = self.log_tail[-1] if self.log_tail else "no FFmpeg diagnostic"
            self._fail(
                f"FFmpeg stopped unexpectedly before the live session ended: {detail}"
                if exit_code == 0
                else f"FFmpeg exited with code {exit_code}: {detail}"
            )
        publisher = self.publisher.snapshot() if self.publisher else {}
        return {
            "state": "error" if self.error else ("running" if running else "idle"),
            "error": self.error,
            "pageUrl": self.page_url,
            "quality": asdict(self.quality) if self.quality else None,
            "uptimeSeconds": round(time.monotonic() - self.started_at, 1) if self.started_at and running else 0,
            "playlistUrl": "/hls/live.m3u8" if publisher.get("playlistReady") else None,
            "ffmpegLogTail": list(self.log_tail)[-6:] if self.error else [],
            "mediaSessionId": self.media_session_id,
            **publisher,
        }
