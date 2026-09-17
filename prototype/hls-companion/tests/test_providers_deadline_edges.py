"""B2-R: the chain checks its own deadline per attempt, and local skips are not failures.

Two separate things are pinned here.

The first is WHEN the total budget stops new provider calls. The pipeline checks
the cue deadline at its own boundaries, but those checks describe the moment they
run: a primary call awaits, and by the time the fallback's turn comes the budget
can be gone. The chain therefore has to check the ORIGINAL deadline before every
attempt, and it has to keep that distinct from the primary's LOCAL deadline of
``D - reserve`` -- that one expiring is exactly what the reserve is for, and the
fallback must still get its turn.

The second is WHAT a skip is. Skipping the primary to protect the fallback's
share, or skipping a fallback that is cooling, disabled, too small for the cue,
or declared unable to translate the pair, are all decisions the chain makes
locally. None of them is evidence about a provider, so none of them may touch
health, and none may be reported as a provider failure.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.providers.base import (
    ProviderRateLimitError,
    StreamMeta,
    TranslationCapabilities,
    TranslationLanguageCapabilities,
    TranslationProvider,
    TranslationRequest,
    TranslationResult,
)
from companion.providers.fallback import FallbackChain


class Clock:
    def __init__(self, now: float = 10.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_request(
    *,
    source: str = "ja",
    target: str = "zh",
    text: str = "こんにちは",
    deadline: float | None = None,
) -> TranslationRequest:
    return TranslationRequest(
        source_text=text,
        meta=StreamMeta("title", "channel", "gaming", source, target),
        history=[],
        glossary=[],
        deadline_monotonic=deadline,
    )


class RecordingTranslation(TranslationProvider):
    """Records the deadline each call received, and can move the clock while it runs."""

    def __init__(
        self,
        provider_id: str,
        script: Any = (),
        *,
        clock: Clock | None = None,
        arrive_at: float | None = None,
        limit: int = 10000,
        language: TranslationLanguageCapabilities | None = None,
    ) -> None:
        self.id = provider_id
        self.label = provider_id
        self.model = provider_id
        self.script = list(script)
        self.clock = clock
        self.arrive_at = arrive_at
        self.limit = limit
        self.language = language or TranslationLanguageCapabilities()
        self.calls = 0
        self.deadlines: list[float | None] = []

    @property
    def capabilities(self) -> TranslationCapabilities:
        return TranslationCapabilities(True, True, True, False, self.limit, self.language)

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        self.calls += 1
        self.deadlines.append(request.deadline_monotonic)
        if self.arrive_at is not None and self.clock is not None:
            # Models a provider that returns after the cue budget has run out
            # while it was working.
            self.clock.now = self.arrive_at
        outcome = self.script.pop(0) if self.script else "ok"
        if isinstance(outcome, BaseException):
            raise outcome
        return TranslationResult(f"{self.id}:{request.source_text}", self.id, 1, None)


class DeadlinePerAttemptTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_provider_is_started_once_the_total_budget_is_gone(self) -> None:
        """D: an expired total budget stops the chain before any call."""
        for deadline in (10.0, 9.0):
            with self.subTest(deadline=deadline):
                clock = Clock(10.0)
                primary = RecordingTranslation("primary", clock=clock)
                fallback = RecordingTranslation("fallback", clock=clock)
                chain = FallbackChain(
                    [primary, fallback], fallback_reserve_seconds=2.0, clock=clock
                )
                with self.assertRaises(asyncio.TimeoutError):
                    await chain.translate(make_request(deadline=deadline))
                self.assertEqual(primary.calls, 0)
                self.assertEqual(fallback.calls, 0)
                for provider_id in ("primary", "fallback"):
                    state = chain.health[provider_id]
                    self.assertIsNone(state.disabled_reason)
                    self.assertEqual(state.consecutive_failures, 0)
                    self.assertEqual(state.cooldown_until, 0.0)

    async def test_a_single_provider_chain_also_refuses_an_expired_budget(self) -> None:
        """D: the check belongs to the chain, not to having a fallback."""
        clock = Clock(10.0)
        only = RecordingTranslation("only", clock=clock)
        chain = FallbackChain([only], clock=clock)
        with self.assertRaises(asyncio.TimeoutError):
            await chain.translate(make_request(deadline=9.999))
        self.assertEqual(only.calls, 0)

    async def test_the_pipeline_entry_check_is_not_a_guarantee_for_the_fallback(self) -> None:
        """D: the primary's local deadline expiring may not spend the fallback's turn."""
        clock = Clock(10.0)
        primary = RecordingTranslation(
            "primary", [asyncio.TimeoutError("slow")], clock=clock, arrive_at=16.001
        )
        fallback = RecordingTranslation("fallback", ["fb"], clock=clock)
        chain = FallbackChain(
            [primary, fallback], fallback_reserve_seconds=2.0, clock=clock
        )

        with self.assertRaises(asyncio.TimeoutError):
            await chain.translate(make_request(deadline=16.0))

        self.assertEqual(primary.deadlines, [14.0], "the primary keeps D - reserve")
        self.assertEqual(fallback.calls, 0, "an already-expired provider was called anyway")
        state = chain.health["fallback"]
        self.assertEqual(state.consecutive_failures, 0)
        self.assertEqual(state.cooldown_until, 0.0)
        self.assertIsNone(state.disabled_reason)

    async def test_a_fallback_whose_cooldown_ended_during_the_primary_call_is_tried(self) -> None:
        """D: the loop must re-read the clock; the old loop reused the stale one."""
        clock = Clock(10.0)
        primary = RecordingTranslation(
            "primary", [asyncio.TimeoutError("slow")], clock=clock, arrive_at=12.0
        )
        fallback = RecordingTranslation("fallback", ["fb"], clock=clock)
        chain = FallbackChain(
            [primary, fallback], fallback_reserve_seconds=2.0, clock=clock
        )
        chain.health["fallback"].cooldown_until = 11.0

        result = await chain.translate(make_request(deadline=16.0))

        self.assertEqual(result.provider_id, "fallback")
        self.assertEqual(fallback.calls, 1, "a recovered fallback was skipped on a stale clock")
        self.assertEqual(chain.health["fallback"].cooldown_until, 0.0)

    async def test_a_known_incompatible_fallback_reserves_nothing(self) -> None:
        """D: the primary keeps the whole budget when the fallback cannot answer."""
        clock = Clock(10.0)
        primary = RecordingTranslation("primary", ["primary"], clock=clock)
        fallback = RecordingTranslation(
            "fallback",
            ["fb"],
            clock=clock,
            language=TranslationLanguageCapabilities(supported_pairs=(("en", "zh"),)),
        )
        chain = FallbackChain(
            [primary, fallback], fallback_reserve_seconds=2.0, clock=clock
        )

        result = await chain.translate(make_request(source="ja", target="zh", deadline=11.0))

        self.assertEqual(result.provider_id, "primary")
        self.assertEqual(primary.deadlines, [11.0], "the primary was shortened for nothing")
        self.assertEqual(fallback.calls, 0)
        state = chain.health["fallback"]
        self.assertIsNone(state.disabled_reason, "a local language skip became a health change")
        self.assertEqual(state.consecutive_failures, 0)

    async def test_an_open_world_fallback_is_still_reserved_for(self) -> None:
        """G: unknown language ability stays eligible; nothing is probed or inferred."""
        clock = Clock(10.0)
        primary = RecordingTranslation("primary", [asyncio.TimeoutError("slow")], clock=clock)
        fallback = RecordingTranslation(
            "fallback", ["fb"], clock=clock,
            language=TranslationLanguageCapabilities(open_world_prompting=True),
        )
        chain = FallbackChain(
            [primary, fallback], fallback_reserve_seconds=2.0, clock=clock
        )

        result = await chain.translate(make_request(source="ja", target="zh", deadline=16.0))

        self.assertEqual(primary.deadlines, [14.0])
        self.assertEqual(result.provider_id, "fallback")

    async def test_the_final_message_says_who_was_not_called_and_who_failed(self) -> None:
        """D: a skip is not a failure, and it may not become the exception cause."""
        clock = Clock(10.0)
        primary = RecordingTranslation("primary", ["primary"], clock=clock)
        fallback = RecordingTranslation(
            "fallback", [asyncio.TimeoutError("slow")], clock=clock
        )
        chain = FallbackChain(
            [primary, fallback], fallback_reserve_seconds=2.0, clock=clock
        )

        with self.assertRaises(RuntimeError) as caught:
            await chain.translate(make_request(deadline=11.0))

        message = str(caught.exception)
        self.assertIn("fallback: TimeoutError: slow", message)
        self.assertIn("primary: not called", message)
        self.assertNotIn("primary: RuntimeError", message)
        self.assertIsInstance(caught.exception.__cause__, asyncio.TimeoutError)

    async def test_only_skips_reports_reasons_and_has_no_cause(self) -> None:
        """D: nothing was called, so there is no failing provider to blame."""
        clock = Clock(10.0)
        first = RecordingTranslation("first", [ProviderRateLimitError("429")], clock=clock)
        second = RecordingTranslation("second", [ProviderRateLimitError("429")], clock=clock)
        chain = FallbackChain(
            [first, second], failure_threshold=1, cooldown_seconds=60.0, clock=clock
        )
        with self.assertRaises(RuntimeError):
            await chain.translate(make_request(deadline=100.0))

        with self.assertRaises(RuntimeError) as caught:
            await chain.translate(make_request(deadline=100.0))

        message = str(caught.exception)
        self.assertIn("cooling down or disabled", message)
        self.assertIn("first: cooling", message)
        self.assertIn("second: cooling", message)
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)


class AllocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_approved_allocation_is_unchanged(self) -> None:
        """G: R=1, 2, 2.01, 6 keep the boundaries ce1e47e already fixed."""
        cases = [
            # (remaining, who answers, primary deadline, fallback deadline)
            # At 2.01 the primary is still called, and it is called with
            # D - reserve -- the reserve is deducted from the PRIMARY's share,
            # not handed back to it when the fallback goes unused.
            (1.0, "fallback", None, 11.0),
            (2.0, "fallback", None, 12.0),
            (2.01, "primary", 10.01, None),
            (6.0, "primary", 14.0, None),
        ]
        for remaining, answerer, primary_deadline, fallback_deadline in cases:
            with self.subTest(remaining=remaining):
                clock = Clock(10.0)
                primary = RecordingTranslation("primary", ["primary"], clock=clock)
                fallback = RecordingTranslation("fallback", ["fallback"], clock=clock)
                chain = FallbackChain(
                    [primary, fallback], fallback_reserve_seconds=2.0, clock=clock
                )
                request = make_request(deadline=10.0 + remaining)

                result = await chain.translate(request)

                self.assertEqual(result.provider_id, answerer)
                self.assertEqual(primary.deadlines, [] if primary_deadline is None else [primary_deadline])
                self.assertEqual(
                    fallback.deadlines, [] if fallback_deadline is None else [fallback_deadline]
                )
                for provider in (primary, fallback):
                    for deadline in provider.deadlines:
                        self.assertLessEqual(deadline, 10.0 + remaining)

    async def test_no_deadline_means_no_shortening_and_no_guard(self) -> None:
        """G: a cue without a budget keeps the old behaviour exactly."""
        clock = Clock(10.0)
        primary = RecordingTranslation("primary", ["primary"], clock=clock)
        fallback = RecordingTranslation("fallback", ["fallback"], clock=clock)
        chain = FallbackChain([primary, fallback], fallback_reserve_seconds=2.0, clock=clock)

        result = await chain.translate(make_request(deadline=None))

        self.assertEqual(result.provider_id, "primary")
        self.assertEqual(primary.deadlines, [None])
        self.assertEqual(fallback.deadlines, [])

    async def test_only_the_primary_pays_the_reserve(self) -> None:
        """G: a three-provider chain must not deduct the reserve twice."""
        clock = Clock(10.0)
        first = RecordingTranslation("first", [asyncio.TimeoutError("slow")], clock=clock)
        second = RecordingTranslation("second", [asyncio.TimeoutError("slow")], clock=clock)
        third = RecordingTranslation("third", ["third"], clock=clock)
        chain = FallbackChain(
            [first, second, third], fallback_reserve_seconds=2.0, clock=clock
        )

        result = await chain.translate(make_request(deadline=20.0))

        self.assertEqual(result.provider_id, "third")
        self.assertEqual(first.deadlines, [18.0])
        self.assertEqual(second.deadlines, [20.0])
        self.assertEqual(third.deadlines, [20.0])

    async def test_a_primary_local_expiry_still_falls_through(self) -> None:
        """G: running out of the primary's share is not running out of the cue."""
        clock = Clock(10.0)
        primary = RecordingTranslation(
            "primary", [asyncio.TimeoutError("local budget")], clock=clock
        )
        fallback = RecordingTranslation("fallback", ["fb"], clock=clock)
        chain = FallbackChain([primary, fallback], fallback_reserve_seconds=2.0, clock=clock)

        result = await chain.translate(make_request(deadline=16.0))

        self.assertEqual(result.provider_id, "fallback")
        self.assertEqual(primary.deadlines, [14.0])
        self.assertEqual(fallback.deadlines, [16.0])

    async def test_cancellation_propagates_untouched(self) -> None:
        """G: a cancelled cue is not a provider failure and must not be retried."""
        clock = Clock(10.0)
        primary = RecordingTranslation(
            "primary", [asyncio.CancelledError()], clock=clock
        )
        fallback = RecordingTranslation("fallback", ["fb"], clock=clock)
        chain = FallbackChain([primary, fallback], clock=clock)

        with self.assertRaises(asyncio.CancelledError):
            await chain.translate(make_request(deadline=16.0))

        self.assertEqual(fallback.calls, 0)
        self.assertEqual(chain.health["primary"].consecutive_failures, 0)


if __name__ == "__main__":
    unittest.main()
