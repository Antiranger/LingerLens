"""Resource and correspondence regressions for the real native translation bus."""
import asyncio
import time
import unittest

from companion.providers.native_session import NativeTranslationBus


class NativeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_segments_are_bounded_even_without_interim_updates(self):
        for translated in (False, True):
            bus = NativeTranslationBus()
            for index in range(1000):
                key = str(index)
                bus.close_item(key, source_text="hello", translation="你好" if translated else "")
                await bus.resolve(item_id=key, source_text="hello", deadline_monotonic=None)
            self.assertLessEqual(len(bus._segments), 256)

    async def test_unconsumed_and_open_segments_are_also_bounded(self):
        bus = NativeTranslationBus()
        for index in range(1000):
            bus.record(item_id=str(index), source_text="hello", translation="")
        self.assertLessEqual(len(bus._segments), 256)

    async def test_closed_segment_cannot_give_whole_translation_to_one_fragment(self):
        bus = NativeTranslationBus()
        bus.close_item("1", source_text="私は行きません。", translation="I will not go.")
        result = await bus.resolve(item_id="1", source_text="私は", deadline_monotonic=None)
        self.assertIsNone(result, "a character ratio cannot establish semantic correspondence")
        whole = await bus.resolve(item_id="1", source_text="私は行きません。", deadline_monotonic=None)
        self.assertEqual(whole, "I will not go.")

    async def test_an_explicit_missing_id_never_claims_another_segments_text(self):
        bus = NativeTranslationBus()
        bus.close_item("new", source_text="hello", translation="new translation")
        result = await bus.resolve(item_id="old", source_text="hello", deadline_monotonic=time.monotonic() + .01)
        self.assertIsNone(result)

    async def test_waiter_does_not_cross_a_reset_with_reused_item_ids(self):
        bus = NativeTranslationBus()
        bus.record(item_id="1", source_text="hello", translation="")
        waiter = asyncio.create_task(bus.resolve(item_id="1", source_text="hello", deadline_monotonic=time.monotonic() + 1))
        await asyncio.sleep(0)
        bus.reset()
        bus.close_item("1", source_text="hello", translation="new session")
        self.assertIsNone(await waiter)

    async def test_request_queued_before_reset_cannot_claim_new_session(self):
        bus = NativeTranslationBus()
        bus.reset(1)
        bus.close_item("1", source_text="hello", translation="old")
        bus.reset(2)
        bus.close_item("1", source_text="hello", translation="new")
        self.assertIsNone(await bus.resolve(item_id="1", source_text="hello", deadline_monotonic=None, generation=1))
        self.assertEqual(await bus.resolve(item_id="1", source_text="hello", deadline_monotonic=None, generation=2), "new")

    async def test_translation_only_update_preserves_the_source_join(self):
        bus = NativeTranslationBus()
        bus.record(item_id="1", source_text="hello", translation="你")
        bus.record(item_id="1", source_text="", translation="你好")
        bus.close_item("1")
        self.assertEqual(await bus.resolve(item_id="1", source_text="hello", deadline_monotonic=None), "你好")

    async def test_active_waiter_survives_many_completed_unrelated_segments(self):
        bus = NativeTranslationBus()
        bus.record(item_id="waiting", source_text="hello", translation="")
        waiting = asyncio.create_task(bus.resolve(item_id="waiting", source_text="hello", deadline_monotonic=time.monotonic() + 2))
        await asyncio.sleep(0)
        for index in range(1000):
            bus.close_item(str(index), source_text="other", translation="")
        bus.close_item("waiting", translation="你好")
        self.assertEqual(await waiting, "你好")
        self.assertLessEqual(len(bus._segments), 256)


if __name__ == "__main__":
    unittest.main()
