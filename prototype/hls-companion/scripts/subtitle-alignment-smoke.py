#!/usr/bin/env python3
"""Golden subtitle alignment smoke test (redesign Fix K).

Offline (no API key) end-to-end verification of the subtitle timeline:

1. Synthesize a 60s H.264/AAC MPEG-TS whose audio track carries 1 kHz beeps
   at media times 5, 15, 25, 35, 45 s (1 s each).
2. Drive the REAL pipeline in the production startup shape: packaging ffmpeg
   writes private HLS with PDT -> the subtitle ffmpeg reads that same HLS over
   loopback HTTP -> stub ASR detects the beeps.
   The stream is fed at realtime speed, matching the live HLS production rate.
3. Assert that cue.tEnd maps back onto the packaging PDT timeline with
   <= 300 ms error and that no cue ever lives in wall-clock space.

Stub ASR runs in two timing shapes:
  --mode asr  finals also carry begin/end offsets
  --mode vad  boundaries arrive only through speech_started/speech_stopped

Requires ffmpeg on PATH.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from companion.core import _AUDIO_SETTS, _VIDEO_SETTS, DelayedPlaylistPublisher, hls_output_args
from companion.providers.base import (
    ASRCapabilities,
    ASREvent,
    ASRProvider,
    ASRStream,
    StreamMeta,
    TranslationCapabilities,
    TranslationProvider,
    TranslationRequest,
    TranslationResult,
)
from companion.subtitle_pipeline import SubtitlePipeline
from companion.subtitle_store import CueStore
from companion.ytdlp_ingest import _TcpPump

BEEP_STARTS = (5.0, 15.0, 25.0, 35.0, 45.0)
BEEP_DURATION = 1.0
STREAM_SECONDS = 60.0
TOLERANCE_SECONDS = 0.300
SUB_WINDOW = 0.01  # beep-detection granularity in the stub ASR
SILENCE_CONFIRM = 0.300  # stub VAD: how much silence confirms an utterance end


def ffmpeg_executable() -> str:
    found = shutil.which("ffmpeg")
    if not found:
        raise SystemExit("ffmpeg is required on PATH for the alignment smoke test")
    return found


def build_test_stream(destination: Path) -> None:
    """60s AVC/AAC MPEG-TS with 1 kHz beeps at 5/15/25/35/45s (1s each)."""
    # Commas inside function args must be escaped for lavfi's filter parser.
    beep_expr = "0.5*sin(2*PI*1000*t)*lt(mod(t-5\,10)\,1)*gte(t\,5)*lt(t\,46)"
    command = [
        ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"aevalsrc={beep_expr}:s=44100:d={STREAM_SECONDS}",
        "-f", "lavfi", "-i", f"color=c=black:s=320x240:r=25:d={STREAM_SECONDS}",
        "-map", "0:a:0", "-map", "1:v:0",
        "-c:a", "aac", "-b:a", "64k",
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage", "-g", "50",
        "-f", "mpegts", str(destination),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    if completed.returncode != 0:
        raise SystemExit(f"test stream synthesis failed:\n{completed.stderr[-2000:]}")


class PacedSource(io.BytesIO):
    """Finite TS source delivered at the live media production rate."""

    def __init__(self, payload: bytes, wall_seconds: float = STREAM_SECONDS) -> None:
        super().__init__(payload)
        self.total_bytes = len(payload)
        self.wall_seconds = wall_seconds

    def read(self, size: int = -1) -> bytes:
        # MPEG-TS-aligned chunks produce ~30 observations over six seconds.
        chunk = super().read(min(size, 188 * 100))
        if chunk:
            time.sleep(self.wall_seconds * len(chunk) / self.total_bytes)
        return chunk


class BeepDetectingStream(ASRStream):
    """Turns 1 kHz beeps in the pushed PCM into MARK-<n> finals.

    Mode ``asr`` emits finals with exact begin/end offsets.  Mode ``vad``
    emits only speech_started/speech_stopped events and relies on the
    pipeline's VAD boundary bookkeeping (event lag + silence subtraction).
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.events: asyncio.Queue[ASREvent] = asyncio.Queue()
        self.in_speech = False
        self.speech_start = 0.0
        self.last_loud_end = 0.0
        self.count = 0
        self.current_item = ""
        self.quiet_since: float | None = None

    async def push_pcm(self, chunk: bytes, pcm_offset: float) -> None:
        samples = struct.unpack(f"<{len(chunk) // 2}h", chunk[: (len(chunk) // 2) * 2])
        windows = len(samples) // 160  # 10ms windows @16k
        for index in range(windows):
            window = samples[index * 160 : (index + 1) * 160]
            t = pcm_offset + index * SUB_WINDOW
            loud = max(abs(min(window)), abs(max(window))) > 2000
            if loud:
                if not self.in_speech:
                    self.in_speech = True
                    self.speech_start = t
                    self.current_item = f"marker-{self.count + 1}"
                    self.events.put_nowait(ASREvent(
                        "speech_started", begin_pcm=t, item_id=self.current_item
                    ))
                self.last_loud_end = t + SUB_WINDOW
                self.quiet_since = None
            elif self.in_speech:
                if self.quiet_since is None:
                    self.quiet_since = t
                if t + SUB_WINDOW - self.quiet_since >= SILENCE_CONFIRM:
                    self.count += 1
                    self.in_speech = False
                    self.quiet_since = None
                    self.events.put_nowait(ASREvent(
                        "speech_stopped", end_pcm=self.last_loud_end, item_id=self.current_item
                    ))
                    if self.mode == "asr":
                        self.events.put_nowait(
                            ASREvent(
                                "final",
                                text=f"MARK-{self.count}",
                                begin_pcm=self.speech_start,
                                end_pcm=self.last_loud_end,
                                item_id=self.current_item,
                            )
                        )
                    else:
                        self.events.put_nowait(ASREvent(
                            "final", text=f"MARK-{self.count}", item_id=self.current_item
                        ))

    async def flush(self) -> None:
        return None

    async def _events(self):
        while True:
            yield await self.events.get()

    def __aiter__(self):
        return self._events()

    async def aclose(self) -> None:
        return None


class BeepASRProvider(ASRProvider):
    id = "beep-stub"
    label = "Beep Stub ASR"
    model = "stub"
    price_per_second_cny = 0.0

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.stream_instance: BeepDetectingStream | None = None

    @property
    def capabilities(self) -> ASRCapabilities:
        return ASRCapabilities(True, False, False, True, False, False, False, ("ja",), (16000,), False)

    async def stream(self, *, policy, sample_rate: int, hotwords: list[str], context: list[str]) -> ASRStream:
        del policy, sample_rate, hotwords, context
        self.stream_instance = BeepDetectingStream(self.mode)
        return self.stream_instance


class StubTranslation(TranslationProvider):
    id = "stub-mt"
    label = "Stub MT"
    model = "stub"
    delay_seconds: float

    def __init__(self, delay_seconds: float = 0.15) -> None:
        self.delay_seconds = delay_seconds

    @property
    def capabilities(self) -> TranslationCapabilities:
        return TranslationCapabilities(False, False, False, False, 1000)

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        await asyncio.sleep(self.delay_seconds)
        return TranslationResult(f"标记-{request.source_text}", self.id, int(self.delay_seconds * 1000))


async def packaged_beep_ends(playlist: Path) -> list[float]:
    """Measure marker ends on the actual packaged HLS media timeline.

    This is the golden truth: stream-copy/setts/AAC priming can shift the
    packaged timeline by a constant relative to the ideal lavfi source clock.
    Cue PDT mapping must match the output the player consumes, not that ideal
    pre-packaging clock.
    """
    completed = subprocess.run(
        [
            ffmpeg_executable(), "-hide_banner", "-loglevel", "error",
            "-i", str(playlist), "-vn", "-ac", "1", "-ar", "16000",
            "-f", "s16le", "pipe:1",
        ],
        capture_output=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.decode("utf-8", "replace")[-2000:])
    detector = BeepDetectingStream("asr")
    data = completed.stdout
    for byte_offset in range(0, len(data), 3200):
        chunk = data[byte_offset : byte_offset + 3200]
        if len(chunk) < 3200:
            break
        await detector.push_pcm(chunk, byte_offset / 32000.0)
    ends: list[float] = []
    while not detector.events.empty():
        event = detector.events.get_nowait()
        if event.type == "final" and event.end_pcm is not None:
            ends.append(event.end_pcm)
    return ends


def packaging_command(pump: _TcpPump, private_dir: Path) -> list[str]:
    """Same HLS/PDT/setts shape as core.build_ffmpeg_command's TCP path."""
    return [
        ffmpeg_executable(), "-hide_banner", "-loglevel", "warning", "-nostdin",
        "-f", "mpegts", "-i", pump.url,
        "-map", "0:v:0", "-map", "0:a:0",
        "-c", "copy",
        "-bsf:v", _VIDEO_SETTS,
        "-bsf:a", _AUDIO_SETTS,
        "-max_interleave_delta", "0",
        # Same packaging arguments production uses, so this harness cannot pass
        # while the shipped packager emits segments no player can decode.
        *hls_output_args(private_dir, list_size=150),
    ]


async def run_scenario(mode: str, ts_bytes: bytes, workspace: Path) -> dict:
    private_dir = workspace / mode / "private"
    public_dir = workspace / mode / "public"
    private_dir.mkdir(parents=True)
    public_dir.mkdir(parents=True)

    store = CueStore()
    publisher = DelayedPlaylistPublisher(private_dir, public_dir, publish_delay=0.0)
    asr_provider = BeepASRProvider(mode)
    pipeline = SubtitlePipeline(
        asr_provider=asr_provider,
        translation_provider=StubTranslation(),
        cue_store=store,
        meta=StreamMeta("alignment-smoke", "stub", "test", "ja", "zh"),
        silence_duration_ms=300,  # matches the stub's confirmation window
        translation_workers=2,
    )
    pump = _TcpPump("smoke")
    publisher.start()
    pump.start(PacedSource(ts_bytes))
    process = subprocess.Popen(
        packaging_command(pump, private_dir),
        cwd=private_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=False,
    )
    ffmpeg_tail: list[str] = []

    def read_stderr() -> None:
        for raw in process.stderr or []:
            ffmpeg_tail.append(raw.decode("utf-8", "replace").strip())

    reader = threading.Thread(target=read_stderr, daemon=True)
    reader.start()

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(private_dir)))
    http_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    http_thread.start()
    ready_deadline = time.monotonic() + 10
    while time.monotonic() < ready_deadline:
        if publisher.pdt_epoch is not None and (private_dir / "seg_000000000.m4s").is_file():
            break
        await asyncio.sleep(0.05)
    else:
        raise RuntimeError("private HLS did not become ready")
    await pipeline.start(f"http://127.0.0.1:{httpd.server_port}/live.m3u8", publisher.pdt_epoch)

    deadline = time.monotonic() + 120
    last_progress = (-1.0, -1)
    last_progress_at = time.monotonic()
    try:
        while time.monotonic() < deadline:
            status = pipeline.status()
            cues = store.query(after_seq=0)
            done = [cue for cue in cues if cue.state == "done"]
            progress = (status["pcmOffset"], len(done))
            if progress != last_progress:
                last_progress = progress
                last_progress_at = time.monotonic()
            if len(done) >= len(BEEP_STARTS) and status["pcmOffset"] >= STREAM_SECONDS - 1.0:
                break
            if status["lastError"]:
                raise RuntimeError(f"subtitle pipeline error: {status['lastError']}")
            # The pump source is finite: at CPU speed the packaging ffmpeg hits
            # a normal EOF while the subtitle leg is still draining. Only a
            # *stalled* pipeline is an error, not the EOF itself.
            stall_limit = 6 if process.poll() is not None else 25
            if time.monotonic() - last_progress_at > stall_limit:
                stub = asr_provider.stream_instance
                stub_status = {
                    "finals": stub.count if stub else None,
                    "queuedEvents": stub.events.qsize() if stub else None,
                    "inSpeech": stub.in_speech if stub else None,
                }
                raise RuntimeError(
                    f"pipeline stalled (ffmpeg exited={process.poll()}): "
                    f"{json.dumps(pipeline.status(), ensure_ascii=False)}; "
                    f"stub={json.dumps(stub_status)}; "
                    f"ffmpeg tail: {' | '.join(ffmpeg_tail[-5:])}"
                )
            await asyncio.sleep(0.2)
        else:
            raise RuntimeError(f"timeout; pipeline status={json.dumps(pipeline.status(), ensure_ascii=False)}")
    finally:
        pump.stop()
        process.wait(timeout=10)
        publisher.stop()
        await pipeline.stop()
        httpd.shutdown()
        httpd.server_close()
        http_thread.join(timeout=3)

    status = pipeline.status()
    cues = sorted(store.query(after_seq=0), key=lambda cue: cue.t_end)
    failures: list[str] = []
    pdt0 = publisher.pdt_epoch
    true_ends = await packaged_beep_ends(private_dir / "live.m3u8")

    if pdt0 is None:
        raise RuntimeError("publisher never observed a program date time")
    if len(true_ends) != len(BEEP_STARTS):
        failures.append(f"expected {len(BEEP_STARTS)} packaged markers, measured {true_ends}")
    if len(cues) != len(BEEP_STARTS):
        failures.append(f"expected {len(BEEP_STARTS)} cues, got {len(cues)}: {[c.src for c in cues]}")
    elif len(true_ends) == len(BEEP_STARTS):
        for cue, beep_start, beep_end in zip(cues, BEEP_STARTS, true_ends):
            mapped = cue.t_end - pdt0  # == end_pcm + C on the media timeline
            error = mapped - beep_end
            if abs(error) > TOLERANCE_SECONDS + 1e-6:
                failures.append(f"{cue.src}: mapped media end {mapped:.3f}s vs true {beep_end:.3f}s (error {error * 1000:+.0f}ms)")
            if cue.t_start is not None:
                start_error = (cue.t_start - pdt0) - beep_start
                if abs(start_error) > 1.0:
                    failures.append(f"{cue.src}: mapped start off by {start_error * 1000:+.0f}ms")
            if not cue.zh:
                failures.append(f"{cue.src}: translation never completed")

    for cue in cues:
        media_position = cue.t_end - pdt0
        if not -1.0 <= media_position <= STREAM_SECONDS + 5.0:
            failures.append(f"{cue.src}: tEnd maps outside the media timeline ({media_position:.1f}s) — wall-clock space leak")
    if status.get("pcmDropped"):
        failures.append(f"pcmDropped={status['pcmDropped']} (PCM queue overflowed)")
    if status.get("timingSourceCounts", {}).get("asr") != len(BEEP_STARTS):
        failures.append(f"timingSourceCounts={status.get('timingSourceCounts')} for mode {mode}")
    if status.get("readyLagP95") is None:
        failures.append("readyLag percentiles were never recorded (Fix E wiring)")

    report = {
        "mode": mode,
        "pdtEpoch": pdt0,
        "cues": [
            {
                "src": cue.src,
                "state": cue.state,
                "mediaEnd": round(cue.t_end - pdt0, 3),
                "trueEnd": round(beep_end, 3),
                "errorMs": round(((cue.t_end - pdt0) - beep_end) * 1000),
                "timingSource": cue.timing_source,
            }
            for cue, beep_end in zip(cues, true_ends)
        ],
        "timelineSource": status.get("timelineSource"),
        "timingSourceCounts": status.get("timingSourceCounts"),
        "readyLag": {"p50": status.get("readyLagP50"), "p95": status.get("readyLagP95")},
        "failures": failures,
    }
    return report


async def amain(args: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory(prefix="lingerlens-alignment-") as raw:
        workspace = Path(raw)
        ts_path = workspace / "source.ts"
        print(f"[smoke] synthesizing {STREAM_SECONDS:.0f}s test stream with beeps at {BEEP_STARTS} ...", flush=True)
        build_test_stream(ts_path)
        ts_bytes = ts_path.read_bytes()
        print(f"[smoke] stream ready ({len(ts_bytes) / 1e6:.1f} MB); feeding both legs at realtime speed", flush=True)
        modes = ["asr", "vad"] if args.mode == "both" else [args.mode]
        reports = []
        failed = False
        for mode in modes:
            report = await run_scenario(mode, ts_bytes, workspace)
            reports.append(report)
            for cue in report["cues"]:
                print(f"[smoke] {mode:>4} {cue['src']:>8}: true {cue['trueEnd']:.2f}s -> mapped {cue['mediaEnd']:.2f}s  (error {cue['errorMs']:+d}ms, {cue['timingSource']})", flush=True)
            print(f"[smoke] timeline: {report['timelineSource']}", flush=True)
            if report["failures"]:
                failed = True
                for failure in report["failures"]:
                    print(f"[FAIL] {mode}: {failure}", file=sys.stderr)
            else:
                print(f"[PASS] mode={mode}: all cues within ±{TOLERANCE_SECONDS * 1000:.0f}ms", flush=True)
        print(json.dumps(reports, ensure_ascii=False, indent=2))
        return 1 if failed else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["asr", "vad", "both"], default="both")
    return parser.parse_args()


def main() -> int:
    return asyncio.run(amain(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
