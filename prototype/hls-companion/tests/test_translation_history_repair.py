from __future__ import annotations
import asyncio
import unittest
from test_subtitle_pipeline import FakeASR, RecordingTranslation
from companion.subtitle_pipeline import SubtitlePipeline
from companion.subtitle_store import CueStore
from companion.providers.base import TranslationResult, StreamMeta

class HistoryRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_is_retried_and_updates_the_same_history_cue(self):
        class TimeoutOnce(RecordingTranslation):
            async def translate(self, request):
                self.requests.append(request)
                if len(self.requests) == 1:
                    raise asyncio.TimeoutError('temporary timeout')
                return TranslationResult('补齐的翻译', self.id, 1)
        provider = TimeoutOnce('repair')
        store = CueStore()
        pipeline = SubtitlePipeline(asr_provider=FakeASR(), translation_provider=provider,
            cue_store=store, meta=StreamMeta("test", "test", "", "en", "zh-Hans"), translation_timeout_seconds=0.05)
        cue = store.add(t_start=1, t_end=2, hold=1, src='original', lang='en', timing_source='asr')
        pipeline._running = True
        pipeline._history_retry_delays = (0.01, 0.02, 0.03)
        pipeline._enqueue_translation(cue)
        worker = asyncio.create_task(pipeline._translation_worker())
        try:
            await asyncio.wait_for(pipeline._translation_queue.join(), 1)
            for _ in range(30):
                if store.get(cue.id).zh: break
                await asyncio.sleep(0.01)
            self.assertEqual(store.get(cue.id).zh, '补齐的翻译')
            self.assertEqual(len(store), 1)
            self.assertGreater(store.get(cue.id).revision, 1)
        finally:
            pipeline._running = False
            for task in [worker, *pipeline._tasks]: task.cancel()
            await asyncio.gather(worker, *pipeline._tasks, return_exceptions=True)


    def make_pipeline(self, provider, *, store=None):
        pipeline = SubtitlePipeline(asr_provider=FakeASR(), translation_provider=provider,
            cue_store=store if store is not None else CueStore(),
            meta=StreamMeta("test", "test", "", "en", "zh-Hans"), translation_timeout_seconds=.05)
        pipeline._history_retry_delays = (.01, .02, .03)
        pipeline._running = True
        self.addAsyncCleanup(self.cleanup, pipeline)
        worker = asyncio.create_task(pipeline._translation_worker())
        pipeline._tasks.append(worker)
        return pipeline

    async def cleanup(self, pipeline):
        pipeline._running = False
        for task in pipeline._tasks: task.cancel()
        await asyncio.gather(*pipeline._tasks, return_exceptions=True)

    def enqueue(self, pipeline, text="original", order=1):
        cue = pipeline.store.add(t_start=order, t_end=order+1, hold=1, src=text,
            lang="en", timing_source="asr", generation=1, chunk_order=order)
        pipeline._enqueue_translation(cue)
        return cue

    async def wait_until(self, predicate):
        for _ in range(100):
            if predicate(): return
            await asyncio.sleep(.01)
        self.fail("background translation did not reach the expected state")

    async def test_temporary_failures_have_bounded_retries(self):
        class AlwaysTimeout(RecordingTranslation):
            async def translate(self, request):
                self.requests.append(request)
                raise asyncio.TimeoutError()
        provider = AlwaysTimeout("timeout")
        pipeline = self.make_pipeline(provider)
        cue = self.enqueue(pipeline)
        await self.wait_until(lambda: pipeline.stats.history_translation_exhausted == 1)
        self.assertEqual(len(provider.requests), 4)
        self.assertEqual(pipeline.store.get(cue.id).state, "failed")
        self.assertEqual(len(pipeline._history_repairs), 0)

    async def test_authentication_errors_are_not_retried(self):
        from companion.providers.base import ProviderAuthError
        class AuthFailure(RecordingTranslation):
            async def translate(self, request):
                self.requests.append(request)
                raise ProviderAuthError("bad credentials")
        provider = AuthFailure("auth")
        pipeline = self.make_pipeline(provider)
        self.enqueue(pipeline)
        await asyncio.wait_for(pipeline._translation_queue.join(), 1)
        await asyncio.sleep(.05)
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(len(pipeline._history_repairs), 0)

    async def test_background_retry_does_not_block_fresh_subtitles_and_uses_new_budget(self):
        gate = asyncio.Event()
        started = asyncio.Event()
        class SlowRepair(RecordingTranslation):
            async def translate(self, request):
                self.requests.append(request)
                if request.source_text == "old":
                    if len(self.requests) == 1: raise asyncio.TimeoutError()
                    started.set()
                    await gate.wait()
                return TranslationResult("translated " + request.source_text, self.id, 1)
        provider = SlowRepair("repair")
        pipeline = self.make_pipeline(provider)
        old = self.enqueue(pipeline, "old")
        await asyncio.wait_for(started.wait(), 1)
        fresh = self.enqueue(pipeline, "fresh", 2)
        await self.wait_until(lambda: fresh.zh is not None)
        self.assertIsNone(old.zh)
        self.assertGreater(provider.requests[1].deadline_monotonic - provider.requests[0].deadline_monotonic, 20)
        gate.set()
        await self.wait_until(lambda: old.zh is not None)
        self.assertEqual([cue.zh for cue in pipeline.store.query()], ["translated fresh", "translated old"])

    async def test_retry_cannot_publish_after_target_language_changes(self):
        gate = asyncio.Event()
        started = asyncio.Event()
        class SlowRepair(RecordingTranslation):
            async def translate(self, request):
                self.requests.append(request)
                if len(self.requests) == 1: raise asyncio.TimeoutError()
                started.set(); await gate.wait()
                return TranslationResult("old target", self.id, 1)
        provider = SlowRepair("repair")
        pipeline = self.make_pipeline(provider)
        cue = self.enqueue(pipeline)
        await asyncio.wait_for(started.wait(), 1)
        pipeline.update_target_language("de")
        gate.set()
        await asyncio.sleep(.05)
        self.assertIsNone(cue.zh)

    async def test_evicted_cues_are_not_sent_to_the_provider_again(self):
        class Timeout(RecordingTranslation):
            async def translate(self, request):
                self.requests.append(request); raise asyncio.TimeoutError()
        provider = Timeout("repair")
        pipeline = self.make_pipeline(provider, store=CueStore(max_cues=1))
        pipeline._history_retry_delays = (.2, .2, .2)
        old = self.enqueue(pipeline)
        await asyncio.wait_for(pipeline._translation_queue.join(), 1)
        pipeline.store.add(t_start=10, t_end=11, hold=1, src="fresh", lang="en", timing_source="asr")
        await asyncio.sleep(.25)
        self.assertIsNone(pipeline.store.get(old.id))
        self.assertEqual(len(provider.requests), 1)

    async def test_expired_queue_budget_can_be_repaired_for_history(self):
        from companion.translation_budget import TranslationBudget
        provider = RecordingTranslation("repair")
        pipeline = self.make_pipeline(provider)
        cue = self.enqueue(pipeline)
        pipeline._translation_budgets[cue.id] = TranslationBudget(pipeline.monotonic()-1, .05, None)
        await self.wait_until(lambda: cue.zh is not None)
        self.assertEqual(pipeline.stats.translation_deadline_expired, 1)
        self.assertEqual(pipeline.stats.history_translation_repaired, 1)
        self.assertEqual(len(provider.requests), 1)

if __name__ == "__main__":
    unittest.main()
