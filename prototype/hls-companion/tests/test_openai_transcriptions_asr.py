from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.providers import create_asr


class OpenAITranscriptionsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.requests: list[dict] = []
        self.responses: asyncio.Queue[tuple[int, object]] = asyncio.Queue()

        async def transcribe(request: web.Request) -> web.Response:
            reader = await request.multipart()
            fields: dict[str, object] = {}
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.filename:
                    fields[part.name] = {"filename": part.filename, "contentType": part.headers.get("Content-Type"), "data": await part.read()}
                else:
                    fields[part.name] = await part.text()
            fields["authorization"] = request.headers.get("Authorization")
            self.requests.append(fields)
            status, payload = await self.responses.get()
            if isinstance(payload, str):
                return web.Response(status=status, text=payload, content_type="text/plain")
            return web.json_response(payload, status=status)

        app = web.Application()
        app.router.add_post("/v1/audio/transcriptions", transcribe)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}/v1"

    async def asyncTearDown(self) -> None:
        await self.runner.cleanup()

    def provider(self, api_key: str = "secret", **options):
        return create_asr({
            "id": "local-whisper",
            "label": "Local Whisper",
            "kind": "openai-audio-transcriptions",
            "model": "whisper-1",
            "baseUrl": self.base_url,
            "apiKey": api_key,
            "options": {"sampleRate": 16000, "windowSeconds": 0.1, "requestTimeoutSeconds": 2, **options},
        })

    async def test_posts_bounded_wav_multipart_with_optional_authorization_and_segment_timing(self) -> None:
        await self.responses.put((200, {"text": "unused", "segments": [{"start": 0.01, "end": 0.08, "text": "こんにちは"}]}))
        stream = await self.provider().stream(language="ja", hotwords=[], context=[])
        await stream.push_pcm(b"\x01\x00" * 1600, 4.0)
        await stream.flush()
        events = [event async for event in stream]
        self.assertEqual([(event.type, event.text, event.begin_pcm, event.end_pcm) for event in events], [("final", "こんにちは", 4.01, 4.08)])
        request = self.requests[0]
        self.assertEqual(request["model"], "whisper-1")
        self.assertEqual(request["language"], "ja")
        self.assertEqual(request["response_format"], "verbose_json")
        self.assertEqual(request["authorization"], "Bearer secret")
        audio = request["file"]
        self.assertEqual(audio["filename"], "audio.wav")
        self.assertEqual(audio["contentType"], "audio/wav")
        self.assertTrue(audio["data"].startswith(b"RIFF"))
        self.assertLessEqual(len(audio["data"]), 44 + 3200)

        await self.responses.put((200, {"text": "plain json"}))
        stream = await self.provider(api_key="").stream(language="ja", hotwords=[], context=[])
        await stream.push_pcm(b"\x00\x00" * 800, 9.0)
        await stream.flush()
        events = [event async for event in stream]
        self.assertEqual(events[0].text, "plain json")
        self.assertIsNone(self.requests[1]["authorization"])

    async def test_plain_text_errors_and_close_are_bounded_and_finish_iteration(self) -> None:
        await self.responses.put((200, "plain text transcript"))
        await self.responses.put((503, {"error": {"message": "model unavailable"}}))
        stream = await self.provider(windowSeconds=0.05, maxPendingWindows=2).stream(language="", hotwords=[], context=[])
        await stream.push_pcm(b"\x00\x00" * 800, 0.0)
        await stream.push_pcm(b"\x00\x00" * 800, 0.05)
        await stream.flush()
        events = [event async for event in stream]
        self.assertEqual(events[0].text, "plain text transcript")
        self.assertEqual(events[1].type, "error")
        self.assertIn("503", events[1].message)
        await stream.aclose()
        await stream.aclose()


if __name__ == "__main__":
    unittest.main()
