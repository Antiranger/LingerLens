from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from types import SimpleNamespace

from aiohttp.test_utils import AioHTTPTestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import companion.server as server_module
from companion.server import CompanionApplication, errors
from companion.core import BrowserCookieSnapshot, LiveSession
from companion.providers.base import SourceLanguagePolicy, TranslationCapabilities, TranslationLanguageCapabilities
from companion.source_timeline import PTS_HZ, PTS_MODULUS

# One whole turn of the 33-bit source clock: 2**33 ticks at 90kHz, about
# 26.512144 hours. A wrap landing between the two legs' first packets is what
# makes their raw origins look 26.5 hours apart.
WRAP_SECONDS = PTS_MODULUS / PTS_HZ


class AsrAudioLegSelectorTests(unittest.TestCase):
    """P3-B: the dedicated ASR leg uses a self-resolving yt-dlp selector so
    probe-time manifest fluctuation cannot silently disable it (observed live:
    audio-only renditions 233/234 present in one extraction, gone the next)."""

    def test_youtube_gets_generic_hls_audio_selector(self) -> None:
        info = {"extractor": "youtube", "formats": []}
        selector = CompanionApplication._asr_audio_leg_selector(info)
        self.assertEqual(selector, "234/233/ba[protocol^=m3u8]/worst[protocol^=m3u8]")

    def test_selector_does_not_depend_on_probe_formats(self) -> None:
        # Even a probe that transiently lists zero audio-only formats must
        # still enable the audio leg.
        info = {
            "extractor": "youtube",
            "formats": [
                {"format_id": "301", "vcodec": "avc1.4D402A", "acodec": "mp4a.40.2", "url": "u", "protocol": "m3u8_native"},
            ],
        }
        self.assertIsNotNone(CompanionApplication._asr_audio_leg_selector(info))

    def test_muxed_only_platforms_return_none(self) -> None:
        for extractor in ("bililive", "twitchstream"):
            info = {"extractor": extractor, "formats": []}
            self.assertIsNone(CompanionApplication._asr_audio_leg_selector(info), msg=extractor)


