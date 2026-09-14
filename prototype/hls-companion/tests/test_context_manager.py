from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("laglingo_context_manager", ROOT / "companion" / "context_manager.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class RollingContextTests(unittest.TestCase):
    def test_pending_source_is_usable_then_upgraded_without_mutating_old_request(self) -> None:
        context = MODULE.RollingContext()
        context.add("前文", None, generation=1, chunk_order=1, media_t_end=2)
        self.assertFalse(context.contains(generation=1, chunk_order=1))
        snapshot = context.history(generation=1, before_order=2, at_media_time=3, include_untranslated=True)
        self.assertEqual(snapshot, [("前文", None)])
        self.assertEqual(context.history(generation=1, before_order=2, at_media_time=3), [])
        context.add("前文", "previous", generation=1, chunk_order=1, media_t_end=2)
        self.assertEqual(snapshot, [("前文", None)])
        self.assertEqual(context.history(generation=1, before_order=2, at_media_time=3, include_untranslated=True), [("前文", "previous")])
        self.assertFalse(context.add("前文", None, generation=1, chunk_order=1, media_t_end=2))
        self.assertTrue(context.contains(generation=1, chunk_order=1))

    def test_source_only_context_excludes_future_and_is_limited_and_expired(self) -> None:
        context = MODULE.RollingContext(context_pairs=2, context_seconds=3)
        for order in range(1, 7):
            context.add(str(order), None, generation=1, chunk_order=order, media_t_end=order)
        context.add("other", None, generation=2, chunk_order=1, media_t_end=4)
        self.assertEqual(context.history(generation=1, before_order=5, at_media_time=5, include_untranslated=True), [("3", None), ("4", None)])
        context.trim(generation=1, at_media_time=7)
        self.assertEqual(context.orders(1), (4, 5, 6))

    def test_completion_order_is_reassembled_in_source_order(self) -> None:
        context = MODULE.RollingContext(context_pairs=6, context_seconds=90)
        context.add("s3", "z3", generation=7, chunk_order=3, media_t_end=13.0)
        context.add("s1", "z1", generation=7, chunk_order=1, media_t_end=5.0)
        context.add("s2", "z2", generation=7, chunk_order=2, media_t_end=9.0)
        self.assertEqual(
            context.history(generation=7, before_order=4, at_media_time=14.0),
            [("s1", "z1"), ("s2", "z2"), ("s3", "z3")],
        )

    def test_excludes_current_future_other_generation_and_media_future(self) -> None:
        context = MODULE.RollingContext(context_pairs=10, context_seconds=90)
        context.add("previous", "prev", generation=1, chunk_order=1, media_t_end=5.0)
        context.add("current", "now", generation=1, chunk_order=2, media_t_end=8.0)
        context.add("future-order", "later", generation=1, chunk_order=3, media_t_end=7.0)
        context.add("future-time", "clock", generation=1, chunk_order=4, media_t_end=10.0)
        context.add("old-generation", "old", generation=0, chunk_order=1, media_t_end=4.0)
        self.assertEqual(
            context.history(generation=1, before_order=2, at_media_time=8.0),
            [("previous", "prev")],
        )

    def test_context_seconds_uses_media_time_not_translation_completion_time(self) -> None:
        context = MODULE.RollingContext(context_pairs=6, context_seconds=10)
        # Added in the reverse wall/completion order on purpose. Only media time matters.
        context.add("too-old", "old", generation=1, chunk_order=1, media_t_end=9.9)
        context.add("kept", "new", generation=1, chunk_order=2, media_t_end=10.0)
        self.assertEqual(
            context.history(generation=1, before_order=3, at_media_time=20.0),
            [("kept", "new")],
        )
        context.trim(generation=1, at_media_time=20.0)
        self.assertEqual(context.orders(1), (2,))

    def test_pair_limit_keeps_most_recent_source_order_pairs(self) -> None:
        context = MODULE.RollingContext(context_pairs=4, context_seconds=90)
        for order in range(1, 7):
            context.add(f"s{order}", f"z{order}", generation=3, chunk_order=order, media_t_end=float(order))
        self.assertEqual(
            context.history(generation=3, before_order=7, at_media_time=7.0),
            [("s3", "z3"), ("s4", "z4"), ("s5", "z5"), ("s6", "z6")],
        )
        self.assertEqual(
            context.history(generation=3, before_order=7, at_media_time=7.0, pair_limit=2),
            [("s5", "z5"), ("s6", "z6")],
        )

    def test_duplicate_generation_order_is_rejected_even_if_text_differs(self) -> None:
        context = MODULE.RollingContext()
        context.add("source", "translation", generation=4, chunk_order=2, media_t_end=3.0)
        with self.assertRaisesRegex(ValueError, "duplicate context chunk order"):
            context.add("other", "different", generation=4, chunk_order=2, media_t_end=4.0)
        # The same order is valid in a new generation.
        context.add("new", "generation", generation=5, chunk_order=2, media_t_end=1.0)

    def test_contains_is_generation_local(self) -> None:
        context = MODULE.RollingContext()
        context.add("source", "translation", generation=4, chunk_order=2, media_t_end=3.0)
        self.assertTrue(context.contains(generation=4, chunk_order=2))
        self.assertFalse(context.contains(generation=5, chunk_order=2))

    def test_legacy_metadata_free_callers_do_not_enter_caption_history(self) -> None:
        context = MODULE.RollingContext()
        self.assertFalse(context.add("live message", "弹幕", media_t_end=3.0))
        self.assertEqual(context.history(generation=1, before_order=2, at_media_time=4.0), [])

    def test_context_manager_has_no_private_language_dictionary(self) -> None:
        self.assertFalse(hasattr(MODULE, "_LANGUAGE_NAMES"))
        self.assertFalse(hasattr(MODULE, "build_prompt"))


if __name__ == "__main__":
    unittest.main()
