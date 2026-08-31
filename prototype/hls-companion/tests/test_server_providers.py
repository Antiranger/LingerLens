from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from aiohttp.test_utils import AioHTTPTestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import companion.server as server_module
from companion.server import CompanionApplication, errors
from companion.core import BrowserCookieSnapshot, LiveSession


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

    async def test_model_settings_catalog_persists_multiple_profiles_and_active_selection(self) -> None:
        catalog = {
            "asr": {
                "active": "local-whisper",
                "providers": [
                    {
                        "id": "cloud-qwen",
                        "label": "Cloud Qwen",
                        "kind": "dashscope-qwen-realtime",
                        "model": "qwen3-asr-flash-realtime",
                        "baseUrl": "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
                        "apiKey": "cloud-secret",
                        "options": {"sampleRate": 16000},
                    },
                    {
                        "id": "local-whisper",
                        "label": "Local Whisper",
                        "kind": "openai-audio-transcriptions",
                        "model": "whisper-1",
                        "baseUrl": "http://127.0.0.1:8000/v1",
                        "apiKey": "",
                        "options": {"windowSeconds": 2.0, "requestTimeoutSeconds": 10},
                    },
                ],
            },
            "translation": {
                "active": "local-translation",
                "fallback": ["cloud-translation"],
                "providers": [
                    {
                        "id": "cloud-translation",
                        "label": "Cloud Translation",
                        "kind": "openai-compatible",
                        "model": "qwen3.5-flash",
                        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "apiKey": "translation-secret",
                        "options": {"timeoutSeconds": 6},
                    },
                    {
                        "id": "local-translation",
                        "label": "Local Translation",
                        "kind": "openai-compatible",
                        "model": "local-model",
                        "baseUrl": "http://127.0.0.1:9000/v1",
                        "apiKey": "dummy",
                        "options": {"timeoutSeconds": 4},
                    },
                ],
            },
        }
        response = await self.client.post("/api/model-settings", json=catalog)
        self.assertEqual(response.status, 200, await response.text())
        saved = await response.json()
        self.assertEqual(saved["version"], 2)
        self.assertEqual(saved["asr"]["active"], "local-whisper")
        self.assertEqual([item["id"] for item in saved["asr"]["providers"]], ["cloud-qwen", "local-whisper"])
        self.assertEqual(saved["asr"]["providers"][0]["apiKey"], "cloud-secret")
        self.assertEqual(saved["translation"]["providers"][1]["apiKey"], "dummy")

        args = argparse.Namespace(
            runtime_dir=Path(self.temporary.name) / "media2",
            providers_file=Path(self.temporary.name) / "providers.json",
            publish_delay=2.0,
            cookies_from_browser=None,
        )
        restarted = CompanionApplication(args)
        restarted_view = restarted.providers_config
        self.assertEqual(restarted_view["version"], 2)
        self.assertEqual(restarted_view["asr"]["active"], "local-whisper")
        self.assertEqual(restarted_view["translation"]["fallback"], ["cloud-translation"])
        response = await self.client.get("/api/model-settings")
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["translation"]["active"], "local-translation")

    async def test_version_one_catalog_migrates_without_losing_records_or_subtitle_preferences(self) -> None:
        path = Path(self.temporary.name) / "providers.json"
        legacy = {
            "version": 1,
            "asr": {
                "active": "legacy-asr",
                "providers": [{
                    "id": "legacy-asr", "label": "Legacy ASR", "kind": "dashscope-qwen-realtime",
                    "model": "qwen3-asr-flash-realtime", "baseUrl": "wss://legacy.example/realtime",
                    "apiKey": "legacy-asr-key", "options": {"sampleRate": 16000, "turnDetection": {"silenceDurationMs": 321}},
                }],
            },
            "translation": {
                "active": "legacy-mt", "fallback": ["legacy-fallback"],
                "providers": [
                    {"id": "legacy-mt", "label": "Legacy MT", "kind": "openai-compatible", "model": "legacy-model", "baseUrl": "https://legacy.example/v1", "apiKey": "legacy-mt-key", "options": {"contextPairs": 7}},
                    {"id": "legacy-fallback", "label": "Legacy Qwen MT", "kind": "qwen-mt", "model": "qwen-mt-flash", "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1", "apiKey": "fallback-key", "options": {"tmPairs": 3}},
                ],
            },
            "subtitle": {"sourceLanguage": "ja", "targetLanguage": "zh", "manualOffsetSeconds": 1.25, "bilingual": False},
        }
        path.write_text(json.dumps(legacy), encoding="utf-8")
        response = await self.client.get("/api/model-settings")
        self.assertEqual(response.status, 200)
        migrated = await response.json()
        self.assertEqual(migrated["version"], 2)
        self.assertEqual(migrated["asr"]["active"], "legacy-asr")
        self.assertEqual(migrated["translation"]["fallback"], ["legacy-fallback"])
        self.assertEqual(migrated["asr"]["providers"][0]["apiKey"], "legacy-asr-key")
        self.assertEqual(migrated["translation"]["providers"][0]["model"], "legacy-model")
        self.assertEqual(migrated["subtitle"]["manualOffsetSeconds"], 1.25)
        persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["version"], 2)
        self.assertEqual(persisted["translation"]["providers"][1]["apiKey"], "fallback-key")

    async def test_model_settings_rejects_deleting_active_or_last_profile(self) -> None:
        initial = await (await self.client.get("/api/model-settings")).json()
        active_asr = initial["asr"]["active"]
        remaining_asr = [item for item in initial["asr"]["providers"] if item["id"] != active_asr]
        response = await self.client.post("/api/model-settings", json={
            "asr": {"active": active_asr, "providers": remaining_asr},
            "translation": initial["translation"],
            "subtitle": initial["subtitle"],
        })
        self.assertEqual(response.status, 400)
        self.assertIn("asr.active", (await response.json())["error"])

        only_translation = initial["translation"]["providers"][0]
        response = await self.client.post("/api/model-settings", json={
            "asr": initial["asr"],
            "translation": {"active": only_translation["id"], "fallback": [], "providers": []},
            "subtitle": initial["subtitle"],
        })
        self.assertEqual(response.status, 400)
        self.assertIn("translation.active", (await response.json())["error"])

    async def test_raw_keys_are_confined_to_loopback_same_origin_model_settings(self) -> None:
        response = await self.client.get("/api/model-settings", headers={"Origin": "http://evil.example"})
        self.assertEqual(response.status, 403)
        response = await self.client.post(
            "/api/model-settings",
            json={},
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(response.status, 403)

        settings = await (await self.client.get("/api/model-settings")).json()
        settings["asr"]["providers"][0]["apiKey"] = "visible-only-here"
        response = await self.client.post("/api/model-settings", json=settings)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        self.assertIn("visible-only-here", await response.text())

        providers_response = await self.client.get("/api/providers")
        status_response = await self.client.get("/api/status")
        self.assertNotIn("visible-only-here", await providers_response.text())
        self.assertNotIn("visible-only-here", await status_response.text())

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

    async def test_bilibili_cookie_import_formats_preserve_cookies_and_only_require_sessdata(self) -> None:
        cases = [
            {"lines": "SESSDATA\tsession-secret\nbili_jct\tcsrf-secret\nDedeUserID\t12345\n"},
            {"header": "SESSDATA=session-secret; bili_jct=csrf-secret; DedeUserID=12345"},
            {
                "netscape": (
                    "# Netscape HTTP Cookie File\n"
                    ".bilibili.com\tTRUE\t/\tTRUE\t1900000000\tSESSDATA\tsession-secret\n"
                    ".bilibili.com\tTRUE\t/\tTRUE\t1900000000\tbili_jct\tcsrf-secret\n"
                    ".bilibili.com\tTRUE\t/\tTRUE\t1900000000\tDedeUserID\t12345\n"
                )
            },
        ]
        for import_body in cases:
            response = await self.client.post(
                "/api/auth-cookies",
                json={"platform": "bilibili", **import_body},
            )
            self.assertEqual(response.status, 200)
            payload = await response.json()
            self.assertEqual(payload["platform"], "bilibili")
            self.assertEqual(payload["missingCritical"], [])
            self.assertEqual(payload["names"], ["DedeUserID", "SESSDATA", "bili_jct"])
            companion = self.app["companion"]
            snapshot = companion._authentication(
                {"url": "https://live.bilibili.com/1", "authToken": payload["authToken"]},
                consume=False,
            )
            try:
                rows = Path(snapshot.yt_dlp_args()[1]).read_text(encoding="utf-8").splitlines()[1:]
                self.assertEqual(len(rows), 3)
                self.assertTrue(all(len(row.split("\t")) == 7 for row in rows))
                self.assertTrue(all(row.startswith(".bilibili.com\t") for row in rows))
            finally:
                snapshot.close()

        response = await self.client.post(
            "/api/auth-cookies",
            json={"platform": "bilibili", "lines": "bili_jct\tcsrf-secret\nDedeUserID\t12345\n"},
        )
        payload = await response.json()
        self.assertEqual(payload["missingCritical"], ["SESSDATA"])

        response = await self.client.post(
            "/api/auth-cookies",
            json={
                "platform": "bilibili",
                "netscape": (
                    ".bilibili.com\tTRUE\t/\tTRUE\t1900000000\tSESSDATA\tbili\n"
                    ".youtube.com\tTRUE\t/\tTRUE\t1900000000\tSID\tyoutube\n"
                ),
            },
        )
        payload = await response.json()
        self.assertEqual(payload["accepted"], 1)
        self.assertEqual(payload["names"], ["SESSDATA"])

    async def test_cookie_import_rejects_cross_origin_and_empty_imports(self) -> None:
        response = await self.client.post(
            "/api/auth-cookies",
            json={"header": "SID=abc"},
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(response.status, 403)
        response = await self.client.post("/api/auth-cookies", json={"header": "a=b", "domain": ".evil.com"})
        self.assertEqual(response.status, 400)

    async def test_platform_cookies_coexist_survive_restart_and_are_selected_by_url(self) -> None:
        youtube = await self.client.post(
            "/api/auth-cookies",
            json={"platform": "youtube", "lines": "SID\tyoutube-secret\n"},
        )
        bilibili = await self.client.post(
            "/api/auth-cookies",
            json={"platform": "bilibili", "lines": "SESSDATA\tbilibili-secret\nbili_jct\tkeep-me\n"},
        )
        self.assertTrue((await youtube.json())["persisted"])
        self.assertTrue((await bilibili.json())["persisted"])

        args = argparse.Namespace(
            runtime_dir=Path(self.temporary.name) / "media2",
            providers_file=Path(self.temporary.name) / "providers.json",
            publish_delay=2.0,
            cookies_from_browser=None,
        )
        restarted = CompanionApplication(args)
        saved = json.loads((Path(self.temporary.name) / "auth-snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], 2)
        self.assertEqual(sorted(saved["platforms"]), ["bilibili", "youtube"])

        for url, expected, absent in [
            ("https://www.youtube.com/watch?v=test", "youtube-secret", "bilibili-secret"),
            ("https://live.bilibili.com/1", "bilibili-secret", "youtube-secret"),
        ]:
            auth = restarted._authentication({"url": url}, consume=True)
            try:
                contents = Path(auth.yt_dlp_args()[1]).read_text(encoding="utf-8")
                self.assertIn(expected, contents)
                self.assertNotIn(absent, contents)
                if "bilibili" in url:
                    self.assertIn("bili_jct", contents)
            finally:
                auth.close()

    async def test_legacy_single_snapshot_migrates_to_target_platform(self) -> None:
        legacy_file = Path(self.temporary.name) / "auth-snapshot.json"
        legacy_file.write_text(
            json.dumps({"version": 1, "cookies": [{"domain": ".youtube.com", "path": "/", "name": "SID", "value": "legacy-secret", "secure": True}]}),
            encoding="utf-8",
        )
        args = argparse.Namespace(
            runtime_dir=Path(self.temporary.name) / "media-legacy",
            providers_file=Path(self.temporary.name) / "providers.json",
            publish_delay=2.0,
            cookies_from_browser=None,
        )
        restarted = CompanionApplication(args)
        auth = restarted._authentication({"url": "https://www.youtube.com/watch?v=test"}, consume=True)
        try:
            self.assertIn("legacy-secret", Path(auth.yt_dlp_args()[1]).read_text(encoding="utf-8"))
        finally:
            auth.close()
        migrated = json.loads(legacy_file.read_text(encoding="utf-8"))
        self.assertEqual(migrated["version"], 2)
        self.assertIn("youtube", migrated["platforms"])

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
        auth = restarted._authentication({"url": "https://www.youtube.com/watch?v=test"}, consume=True)
        try:
            self.assertIsInstance(auth, BrowserCookieSnapshot)
            cookie_args = auth.yt_dlp_args()
            self.assertIn("--cookies", cookie_args)
            saved = json.loads((Path(self.temporary.name) / "auth-snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["platforms"]["youtube"][0]["name"], "SID")
        finally:
            auth.close()

    async def test_stop_clears_session_identity_and_allows_a_different_start(self) -> None:
        companion = self.app["companion"]

        def info_for(url: str):
            return {
                "title": "Stream B" if url.endswith("/2") else "Stream A",
                "extractor": "BiliBili",
                "is_live": True,
                "formats": [
                    {
                        "format_id": "live",
                        "url": "https://media.example/live.m3u8",
                        "width": 1280,
                        "height": 720,
                        "fps": 30,
                        "vcodec": "avc1.4d401f",
                        "acodec": "mp4a.40.2",
                        "tbr": 2500,
                    }
                ],
            }

        companion.probe.extract = lambda url, auth: info_for(url)

        class FakeIngest:
            def __init__(self, *_args, **_kwargs):
                pass

            def start(self):
                pass

            def stop(self):
                pass

            def input_urls(self):
                return ["http://127.0.0.1:1/live.ts"]

            def snapshot(self):
                return {}

            def tee_snapshot(self):
                return {"teeDropped": 0}

            def detach_audio_tee(self):
                pass

        def fake_start(page_url, inputs, _publish_delay, _command):
            LiveSession.stop(companion.session)
            companion.session.page_url = page_url
            companion.session.quality = inputs.quality
            companion.session.started_at = 123.0
            companion.session.error = None

        companion.session.start = fake_start
        with patch("companion.server.YtDlpLiveIngest", FakeIngest), patch("companion.server.ProbeInfoSnapshot"):
            response = await self.client.post(
                "/api/start",
                json={"url": "https://live.bilibili.com/1", "qualityId": "auto"},
            )
            self.assertEqual(response.status, 200)

            response = await self.client.post("/api/stop", json={})
            self.assertEqual(response.status, 200)
            stopped = (await response.json())["status"]
            self.assertEqual(stopped["state"], "idle")
            self.assertIsNone(stopped["pageUrl"])
            self.assertIsNone(stopped["quality"])
            self.assertIsNone(stopped["playlistUrl"])
            self.assertIsNone(stopped["error"])
            self.assertEqual(stopped["uptimeSeconds"], 0)
            self.assertEqual(stopped["ffmpegLogTail"], [])

            response = await self.client.post("/api/stop", json={})
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["status"], stopped)

            response = await self.client.post("/api/probe", json={"url": "https://live.bilibili.com/2"})
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["title"], "Stream B")
            response = await self.client.post(
                "/api/start",
                json={"url": "https://live.bilibili.com/2", "qualityId": "auto"},
            )
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["status"]["pageUrl"], "https://live.bilibili.com/2")

    async def test_target_delay_defaults_rejects_unsafe_values_and_stays_separate_from_estimate(self) -> None:
        companion = self.app["companion"]
        status = await (await self.client.get("/api/status")).json()
        self.assertEqual(status["targetDelaySeconds"], 15.0)
        self.assertIn("estimatedTotalDelaySeconds", status)
        self.assertNotIn("publishDelaySeconds", status)

        response = await self.client.post("/api/target-delay", json={"seconds": 10})
        self.assertEqual(response.status, 400)
        response = await self.client.post("/api/target-delay", json={"seconds": 18})
        self.assertEqual(response.status, 400)  # live tuning requires an active publisher

        class FakeIngest:
            def __init__(self, *_args, **_kwargs): pass
            def start(self): pass
            def stop(self): pass
            def input_urls(self): return ["tcp://127.0.0.1:1"]
            def snapshot(self): return {}
            def detach_audio_tee(self): pass

        info = {
            "is_live": True,
            "title": "test",
            "formats": [{
                "format_id": "95", "url": "https://media.example/live.m3u8", "height": 720,
                "width": 1280, "fps": 30, "vcodec": "avc1", "acodec": "mp4a", "tbr": 1200,
            }],
        }
        companion.probe.extract = lambda *_args: info
        captured = {}
        companion.session.stop = lambda: None
        companion.session.start = lambda _url, _inputs, delay, _command: captured.update(delay=delay)
        original = server_module.YtDlpLiveIngest
        server_module.YtDlpLiveIngest = FakeIngest
        try:
            response = await self.client.post("/api/start", json={"url": "https://www.youtube.com/watch?v=test", "qualityId": "auto"})
            self.assertEqual(response.status, 200)
            self.assertEqual(captured["delay"], 3.0)
            self.assertEqual((await response.json())["status"]["targetDelaySeconds"], 15.0)

            response = await self.client.post("/api/start", json={"url": "https://www.youtube.com/watch?v=test", "qualityId": "auto", "targetDelaySeconds": 18})
            self.assertEqual(response.status, 200)
            self.assertEqual(captured["delay"], 6.0)
            self.assertEqual((await response.json())["status"]["targetDelaySeconds"], 18.0)

            response = await self.client.post("/api/start", json={"url": "https://www.youtube.com/watch?v=test", "qualityId": "auto", "targetDelaySeconds": 10})
            self.assertEqual(response.status, 400)
        finally:
            server_module.YtDlpLiveIngest = original

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
