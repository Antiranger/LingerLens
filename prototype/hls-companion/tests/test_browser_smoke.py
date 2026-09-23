from __future__ import annotations

import argparse
import asyncio
import os
import json
import tempfile
from pathlib import Path

from aiohttp import web
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from companion.server import CompanionApplication, errors


async def run() -> None:
    temporary = tempfile.TemporaryDirectory()
    args = argparse.Namespace(
        runtime_dir=Path(temporary.name) / "media",
        providers_file=Path(temporary.name) / "providers.json",
        publish_delay=2.0,
        cookies_from_browser=None,
    )
    companion = CompanionApplication(args)
    companion.control.start = lambda: None
    companion.control.stop = lambda: None
    companion.message_store.add(
        platform="youtube",
        source_id="smoke",
        author={"id": "1", "name": "SmokeUser", "badges": []},
        text="こんにちは",
        media_time=1_700_000_000.0,
        received_at=1_700_000_001.0,
        received_monotonic=1.0,
    )
    class ActiveIngest:
        def status(self):
            return {"state": "running", "platform": "youtube", "running": True, "connected": True, "received": 1, "reconnects": 0, "lastError": None}
        async def stop(self):
            return None
    companion.message_ingest = ActiveIngest()

    class FakeTranslator:
        def __init__(self):
            self.enabled = False
        def set_enabled(self, enabled):
            self.enabled = bool(enabled)
        def status(self):
            return {"backlog": 0, "translationUsage": {}}
        async def stop(self):
            return None

    fake_translator = FakeTranslator()

    async def create_fake_translator():
        return fake_translator

    companion._create_message_translator = create_fake_translator

    app = companion.routes()
    app.middlewares.append(errors)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    async with async_playwright() as playwright:
        # The player opens in whatever language the browser reports, and every assertion
        # below reads Chinese UI text, so this page has to say it speaks Chinese.
        browser = await playwright.chromium.launch(headless=True, executable_path=os.environ.get("LINGERLENS_TEST_CHROMIUM") or None)
        page = await browser.new_page(viewport={"width": 2000, "height": 1100}, locale="zh-CN")
        errors_seen: list[str] = []
        page.on("pageerror", lambda error: errors_seen.append(str(error)))
        await page.goto(f"http://127.0.0.1:{port}/")
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(700)
        role_check = await page.evaluate("""() => ({
            fallbackOptions: Array.from(document.querySelectorAll('#roleFallback option'), option => option.textContent),
            subtitleOptions: Array.from(document.querySelectorAll('#roleSubtitle option'), option => option.textContent),
            japaneseHeader: Boolean(document.querySelector('.brand-jp, .side-note')),
            capabilityHint: Boolean(document.querySelector('#languageCapabilityHint')),
            fallbackSettings: Boolean(document.querySelector('.fallback-settings')),
        })""")
        # 兜底不是另一套逻辑：它能选到的 Provider 就是「字幕翻译」那一栏的全集。
        # 以前这里额外把当前生效的 Provider 滤掉，于是只配了一个翻译 Provider 时
        # 兜底永远只剩「不选」——那不像「没得选」，像坏掉了。
        assert role_check["fallbackOptions"][0] == "不选", role_check
        for option in role_check["subtitleOptions"]:
            assert option in role_check["fallbackOptions"], (option, role_check)
        assert not role_check["japaneseHeader"] and not role_check["capabilityHint"], role_check
        assert not role_check["fallbackSettings"], role_check
        cookie_switch = await page.evaluate("""() => {
            const platform = document.getElementById('cookiePlatform');
            const payload = document.getElementById('cookiePayload');
            platform.value = 'youtube';
            payload.value = 'youtube-test-only';
            platform.value = 'bilibili';
            platform.dispatchEvent(new Event('change', { bubbles: true }));
            const bilibiliValue = payload.value;
            platform.value = 'youtube';
            platform.dispatchEvent(new Event('change', { bubbles: true }));
            return { bilibiliValue, restoredYoutubeDraft: payload.value };
        }""")
        assert cookie_switch == {"bilibiliValue": "", "restoredYoutubeDraft": "youtube-test-only"}, cookie_switch
        await page.get_by_role("button", name="模型设置", exact=True).click()
        await page.wait_for_timeout(200)
        settings_check = await page.evaluate("""() => ({
            asrCards: document.querySelectorAll('#asrProfiles .connection-item').length,
            translationCards: document.querySelectorAll('#translationProfiles .connection-item').length,
            fallbackSettings: Boolean(document.querySelector('.fallback-settings')),
        })""")
        assert settings_check["asrCards"] == 1 and settings_check["translationCards"] == 1, settings_check
        assert not settings_check["fallbackSettings"], settings_check
        await page.get_by_role("button", name="关闭", exact=True).click()
        layout_check = await page.evaluate("""() => {
            const title = document.querySelector('#streamTitle').getBoundingClientRect();
            const stop = document.querySelector('#stop').getBoundingClientRect();
            const model = document.querySelector('.model-roles').getBoundingClientRect();
            const settings = document.querySelector('[aria-label="字幕与弹幕设置"]').getBoundingClientRect();
            const rows = ['#subtitlesTimelineList', '#chatTimelineList'].map(selector => {
                const row = document.createElement('article'); row.className='timeline-row';
                row.innerHTML='<div class="timeline-time">06:22:31</div><div class="timeline-body">这是时间下方的内容</div>';
                document.querySelector(selector).append(row);
                const time=row.firstChild.getBoundingClientRect(), body=row.lastChild.getBoundingClientRect();
                const correct=body.top >= time.bottom && Math.abs(body.left-time.left)<1;
                row.remove(); return correct;
            });
            return {rows, stopRight:stop.left >= title.right, modelsFirst:model.bottom <= settings.top,
                heroVisible:Boolean(document.querySelector('.hero-title'))};
        }""")
        assert all(layout_check["rows"]), layout_check
        assert layout_check["stopRight"] and layout_check["modelsFirst"] and layout_check["heroVisible"], layout_check
        overlay_check = await page.evaluate("""async () => {
            const host = document.createElement('div');
            host.className = 'chat-overlay';
            host.style.cssText = 'position:fixed;inset:auto;left:0;top:0;width:700px;height:200px';
            document.body.append(host);
            const overlay = createChatOverlay({container: host});
            const rows = [{id:'resize-check',text:'连续调节大小与动画终点测试',mediaTime:100}];
            overlay.render(100, rows);
            const node = host.firstChild;
            const before = performance.now();
            for (let i=0;i<300;i++) overlay.render(100, rows, {size: .75+(i%26)*.05});
            const elapsed = performance.now()-before;
            const sameNode = host.firstChild === node;
            const animation = node.getAnimations()[0];
            animation.pause();
            animation.currentTime = animation.effect.getTiming().duration;
            const rightBefore = node.getBoundingClientRect().right;
            host.style.width='1400px';
            const rightAfter = node.getBoundingClientRect().right;
            host.remove();
            return {elapsed,sameNode,rightBefore,rightAfter};
        }""")
        assert overlay_check["sameNode"], overlay_check
        assert abs(overlay_check["rightBefore"]) < 1, overlay_check
        assert abs(overlay_check["rightAfter"]) < 1, overlay_check
        print("Overlay resize check:", overlay_check)
        perf = await page.evaluate("""async () => {
            const host = document.getElementById('chatOverlay');
            const timelineHost = document.getElementById('chatTimelineList');
            const overlay = createChatOverlay({container:host});
            let rows = Array.from({length:500},(_,i)=>({id:'load-'+i,seq:i,revision:1,mediaTime:100-i*.02,text:'密集弹幕测试 English 日本語 '.repeat(8),author:'test'}));
            const timeline = createLiveMessagesTimeline({container:timelineHost});
            let frames=[],costs=[],longTasks=[],last=performance.now(),maxNodes=0;
            const observer=new PerformanceObserver(list=>longTasks.push(...list.getEntries().map(e=>e.duration)));
            observer.observe({type:'longtask',buffered:false});
            let active=true;
            function frame(now){frames.push(now-last);last=now;if(active)requestAnimationFrame(frame);}
            requestAnimationFrame(frame);
            const begin=performance.now();
            for(let i=0;i<60;i++) {
                const wall=100+i*.25;
                rows.push(...Array.from({length:20},(_,j)=>({id:'new-'+i+'-'+j,seq:500+i*20+j,revision:1,mediaTime:wall,text:'真实渲染压力测试 / live chat '.repeat(6),author:'test'})));
                rows=rows.slice(-500);
                const slider=document.getElementById('chatOverlaySize');
                slider.value=String(.75+(i%26)*.05);
                slider.dispatchEvent(new Event('input',{bubbles:true}));
                if(i%5===0)slider.dispatchEvent(new Event('change',{bubbles:true}));
                host.style.width=i%20<10?'100%':'70%';
                const t=performance.now();
                overlay.render(wall,rows,{size:Number(slider.value)});
                timeline.render(wall,rows);
                costs.push(performance.now()-t);
                maxNodes=Math.max(maxNodes,host.children.length);
                await new Promise(resolve=>setTimeout(resolve,250));
            }
            active=false;observer.disconnect();overlay.clear();host.style.width='';timeline.clear();
            const stats=xs=>{xs.sort((a,b)=>a-b);return {p95:xs[Math.floor(xs.length*.95)],max:xs.at(-1)};};
            return {elapsed:performance.now()-begin,frames:stats(frames),renderMs:stats(costs),longTasks:longTasks.length,maxNodes,retained:rows.length};
        }""")
        print("Dense chat performance:", perf)
        assert perf["maxNodes"] <= 40, perf
        screenshot_dir = ROOT.parents[1] / "output" / "playwright"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(screenshot_dir / "viewing-layout.png"))
        await page.set_viewport_size({"width": 390, "height": 844})
        assert await page.locator("#url").is_visible()
        mobile_url = await page.locator("#url").bounding_box()
        assert mobile_url["x"] + mobile_url["width"] <= 390, mobile_url
        await page.screenshot(path=str(screenshot_dir / "viewing-layout-mobile.png"))
        await page.set_viewport_size({"width": 2000, "height": 1100})

        # The first action lives in the video, and preparing reveals the next step.
        assert await page.locator(".player-stage #url").is_visible()
        assert not await page.locator("#setupPlayback").is_visible()
        assert await page.locator("section[aria-label='字幕与弹幕设置'] #chatOverlaySize").count() == 1
        async def probe_route(route):
            await route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "title": "Preview test", "qualities": [{"qualityId": "720p", "label": "720p", "height": 720}]
            }))
        await page.route("**/api/probe", probe_route)
        await page.locator("#url").fill("https://www.youtube.com/watch?v=test")
        await page.get_by_role("button", name="解析", exact=True).click()
        await page.locator("#setupPlayback").wait_for(state="visible")
        assert await page.locator("#start").is_enabled()
        await page.locator("#url").fill("https://www.twitch.tv/test")
        assert await page.locator("#start").is_disabled()
        assert not await page.locator("#setupPlayback").is_visible()
        await page.unroute("**/api/probe", probe_route)

        await page.evaluate("""() => {
            const video = document.getElementById('video');
            Object.defineProperty(video, 'currentTime', { configurable: true, writable: true, value: 10 });
            Object.defineProperty(video, 'seekable', {
                configurable: true,
                value: { length: 1, start: () => 0, end: () => 30 },
            });
            const hls = {
                playingDate: new Date(1_700_000_001_000),
                currentLevel: 0,
                levels: [{ details: { fragments: [] } }],
            };
            window.__smokeClock = window.createMediaClock({ getHls: () => hls, getVideo: () => video });
            const chatContainer = document.getElementById('chatTimelineList');
            chatContainer.innerHTML = '';
            window.__smokeSource = () => [{
                id: 'youtube:smoke', seq: 1, revision: 1, kind: 'text',
                mediaTime: 1_700_000_000, text: 'こんにちは',
                author: { name: 'SmokeUser', badges: [] },
            }];
            window.__smokeMessages = window.createLiveMessagesTimeline({
                container: chatContainer,
                source: window.__smokeSource,
            });
            window.__smokeMessages.setSource(window.__smokeSource);
            window.__smokeRows = window.__smokeMessages.render(
                window.__smokeClock.playingWallTime(),
            );
            window.__smokeRenderedHtml = chatContainer.innerHTML;
            window.__lingerlensSetSubtitleTestWallTime(1_700_000_001);
            const cueMap = window.__lingerlensSubtitleCues;
            cueMap.clear();
            cueMap.set(101, { id: 101, seq: 1, tStart: 1_699_999_998, tEnd: 1_700_000_002, hold: 2, state: 'done', src: '一人目', zh: '第一位', lang: 'ja', speaker: 'A' });
            cueMap.set(102, { id: 102, seq: 2, tStart: 1_699_999_999, tEnd: 1_700_000_003, hold: 2, state: 'done', src: '二人目', zh: '第二位', lang: 'ja', speaker: 'B' });
            window.__lingerlensRenderSubtitle();
            window.__lingerlensRenderTimelines();
        }""")

        grid = await page.locator("#workbenchContainer").evaluate("node => getComputedStyle(node).gridTemplateColumns")
        assert len(grid.split()) >= 3, grid
        assert await page.locator("#paneSubtitles").is_visible()
        assert await page.locator(".player-stage").is_visible()
        assert await page.locator("#paneChat").is_visible()
        assert await page.locator("#video").get_attribute("controls") is None

        stage_box = await page.locator(".player-stage").bounding_box()
        lower_deck_box = await page.locator("section.deck[aria-label='字幕与弹幕设置']").bounding_box()
        workbench_deck_box = await page.locator("section.deck[aria-label='实时工作台']").bounding_box()
        assert stage_box and lower_deck_box and workbench_deck_box
        # The design prototype gives section 01 a full-bleed deck (100vw minus a
        # 16px gutter each side) while the other sections stay inside the 1560px
        # shell, so the workbench deck must bleed past the shell on both sides.
        assert workbench_deck_box["width"] > lower_deck_box["width"], (workbench_deck_box, lower_deck_box)
        assert workbench_deck_box["x"] < lower_deck_box["x"], (workbench_deck_box, lower_deck_box)
        assert abs(workbench_deck_box["x"] - 16) < 2, workbench_deck_box
        # Panes and the stage share one grid row: same top edge, and both panes
        # are the prototype's fixed 320px sidebars, so they must match each other
        # and be narrower than the stage column.
        pane_boxes = {}
        for pane in ("#paneSubtitles", "#paneChat"):
            box = await page.locator(pane).bounding_box()
            pane_boxes[pane] = box
            assert abs(box["y"] - stage_box["y"]) < 1, (box, stage_box)
            # The stage column also carries the NOW panel, so the side panes are
            # taller than the video area itself -- that is the prototype layout.
            assert box["height"] > stage_box["height"], (box, stage_box)
            assert abs(box["width"] - 320) < 1, box
        assert abs(pane_boxes["#paneSubtitles"]["width"] - pane_boxes["#paneChat"]["width"]) < 1, pane_boxes
        assert stage_box["width"] > pane_boxes["#paneSubtitles"]["width"], (stage_box, pane_boxes)
        assert stage_box["width"] >= lower_deck_box["width"] * 0.75, (stage_box, lower_deck_box)

        assert await page.locator("#returnLive").count() == 0
        assert await page.locator("#playPause svg").count() == 2
        assert await page.locator("#muteToggle svg").count() == 2
        assert await page.locator("#toggleFullscreen svg").count() == 2
        # The fullscreen control sits in the right-hand cluster of the control
        # bar (prototype), i.e. the lower-right corner of the video area -- not
        # floating in the top-right as it did before the redesign.
        assert await page.locator(".ctl-cluster #toggleFullscreen").count() == 1
        fullscreen_box = await page.locator("#toggleFullscreen").bounding_box()
        assert fullscreen_box
        assert fullscreen_box["y"] > stage_box["y"] + stage_box["height"] / 2, (fullscreen_box, stage_box)
        assert fullscreen_box["x"] > stage_box["x"] + stage_box["width"] * 0.75, (fullscreen_box, stage_box)

        playback = await page.evaluate("""async () => {
            const video = document.getElementById('video');
            let paused = false;
            let playCalls = 0;
            let pauseCalls = 0;
            Object.defineProperty(video, 'paused', { configurable: true, get: () => paused });
            video.play = async () => {
                playCalls += 1;
                paused = false;
                video.dispatchEvent(new Event('playing'));
            };
            video.pause = () => {
                pauseCalls += 1;
                paused = true;
                video.dispatchEvent(new Event('pause'));
            };
            document.getElementById('playPause').click();
            const afterPause = { paused, playCalls, pauseCalls };
            document.getElementById('playPause').click();
            await Promise.resolve();
            return { afterPause, afterPlay: { paused, playCalls, pauseCalls } };
        }""")
        assert playback == {
            "afterPause": {"paused": True, "playCalls": 0, "pauseCalls": 1},
            "afterPlay": {"paused": False, "playCalls": 1, "pauseCalls": 1},
        }, playback

        await page.locator(".player-stage").evaluate("node => { Object.defineProperty(document.querySelector('#video'), 'paused', { configurable: true, value: false }); node.classList.remove('is-paused'); node.dispatchEvent(new PointerEvent('pointermove', { bubbles: true })); }")
        assert await page.locator(".player-stage").evaluate("node => node.classList.contains('controls-visible')")
        await page.wait_for_timeout(1800)
        assert not await page.locator(".player-stage").evaluate("node => node.classList.contains('controls-visible')")

        assert await page.locator("#subtitleLayer .subtitle-cue-row").count() == 2
        assert await page.locator("#subtitlesTimelineList .timeline-row").count() == 2
        subtitle_colors = await page.locator("#subtitleLayer .subtitle-cue-row").evaluate_all("rows => rows.map(row => getComputedStyle(row).getPropertyValue('--speaker-color').trim())")
        assert len(set(subtitle_colors)) == 2, subtitle_colors
        stable_render = await page.evaluate("""async () => {
            const overlay = document.querySelector('#subtitleLayer');
            const timeline = document.querySelector('#subtitlesTimelineList');
            let overlayMutations = 0;
            let timelineMutations = 0;
            const overlayObserver = new MutationObserver((records) => { overlayMutations += records.length; });
            const timelineObserver = new MutationObserver((records) => { timelineMutations += records.length; });
            overlayObserver.observe(overlay, { subtree: true, childList: true, characterData: true, attributes: true });
            timelineObserver.observe(timeline, { subtree: true, childList: true, characterData: true, attributes: true });
            for (let index = 0; index < 20; index += 1) {
                window.__lingerlensRenderSubtitle();
                window.__lingerlensRenderTimelines();
            }
            await new Promise((resolve) => setTimeout(resolve, 0));
            overlayObserver.disconnect();
            timelineObserver.disconnect();
            return { overlayMutations, timelineMutations };
        }""")
        assert stable_render == {"overlayMutations": 0, "timelineMutations": 0}, stable_render

        immediate_offset = await page.evaluate("""() => {
            const cueMap = window.__lingerlensSubtitleCues;
            cueMap.clear();
            window.__lingerlensSetSubtitleTestWallTime(1_700_000_100);
            cueMap.set(201, {
                id: 201, seq: 1, tStart: 1_700_000_100.5,
                tEnd: 1_700_000_102, hold: 1, state: 'done',
                src: '偏移即时生效', zh: 'Immediate offset', lang: 'zh', speaker: 'A',
            });
            window.__lingerlensRenderSubtitle();
            const before = document.querySelectorAll('#subtitleLayer .subtitle-cue-row').length;
            const slider = document.getElementById('subtitleOffset');
            slider.value = '0.6';
            slider.dispatchEvent(new Event('input', { bubbles: true }));
            const after = document.querySelectorAll('#subtitleLayer .subtitle-cue-row').length;
            const label = document.getElementById('subtitleOffsetValue').textContent;
            slider.value = '0';
            slider.dispatchEvent(new Event('input', { bubbles: true }));
            return { before, after, label };
        }""")
        assert immediate_offset == {"before": 0, "after": 1, "label": "0.6s"}, immediate_offset

        chat_title_box = await page.locator("#paneChat .pane-title").bounding_box()
        chat_controls_box = await page.locator("#paneChat .pane-controls").bounding_box()
        chat_head_box = await page.locator("#paneChat .pane-head").bounding_box()
        assert chat_title_box and chat_controls_box and chat_head_box
        # The prototype gives every pane a single 52px header row: title on the
        # left, controls flushed right on the same line, both inside the row.
        assert abs(chat_head_box["height"] - 52) < 1, chat_head_box
        assert chat_controls_box["x"] > chat_title_box["x"], (chat_title_box, chat_controls_box)
        for box in (chat_title_box, chat_controls_box):
            assert box["y"] >= chat_head_box["y"] - 1, (box, chat_head_box)
            assert box["y"] + box["height"] <= chat_head_box["y"] + chat_head_box["height"] + 1, (box, chat_head_box)
        smoke = await page.evaluate("""() => ({
            wall: window.__smokeClock?.playingWallTime(),
            rows: window.__smokeRows?.rows?.length,
            html: window.__smokeRenderedHtml,
        })""")
        assert smoke["wall"] == 1_700_000_001, smoke
        assert smoke["rows"] == 1, smoke
        assert "SmokeUser" in smoke["html"], smoke

        toggle = page.locator("#chatTranslateToggle")
        assert not await toggle.is_checked()
        await toggle.click()
        await page.wait_for_timeout(200)
        assert companion.message_translate_enabled is True
        assert companion.message_translator is fake_translator
        assert fake_translator.enabled is True

        control_state = {"state": "idle"}
        stop_confirmed = asyncio.Event()

        async def status_route(route):
            state = control_state["state"]
            payload = {
                "state": state,
                "error": "simulated FFmpeg failure" if state == "error" else None,
                "playlistReady": False,
                "playlistUrl": None,
                "hiddenMediaSeconds": 0,
                "sourceDelaySeconds": 0,
                "targetDelaySeconds": 15,
                "uptimeSeconds": 0,
                "subtitles": {},
            }
            await route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

        async def start_route(route):
            await asyncio.sleep(1.0)
            control_state["state"] = "running"
            payload = {
                "ok": True,
                "quality": {"width": 1280, "height": 720, "fps": 30},
                "status": {"state": "running"},
            }
            await route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

        async def stop_route(route):
            await asyncio.sleep(1.0)
            control_state["state"] = "idle"
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"ok": True, "status": {"state": "idle"}}),
            )
            stop_confirmed.set()

        await page.route("**/api/status", status_route)
        await page.route("**/api/start", start_route)
        await page.route("**/api/stop", stop_route)
        start_feedback = await page.evaluate("""() => {
            const quality = document.getElementById('quality');
            quality.disabled = false;
            document.getElementById('url').value = 'https://example.com/live';
            document.getElementById('subtitlesEnabled').checked = false;
            const start = document.getElementById('start');
            start.disabled = false;
            start.click();
            return {
                busy: start.getAttribute('aria-busy'),
                label: start.querySelector('.button-label').textContent,
                loading: !document.getElementById('mediaLoading').hidden,
            };
        }""")
        assert start_feedback == {"busy": "true", "label": "正在启动", "loading": True}, start_feedback
        await page.wait_for_function("!document.getElementById('start').hasAttribute('aria-busy')")

        control_state["state"] = "error"
        await page.wait_for_function("!document.getElementById('stop').disabled", timeout=2500)
        stop_feedback = await page.evaluate("""() => {
            const stop = document.getElementById('stop');
            stop.click();
            const video = document.getElementById('video');
            video.dispatchEvent(new Event('playing'));
            video.dispatchEvent(new Event('waiting'));
            return {
                busy: stop.getAttribute('aria-busy'),
                label: stop.querySelector('.button-label').textContent,
                loadingHidden: document.getElementById('mediaLoading').hidden,
                message: document.getElementById('message').textContent,
                sessionActive: document.querySelector('.player-stage').classList.contains('session-active'),
            };
        }""")
        # Clicking Stop is a local action now: the click handler itself must have
        # already torn the session down, while /api/stop (deliberately 1s slow
        # here) is still in flight. It used to await that round trip, so the
        # button read "正在停止" and the spinner stayed up for its whole duration.
        assert stop_feedback["busy"] is None, stop_feedback
        assert stop_feedback["label"] == "停止", stop_feedback
        assert stop_feedback["loadingHidden"] is True, stop_feedback
        assert stop_feedback["message"] == "已停止当前直播。", stop_feedback
        assert stop_feedback["sessionActive"] is False, stop_feedback
        await page.wait_for_function("!document.getElementById('stop').hasAttribute('aria-busy')")
        assert await page.locator("#stop").is_disabled()
        # Keep the immediate assertion above; then drain the owned route.
        await asyncio.wait_for(stop_confirmed.wait(), timeout=5)
        assert not errors_seen, errors_seen
        await browser.close()

    await runner.cleanup()
    temporary.cleanup()
    print("Companion production-routing browser smoke passed")


if __name__ == "__main__":
    asyncio.run(run())