class SourceClockOffsetTests(unittest.TestCase):
    """C = ptsFirst(asr-audio) - ptsFirst(media video), read off the source clock.

    The two legs ARE independently extracted; that makes their origins
    different, not incomparable. Live measurement 2026-09-16 held this at
    +5.006s with a range of 0.000s over 600s, while the window median the
    anchor used before swung across 9.1s.

    The dangerous case is a leg that never had an absolute clock: the mpegts
    muxer's default output origin is 1.4s, and subtracting two such origins
    yields ~0 -- confidently wrong, not merely imprecise.

    R1 split the None cases in two, and the split is the point. "Not measured
    yet" returns None and leaves the sampled window in charge, which is what the
    anchor always did. "Measured, and the clock is not trustworthy" returns None
    AND switches the sampled window off, because that window is the mapping the
    exact offset replaced -- publishing it after deciding the clock is unusable
    would present a suspect position as a measured one. The 33-bit wrap is no
    longer in the first group at all: it is unwrapped, not refused.
    """

    REBASED = 1.4
    ABSOLUTE_AUDIO = 27886.406
    ABSOLUTE_VIDEO = 27881.4

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        args = argparse.Namespace(
            runtime_dir=root / "media",
            providers_file=root / "providers.json",
            publish_delay=2.0,
            cookies_from_browser=None,
            host="127.0.0.1",
            port=8765,
        )
        self.companion = CompanionApplication(args)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def leg(self, first: float | None, pumps: int = 1, *, clock_valid: bool = True, reason: str | None = None):
        # A leg now has to vouch for its own clock as well as report an origin.
        # The default here is the production one; the tests that care about a
        # broken clock pass clock_valid=False explicitly.
        return SimpleNamespace(
            source_pts_first=[first] * pumps,
            sourceClockValid=clock_valid,
            sourceClockReason=reason,
        )

    def legs(self, *firsts: float | None, clock_valid: bool = True, reason: str | None = None):
        """One leg with one entry per pump, for the tests that need two origins."""
        return SimpleNamespace(
            source_pts_first=list(firsts),
            sourceClockValid=clock_valid,
            sourceClockReason=reason,
        )

    def wire(self, audio, video) -> None:
        self.companion.asr_audio_ingest = audio
        self.companion.source_ingest = video

    def test_subtracts_the_two_absolute_origins(self) -> None:
        self.wire(self.leg(self.ABSOLUTE_AUDIO), self.leg(self.ABSOLUTE_VIDEO, pumps=2))
        self.assertAlmostEqual(
            self.companion._source_clock_offset() or 0,
            self.ABSOLUTE_AUDIO - self.ABSOLUTE_VIDEO,
            places=6,
        )

    def test_reads_the_media_legs_video_pump_not_its_audio_pump(self) -> None:
        # Pump 0 is the video leg; pump 1 accompanies it in the packaging mux.
        # Reading the wrong pump would shift C by the mux's internal A/V skew.
        video = self.legs(self.ABSOLUTE_VIDEO, self.ABSOLUTE_VIDEO + 3.0)
        self.wire(self.leg(self.ABSOLUTE_AUDIO), video)
        self.assertAlmostEqual(
            self.companion._source_clock_offset() or 0,
            self.ABSOLUTE_AUDIO - self.ABSOLUTE_VIDEO,
            places=6,
        )

    def test_rebased_origins_are_refused(self) -> None:
        self.wire(self.leg(self.REBASED), self.leg(self.REBASED, pumps=2))
        self.assertIsNone(self.companion._source_clock_offset())

    def test_one_rebased_leg_is_refused(self) -> None:
        self.wire(self.leg(self.ABSOLUTE_AUDIO), self.leg(self.REBASED, pumps=2))
        self.assertIsNone(self.companion._source_clock_offset())

    def test_a_young_streams_small_absolute_origin_is_accepted(self) -> None:
        # Regression: the guard first tested for "hours", which is wrong. A
        # stream that began minutes ago legitimately reports an absolute origin
        # of a few hundred seconds -- measured 216s live on a freshly started
        # ANNnewsCH stream, tracking wall clock across two launches 50s apart.
        # Gating on magnitude silently disabled the exact offset for every
        # young stream.
        audio = self.legs(266.479)
        video = self.legs(261.473, 261.5)
        self.wire(audio, video)
        self.assertAlmostEqual(
            self.companion._source_clock_offset() or 0, 5.006, places=3
        )

    def test_missing_or_absent_pts_is_refused(self) -> None:
        self.wire(None, None)
        self.assertIsNone(self.companion._source_clock_offset())
        self.wire(self.leg(None), self.leg(self.ABSOLUTE_VIDEO, pumps=2))
        self.assertIsNone(self.companion._source_clock_offset())
        self.wire(self.leg(self.ABSOLUTE_AUDIO), self.legs())
        self.assertIsNone(self.companion._source_clock_offset())

    def test_a_leg_that_rebases_mid_session_is_refused(self) -> None:
        # _pcm_offset is monotonic across a decoder restart, so a fresh
        # subtraction after a leg restarts no longer describes where pcm 0 sits
        # on the packaged timeline. Refusing it leaves the sampled anchor in
        # charge, which re-converges on its own.
        audio = self.leg(self.ABSOLUTE_AUDIO)
        self.wire(audio, self.leg(self.ABSOLUTE_VIDEO, pumps=2))
        self.assertIsNotNone(self.companion._source_clock_offset())
        audio.source_pts_first = [self.ABSOLUTE_AUDIO + 30.0]
        self.assertIsNone(self.companion._source_clock_offset())

    def test_a_wrap_between_the_legs_is_unwrapped_not_refused(self) -> None:
        # R1. The clock is 33 bits wide at 90kHz, so an origin of 3s and an origin
        # of (W - 2s) are FIVE SECONDS apart, not 26.5 hours. Before R1 the raw
        # subtraction produced the 26.5-hour figure and the skew guard refused it,
        # which was safe but left a stream that crossed the wrap permanently on the
        # sampled window -- the mapping measured 4.44s off.
        self.wire(self.leg(3.0), self.leg(WRAP_SECONDS - 2.0, pumps=2))
        offset = self.companion._source_clock_offset()
        self.assertIsNotNone(offset)
        self.assertAlmostEqual(offset, 5.0, places=6)
        # Exact to the tick, not merely close: the protocol resolution is 1/90000s.
        self.assertLessEqual(abs(offset - 5.0), 1 / PTS_HZ)

    def test_the_wrap_is_unwrapped_in_the_other_direction_too(self) -> None:
        self.wire(self.leg(WRAP_SECONDS - 2.0), self.leg(3.0, pumps=2))
        offset = self.companion._source_clock_offset()
        self.assertIsNotNone(offset)
        self.assertAlmostEqual(offset, -5.0, places=6)

    def test_a_skew_that_survives_unwrapping_is_still_refused(self) -> None:
        # G: the 600s bound is not decoration. Two origins 26.5 hours apart in the
        # RAW numbers are 5s apart once unwrapped, so the bound now catches a
        # genuine mismatch rather than the wrap itself.
        self.wire(self.leg(3.0), self.leg(4.0 + 700.0, pumps=2))
        self.assertIsNone(self.companion._source_clock_offset())

    def test_an_untrusted_leg_clock_refuses_and_switches_the_fallback_off(self) -> None:
        # D: the probe said the clock is not usable. Publishing the sampled
        # estimate instead would present the very mapping the exact offset
        # replaced, under a name that implies something was measured.
        self.wire(
            self.leg(self.ABSOLUTE_AUDIO),
            self.leg(self.ABSOLUTE_VIDEO, pumps=2, clock_valid=False, reason="transport-discontinuity"),
        )
        self.assertIsNone(self.companion._source_clock_offset())
        self.assertFalse(self.companion._sampled_fallback_allowed())

    def test_a_leg_that_does_not_report_its_clock_is_not_trusted(self) -> None:
        # D: an absent statement is not a positive one. Treating a missing field as
        # valid is how a reset gets modulo'd into a small, plausible-looking gap.
        self.wire(
            SimpleNamespace(source_pts_first=[self.ABSOLUTE_AUDIO]),
            self.leg(self.ABSOLUTE_VIDEO, pumps=2),
        )
        self.assertIsNone(self.companion._source_clock_offset())
        self.assertFalse(self.companion._sampled_fallback_allowed())

    def test_one_untrusted_leg_is_not_rescued_by_the_other(self) -> None:
        # D: the offset is a difference between the two legs, so a pair is only as
        # good as its weaker clock.
        self.wire(
            self.leg(self.ABSOLUTE_AUDIO, clock_valid=False, reason="non-wrap-clock-reset"),
            self.leg(self.ABSOLUTE_VIDEO, pumps=2),
        )
        self.assertIsNone(self.companion._source_clock_offset())
        self.assertFalse(self.companion._sampled_fallback_allowed())

    def test_the_rebase_window_stays_ambiguous_even_with_a_wrap_shaped_pair(self) -> None:
        # D: a first origin inside the mpegts re-base window is what a legitimate
        # beginning-of-clock looks like after a wrap. Taking a modulus would turn
        # "I cannot tell" into a number that looks precise, so it stays refused.
        self.wire(self.leg(1.4), self.leg(WRAP_SECONDS - 2.0, pumps=2))
        self.assertIsNone(self.companion._source_clock_offset())
        self.assertFalse(self.companion._sampled_fallback_allowed())

    def test_a_session_that_has_not_measured_yet_keeps_the_sampled_window(self) -> None:
        # G: "not measured" is not "untrustworthy". The anchor behaved this way
        # before an exact offset existed, and a session that has simply not
        # received its first PTS must not lose its subtitles over it.
        self.wire(None, None)
        self.assertIsNone(self.companion._source_clock_offset())
        self.assertTrue(self.companion._sampled_fallback_allowed())
        self.wire(self.leg(None), self.leg(self.ABSOLUTE_VIDEO, pumps=2))
        self.assertIsNone(self.companion._source_clock_offset())
        self.assertTrue(self.companion._sampled_fallback_allowed())

    def test_a_mid_session_rebase_keeps_the_sampled_window(self) -> None:
        # G: the sampled window is the mechanism that re-converges after a re-base,
        # so this case must NOT disable it -- refusing the stale subtraction and
        # refusing the fallback are different decisions.
        audio = self.leg(self.ABSOLUTE_AUDIO)
        self.wire(audio, self.leg(self.ABSOLUTE_VIDEO, pumps=2))
        self.assertIsNotNone(self.companion._source_clock_offset())
        audio.source_pts_first = [self.ABSOLUTE_AUDIO + 30.0]
        self.assertIsNone(self.companion._source_clock_offset())
        self.assertTrue(self.companion._sampled_fallback_allowed())

    def test_the_offset_is_computed_once_and_survives_a_wrap_crossing(self) -> None:
        # G: a wrap crossing the session moves the raw origins but must not move
        # the latched offset. The latched pair is the raw pair, and the offset is
        # derived from it, so both readings agree to the tick.
        audio = self.leg(self.ABSOLUTE_AUDIO)
        video = self.leg(self.ABSOLUTE_VIDEO, pumps=2)
        self.wire(audio, video)
        first = self.companion._source_clock_offset()
        self.assertIsNotNone(first)
        self.assertAlmostEqual(self.companion._source_clock_offset(), first, places=9)
        self.assertTrue(self.companion._sampled_fallback_allowed())


