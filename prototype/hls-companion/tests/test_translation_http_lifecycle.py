import asyncio
import unittest

from aiohttp import web

from companion.providers import http
from companion.providers.mt_openai_compat import OpenAICompatibleTranslationProvider
from companion.providers.base import StreamMeta
from companion.subtitle_pipeline import SubtitlePipeline
from companion.subtitle_store import CueStore
from test_subtitle_pipeline import FakeASR, deliver_final


class TranslationHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connections = set()
        self.headers = []
        self.release = asyncio.Event()
        self.entered = asyncio.Event()
        app = web.Application()
        app.router.add_post('/{tail:.*}', self.handle)
        self.runner = web.AppRunner(app, shutdown_timeout=.1)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'

    async def asyncTearDown(self):
        self.release.set()
        await self.runner.cleanup()

    async def handle(self, request):
        self.connections.add(request.transport)
        self.headers.append(dict(request.headers))
        await request.read()
        if request.path == '/stall':
            response = web.StreamResponse(headers={'Content-Type': 'application/json'})
            await response.prepare(request)
            await response.write(b'{')
            self.entered.set()
            await self.release.wait()
            return response
        response = web.json_response({'choices': [{'message': {'content': '你好'}}]})
        response.set_cookie('must_not_replay', 'test')
        return response

    async def test_real_subtitle_worker_reuses_connection_and_closes_on_cancel(self):
        provider = OpenAICompatibleTranslationProvider({'id': 'local', 'model': 'local', 'baseUrl': self.url, 'apiKey': 'test'})
        pipeline = SubtitlePipeline(asr_provider=FakeASR(), translation_provider=provider,
                                    cue_store=CueStore(), meta=StreamMeta(None, None, None, 'ja', 'zh-Hans'))
        pipeline.media_epoch = 0
        pipeline._running = True
        worker = asyncio.create_task(pipeline._translation_worker())
        try:
            for index in range(12):
                await deliver_final(pipeline, f'こんにちは{index}。', str(index), begin=index * 3, end=index * 3 + 2)
                await asyncio.wait_for(pipeline._translation_queue.join(), 2)
            self.assertEqual(len(self.headers), 12)
            self.assertEqual(len(self.connections), 1)
        finally:
            pipeline._running = False
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        await asyncio.sleep(.02)
        self.assertTrue(all(connection.is_closing() for connection in self.connections))

    async def test_timeout_while_reading_json_is_not_swallowed(self):
        with self.assertRaises(asyncio.TimeoutError):
            await http.post_json(self.url + '/stall', headers={}, payload={}, timeout=.04)

    async def test_reuse_does_not_keep_auth_headers_or_provider_cookies(self):
        async with http.translation_session():
            await http.post_json(self.url + '/', headers={'Authorization': 'Bearer first'}, payload={}, timeout=1)
            await http.post_json(self.url + '/', headers={'Authorization': 'Bearer second'}, payload={}, timeout=1)
        self.assertEqual(len(self.connections), 1)
        self.assertEqual([h.get('Authorization') for h in self.headers], ['Bearer first', 'Bearer second'])
        self.assertTrue(all('Cookie' not in h for h in self.headers))

    async def test_cancel_inflight_body_releases_the_connection(self):
        async def worker():
            async with http.translation_session():
                await http.post_json(self.url + '/stall', headers={}, payload={}, timeout=10)
        task = asyncio.create_task(worker())
        await asyncio.wait_for(self.entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.sleep(.02)
        self.assertTrue(all(connection.is_closing() for connection in self.connections))


if __name__ == '__main__':
    unittest.main()
