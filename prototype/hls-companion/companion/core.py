#!/usr/bin/env python3
"""Core of LagLingo Prototype 2: probe, select, remux, and delay-publish live HLS."""

from __future__ import annotations

import json
import math
import os
import re
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
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_HOST_SUFFIXES = (
    "youtube.com",
    "youtu.be",
    "googlevideo.com",
    "bilibili.com",
    "bilivideo.com",
)
SUPPORTED_COOKIE_SUFFIXES = ("youtube.com", "google.com", "bilibili.com")
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
        raise ValueError("Only HTTPS YouTube/Bilibili page URLs are accepted")
    hostname = parsed.hostname.lower().rstrip(".")
    if not any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in SUPPORTED_HOST_SUFFIXES):
        raise ValueError("Only YouTube and Bilibili URLs are accepted by this prototype")
    return url


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
        rows = [self._netscape_row(cookie) for cookie in cookies]
        rows = [row for row in rows if row]
        if not rows:
            return
        fd, raw_path = tempfile.mkstemp(prefix="laglingo-cookies-", suffix=".txt")
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

    def __init__(self, info: dict[str, Any]):
        self._path: Path | None = None
        fd, raw_path = tempfile.mkstemp(prefix="laglingo-info-", suffix=".json")
        self._path = Path(raw_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
                json.dump(info, output)
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

    This matches the TSV that DevTools' cookie table copies to the clipboard
    ("Name<TAB>Value" per selected row, optional localized header row).
    """
    cookies: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            # DevTools copies full cookie-table rows (name, value, domain, path,
            # expiry, size, flags, ...). Keep only the first two columns.
            fields = line.split("\t")
            if len(fields) < 2:
                continue
            name, value = fields[0], fields[1]
        elif "=" in line:
            name, _, value = line.partition("=")
        else:
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            name, value = parts[0], parts[1]
        name = name.strip()
        value = value.strip()
        if not name or name.lower() in {"name", "名称", "名前"} or "\t" in name or "\n" in value or "\r" in value:
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


class YtDlpProbe:
    def __init__(self, yt_dlp: str | None = None):
        self.yt_dlp = yt_dlp or executable("yt-dlp")

    def extract(self, page_url: str, auth: AuthenticationProvider | None = None) -> dict[str, Any]:
        validate_page_url(page_url)
        current_yt_dlp = ROOT / "vendor" / "yt-dlp" / "yt-dlp.exe"
        yt_dlp = str(current_yt_dlp) if current_yt_dlp.is_file() else self.yt_dlp
        command = [
            yt_dlp,
            "--no-config",
            "--no-playlist",
            "--no-warnings",
            "--skip-download",
            "--no-live-from-start",
            "-J",
            *([] if auth is None else auth.yt_dlp_args()),
            page_url,
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
        if completed.returncode != 0:
            tail = "\n".join(completed.stderr.strip().splitlines()[-8:])
            raise RuntimeError(f"yt-dlp format probe failed (exit {completed.returncode}): {tail or 'no diagnostic'}")
        try:
            info = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("yt-dlp returned invalid JSON") from error
        if not info.get("is_live") and info.get("live_status") not in {"is_live", "is_upcoming"}:
            raise RuntimeError("The URL did not resolve to a current live stream")
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


def select_quality(options: list[QualityOption], quality_id: str, max_height: int = 1080) -> QualityOption:
    compatible = [option for option in options if not option.requiresTranscode and (option.height or 0) <= max_height]
    if quality_id == "auto":
        if not compatible:
            raise RuntimeError("No browser-compatible H.264/AAC stream-copy quality is available")
        return max(compatible, key=lambda option: (option.height or 0, option.fps or 0, option.estimatedBitrate or 0))
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
    "pts=if(eq(N\,0)\,PTS\,PREV_OUTDTS+if(between(DTS-PREV_INDTS\,1\,3*PREV_OUTDURATION)\,DTS-PREV_INDTS\,PREV_OUTDURATION)+PTS-DTS)"
)
_AUDIO_SETTS = (
    "setts="
    "ts=if(eq(N\,0)\,PTS\,PREV_OUTPTS+if(between(PTS-PREV_INPTS\,1\,3*PREV_OUTDURATION)\,PTS-PREV_INPTS\,PREV_OUTDURATION))"
    ",aac_adtstoasc"
)


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
            "-f",
            "hls",
            "-hls_time",
            "1",  # 1 秒分片；split_by_time 使其真正生效（不再受 GOP 约束）
            "-hls_list_size",
            "150",
            "-hls_delete_threshold",
            "60",
            "-hls_segment_type",
            "fmp4",
            "-hls_fmp4_init_filename",
            str(private_dir / "init.mp4"),
            "-hls_flags",
            "delete_segments+program_date_time+temp_file+split_by_time",
            "-hls_segment_filename",
            str(private_dir / "seg_%09d.m4s"),
            str(private_dir / "live.m3u8"),
        ]
    )
    return command


@dataclass
class Segment:
    name: str
    duration: float
    program_date_time: str | None
    discovered_at: float


class DelayedPlaylistPublisher:
    # The public playlist must stay long enough that a player deliberately
    # sitting well behind the live edge (see liveSyncDurationCount in
    # player.js) can also stall for a while without the segment it is about to
    # request being trimmed out from under it -- that turns a brief rebuffer
    # into a hard 404 stall.
    def __init__(self, private_dir: Path, public_dir: Path, publish_delay: float, window_seconds: float = 120):
        self.private_dir = private_dir
        self.public_dir = public_dir
        # NOTE (redesign Fix E): the release criterion in _tick re-reads this
        # attribute every iteration, so mutating it takes effect immediately —
        # POST /api/publish-delay relies on this; do not snapshot it locally.
        self.publish_delay = publish_delay
        self.window_seconds = window_seconds
        self.pending: dict[str, Segment] = {}
        self.published: deque[Segment] = deque()
        self.seen_names: set[str] = set()
        # Sum of durations of every segment ever seen on the private playlist,
        # published or not. This is the packaging leg's media position counter
        # used by the subtitle MediaAnchor (redesign Fix B).
        self._media_seconds_total = 0.0
        self.target_duration = 1
        self.media_sequence = 0
        self.pdt_epoch: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def private_media_seconds(self) -> float:
        """Media seconds seen on the private playlist (both legs' reference)."""
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
        for item in parsed:
            if self.pdt_epoch is None and item.program_date_time:
                self.pdt_epoch = self._parse_program_date_time(item.program_date_time)
            if item.name not in self.seen_names:
                self.seen_names.add(item.name)
                self.pending[item.name] = Segment(item.name, item.duration, item.program_date_time, now)
                self._media_seconds_total += item.duration
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
                continue
            self._atomic_copy(source, self.public_dir / segment.name)
            self.published.append(segment)
            self.pending.pop(segment.name, None)
            changed = True
        if changed:
            self._trim_window()
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
        self.runtime_dir = runtime_dir
        self.private_dir = runtime_dir / "private"
        self.public_dir = runtime_dir / "public"
        self.process: subprocess.Popen[bytes] | None = None
        self.publisher: DelayedPlaylistPublisher | None = None
        self.quality: QualityOption | None = None
        self.page_url: str | None = None
        self.started_at: float | None = None
        self.log_tail: deque[str] = deque(maxlen=30)
        self._log_thread: threading.Thread | None = None
        self.error: str | None = None
        self.ingests: list[Any] = []
        self.source_process: subprocess.Popen[bytes] | None = None

    def start(
        self,
        page_url: str,
        inputs: SelectedInputs,
        publish_delay: float,
        command_override: list[str] | None = None,
        ingests: list[Any] | None = None,
        source_process: subprocess.Popen[bytes] | None = None,
    ) -> None:
        previous_source = self.source_process
        if source_process is previous_source:
            self.source_process = None
        self.stop()
        shutil.rmtree(self.runtime_dir, ignore_errors=True)
        self.private_dir.mkdir(parents=True, exist_ok=True)
        self.public_dir.mkdir(parents=True, exist_ok=True)
        command = command_override or build_ffmpeg_command(inputs, self.private_dir)
        stdin_target: Any = subprocess.PIPE if ingests and len(ingests) == 1 else (source_process.stdout if source_process else None)
        process = subprocess.Popen(
            command,
            stdin=stdin_target,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=False,
        )
        if source_process and source_process.stdout:
            source_process.stdout.close()
        self.process = process
        self.quality = inputs.quality
        self.page_url = page_url
        self.started_at = time.monotonic()
        self.error = None
        self.source_process = source_process or previous_source
        self.publisher = DelayedPlaylistPublisher(self.private_dir, self.public_dir, publish_delay)
        self.publisher.start()
        self.ingests = ingests or []
        if self.ingests:
            if len(self.ingests) != 1 or process.stdin is None:
                raise RuntimeError("Prototype Streamlink ingest currently supports one muxed HLS input")
            self.ingests[0].start(process.stdin)
        self._log_thread = threading.Thread(target=self._read_log, name="ffmpeg-log", daemon=True)
        self._log_thread.start()

    def _read_log(self) -> None:
        if not self.process or not self.process.stderr:
            return
        for raw_line in self.process.stderr:
            clean = raw_line.decode("utf-8", "replace").strip()
            if clean:
                self.log_tail.append(clean)
        code = self.process.poll()
        if code is not None:
            detail = self.log_tail[-1] if self.log_tail else "no FFmpeg diagnostic"
            if code == 0:
                self.error = f"FFmpeg stopped unexpectedly before the live session ended: {detail}"
            else:
                self.error = f"FFmpeg exited with code {code}: {detail}"

    def stop(self) -> None:
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
        self.process = None
        self.source_process = None

    def status(self) -> dict[str, Any]:
        exit_code = self.process.poll() if self.process else None
        running = bool(self.process and exit_code is None)
        if self.process and exit_code is not None and not self.error:
            detail = self.log_tail[-1] if self.log_tail else "no FFmpeg diagnostic"
            self.error = (
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
            **publisher,
        }
