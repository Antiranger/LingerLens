from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("laglingo_subtitle_store", ROOT / "companion" / "subtitle_store.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CueStoreTests(unittest.TestCase):
    def make_store(self):
        return MODULE.CueStore(retention_seconds=120)

    def add(self, store, t_end, src="src"):
        return store.add(t_start=t_end - 1, t_end=t_end, hold=1.2, src=src, lang="ja", timing_source="vad")

    def test_assigns_ids_seq_serializes_api_shape_and_updates_revision(self) -> None:
        store = self.make_store()
        cue = self.add(store, 100)
        self.assertEqual(cue.id, 1)
        self.assertEqual(cue.seq, 1)
        self.assertEqual(cue.revision, 1)
        updated = store.update(cue.id, zh="译文", state="done", hold=2.4)
        self.assertIs(updated, cue)
        self.assertEqual(updated.seq, 2)
        self.assertEqual(updated.revision, 2)
        self.assertEqual(store.max_seq, 2)
        self.assertEqual(updated.to_dict()["tEnd"], 100)
        self.assertEqual(updated.to_dict()["timingSource"], "vad")
        self.assertEqual(updated.to_dict()["seq"], 2)

    def test_query_uses_global_monotonic_seq_for_adds_and_updates(self) -> None:
        store = self.make_store()
        old = self.add(store, 100, "old")       # seq 1
        recent = self.add(store, 130, "recent") # seq 2
        store.update(old.id, zh="late translation", state="done")  # seq 3
        result = store.query(after_seq=1)
        self.assertEqual([(cue.id, cue.seq) for cue in result], [(recent.id, 2), (old.id, 3)])
        self.assertEqual(store.query(after_seq=3), [])

    def test_prunes_against_newest_cue_timeline_not_wall_clock(self) -> None:
        store = self.make_store()
        expired = self.add(store, 10)
        retained = self.add(store, 11)
        newest = self.add(store, 130)
        result = store.query()
        self.assertEqual([cue.id for cue in result], [retained.id, newest.id])
        self.assertIsNone(store.get(expired.id))
        self.assertEqual(len(store), 2)

    def test_rejects_unknown_or_cursor_updates_and_missing_ids(self) -> None:
        store = self.make_store()
        cue = self.add(store, 100)
        for field in ("id", "seq", "revision"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                store.update(cue.id, **{field: 99})
        with self.assertRaises(KeyError):
            store.update(999, state="done")


if __name__ == "__main__":
    unittest.main()
