from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.providers.base import (
    ASRCapabilities,
    ASREvent,
    CaptionObservation,
    RecognitionToken,
)


class RecognitionContractTests(unittest.TestCase):
    def test_contracts_are_frozen_and_defaults_preserve_old_constructors(self) -> None:
        token = RecognitionToken("hello", 0.1, 0.4, True)
        observation = CaptionObservation("stable_token_delta", 7, "item", tokens=(token,))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            token.text = "changed"  # type: ignore[misc]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            observation.item_id = "changed"  # type: ignore[misc]

        capabilities = ASRCapabilities(True, True, False, True, False, False, False, (), (16000,))
        self.assertEqual(capabilities.caption_evidence, frozenset({"utterance_final"}))
        event = ASREvent("final", "legacy text")
        self.assertEqual(event.text, "legacy text")
        self.assertIsNone(event.caption_observation)


if __name__ == "__main__":
    unittest.main()
