"""Actual player, DOM and request wiring with controlled clocks and API fixtures.

This is not a live-broadcast latency measurement. Poll callbacks are captured at
the existing factory boundary so thirty seconds of observations need no sleep.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

from aiohttp import web
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from companion.server import CompanionApplication


async def run() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        companion = CompanionApplication(argparse.Namespace(
            runtime_dir=Path(temporary) / "media", providers_file=Path(temporary) / "providers.json",
            publish_delay=3, cookies_from_browser=None,
        ))
        companion.control.start = lambda: None
        companion.control.stop = lambda: None
        runner = web.AppRunner(companion.routes())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with async_playwright() as playwright:
                # Assertions read Chinese UI text before the locale loop below switches
                # it, and the player now opens in the browser language, so pin it.
                browser = await playwright.chromium.launch(headless=True, executable_path=os.environ.get("LINGERLENS_TEST_CHROMIUM") or None)
                page = await browser.new_page(viewport={"width": 1680, "height": 1100}, locale="zh-CN")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.add_init_script("""
                    window.metricPolls = {};
                    window.metricNow = 100000;
                    Object.defineProperty(performance, 'now', {value: () => window.metricNow});
                    Object.defineProperty(window, 'createSerialPoller', {
                        configurable: true,
                        set(factory) {
                            this.metricPollFactory = options => {
                                window.metricPolls[options.run.name] = options.run;
                                return {start() {}, stop() {}, wake() {}};
                            };
                        },
                        get() {return this.metricPollFactory;},
                    });
                    Object.defineProperty(window, 'createMediaClock', {
                        configurable: true,
                        set(factory) {
                            this.metricClockFactory = options => {
                                const clock = factory(options);
                                window.metricClock = clock;
                                return clock;
                            };
                        },
                        get() {return this.metricClockFactory;},
                    });
                """)
                status = {"state": "running", "mediaSessionId": "metrics",
                    "playlistReady": True, "playlistUrl": None, "sourceStallSeconds": 0,
                    "hiddenMediaSeconds": 3, "privateEdgeWallTime": 1120,
                    "targetDelaySeconds": 15, "uptimeSeconds": 100,
                    "subtitles": {"avgTranslationLatencyMs": None,
                        "translationProcessingP50": 2, "translationProcessingP95": 4}}
                cues = []
                posted = []

                async def status_route(route):
                    await route.fulfill(json=status)

                async def subtitles_route(route):
                    await route.fulfill(json={"mediaSessionId": "metrics", "cues": cues,
                        "maxSeq": max((cue["seq"] for cue in cues), default=0)})

                async def target_route(route):
                    posted.append(route.request.post_data_json)
                    status["targetDelaySeconds"] = posted[-1]["seconds"]
                    await route.fulfill(json={"ok": True})

                await page.route("**/api/status*", status_route)
                await page.route("**/api/subtitles?*", subtitles_route)
                await page.route("**/api/target-delay", target_route)
                await page.goto(f"http://127.0.0.1:{port}/")
                await page.wait_for_function("window.metricPolls.refreshSubtitles && window.I18N")
                await page.evaluate("""() => {
                    const video = document.getElementById('video');
                    for (const [key, value] of Object.entries({paused: false, seeking: false,
                        readyState: 4, currentTime: 88,
                        seekable: {length: 1, start: () => 0, end: () => 100},
                        buffered: {length: 1, start: () => 0, end: () => 100}})) {
                        Object.defineProperty(video, key, {configurable: true, writable: true, value});
                    }
                    window.metricClock.playingWallTime = () => 1100;
                    document.getElementById('targetDelay').value = '15';
                }""")
                await page.evaluate("window.metricPolls.refreshStatus()")
                assert await page.locator("#playerDelay").inner_text() == "12.0 秒"
                assert await page.locator("#totalDelay").inner_text() == "20.0 秒"
                assert await page.locator("#translationLatency").inner_text() == "—"
                assert await page.locator("#subtitleReadyLag").inner_text() == "2.00s / 4.00s"
                # Put playback back at its target before collecting normal-speed
                # observations; the previous 20s lag correctly activates catch-up.
                status["privateEdgeWallTime"] = 1115
                await page.evaluate("window.metricPolls.refreshStatus()")
                await page.evaluate("window.metricPolls.refreshSubtitles()")  # Initial snapshot.

                for index in range(9):
                    cues[:] = [{"id": index + 1, "seq": index + 1, "generation": 1,
                        "tStart": 1098, "tEnd": 1101, "hold": 1, "state": "done",
                        "src": "hello", "zh": "你好"}]
                    await page.evaluate("n => { window.metricNow = n; }", 100000 + index * 5000)
                    await page.evaluate("window.metricPolls.refreshSubtitles()")
                    await page.evaluate("window.metricPolls.refreshStatus()")
                # Five samples first establish the deficit at t=120s. Keep
                # receiving evidence until it has persisted for another 30s.
                for index in range(9, 12):
                    cues[0] = dict(cues[0], id=index + 1, seq=index + 1)
                    await page.evaluate("n => { window.metricNow = n; }", 100000 + index * 5000)
                    await page.evaluate("window.metricPolls.refreshSubtitles()")
                    await page.evaluate("window.metricPolls.refreshStatus()")
                button = page.locator("#applyDelayButton")
                assert await button.is_visible()
                assert await button.inner_text() == "将目标延迟调到 19 秒"
                assert await page.locator("#budgetMargin").inner_text() == "-2.0 秒"

                labels = {"en": "Local segment lag", "ja": "ローカル区間の遅延",
                    "de": "Lokaler Segmentabstand", "ru": "Отставание от локальных сегментов",
                    "zh-CN": "本地分片延迟"}
                for locale, label in labels.items():
                    await page.select_option("#localeSwitch", locale)
                    await page.wait_for_function("label => document.querySelector('.delay-estimate .field-label').textContent === label", arg=label)
                    assert "19" in await button.inner_text()
                    tooltip = await page.locator(".delay-comparison").get_attribute("title")
                    assert tooltip and (locale == "zh-CN" or "当前值" not in tooltip)

                await button.click()
                await page.wait_for_function("document.getElementById('targetDelay').value === '19'")
                assert posted == [{"seconds": 19}], posted
                assert "继续观察" in await page.locator("#message").inner_text()
                assert await button.is_hidden()
                assert await page.locator("#budgetMargin").inner_text() == "—"
                # No current playhead means no measured delay, regardless of target.
                await page.evaluate("window.metricClock.playingWallTime = () => null")
                await page.evaluate("window.metricPolls.refreshStatus()")
                assert await page.locator("#totalDelay").inner_text() == "—"
                assert not errors, errors
                await browser.close()
        finally:
            await runner.cleanup()
    print("Metrics browser fixtures passed: local lag, arrival margin, five locales, absolute target, unknown values")


if __name__ == "__main__":
    asyncio.run(run())
