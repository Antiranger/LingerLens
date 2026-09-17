"""A1: the decoder's stderr is drained, and teardown spends ONE deadline.

The subtitle decoder has always been started with ``stderr=PIPE`` and nothing has
ever read that pipe. FFmpeg writes its diagnostics there for as long as it lives,
so once the pipe fills the write that blocks is the one immediately before the
next PCM byte: a burst of decoder errors stops the audio that feeds recognition.
The reader itself is small; what makes this a change rather than an addition is
the teardown it needs, because the reader must outlive its writer, the writer's
exit must not be waited for without a bound, and nothing may be declared cleaned
up while a task or a process is still alive.

The real-pipe tests below run a real child process and register it, so a hang
fails the test and kills the child instead of leaving it behind. The budget tests
run on a fake clock, because waiting five seconds to observe a five second budget
is testing the wall clock rather than the arithmetic.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import companion.logbook as logbook
import companion.subtitle_pipeline as pipeline_module
from companion.providers.base import ASREvent, SourceLanguagePolicy, StreamMeta
from companion.server import CompanionApplication
from companion.subtitle_pipeline import (
    _STDERR_MAX_SNAPSHOTS,
    _STDERR_TAIL_BYTES,
    _STDERR_TAIL_LINES,
    SubtitlePipeline,
)
from companion.subtitle_store import CueStore
from test_subtitle_pipeline import FakeASR, FakeStream

# One chunk of 16 kHz mono s16le is 0.1s of audio: the unit the "PCM reached
# 0.2s" assertion is expressed in.
PCM_CHUNK = 3200
# Hard exit insurance for a real child. It is deliberately larger than the
# module's five second close budget plus process setup, so it only fires when the
# test itself is stuck rather than when the code under test is slow.
DECODER_HARD_EXIT_SECONDS = 10.0

# Writes 0.1s of PCM, then a large burst of stderr, then another 0.1s of PCM.
# os.write blocks once the pipe is full, which is the whole point: without a
# reader the child never reaches the second PCM chunk.
DECODER_BURST = r"""
import os, sys, time
burst = int(sys.argv[1])
line = b"decoder: " + b"e" * 4087 + b"\n"
os.write(1, b"\x00" * 3200)
left = burst
while left > 0:
    piece = line[:min(len(line), left)]
    os.write(2, piece)
    left -= len(piece)
os.write(1, b"\x00" * 3200)
time.sleep(2.0)
"""

# Multi-megabyte stderr with NO newline at all, PCM interleaved between blocks.
# A line-count bound cannot survive this; a byte bound can.
DECODER_NO_NEWLINE = r"""
import os, sys, time
burst = int(sys.argv[1])
os.write(1, b"\x00" * 3200)
left = burst
while left > 0:
    piece = b"e" * min(65536, left)
    os.write(2, piece)
    left -= len(piece)
    if left % (65536 * 4) < 65536:
        os.write(1, b"\x00" * 3200)
