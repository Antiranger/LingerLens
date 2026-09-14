from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.caption_chunker import CaptionChunker
from companion.context_manager import RollingContext
from companion.providers.base import CaptionObservation, RecognitionToken

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ticket03_translation_continuity.json"


class Ticket03AcceptanceFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_multilingual_caption_and_continuity_fixtures_are_deterministic(self) -> None:
        names = set()
        for case in self.fixture["captionCases"]:
            names.add(case["name"])
            with self.subTest(case=case["name"]):
                chunker = CaptionChunker()
                tokens = tuple(
                    RecognitionToken(text, begin, end, True, language=case["language"])
                    for text, begin, end in case["tokens"]
                )
                chunks = list(chunker.observe(CaptionObservation(
                    "stable_token_delta", 1, case["name"], tokens=tokens,
                )).chunks)
                chunks.extend(chunker.flush_utterance(case["name"]).chunks)
                self.assertEqual([chunk.text for chunk in chunks], case["expectedChunks"])
                self.assertEqual(chunks[0].begin_pcm, tokens[0].begin_pcm)
                self.assertEqual(chunks[-1].end_pcm, tokens[-1].end_pcm)
                self.assertTrue(all(chunk.exact_timing for chunk in chunks))
                self.assertEqual(
                    "".join("".join(chunk.text.split()) for chunk in chunks),
                    "".join("".join(token.text.split()) for token in tokens),
                )
                self.assertEqual(
                    [chunk.starts_mid_sentence for chunk in chunks],
                    [item.get("startsMidSentence", False) for item in case["continuity"]],
                )
                self.assertEqual(
                    [chunk.ends_mid_sentence for chunk in chunks],
                    [
                        item.get("endsMidSentence", False)
                        if chunk.cut_reason == "terminal_punctuation"
                        else item.get("endsMidSentence")
                        for item, chunk in zip(case["continuity"], chunks)
                    ],
                )
        self.assertEqual(
            names,
            {
                "english-run-on", "spanish-run-on", "chinese-no-spaces",
                "japanese-separated-punctuation", "korean-no-spaces-required",
                "mixed-universal-atomic-tokens",
            },
        )

    def test_translation_completion_order_fixture_reassembles_source_order(self) -> None:
        case = self.fixture["completionOrder"]
        context = RollingContext(context_pairs=6, context_seconds=90)
        for order in case["completed"]:
            context.add(
                f"s{order}", f"z{order}",
                generation=case["generation"],
                chunk_order=order,
                media_t_end=case["mediaEnds"][str(order)],
            )
        history = context.history(
            generation=case["generation"],
            before_order=case["beforeOrder"],
            at_media_time=case["atMediaTime"],
        )
        self.assertEqual([int(source[1:]) for source, _ in history], case["expectedSourceOrder"])


if __name__ == "__main__":
    unittest.main()
