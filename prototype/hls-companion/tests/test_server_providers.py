from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import AioHTTPTestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.server import CompanionApplication, errors
from companion.core import BrowserCookieSnapshot


class ProviderApiTests(AioHTTPTestCase):
    async def get_application(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        args = argparse.Namespace(
            runtime_dir=root / "media",
            providers_file=root / "providers.json",
            publish_delay=2.0,
            cookies_from_browser=None,
        )
        companion = CompanionApplication(args)
        companion.control.start = lambda: None
        companion.control.stop = lambda: None
        app = companion.routes()
        app.middlewares.append(errors)
        return app

    async def asyncTearDown(self) -> None:
        await super().asyncTearDown()
        self.temporary.cleanup()

    async def test_get_masks_keys_and_post_only_updates_allowed_fields(self) -> None:
        response = await self.client.get("/api/providers")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("_apiKey", rendered)
        self.assertIn("apiKeyConfigured", rendered)

        response = await self.client.post(
            "/api/providers",
            json={"asr": {"active": "bailian-paraformer"}, "translation": {"fallback": []}},
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["asr"]["active"], "bailian-paraformer")
        self.assertEqual(payload["translation"]["fallback"], [])

        response = await self.client.post("/api/providers", json={"asr": {"apiKey": "leak"}})
        self.assertEqual(response.status, 400)
        persisted = (Path(self.temporary.name) / "providers.json").read_text(encoding="utf-8")
        self.assertNotIn("leak", persisted)

    async def test_model_settings_persist_asr_and_openai_compatible_translation(self) -> None:
        response = await self.client.get("/api/model-settings")
        self.assertEqual(response.status, 200)
        initial = await response.json()
        self.assertEqual(initial["asr"]["model"], "qwen3-asr-flash-realtime")
        self.assertIn("fun-asr-realtime-2026-02-28", [choice["model"] for choice in initial["asr"]["choices"]])
        self.assertEqual(initial["translation"]["kind"], "openai-compatible")

        response = await self.client.post(
            "/api/model-settings",
            json={
                "asr": {"baseUrl": "wss://dashscope.aliyuncs.com/api-ws/v1/realtime", "apiKey": "asr-secret"},
                "translation": {
                    "baseUrl": "https://api.openai.com/v1",
                    "model": "gpt-4.1-mini",
                    "apiKey": "translation-secret",
                    "temperature": 0.2,
                    "maxTokens": 384,
                    "timeoutSeconds": 8,
                    "contextPairs": 5,
                },
            },
        )
        self.assertEqual(response.status, 200)
        view = await response.json()
        self.assertTrue(view["asr"]["apiKeyConfigured"])
        self.assertTrue(view["translation"]["apiKeyConfigured"])
        self.assertEqual(view["translation"]["model"], "gpt-4.1-mini")
        rendered = json.dumps(view)
        self.assertNotIn("asr-secret", rendered)
        self.assertNotIn("translation-secret", rendered)
        persisted = json.loads((Path(self.temporary.name) / "providers.json").read_text(encoding="utf-8"))
        openai = next(item for item in persisted["translation"]["providers"] if item["kind"] == "openai-compatible")
        self.assertEqual(openai["baseUrl"], "https://api.openai.com/v1")
        self.assertEqual(openai["model"], "gpt-4.1-mini")
        self.assertEqual(openai["apiKey"], "translation-secret")

        # Switching protocol adds the built-in Fun-ASR preset to an old config,
        # reuses the current DashScope key, and makes it active.
        response = await self.client.post(
            "/api/model-settings",
            json={
                "asr": {
                    "providerId": "bailian-fun-asr-2026-02-28",
                    "baseUrl": "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
                },
                "translation": {
                    "baseUrl": "https://api.openai.com/v1",
                    "model": "gpt-4.1-mini",
                },
            },
        )
        self.assertEqual(response.status, 200)
        view = await response.json()
        self.assertEqual(view["asr"]["providerId"], "bailian-fun-asr-2026-02-28")
        self.assertEqual(view["asr"]["model"], "fun-asr-realtime-2026-02-28")
        persisted = json.loads((Path(self.temporary.name) / "providers.json").read_text(encoding="utf-8"))
        fun = next(item for item in persisted["asr"]["providers"] if item["id"] == "bailian-fun-asr-2026-02-28")
        self.assertEqual(persisted["asr"]["active"], fun["id"])
        self.assertEqual(fun["kind"], "dashscope-task-asr")
        self.assertEqual(fun["apiKey"], "asr-secret")

    async def test_cookie_import_filters_domains_and_returns_token(self) -> None:
        lines = (
            "Name\tValue\n"
            "SID\tsecret-value\t.youtube.com\t/\t2027-10-02T08:29:22.479Z\t156\t\t\t\t\t\tHigh\n"
            "__Secure-1PSID\tanother\n"
            "badrow\n"
        )
        response = await self.client.post("/api/auth-cookies", json={"lines": lines, "domain": ".youtube.com"})
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["accepted"], 2)
        self.assertIn("missingCritical", payload)
        self.assertIn("SID", payload["names"])
        companion = self.app["companion"]
        stored = companion.auth_snapshots[payload["authToken"]]
        self.assertEqual(stored[0]["name"], "SID")
        self.assertEqual(stored[0]["value"], "secret-value")
        # The regenerated Netscape row must stay exactly 7 tab-separated fields.
        snapshot = companion._authentication({"authToken": payload["authToken"]}, consume=False)
        try:
            row = Path(snapshot.yt_dlp_args()[1]).read_text(encoding="utf-8").splitlines()[1]
            self.assertEqual(len(row.split("\t")), 7)
        finally:
            snapshot.close()

        netscape = (
            "# Netscape HTTP Cookie File\n"
            "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1900000000\tSID\tsecret-value\n"
            ".evil.com\tTRUE\t/\tTRUE\t1900000000\tbad\tx\n"
            ".youtube.com\tTRUE\t/\tTRUE\t1900000000\tSID\tduplicate\n"
        )
        response = await self.client.post("/api/auth-cookies", json={"netscape": netscape})
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["accepted"], 1)
        self.assertEqual(payload["skipped"], 2)
        companion = self.app["companion"]
        stored = companion.auth_snapshots[payload["authToken"]]
        self.assertEqual(stored[0]["name"], "SID")
        self.assertEqual(stored[0]["value"], "secret-value")

        response = await self.client.post(
            "/api/auth-cookies",
            json={"header": "SID=abc; VISITOR_INFO1_LIVE=xyz; junk", "domain": ".youtube.com"},
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["accepted"], 2)
        # A partial import without the login-critical cookies is flagged.
        self.assertIn("HSID", payload["missingCritical"])
        self.assertIn("SAPISID", payload["missingCritical"])

    async def test_cookie_import_rejects_cross_origin_and_empty_imports(self) -> None:
        response = await self.client.post(
            "/api/auth-cookies",
            json={"header": "SID=abc"},
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(response.status, 403)
        response = await self.client.post("/api/auth-cookies", json={"header": "a=b", "domain": ".evil.com"})
        self.assertEqual(response.status, 400)

    async def test_imported_cookies_survive_restart(self) -> None:
        companion = self.app["companion"]
        response = await self.client.post(
            "/api/auth-cookies",
            json={"lines": "SID\tsaved-secret\nHSID\th\nSSID\ts\nAPISID\ta\nSAPISID\tsa\nLOGIN_INFO\tli\n__Secure-1PSID\tp1\n__Secure-3PSID\tp3\n__Secure-1PSIDTS\tt1\n__Secure-3PSIDTS\tt3\n__Secure-1PSIDCC\tc1\n__Secure-3PSIDCC\tc3\n", "domain": ".youtube.com"},
        )
        self.assertEqual(response.status, 200)
        self.assertTrue((await response.json())["persisted"])

        # Simulate a restart: a fresh application over the same providers file.
        args = argparse.Namespace(
            runtime_dir=Path(self.temporary.name) / "media2",
            providers_file=Path(self.temporary.name) / "providers.json",
            publish_delay=2.0,
            cookies_from_browser=None,
        )
        restarted = CompanionApplication(args)
        self.assertIsNotNone(restarted.persisted_auth)
        auth = restarted._authentication({}, consume=True)
        try:
            self.assertIsInstance(auth, BrowserCookieSnapshot)
            cookie_args = auth.yt_dlp_args()
            self.assertIn("--cookies", cookie_args)
            saved = json.loads((Path(self.temporary.name) / "auth-snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["cookies"][0]["name"], "SID")
        finally:
            auth.close()

    async def test_subtitle_polling_returns_seq_updates_and_status(self) -> None:
        companion = self.app["companion"]
        import time
        cue_end = time.time()
        companion.subtitle_store.add(
            t_start=cue_end - 1,
            t_end=cue_end,
            hold=2.0,
            src="こんにちは",
            lang="ja",
            timing_source="vad",
        )
        response = await self.client.get("/api/subtitles?afterSeq=0")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["cues"][0]["tEnd"], cue_end)
        self.assertEqual(payload["cues"][0]["seq"], 1)
        self.assertEqual(payload["maxSeq"], 1)
        self.assertIn("asrSeconds", payload["stats"])

        companion.subtitle_store.update(1, zh="你好", state="done")
        response = await self.client.get("/api/subtitles?afterSeq=1")
        payload = await response.json()
        self.assertEqual(payload["cues"][0]["zh"], "你好")
        self.assertEqual(payload["cues"][0]["revision"], 2)
        self.assertEqual(payload["cues"][0]["seq"], 2)
        self.assertEqual(payload["maxSeq"], 2)


if __name__ == "__main__":
    unittest.main()