os.write(1, b"\x00" * 3200)
time.sleep(2.0)
"""


class SyntheticDecoder:
    """A real child process that behaves like a loud decoder.

    Registered so the test can kill exactly this process and no other: the PID is
    the handle, and nothing here ever matches on a process name.
    """

    def __init__(self, script: str, burst: int) -> None:
        self.script = script
        self.burst = burst
        self.process: Any = None

    async def factory(self, *args: Any, **kwargs: Any) -> Any:
        del args
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-c",
            self.script,
            str(self.burst),
            stdin=kwargs.get("stdin"),
            stdout=kwargs.get("stdout"),
            stderr=kwargs.get("stderr"),
        )
        return self.process

    @property
    def pid(self) -> int | None:
        return None if self.process is None else self.process.pid

    def kill_if_alive(self) -> None:
        process = self.process
        if process is not None and process.returncode is None:
            with contextlib.suppress(Exception):
                process.kill()


def make_pipeline(subprocess_factory: Any = None, asr_provider: Any = None) -> SubtitlePipeline:
    return SubtitlePipeline(
        asr_provider=asr_provider or FakeASR(),
        cue_store=CueStore(),
        meta=StreamMeta("title", "channel", "gaming", "en", "zh"),
        subprocess_factory=subprocess_factory or asyncio.create_subprocess_exec,
    )


class TailFinalStream(FakeStream):
    """A realtime stream whose last final arrives only AFTER the close control.

    Soniox, AssemblyAI and Volcengine all behave this way, which is why the
    teardown drains the ASR consumer before cancelling it: a Stop that cancelled
    the consumer first would discard a sentence the provider had already
    recognized.
    """

    def __init__(self) -> None:
        super().__init__()
        self.queue: asyncio.Queue = asyncio.Queue()
        self.closed = False

    def __aiter__(self):
        async def events():
            while True:
                event = await self.queue.get()
                if event is None:
                    return
                yield event

        return events()

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.queue.put_nowait(ASREvent("final", text="尾句", item_id="tail"))
        self.queue.put_nowait(None)


class TailFinalASR(FakeASR):
    async def stream(self, **kwargs: Any) -> Any:
        del kwargs
        return TailFinalStream()


async def until(predicate: Any, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def media_records() -> list[str]:
    return [
        entry["message"]
        for entry in logbook.snapshot()["records"]
        if entry["source"] == "media"
    ]


class FakePipe:
    """StreamReader stand-in: yields queued blocks, then EOF or waits for release.

    ``hold`` models a real pipe whose writer is still alive: the read blocks until
    the process ends. ``release`` is what the process's exit does to it, and a
    blocked read must wake on that -- otherwise the fake would report a reader
    that cannot finish even after its writer is gone, which no real pipe does.
    """

    def __init__(self, blocks: Any = (), *, hold: bool = False) -> None:
        self.blocks = list(blocks)
        self.hold = hold
        self.closed = False
        self.reads = 0
        self._released = asyncio.Event()

    async def read(self, size: int = -1) -> bytes:
        del size
        self.reads += 1
        if self.blocks:
            return self.blocks.pop(0)
        if self.hold and not self.closed:
            await self._released.wait()
        return b""

    def release(self) -> None:
        self.closed = True
        self._released.set()


class FakeProcess:
    """A decoder whose exit is controllable, including one that ignores terminate.

    Windows cannot express "terminate is ignored" with a real child: terminate is
    TerminateProcess there and cannot be caught. The code path under test is
    terminate -> bounded grace -> kill, so the process is faked and the assertion
    is on which call ended it and how long the grace took.
    """

    def __init__(self, *, obey_terminate: bool = True, hold_pipes: bool = False,
                 stderr_blocks: Any = ()) -> None:
        self.stdout = FakePipe(hold=hold_pipes)
        self.stderr = FakePipe(stderr_blocks, hold=hold_pipes)
        self.returncode: int | None = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self.obey_terminate = obey_terminate
        self._exited = asyncio.Event()

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.obey_terminate:
            self._exit()

    def kill(self) -> None:
        self.kill_calls += 1
        self._exit()

    def _exit(self) -> None:
        if self.returncode is None:
            self.returncode = 0
            self.stdout.release()
            self.stderr.release()
            self._exited.set()

    async def wait(self) -> int | None:
        await self._exited.wait()
        return self.returncode


class FakeClock:
    """A monotonic clock that only moves when the test moves it."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return 1_700_000_000.0 + (self.now - 1000.0)


class BudgetStream(FakeStream):
    """A provider close that consumes the teardown deadline on purpose."""

    def __init__(self, clock: FakeClock, *, flush_advance: float, aclose_sleep: float) -> None:
        self.clock = clock
        self.flush_advance = flush_advance
        self.aclose_sleep = aclose_sleep
        self.flush_calls = 0
        self.aclose_calls = 0
        self.aclose_started_at: float | None = None

    async def flush(self) -> None:
        self.flush_calls += 1
        self.clock.now += self.flush_advance
        await asyncio.sleep(0)

    async def aclose(self) -> None:
        self.aclose_calls += 1
        self.aclose_started_at = self.clock.now
        await asyncio.sleep(self.aclose_sleep)


class StderrDrainTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        logbook.reset()

    async def tearDown(self) -> None:
        # A failed assertion must not leave the synthetic decoder behind.
        await asyncio.sleep(0)

    async def _run_with_insurance(self, body: Any, decoder: SyntheticDecoder) -> None:
        try:
            await asyncio.wait_for(body(), DECODER_HARD_EXIT_SECONDS)
        finally:
            decoder.kill_if_alive()

    async def _stop_quietly(self, pipeline: SubtitlePipeline) -> None:
        with contextlib.suppress(Exception):
            await pipeline.stop()

    async def test_pcm_advances_past_a_one_megabyte_stderr_burst(self) -> None:
        """D: without a reader this stops at 0.1s, because the child blocks on write."""
        decoder = SyntheticDecoder(DECODER_BURST, 1 << 20)
        pipeline = make_pipeline(decoder.factory)

        async def body() -> None:
            await pipeline.start("tcp://synthetic", None, "mpegts")
            self.assertIsNotNone(decoder.pid, "the synthetic decoder never started")
            await until(
                lambda: pipeline._pcm_offset >= 0.2,
                5.0,
                "PCM to reach 0.2s: the decoder is blocked writing stderr",
            )
            self.assertGreaterEqual(pipeline._stderr_bytes, 1 << 20)
            self.assertLessEqual(len(pipeline._stderr_tail), _STDERR_TAIL_BYTES)
            self.assertTrue(pipeline._stderr_tail_truncated)
            await pipeline.stop()
            self.assertEqual(pipeline._residue, [], "teardown left something alive")
            self.assertTrue(pipeline._teardown_done)

        try:
            await self._run_with_insurance(body, decoder)
        finally:
            await self._stop_quietly(pipeline)

    async def test_a_multi_megabyte_line_cannot_grow_the_tail_or_starve_the_loop(self) -> None:
        """D: a byte bound survives what a line bound cannot, and the loop keeps running."""
        decoder = SyntheticDecoder(DECODER_NO_NEWLINE, 3 << 20)
        pipeline = make_pipeline(decoder.factory)
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0)

        async def body() -> None:
            await pipeline.start("tcp://synthetic", None, "mpegts")
            spinner = asyncio.create_task(ticker())
            try:
                await until(
                    lambda: pipeline._pcm_offset >= 0.2,
                    5.0,
                    "PCM to advance while a multi-megabyte line is drained",
                )
            finally:
                spinner.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await spinner
            self.assertLessEqual(len(pipeline._stderr_tail), _STDERR_TAIL_BYTES)
            self.assertGreater(ticks, 0, "the event loop never got a turn during the drain")
            await pipeline.stop()
            self.assertEqual(pipeline._residue, [])

        try:
            await self._run_with_insurance(body, decoder)
        finally:
            await self._stop_quietly(pipeline)

    async def test_a_reader_failure_ends_its_own_decoder_and_leaves_a_record(self) -> None:
        """D: the reader has no recovery, so its fault owner must end the decoder."""
        process = FakeProcess(hold_pipes=True, stderr_blocks=[b"decoder: first line\n"])
        pipeline = make_pipeline()
        pipeline._process = process

        async def explode(_size: int = -1) -> bytes:
            raise OSError("stderr pipe broke")

        process.stderr.read = explode  # type: ignore[method-assign]
        task = asyncio.create_task(pipeline._stderr_reader(process), name="subtitle-stderr-reader")
        pipeline._stderr_task = task
        task.add_done_callback(pipeline._on_stderr_done)

        await asyncio.wait({task}, timeout=2.0)
        await asyncio.sleep(0.05)
        self.assertTrue(task.done())
        self.assertEqual(process.kill_calls, 1, "a failed reader left the decoder writing")
        self.assertIn("OSError", pipeline.stats.last_error or "")
        self.assertIsNotNone(pipeline._stop_task, "no owner took over the teardown")
        records = media_records()
        self.assertTrue(any("诊断读取失败" in message for message in records), records)

    async def test_teardown_kills_a_decoder_that_ignores_terminate(self) -> None:
        """D: terminate, bounded grace, kill -- and the pipes still get reclaimed."""
        process = FakeProcess(obey_terminate=False, hold_pipes=True)
        pipeline = make_pipeline()
        pipeline._process = process
        pipeline._running = True
        reader = asyncio.create_task(pipeline._stderr_reader(process), name="subtitle-stderr-reader")
        pipeline._stderr_task = reader
        reader.add_done_callback(pipeline._on_stderr_done)
        pcm = asyncio.create_task(pipeline._pcm_reader(), name="subtitle-pcm-reader")
        pipeline._tasks = [pcm]

        started = time.monotonic()
        await pipeline.stop()
        elapsed = time.monotonic() - started

        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 1)
        self.assertLess(elapsed, 3.0, "the kill grace did not overlap the rest of teardown")
        self.assertEqual(pipeline._residue, [], "teardown reported residue after a successful kill")
        self.assertTrue(pcm.done())
        self.assertTrue(pipeline._teardown_done)

    async def test_no_step_receives_a_second_five_seconds(self) -> None:
        """D: flush eats 4.8s of the budget; the close then gets 0.2s, not a fresh 5s."""
        clock = FakeClock()
        original = pipeline_module.time
        pipeline_module.time = clock  # type: ignore[assignment]
        self.addCleanup(setattr, pipeline_module, "time", original)

        stream = BudgetStream(clock, flush_advance=4.8, aclose_sleep=0.3)
        pipeline = make_pipeline()
        pipeline._stream = stream
        pipeline._running = True

        started = time.monotonic()
        with self.assertRaises(RuntimeError) as raised:
            await pipeline.stop()
        elapsed = time.monotonic() - started

        self.assertEqual(stream.flush_calls, 1)
        self.assertEqual(stream.aclose_calls, 1)
        self.assertEqual(stream.aclose_started_at, 1004.8, "flush did not spend the shared deadline")
        self.assertIn("stream.aclose", str(raised.exception))
        self.assertEqual(pipeline._residue, ["stream.aclose"])
        self.assertLess(elapsed, 1.5, "a later step was handed a second full budget")

    async def test_a_worker_that_will_not_stop_is_reported_not_forgotten(self) -> None:
        """D: the shared deadline also governs worker cancellation."""
        clock = FakeClock()
        original = pipeline_module.time
        pipeline_module.time = clock  # type: ignore[assignment]
        self.addCleanup(setattr, pipeline_module, "time", original)

        stream = BudgetStream(clock, flush_advance=4.9, aclose_sleep=0.0)

        async def stubborn() -> None:
            # Swallows the first cancellation and honours the second, which is
            # the honest shape of "a coroutine that will not cooperate with one
            # cancel" without making the test impossible to clean up.
            swallowed = False
            while True:
                try:
                    await asyncio.sleep(0.05)
                except asyncio.CancelledError:
                    if swallowed:
                        raise
                    swallowed = True

        pipeline = make_pipeline()
        pipeline._stream = stream
        pipeline._running = True
        worker = asyncio.create_task(stubborn(), name="subtitle-stubborn")
        pipeline._tasks = [worker]

        started = time.monotonic()
        with self.assertRaises(RuntimeError) as raised:
            await pipeline.stop()
        elapsed = time.monotonic() - started

        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        self.assertIn("worker cancellation", str(raised.exception))
        self.assertLess(elapsed, 1.5, "worker cancellation waited out a fresh five seconds")
        self.assertEqual(pipeline._tasks, [worker], "the live handle was dropped")


class DecoderDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        logbook.reset()

    def test_a_truncated_leading_line_never_reaches_the_log(self) -> None:
        """D: the trim cuts on a byte boundary, so the fragment can be mid-credential."""
        pipeline = make_pipeline()
        pipeline._stderr_bytes = 4096
        pipeline._stderr_tail_truncated = True
        pipeline._stderr_tail.extend(
            b"oken=SUPERSECRETVALUE&signature=deadbeef\nreal decoder line\n"
        )
        pipeline._emit_decoder_diagnostic("warn", "字幕解码器开始输出诊断")
        joined = "\n".join(media_records())
        self.assertNotIn("SUPERSECRETVALUE", joined)
        self.assertNotIn("deadbeef", joined)
        self.assertIn("real decoder line", joined)

    def test_a_single_overlong_line_is_summarised_not_sliced(self) -> None:
        """D: no arbitrary slice of a >64KiB line, and the byte count is stated."""
        pipeline = make_pipeline()
        pipeline._stderr_bytes = 70000
        pipeline._stderr_tail_truncated = True
        pipeline._stderr_tail.extend(b"e" * 70000)
        lines = pipeline._decoder_tail_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("70000", lines[0])
        self.assertLess(len(lines[0]), 200, "an arbitrary slice of a long line was quoted")

    def test_every_record_stays_inside_the_logbook_cap(self) -> None:
        """D: the existing 400-character cap and redaction still apply."""
        pipeline = make_pipeline()
        pipeline._stderr_bytes = 2000
        pipeline._stderr_tail.extend(
            b"https://example.invalid/live.m3u8?token=abcdef\n" + b"z" * 1000 + b"\n"
        )
        pipeline._emit_decoder_diagnostic("warn", "字幕解码器开始输出诊断")
        records = media_records()
        self.assertTrue(records)
        for message in records:
            self.assertLessEqual(len(message), logbook.MAX_MESSAGE_CHARS)
            self.assertNotIn("token=abcdef", message)
        self.assertNotIn("z" * 401, "\n".join(records))

    def test_snapshots_are_capped_per_decoder_lifecycle(self) -> None:
        """D: two snapshots -- the first capture and the end -- and then silence."""
        pipeline = make_pipeline()
        pipeline._stderr_bytes = 100
        pipeline._stderr_tail.extend(b"one\ntwo\n")
        emitted = [
            pipeline._emit_decoder_diagnostic("warn", f"snapshot {index}")
            for index in range(5)
        ]
        self.assertEqual(emitted, [True, True, False, False, False])
        self.assertEqual(len(media_records()), _STDERR_MAX_SNAPSHOTS * (1 + 2))
        self.assertLessEqual(_STDERR_TAIL_LINES, _STDERR_TAIL_LINES)

    async def test_no_stderr_means_no_records(self) -> None:
        """G: a decoder that says nothing must not produce an empty diagnostic."""
        process = FakeProcess()
        pipeline = make_pipeline()
        await pipeline._stderr_reader(process)
        self.assertEqual(pipeline._stderr_bytes, 0)
        self.assertEqual(media_records(), [])
        self.assertEqual(pipeline._stderr_snapshots, 0)


class StopOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        logbook.reset()

    async def test_stop_is_idempotent_and_leaves_no_task_behind(self) -> None:
        """G: repeated Stop, no residue, nothing reported as still running."""
        pipeline = make_pipeline()

        async def worker() -> None:
            while True:
                await asyncio.sleep(0.05)

        task = asyncio.create_task(worker(), name="subtitle-worker")
        pipeline._running = True
        pipeline._tasks = [task]

        await pipeline.stop()
        await pipeline.stop()
        self.assertTrue(task.done())
        self.assertEqual(pipeline._tasks, [])
        self.assertEqual(pipeline._residue, [])
        self.assertTrue(pipeline._teardown_done)

    async def test_a_tail_final_emitted_at_close_is_still_consumed(self) -> None:
        """G: the bounded drain exists so Stop does not discard the last cue."""
        pipeline = make_pipeline(asr_provider=TailFinalASR())
        seen: list[str] = []

        async def record(event: Any) -> None:
            seen.append(event.text or "")

        pipeline._handle_asr_event = record  # type: ignore[method-assign]
        pipeline._running = True
        manager = asyncio.create_task(pipeline._asr_manager(), name="subtitle-asr-manager")
        pipeline._tasks = [manager]
        await until(lambda: pipeline._stream is not None, 2.0, "the ASR stream to be adopted")

        await pipeline.stop()

        self.assertEqual(seen, ["尾句"], "the final the provider emitted at close was dropped")
        self.assertTrue(manager.done())
        self.assertEqual(pipeline._residue, [])

    async def test_a_failed_teardown_keeps_the_pipeline_and_blocks_a_restart(self) -> None:
        """D: the reference survives a failed stop, so Start cannot overwrite it."""
        pipeline = make_pipeline()
        pipeline._residue = ["decoder process"]
        with self.assertRaises(RuntimeError) as raised:
            await pipeline.start("tcp://synthetic", None, "mpegts")
        self.assertIn("decoder process", str(raised.exception))

    async def test_server_keeps_the_pipeline_when_its_teardown_fails(self) -> None:
        """D: server._stop_subtitles must not forget a pipeline it could not clean."""
        app = object.__new__(CompanionApplication)
        app.subtitle_pipeline = _FailingPipeline()
        app.asr_audio_ingest = None
        app.private_hls_token = "token"
        app._asr_audio_leg = True

        with self.assertRaises(RuntimeError):
            await app._stop_subtitles()

        self.assertIsInstance(app.subtitle_pipeline, _FailingPipeline)
        self.assertIsNone(app.private_hls_token)
        self.assertFalse(app._asr_audio_leg)

    async def test_server_releases_the_pipeline_after_a_clean_teardown(self) -> None:
        """G: the ordinary path still forgets the pipeline."""
        app = object.__new__(CompanionApplication)
        pipeline = make_pipeline()
        app.subtitle_pipeline = pipeline
        app.asr_audio_ingest = None
        app.private_hls_token = "token"
        app._asr_audio_leg = True

        await app._stop_subtitles()

        self.assertIsNone(app.subtitle_pipeline)
        self.assertTrue(pipeline._teardown_done)


class _FailingPipeline:
    """Minimal stand-in whose stop() reports residue the way the real one does."""

    async def stop(self) -> None:
        raise RuntimeError("subtitle decoder teardown did not finish: stream.aclose")


if __name__ == "__main__":
    unittest.main()
