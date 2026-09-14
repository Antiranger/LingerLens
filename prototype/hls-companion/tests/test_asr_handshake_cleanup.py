"""Regression tests: a failed ASR handshake must not leak its ClientSession.

A failed handshake is the *common* production case, not an edge case:
``subtitle_pipeline.py:713-756`` (_asr_manager) retries forever with 0.5s -> 8s
backoff, so a wrong API key or a flaky network produces thousands of attempts.

Before the fix, ``asr_dashscope_task`` guarded only the task-started wait and
``asr_qwen_realtime`` had no guard at all, so ``ws_connect`` or the first
``send_json`` raising left both the ClientSession and its TCPConnector open:

    dashscope-task     ClientConnectorError   session.closed=False  LEAK
    qwen-realtime      ClientConnectorError   session.closed=False  LEAK
    deepgram           ClientConnectorError   session.closed=True   clean
    soniox             ClientConnectorError   session.closed=True   clean

    40 failed handshakes -> 40 unclosed ClientSessions + 40 unclosed connectors

These tests pin the contract: ``connect()`` either succeeds or raises with
nothing left open. Every attempt is hard-bounded so a hanging adapter cannot
stall the suite.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import aiohttp  # noqa: E402

from companion.providers.asr_dashscope_task import _DashScopeTaskStream  # noqa: E402
from companion.providers.asr_deepgram_streaming import _DeepgramStream  # noqa: E402
from companion.providers.asr_qwen_realtime import _QwenRealtimeStream  # noqa: E402
from companion.providers.asr_soniox_realtime import _SonioxStream  # noqa: E402

CONNECT_TIMEOUT = 5.0


def dead_url() -> str:
    """A loopback port that is closed, so connection is refused immediately."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}/ws"


def _provider(url: str) -> SimpleNamespace:
    return SimpleNamespace(
        base_url=url,
        api_key="unused-test-key",
        model="test-model",
        options={},
        capabilities=SimpleNamespace(language=SimpleNamespace(supported_tags=None)),
    )


def build_stream(kind: str, url: str):
    provider = _provider(url)
    if kind == "dashscope-task":
        return _DashScopeTaskStream(provider, language_hints=["ja"], sample_rate=16000, context=[])
    if kind == "qwen-realtime":
        return _QwenRealtimeStream(provider, language="ja", sample_rate=16000)
    if kind == "deepgram":
        policy = SimpleNamespace(mode="specified", tag="ja", candidates=(),
                                 preferred=None, allow_code_switching=False)
        return _DeepgramStream(provider, policy=policy, sample_rate=16000)
    if kind == "soniox":
        return _SonioxStream(provider, policy=None, sample_rate=16000, context=[])
    raise ValueError(kind)


ALL_KINDS = ("dashscope-task", "qwen-realtime", "deepgram", "soniox")


class AsrHandshakeCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def _failed_connect(self, kind: str):
        stream = build_stream(kind, dead_url())
        with self.assertRaises(BaseException):
            await asyncio.wait_for(stream.connect(), timeout=CONNECT_TIMEOUT)
        return stream

    async def _close(self, stream) -> None:
        session = getattr(stream, "session", None)
        if isinstance(session, aiohttp.ClientSession) and not session.closed:
            await session.close()

    async def test_failed_handshake_closes_the_session(self) -> None:
        for kind in ALL_KINDS:
            with self.subTest(adapter=kind):
                stream = await self._failed_connect(kind)
                try:
                    session = getattr(stream, "session", None)
                    if isinstance(session, aiohttp.ClientSession):
                        self.assertTrue(
                            session.closed,
                            f"{kind} left an unclosed ClientSession after a failed handshake",
                        )
                        self.assertTrue(
                            getattr(session.connector, "closed", True),
                            f"{kind} left an unclosed TCPConnector after a failed handshake",
                        )
                finally:
                    await self._close(stream)

    async def test_repeated_failures_do_not_accumulate_sessions(self) -> None:
        """Mirrors _asr_manager's unbounded reconnect loop.

        Attempts run concurrently: a connection refusal costs ~2s in aiohttp
        (DNS + connect retry), and the property under test -- each stream
        cleaning up after itself -- does not depend on ordering.
        """
        attempts = 12
        for kind in ALL_KINDS:
            with self.subTest(adapter=kind):
                url = dead_url()
                streams = [build_stream(kind, url) for _ in range(attempts)]

                async def attempt(stream):
                    try:
                        await asyncio.wait_for(stream.connect(), timeout=CONNECT_TIMEOUT)
                    except BaseException:  # noqa: BLE001 - the failure is the point
                        pass

                await asyncio.gather(*(attempt(stream) for stream in streams))
                leaked = 0
                for stream in streams:
                    session = getattr(stream, "session", None)
                    if isinstance(session, aiohttp.ClientSession) and not session.closed:
                        leaked += 1
                self.assertEqual(
                    leaked,
                    0,
                    f"{kind} leaked {leaked}/{attempts} sessions across failed handshakes",
                )


if __name__ == "__main__":
    unittest.main()
