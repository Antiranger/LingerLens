import argparse
import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from companion.core import AuthenticationProvider
from companion.server import CompanionApplication
from test_media_session_identity import selected_inputs


class SessionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_during_slow_start_cannot_leave_playback_running_after_stop(self):
        await self.slow_start_then_stop(cancel_caller=False)

    async def test_disconnected_start_caller_does_not_abandon_a_running_spawn(self):
        await self.slow_start_then_stop(cancel_caller=True)

    async def slow_start_then_stop(self, *, cancel_caller):
        with tempfile.TemporaryDirectory() as raw:
            app = CompanionApplication(argparse.Namespace(runtime_dir=Path(raw)/'media', providers_file=Path(raw)/'providers.json'), enable_native_control=False)
            entered, release = threading.Event(), threading.Event()
            state = {'running': False}
            inputs = selected_inputs()
            def probe(*args):
                entered.set()
                if not release.wait(2):
                    raise TimeoutError('test release missing')
                return {'formats': []}
            def media_start(*args, **kwargs): state['running'] = True
            def media_stop(): state['running'] = False
            class Ingest:
                def __init__(self, *args, auth_cleanup=None, **kwargs): self.cleanup = auth_cleanup
                def start(self): pass
                def stop(self):
                    if self.cleanup:
                        self.cleanup()
                        self.cleanup = None
                def input_urls(self): return ['tcp://127.0.0.1:1']
            request = SimpleNamespace(json=AsyncMock(return_value={'url': 'https://www.youtube.com/watch?v=test', 'liveMessages': {'enabled': False}}))
            app._authentication = lambda *args, **kwargs: AuthenticationProvider()
            app.probe.extract = probe
            app.session.start = media_start
            app.session.stop = media_stop
            app.session.status = lambda: {'state': 'running' if state['running'] else 'idle'}
            with patch('companion.server.build_quality_options', return_value=[inputs.quality]), \
                 patch('companion.server.selected_inputs', return_value=inputs), \
                 patch('companion.server.build_ffmpeg_command', return_value=[]), \
                 patch('companion.server.ProbeInfoSnapshot', return_value=SimpleNamespace(path=None, close=lambda: None)), \
                 patch('companion.server.YtDlpLiveIngest', Ingest):
                start = asyncio.create_task(app.handle_start(request))
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                if cancel_caller:
                    start.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await start
                stop = asyncio.create_task(app.handle_stop(None))
                await asyncio.sleep(.02)
                release.set()
                try:
                    await asyncio.wait_for(stop if cancel_caller else asyncio.gather(start, stop), 3)
                    self.assertFalse(state['running'], 'Stop must not be followed by an older in-flight Start')
                    self.assertIsNone(app.source_ingest)
                finally:
                    release.set()
                    await app.cleanup(None)

    async def test_sidecar_cleanup_failure_still_stops_media_and_releases_auth(self):
        with tempfile.TemporaryDirectory() as raw:
            app = CompanionApplication(argparse.Namespace(runtime_dir=Path(raw)/'media', providers_file=Path(raw)/'providers.json'), enable_native_control=False)
            stopped = []
            released = []
            app._stop_messages = AsyncMock(side_effect=RuntimeError('chat cleanup failed'))
            app._stop_subtitles = AsyncMock()
            app._stop_source_ingest = AsyncMock()
            app.session.stop = lambda: stopped.append(True)
            app.auth_lease = SimpleNamespace(force_close=lambda: released.append(True))
            with self.assertRaisesRegex(RuntimeError, 'chat cleanup failed'):
                await app._teardown_session()
            self.assertEqual(stopped, [True])
            self.assertEqual(released, [True])
            self.assertIsNone(app.auth_lease)


if __name__ == '__main__':
    unittest.main()