class ProviderApiTests(AioHTTPTestCase):
    async def get_application(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        args = argparse.Namespace(
            runtime_dir=root / "media",
            providers_file=root / "providers.json",
            publish_delay=2.0,
            cookies_from_browser=None,
            host="127.0.0.1",
            port=8765,
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
            json={"asr": {"active": "bailian-fun-asr-2026-02-28"}, "translation": {"fallback": []}},
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["asr"]["active"], "bailian-fun-asr-2026-02-28")
        self.assertEqual(payload["translation"]["fallback"], [])

        response = await self.client.post("/api/providers", json={"asr": {"apiKey": "leak"}})
        self.assertEqual(response.status, 400)
        persisted = (Path(self.temporary.name) / "providers.json").read_text(encoding="utf-8")
        self.assertNotIn("leak", persisted)

    async def test_target_language_update_hot_switches_running_subtitle_pipeline(self) -> None:
        class RunningPipeline:
            def __init__(self) -> None:
                language = TranslationLanguageCapabilities(open_world_prompting=True)
                self.translation_provider = SimpleNamespace(
                    capabilities=TranslationCapabilities(True, True, True, False, 1000, language)
                )
                self.source_policy = SourceLanguagePolicy.specified("ja")
                self.targets: list[str] = []

            def update_target_language(self, target: str) -> bool:
                self.targets.append(target)
                return True

            async def stop(self) -> None:
                return None

        companion = self.app["companion"]
        pipeline = RunningPipeline()
        companion.subtitle_pipeline = pipeline
        current = await (await self.client.get("/api/providers")).json()
        subtitle = {**current["subtitle"], "targetLanguage": "en-US"}

        response = await self.client.post("/api/providers", json={"subtitle": subtitle})

        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual((await response.json())["subtitle"]["targetLanguage"], "en-US")
        self.assertEqual(pipeline.targets, ["en-US"])

    async def test_private_hls_requires_session_token_and_ready_segment(self) -> None:
        companion = self.app["companion"]
        private = companion.session.private_dir
        private.mkdir(parents=True)
        (private / "init.mp4").write_bytes(b"init")
        (private / "seg_000000000.m4s").write_bytes(b"segment")
        (private / "live.m3u8").write_text(
            "#EXTM3U\n#EXT-X-MAP:URI=\"init.mp4\"\n"
            "#EXT-X-PROGRAM-DATE-TIME:2026-09-05T00:00:00+00:00\n"
            "#EXTINF:1.0,\nseg_000000000.m4s\n",
            encoding="utf-8",
        )
        companion.session.publisher = SimpleNamespace(pdt_epoch=1_788_547_200.0, stop=lambda: None)
        companion.private_hls_token = "private-token"

        epoch = await companion._wait_for_private_hls()
        self.assertEqual(epoch, 1_788_547_200.0)
        denied = await self.client.get("/_private-hls/wrong/live.m3u8")
        self.assertEqual(denied.status, 404)
        allowed = await self.client.get("/_private-hls/private-token/live.m3u8")
        self.assertEqual(allowed.status, 200)
        self.assertEqual(allowed.headers["Cache-Control"], "no-store, max-age=0")

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
        self.assertEqual(saved["version"], 3)
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
        self.assertEqual(restarted_view["version"], 3)
        self.assertEqual(restarted_view["asr"]["active"], "local-whisper")
        self.assertEqual(restarted_view["translation"]["fallback"], ["cloud-translation"])
        response = await self.client.get("/api/model-settings")
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["translation"]["active"], "local-translation")

    async def test_model_settings_persists_provider_pricing_and_rejects_negative_rates(self) -> None:
        catalog = await (await self.client.get("/api/model-settings")).json()
        asr = catalog["asr"]["providers"][0]
        translation = catalog["translation"]["providers"][0]
        asr["pricePerSecondCny"] = 0
        translation["pricePerMillionInputTokensCny"] = 1.25
        translation["pricePerMillionCachedInputTokensCny"] = 0.25
        translation["pricePerMillionOutputTokensCny"] = 3.5

        response = await self.client.post("/api/model-settings", json=catalog)
        self.assertEqual(response.status, 200, await response.text())
        saved = await response.json()
        self.assertEqual(saved["asr"]["providers"][0]["pricePerSecondCny"], 0)
        self.assertEqual(saved["translation"]["providers"][0]["pricePerMillionInputTokensCny"], 1.25)
        self.assertEqual(saved["translation"]["providers"][0]["pricePerMillionCachedInputTokensCny"], 0.25)
        self.assertEqual(saved["translation"]["providers"][0]["pricePerMillionOutputTokensCny"], 3.5)

        restarted = CompanionApplication(argparse.Namespace(
            runtime_dir=Path(self.temporary.name) / "media-pricing-restart",
            providers_file=Path(self.temporary.name) / "providers.json",
            publish_delay=2.0,
            cookies_from_browser=None,
        ))
        restarted_translation = restarted.providers_config["translation"]["providers"][0]
        self.assertEqual(restarted.providers_config["asr"]["providers"][0]["pricePerSecondCny"], 0)
        self.assertEqual(restarted_translation["pricePerMillionInputTokensCny"], 1.25)
        self.assertEqual(restarted_translation["pricePerMillionCachedInputTokensCny"], 0.25)
        self.assertEqual(restarted_translation["pricePerMillionOutputTokensCny"], 3.5)

        catalog["asr"]["providers"][0]["pricePerSecondCny"] = -0.01
        response = await self.client.post("/api/model-settings", json=catalog)
        self.assertEqual(response.status, 400)
        self.assertIn("pricePerSecondCny", (await response.json())["error"])

        catalog["asr"]["providers"][0]["pricePerSecondCny"] = 0
        catalog["translation"]["providers"][0]["pricePerMillionCachedInputTokensCny"] = -1
        response = await self.client.post("/api/model-settings", json=catalog)
        self.assertEqual(response.status, 400)
        self.assertIn("pricePerMillionCachedInputTokensCny", (await response.json())["error"])

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
                    {"id": "legacy-fallback", "label": "Legacy Fallback", "kind": "openai-compatible", "model": "fallback-model", "baseUrl": "https://fallback.example/v1", "apiKey": "fallback-key", "options": {"contextPairs": 3}},
                ],
            },
            "subtitle": {"sourceLanguage": "ja", "targetLanguage": "zh", "manualOffsetSeconds": 1.25, "bilingual": False},
        }
        path.write_text(json.dumps(legacy), encoding="utf-8")
        response = await self.client.get("/api/model-settings")
        self.assertEqual(response.status, 200)
        migrated = await response.json()
        self.assertEqual(migrated["version"], 3)
        self.assertEqual(migrated["asr"]["active"], "legacy-asr")
        self.assertEqual(migrated["translation"]["fallback"], ["legacy-fallback"])
        self.assertEqual(migrated["asr"]["providers"][0]["apiKey"], "legacy-asr-key")
        self.assertEqual(migrated["translation"]["providers"][0]["model"], "legacy-model")
        self.assertEqual(migrated["subtitle"]["sourceLanguage"], {"mode": "specified", "tag": "ja"})
        self.assertEqual(migrated["subtitle"]["targetLanguage"], "zh-Hans")
        self.assertEqual(migrated["subtitle"]["manualOffsetSeconds"], 1.25)
        persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["version"], 3)
        self.assertEqual(persisted["translation"]["providers"][1]["apiKey"], "fallback-key")

    async def test_version_two_language_config_migrates_to_v3_without_losing_anything(self) -> None:
        path = Path(self.temporary.name) / "providers.json"
        legacy = {
            "version": 2,
            "asr": {
                "active": "fun-asr",
                "providers": [{
                    "id": "fun-asr", "label": "Fun ASR", "kind": "dashscope-task-asr",
                    "model": "fun-asr-realtime-2026-02-28", "baseUrl": "wss://legacy.example/inference",
                    "apiKey": "asr-key-2", "pricePerSecondCny": 0.00033,
                    "options": {"sampleRate": 16000, "languages": ["zh", "en", "ja"]},
                }],
            },
            "translation": {
                "active": "legacy-mt", "fallback": ["legacy-fallback"],
                "providers": [
                    {"id": "legacy-mt", "label": "Legacy MT", "kind": "openai-compatible", "model": "legacy-model", "baseUrl": "https://legacy.example/v1", "apiKey": "legacy-mt-key", "pricePerMillionInputTokensCny": 2.0, "pricePerMillionCachedInputTokensCny": 0.5, "pricePerMillionOutputTokensCny": 8.0, "options": {"contextPairs": 7}},
                    {"id": "legacy-fallback", "label": "Legacy Fallback", "kind": "openai-compatible", "model": "fallback-model", "baseUrl": "https://fallback.example/v1", "apiKey": "fallback-key", "options": {"contextPairs": 3}},
                ],
            },
            "subtitle": {
                "sourceLanguage": "ja", "targetLanguage": "zh",
                "manualOffsetSeconds": 1.25, "bilingual": False, "holdSecondsMin": 2.0,
            },
        }
        path.write_text(json.dumps(legacy), encoding="utf-8")
        response = await self.client.get("/api/model-settings")
        self.assertEqual(response.status, 200)
        migrated = await response.json()
        self.assertEqual(migrated["version"], 3)
        # ja -> zh becomes specified ja -> zh-Hans.
        self.assertEqual(migrated["subtitle"]["sourceLanguage"], {"mode": "specified", "tag": "ja"})
        self.assertEqual(migrated["subtitle"]["targetLanguage"], "zh-Hans")
        # Profiles, active/fallback ids, keys, prices and other subtitle
        # preferences all survive untouched.
        self.assertEqual(migrated["asr"]["active"], "fun-asr")
        self.assertEqual([item["id"] for item in migrated["asr"]["providers"]], ["fun-asr"])
        self.assertEqual(migrated["asr"]["providers"][0]["apiKey"], "asr-key-2")
        self.assertEqual(migrated["asr"]["providers"][0]["pricePerSecondCny"], 0.00033)
        self.assertEqual(migrated["translation"]["active"], "legacy-mt")
        self.assertEqual(migrated["translation"]["fallback"], ["legacy-fallback"])
        self.assertEqual(migrated["translation"]["providers"][0]["apiKey"], "legacy-mt-key")
        self.assertEqual(migrated["translation"]["providers"][0]["pricePerMillionInputTokensCny"], 2.0)
        self.assertEqual(migrated["translation"]["providers"][0]["pricePerMillionCachedInputTokensCny"], 0.5)
        self.assertEqual(migrated["translation"]["providers"][0]["pricePerMillionOutputTokensCny"], 8.0)
        self.assertEqual(migrated["translation"]["providers"][1]["apiKey"], "fallback-key")
        self.assertEqual(migrated["subtitle"]["manualOffsetSeconds"], 1.25)
        self.assertEqual(migrated["subtitle"]["bilingual"], False)
        self.assertEqual(migrated["subtitle"]["holdSecondsMin"], 2.0)
        persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["version"], 3)
        self.assertEqual(persisted["subtitle"]["sourceLanguage"], {"mode": "specified", "tag": "ja"})
        self.assertEqual(persisted["subtitle"]["targetLanguage"], "zh-Hans")
        self.assertEqual(persisted["asr"]["providers"][0]["apiKey"], "asr-key-2")
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

    async def test_languages_endpoint_returns_catalog_and_effective_capabilities(self) -> None:
        response = await self.client.get("/api/languages")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["catalogVersion"], 1)
        by_tag = {entry["tag"]: entry for entry in payload["languages"]}
        self.assertEqual(by_tag["zh-Hans"]["englishName"], "Chinese (Simplified)")
        self.assertEqual(by_tag["zh-Hant"]["autonym"], "中文（繁體）")
        self.assertEqual(by_tag["ar"]["direction"], "rtl")
        self.assertIn("zh-CN", by_tag["zh-Hans"]["aliases"])
        # The default active ASR is the verified Fun-ASR preset: specified
        # languages only, no detection, no code-switching.
        asr = payload["asr"]
        self.assertEqual(asr["providerId"], "bailian-fun-asr-2026-02-28")
        self.assertEqual(asr["language"]["tier"], "verified")
        self.assertEqual(asr["language"]["detection"], "none")
        self.assertFalse(asr["language"]["codeSwitching"])
        self.assertEqual(asr["language"]["supportedTags"], ["zh", "en", "ja"])
        self.assertEqual(asr["preferredSampleRate"], 16000)
        translation = payload["translation"]
        self.assertTrue(translation["language"]["openWorldPrompting"])
        self.assertEqual(payload["defaults"]["sourceLanguage"], {"mode": "specified", "tag": "ja"})
        self.assertEqual(payload["defaults"]["targetLanguage"], "zh-Hans")

    async def test_start_rejects_unsupported_source_policy_and_target_pair(self) -> None:
        companion = self.app["companion"]
        with (
            patch.object(server_module.SubtitlePipeline, "start", new=AsyncMock(return_value=lambda _chunk: None)),
            patch.object(server_module.SubtitlePipeline, "stop", new=AsyncMock(return_value=None)),
        ):
            # Fun-ASR cannot auto-detect.
            with self.assertRaisesRegex(ValueError, "auto-detect"):
                await companion._prepare_subtitles({}, {"enabled": True, "sourceLanguage": {"mode": "detect"}})
            # Fun-ASR supports zh/en/ja only.
            with self.assertRaisesRegex(ValueError, "fr"):
                await companion._prepare_subtitles({}, {"enabled": True, "sourceLanguage": {"mode": "specified", "tag": "fr"}})
            # Code-switching is not a Fun-ASR capability.
            with self.assertRaisesRegex(ValueError, "code-switching"):
                await companion._prepare_subtitles({}, {
                    "enabled": True,
                    "sourceLanguage": {"mode": "detect", "candidates": ["ja"], "allowCodeSwitching": True},
                })

    async def test_start_rejects_missing_key_and_invalid_candidates_for_new_stt(self) -> None:
        """Ticket 02 pre-start errors: missing credential and candidates outside
        Deepgram nova-3's multi detection set are rejected before playback."""
        companion = self.app["companion"]
        catalog = await (await self.client.get("/api/model-settings")).json()
        deepgram = {
            "id": "dg-nova3",
            "label": "Deepgram Nova-3",
            "kind": "deepgram-streaming",
            "model": "nova-3",
            "baseUrl": "wss://api.deepgram.com/v1/listen",
            "apiKey": "",
            "options": {},
        }
        catalog["asr"]["providers"].append(deepgram)
        catalog["asr"]["active"] = "dg-nova3"
        response = await self.client.post("/api/model-settings", json=catalog)
        self.assertEqual(response.status, 200, await response.text())
        with (
            patch.object(server_module.SubtitlePipeline, "start", new=AsyncMock(return_value=lambda _chunk: None)),
            patch.object(server_module.SubtitlePipeline, "stop", new=AsyncMock(return_value=None)),
        ):
            # Missing credential is reported before playback starts.
            with self.assertRaisesRegex(ValueError, "API key"):
                await companion._prepare_subtitles({}, {"enabled": True, "sourceLanguage": {"mode": "specified", "tag": "ja"}})

            deepgram["apiKey"] = "dg-secret"
            response = await self.client.post("/api/model-settings", json=catalog)
            self.assertEqual(response.status, 200, await response.text())
            # nova-3's multi detection cannot detect zh, even though zh is a
            # valid specified language for the same model.
            with self.assertRaisesRegex(ValueError, "candidates"):
                await companion._prepare_subtitles({}, {
                    "enabled": True,
                    "sourceLanguage": {"mode": "detect", "candidates": ["zh"]},
                })
            # Multi-set candidates plus code-switching are honored.
            pipeline = await companion._prepare_subtitles({}, {
                "enabled": True,
                "sourceLanguage": {"mode": "detect", "candidates": ["ja", "en"], "allowCodeSwitching": True},
            })
            self.assertIsInstance(pipeline, server_module.SubtitlePipeline)

    async def test_subtitle_start_uses_active_catalog_records_not_request_overrides(self) -> None:
        companion = self.app["companion"]
        catalog = await (await self.client.get("/api/model-settings")).json()
        catalog["asr"]["active"] = "bailian-fun-asr-2026-02-28"
        catalog["translation"]["active"] = "bailian-qwen35-flash"
        catalog["translation"]["fallback"] = []
        response = await self.client.post("/api/model-settings", json=catalog)
        self.assertEqual(response.status, 200, await response.text())

        selected = []
        original = companion._provider_record

        def record(section, provider_id):
            selected.append(provider_id)
            return original(section, provider_id)

        companion._provider_record = record
        with (
            patch.object(server_module.SubtitlePipeline, "start", new=AsyncMock(return_value=lambda _chunk: None)),
            patch.object(server_module.SubtitlePipeline, "stop", new=AsyncMock(return_value=None)),
        ):
            pipeline = await companion._prepare_subtitles({}, {
                "asrProviderId": "bailian-qwen3-realtime",
                "translationProviderId": "bailian-qwen35-flash",
            })
            self.assertEqual(selected[:2], ["bailian-fun-asr-2026-02-28", "bailian-qwen35-flash"])
            # Independent YouTube legs keep the MediaAnchor, but not as their
            # primary source of truth: both renditions carry ONE absolute 90kHz
            # clock (held to a range of 0.000s over 600s live), so the offset is
            # the difference of the two legs' origins rather than a median of two
            # stage-output counters. source_pts_mapper stays None because the
            # mapping now lives on the anchor itself.
            self.assertIsNone(pipeline.source_pts_mapper)
            self.assertIsNotNone(pipeline.media_anchor)
            await companion._stop_subtitles()

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

    async def test_twitch_cookie_import_is_optional_and_domain_scoped(self) -> None:
        response = await self.client.post(
            "/api/auth-cookies",
            json={"platform": "twitch", "header": "auth-token=secret; persistent=1"},
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["platform"], "twitch")
        self.assertEqual(payload["missingCritical"], [])
        companion = self.app["companion"]
        snapshot = companion._authentication(
            {"url": "https://www.twitch.tv/example", "authToken": payload["authToken"]},
            consume=False,
        )
        try:
            content = Path(snapshot.yt_dlp_args()[1]).read_text(encoding="utf-8")
            self.assertIn("twitch.tv", content)
            self.assertNotIn("youtube.com", content)
        finally:
            snapshot.close()

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

        def fake_start(page_url, inputs, _publish_delay, _command, **_kwargs):
            LiveSession.stop(companion.session)
            companion.session.page_url = page_url
            companion.session.quality = inputs.quality
            companion.session.started_at = 123.0
            companion.session.error = None

        companion.session.start = fake_start
        with (
            patch("companion.server.YtDlpLiveIngest", FakeIngest),
            patch("companion.server.ProbeInfoSnapshot"),
            patch("companion.server.build_ffmpeg_command", return_value=["fake-ffmpeg"]),
        ):
            response = await self.client.post(
                "/api/start",
                json={"url": "https://live.bilibili.com/1", "qualityId": "auto"},
            )
            self.assertEqual(response.status, 200)

            # A failed source/FFmpeg still owns local resources until Stop runs.
            # The cleanup endpoint must remain valid and reset that error state.
            companion.session.error = "simulated FFmpeg failure"
            failed = await (await self.client.get("/api/status")).json()
            self.assertEqual(failed["state"], "error")

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
        companion.session.start = lambda _url, _inputs, delay, _command, **_kwargs: captured.update(delay=delay)
        with (
            patch.object(server_module, "YtDlpLiveIngest", FakeIngest),
            patch.object(server_module, "build_ffmpeg_command", return_value=["fake-ffmpeg"]),
        ):
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
        self.assertIn("asrUsage", payload["stats"])
        self.assertIn("asrEstimatedCostCny", payload["stats"])
        self.assertIn("translationUsage", payload["stats"])
        self.assertIn("translationEstimatedCostCny", payload["stats"])
        self.assertIn("totalEstimatedCostCny", payload["stats"])
        self.assertIsNone(payload["stats"]["totalEstimatedCostCny"])
        self.assertIn("unavailable", payload["stats"]["totalEstimateReason"])

        status = await (await self.client.get("/api/status")).json()
        self.assertEqual(status["subtitles"]["asrUsage"], payload["stats"]["asrUsage"])
        self.assertEqual(status["subtitles"]["translationUsage"], payload["stats"]["translationUsage"])
        self.assertIsNone(status["subtitles"]["totalEstimatedCostCny"])

        companion.subtitle_store.update(1, zh="你好", state="done")
        response = await self.client.get("/api/subtitles?afterSeq=1")
        payload = await response.json()
        self.assertEqual(payload["cues"][0]["zh"], "你好")
        self.assertEqual(payload["cues"][0]["revision"], 2)
        self.assertEqual(payload["cues"][0]["seq"], 2)
        self.assertEqual(payload["maxSeq"], 2)


    async def test_live_messages_endpoints_and_status(self) -> None:
        companion = self.app["companion"]
        companion.message_store.add(
            platform="youtube",
            source_id="m1",
            author={"id": "a", "name": "Alice", "badges": []},
            text="Hello server messages",
            received_monotonic=100.0,
            received_at=1700000001.0,
            media_time=1700000000.0,
        )
        response = await self.client.get("/api/live-messages?afterSeq=0")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(len(payload["messages"]), 1)
        self.assertEqual(payload["messages"][0]["author"]["name"], "Alice")
        self.assertEqual(payload["messages"][0]["kind"], "text")
        self.assertEqual(payload["maxSeq"], 1)
        self.assertIn("stats", payload)

        status_res = await self.client.get("/api/messages/status")
        self.assertEqual(status_res.status, 200)
        status_payload = await status_res.json()
        self.assertIn("state", status_payload)
        self.assertIn("pendingClock", status_payload)

        main_status = await (await self.client.get("/api/status")).json()
        self.assertIn("mediaClock", main_status)
        self.assertIn("liveMessages", main_status)


if __name__ == "__main__":
    unittest.main()
