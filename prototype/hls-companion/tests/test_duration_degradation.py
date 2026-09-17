#!/usr/bin/env python3
"""Behaviour tests for the offline duration-degradation analyzer (plan v2 section W10).

The tests generate roughly 1440 synthetic sampler records in-process, so a
two-hour experiment costs about two seconds instead of two hours. Nothing here
touches the network, the product, a browser or a real capture file.

What is asserted is the *executed* output -- the values returned by
``normalize_record`` / ``build_windows`` / ``fit_duration_trend`` /
``classify_duration_effect``, the report dict, and the JSON actually written to
disk. The tests never grep the analyzer's source for a string, because a test
that passes on a comment has tested nothing.

Run directly, or through the repository runner::

    py -3.10 prototype/hls-companion/tests/test_duration_degradation.py
    py -3.10 scripts/run-hls-tests.py --only duration_degradation --timeout 180
"""

from __future__ import annotations

import importlib.util
import json
import math
import random
import sys
import tempfile
import unittest
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]
ANALYZER_PATH = PKG_ROOT / "scripts" / "analyze-duration-degradation.py"


def _load_analyzer():
    """Import the hyphenated script as a module.

    ``spec_from_file_location`` + ``module_from_spec`` alone is not enough: a
    ``@dataclass`` resolves its annotations through ``sys.modules[cls.__module__]``
    during class creation, so the module must be registered before it executes.
    """
    spec = importlib.util.spec_from_file_location("analyze_duration_degradation", ANALYZER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ANALYZER = _load_analyzer()

SESSION_SECONDS = ANALYZER.SESSION_SECONDS
BASE_SAMPLE_SECONDS = ANALYZER.BASE_SAMPLE_SECONDS
PLANNED_SESSION_RECORDS = ANALYZER.PLANNED_SESSION_RECORDS
EFFECT_GROWTH = ANALYZER.EFFECT_GROWTH
EFFECT_NONE_DETECTED = ANALYZER.EFFECT_NONE_DETECTED
EFFECT_INSUFFICIENT = ANALYZER.EFFECT_INSUFFICIENT
HYPOTHESIS_SUPPORTED = ANALYZER.HYPOTHESIS_SUPPORTED
HYPOTHESIS_REFUTED = ANALYZER.HYPOTHESIS_REFUTED
HYPOTHESIS_NO_DATA = ANALYZER.HYPOTHESIS_NO_DATA
VISIBILITY_HIDDEN = ANALYZER.VISIBILITY_HIDDEN
VISIBILITY_VISIBLE = ANALYZER.VISIBILITY_VISIBLE
VISIBILITY_UNKNOWN = ANALYZER.VISIBILITY_UNKNOWN

BASE_WALL_EPOCH = 1_760_000_000.0


# --------------------------------------------------------------------------
# Synthetic sampler records
# --------------------------------------------------------------------------
def synthetic_record(
    index: int,
    *,
    sample_seconds: float = BASE_SAMPLE_SECONDS,
    cpu_base: float = 1.0,
    cpu_sine: float = 0.0,
    cpu_linear_per_hour: float = 0.0,
    heap_base_mb: float = 120.0,
    heap_linear_per_hour: float = 0.0,
    raf_p95_ms: float = 17.0,
    raf_p95_linear_per_hour_ms: float = 0.0,
    dropped_frame_delta: int = 0,
    total_frame_delta: int = 300,
    backlog: float = 1.0,
    queue_delay_p95: float = 0.4,
    queue_delay_linear_per_hour: float = 0.0,
    attempts_step: int = 2,
    failures_step: int = 0,
    deadline_step: int = 0,
    dropped_step: int = 0,
    latency_window_samples: int = 40,
    latency_sample_age_seconds: float = 2.0,
    hidden: bool | None = False,
    visible: bool | None = True,
    no_raf_callbacks: bool = False,
    no_rvfc_callbacks: bool = False,
    renderer_block: bool = True,
    session_id: str = "sess-a",
    code_sha: str = "8b3ce74",
    config_fingerprint: str = "fp-1",
    processes: dict | None = None,
    noise_seed: int = 20260917,
) -> dict:
    """One line in the sampler's documented shape: exactly six top-level keys.

    Defaults describe a healthy fixed session, so each test states only the
    single defect it is about.
    """
    rng = random.Random(noise_seed * 1_000_003 + index)
    elapsed = index * sample_seconds
    hours = elapsed / 3600.0

    renderer_cpu = cpu_base + cpu_linear_per_hour * hours
    if cpu_sine:
        renderer_cpu += cpu_sine * math.sin(2.0 * math.pi * elapsed / 95.0)
    renderer_cpu += rng.gauss(0.0, 0.01) + abs(cpu_linear_per_hour) * rng.gauss(0.0, 0.002)

    # gapMax is milliseconds, like its rVFC sibling gapMaxMs.
    raf_gap_max_ms = 250.0 + raf_p95_ms * 2.0 + rng.gauss(0.0, 10.0)
    raf_gap_p95_ms = raf_p95_ms + raf_p95_linear_per_hour_ms * hours
    if not no_raf_callbacks:
        raf_gap_p95_ms += rng.gauss(0.0, 0.8)

    dropped_total = index * dropped_frame_delta
    frames_total = index * total_frame_delta
    renderer = {
        "frames": 0 if no_raf_callbacks else 300,
        "gapP50": 0.0 if no_raf_callbacks else 16.6 + rng.gauss(0.0, 0.3),
        "gapP95": 0.0 if no_raf_callbacks else raf_gap_p95_ms,
        "gapMax": 0.0 if no_raf_callbacks else raf_gap_max_ms,
        "over100ms": 0 if no_raf_callbacks else 0,
        "over500ms": 0 if no_raf_callbacks else 0,
        "heapMB": heap_base_mb + heap_linear_per_hour * hours + rng.gauss(0.0, 3.0),
        "domNodes": 5000 + (index % 5) + int(rng.gauss(0.0, 20.0)),
        "currentTime": max(elapsed - sample_seconds, 0.0),
        "paused": False,
        "readyState": 4,
        "bufferedAhead": 12.0,
        "dropped": dropped_total,
        "totalVideoFrames": frames_total,
        "longTasks": index // 10,
        "longTaskMs": 3.0,
        "longTaskMaxMs": 40.0,
        "hidden": bool(hidden),
        "visible": bool(visible),
        "rvfc": {
            "count": 0 if (no_raf_callbacks or no_rvfc_callbacks) else 300,
            "gapMaxMs": 0.0 if (no_raf_callbacks or no_rvfc_callbacks) else 18.0,
            "over100": 0,
            "over500": 0,
            "mediaTime": elapsed,
            "presentedFrames": frames_total,
            "expectedDisplayTime": elapsed * 1000.0,
            "supported": True,
            "attached": True,
        },
        "visibilityEvents": [],
    }

    m0 = {
        "wall": BASE_WALL_EPOCH + elapsed,
        "state": "running",
        "pdtEpoch": 1_760_000_000.0,
        "asrProviderId": "asr-x",
        "translationProviderId": "prov-x",
        "targetLanguage": "zh",
        "sourceLanguagePolicy": "auto",
        "translationAttempts": max(index, 0) * attempts_step,
        "translationFailures": max(index, 0) * failures_step,
        "translationProviderFailures": 0,
        "translationDeadlineExpired": max(index, 0) * deadline_step,
        "sourceOnlyCues": 0,
        "translationDropped": max(index, 0) * dropped_step,
        "translationBacklog": backlog,
        "translationQueueDelayP50": 0.2,
        "translationQueueDelayP95": queue_delay_p95 + queue_delay_linear_per_hour * hours,
        "translationProviderDelayP50": 0.3,
        "translationProviderDelayP95": 0.6,
        "translationSuccessReadyLagP50": 0.9,
        "translationSuccessReadyLagP95": 1.4,
        "latencySamples": 400,
        "latencyWindowSamples": latency_window_samples,
        "latencySampleAgeSeconds": latency_sample_age_seconds,
        "latencyUnknown": 0,
        "lastTranslationError": None,
    }

    record = {
        "elapsed": elapsed,
        "wall": BASE_WALL_EPOCH + elapsed,
        "cpuByRole": {
            "main": 0.4,
            "renderer": renderer_cpu,
            "gpu-process": 0.5,
            "utility": 0.05,
            "network.mojom": 0.02,
        },
        "cpuTotal": 0.4 + renderer_cpu + 0.5 + 0.05 + 0.02,
        "renderer": renderer if renderer_block else None,
        "m0": m0,
        # Identity fields are not part of the six documented keys; they arrive on
        # a record only when the sampler was told about them, which is why the
        # analyzer treats their absence as "unknown" rather than as "unchanged".
        "session_id": session_id,
        "code_sha": code_sha,
        "config_fingerprint": config_fingerprint,
    }
    if processes is not None:
        record["processes"] = processes
    return record


def session(count: int = PLANNED_SESSION_RECORDS, **kwargs) -> list:
    """A whole synthetic run: 1440 records is 7200 s at the 5 s cadence."""
    return [synthetic_record(index, **kwargs) for index in range(count)]


def session_of(count: int, **kwargs) -> list:
    """``session`` with a positional length, for readability at call sites."""
    return session(count, **kwargs)


def full_session(**kwargs) -> list:
    return session(PLANNED_SESSION_RECORDS, **kwargs)


def write_jsonl(records, path: Path) -> Path:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return path


def window_index_for(minute: float) -> int:
    return int((minute * 60.0 - ANALYZER.FIT_START_SECONDS) / ANALYZER.WINDOW_SECONDS)


# --------------------------------------------------------------------------
# D: cyclic CPU, stable frame behaviour
# --------------------------------------------------------------------------
class CyclicCpuStableFramesTests(unittest.TestCase):
    def test_cycle_is_detected_and_not_reported_as_duration_growth(self):
        report = ANALYZER.analyze(full_session(cpu_sine=0.30), {"code_sha": "8b3ce74"})

        cycle = report["cycle"]
        self.assertEqual(cycle["status"], "ok")
        self.assertEqual(cycle["known_period_seconds"], 95.0)
        # The fitted amplitude must recover the injected 0.30 core sinusoid.
        self.assertAlmostEqual(cycle["amplitude"], 0.30, delta=0.03)
        self.assertAlmostEqual(cycle["linear_slope_per_minute"], 0.0, delta=0.002)
        self.assertLess(abs(cycle["detrended_slope_per_minute"]), 0.002)
        self.assertIn("not performed", cycle["frequency_search"])

        self.assertEqual(report["quality_gate"]["passed"], True)
        self.assertEqual(report["quality_gate"]["valid_fit_records"], 1320)
        self.assertEqual(report["quality_gate"]["qualified_windows"], 22)
        self.assertEqual(report["verdict"]["state"], EFFECT_NONE_DETECTED)
        self.assertEqual(report["verdict"]["growth_metrics"], [])

        # The cycle hypothesis is the one this shape supports.
        states = {entry["id"]: entry["state"] for entry in report["hypotheses"]}
        self.assertEqual(states["c"], HYPOTHESIS_SUPPORTED)

        cpu = report["trends"]["cpu_tree_low_cores"]
        self.assertEqual(cpu["observations"], 22)
        self.assertLess(abs(cpu["slope_span_over_horizon"]), 0.25)

    def test_repeated_analysis_is_reproducible_and_gives_only_a_bounded_negative(self):
        records = full_session(cpu_sine=0.30)
        first = ANALYZER.analyze(records, None)
        second = ANALYZER.analyze(records, None)

        self.assertEqual(first["verdict"]["state"], second["verdict"]["state"])
        self.assertEqual(first["verdict"]["none_detected_metrics"],
                         second["verdict"]["none_detected_metrics"])
        for name, trend in first["trends"].items():
            other = second["trends"][name]
            self.assertEqual(trend["slope"], other["slope"], msg=name)
            self.assertEqual(trend.get("slope_ci_low"), other.get("slope_ci_low"), msg=name)
            self.assertEqual(trend.get("slope_ci_high"), other.get("slope_ci_high"), msg=name)
        self.assertEqual(first["trends"]["cpu_tree_low_cores"]["bootstrap"]["seed"], 20260917)

        verdict = first["verdict"]
        self.assertEqual(verdict["bounded_negative_conclusion"],
                         ANALYZER.BOUNDED_NEGATIVE_CONCLUSION)
        self.assertIn("未检出超过预注册门槛的随时间增长", verdict["bounded_negative_conclusion"])
        self.assertIn("不覆盖全天、未知遮挡层和未测资源", verdict["bounded_negative_conclusion"])
        # The forbidden claims must be named as forbidden, never emitted as a result.
        for forbidden in ("证明没有泄漏", "越跑越卡彻底根治"):
            self.assertIn(forbidden, verdict["forbidden_claims"])
            self.assertNotEqual(verdict["bounded_negative_conclusion"], forbidden)


# --------------------------------------------------------------------------
# D: cycle plus real linear degradation
# --------------------------------------------------------------------------
class LinearDegradationTests(unittest.TestCase):
    def test_slope_endpoints_and_interval_agree_on_a_growth_candidate(self):
        report = ANALYZER.analyze(
            full_session(
                cpu_sine=0.30,
                cpu_linear_per_hour=0.9,
                heap_linear_per_hour=40.0,
                raf_p95_linear_per_hour_ms=30.0,
                queue_delay_linear_per_hour=0.5,
                backlog=1.0,
            ),
            None,
        )
        self.assertEqual(report["quality_gate"]["passed"], True)
        self.assertEqual(report["verdict"]["state"], EFFECT_GROWTH)

        cpu = report["trends"]["cpu_tree_low_cores"]
        # Pre-registered screen: at least +0.25 core.
        self.assertEqual(cpu["threshold"], 0.25)
        self.assertGreater(cpu["early_late_delta"], cpu["threshold"])
        self.assertGreater(cpu["slope_span_over_horizon"], cpu["threshold"])
        self.assertGreater(cpu["slope_ci_low"], 0.0)

        effect = report["metric_effects"]["cpu_tree_low_cores"]
        self.assertEqual(effect["state"], EFFECT_GROWTH)
        self.assertEqual(effect["state_code"], "growth_candidate")

        # The relative/absolute screen: max(20% of early, 32 MiB) for heap.
        heap = report["trends"]["heap_low_mb"]
        self.assertEqual(heap["threshold"], max(abs(heap["early_mean"]) * 0.20, 32.0))
        self.assertEqual(report["metric_effects"]["heap_low_mb"]["state"], EFFECT_GROWTH)

        frame_gap = report["trends"]["raf_gap_p95_seconds"]
        # max(20% of early, 8 ms) -- and the 20% term must win here.
        self.assertAlmostEqual(
            frame_gap["threshold"], max(abs(frame_gap["early_mean"]) * 0.20, 0.008), places=9
        )
        self.assertEqual(report["metric_effects"]["raf_gap_p95_seconds"]["state"], EFFECT_GROWTH)

        # A growth verdict must not carry the bounded negative sentence.
        self.assertIsNone(report["verdict"]["bounded_negative_conclusion"])

    def test_degrowth_is_not_reported_as_growth(self):
        report = ANALYZER.analyze(full_session(cpu_linear_per_hour=-0.9), None)
        cpu = report["metric_effects"]["cpu_tree_low_cores"]
        self.assertNotEqual(cpu["state"], EFFECT_GROWTH)
        self.assertLess(cpu["slope_per_minute"], 0.0)


# --------------------------------------------------------------------------
# D: one discrete PTS boundary step
# --------------------------------------------------------------------------
class DiscretePtsStepTests(unittest.TestCase):
    def test_step_is_reported_as_a_discrete_event_not_a_smooth_leak(self):
        step_at = 3600.0
        records = full_session()
        for record in records:
            if record["elapsed"] >= step_at:
                record["renderer"]["currentTime"] -= 4000.0
        report = ANALYZER.analyze(records, None)

        segmentation = report["segmentation"]
        self.assertTrue(segmentation["split"])
        self.assertEqual(len(segmentation["media_time_steps"]), 1)
        event = segmentation["media_time_steps"][0]
        self.assertEqual(event["kind"], "media_time_step")
        self.assertLess(event["delta_seconds"], -3000.0)
        self.assertIn("R1", event["candidate_owner"])

        # The two sides of the step are separate analysis segments: one segment
        # held 3600 s of the run, the other the rest.
        self.assertEqual(report["quality_gate"]["segments_total"], 2)
        self.assertGreater(len(report["segmentation"]["segments"]), 1)
        for segment in report["segmentation"]["segments"]:
            self.assertIsNotNone(segment["reason_closed"])

        # The event is handed to R1 rather than being fitted as gradual growth.
        states = {entry["id"]: entry["state"] for entry in report["hypotheses"]}
        self.assertEqual(states["e"], HYPOTHESIS_SUPPORTED)
        growth = report["verdict"]["growth_metrics"]
        self.assertNotIn("raf_gap_p95_seconds", growth)
        self.assertNotIn("heap_low_mb", growth)

    def test_sampling_gap_also_splits_the_run(self):
        records = full_session()
        for index, record in enumerate(records):
            if index >= 720:
                # A three-minute sleep: the sampler did not run, so the elapsed
                # clock jumps while the wall clock jumps with it.
                record["elapsed"] += 180.0
                record["wall"] += 180.0
        report = ANALYZER.analyze(records, None)
        kinds = {event["kind"] for event in report["segmentation"]["clock_events"]}
        self.assertIn("sampling_gap", kinds)
        self.assertTrue(report["segmentation"]["split"])

    def test_wall_clock_divergence_is_a_clock_event_not_duration(self):
        records = full_session()
        for index, record in enumerate(records):
            if index >= 720:
                record["wall"] += 600.0  # wall races ahead of the monotonic clock
        report = ANALYZER.analyze(records, None)
        kinds = {event["kind"] for event in report["segmentation"]["clock_events"]}
        self.assertIn("clock_divergence", kinds)
        self.assertTrue(report["segmentation"]["split"])


# --------------------------------------------------------------------------
# D: hidden window, null rVFC/rAF, plus a confirmably visible empty stretch
# --------------------------------------------------------------------------
class NullAndVisibilityTests(unittest.TestCase):
    def _null_renderer_records(self):
        """Two minutes of entirely null renderer blocks (minutes 34-36).

        A whole ``renderer`` block of null is the sampler's way of saying "this
        interval produced no page reading at all"; nothing here may be backfilled
        from a neighbour.
        """
        records = full_session()
        for record in records:
            if 2040.0 <= record["elapsed"] < 2160.0:
                record["renderer"] = None
        return records

    def _hidden_and_empty_callback_records(self):
        """20 hidden minutes, and a 5 s no-callback stretch while visible.

        The hidden minutes keep a *present* renderer block whose callback fields
        are null and whose frame count is 0 -- which is what a hidden window
        actually looks like -- so ``hidden`` stays knowable instead of becoming
        another instance of "unknown".
        """
        records = full_session()
        for record in records:
            elapsed = record["elapsed"]
            if 1800.0 <= elapsed < 3000.0:
                renderer = record["renderer"]
                renderer["hidden"] = True
                renderer["visible"] = False
                renderer["frames"] = 0
                renderer["gapP50"] = None
                renderer["gapP95"] = None
                renderer["gapMax"] = None
                renderer["heapMB"] = None
                renderer["domNodes"] = None
                renderer["over100ms"] = None
                renderer["over500ms"] = None
                renderer["rvfc"]["count"] = 0
                renderer["rvfc"]["gapMaxMs"] = None
            elif 3600.0 <= elapsed < 3605.0:
                # Visible, sampled, and reporting nothing: exactly the moment
                # this tool exists for.
                record["renderer"]["frames"] = 0
                record["renderer"]["gapP50"] = None
                record["renderer"]["gapP95"] = None
                record["renderer"]["gapMax"] = None
                record["renderer"]["rvfc"]["count"] = 0
                record["renderer"]["rvfc"]["gapMaxMs"] = None
        return records

    def test_does_not_crash_on_null_blocks_and_never_fills_null_with_zero(self):
        records = self._null_renderer_records()
        report = ANALYZER.analyze(records, None)
        self.assertEqual(report["generated_from"]["records_with_null_renderer_block"], 24)

        # The null block must remain null: a zero would be a fabricated reading.
        normalized = ANALYZER.normalize_record(records[420])
        self.assertIsNone(normalized["raf_gap_p95_seconds"])
        self.assertIsNone(normalized["raf_gap_p50_seconds"])
        self.assertIsNone(normalized["raf_gap_max_seconds"])
        self.assertIsNone(normalized["heap_mb"])
        self.assertIsNone(normalized["dom_nodes"])
        self.assertIsNone(normalized["rvfc_gap_p95_seconds"])
        self.assertIsNone(normalized["raf_callback_count"])
        self.assertEqual(normalized["observed"]["renderer_present"], False)
        self.assertNotIn(0.0, (normalized["raf_gap_p95_seconds"], normalized["heap_mb"]))
        # Per-role CPU is a sibling of the null block, so it is still measured.
        self.assertIsNotNone(normalized["cpu_core_sum"])
        self.assertIsNone(normalized["document_hidden"])

        # A callback-less window inside a present block is a different case: the
        # count is 0 and the distributions stay null rather than becoming 0.0.
        empty = ANALYZER.normalize_record(
            {
                "elapsed": 0.0,
                "wall": BASE_WALL_EPOCH,
                "cpuByRole": {},
                "cpuTotal": 0.0,
                "renderer": {
                    "frames": 0,
                    "gapP50": None,
                    "gapP95": None,
                    "gapMax": None,
                    "heapMB": None,
                    "domNodes": None,
                    "hidden": False,
                    "visible": True,
                    "rvfc": None,
                },
                "m0": None,
            }
        )
        self.assertEqual(empty["raf_callback_count"], 0)
        self.assertFalse(empty["raf_has_callbacks"])
        self.assertIsNone(empty["raf_gap_p95_seconds"])
        self.assertFalse(empty["rvfc_present"])
        self.assertTrue(empty["rvfc_block_supplied"])
        self.assertFalse(empty["m0_present"])
        self.assertIsNone(empty["cpu_core_sum"])
        self.assertEqual(empty["observed"]["field_null_counts"]["renderer"], 6)
        self.assertTrue(empty["valid"])
    def test_null_windows_are_not_silently_dropped_from_the_report(self):
        records = self._null_renderer_records()
        report = ANALYZER.analyze(records, None)
        # Window 5 covers minutes 35-40, of which 2100-2160 s is the null part.
        window = report["windows"][5]
        self.assertEqual(window["start_seconds"], 2100.0)
        self.assertEqual(window["metrics"]["sample_count"], 60)
        # 12 of the 60 samples lost their page reading. The sample itself is
        # still a valid 5 s record -- what is missing is the page measurement,
        # which is why the metric counts drop while valid_count does not.
        self.assertEqual(window["metrics"]["valid_count"], 60)
        self.assertEqual(window["metrics"]["raf_samples_present"], 48)
        self.assertEqual(window["metrics"]["raf_callback_count"], 48 * 300)
        self.assertEqual(window["unknown_visibility_minutes"], 12)
        self.assertIsNotNone(window["metrics"]["heap_low_mb"])
        # The window is still listed and still declares what it observed.
        self.assertEqual(window["coverage"]["samples"], 60)
        self.assertTrue(window["qualified"])

        fully_null = [
            record for record in records if 2040.0 <= record["elapsed"] < 2160.0
        ]
        self.assertEqual(len(fully_null), 24)
        for record in fully_null:
            normalized = ANALYZER.normalize_record(record)
            self.assertIsNone(normalized["raf_callback_count"])
            self.assertIsNone(normalized["heap_mb"])
            self.assertEqual(normalized["observed"]["renderer_present"], False)
            self.assertEqual(normalized["visibility_layer"], VISIBILITY_UNKNOWN)

    def test_layers_are_layered_and_no_callback_window_is_a_gap_event(self):
        records = self._hidden_and_empty_callback_records()
        report = ANALYZER.analyze(records, None)

        strata = report["visibility_strata"]
        self.assertGreater(strata[VISIBILITY_HIDDEN]["qualified_windows"], 0)
        self.assertGreater(strata[VISIBILITY_VISIBLE]["qualified_windows"], 0)
        self.assertIn("blur", strata[VISIBILITY_UNKNOWN]["note"])
        self.assertGreater(strata[VISIBILITY_HIDDEN]["hidden_minutes"], 0)

        hidden_window = report["windows"][window_index_for(35.0)]
        self.assertEqual(hidden_window["hidden_minutes"], 60)
        self.assertEqual(hidden_window["metrics"]["raf_callback_count"], 0)
        self.assertEqual(hidden_window["metrics"]["raf_synchronous_minutes"], 60)
        self.assertIsNone(hidden_window["metrics"]["raf_gap_p95_seconds"])

        # The visible empty stretch survives as an event instead of being eaten
        # by a null p95.
        counts = report["gap_events"]["counts"]
        self.assertGreaterEqual(counts["no_raf_callbacks_in_interval"], 1)
        minute_sixty = [
            event
            for event in report["gap_events"]["events"]
            if event["kind"] == "no_raf_callbacks_in_interval" and event["minute"] == 60
        ]
        self.assertEqual(len(minute_sixty), 1)
        self.assertEqual(minute_sixty[0]["visibility_layer"], VISIBILITY_VISIBLE)
        self.assertEqual(minute_sixty[0]["callback_count"], 0)
        # Hidden windows are reported as hidden events, not as visible gaps.
        hidden_events = [
            event
            for event in report["gap_events"]["events"]
            if event["kind"] == "no_raf_callbacks_in_interval"
            and event["visibility_layer"] == VISIBILITY_HIDDEN
        ]
        self.assertGreater(len(hidden_events), 0)

    def test_occluded_window_is_unknown_not_visibly_fine(self):
        records = full_session(visible=False)
        report = ANALYZER.analyze(records, None)
        self.assertEqual(report["visibility_strata"][VISIBILITY_VISIBLE]["qualified_windows"], 0)
        self.assertGreaterEqual(
            report["visibility_strata"][VISIBILITY_UNKNOWN]["qualified_windows"], 1
        )
        # With no confirmably visible window, that layer gets no verdict at all.
        self.assertFalse(report["visibility_strata"][VISIBILITY_VISIBLE]["verdict_allowed"])
        self.assertIn(
            "no 'not degraded' conclusion",
            report["visibility_strata"][VISIBILITY_VISIBLE]["verdict_note"],
        )

    def test_five_second_gap_is_reported_per_minute_and_bounded(self):
        records = full_session()
        records[900]["renderer"]["gapMax"] = 6000.0  # milliseconds == 6 s
        report = ANALYZER.analyze(records, None)
        long_gaps = [
            event for event in report["gap_events"]["events"] if event["kind"] == "gap_over_5000ms"
        ]
        self.assertEqual(len(long_gaps), 1)
        self.assertAlmostEqual(long_gaps[0]["gap_seconds"], 6.0, places=6)
        self.assertEqual(long_gaps[0]["minute"], 75)
        self.assertAlmostEqual(report["gap_events"]["longest_gap_seconds"], 6.0, places=6)
        # A healthy 250 ms maximum frame gap is not a gap event at all.
        self.assertEqual(report["gap_events"]["counts"]["gap_over_500ms"], 0)

    def test_sub_second_frame_gaps_are_not_reported_as_stalls(self):
        # The renderer gap fields are milliseconds; reading them as seconds would
        # turn every 250 ms maximum into a false multi-second stall.
        records = full_session()
        records[500]["renderer"]["gapMax"] = 900.0  # 0.9 s: above the report floor
        report = ANALYZER.analyze(records, None)
        self.assertEqual(report["gap_events"]["counts"]["gap_over_500ms"], 1)
        self.assertEqual(report["gap_events"]["counts"]["gap_over_5000ms"], 0)
        event = report["gap_events"]["events"][0]
        self.assertAlmostEqual(event["gap_seconds"], 0.9, places=9)


# --------------------------------------------------------------------------
# D: segmentation
# --------------------------------------------------------------------------
class SegmentationTests(unittest.TestCase):
    def test_session_change_splits_and_forbids_one_120_minute_verdict(self):
        records = full_session()
        for record in records:
            if record["elapsed"] >= 3600.0:
                record["session_id"] = "sess-b"
        report = ANALYZER.analyze(records, None)
        self.assertTrue(report["segmentation"]["split"])
        observations = [
            trend["observations"]
            for trend in [report["trends"]["cpu_tree_low_cores"]]
        ][0]
        self.assertLessEqual(observations, 22)
        self.assertTrue(
            any(
                "segments" in reason
                for reason in report["quality_gate"]["reasons"]
            )
            or report["quality_gate"]["passed"]
        )
        first = report["segmentation"]["segments"][0]
        self.assertIn("session identity changed", first["reason_closed"])

    def test_code_and_config_change_split(self):
        records = full_session()
        for record in records:
            if record["elapsed"] >= 2400.0:
                record["code_sha"] = "deadbee"
            if record["elapsed"] >= 4800.0:
                record["config_fingerprint"] = "fp-2"
        report = ANALYZER.analyze(records, None)
        closed = [segment["reason_closed"] for segment in report["segmentation"]["segments"]]
        self.assertEqual(len(closed), 3)
        self.assertTrue(any("code SHA changed" in reason for reason in closed))
        self.assertTrue(any("configuration fingerprint changed" in reason for reason in closed))

    def test_counter_reset_splits_and_forbids_one_continuous_verdict(self):
        records = full_session(attempts_step=2, failures_step=1)
        for record in records:
            if record["elapsed"] >= 4000.0:
                record["m0"]["translationAttempts"] = 3  # restart: counters begin again
                record["m0"]["translationFailures"] = 0
        report = ANALYZER.analyze(records, None)
        closed = [segment["reason_closed"] for segment in report["segmentation"]["segments"]]
        self.assertTrue(any("went backwards" in reason for reason in closed), closed)
        self.assertTrue(report["segmentation"]["split"])
        self.assertGreater(len(report["segmentation"]["segments"]), 1)
        # The two sides are reported separately; neither is silently merged into
        # a single 120-minute slope.
        self.assertIn("segments", " ".join(report["quality_gate"]["reasons"]))
        self.assertTrue(
            all(segment["records"] > 0 for segment in report["segmentation"]["segments"])
        )

    def test_pid_reuse_splits(self):
        processes = {"renderer": {"pid": 4242, "createTime": 1_700_000_000.0}}
        records = full_session(processes=processes)
        for record in records:
            if record["elapsed"] >= 3600.0:
                record["processes"] = {
                    "renderer": {"pid": 4242, "createTime": 1_760_000_000.0}
                }
        report = ANALYZER.analyze(records, None)
        closed = [segment["reason_closed"] for segment in report["segmentation"]["segments"]]
        self.assertTrue(any("PID 4242 reused" in reason for reason in closed))

    def test_twelve_minute_run_is_insufficient_and_never_says_no_degradation(self):
        report = ANALYZER.analyze(session_of(144), None)  # 12 minutes
        self.assertEqual(report["verdict"]["state"], EFFECT_INSUFFICIENT)
        self.assertIsNone(report["verdict"]["bounded_negative_conclusion"])
        gate = report["quality_gate"]
        self.assertFalse(gate["coverage_ok"])
        self.assertLess(gate["valid_fit_records"], gate["min_valid_records"])
        self.assertTrue(gate["reasons"])

    def test_twenty_four_minute_run_is_insufficient_for_the_same_reason(self):
        report = ANALYZER.analyze(session_of(288), None)  # 24 minutes
        self.assertEqual(report["verdict"]["state"], EFFECT_INSUFFICIENT)
        gate = report["quality_gate"]
        self.assertFalse(gate["coverage_ok"])
        self.assertFalse(gate["end_ranges_ok"])
        self.assertEqual(gate["late_range_qualified_windows"], 0)

    def test_sparse_coverage_fails_the_gate_even_at_full_length(self):
        # Every other sample missing: 50 % coverage inside a full-length span.
        records = [record for index, record in enumerate(full_session()) if index % 2 == 0]
        report = ANALYZER.analyze(records, None)
        self.assertEqual(report["verdict"]["state"], EFFECT_INSUFFICIENT)
        self.assertFalse(report["quality_gate"]["coverage_ok"])
        self.assertLess(report["quality_gate"]["valid_fit_records"], 1254)

    def test_insufficient_end_denominators_block_the_stage_and_frame_ratios(self):
        # A short run: the ratios have no end-window denominators to stand on, so
        # neither screen may be read as a verdict either way.
        records = session_of(144, attempts_step=4, total_frame_delta=300)
        report = ANALYZER.analyze(records, None)
        stage = report["metric_effects"]["translation_failure_ratio_window"]
        self.assertEqual(stage["state"], EFFECT_INSUFFICIENT)
        frames = report["metric_effects"]["dropped_frame_ratio_window"]
        self.assertEqual(frames["state"], EFFECT_INSUFFICIENT)
        self.assertEqual(report["verdict"]["state"], EFFECT_INSUFFICIENT)
        self.assertIsNone(report["verdict"]["bounded_negative_conclusion"])

        # With the denominators present and healthy, the same stable ratios are
        # allowed a limited negative result -- and the counts are still reported.
        full = ANALYZER.analyze(
            full_session(attempts_step=4, failures_step=1, total_frame_delta=300), None
        )
        self.assertEqual(full["quality_gate"]["passed"], True)
        self.assertGreaterEqual(
            full["trends"]["translation_failure_ratio_window"]["early_attempt_delta"],
            ANALYZER.TRANSLATION_MIN_ATTEMPTS,
        )
        self.assertGreaterEqual(
            full["trends"]["dropped_frame_ratio_window"]["early_frame_delta"],
            ANALYZER.DROPPED_MIN_TOTAL_FRAMES,
        )
        self.assertEqual(
            full["metric_effects"]["translation_failure_ratio_window"]["state"],
            EFFECT_NONE_DETECTED,
        )
        self.assertEqual(
            full["metric_effects"]["dropped_frame_ratio_window"]["state"], EFFECT_NONE_DETECTED
        )

    def test_stale_stage_readings_block_a_stage_lag_conclusion(self):
        records = full_session(
            latency_window_samples=4,
            latency_sample_age_seconds=300.0,
            queue_delay_linear_per_hour=1.0,
        )
        report = ANALYZER.analyze(records, None)
        self.assertGreater(report["quality_gate"]["stale_stage_windows"], 0)
        effect = report["metric_effects"]["translation_queue_delay_p95_median_seconds"]
        self.assertEqual(effect["state"], EFFECT_INSUFFICIENT)
        self.assertIn("stale", " ".join(effect["reasons"]))
        self.assertIn("rolling p95", report["stage_latency_note"])


# --------------------------------------------------------------------------
# D: bounded exit
# --------------------------------------------------------------------------
class BoundedExitTests(unittest.TestCase):
    def test_output_cap_is_enforced_without_overwriting_old_evidence(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            output = temp / "analysis.json"
            output.write_text('{"previous": "evidence"}', encoding="utf-8")
            before = output.read_bytes()

            report = ANALYZER.analyze(session_of(240), None)
            params = ANALYZER.AnalysisParams(output_limit_bytes=4096)
            outcome = ANALYZER.write_report(report, output, params)

            self.assertFalse(outcome["written"])
            self.assertEqual(outcome["status"], "output_limit_exceeded")
            self.assertEqual(outcome["limit_bytes"], 4096)
            self.assertGreater(outcome["bytes"], 4096)
            self.assertEqual(output.read_bytes(), before)

    def test_report_under_the_cap_is_written_and_readable(self):
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "nested" / "analysis.json"
            report = ANALYZER.analyze(session_of(240), None)
            outcome = ANALYZER.write_report(report, output)
            self.assertTrue(outcome["written"])
            self.assertEqual(outcome["limit_bytes"], ANALYZER.OUTPUT_LIMIT_BYTES)
            reloaded = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(reloaded["verdict"]["state"], report["verdict"]["state"])

    def test_exceeding_the_instrument_cap_forces_an_insufficient_bounded_exit(self):
        records = full_session()
        report = ANALYZER.analyze(
            records,
            None,
            ANALYZER.AnalysisParams(analysis_deadline_seconds=0.0, instrument_cap_seconds=0.0),
        )
        self.assertTrue(report["instrument"]["cap_exceeded"])
        self.assertTrue(report["instrument"]["bounded_exit"])
        self.assertEqual(report["verdict"]["state"], EFFECT_INSUFFICIENT)
        self.assertIsNone(report["verdict"]["bounded_negative_conclusion"])
        self.assertIn("instrument cap", " ".join(report["verdict"]["reasons"]))

    def test_records_past_the_instrument_cap_are_excluded_not_analyzed(self):
        # 7500 s of samples: the last 300 s are past the 7260 s cap.
        records = session_of(1500)
        report = ANALYZER.analyze(records, None)
        excluded = report["generated_from"]["records_excluded_past_instrument_cap"]
        self.assertGreater(excluded, 0)
        self.assertEqual(
            report["generated_from"]["records_read"] - excluded,
            report["generated_from"]["records_analyzed"],
        )
        # No window may extend past the formal observation.
        for window in report["windows"]:
            self.assertLessEqual(window["end_seconds"], SESSION_SECONDS)

    def test_cli_returns_a_bounded_code_when_the_input_is_oversized(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            input_path = write_jsonl(session_of(60), temp / "lag-samples-20260917T000000Z.jsonl")
            manifest_path = temp / "manifest.json"
            manifest_path.write_text('{"code_sha": "8b3ce74"}', encoding="utf-8")
            code = ANALYZER.main(
                [
                    "--input",
                    str(input_path),
                    "--manifest",
                    str(manifest_path),
                    "--output",
                    str(temp / "out.json"),
                    "--output-limit-bytes",
                    "128",
                ]
            )
            self.assertEqual(code, 3)
            self.assertFalse((temp / "out.json").exists())

    def test_cli_writes_the_reported_state_to_disk(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            input_path = write_jsonl(full_session(cpu_sine=0.30),
                                     temp / "lag-samples-20260917T000000Z.jsonl")
            manifest_path = temp / "manifest.json"
            manifest_path.write_text(
                json.dumps({"code_sha": "8b3ce74", "config_fingerprint": "fp-1"}),
                encoding="utf-8",
            )
            output_path = temp / "analysis.json"
            code = ANALYZER.main(
                [
                    "--input",
                    str(input_path),
                    "--manifest",
                    str(manifest_path),
                    "--output",
                    str(output_path),
                    "--quiet",
                ]
            )
            self.assertEqual(code, 0)
            written = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(written["verdict"]["state"], EFFECT_NONE_DETECTED)
            self.assertEqual(written["input"]["malformed_line_count"], 0)
            self.assertEqual(written["output"]["written"], True)
            self.assertEqual(written["output"]["target"], str(output_path))
            self.assertEqual(written["manifest"]["completeness"]["present_fields"],
                             ["code_sha", "config_fingerprint"])
            self.assertIn("session_id", written["manifest"]["completeness"]["missing_fields"])

    def test_cli_reports_a_missing_manifest_as_missing(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            input_path = write_jsonl(full_session(), temp / "lag-samples-x.jsonl")
            output_path = temp / "analysis.json"
            code = ANALYZER.main(
                [
                    "--input",
                    str(input_path),
                    "--manifest",
                    str(temp / "no-such-manifest.json"),
                    "--output",
                    str(output_path),
                    "--quiet",
                ]
            )
            self.assertEqual(code, 0)
            written = json.loads(output_path.read_text(encoding="utf-8"))
            completeness = written["manifest"]["completeness"]
            self.assertFalse(completeness["provided"])
            self.assertIsNone(written["manifest"]["supplied"])
            self.assertEqual(len(completeness["missing_fields"]),
                             len(ANALYZER.MANIFEST_EXPECTED_FIELDS))


# --------------------------------------------------------------------------
# Registration table and missing-data honesty
# --------------------------------------------------------------------------
class RegisteredConstantsTests(unittest.TestCase):
    def test_all_registered_numbers_are_the_published_ones(self):
        constants = ANALYZER._constant_table(ANALYZER.AnalysisParams())
        self.assertEqual(constants["session_seconds"], 7200.0)
        self.assertEqual(constants["warmup_seconds"], 600.0)
        self.assertEqual(constants["base_sample_seconds"], 5.0)
        self.assertEqual(constants["window_seconds"], 300.0)
        self.assertEqual(constants["fit_range_seconds"], [600.0, 7200.0])
        self.assertEqual(constants["early_range_seconds"], [600.0, 1200.0])
        self.assertEqual(constants["late_range_seconds"], [6600.0, 7200.0])
        self.assertEqual(constants["planned_fit_records"], 1320)
        self.assertEqual(constants["planned_session_records"], 1440)
        self.assertEqual(constants["min_valid_records"], 1254)
        self.assertEqual(constants["min_window_records"], 54)
        self.assertEqual(constants["min_qualified_windows"], 20)
        self.assertEqual(constants["required_windows_per_end_range"], 2)
        self.assertEqual(constants["block_count"], 3)
        self.assertEqual(constants["bootstrap_repeats"], 1000)
        self.assertEqual(constants["bootstrap_seed"], 20260917)
        self.assertEqual(constants["cycle_seconds"], 95.0)
        self.assertEqual(constants["output_limit_bytes"], 256 * 1024 * 1024)
        self.assertEqual(constants["instrument_cap_seconds"], 7260.0)
        self.assertEqual(constants["latency_min_window_samples"], 30)
        self.assertEqual(constants["latency_max_sample_age_seconds"], 30.0)
        self.assertEqual(constants["translation_min_attempts"], 100)
        self.assertEqual(constants["dropped_min_total_frames"], 3000)
        self.assertEqual(constants["trend_horizon_minutes"], 110.0)
        self.assertIn("pre-registered engineering screen", constants["threshold_provenance"])

        thresholds = constants["thresholds"]
        # Every row of the published table is present, once, with its own shape.
        self.assertEqual(len(thresholds), 16)
        self.assertEqual(thresholds["rvfc_gap_p95_seconds"]["rule"], "relative_or_absolute")
        self.assertEqual(thresholds["rvfc_gap_p95_seconds"]["relative"], 0.20)
        self.assertAlmostEqual(thresholds["rvfc_gap_p95_seconds"]["absolute"], 1.0 / 30.0)
        self.assertEqual(thresholds["raf_gap_p95_seconds"]["absolute"], 0.008)
        self.assertEqual(thresholds["dropped_frame_ratio_window"]["absolute"], 0.01)
        self.assertEqual(thresholds["heap_low_mb"]["absolute"], 32.0)
        self.assertEqual(thresholds["heap_low_mb"]["relative"], 0.20)
        self.assertEqual(thresholds["dom_nodes_low"]["absolute"], 200.0)
        self.assertEqual(thresholds["dom_nodes_low"]["relative"], 0.10)
        for key in ("cpu_tree_low_cores", "cpu_renderer_low_cores", "cpu_main_low_cores",
                    "cpu_gpu_low_cores"):
            self.assertEqual(thresholds[key]["absolute"], 0.25)
        self.assertEqual(thresholds["translation_backlog_median_cues"]["absolute"], 3.0)
        self.assertEqual(thresholds["translation_backlog_median_cues"]["sustained_last_windows"], 2)
        for key in ("translation_queue_delay_p95_median_seconds",
                    "translation_provider_delay_p95_median_seconds",
                    "translation_ready_lag_p95_median_seconds"):
            self.assertEqual(thresholds[key]["relative"], 0.25)
            self.assertEqual(thresholds[key]["absolute"], 0.5)

    def test_low_metrics_use_the_window_low_not_the_peak(self):
        # A single GC-shaped spike must not move a "low level" metric.
        records = full_session(heap_base_mb=120.0)
        for record in records:
            if int(record["elapsed"]) % 600 == 0:
                record["renderer"]["heapMB"] = 900.0
        report = ANALYZER.analyze(records, None)
        window = report["windows"][0]
        self.assertLess(window["metrics"]["heap_low_mb"], 200.0)
        self.assertEqual(report["metric_effects"]["heap_low_mb"]["level"], "low")

    def test_missing_manifest_fields_are_reported_rather_than_invented(self):
        report = ANALYZER.analyze(full_session(cpu_sine=0.30), {"code_sha": "8b3ce74"})
        completeness = report["manifest"]["completeness"]
        self.assertTrue(completeness["provided"])
        self.assertEqual(completeness["present_fields"], ["code_sha"])
        self.assertIn("config_fingerprint", completeness["missing_fields"])
        self.assertIn("session_id", completeness["missing_fields"])
        self.assertIn("never filled in from the samples", completeness["note"])

        nothing = ANALYZER.analyze(full_session(cpu_sine=0.30), None)
        self.assertFalse(nothing["manifest"]["completeness"]["provided"])
        self.assertEqual(len(nothing["manifest"]["completeness"]["missing_fields"]),
                         len(ANALYZER.MANIFEST_EXPECTED_FIELDS))

    def test_download_leg_hypothesis_is_no_data_without_bytes_fields(self):
        report = ANALYZER.analyze(full_session(), None)
        states = {entry["id"]: entry for entry in report["hypotheses"]}
        self.assertEqual(states["f"]["state"], HYPOTHESIS_NO_DATA)
        self.assertIn("forwardedBytes", " ".join(states["f"]["reasons"]))
        self.assertEqual(len(report["hypotheses"]), 6)
        for entry in report["hypotheses"]:
            self.assertTrue(entry["supported_shape"])
            self.assertTrue(entry["refuting_shape"])
            self.assertIn(entry["state"],
                          (HYPOTHESIS_SUPPORTED, ANALYZER.HYPOTHESIS_INSUFFICIENT,
                           HYPOTHESIS_REFUTED, HYPOTHESIS_NO_DATA))

    def test_counters_are_used_as_interval_differences_only(self):
        records = full_session(attempts_step=4, failures_step=1, deadline_step=1, dropped_step=2)
        report = ANALYZER.analyze(records, None)
        window = report["windows"][0]
        metrics = window["metrics"]
        # A window holds indices 120..179 inclusive, so the interval difference
        # spans 59 sample steps, not 60: the counter is a level, not a rate.
        self.assertEqual(metrics["translation_attempt_delta"], 59 * 4)
        self.assertEqual(metrics["translation_failure_delta"], 59 * 1)
        self.assertAlmostEqual(metrics["translation_failure_ratio_window"], 0.25)
        # The counter's absolute level is never fitted; only the delta is used.
        self.assertEqual(window["metrics"]["sample_count"], 60)
        # Deadline expiry and drops are reported separately, never summed in.
        self.assertEqual(metrics["deadline_expired_delta"], 59 * 1)
        self.assertEqual(metrics["translation_dropped_delta"], 59 * 2)
        self.assertIn("never summed", report["counter_note"])

    def test_zero_denominator_is_not_a_zero_failure_rate(self):
        records = full_session(attempts_step=0, failures_step=0)
        report = ANALYZER.analyze(records, None)
        self.assertIsNone(report["windows"][0]["metrics"]["translation_failure_ratio_window"])
        self.assertEqual(report["windows"][0]["metrics"]["translation_attempt_delta"], 0)

    def test_persistent_backlog_rise_is_required_to_fire(self):
        # A backlog that accumulates across the whole session is queue build-up.
        accumulating = full_session()
        for record in accumulating:
            record["m0"]["translationBacklog"] = 1.0 + record["elapsed"] / 500.0
        sustained = ANALYZER.analyze(accumulating, None)
        self.assertEqual(
            sustained["metric_effects"]["translation_backlog_median_cues"]["state"], EFFECT_GROWTH
        )
        self.assertTrue(sustained["trends"]["translation_backlog_median_cues"]["sustained_ok"])

        # A burst confined to the middle that drains before the end is a source
        # catch-up, not accumulation. The early/late contrast is zero, and the
        # rise must surface as an aggregated-over stretch rather than as growth.
        burst_records = full_session()
        for record in burst_records:
            if 3000.0 <= record["elapsed"] < 4200.0:
                record["m0"]["translationBacklog"] = 9.0
        burst = ANALYZER.analyze(burst_records, None)
        burst_trend = burst["trends"]["translation_backlog_median_cues"]
        burst_effect = burst["metric_effects"]["translation_backlog_median_cues"]
        self.assertFalse(burst_trend["sustained_ok"])
        self.assertEqual(burst_trend["early_late_delta"], 0.0)
        self.assertNotEqual(burst_effect["state"], EFFECT_GROWTH)
        self.assertFalse(burst_effect["sustained_ok"])
        self.assertEqual(len(burst_effect["masked_windows"]), 4)
        self.assertIn("masks a worse stretch", " ".join(burst_effect["reasons"]))
        self.assertIn("persist", " ".join(burst_effect["reasons"]))


class WindowAndTrendPrimitiveTests(unittest.TestCase):
    def test_windows_are_non_overlapping_five_minute_slots(self):
        window_set = ANALYZER.build_windows(full_session())
        self.assertEqual(len(window_set.windows), 22)
        self.assertEqual(window_set.time_minutes[0], 10.0)
        self.assertEqual(window_set.time_minutes[-1], 115.0)
        for earlier, later in zip(window_set.windows, window_set.windows[1:]):
            self.assertEqual(earlier["end_seconds"], later["start_seconds"])
            self.assertEqual(later["start_seconds"] - earlier["start_seconds"], 300.0)

    def test_warmup_records_are_summarised_but_never_fitted(self):
        window_set = ANALYZER.build_windows(full_session())
        self.assertTrue(all(window["start_seconds"] >= 600.0 for window in window_set.windows))
        self.assertEqual(
            sum(window["metrics"]["valid_count"] for window in window_set.windows), 1320
        )

    def test_uncounted_neighbour_windows_never_become_a_slope(self):
        # Five minutes of samples missing outright. The clock jump also makes the
        # run discontinuous, so no window is manufactured across the hole.
        records = [record for record in full_session() if not 3300.0 <= record["elapsed"] < 3600.0]
        window_set = ANALYZER.build_windows(records)
        self.assertEqual(window_set.gate["valid_fit_records"], 1320 - 60)
        self.assertEqual(window_set.gate["observed_windows"], 21)
        self.assertEqual(window_set.gate["qualified_windows"], 21)
        self.assertEqual(len(window_set.windows), 21)
        # No window was manufactured over the hole.
        self.assertNotIn(3300.0, [window["start_seconds"] for window in window_set.windows])
        self.assertGreater(window_set.gate["segments_total"], 1)
        # 21 of 22 still clears "at least 20", so this deliberately mild defect
        # passes the gate; more holes must not.
        self.assertTrue(window_set.gate["passed"])

        three_holes = [
            record
            for record in records
            if not (1200.0 <= record["elapsed"] < 1500.0 or 6000.0 <= record["elapsed"] < 6300.0)
        ]
        failing = ANALYZER.build_windows(three_holes)
        self.assertEqual(failing.gate["qualified_windows"], 19)
        self.assertFalse(failing.gate["qualified_ok"])
        self.assertFalse(failing.gate["passed"])
        self.assertIn("19", " ".join(failing.gate["reasons"]))
        trend = ANALYZER.fit_duration_trend(failing, "cpu_tree_low_cores")
        self.assertEqual(trend["status"], "insufficient")
        self.assertIsNone(trend["slope"])
        self.assertIn("quality gate", " ".join(trend["notes"]))

    def test_a_late_hole_that_starves_the_end_range_fails_the_gate(self):
        # Removing the final ten minutes leaves no qualifying late window, so the
        # early/late contrast the whole method rests on cannot be formed.
        records = [record for record in full_session() if record["elapsed"] < 6600.0]
        window_set = ANALYZER.build_windows(records)
        gate = window_set.gate
        self.assertEqual(gate["late_range_qualified_windows"], 0)
        self.assertFalse(gate["end_ranges_ok"])
        self.assertFalse(gate["passed"])
        effect = ANALYZER.classify_duration_effect(
            ANALYZER.fit_duration_trend(window_set, "cpu_tree_low_cores")
        )
        self.assertEqual(effect["state"], EFFECT_INSUFFICIENT)

    def test_gate_requires_both_rules_at_once(self):
        # Two early windows missing: the pooled coverage rule fails while every
        # window the run still reached is healthy.
        records = [
            record
            for record in full_session()
            if not (900.0 <= record["elapsed"] < 1500.0)
        ]
        window_set = ANALYZER.build_windows(records)
        gate = window_set.gate
        self.assertEqual(gate["valid_fit_records"], 1320 - 120)
        self.assertLess(gate["valid_fit_records"], gate["min_valid_records"])
        self.assertFalse(gate["coverage_ok"])
        self.assertTrue(gate["qualified_ok"])
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["qualified_windows"], 20)
        self.assertIn("valid base records", " ".join(gate["reasons"]))

        # And the mirror case: every window is healthy, but the recorded span is
        # too short to reach 1254 records at all.
        short = ANALYZER.build_windows(session_of(700))
        self.assertFalse(short.gate["coverage_ok"])
        self.assertEqual(short.gate["passed"], False)
        self.assertEqual(
            ANALYZER.classify_duration_effect(
                ANALYZER.fit_duration_trend(short, "cpu_tree_low_cores")
            )["state"],
            EFFECT_INSUFFICIENT,
        )

    def test_theil_sen_resists_one_extreme_window(self):
        times = [10.0 + 5.0 * index for index in range(22)]
        values = [1.0 + 0.01 * (time - 10.0) for time in times]
        spiked = list(values)
        spiked[7] += 50.0
        self.assertAlmostEqual(ANALYZER.median_pairwise_slopes(times, values), 0.01, places=9)
        self.assertAlmostEqual(ANALYZER.median_pairwise_slopes(times, spiked), 0.01, places=9)

    def test_bootstrap_resamples_contiguous_blocks_with_the_registered_seed(self):
        times = [10.0 + 5.0 * index for index in range(22)]
        values = [2.0 + 0.02 * index for index in range(22)]
        interval = ANALYZER.residual_block_bootstrap(times, values)
        self.assertEqual(interval["status"], "ok")
        self.assertEqual(interval["seed"], 20260917)
        self.assertEqual(interval["block_count"], 3)
        self.assertEqual(interval["repeats"], 1000)
        self.assertEqual(interval["blocks_available"], 8)  # 22 windows / 3
        self.assertEqual(interval["ci_level"], 0.95)
        self.assertIn("never individually resampled", interval["method"])

        blocks = ANALYZER.aligned_residual_blocks(times, list(range(22)), 3)
        for block_times, _ in blocks:
            for earlier, later in zip(block_times, block_times[1:]):
                self.assertEqual(later - earlier, 5.0)

        short = ANALYZER.residual_block_bootstrap([10.0, 15.0], [1.0, 2.0])
        self.assertEqual(short["status"], "insufficient")
        self.assertEqual(short["repeats"], 0)

    def test_threshold_resolution_matches_the_table(self):
        self.assertEqual(ANALYZER.evaluate_threshold("heap_low_mb", 100.0), 32.0)
        self.assertEqual(ANALYZER.evaluate_threshold("heap_low_mb", 400.0), 80.0)
        self.assertEqual(ANALYZER.evaluate_threshold("raf_gap_p95_seconds", 0.017), 0.008)
        self.assertEqual(ANALYZER.evaluate_threshold("cpu_tree_low_cores", 1.4), 0.25)
        self.assertIsNone(ANALYZER.evaluate_threshold("not_a_metric", 1.0))

    def test_cycle_fit_recovers_a_known_amplitude_and_refuses_a_singular_design(self):
        samples = [
            {
                "elapsed_seconds": 600.0 + index * 5.0,
                "value": 1.0 + 0.4 * math.sin(2.0 * math.pi * (600.0 + index * 5.0) / 95.0),
            }
            for index in range(1320)
        ]
        cycle = ANALYZER.fit_cycle_model(samples)
        self.assertEqual(cycle["status"], "ok")
        self.assertAlmostEqual(cycle["amplitude"], 0.4, delta=0.01)
        self.assertAlmostEqual(cycle["amplitude_early"], 0.4, delta=0.02)
        self.assertAlmostEqual(cycle["amplitude_late"], 0.4, delta=0.02)
        self.assertAlmostEqual(cycle["linear_slope_per_minute"], 0.0, delta=1e-6)
        self.assertIn("pre-registered", cycle["frequency_search"])

        empty = ANALYZER.fit_cycle_model([])
        self.assertEqual(empty["status"], "insufficient")
        self.assertIn("usable", empty["reason"])

    def test_normalize_record_keeps_the_synchronous_reading_distinct_from_zero_callbacks(self):
        empty = ANALYZER.normalize_record(
            {
                "elapsed": 0.0,
                "wall": BASE_WALL_EPOCH,
                "cpuByRole": {},
                "cpuTotal": 0.0,
                "renderer": {
                    "frames": 0,
                    "gapP50": None,
                    "gapP95": None,
                    "gapMax": None,
                    "heapMB": None,
                    "domNodes": None,
                    "hidden": False,
                    "visible": True,
                    "rvfc": None,
                },
                "m0": None,
            }
        )
        self.assertEqual(empty["raf_callback_count"], 0)
        self.assertFalse(empty["raf_has_callbacks"])
        self.assertIsNone(empty["raf_gap_p95_seconds"])
        self.assertFalse(empty["rvfc_present"])
        self.assertTrue(empty["rvfc_block_supplied"])
        self.assertTrue(empty["m0_present"] is False)
        # A complete block with every value null is present-but-silent; a block
        # that is missing entirely says so instead.
        self.assertEqual(empty["observed"]["field_null_counts"]["renderer"], 6)
        self.assertEqual(empty["cpu_core_sum"], None)
        self.assertTrue(empty["valid"])
        self.assertFalse(empty["truncated_after_cap"])

    def test_malformed_lines_are_counted_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "lag-samples-20260917T000000Z.jsonl"
            with path.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(synthetic_record(0)) + "\n")
                handle.write("{not json at all\n")
                handle.write(json.dumps(synthetic_record(1)) + "\n")
            records, malformed = ANALYZER._load_jsonl(path)
            self.assertEqual(len(records), 2)
            self.assertEqual(len(malformed), 1)
            self.assertEqual(malformed[0]["line"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
