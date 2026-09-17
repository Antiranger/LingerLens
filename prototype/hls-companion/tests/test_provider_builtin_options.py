"""C3 (backend half): the builtin template and the shipped example stop writing a dead key.

`hotwordsEnabled` was written by `BUILTIN_ASR_PROVIDERS` and by
`runtime/providers.example.json` and read by nothing: the real hotword path is
`vocabulary` / `vocabularyId`, which `asr_dashscope_task.py` checks and
`tests/test_dashscope_task_asr.py` covers (instant-vocabulary passthrough, and
`hotwords` deliberately not sent).

It was not converted into either of those. A boolean cannot be turned into a word
list or a cloud resource id: any automatic replacement would invent a semantic
that nobody chose. It was deleted, and the neighbouring keys are pinned here so
that "one key was removed" stays a claim someone can check.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.providers.config import BUILTIN_ASR_PROVIDERS

EXAMPLE = ROOT / "runtime" / "providers.example.json"

# The Fun-ASR profile is the one that carried the key. The list is written out so
# that a removal cannot quietly take a neighbour with it.
FUN_ASR_OPTIONS = (
    "sampleRate",
    "languages",
    "semanticPunctuationEnabled",
    "maxSentenceSilence",
    "multiThresholdModeEnabled",
    "heartbeat",
    "contextEnabled",
    "startTimeoutSeconds",
)


class BuiltinOptionTests(unittest.TestCase):
    def test_no_builtin_provider_template_writes_a_dead_hotword_flag(self) -> None:
        """D: the key is gone from every builtin, not only the one that had it."""
        offenders = [
            provider["id"]
            for provider in BUILTIN_ASR_PROVIDERS
            if "hotwordsEnabled" in (provider.get("options") or {})
        ]
        self.assertEqual(offenders, [])

    def test_the_fun_asr_template_lost_exactly_one_key(self) -> None:
        """G: the neighbours of the removed key are untouched."""
        provider = next(
            item for item in BUILTIN_ASR_PROVIDERS if item["id"] == "bailian-fun-asr-2026-02-28"
        )
        self.assertEqual(tuple(provider["options"].keys()), FUN_ASR_OPTIONS)

    def test_the_shipped_example_no_longer_contains_the_key(self) -> None:
        """D: a new user's file is not seeded with it either."""
        text = EXAMPLE.read_text(encoding="utf-8")
        self.assertNotIn("hotwordsEnabled", text)
        # Still valid JSON, and the profile that had it is otherwise intact.
        document = json.loads(text)
        providers = document["asr"]["providers"] if "asr" in document else document["providers"]
        fun_asr = next(item for item in providers if item.get("id") == "bailian-fun-asr-2026-02-28")
        self.assertNotIn("hotwordsEnabled", fun_asr.get("options", {}))
        self.assertEqual(fun_asr["options"]["sampleRate"], 16000)
        self.assertTrue(fun_asr["options"]["contextEnabled"] is False)


if __name__ == "__main__":
    unittest.main()
