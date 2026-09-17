#!/usr/bin/env python3
"""Offline "does it get worse the longer it runs?" analyzer (plan v2 section W10 / L1).

This tool reads one captured ``lag-samples-<stamp>[-tag].jsonl`` file plus the
manifest of the experiment that produced it, and answers a single question in a
pre-registered way: *within one fixed-condition 120-minute session, does any
user-visible or resource metric grow with elapsed session time beyond the
thresholds registered before the run?*

Design rules that are load-bearing, not stylistic
-------------------------------------------------
Null is a measurement
    The sampler emits ``null`` for whole blocks (rAF/rVFC distributions, heap,
    DOM nodes, the ``/api/status`` counters) exactly when the page stops being
    observed: window hidden, session idle, no rAF callbacks. Those are the
    minutes this tool exists to report. Filling ``null`` with ``0`` would assert
    "zero gaps, zero heap, zero backlog" for a window nobody watched, and
    dropping the record would delete the worst window from the average. So a
    missing value stays ``None`` all the way into the report, and a
    callback-less window is reported with ``callback_count=0`` *and*
    ``last_callback_age_seconds`` as a gap event.

Theil-Sen plus contiguous-block bootstrap
    A single extreme five-minute window must not steer the fit, so the slope is
    the median of pairwise slopes and the intercept is a median rather than a
    least-squares line. The uncertainty comes from resampling *contiguous*
    residual blocks of 3 windows (15 minutes): neighbouring windows are a time
    series, and drawing their residuals independently would manufacture a
    narrow interval the data does not justify. The 5-second raw samples are
    never individually resampled -- that would treat 1320 autocorrelated points
    as 1320 independent observations.

Three states, never two
    "Evidence insufficient" is a first-class outcome with its own label, and it
    is never reported as "no growth". A two-hour session licenses a bounded
    negative statement about this source, quality and configuration only.

Nothing here is a fault definition
    Every number in :data:`THRESHOLDS` is a pre-registered engineering screen
    chosen before looking at results. They are not repository-measured defect
    limits, and a metric that stays under its screen has not been proven
    leak-free -- only not shown to cross this screen in this run.

Usage::

    py -3.10 prototype/hls-companion/scripts/analyze-duration-degradation.py \\
      --input <this run's only JSONL> --manifest <this experiment's manifest JSON> \\
      --output <this analysis' JSON path>
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

# --------------------------------------------------------------------------
# Pre-registered numbers (plan v2 section W10 section 3). Frozen on purpose:
# they are quoted in the commit message and must not be tuned per run.
# --------------------------------------------------------------------------
SESSION_SECONDS = 7200.0           # 120 minutes of formal observation
WARMUP_SECONDS = 600.0             # first 10 minutes: recorded, never fitted
BASE_SAMPLE_SECONDS = 5.0          # sampler cadence
WINDOW_SECONDS = 300.0             # 5-minute, non-overlapping trend window
FIT_START_SECONDS = 600.0          # minute 10
FIT_END_SECONDS = 7200.0           # minute 120
EARLY_RANGE_SECONDS = (600.0, 1200.0)    # minutes 10-20
LATE_RANGE_SECONDS = (6600.0, 7200.0)    # minutes 110-120
TREND_HORIZON_MINUTES = 110.0      # minutes 10 -> 120, the slope is scaled to this
PLANNED_FIT_RECORDS = 1320         # 22 windows x 60 samples
PLANNED_SESSION_RECORDS = 1440     # what one clean 7200 s run is expected to write
MIN_VALID_RECORDS = 1254           # 95 % of PLANNED_FIT_RECORDS, inclusive
MIN_WINDOW_RECORDS = 54            # 90 % of the 60 samples a full window holds
MIN_QUALIFIED_WINDOWS = 20         # of 22
REQUIRED_WINDOWS_PER_END_RANGE = 2  # each 10-minute end range must carry 2
BLOCK_COUNT = 3                    # contiguous blocks, 3 windows = 15 minutes
BOOTSTRAP_REPEATS = 1000
BOOTSTRAP_SEED = 20260917
CYCLE_SECONDS = 95.0               # the known CPU oscillation, fitted explicitly
GAP_500MS_SECONDS = 0.5
GAP_5000MS_SECONDS = 5.0
OUTPUT_LIMIT_BYTES = 256 * 1024 * 1024
INSTRUMENT_CAP_SECONDS = 7260.0    # 7200 s observation + 60 s exit margin
LATENCY_MIN_WINDOW_SAMPLES = 30
LATENCY_MAX_SAMPLE_AGE_SECONDS = 30.0
TRANSLATION_MIN_ATTEMPTS = 100
DROPPED_MIN_TOTAL_FRAMES = 3000
DEFAULT_BASELINE_FRAME_INTERVAL_SECONDS = 1.0 / 30.0
DEFAULT_EXPECTED_WINDOWS = 22
MIN_STRATUM_WINDOWS = 10           # below this a visibility layer gets no verdict
# The sampler's renderer.gapP50 / gapP95 / gapMax are milliseconds, matching its
# sibling rvfc.gapMaxMs. Reading them as seconds would turn an 18 ms frame gap
# into a false 18 s stall, so every gap is converted once, at normalisation.
RENDERER_GAP_UNIT_SECONDS = 0.001
# The gap-event report is about stretches of at least 0.8 s between callbacks;
# this is a reporting floor only, never a growth threshold.
MIN_REPORTED_GAP_SECONDS = 0.8

# Effect classes. The Chinese strings are the plan's wording and travel into the
# delivered report; the ASCII code is the machine-comparable form.
EFFECT_GROWTH = "检出实质增长候选"
EFFECT_NONE_DETECTED = "在本窗口未检出达到门槛的增长"
EFFECT_INSUFFICIENT = "证据不足或非单调异常"

HYPOTHESIS_SUPPORTED = "支持"
HYPOTHESIS_INSUFFICIENT = "不足"
HYPOTHESIS_REFUTED = "被否证"
HYPOTHESIS_NO_DATA = "数据不足"

VISIBILITY_HIDDEN = "hidden"
VISIBILITY_VISIBLE = "可确认可见"
VISIBILITY_UNKNOWN = "遮挡未知"

# The only negation this tool is allowed to print, and it is deliberately
# narrow: it names the fix commit, the source/quality/configuration, the two
# hours, and the layers it cannot see. "Proved there is no leak" and "the
# slowdown is fixed for good" are outside what one session can establish.
BOUNDED_NEGATIVE_CONCLUSION = (
    "在指定修复提交、该源/画质/配置的两小时会话中，未检出超过预注册门槛的随时间增长；"
    "不覆盖全天、未知遮挡层和未测资源。"
)

FORBIDDEN_CLAIMS = (
    "证明没有泄漏",
    "越跑越卡彻底根治",
    "全天稳定已保证",
)

# Sources whose longest reported callback gap feeds the per-minute gap events.
GAP_SOURCES = (
    ("raf", "raf_gap_max_seconds"),
    ("rvfc", "rvfc_gap_max_seconds"),
)


@dataclass(frozen=True)
class AnalysisParams:
    """Every number the analysis depends on; overridable only by the test harness."""

    sample_seconds: float = BASE_SAMPLE_SECONDS
    window_seconds: float = WINDOW_SECONDS
    warmup_seconds: float = WARMUP_SECONDS
    fit_start_seconds: float = FIT_START_SECONDS
    fit_end_seconds: float = FIT_END_SECONDS
    early_seconds: tuple = EARLY_RANGE_SECONDS
    late_seconds: tuple = LATE_RANGE_SECONDS
    planned_fit_records: int = PLANNED_FIT_RECORDS
    planned_session_records: int = PLANNED_SESSION_RECORDS
    min_valid_records: int = MIN_VALID_RECORDS
    min_window_records: int = MIN_WINDOW_RECORDS
    min_qualified_windows: int = MIN_QUALIFIED_WINDOWS
    required_windows_per_end_range: int = REQUIRED_WINDOWS_PER_END_RANGE
    block_count: int = BLOCK_COUNT
    bootstrap_repeats: int = BOOTSTRAP_REPEATS
    bootstrap_seed: int = BOOTSTRAP_SEED
    cycle_seconds: float = CYCLE_SECONDS
    baseline_frame_interval_seconds: float = DEFAULT_BASELINE_FRAME_INTERVAL_SECONDS
    output_limit_bytes: int = OUTPUT_LIMIT_BYTES
    instrument_cap_seconds: float = INSTRUMENT_CAP_SECONDS
    expected_windows: int = DEFAULT_EXPECTED_WINDOWS
    # Measured, not assumed: how long this analysis itself may run. Kept in the
    # params object so a test can drive a deliberately impossible budget.
    analysis_deadline_seconds: float = INSTRUMENT_CAP_SECONDS


@dataclass(frozen=True)
class Threshold:
    """One pre-registered screen.

    ``rule`` is ``relative_or_absolute`` (the larger of a share of the early
    value and an absolute floor) or ``absolute_delta`` (a flat increase).
    ``level`` says which per-window statistic the screen is applied to: the
    window's *low* level (5th percentile, because the plan asks for the low or
    minimum level, not the GC saw-tooth peak) or the window's median.
    """

    key: str
    label: str
    rule: str
    relative: float = 0.0
    absolute: float = 0.0
    direction: int = 1
    sustained_last_windows: int = 0
    level: str = "low"
    reason: str = ""


THRESHOLDS: dict = {
    # Visible-layer presentation gap, measured by requestVideoFrameCallback.
    # The absolute floor is one video frame period derived from the observed
    # baseline cadence, not a hardcoded 60 fps.
    "rvfc_gap_p95_seconds": Threshold(
        key="rvfc_gap_p95_seconds",
        label="可见层 rVFC 间隔 p95 读数",
        rule="relative_or_absolute",
        relative=0.20,
        absolute=DEFAULT_BASELINE_FRAME_INTERVAL_SECONDS,
        level="low",
        reason="adapts to the real frame rate; one frame period is an interpretable change",
    ),
    "raf_gap_p95_seconds": Threshold(
        key="raf_gap_p95_seconds",
        label="rAF 间隔 p95 读数",
        rule="relative_or_absolute",
        relative=0.20,
        absolute=0.008,
        level="low",
        reason="screens out sub-8 ms timer noise; page callbacks only",
    ),
    "raf_gap_p50_seconds": Threshold(
        key="raf_gap_p50_seconds",
        label="rAF 间隔 p50 读数",
        rule="relative_or_absolute",
        relative=0.20,
        absolute=0.008,
        level="low",
        reason="same screen as p95, kept because the sampler reports both readings",
    ),
    "rvfc_gap_p50_seconds": Threshold(
        key="rvfc_gap_p50_seconds",
        label="rVFC 间隔 p50 读数",
        rule="relative_or_absolute",
        relative=0.20,
        absolute=DEFAULT_BASELINE_FRAME_INTERVAL_SECONDS,
        level="low",
        reason="sampler reports no rVFC p50, so this stays unmeasured unless the field appears",
    ),
    "dropped_frame_ratio_window": Threshold(
        key="dropped_frame_ratio_window",
        label="丢帧/总帧区间比例",
        rule="absolute_delta",
        absolute=0.01,
        level="median",
        reason="a ratio, because cumulative dropped frames must grow with elapsed time",
    ),
    "heap_low_mb": Threshold(
        key="heap_low_mb",
        label="JS heap 低位",
        rule="relative_or_absolute",
        relative=0.20,
        absolute=32.0,
        level="low",
        reason="screens for visible accumulation; it is not evidence of a leak",
    ),
    "dom_nodes_low": Threshold(
        key="dom_nodes_low",
        label="DOM 节点低位",
        rule="relative_or_absolute",
        relative=0.10,
        absolute=200.0,
        level="low",
        reason="suppresses small UI churn; retained objects must still be located separately",
    ),
    "cpu_tree_low_cores": Threshold(
        key="cpu_tree_low_cores",
        label="进程树 CPU 低位",
        rule="absolute_delta",
        absolute=0.25,
        level="low",
        reason="100 % == one core; an interpretable sustained load change",
    ),
    "cpu_renderer_low_cores": Threshold(
        key="cpu_renderer_low_cores",
        label="单角色 CPU（renderer 低位）",
        rule="absolute_delta",
        absolute=0.25,
        level="low",
        reason="same 0.25-core screen, per role the sampler actually reported",
    ),
    "cpu_main_low_cores": Threshold(
        key="cpu_main_low_cores",
        label="单角色 CPU（main 低位）",
        rule="absolute_delta",
        absolute=0.25,
        level="low",
        reason="same 0.25-core screen, per role the sampler actually reported",
    ),
    "cpu_gpu_low_cores": Threshold(
        key="cpu_gpu_low_cores",
        label="单角色 CPU（gpu-process 低位）",
        rule="absolute_delta",
        absolute=0.25,
        level="low",
        reason="same 0.25-core screen, per role the sampler actually reported",
    ),
    "translation_backlog_median_cues": Threshold(
        key="translation_backlog_median_cues",
        label="翻译 backlog",
        rule="absolute_delta",
        absolute=3.0,
        sustained_last_windows=2,
        level="median",
        reason="separates a one-off catch-up burst from a sustained backlog",
    ),
    "translation_queue_delay_p95_median_seconds": Threshold(
        key="translation_queue_delay_p95_median_seconds",
        label="queue lag 读数（滚动 p95 的五分钟中位值）",
        rule="relative_or_absolute",
        relative=0.25,
        absolute=0.5,
        level="median",
        reason="meaningful against a 6 s budget; requires fresh samples",
    ),
    "translation_provider_delay_p95_median_seconds": Threshold(
        key="translation_provider_delay_p95_median_seconds",
        label="provider lag 读数（滚动 p95 的五分钟中位值）",
        rule="relative_or_absolute",
        relative=0.25,
        absolute=0.5,
        level="median",
        reason="meaningful against a 6 s budget; requires fresh samples",
    ),
    "translation_ready_lag_p95_median_seconds": Threshold(
        key="translation_ready_lag_p95_median_seconds",
        label="成功 ready lag 读数（滚动 p95 的五分钟中位值）",
        rule="relative_or_absolute",
        relative=0.25,
        absolute=0.5,
        level="median",
        reason="meaningful against a 6 s budget; requires fresh samples",
    ),
    "translation_failure_ratio_window": Threshold(
        key="translation_failure_ratio_window",
        label="区间翻译失败比例 Δfailures/Δattempts",
        rule="absolute_delta",
        absolute=0.05,
        level="median",
        reason="counter-derived ratio; a zero denominator is not a measurement",
    ),
}

# Metrics read directly off a normalised record field, with the level used for
# the window statistic. Everything else is derived in _window_metrics.
DIRECT_METRIC_FIELDS = {
    "raf_gap_p95_seconds": ("raf_gap_p95_seconds", "low"),
    "raf_gap_p50_seconds": ("raf_gap_p50_seconds", "low"),
    "heap_low_mb": ("heap_mb", "low"),
    "dom_nodes_low": ("dom_nodes", "low"),
    "translation_backlog_median_cues": ("translation_backlog", "median"),
    "translation_queue_delay_p95_median_seconds": (
        "translation_queue_delay_p95_seconds",
        "median",
    ),
    "translation_provider_delay_p95_median_seconds": (
        "translation_provider_delay_p95_seconds",
        "median",
    ),
    "translation_ready_lag_p95_median_seconds": (
        "translation_ready_lag_p95_seconds",
        "median",
    ),
}

# Derived metrics, in report order. Values are filled by _window_metrics.
DERIVED_METRICS = (
    "rvfc_gap_p95_seconds",
    "rvfc_gap_p50_seconds",
    "dropped_frame_ratio_window",
    "cpu_tree_low_cores",
    "cpu_renderer_low_cores",
    "cpu_main_low_cores",
    "cpu_gpu_low_cores",
    "translation_failure_ratio_window",
)

# Report order of every per-window metric.
WINDOW_METRIC_NAMES = (
    "rvfc_gap_p95_seconds",
    "raf_gap_p95_seconds",
    "raf_gap_p50_seconds",
    "rvfc_gap_p50_seconds",
    "dropped_frame_ratio_window",
    "heap_low_mb",
    "dom_nodes_low",
    "cpu_tree_low_cores",
    "cpu_renderer_low_cores",
    "cpu_main_low_cores",
    "cpu_gpu_low_cores",
    "translation_backlog_median_cues",
    "translation_queue_delay_p95_median_seconds",
    "translation_provider_delay_p95_median_seconds",
    "translation_ready_lag_p95_median_seconds",
    "translation_failure_ratio_window",
)

# Metrics whose fit is blocked while stage statistics are stale: fitting a
# rolling p95 that nobody refreshed is not a measurement of this minute.
STAGE_LATENCY_METRICS = (
    "translation_queue_delay_p95_median_seconds",
    "translation_provider_delay_p95_median_seconds",
    "translation_ready_lag_p95_median_seconds",
)

# The six hypotheses of plan v2 section W10 section 3 L1-f. Each entry carries
# the shape that would support it and -- scoped to this run only -- what would
# make it insufficient or refuted. Static, because these are the questions
# frozen before collection.
HYPOTHESES = (
    {
        "id": "a",
        "claim": "U3 已完全随 GPU 修复消失",
        "supported_shape": "修复后同配置长跑中，主要用户指标满足有限负结果，资源不显著累积",
        "refuting_shape": "修复后仍出现可重复、条件相同的实质时间增长，即否证“GPU 修复解释全部”",
        "scope_note": "一次通过只支持本两小时／本配置，不证明所有机器和全天",
    },
    {
        "id": "b",
        "claim": "真实对象／资源泄漏",
        "supported_shape": "heap/DOM/私有内存低位持续增长，并有对应持有对象或集合的额外证据",
        "refuting_shape": "已测对象低位有界而主要用户指标仍恶化，使这些对象的泄漏解释不足",
        "scope_note": "JS heap 稳定不能否证未测的 GPU／native 泄漏",
    },
    {
        "id": "c",
        "claim": "约 95 秒周期而不增长",
        "supported_shape": "振幅和均值稳定、去周期后的斜率接近零，观众指标没有累积恶化",
        "refuting_shape": "均值／振幅随时间增加，或去周期后仍有实质用户指标增长",
        "scope_note": "单一固定 95 秒频率，不在几十个频率里挑最显著者",
    },
    {
        "id": "d",
        "claim": "字幕／翻译队列累积",
        "supported_shape": "backlog 及 queue delay 持续升、deadline/drop 增，和症状时间对齐",
        "refuting_shape": "队列长度与延迟稳定、有新鲜样本而 UI 持续恶化，限制队列累积解释",
        "scope_note": "静态 p95 陈旧不能算稳定证据",
    },
    {
        "id": "e",
        "claim": "PTS 回绕或重置",
        "supported_shape": "事件发生时 raw PTS 跨边界／discontinuity，offset 或 extent 离散改变",
        "refuting_shape": "症状时 PTS 远离边界、源时钟映射连续，否证该次事件的回绕解释",
        "scope_note": "不能因应用才运行半小时就排除源接近回绕",
    },
    {
        "id": "f",
        "claim": "下载腿随时间劣化",
        "supported_shape": "在类似画质负载下，字节／源 PTS 推进落后，idle 和缓冲问题累积，先于下游症状",
        "refuting_shape": "源交付与 PTS 进度持续正常而下游仍增长，使下载腿解释不足",
        "scope_note": "下游反压也会影响源 idle，只有时间相关不能区分网络与下游责任",
    },
)


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------
def _get(container, key):
    """Read ``key`` from a mapping; anything that is not a mapping has no keys.

    A whole block may arrive as ``null``. That is a real observation and must
    surface as ``None`` rather than as an absent key with an implicit default.
    """
    if isinstance(container, dict):
        return container.get(key)
    return None


def _num(value):
    """Return *value* as a float, or ``None`` for null/absent/non-numeric.

    Deliberately never returns 0.0 for a missing value: ``0`` and ``null`` mean
    different things here (a measured zero versus nothing observed), and several
    plan rules depend on that difference.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return None
        return result
    return None


def _bool_or_none(value):
    return value if isinstance(value, bool) else None


def _int_or_none(value):
    number = _num(value)
    return None if number is None else int(number)


def _list_or_none(value):
    return value if isinstance(value, list) else None


def _milliseconds_to_seconds(value):
    """Convert a sampler millisecond reading, preserving null.

    The renderer gap fields are milliseconds (their sibling rvfc field is named
    ``gapMaxMs``). Keeping the conversion in one place is what stops a plausible
    milliseconds-as-seconds error from inventing multi-second stalls.
    """
    number = _num(value)
    return None if number is None else number * RENDERER_GAP_UNIT_SECONDS


def _load_jsonl(path: Path):
    """Stream one JSON object per line.

    Returns ``(records, malformed)``. A malformed line is reported by line
    number and never silently dropped: a truncated tail is itself evidence
    about the instrument.
    """
    records: list = []
    malformed: list = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as error:
                malformed.append({"line": line_number, "error": str(error)})
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
            else:
                malformed.append({"line": line_number, "error": "line is not a JSON object"})
    return records, malformed


def _normalize_visibility_events(raw_events):
    events: list = []
    for event in raw_events or []:
        if not isinstance(event, dict):
            continue
        events.append(
            {
                "type": event.get("type"),
                "hidden": _bool_or_none(event.get("hidden")),
                "at": _num(event.get("at")),
            }
        )
    return events


def _classify_visibility(record, notes):
    """Three layers, and never collapse one into another.

    ``blur`` is not ``hidden``: a focused-away but visible window still paints,
    so treating blur as hidden would quietly move windows between strata. An
    occlusion reading that was never taken is *unknown*, which is not
    "unoccluded" -- but a window that is not hidden and in which the page
    demonstrably kept getting rAF callbacks is as close to "confirmably visible"
    as this sampler can establish, so that is the label it gets.
    """
    hidden = record.get("document_hidden")
    if hidden is None:
        notes.append("record carried no document.hidden flag; visibility layer unknown")
        return VISIBILITY_UNKNOWN
    if hidden is True:
        return VISIBILITY_HIDDEN
    visible_flag = record.get("document_visible")
    obscured = record.get("document_obscured")
    if visible_flag is False or obscured is True:
        notes.append("window not confirmably visible (visible=false or occluded=true)")
        return VISIBILITY_UNKNOWN
    callbacks = record.get("raf_has_callbacks") or record.get("rvfc_has_callbacks")
    if visible_flag is True or callbacks is True:
        if obscured is None:
            notes.append(
                "not hidden and callbacks kept arriving, so the visible layer is confirmable; "
                "occlusion was never sampled, so the occlusion-unknown layer is reported separately"
            )
        return VISIBILITY_VISIBLE
    notes.append(
        "not hidden but no callback arrived to confirm painting, and occlusion was not sampled; "
        "this window stays in the occlusion-unknown layer rather than being counted as visible"
    )
    return VISIBILITY_UNKNOWN


def _null_field_counts(raw) -> dict:
    """Count, per block, how many fields arrived as an explicit null.

    This is the receipt that "null at the worst moment" is a property of the
    data rather than of this tool.
    """
    counts = {"renderer": 0, "rvfc": 0, "m0": 0, "cpuByRole": 0}
    for block in ("renderer", "m0", "cpuByRole"):
        container = raw.get(block)
        if isinstance(container, dict):
            counts[block] = sum(1 for value in container.values() if value is None)
    rvfc = _get(raw.get("renderer"), "rvfc")
    if isinstance(rvfc, dict):
        counts["rvfc"] = sum(1 for value in rvfc.values() if value is None)
    return counts


def _stage_sample_state(record) -> str:
    """Freshness of the rolling stage statistics, per plan section L1-d.

    The stage numbers are rolling-window readings. A stale reading, or one built
    from too few samples, is not a measurement of this minute, so it must not
    enter a slope fit as if it were.
    """
    window_samples = record.get("latency_window_samples")
    age = record.get("latency_sample_age_seconds")
    if window_samples is None and age is None:
        return "unmeasured"
    if window_samples is None or age is None:
        return "incomplete"
    if window_samples < LATENCY_MIN_WINDOW_SAMPLES:
        return "too_few_samples"
    if age > LATENCY_MAX_SAMPLE_AGE_SECONDS:
        return "stale"
    return "fresh"


def normalize_record(raw, index: int = 0, params: AnalysisParams = None):
    """Turn one sampler line into a flat, lossless analysis record.

    Every distinction the plan depends on is preserved: null versus zero,
    present-block-with-null-sub-block versus absent block, and the difference
    between a synchronous callback-to-callback gap distribution and the time
    from an arbitrary 5-second sample to the next reported gap above a
    threshold. The two answer different questions; averaging them would answer
    neither.
    """
    params = params or AnalysisParams()
    if not isinstance(raw, dict):
        raw = {}
    notes: list = []
    record: dict = {"index": index}

    elapsed = _num(raw.get("elapsed"))
    wall = _num(raw.get("wall"))
    record["elapsed_seconds"] = elapsed
    record["wall_epoch_seconds"] = wall

    raw_cpu = raw.get("cpuByRole")
    cpu_by_role: dict = {}
    if isinstance(raw_cpu, dict):
        for role, value in raw_cpu.items():
            cpu_by_role[str(role)] = _num(value)
    else:
        notes.append("cpuByRole absent or not an object; per-role CPU is unmeasured")
    record["cpu_by_role"] = cpu_by_role
    record["cpu_total"] = _num(raw.get("cpuTotal"))
    numeric_cpu = [value for value in cpu_by_role.values() if value is not None]
    # None, not 0.0: an empty cpuByRole means "no role was read", which must not
    # be scored as a quiet, idle process tree.
    record["cpu_core_sum"] = sum(numeric_cpu) if numeric_cpu else None

    session_keys = {
        "session_id": raw.get("session_id") or raw.get("media_session_id") or raw.get("sessionId"),
        "code_sha": raw.get("code_sha") or raw.get("codeSha"),
        "config_fingerprint": raw.get("config_fingerprint") or raw.get("configFingerprint"),
        "driver_pid": raw.get("driver_pid") or raw.get("driverPid"),
    }
    for key, value in session_keys.items():
        record[key] = value
    record["processes"] = raw.get("processes") if isinstance(raw.get("processes"), dict) else {}

    raw_renderer = raw.get("renderer")
    renderer_present = isinstance(raw_renderer, dict)
    if not renderer_present:
        notes.append(
            "renderer block was null: no rAF/rVFC distribution, heap or DOM reading this sample"
        )
    renderer = raw_renderer if renderer_present else {}

    raf_callback_count = _int_or_none(_get(renderer, "frames"))
    record["raf_callback_count"] = raf_callback_count
    record["raf_present"] = raf_callback_count is not None
    record["raf_has_callbacks"] = (
        bool(raf_callback_count) if raf_callback_count is not None else None
    )
    record["raf_gap_p50_seconds"] = _milliseconds_to_seconds(_get(renderer, "gapP50"))
    record["raf_gap_p95_seconds"] = _milliseconds_to_seconds(_get(renderer, "gapP95"))
    record["raf_gap_max_seconds"] = _milliseconds_to_seconds(_get(renderer, "gapMax"))
    record["raf_over_100ms"] = _int_or_none(_get(renderer, "over100ms"))
    record["raf_over_500ms"] = _int_or_none(_get(renderer, "over500ms"))
    record["heap_mb"] = _num(_get(renderer, "heapMB"))
    record["dom_nodes"] = _int_or_none(_get(renderer, "domNodes"))
    record["current_time"] = _num(_get(renderer, "currentTime"))
    record["paused"] = _bool_or_none(_get(renderer, "paused"))
    record["ready_state"] = _int_or_none(_get(renderer, "readyState"))
    record["buffered_ahead"] = _num(_get(renderer, "bufferedAhead"))
    record["dropped_frames_total"] = _int_or_none(_get(renderer, "dropped"))
    record["total_video_frames"] = _int_or_none(_get(renderer, "totalVideoFrames"))
    record["long_tasks"] = _int_or_none(_get(renderer, "longTasks"))
    record["long_task_ms"] = _num(_get(renderer, "longTaskMs"))
    record["long_task_max_ms"] = _num(_get(renderer, "longTaskMaxMs"))

    record["visibility_events"] = _normalize_visibility_events(
        _list_or_none(_get(renderer, "visibilityEvents"))
    )
    hidden = _bool_or_none(_get(renderer, "hidden"))
    record["document_hidden"] = hidden
    obscured = _bool_or_none(_get(renderer, "occluded"))
    record["document_obscured"] = obscured
    record["document_visible"] = _bool_or_none(_get(renderer, "visible"))
    record["occlusion_measured"] = obscured is not None

    raw_rvfc = _get(renderer, "rvfc")
    rvfc_present = isinstance(raw_rvfc, dict)
    rvfc = raw_rvfc if rvfc_present else {}
    if not rvfc_present:
        notes.append("rVFC block absent: no presentation-interval reading this sample")
    rvfc_count = _int_or_none(_get(rvfc, "count"))
    record["rvfc_present"] = rvfc_present
    record["rvfc_block_supplied"] = rvfc_present or (renderer_present and raw_rvfc is None)
    record["rvfc_count"] = rvfc_count
    record["rvfc_has_callbacks"] = bool(rvfc_count) if rvfc_count is not None else None
    # gapMaxMs is milliseconds; every gap comparison in this tool is in seconds,
    # and reading it as seconds turns an 18 ms frame gap into a false >5 s stall.
    gap_max_ms = _num(_get(rvfc, "gapMaxMs"))
    record["rvfc_gap_max_ms"] = gap_max_ms
    record["rvfc_gap_max_seconds"] = gap_max_ms / 1000.0 if gap_max_ms is not None else None
    record["rvfc_over_100"] = _int_or_none(_get(rvfc, "over100"))
    record["rvfc_over_500"] = _int_or_none(_get(rvfc, "over500"))
    record["rvfc_media_time"] = _num(_get(rvfc, "mediaTime"))
    record["rvfc_presented_frames"] = _int_or_none(_get(rvfc, "presentedFrames"))
    record["rvfc_expected_display_time"] = _num(_get(rvfc, "expectedDisplayTime"))
    record["rvfc_supported"] = _bool_or_none(_get(rvfc, "supported"))
    record["rvfc_attached"] = _bool_or_none(_get(rvfc, "attached"))
    # rVFC exposes a maximum and coarse over-threshold counts, never a p95. The
    # reading below is a derived approximation and is flagged as such.
    record["rvfc_gap_p95_seconds"] = _rvfc_p95_reading(record)

    # Both callback readings now exist, so the visibility layer can be decided.
    record["visibility_layer"] = _classify_visibility(record, notes)

    if record["raf_has_callbacks"] is False:
        notes.append(
            "rAF reported 0 callbacks in this interval: the p95 stays null and is reported "
            "through callback_count=0 plus last_callback_age_seconds"
        )
    if record["rvfc_has_callbacks"] is False:
        notes.append("rVFC reported 0 presentations in this interval: the reading stays null")

    raw_m0 = raw.get("m0")
    m0_present = isinstance(raw_m0, dict)
    if not m0_present:
        notes.append("m0 status block was null: no counter or stage reading this sample")
    m0 = raw_m0 if m0_present else {}
    record["m0_present"] = m0_present
    record["m0_wall_epoch_seconds"] = _num(_get(m0, "wall"))
    record["session_state"] = _get(m0, "state")
    record["pdt_epoch"] = _num(_get(m0, "pdtEpoch"))
    record["asr_provider_id"] = _get(m0, "asrProviderId")
    record["translation_provider_id"] = _get(m0, "translationProviderId")
    record["target_language"] = _get(m0, "targetLanguage")
    record["source_language_policy"] = _get(m0, "sourceLanguagePolicy")
    # Counters accumulate for the whole session; they are only ever used as
    # interval differences below, never fitted as levels.
    record["translation_attempts_total"] = _int_or_none(_get(m0, "translationAttempts"))
    record["translation_failures_total"] = _int_or_none(_get(m0, "translationFailures"))
    record["translation_provider_failures_total"] = _int_or_none(
        _get(m0, "translationProviderFailures")
    )
    record["translation_deadline_expired_total"] = _int_or_none(
        _get(m0, "translationDeadlineExpired")
    )
    record["translation_dropped_total"] = _int_or_none(_get(m0, "translationDropped"))
    record["source_only_cues_total"] = _int_or_none(_get(m0, "sourceOnlyCues"))
    record["translation_backlog"] = _num(_get(m0, "translationBacklog"))
    record["translation_queue_delay_p50_seconds"] = _num(_get(m0, "translationQueueDelayP50"))
    record["translation_queue_delay_p95_seconds"] = _num(_get(m0, "translationQueueDelayP95"))
    record["translation_provider_delay_p50_seconds"] = _num(
        _get(m0, "translationProviderDelayP50")
    )
    record["translation_provider_delay_p95_seconds"] = _num(
        _get(m0, "translationProviderDelayP95")
    )
    record["translation_ready_lag_p50_seconds"] = _num(_get(m0, "translationSuccessReadyLagP50"))
    record["translation_ready_lag_p95_seconds"] = _num(_get(m0, "translationSuccessReadyLagP95"))
    record["latency_samples"] = _int_or_none(_get(m0, "latencySamples"))
    record["latency_window_samples"] = _int_or_none(_get(m0, "latencyWindowSamples"))
    record["latency_sample_age_seconds"] = _num(_get(m0, "latencySampleAgeSeconds"))
    record["latency_unknown"] = _int_or_none(_get(m0, "latencyUnknown"))
    record["last_translation_error"] = _get(m0, "lastTranslationError")

    record["stage_sample_state"] = _stage_sample_state(record)
    record["stage_sample_fresh"] = record["stage_sample_state"] == "fresh"
    record["last_callback_age_seconds"] = None  # filled per window, xN callbacks

    record["observed"] = {
        "cpu_roles": sorted(cpu_by_role.keys()),
        "renderer_present": renderer_present,
        "rvfc_present": rvfc_present,
        "m0_present": m0_present,
        "raf_callbacks_present": record["raf_has_callbacks"],
        "rvfc_callbacks_present": record["rvfc_has_callbacks"],
        "field_null_counts": _null_field_counts(raw),
        "notes": notes,
    }
    truncated = elapsed is not None and elapsed > params.instrument_cap_seconds
    record["truncated_after_cap"] = truncated
    # A record past the instrument cap is not part of a 120-minute window; it is
    # the signature of a run that failed to terminate on its own.
    record["valid"] = elapsed is not None and wall is not None and not truncated
    return record


def _rvfc_p95_reading(record):
    """Approximate presentation-interval p95, or ``None``.

    rVFC supplies a maximum and coarse over-threshold counts, so this
    reconstructs a reading from those. It is never manufactured when the block
    is null: ``None`` out is the honest answer for "nobody presented a frame".
    """
    count = record.get("rvfc_count")
    over500 = record.get("rvfc_over_500")
    over100 = record.get("rvfc_over_100")
    if not count or over500 is None or over100 is None:
        return None
    if over500 / count > 0.05:
        return max(record.get("rvfc_gap_max_seconds") or 0.0, GAP_500MS_SECONDS)
    if over100 / count > 0.05:
        return max(record.get("rvfc_gap_max_seconds") or 0.0, 0.1)
    return None


# --------------------------------------------------------------------------
# Segmentation: one session, one configuration, one clock
# --------------------------------------------------------------------------
def _identity(record) -> dict:
    """Session identity, preferring the explicit media-session identity.

    ``session_id`` from the sampler wins; pdtEpoch, provider and language stay
    as cross-checks, because a lone pdtEpoch can repeat across days and is not a
    universal session key.
    """
    return {
        "session_id": record.get("session_id"),
        "code_sha": record.get("code_sha"),
        "config_fingerprint": record.get("config_fingerprint"),
        "pdt_epoch": record.get("pdt_epoch"),
        "translation_provider_id": record.get("translation_provider_id"),
        "target_language": record.get("target_language"),
    }


def _pid_map(record) -> dict:
    raw = record.get("processes")
    result: dict = {}
    if isinstance(raw, dict):
        for role, entry in raw.items():
            if isinstance(entry, dict):
                result[str(role)] = (
                    entry.get("pid"),
                    entry.get("createTime") or entry.get("created"),
                )
            else:
                result[str(role)] = (entry, None)
    return result


def _clock_events(previous, current, params: AnalysisParams) -> list:
    """Detect the discontinuities that must break an analysis segment.

    A monotonic clock that steps, or a wall clock diverging from the monotonic
    one, is a measurement fault rather than a performance signal. A source media
    time step is the discrete event class owned by R1. Both are recorded, and
    both split the segment: one full-range slope cannot describe either.
    """
    events: list = []
    if previous is None or current is None:
        return events
    prev_elapsed = previous.get("elapsed_seconds")
    elapsed = current.get("elapsed_seconds")
    prev_wall = previous.get("wall_epoch_seconds")
    wall = current.get("wall_epoch_seconds")
    if prev_elapsed is not None and elapsed is not None:
        delta = elapsed - prev_elapsed
        if delta < 0:
            events.append(
                {"kind": "monotonic_step_back", "delta_seconds": delta, "at_elapsed": prev_elapsed}
            )
        elif delta > params.sample_seconds * 3.0:
            events.append(
                {
                    "kind": "sampling_gap",
                    "delta_seconds": delta,
                    "at_elapsed": prev_elapsed,
                    "note": "far beyond the 5 s cadence; a discontinuity, not duration",
                }
            )
        if prev_wall is not None and wall is not None and delta > 0:
            wall_delta = wall - prev_wall
            if abs(wall_delta - delta) > 2.0 * params.sample_seconds:
                events.append(
                    {
                        "kind": "clock_divergence",
                        "monotonic_delta_seconds": delta,
                        "wall_delta_seconds": wall_delta,
                        "at_elapsed": prev_elapsed,
                    }
                )
    prev_time = previous.get("current_time")
    current_time = current.get("current_time")
    if prev_time is not None and current_time is not None:
        step = current_time - prev_time
        # A backwards or implausibly large forward step in the media timeline is
        # the discrete PTS-boundary signature: wrap, reset or seek.
        if step < -1.0 or step > 5.0 * params.sample_seconds:
            events.append(
                {
                    "kind": "media_time_step",
                    "delta_seconds": step,
                    "from_current_time": prev_time,
                    "to_current_time": current_time,
                    "at_elapsed": prev_elapsed,
                    "candidate_owner": "R1 discrete clock event, not a smooth leak",
                }
            )
    return events


def _split_reason(previous, identity, counters, pids, record, params):
    """First reason this record cannot share a segment with its predecessor."""
    new_identity = _identity(record)
    new_counters = (
        record.get("translation_attempts_total"),
        record.get("translation_failures_total"),
        record.get("translation_deadline_expired_total"),
        record.get("translation_dropped_total"),
    )
    new_pids = _pid_map(record)
    reason = None
    events: list = []
    if previous is not None:
        for key, label in (
            ("session_id", "session identity changed"),
            ("code_sha", "code SHA changed"),
            ("config_fingerprint", "configuration fingerprint changed"),
            ("pdt_epoch", "pdtEpoch changed"),
            ("translation_provider_id", "translation provider changed"),
            ("target_language", "target language changed"),
        ):
            before, after = identity.get(key), new_identity.get(key)
            if before is not None and after is not None and before != after:
                reason = f"{label} ({before!r} -> {after!r})"
                break
        if reason is None and counters is not None:
            for position, label in (
                (0, "attempt counter"),
                (1, "failure counter"),
                (2, "deadline-expired counter"),
                (3, "dropped counter"),
            ):
                if (
                    counters[position] is not None
                    and new_counters[position] is not None
                    and new_counters[position] < counters[position]
                ):
                    reason = f"{label} went backwards (reset), so the run is not one session"
                    break
        if reason is None and pids:
            for role, (pid, created) in new_pids.items():
                before = pids.get(role)
                if before and before[0] is not None and pid is not None and before[0] == pid:
                    if before[1] is not None and created is not None and before[1] != created:
                        reason = f"PID {pid} reused for role {role} with a different creation time"
                        break
        if reason is None:
            events = _clock_events(previous, record, params)
            if events:
                reason = "; ".join(event["kind"] for event in events)
    return reason, events, new_identity, new_counters, new_pids


def split_segments(records, params: AnalysisParams = None) -> list:
    """Split raw sampler lines into segments that may each be called one condition.

    Session identity, code, configuration, counter resets, PID reuse and clock
    discontinuities all break the run. Gluing them together would compare two
    populations and call the difference "time", which is the error this rule
    exists to prevent. Each segment keeps both the raw line and its normalised
    form, so normalisation happens exactly once.
    """
    params = params or AnalysisParams()
    segments: list = []
    current_raw: list = []
    current_norm: list = []
    previous = None
    identity: dict = {}
    counters = None
    pids: dict = {}

    def close(reason, at_elapsed):
        if current_norm:
            segments.append(
                {
                    "index": len(segments),
                    "reason_started": current_norm[0].get("segment_reason", "start"),
                    "reason_closed": reason,
                    "closed_at_elapsed_seconds": at_elapsed,
                    "records": current_norm,
                    "raw_records": current_raw,
                    "windows": [],
                }
            )

    for index, raw in enumerate(records):
        record = normalize_record(raw, index, params)
        reason, events, new_identity, new_counters, new_pids = _split_reason(
            previous, identity, counters, pids, record, params
        )
        if reason is not None:
            close(reason, previous.get("elapsed_seconds") if previous else None)
            current_raw = []
            current_norm = []
            record["segment_reason"] = reason
            record["clock_events"] = events
        elif not current_norm:
            record["segment_reason"] = "start"
        current_raw.append(raw)
        current_norm.append(record)
        identity = new_identity
        counters = new_counters
        pids = new_pids
        previous = record

    close("end of file", previous.get("elapsed_seconds") if previous else None)
    return segments


# --------------------------------------------------------------------------
# Windows and per-window metrics
# --------------------------------------------------------------------------
def _percentile(values, fraction):
    """Type-7 percentile; ``None`` for an empty population."""
    ordered = sorted(values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return float(ordered[lower] * (1.0 - weight) + ordered[upper] * weight)


def _low(values):
    """A window's low level: 5th percentile of the samples it actually has.

    The plan asks for the low level, not the peak, precisely because a forced or
    incidental GC spike must not be read as resource growth.
    """
    return _percentile(values, 0.05)


def _plan_window_starts(params: AnalysisParams) -> list:
    starts: list = []
    start = params.fit_start_seconds
    while start + params.window_seconds <= params.fit_end_seconds + 1e-9:
        starts.append(start)
        start += params.window_seconds
    return starts


def _counter_delta(samples, field):
    """Interval difference across the samples that carry the counter.

    Cumulative counters are never fitted as levels: they can only grow, so a
    positive slope would be guaranteed by arithmetic rather than by behaviour.
    A reset inside the window yields ``None`` rather than a zero delta, because
    zero would hide the reset.
    """
    values = [sample.get(field) for sample in samples if sample.get(field) is not None]
    if len(values) < 2:
        return None
    delta = values[-1] - values[0]
    return None if delta < 0 else delta


def _number_min(samples, field):
    values = [sample.get(field) for sample in samples if sample.get(field) is not None]
    return min(values) if values else None


def _paused_fraction(samples):
    flags = [sample.get("paused") for sample in samples if sample.get("paused") is not None]
    if not flags:
        return None
    return sum(1 for flag in flags if flag) / len(flags)


def _time_to_threshold(samples, index, threshold_seconds, params):
    """Seconds from sample *index* to the next interval that recorded no callback.

    An interval with zero callbacks is itself the observable event -- its p95 is
    null, so without this measure it would contribute nothing to the window at
    all instead of the emptiness it actually recorded. A reported gap also
    counts, but only above ``MIN_REPORTED_GAP_SECONDS``, because a healthy 30 fps
    stream still reports a maximum frame gap in the tens of milliseconds.
    """
    for offset in range(index + 1, len(samples)):
        later = samples[offset]
        if later.get("raf_has_callbacks") is False or later.get("rvfc_has_callbacks") is False:
            return (offset - index) * params.sample_seconds
        observed = [
            value
            for value in (later.get("raf_gap_max_seconds"), later.get("rvfc_gap_max_seconds"))
            if value is not None and value >= MIN_REPORTED_GAP_SECONDS
        ]
        if any(value > threshold_seconds for value in observed):
            return (offset - index) * params.sample_seconds
    return None


def _window_metrics(samples, params: AnalysisParams) -> dict:
    """Per-window metrics for callbacks, resources, counters, visibility and CPU.

    A rolling p95 is never averaged into a session-wide p95 here. The stage
    metrics are reported as the median of the five-minute rolling readings,
    which is the strongest claim this sampler's data supports.
    """
    metrics: dict = {
        "sample_count": len(samples),
        "valid_count": sum(1 for sample in samples if sample.get("valid")),
        "raf_callback_count": sum(
            sample["raf_callback_count"]
            for sample in samples
            if sample.get("raf_callback_count") is not None
        ),
        "raf_samples_present": sum(1 for sample in samples if sample.get("raf_present")),
        "raf_synchronous_minutes": sum(
            1
            for sample in samples
            if sample.get("raf_present") and not sample.get("raf_has_callbacks")
        ),
        "rvfc_count": sum(
            sample["rvfc_count"] for sample in samples if sample.get("rvfc_count") is not None
        ),
        "rvfc_samples_present": sum(1 for sample in samples if sample.get("rvfc_present")),
        "rvfc_synchronous_minutes": sum(
            1
            for sample in samples
            if sample.get("rvfc_present") and not sample.get("rvfc_has_callbacks")
        ),
        "fresh_latency_windows": sum(
            1 for sample in samples if sample.get("stage_sample_fresh") is True
        ),
        "stale_latency_windows": sum(
            1
            for sample in samples
            if sample.get("stage_sample_state") in {"stale", "too_few_samples", "incomplete"}
        ),
    }

    # Distance from each sample to the most recent interval in which a callback
    # demonstrably ran. A callback-less window ends with this age attached, so
    # "0 callbacks" is never mistaken for "nothing to report".
    last_callback_age = None
    for sample in samples:
        if sample.get("raf_has_callbacks") or sample.get("rvfc_has_callbacks"):
            last_callback_age = 0.0
        elif last_callback_age is not None:
            last_callback_age += params.sample_seconds
        sample["last_callback_age_seconds"] = last_callback_age
    metrics["last_callback_age_seconds"] = last_callback_age
    metrics["last_callback_age_never_measured"] = last_callback_age is None

    metrics["time_to_500ms_gap_seconds"] = _low(
        [
            value
            for value in (
                _time_to_threshold(samples, index, GAP_500MS_SECONDS, params)
                for index in range(len(samples))
            )
            if value is not None
        ]
    )
    metrics["time_to_5000ms_gap_seconds"] = _low(
        [
            value
            for value in (
                _time_to_threshold(samples, index, GAP_5000MS_SECONDS, params)
                for index in range(len(samples))
            )
            if value is not None
        ]
    )

    for name, (field, level) in DIRECT_METRIC_FIELDS.items():
        values = [sample.get(field) for sample in samples]
        present = [value for value in values if value is not None]
        metrics[name] = _low(present) if level == "low" else (
            statistics.median(present) if present else None
        )
        metrics[f"{name}__samples"] = len(present)

    for name in DERIVED_METRICS:
        metrics[name] = None
        metrics[f"{name}__samples"] = 0

    for role, name in (
        ("renderer", "cpu_renderer_low_cores"),
        ("main", "cpu_main_low_cores"),
        ("gpu-process", "cpu_gpu_low_cores"),
    ):
        values = [
            sample["cpu_by_role"].get(role)
            for sample in samples
            if sample.get("cpu_by_role", {}).get(role) is not None
        ]
        metrics[name] = _low(values) if values else None
        metrics[f"{name}__samples"] = len(values)

    tree_values = [sample.get("cpu_core_sum") for sample in samples]
    tree_values = [value for value in tree_values if value is not None]
    metrics["cpu_tree_low_cores"] = _low(tree_values) if tree_values else None
    metrics["cpu_tree_low_cores__samples"] = len(tree_values)

    rvfc_p95_values = [
        sample.get("rvfc_gap_p95_seconds")
        for sample in samples
        if sample.get("rvfc_gap_p95_seconds") is not None
    ]
    metrics["rvfc_gap_p95_seconds"] = _low(rvfc_p95_values) if rvfc_p95_values else None
    metrics["rvfc_gap_p95_seconds__samples"] = len(rvfc_p95_values)
    # The sampler records no rVFC p50 field; the metric exists so the absence is
    # visible in the report instead of silently missing from the metric list.
    metrics["rvfc_gap_p50_seconds"] = None
    metrics["rvfc_gap_p50_seconds__samples"] = 0

    dropped = _counter_delta(samples, "dropped_frames_total")
    total_frames = _counter_delta(samples, "total_video_frames")
    ratio = dropped / total_frames if (dropped is not None and total_frames) else None
    metrics["dropped_frame_ratio_window"] = ratio
    metrics["dropped_frame_ratio_window__samples"] = 1 if ratio is not None else 0

    failures = _counter_delta(samples, "translation_failures_total")
    attempts = _counter_delta(samples, "translation_attempts_total")
    # A zero denominator is not a measurement of a zero failure rate.
    failure_ratio = failures / attempts if (failures is not None and attempts) else None
    metrics["translation_failure_ratio_window"] = failure_ratio
    metrics["translation_failure_ratio_window__samples"] = 1 if failure_ratio is not None else 0

    metrics["translation_failure_delta"] = failures
    metrics["translation_attempt_delta"] = attempts
    # Deadline expiry and drops are separate populations from failures; they are
    # reported individually and never summed into a "total failure rate".
    metrics["deadline_expired_delta"] = _counter_delta(
        samples, "translation_deadline_expired_total"
    )
    metrics["translation_dropped_delta"] = _counter_delta(samples, "translation_dropped_total")
    metrics["source_only_cues_delta"] = _counter_delta(samples, "source_only_cues_total")
    metrics["backlog_drained_to_zero"] = any(
        sample.get("translation_backlog") == 0 for sample in samples
    )
    metrics["dropped_frame_total_delta"] = dropped
    metrics["video_frame_total_delta"] = total_frames
    metrics["long_tasks_delta"] = _counter_delta(samples, "long_tasks")
    metrics["buffered_ahead_min_seconds"] = _number_min(samples, "buffered_ahead")
    metrics["media_time_delta_seconds"] = _counter_delta(samples, "current_time")
    metrics["paused_fraction"] = _paused_fraction(samples)
    return metrics


def _window_coverage(window) -> dict:
    metrics = window["metrics"]
    present = [
        metrics.get(f"{name}__samples", 0)
        for name in WINDOW_METRIC_NAMES
        if THRESHOLDS[name].level == "low"
    ]
    return {
        "samples": metrics["sample_count"],
        "valid": metrics["valid_count"],
        "metrics_with_readings": sum(1 for count in present if count > 0),
        "metrics_expected": len(present),
        "thinnest_metric_readings": min(present) if present else 0,
    }


class WindowSet:
    """Windowed view of one run plus the metric series the fitter consumes."""

    def __init__(self, params: AnalysisParams, windows: list, segments: list):
        self.params = params
        self.windows = windows
        self.segments = segments
        self.gate: dict = {}

    @property
    def fit_windows(self) -> list:
        return [window for window in self.windows if window["in_fit_range"]]

    @property
    def time_minutes(self) -> list:
        """Session-monotonic run minutes of every fit-range window."""
        return [window["time_minutes"] for window in self.fit_windows]

    def values(self, metric: str, qualified_only: bool = True) -> list:
        result: list = []
        for window in self.fit_windows:
            if qualified_only and not window["qualified"]:
                continue
            value = window["metrics"].get(metric)
            if value is not None:
                result.append(value)
        return result

    @property
    def metric(self):
        """The plan's sketch indexes ``blocks.metric``; default to CPU-tree low.

        Callers should prefer :meth:`series`, which names the metric explicitly
        instead of relying on a default.
        """
        return self.values("cpu_tree_low_cores")

    def series(self, metric: str, qualified_only: bool = True):
        """Aligned ``(time_minutes, values)`` for one metric.

        Unqualified windows are dropped rather than interpolated: a window that
        did not reach 54/60 valid records is not evidence, and filling it from
        its neighbours would invent the very slope it is meant to measure.
        """
        times: list = []
        values: list = []
        for window in self.fit_windows:
            if qualified_only and not window["qualified"]:
                continue
            value = window["metrics"].get(metric)
            if value is None:
                continue
            times.append(window["time_minutes"])
            values.append(value)
        return times, values

    def segments_of(self, metric: str) -> dict:
        """One series per analysis segment, so a split never becomes one slope."""
        grouped: dict = {}
        for window in self.fit_windows:
            value = window["metrics"].get(metric)
            if value is None or not window["qualified"]:
                continue
            times, values = grouped.setdefault(window["segment_index"], ([], []))
            times.append(window["time_minutes"])
            values.append(value)
        return grouped


def build_windows(records, seconds: float = 300.0, warmup_seconds: float = 600.0, params=None):
    """Bucket records into non-overlapping windows and compute each window's metrics.

    Positional ``seconds`` and ``warmup_seconds`` are honoured because they are
    the published interface. The result carries the per-metric series the fitter
    needs (:meth:`WindowSet.series`) alongside the plan's quality gate.

    Windows extend only to the end of the formal observation: a record past the
    7260 s instrument cap is reported as a bounded termination, never analysed
    as if the session had been longer than it was.
    """
    params = params or AnalysisParams()
    params = replace(params, window_seconds=float(seconds), warmup_seconds=float(warmup_seconds))
    if not records:
        empty = WindowSet(params=params, windows=[], segments=[])
        empty.gate = _empty_gate(params, "no records were supplied")
        return empty

    segments = split_segments(records, params)
    starts = _plan_window_starts(params)
    windows: list = []
    for segment in segments:
        segment_windows = _segment_windows(segment, starts, params)
        segment["windows"] = segment_windows
        windows.extend(segment_windows)

    result = WindowSet(params=params, windows=windows, segments=segments)
    result.gate = _quality_gate(result, params, len(starts))
    return result


def _empty_gate(params, reason) -> dict:
    return {
        "planned_fit_records": params.planned_fit_records,
        "valid_fit_records": 0,
        "min_valid_records": params.min_valid_records,
        "coverage_ratio": 0.0,
        "coverage_ok": False,
        "planned_windows": params.expected_windows,
        "observed_windows": 0,
        "qualified_windows": 0,
        "min_qualified_windows": params.min_qualified_windows,
        "qualified_ok": False,
        "early_range_qualified_windows": 0,
        "late_range_qualified_windows": 0,
        "required_windows_per_end_range": params.required_windows_per_end_range,
        "end_ranges_ok": False,
        "passed": False,
        "reasons": [reason],
        "segments_total": 0,
        "clock_events_total": 0,
    }


def _quality_gate(window_set: WindowSet, params: AnalysisParams, planned_windows: int) -> dict:
    """The two rules that must BOTH hold before a trend is allowed to speak.

    Rule 1: at least 1254 of the 1320 planned base records in the fit range are
    valid. Rule 2: at least 20 of the 22 windows each hold 54/60 valid records,
    and both ten-minute end ranges carry at least two qualifying windows.

    When either fails the answer is "evidence insufficient", never "zero
    growth": a short or sparse run has not measured a flat trend, it has failed
    to measure a trend at all.
    """
    fit_windows = window_set.fit_windows
    valid_fit_records = sum(window["metrics"]["valid_count"] for window in fit_windows)
    planned_in_observed_range = sum(window["planned_records"] for window in fit_windows)
    observed_coverage_ratio = (
        valid_fit_records / planned_in_observed_range if planned_in_observed_range else 0.0
    )
    qualified = [window for window in fit_windows if window["qualified"]]
    early_qualified = [
        window
        for window in fit_windows
        if window["qualified"] and _overlaps_range(window, params.early_seconds)
    ]
    late_qualified = [
        window
        for window in fit_windows
        if window["qualified"] and _overlaps_range(window, params.late_seconds)
    ]
    gate = {
        "planned_fit_records": params.planned_fit_records,
        "valid_fit_records": valid_fit_records,
        "min_valid_records": params.min_valid_records,
        "coverage_ratio": valid_fit_records / params.planned_fit_records,
        "coverage_ok": valid_fit_records >= params.min_valid_records,
        "coverage_ok_within_observed_range": (
            observed_coverage_ratio >= params.min_valid_records / params.planned_fit_records
        ),
        # Reported alongside, because a segment that began mid-session was never
        # planned to produce the missing records.
        "coverage_ratio_within_observed_range": (
            valid_fit_records / planned_in_observed_range if planned_in_observed_range else 0.0
        ),
        "planned_in_observed_range": planned_in_observed_range,
        "planned_windows": planned_windows,
        "observed_windows": len(fit_windows),
        "qualified_windows": len(qualified),
        "min_qualified_windows": params.min_qualified_windows,
        "qualified_ok": len(qualified) >= params.min_qualified_windows,
        "early_range_qualified_windows": len(early_qualified),
        "late_range_qualified_windows": len(late_qualified),
        "required_windows_per_end_range": params.required_windows_per_end_range,
        "end_ranges_ok": (
            len(early_qualified) >= params.required_windows_per_end_range
            and len(late_qualified) >= params.required_windows_per_end_range
        ),
        "stale_stage_windows": sum(
            1 for window in fit_windows if window["metrics"].get("stale_latency_windows", 0) > 0
        ),
        "segments_total": len(window_set.segments),
        "clock_events_total": sum(
            len(record.get("clock_events", []) or [])
            for segment in window_set.segments
            for record in segment.get("records", [])
        ),
    }
    reasons: list = []
    if not gate["coverage_ok"]:
        reasons.append(
            f"only {valid_fit_records}/{params.planned_fit_records} valid base records in the fit "
            f"range (need {params.min_valid_records})"
        )
    if not gate["qualified_ok"]:
        reasons.append(
            f"only {len(qualified)} of {len(fit_windows)} observed windows reached "
            f"{params.min_window_records} valid records"
        )
    if not gate["end_ranges_ok"]:
        reasons.append(
            "the early and/or late ten-minute range does not hold "
            f"{params.required_windows_per_end_range} qualifying windows each "
            f"(early={len(early_qualified)}, late={len(late_qualified)})"
        )
    if gate["stale_stage_windows"]:
        reasons.append(
            f"{gate['stale_stage_windows']} window(s) carry stale or undersampled rolling stage "
            "statistics; stage-lag conclusions are blocked for those windows"
        )
    if len(window_set.segments) > 1:
        reasons.append(
            f"the run was split into {len(window_set.segments)} analysis segments; the segments "
            "are reported separately and must not be read as one continuous 120 minutes"
        )
    gate["reasons"] = reasons
    gate["passed"] = bool(gate["coverage_ok"] and gate["qualified_ok"] and gate["end_ranges_ok"])
    return gate


def _segment_windows(segment, starts, params: AnalysisParams) -> list:
    """Windows for one segment, on the segment's own monotonic clock.

    A window is created only where the segment actually had that time. A segment
    that opened at minute 40 must not emit window objects for minutes 10-40, or
    its absence would be counted as sampling loss it never had.
    """
    records = segment["records"]
    elapsed_values = [
        record.get("elapsed_seconds")
        for record in records
        if record.get("elapsed_seconds") is not None
    ]
    if not elapsed_values:
        return []
    first_elapsed = min(elapsed_values)
    last_elapsed = max(elapsed_values)
    full_window = int(round(params.window_seconds / params.sample_seconds))
    windows: list = []
    for start in starts:
        end = start + params.window_seconds
        if first_elapsed >= end:
            # This segment did not exist yet; the window is not a gap in it.
            continue
        if last_elapsed < start:
            # The segment was already over; the window is not part of it either.
            continue
        samples = [
            record
            for record in records
            if record.get("elapsed_seconds") is not None
            and start <= record["elapsed_seconds"] < end
        ]
        planned_here = full_window
        if first_elapsed > start:
            planned_here = max(
                0, full_window - int(math.ceil((first_elapsed - start) / params.sample_seconds))
            )
        valid = sum(1 for sample in samples if sample.get("valid"))
        metrics = _window_metrics(samples, params)
        window = {
            "segment_index": segment["index"],
            "start_seconds": start,
            "end_seconds": end,
            "time_minutes": start / 60.0,
            "in_fit_range": start >= params.fit_start_seconds - 1e-9,
            "planned_records": planned_here,
            "metrics": metrics,
            "visible_minutes": sum(
                1 for sample in samples if sample.get("visibility_layer") == VISIBILITY_VISIBLE
            ),
            "hidden_minutes": sum(
                1 for sample in samples if sample.get("visibility_layer") == VISIBILITY_HIDDEN
            ),
            "unknown_visibility_minutes": sum(
                1 for sample in samples if sample.get("visibility_layer") == VISIBILITY_UNKNOWN
            ),
        }
        window["coverage"] = _window_coverage(window)
        window["qualified"] = valid >= params.min_window_records
        windows.append(window)
    return windows


def _overlaps_range(window, span) -> bool:
    start, end = span
    return window["start_seconds"] < end and window["end_seconds"] > start


# --------------------------------------------------------------------------
# Trend fitting
# --------------------------------------------------------------------------
def median_pairwise_slopes(times, values):
    """Theil-Sen slope: the median of every pair's slope.

    A single five-minute window that happened to catch a source stall cannot
    bend this estimate the way it would bend a least-squares line.
    """
    slopes: list = []
    for i in range(len(times)):
        for j in range(i + 1, len(times)):
            delta_time = times[j] - times[i]
            if delta_time == 0:
                continue
            slopes.append((values[j] - values[i]) / delta_time)
    if not slopes:
        return None
    return statistics.median(slopes)


def median_intercept(times, values, slope):
    if slope is None or not times:
        return None
    return statistics.median([value - slope * time for time, value in zip(times, values)])


def residuals_for(times, values, slope, intercept):
    return [value - (intercept + slope * time) for time, value in zip(times, values)]


def aligned_residual_blocks(times, residuals, block_count):
    """Consecutive residual blocks in time order; never shuffled across the series.

    Neighbouring windows are autocorrelated. Drawing their residuals
    independently would understate the interval and hand back a false precision,
    so blocks stay contiguous and the block boundary is the only resampling unit.
    """
    pairs: list = []
    for index in range(0, len(residuals), block_count):
        block_times = times[index:index + block_count]
        block_residuals = residuals[index:index + block_count]
        if block_residuals:
            pairs.append((list(block_times), list(block_residuals)))
    return pairs


def residual_block_bootstrap(
    times, values, block_count: int = 3, repeats: int = 1000, seed: int = 20260917
):
    """Percentile interval for the Theil-Sen slope by resampling residual blocks.

    The plan's sketch calls this over the windowed series. The raw 5-second
    samples are never individually resampled, because 1320 autocorrelated points
    are not 1320 independent observations, and shuffling them would shrink the
    interval for no reason other than arithmetic.
    """
    if len(times) < block_count:
        return {
            "status": "insufficient",
            "reason": (
                f"only {len(times)} usable window(s); fewer than one {block_count}-window block, "
                "so there is nothing to resample"
            ),
            "repeats": 0,
        }
    slope = median_pairwise_slopes(times, values)
    intercept = median_intercept(times, values, slope)
    residuals = residuals_for(times, values, slope, intercept)
    blocks = aligned_residual_blocks(times, residuals, block_count)
    if not blocks:
        return {"status": "insufficient", "reason": "no residual blocks", "repeats": 0}

    rng = random.Random(seed)
    slopes: list = []
    for _ in range(repeats):
        sampled_times: list = []
        sampled_values: list = []
        for _draw in range(len(blocks)):
            block_times, block_residuals = blocks[rng.randrange(len(blocks))]
            for time, residual in zip(block_times, block_residuals):
                sampled_times.append(time)
                sampled_values.append(intercept + slope * time + residual)
        resampled = median_pairwise_slopes(sampled_times, sampled_values)
        if resampled is not None:
            slopes.append(resampled)
    if not slopes:
        return {"status": "insufficient", "reason": "no resampled slope", "repeats": 0}
    return {
        "status": "ok",
        "repeats": repeats,
        "seed": seed,
        "block_count": block_count,
        "blocks_available": len(blocks),
        "slope_low": _percentile(slopes, 0.025),
        "slope_high": _percentile(slopes, 0.975),
        "slope_median": _percentile(slopes, 0.5),
        "ci_level": 0.95,
        "method": (
            "contiguous residual blocks of 3 windows (15 min), 1000 draws, seed 20260917; "
            "raw 5 s samples are never individually resampled"
        ),
    }


def _metric_unit(metric):
    for name, _label, unit in METRIC_UNITS:
        if name == metric:
            return unit
    return "unknown"


METRIC_UNITS = (
    ("rvfc_gap_p95_seconds", "", "seconds"),
    ("raf_gap_p95_seconds", "", "seconds"),
    ("raf_gap_p50_seconds", "", "seconds"),
    ("rvfc_gap_p50_seconds", "", "seconds"),
    ("dropped_frame_ratio_window", "", "ratio"),
    ("heap_low_mb", "", "MiB"),
    ("dom_nodes_low", "", "nodes"),
    ("cpu_tree_low_cores", "", "cores"),
    ("cpu_renderer_low_cores", "", "cores"),
    ("cpu_main_low_cores", "", "cores"),
    ("cpu_gpu_low_cores", "", "cores"),
    ("translation_backlog_median_cues", "", "cues"),
    ("translation_queue_delay_p95_median_seconds", "", "seconds"),
    ("translation_provider_delay_p95_median_seconds", "", "seconds"),
    ("translation_ready_lag_p95_median_seconds", "", "seconds"),
    ("translation_failure_ratio_window", "", "ratio"),
)


def _range_mean(window_set: WindowSet, metric, span):
    """Mean over the qualifying windows inside one ten-minute end range."""
    values = [
        window["metrics"].get(metric)
        for window in window_set.fit_windows
        if window["qualified"] and _overlaps_range(window, span)
    ]
    present = [value for value in values if value is not None]
    return {"mean": statistics.fmean(present) if present else None, "count": len(present)}


def _range_sum(window_set: WindowSet, metric, span):
    values = [
        window["metrics"].get(metric)
        for window in window_set.fit_windows
        if window["qualified"] and _overlaps_range(window, span)
    ]
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def evaluate_threshold(metric, early_value, params: AnalysisParams = None):
    """Resolve a metric's registered screen into one number.

    ``max(share of the early value, absolute floor)`` is the shape four rows of
    the registered table use: the share adapts to the machine, and the floor
    stops a tiny early baseline from turning timer noise into a finding.
    """
    spec = THRESHOLDS.get(metric)
    if spec is None:
        return None
    if spec.rule == "relative_or_absolute":
        if early_value is None:
            # Without an early reading the relative part cannot be formed; the
            # floor is still a registered number, so it stands alone.
            return spec.absolute
        return max(abs(early_value) * spec.relative, spec.absolute)
    return spec.absolute


def sustained_backlog_ok(window_set: WindowSet, metric, delta) -> bool:
    """A backlog screen only fires when the rise persists to the end.

    A three-cue rise confined to the last two five-minute windows is queue
    accumulation; a three-cue rise that drains before minute 118 is a source
    catch-up burst, which happens on live streams with no regression at all.
    """
    spec = THRESHOLDS.get(metric)
    if spec is None or not spec.sustained_last_windows:
        return True
    qualifying = [window for window in window_set.fit_windows if window["qualified"]]
    tail = qualifying[-spec.sustained_last_windows:]
    if len(tail) < spec.sustained_last_windows:
        return False
    earliest = next(
        (
            window["metrics"].get(metric)
            for window in qualifying
            if window["metrics"].get(metric) is not None
        ),
        None,
    )
    if earliest is None:
        return False
    for window in tail:
        value = window["metrics"].get(metric)
        if value is None or value - earliest < delta:
            return False
        if window["metrics"].get("backlog_drained_to_zero"):
            return False
    return True


def fit_duration_trend(
    windows,
    metric: str = None,
    params: AnalysisParams = None,
    block_count: int = None,
    repeats: int = None,
    seed: int = None,
):
    """Fit one metric's trend against session-monotonic minutes.

    Returns a dict consumed by :func:`classify_duration_effect` and delivered in
    the report: ``status``, ``metric``, ``unit``, ``observations``,
    ``time_minutes``, ``values``, ``slope``, ``intercept``, ``residuals``,
    ``slope_ci_low``, ``slope_ci_high``, ``slope_span_over_horizon``,
    ``early_mean``, ``late_mean``, ``early_late_delta``, ``threshold``,
    ``gate``, ``segments`` and ``notes``.

    The quality gate is consulted first and short-circuits to
    ``status="insufficient"``: with too few valid records the honest answer is
    that the trend was not measured, not that it was flat.
    """
    if metric is None:
        metric = "cpu_tree_low_cores"
    if isinstance(windows, WindowSet):
        window_set = windows
    else:
        window_set = build_windows(windows, params=params)
    effective = replace(
        window_set.params,
        block_count=block_count if block_count is not None else window_set.params.block_count,
        bootstrap_repeats=repeats if repeats is not None else window_set.params.bootstrap_repeats,
        bootstrap_seed=seed if seed is not None else window_set.params.bootstrap_seed,
    )
    gate = window_set.gate or {}
    times, values = window_set.series(metric)
    result = {
        "status": "insufficient",
        "metric": metric,
        "unit": _metric_unit(metric),
        "observations": len(times),
        "time_minutes": times,
        "values": values,
        "slope": None,
        "slope_per_minute": None,
        "intercept": None,
        "residuals": [],
        "slope_ci_low": None,
        "slope_ci_high": None,
        "ci_low": None,
        "ci_high": None,
        "slope_span_over_horizon": None,
        "slope_ci_low_span_over_horizon": None,
        "slope_ci_high_span_over_horizon": None,
        "horizon_minutes": TREND_HORIZON_MINUTES,
        "early_mean": None,
        "late_mean": None,
        "early_late_delta": None,
        "early_window_count": 0,
        "late_window_count": 0,
        "threshold": None,
        "gate": gate,
        "segments": [
            {
                "segment_index": index,
                "observations": len(segment_times),
                "time_minutes": segment_times,
                "values": segment_values,
            }
            for index, (segment_times, segment_values) in sorted(
                window_set.segments_of(metric).items()
            )
        ],
        "notes": [],
    }
    notes = result["notes"]

    if gate and not gate.get("passed"):
        notes.append("data quality gate not met: " + "; ".join(gate.get("reasons", [])))
        return result
    if len(times) < 3:
        notes.append(f"only {len(times)} usable window(s) carry this metric; no trend can be fit")
        return result
    if len(set(values)) == 0:
        notes.append("this metric carried no variation across the usable windows")
        return result
    if len(window_set.segments) > 1:
        notes.append(
            f"the run was split into {len(window_set.segments)} analysis segments; the whole-range "
            "slope below is reported for completeness and must not be read as one smooth "
            "120-minute leak (see segments[])"
        )
    if metric in STAGE_LATENCY_METRICS:
        notes.append(
            "this is the median of the fresh five-minute rolling p95 readings, not an original "
            "per-cue p95; several rolling p95 values cannot be averaged into a session p95"
        )

    slope = median_pairwise_slopes(times, values)
    if slope is None:
        notes.append("no pair of usable windows had a non-zero time difference")
        return result
    intercept = median_intercept(times, values, slope)
    residuals = residuals_for(times, values, slope, intercept)
    bootstrap = residual_block_bootstrap(
        times,
        values,
        block_count=effective.block_count,
        repeats=effective.bootstrap_repeats,
        seed=effective.bootstrap_seed,
    )
    early = _range_mean(window_set, metric, effective.early_seconds)
    late = _range_mean(window_set, metric, effective.late_seconds)
    threshold = evaluate_threshold(metric, early["mean"], effective)

    result.update(
        {
            "status": "ok",
            "slope": slope,
            "slope_per_minute": slope,
            "intercept": intercept,
            "residuals": residuals,
            "bootstrap": bootstrap,
            "early_mean": early["mean"],
            "late_mean": late["mean"],
            "early_window_count": early["count"],
            "late_window_count": late["count"],
            "early_late_delta": (
                late["mean"] - early["mean"]
                if early["mean"] is not None and late["mean"] is not None
                else None
            ),
            "threshold": threshold,
            "baseline_frame_interval_seconds": effective.baseline_frame_interval_seconds,
        }
    )
    # Evaluated here, once, so every consumer sees the same sustained verdict.
    if metric == "translation_backlog_median_cues" and threshold is not None:
        result["sustained_ok"] = sustained_backlog_ok(window_set, metric, threshold)
    if bootstrap.get("status") != "ok":
        result["status"] = "insufficient"
        notes.append("bootstrap interval unavailable: " + str(bootstrap.get("reason")))
        return result

    result["slope_ci_low"] = bootstrap["slope_low"]
    result["slope_ci_high"] = bootstrap["slope_high"]
    result["ci_low"] = bootstrap["slope_low"]
    result["ci_high"] = bootstrap["slope_high"]
    result["slope_span_over_horizon"] = slope * TREND_HORIZON_MINUTES
    result["slope_ci_low_span_over_horizon"] = bootstrap["slope_low"] * TREND_HORIZON_MINUTES
    result["slope_ci_high_span_over_horizon"] = bootstrap["slope_high"] * TREND_HORIZON_MINUTES
    return result


def attach_denominators(trend, window_set: WindowSet, metric):
    """Attach the attempt/frame denominators and the sustained-backlog verdict.

    Both registered screens are conditional: a ratio difference needs at least
    100 attempts in each end window, and a dropped-frame ratio needs at least
    3000 real frames in each. Without those the counts are still reported, but
    no ratio-based conclusion is drawn from them.
    """
    params = window_set.params
    if metric == "translation_failure_ratio_window":
        trend["early_attempt_delta"] = _range_sum(
            window_set, "translation_attempt_delta", params.early_seconds
        )
        trend["late_attempt_delta"] = _range_sum(
            window_set, "translation_attempt_delta", params.late_seconds
        )
        trend["min_attempts_required"] = TRANSLATION_MIN_ATTEMPTS
    if metric == "dropped_frame_ratio_window":
        trend["early_frame_delta"] = _range_sum(
            window_set, "video_frame_total_delta", params.early_seconds
        )
        trend["late_frame_delta"] = _range_sum(
            window_set, "video_frame_total_delta", params.late_seconds
        )
        trend["min_total_frames_required"] = DROPPED_MIN_TOTAL_FRAMES
    return trend


def _failure_ratio_denominators_ok(trend) -> bool:
    return bool(
        trend.get("early_attempt_delta") is not None
        and trend.get("late_attempt_delta") is not None
        and trend["early_attempt_delta"] >= TRANSLATION_MIN_ATTEMPTS
        and trend["late_attempt_delta"] >= TRANSLATION_MIN_ATTEMPTS
    )


def _dropped_denominators_ok(trend) -> bool:
    return bool(
        trend.get("early_frame_delta") is not None
        and trend.get("late_frame_delta") is not None
        and trend["early_frame_delta"] >= DROPPED_MIN_TOTAL_FRAMES
        and trend["late_frame_delta"] >= DROPPED_MIN_TOTAL_FRAMES
    )


def masked_gap(trend, threshold, direction) -> dict:
    """Look for a rise inside the range that the aggregate figures concealed.

    A regression through 22 windows can pass a flat-looking line over a stretch
    that clearly worsened and then recovered. Because the plan forbids
    announcing "no degradation" while such a stretch exists, the individual
    window values are checked against the early level before the negative state
    is allowed to stand.
    """
    values = trend.get("values") or []
    times = trend.get("time_minutes") or []
    if len(values) < 4:
        return {"masked": False, "windows": [], "observed_max_gap": 0.0}
    baseline = statistics.median(values[: max(1, len(values) // 5)])
    flagged: list = []
    worst = 0.0
    for time_minute, value in zip(times, values):
        gap = direction * (value - baseline)
        worst = max(worst, gap)
        if gap >= threshold:
            flagged.append(
                {"time_minutes": time_minute, "value": value, "gap_over_baseline": gap}
            )
    return {"masked": bool(flagged), "windows": flagged, "observed_max_gap": worst}


def classify_duration_effect(trend, params: AnalysisParams = None) -> dict:
    """Assign one of the three registered states; never force a binary answer.

    ``检出实质增长候选`` requires the early/late gap, the slope scaled over the
    110-minute horizon and the interval's lower bound to agree. Any one of them
    alone is a shape that a single window or a slow source drift can produce.

    ``在本窗口未检出达到门槛的增长`` requires the gate to pass *and* both the
    early/late gap and the interval's upper bound to stay under the threshold.
    If some individual window nonetheless rose above the threshold, the rise was
    masked by aggregation, so the answer degrades to insufficient.

    Everything else is ``证据不足或非单调异常`` -- including an interval that
    straddles zero with a wide upper bound, few late windows, stale stage
    readings or one discrete pause. Reporting that as "no growth" is the failure
    this third state exists to prevent.
    """
    params = params or AnalysisParams()
    metric = trend.get("metric")
    threshold = trend.get("threshold")
    gate = trend.get("gate", {}) or {}
    spec = THRESHOLDS.get(metric)
    effect = {
        "metric": metric,
        "label": spec.label if spec else metric,
        "state": EFFECT_INSUFFICIENT,
        "state_code": "insufficient_or_non_monotonic",
        "threshold": threshold,
        "threshold_kind": (
            "pre-registered engineering screen, not a repository-measured fault definition"
        ),
        "level": spec.level if spec else None,
        "early_mean": trend.get("early_mean"),
        "late_mean": trend.get("late_mean"),
        "early_late_delta": trend.get("early_late_delta"),
        "slope_per_minute": trend.get("slope"),
        "slope_span_over_horizon": trend.get("slope_span_over_horizon"),
        "slope_ci_low": trend.get("slope_ci_low"),
        "slope_ci_high": trend.get("slope_ci_high"),
        "horizon_minutes": TREND_HORIZON_MINUTES,
        "observations": trend.get("observations"),
        "early_window_count": trend.get("early_window_count"),
        "late_window_count": trend.get("late_window_count"),
        "reasons": [],
        "caveats": [],
    }
    reasons = effect["reasons"]

    if trend.get("status") != "ok":
        reasons.append(
            "trend not fitted: " + ("; ".join(trend.get("notes", [])) or "insufficient data")
        )
        return effect
    if not gate.get("passed"):
        reasons.append("data quality gate not met: " + "; ".join(gate.get("reasons", [])))
        return effect
    if threshold is None:
        reasons.append("no pre-registered screen exists for this metric")
        return effect
    if metric in STAGE_LATENCY_METRICS:
        effect["caveats"].append(
            "reading is the median of the five-minute rolling p95 values, not an original "
            "per-cue p95"
        )
        if gate.get("stale_stage_windows"):
            reasons.append("stale rolling stage readings block a stage-lag conclusion")
            return effect
    if metric == "translation_failure_ratio_window" and not _failure_ratio_denominators_ok(trend):
        reasons.append(
            "the early and late windows do not each hold "
            f"{TRANSLATION_MIN_ATTEMPTS} translation attempts "
            f"(early={trend.get('early_attempt_delta')}, late={trend.get('late_attempt_delta')}); "
            "counts and a wide interval are reported instead of a ratio difference"
        )
        return effect
    if metric == "dropped_frame_ratio_window" and not _dropped_denominators_ok(trend):
        reasons.append(
            "the early and late windows do not each hold "
            f"{DROPPED_MIN_TOTAL_FRAMES} actual video frames "
            f"(early={trend.get('early_frame_delta')}, late={trend.get('late_frame_delta')}); "
            "a small ratio here is not evidence of stability"
        )
        return effect

    direction = spec.direction or 1
    delta = trend.get("early_late_delta")
    horizon = trend.get("slope_span_over_horizon")
    ci_low = trend.get("slope_ci_low")
    ci_high = trend.get("slope_ci_high")
    if delta is None or horizon is None or ci_low is None or ci_high is None:
        reasons.append("early/late or interval figures are missing; there is nothing to compare")
        return effect

    early_gap = direction * delta
    late_gap = direction * horizon
    low_gap = direction * ci_low
    high_gap = direction * ci_high

    # A backlog screen only fires when the rise persists to the end. This is a
    # gate on *claiming growth*; it must not turn a clean flat backlog into a
    # non-monotonic anomaly, which is what consulting it in the negative branch
    # would do.
    sustained = trend.get("sustained_ok") is not False
    effect["sustained_ok"] = trend.get("sustained_ok")
    if metric == "translation_backlog_median_cues" and not sustained:
        effect["reasons"].append(
            "the backlog rise, if any, does not persist through the last two five-minute windows "
            "(a burst that drained is not accumulation)"
        )

    # The three-way verdict. The two branches below are different failures and
    # must read differently: one is a real rise the interval cannot confirm, the
    # other is a rise the aggregate figures never showed at all.
    if early_gap >= threshold:
        if low_gap > 0 and sustained:
            effect["state"] = EFFECT_GROWTH
            effect["state_code"] = "growth_candidate"
            reasons.append(
                f"early/late gap {early_gap:.6g} and slope x {TREND_HORIZON_MINUTES:.0f} min "
                f"{late_gap:.6g} both reach the {threshold:.6g} screen, and the interval's lower "
                f"bound is positive ({low_gap:.6g})"
            )
            effect["caveats"].append(
                "this establishes a time-correlated candidate, not a cause; attribution still "
                "needs object-level, provider-stage or download-leg evidence"
            )
            return effect
        reasons.append(
            f"the early/late gap {early_gap:.6g} does reach the {threshold:.6g} screen, but the "
            f"resampled interval [{low_gap:.6g}, {high_gap:.6g}] does not establish the direction "
            "(its lower bound is not positive); a step or a single stretch cannot be told apart "
            "from a gradual trend by this fit"
        )
        effect["masked_windows"] = masked_gap(trend, threshold, direction)["windows"]
        return effect

    masked = masked_gap(trend, threshold, direction)
    if masked["masked"]:
        reasons.append(
            "aggregation masks a worse stretch: "
            f"{len(masked['windows'])} window(s) rose at least {threshold:.6g} above the early "
            "level while the whole-range figures stayed under it"
        )
        effect["masked_windows"] = masked["windows"]
        return effect

    if high_gap < threshold:
        effect["state"] = EFFECT_NONE_DETECTED
        effect["state_code"] = "none_detected_in_this_window"
        reasons.append(
            f"quality gate passed; the early/late gap {early_gap:.6g} and the interval's upper "
            f"bound over {TREND_HORIZON_MINUTES:.0f} min {high_gap:.6g} both stay under the "
            f"{threshold:.6g} screen"
        )
        effect["caveats"].append(
            "limited negative result: valid for this two-hour run, this "
            "source/quality/configuration, this machine and exactly these metrics"
        )
        return effect

    reasons.append(
        f"the interval does not agree with the screen: early/late {early_gap:.6g}, slope span "
        f"{late_gap:.6g}, interval [{low_gap:.6g}, {high_gap:.6g}] against {threshold:.6g}; not "
        "enough to claim growth and not enough to claim its absence"
    )
    return effect


# --------------------------------------------------------------------------
# 95-second cycle, gap events, layered visibility, hypotheses
# --------------------------------------------------------------------------
def _solve_normal_equations(design, target):
    """Least squares for a small fixed column count via the normal equations.

    The design is small by construction (one known frequency, never a search
    over many), so an explicit solve keeps this tool free of numeric
    dependencies. Returns ``None`` for a singular system instead of guessing.
    """
    columns = len(design[0])
    matrix = [[0.0] * (columns + 1) for _ in range(columns)]
    for row, value in zip(design, target):
        for i in range(columns):
            for j in range(columns):
                matrix[i][j] += row[i] * row[j]
            matrix[i][columns] += row[i] * value
    for pivot in range(columns):
        best = max(range(pivot, columns), key=lambda row: abs(matrix[row][pivot]))
        if abs(matrix[best][pivot]) < 1e-12:
            return None
        matrix[pivot], matrix[best] = matrix[best], matrix[pivot]
        scale = matrix[pivot][pivot]
        matrix[pivot] = [entry / scale for entry in matrix[pivot]]
        for row in range(columns):
            if row == pivot:
                continue
            factor = matrix[row][pivot]
            if factor:
                matrix[row] = [
                    entry - factor * other for entry, other in zip(matrix[row], matrix[pivot])
                ]
    return [matrix[index][columns] for index in range(columns)]


def _simple_slope(times, values):
    if len(times) < 2:
        return None
    mean_time = statistics.fmean(times)
    mean_value = statistics.fmean(values)
    denominator = sum((time - mean_time) ** 2 for time in times)
    if denominator == 0:
        return None
    return (
        sum(
            (time - mean_time) * (value - mean_value)
            for time, value in zip(times, values)
        )
        / denominator
    )


def _cycle_amplitude(usable, period):
    if len(usable) < 8:
        return None
    design = [
        [
            math.sin(2.0 * math.pi * elapsed / period),
            math.cos(2.0 * math.pi * elapsed / period),
            1.0,
        ]
        for elapsed, _value in usable
    ]
    coefficients = _solve_normal_equations(design, [value for _elapsed, value in usable])
    if coefficients is None:
        return None
    return math.hypot(coefficients[0], coefficients[1])


def fit_cycle_model(samples, params: AnalysisParams = None, known_period_seconds: float = None):
    """Fit fixed 95-second sin/cos terms plus a linear term to a 5-second series.

    The frequency is *given* (95 s, from prior observation), not chosen from a
    periodogram: picking the most significant of dozens of frequencies and then
    reporting its amplitude would be a search, and the plan forbids presenting a
    searched frequency as a discovered one. Amplitude and mean are reported for
    each half separately, and the linear slope is reported both with and without
    the periodic term, so "stable cycle" is measured rather than assumed.
    """
    params = params or AnalysisParams()
    period = known_period_seconds or params.cycle_seconds
    usable = [
        (sample.get("elapsed_seconds"), sample.get("value"))
        for sample in samples
        if sample.get("elapsed_seconds") is not None
        and sample.get("value") is not None
        and sample.get("elapsed_seconds") >= params.fit_start_seconds
        and sample.get("elapsed_seconds") <= params.fit_end_seconds
    ]
    result = {
        "known_period_seconds": period,
        "samples": len(usable),
        "status": "insufficient",
        "reason": None,
        "amplitude": None,
        "amplitude_early": None,
        "amplitude_late": None,
        "amplitude_delta": None,
        "mean_early": None,
        "mean_late": None,
        "mean_delta": None,
        "sin_coefficient": None,
        "cos_coefficient": None,
        "linear_slope_per_minute": None,
        "detrended_slope_per_minute": None,
        "intercept": None,
        "frequency_search": (
            "not performed: the 95 s period is pre-registered, not discovered from this data"
        ),
    }
    if len(usable) < 8:
        result["reason"] = f"only {len(usable)} usable 5 s sample(s) in the fit range"
        return result

    design = [
        [
            math.sin(2.0 * math.pi * elapsed / period),
            math.cos(2.0 * math.pi * elapsed / period),
            elapsed / 60.0,
            1.0,
        ]
        for elapsed, _value in usable
    ]
    target = [value for _elapsed, value in usable]
    coefficients = _solve_normal_equations(design, target)
    if coefficients is None:
        result["reason"] = (
            "design matrix is singular; the cycle fit was abandoned rather than guessed"
        )
        return result
    sin_coefficient, cos_coefficient, slope, intercept = coefficients
    amplitude = math.hypot(sin_coefficient, cos_coefficient)

    detrended_slope = _simple_slope(
        [elapsed / 60.0 for elapsed, _value in usable],
        [value - (intercept + slope * elapsed / 60.0) for elapsed, value in usable],
    )
    midpoint = (params.fit_start_seconds + params.fit_end_seconds) / 2.0
    early_values = [value for elapsed, value in usable if elapsed < midpoint]
    late_values = [value for elapsed, value in usable if elapsed >= midpoint]
    early_amplitude = _cycle_amplitude(
        [item for item in usable if item[0] < midpoint], period
    )
    late_amplitude = _cycle_amplitude(
        [item for item in usable if item[0] >= midpoint], period
    )
    result.update(
        {
            "status": "ok",
            "sin_coefficient": sin_coefficient,
            "cos_coefficient": cos_coefficient,
            "amplitude": amplitude,
            "amplitude_early": early_amplitude,
            "amplitude_late": late_amplitude,
            "amplitude_delta": (
                late_amplitude - early_amplitude
                if early_amplitude is not None and late_amplitude is not None
                else None
            ),
            "mean_early": statistics.fmean(early_values) if early_values else None,
            "mean_late": statistics.fmean(late_values) if late_values else None,
            "intercept": intercept,
            "linear_slope_per_minute": slope,
            "detrended_slope_per_minute": detrended_slope,
        }
    )
    result["mean_delta"] = (
        result["mean_late"] - result["mean_early"]
        if result["mean_late"] is not None and result["mean_early"] is not None
        else None
    )
    return result


def _minute_of(elapsed):
    return None if elapsed is None else int(elapsed // 60.0)


def collect_gap_events(records, params: AnalysisParams = None):
    """Per-minute >500 ms and >5000 ms callback/presentation gaps, plus drain events.

    These are reported per minute and never filtered by a p95: a window whose
    p95 is null *because* no callback ran is exactly the window that must appear
    here, with the distance to the last callback attached. One 5-second gap is
    worth keeping and checking; it is not by itself evidence of growth.
    """
    params = params or AnalysisParams()
    events: list = []
    for sample in records:
        if not sample.get("valid"):
            continue
        for source, field in GAP_SOURCES:
            gap = sample.get(field)
            if gap is None or gap < MIN_REPORTED_GAP_SECONDS:
                continue
            if gap > GAP_5000MS_SECONDS:
                kind = "gap_over_5000ms"
            elif gap > GAP_500MS_SECONDS:
                kind = "gap_over_500ms"
            else:
                continue
            events.append(
                {
                    "kind": kind,
                    "source": source,
                    "gap_seconds": gap,
                    "elapsed_seconds": sample.get("elapsed_seconds"),
                    "minute": _minute_of(sample.get("elapsed_seconds")),
                    "visibility_layer": sample.get("visibility_layer"),
                    "last_callback_age_seconds": sample.get("last_callback_age_seconds"),
                }
            )
        if sample.get("raf_present") and sample.get("raf_has_callbacks") is False:
            events.append(
                {
                    "kind": "no_raf_callbacks_in_interval",
                    "source": "raf",
                    "gap_seconds": None,
                    "elapsed_seconds": sample.get("elapsed_seconds"),
                    "minute": _minute_of(sample.get("elapsed_seconds")),
                    "callback_count": 0,
                    "last_callback_age_seconds": sample.get("last_callback_age_seconds"),
                    "visibility_layer": sample.get("visibility_layer"),
                    "note": "the p95 stays null on purpose; the emptiness is the measurement",
                }
            )
        if sample.get("buffered_ahead") == 0:
            events.append(
                {
                    "kind": "buffer_exhausted",
                    "source": "renderer.bufferedAhead",
                    "elapsed_seconds": sample.get("elapsed_seconds"),
                    "minute": _minute_of(sample.get("elapsed_seconds")),
                    "visibility_layer": sample.get("visibility_layer"),
                }
            )

    per_minute: dict = {}
    for event in events:
        minute = event["minute"]
        if minute is None:
            continue
        bucket = per_minute.setdefault(
            minute,
            {
                "minute": minute,
                "gap_over_500ms": 0,
                "gap_over_5000ms": 0,
                "no_raf_callbacks": 0,
                "buffer_exhausted": 0,
                "max_gap_seconds": None,
            },
        )
        if event["kind"] == "gap_over_500ms":
            bucket["gap_over_500ms"] += 1
        elif event["kind"] == "gap_over_5000ms":
            bucket["gap_over_5000ms"] += 1
        elif event["kind"] == "no_raf_callbacks_in_interval":
            bucket["no_raf_callbacks"] += 1
        elif event["kind"] == "buffer_exhausted":
            bucket["buffer_exhausted"] += 1
        if event.get("gap_seconds") is not None:
            current = bucket["max_gap_seconds"]
            bucket["max_gap_seconds"] = (
                event["gap_seconds"] if current is None else max(current, event["gap_seconds"])
            )

    gaps = [event["gap_seconds"] for event in events if event.get("gap_seconds") is not None]
    return {
        "events": events,
        "per_minute": [per_minute[minute] for minute in sorted(per_minute)],
        "counts": {
            "gap_over_500ms": sum(1 for event in events if event["kind"] == "gap_over_500ms"),
            "gap_over_5000ms": sum(1 for event in events if event["kind"] == "gap_over_5000ms"),
            "no_raf_callbacks_in_interval": sum(
                1 for event in events if event["kind"] == "no_raf_callbacks_in_interval"
            ),
            "buffer_exhausted": sum(
                1 for event in events if event["kind"] == "buffer_exhausted"
            ),
        },
        "longest_gap_seconds": max(gaps) if gaps else None,
        "note": (
            "a single 5 s empty stretch is worth keeping and checking, but it is not by itself "
            "evidence of growth with duration; gaps are never dropped just because a p95 is null"
        ),
    }


def visibility_strata(windows) -> dict:
    """hidden / confirmably visible / occlusion-unknown, with coverage counts.

    A stratum without enough qualifying windows gets no verdict at all: "not
    degraded" is a claim about a layer that was observed, not about a layer that
    was never sampled. A window is assigned to the layer that covers most of its
    minutes, so a hidden stretch inside a visible window does not silently move
    the whole window.
    """
    strata = {
        VISIBILITY_HIDDEN: {
            "windows": [],
            "note": "document.hidden was true for most of the window",
        },
        VISIBILITY_VISIBLE: {
            "windows": [],
            "note": "document.hidden false and occlusion sampled false for most of the window",
        },
        VISIBILITY_UNKNOWN: {
            "windows": [],
            "note": (
                "visibility or occlusion was not sampled; occlusion unknown is not the same as "
                "unoccluded, and blur is never counted as hidden"
            ),
        },
    }
    for window in windows:
        if not window["qualified"]:
            continue
        dominant = max(
            (
                (window["hidden_minutes"], VISIBILITY_HIDDEN),
                (window["visible_minutes"], VISIBILITY_VISIBLE),
                (window["unknown_visibility_minutes"], VISIBILITY_UNKNOWN),
            ),
            key=lambda pair: pair[0],
        )
        strata[dominant[1]]["windows"].append(window)

    result: dict = {}
    for layer, entry in strata.items():
        windows_here = entry["windows"]
        enough = len(windows_here) >= MIN_STRATUM_WINDOWS
        result[layer] = {
            "note": entry["note"],
            "qualified_windows": len(windows_here),
            "visible_minutes": sum(window["visible_minutes"] for window in windows_here),
            "hidden_minutes": sum(window["hidden_minutes"] for window in windows_here),
            "unknown_visibility_minutes": sum(
                window["unknown_visibility_minutes"] for window in windows_here
            ),
            "raf_callback_count": sum(
                window["metrics"]["raf_callback_count"] for window in windows_here
            ),
            "raf_synchronous_minutes": sum(
                window["metrics"]["raf_synchronous_minutes"] for window in windows_here
            ),
            "verdict_allowed": enough,
            "verdict_note": (
                "enough same-state windows exist to state a limited result for this layer"
                if enough
                else "too few same-state windows: no 'not degraded' conclusion may be issued for "
                "this layer"
            ),
        }
    return result


def hypothesis_states(evidence) -> list:
    """Per-hypothesis evidence status for this run, with both shapes attached.

    The statuses constrain this run only. "被否证" here means "this evidence does
    not support it", not "disproved in general": a two-hour session cannot
    settle a native or GPU leak it never measured.
    """
    growth = evidence["growth_metrics"]
    none_detected = evidence["none_detected_metrics"]
    insufficient = evidence["insufficient_metrics"]
    cycle = evidence["cycle"]
    gate = evidence["gate"]
    segmentation = evidence["segmentation"]
    effects = evidence["metric_effects"]

    states: list = []
    for hypothesis in HYPOTHESES:
        state = HYPOTHESIS_NO_DATA
        reasons: list = []
        if hypothesis["id"] == "a":
            if growth:
                state = HYPOTHESIS_REFUTED
                reasons.append(
                    "time-correlated growth appeared under one fixed condition: "
                    + ", ".join(sorted(growth))
                )
            elif none_detected and gate.get("passed"):
                state = HYPOTHESIS_SUPPORTED
                reasons.append(
                    "the quality gate passed and no metric crossed its registered screen; this "
                    f"supports the claim for this run only ({len(none_detected)} metrics scored)"
                )
            else:
                state = HYPOTHESIS_INSUFFICIENT
                reasons.append(
                    "the quality gate did not pass, so nothing about this claim is established here"
                )
            if insufficient:
                reasons.append("metrics left unmeasured: " + ", ".join(sorted(insufficient)))
        elif hypothesis["id"] == "b":
            heap = effects.get("heap_low_mb") or {}
            dom = effects.get("dom_nodes_low") or {}
            if EFFECT_GROWTH in (heap.get("state"), dom.get("state")):
                state = HYPOTHESIS_SUPPORTED
                reasons.append(
                    "heap/DOM low levels crossed their screens with a positive interval; retained-"
                    "object evidence is still required before this is called a leak"
                )
            elif growth:
                state = HYPOTHESIS_INSUFFICIENT
                reasons.append(
                    "measured object levels stayed bounded while user metrics grew: a leak of the "
                    "measured objects is not the explanation, and unmeasured GPU/native retention "
                    "cannot be ruled out"
                )
            elif none_detected:
                state = HYPOTHESIS_REFUTED
                reasons.append(
                    "measured heap/DOM levels stayed within their screens for this run; this does "
                    "not deny a leak in objects this sampler never read"
                )
            else:
                state = HYPOTHESIS_INSUFFICIENT
                reasons.append("heap/DOM evidence is insufficient for this run")
        elif hypothesis["id"] == "c":
            if cycle.get("status") != "ok":
                state = HYPOTHESIS_NO_DATA
                reasons.append("cycle fit unavailable: " + str(cycle.get("reason")))
            else:
                slope = cycle.get("detrended_slope_per_minute")
                amplitude_delta = cycle.get("amplitude_delta")
                amplitude = cycle.get("amplitude")
                stable_amplitude = bool(
                    amplitude_delta is not None
                    and amplitude
                    and abs(amplitude_delta) <= 0.2 * abs(amplitude)
                )
                slope_tiny = bool(slope is not None and abs(slope) < 1e-9)
                if growth or (slope is not None and not slope_tiny and not stable_amplitude):
                    state = HYPOTHESIS_REFUTED
                    reasons.append(
                        "the de-periodised slope or the amplitude trend is not flat, so a pure "
                        f"periodic explanation is not sufficient (detrended slope/min={slope}, "
                        f"amplitude delta={amplitude_delta})"
                    )
                elif slope is not None and stable_amplitude and not growth:
                    state = HYPOTHESIS_SUPPORTED
                    reasons.append(
                        "amplitude is stable between halves and the de-periodised slope is small "
                        "while no user metric crossed its screen"
                    )
                else:
                    state = HYPOTHESIS_INSUFFICIENT
                    reasons.append(
                        "a cycle is present but the surrounding evidence is not decisive"
                    )
                reasons.append("a low CPU/frame-gap correlation excludes no system cause")
        elif hypothesis["id"] == "d":
            backlog = effects.get("translation_backlog_median_cues") or {}
            if backlog.get("state") == EFFECT_GROWTH:
                state = HYPOTHESIS_SUPPORTED
                reasons.append(
                    "the backlog rose and persisted through the final two five-minute windows"
                )
            elif growth:
                state = HYPOTHESIS_INSUFFICIENT
                reasons.append(
                    "queue length and delay look stable in fresh samples while other metrics grew; "
                    "this limits the queue explanation, but the stage metrics are rolling readings"
                )
            elif none_detected:
                state = HYPOTHESIS_REFUTED
                reasons.append(
                    "backlog and queue readings stayed flat under fresh samples; a stale reading "
                    "would not have counted as stability"
                )
            else:
                state = HYPOTHESIS_INSUFFICIENT
                reasons.append(
                    "stage readings were stale or undersampled, so stability is not established"
                )
        elif hypothesis["id"] == "e":
            if segmentation.get("media_time_steps"):
                state = HYPOTHESIS_SUPPORTED
                reasons.append(
                    f"{len(segmentation['media_time_steps'])} discrete media-time step(s) "
                    "recorded; handed to R1 as the clock-boundary owner rather than fitted as "
                    "smooth growth"
                )
            elif segmentation.get("clock_events_total"):
                state = HYPOTHESIS_INSUFFICIENT
                reasons.append(
                    "monotonic/wall clock events were seen but no source PTS boundary step: the "
                    "wrap explanation is neither supported nor excluded"
                )
            else:
                state = HYPOTHESIS_REFUTED
                reasons.append(
                    "media time advanced continuously with no boundary step in this run, which "
                    "bounds the wrap explanation for these symptoms only; a short run does not "
                    "exclude the source approaching a wrap"
                )
        elif hypothesis["id"] == "f":
            if not evidence.get("download_metrics_available"):
                state = HYPOTHESIS_NO_DATA
                reasons.append(
                    "this sampler's records carry no per-leg forwardedBytes / sourcePts fields, so "
                    "the download leg cannot be scored at all by this tool"
                )
            else:
                state = HYPOTHESIS_INSUFFICIENT
                reasons.append(
                    "download-leg fields are present but this tool does not yet score them; no "
                    "conclusion is drawn"
                )
        states.append(
            {
                "id": hypothesis["id"],
                "claim": hypothesis["claim"],
                "state": state,
                "supported_shape": hypothesis["supported_shape"],
                "refuting_shape": hypothesis["refuting_shape"],
                "scope_note": hypothesis["scope_note"],
                "reasons": reasons,
            }
        )
    return states


# --------------------------------------------------------------------------
# Top-level analysis
# --------------------------------------------------------------------------
def _download_metrics_available(records) -> bool:
    """Whether any raw record carries per-leg download fields.

    The documented record shape has no forwardedBytes / sourcePts / sourceIdle,
    so hypothesis (f) is normally unscorable. The check stays dynamic so a
    future sampler that does emit them is not silently ignored.
    """
    for record in records:
        if not isinstance(record, dict):
            continue
        for key in ("download", "legs", "forwardedBytes", "sourcePtsFirst", "sourceIdleSeconds"):
            if key in record:
                return True
    return False


def _segmentation_report(window_set: WindowSet) -> dict:
    clock_events: list = []
    media_time_steps: list = []
    for segment in window_set.segments:
        for record in segment.get("records", []):
            for event in record.get("clock_events", []) or []:
                entry = dict(event)
                entry["segment_index"] = segment["index"]
                clock_events.append(entry)
                if event.get("kind") == "media_time_step":
                    media_time_steps.append(entry)
    return {
        "segments": [
            {
                "index": segment["index"],
                "reason_started": segment.get("reason_started"),
                "reason_closed": segment.get("reason_closed"),
                "closed_at_elapsed_seconds": segment.get("closed_at_elapsed_seconds"),
                "records": len(segment.get("records", [])),
                "qualified_windows": sum(
                    1 for window in segment.get("windows", []) if window.get("qualified")
                ),
            }
            for segment in window_set.segments
        ],
        "split": len(window_set.segments) > 1,
        "clock_events": clock_events,
        "clock_events_total": len(clock_events),
        "media_time_steps": media_time_steps,
        "note": (
            "a change of session identity, code or configuration, a counter reset, PID reuse or a "
            "sleep/sampling-clock step splits the run; those populations must not be glued into "
            "one 120-minute slope. Media-time steps are discrete clock events owned by R1 and are "
            "reported in full rather than smoothed away."
        ),
    }


def _constant_table(params: AnalysisParams) -> dict:
    return {
        "session_seconds": SESSION_SECONDS,
        "warmup_seconds": params.warmup_seconds,
        "base_sample_seconds": params.sample_seconds,
        "window_seconds": params.window_seconds,
        "fit_range_seconds": [params.fit_start_seconds, params.fit_end_seconds],
        "early_range_seconds": list(params.early_seconds),
        "late_range_seconds": list(params.late_seconds),
        "planned_fit_records": params.planned_fit_records,
        "planned_session_records": params.planned_session_records,
        "min_valid_records": params.min_valid_records,
        "min_window_records": params.min_window_records,
        "min_qualified_windows": params.min_qualified_windows,
        "required_windows_per_end_range": params.required_windows_per_end_range,
        "block_count": params.block_count,
        "bootstrap_repeats": params.bootstrap_repeats,
        "bootstrap_seed": params.bootstrap_seed,
        "cycle_seconds": params.cycle_seconds,
        "trend_horizon_minutes": TREND_HORIZON_MINUTES,
        "output_limit_bytes": params.output_limit_bytes,
        "instrument_cap_seconds": params.instrument_cap_seconds,
        "latency_min_window_samples": LATENCY_MIN_WINDOW_SAMPLES,
        "latency_max_sample_age_seconds": LATENCY_MAX_SAMPLE_AGE_SECONDS,
        "translation_min_attempts": TRANSLATION_MIN_ATTEMPTS,
        "dropped_min_total_frames": DROPPED_MIN_TOTAL_FRAMES,
        "baseline_frame_interval_seconds": params.baseline_frame_interval_seconds,
        "min_stratum_windows": MIN_STRATUM_WINDOWS,
        "thresholds": {
            key: {
                "label": spec.label,
                "rule": spec.rule,
                "relative": spec.relative,
                "absolute": spec.absolute,
                "direction": spec.direction,
                "level": spec.level,
                "sustained_last_windows": spec.sustained_last_windows,
                "reason": spec.reason,
            }
            for key, spec in THRESHOLDS.items()
        },
        "threshold_provenance": (
            "every number above is a pre-registered engineering screen, not a repository-measured "
            "fault definition; a metric under its screen has not been proven leak-free"
        ),
    }


MANIFEST_EXPECTED_FIELDS = (
    "code_sha",
    "config_fingerprint",
    "session_id",
    "media_session_id",
    "started_at",
    "ended_at",
    "source",
    "quality",
    "target_language",
    "translation_provider",
    "asr_provider",
    "machine",
    "os_version",
    "python_version",
    "app_commit",
    "instrument_commit",
    "fixed_conditions",
    "expected_duration_seconds",
    "sample_interval_seconds",
    "window_layout",
    "audio_state",
    "virtualization_environment",
    "notes",
)


def _manifest_completeness(manifest) -> dict:
    """Report which manifest fields are absent instead of inventing them.

    The manifest is the experiment's own record of code SHA, redacted
    configuration fingerprint, session identity and fixed conditions. If it is
    incomplete the analysis is still produced, but every claim that depends on
    those fields is labelled as not established here.
    """
    if not isinstance(manifest, dict):
        return {
            "provided": False,
            "note": (
                "no manifest was supplied; the code SHA, configuration fingerprint, session "
                "identity and fixed conditions of this run are therefore unknown to this analysis "
                "and are not guessed from the samples"
            ),
            "missing_fields": list(MANIFEST_EXPECTED_FIELDS),
            "present_fields": [],
            "unrecognised_fields": [],
        }
    present = [field for field in MANIFEST_EXPECTED_FIELDS if manifest.get(field) not in (None, "")]
    missing = [field for field in MANIFEST_EXPECTED_FIELDS if field not in present]
    extra = [field for field in manifest if field not in MANIFEST_EXPECTED_FIELDS]
    return {
        "provided": True,
        "present_fields": present,
        "missing_fields": missing,
        "unrecognised_fields": extra,
        "note": (
            "present fields are quoted verbatim; missing fields are reported as missing and are "
            "never filled in from the samples. A conclusion that would need a fix commit or a "
            "configuration this manifest does not carry is therefore not issued."
        ),
    }


def write_report(report, output_path: Path, params: AnalysisParams = None) -> dict:
    """Write the report, refusing to exceed the 256 MiB output cap.

    On overflow nothing is written at all: overwriting or rotating an existing
    evidence file to make room is explicitly not allowed, so the previous
    evidence survives a failed run.

    ``report["output"]`` is filled in *before* serialization, so the file on disk
    states its own write outcome; a report that cannot say how it was written is
    not auditable evidence.
    """
    params = params or AnalysisParams()
    report["output"] = {
        "target": str(output_path),
        "limit_bytes": params.output_limit_bytes,
    }
    # The outcome is part of the payload, so the measured size must already
    # include it; otherwise a report could pass the cap and still grow past it.
    report["output"].update({"written": True, "bytes": None, "status": "ok"})
    size = len(json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    if size > params.output_limit_bytes:
        report["output"].update(
            {
                "written": False,
                "bytes": size,
                "status": "output_limit_exceeded",
                "note": (
                    "the report exceeded the 256 MiB output cap; nothing was written and no "
                    "existing evidence file was overwritten or rotated. Stop collecting and "
                    "report, rather than raising the cap after seeing the result."
                ),
            }
        )
        return dict(report["output"])
    report["output"]["bytes"] = size
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(payload, encoding="utf-8")
    return dict(report["output"])


def analyze(records, manifest, params: AnalysisParams = None, started_at: float = None):
    """Run the whole pipeline and return the delivered report object.

    Never raises on bad data: a short run, a null block or a truncated file
    produce a labelled insufficient outcome, because the alternative is a crash
    exactly when there is something to report.
    """
    params = params or AnalysisParams()
    started = started_at if started_at is not None else time.monotonic()

    normalized: list = []
    for index, record in enumerate(records):
        entry = normalize_record(record, index, params)
        entry["_normalized"] = True
        normalized.append(entry)

    window_set = build_windows(records, params=params)
    gate = window_set.gate

    trends: dict = {}
    for name in WINDOW_METRIC_NAMES:
        trend = fit_duration_trend(window_set, name, params)
        trends[name] = attach_denominators(trend, window_set, name)

    # The cycle is fitted on the 5-second process-tree CPU series, which is the
    # series the 95 s observation came from.
    cycle = fit_cycle_model(
        [
            {"elapsed_seconds": record.get("elapsed_seconds"), "value": record.get("cpu_core_sum")}
            for record in normalized
        ],
        params,
    )
    trends["cycle_95s_cpu"] = {
        "status": cycle.get("status"),
        "metric": "cycle_95s_cpu",
        "unit": "cores",
        "cycle": cycle,
        "gate": gate,
        "threshold": None,
        "observations": cycle.get("samples"),
        "early_mean": cycle.get("mean_early"),
        "late_mean": cycle.get("mean_late"),
        "early_late_delta": cycle.get("mean_delta"),
        "slope": cycle.get("linear_slope_per_minute"),
        "slope_span_over_horizon": (
            cycle["linear_slope_per_minute"] * TREND_HORIZON_MINUTES
            if cycle.get("linear_slope_per_minute") is not None
            else None
        ),
        "notes": [
            "fixed 95 s sin/cos plus a linear time term on the 5 s CPU series; amplitude and mean "
            "are reported per half and the slope is also reported after removing the periodic term"
        ],
    }

    effects = {name: classify_duration_effect(trends[name], params) for name in WINDOW_METRIC_NAMES}
    gaps = collect_gap_events(normalized, params)
    strata = visibility_strata(window_set.windows)
    segmentation = _segmentation_report(window_set)

    growth = sorted(name for name, effect in effects.items() if effect["state"] == EFFECT_GROWTH)
    none_detected = sorted(
        name for name, effect in effects.items() if effect["state"] == EFFECT_NONE_DETECTED
    )
    insufficient = sorted(
        name for name, effect in effects.items() if effect["state"] == EFFECT_INSUFFICIENT
    )
    evidence = {
        "growth_metrics": growth,
        "none_detected_metrics": none_detected,
        "insufficient_metrics": insufficient,
        "metric_effects": effects,
        "cycle": cycle,
        "gaps": gaps,
        "visibility": strata,
        "gate": gate,
        "segmentation": segmentation,
        "download_metrics_available": _download_metrics_available(records),
    }
    hypotheses = hypothesis_states(evidence)

    elapsed_analysis = time.monotonic() - started
    cap_exceeded = elapsed_analysis > params.instrument_cap_seconds
    if growth:
        overall = EFFECT_GROWTH
    elif none_detected and gate.get("passed"):
        overall = EFFECT_NONE_DETECTED
    else:
        overall = EFFECT_INSUFFICIENT
    if cap_exceeded:
        overall = EFFECT_INSUFFICIENT

    verdict_reasons: list = []
    if cap_exceeded:
        verdict_reasons.append(
            f"the analysis exceeded the {params.instrument_cap_seconds:.0f} s instrument cap: "
            "partial results only, and no conclusion is issued"
        )
    elif not gate.get("passed"):
        verdict_reasons.append("data quality gate not met: " + "; ".join(gate.get("reasons", [])))

    report = {
        "tool": "analyze-duration-degradation.py",
        "plan_reference": "LingerLens v2 section W10 / L1",
        "generated_from": {
            "records_read": len(records),
            "records_analyzed": sum(1 for record in normalized if record.get("valid")),
            "records_excluded_past_instrument_cap": sum(
                1 for record in normalized if record.get("truncated_after_cap")
            ),
            "records_with_null_renderer_block": sum(
                1 for record in normalized if not record["observed"]["renderer_present"]
            ),
            "records_with_null_rvfc_block": sum(
                1 for record in normalized if not record["observed"]["rvfc_present"]
            ),
            "records_with_null_m0_block": sum(
                1 for record in normalized if not record["observed"]["m0_present"]
            ),
            "records_without_raf_callbacks": sum(
                1 for record in normalized if record.get("raf_has_callbacks") is False
            ),
        },
        "pre_registered_constants": _constant_table(params),
        "manifest": {
            "supplied": manifest,
            "completeness": _manifest_completeness(manifest),
        },
        "quality_gate": gate,
        "segmentation": segmentation,
        "windows": window_set.windows,
        "trends": trends,
        "metric_effects": effects,
        "cycle": cycle,
        "gap_events": gaps,
        "visibility_strata": strata,
        "stage_latency_note": (
            "the existing stage statistics are rolling windows: they are reported as the median of "
            "the five-minute rolling p95 readings. They are not an original per-cue p95, and "
            "averaging several rolling p95 values would not produce one."
        ),
        "counter_note": (
            "cumulative counters are used only as interval differences, and a zero denominator is "
            "not a measurement. Deadline expiry and drops are separate populations from failures "
            "and are reported separately, never summed into a 'total failure rate'."
        ),
        "hypotheses": hypotheses,
        "verdict": {
            "state": overall,
            "growth_metrics": growth,
            "none_detected_metrics": none_detected,
            "insufficient_metrics": insufficient,
            "reasons": verdict_reasons,
            "bounded_negative_conclusion": (
                BOUNDED_NEGATIVE_CONCLUSION if overall == EFFECT_NONE_DETECTED else None
            ),
            "forbidden_claims": list(FORBIDDEN_CLAIMS),
            "statements_this_run_cannot_support": [
                "all-day stability on this or any other machine",
                "the occlusion-unknown layer is undegraded",
                "unmeasured GPU or native retention is bounded",
                "the 95 s CPU cycle has one attributed cause",
                "the download leg is exonerated (this sampler records no per-leg bytes/PTS)",
                "any claim about resource use this sampler never read",
            ],
            "interpretation": (
                "even a clean two-hour run licenses only the bounded sentence above; it does not "
                "retire U3 as a work item and it does not license 'proved there is no leak'"
            ),
        },
        "instrument": {
            "analysis_seconds": elapsed_analysis,
            "instrument_cap_seconds": params.instrument_cap_seconds,
            "cap_exceeded": cap_exceeded,
            "bounded_exit": True,
            "output_limit_bytes": params.output_limit_bytes,
            "note": (
                "the 256 MiB cap and the 7260 s termination are enforced by the sampler and runner "
                "that own the process; this analyzer restates both and refuses to emit a report "
                "larger than the cap"
            ),
        },
        "analysis_seconds": elapsed_analysis,
    }
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Offline duration-degradation analysis for one L1 soak run (plan v2 section W10). "
            "No network, no provider calls, no product code touched."
        )
    )
    parser.add_argument("--input", required=True, help="this run's single lag-samples JSONL file")
    parser.add_argument(
        "--manifest",
        required=True,
        help="this experiment's manifest JSON (code SHA, config fingerprint, session identity)",
    )
    parser.add_argument("--output", required=True, help="path for this analysis' JSON report")
    parser.add_argument(
        "--output-limit-bytes",
        type=int,
        default=OUTPUT_LIMIT_BYTES,
        help="hard output cap for both the input and the report; default 256 MiB",
    )
    parser.add_argument(
        "--instrument-cap-seconds",
        type=float,
        default=INSTRUMENT_CAP_SECONDS,
        help="bounded termination for the instrument; default 7260 s",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress the stdout summary; the report file is unchanged",
    )
    args = parser.parse_args(argv)

    params = replace(
        AnalysisParams(),
        output_limit_bytes=args.output_limit_bytes,
        instrument_cap_seconds=args.instrument_cap_seconds,
    )
    input_path = Path(args.input)
    manifest_path = Path(args.manifest)
    output_path = Path(args.output)

    if not input_path.is_file():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 2
    input_bytes = input_path.stat().st_size
    if input_bytes > params.output_limit_bytes:
        print(
            f"ERROR: input is {input_bytes} bytes, already past the "
            f"{params.output_limit_bytes}-byte cap; refusing to read further and refusing to touch "
            "existing evidence",
            file=sys.stderr,
        )
        return 3

    manifest = None
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"ERROR: manifest unreadable: {error}", file=sys.stderr)
            return 2
    else:
        print(
            f"WARNING: manifest not found at {manifest_path}; the report will say the code SHA, "
            "configuration fingerprint and session identity are missing rather than guess them",
            file=sys.stderr,
        )

    started = time.monotonic()
    records, malformed = _load_jsonl(input_path)
    report = analyze(records, manifest, params, started_at=started)
    report["input"] = {
        "path": str(input_path),
        "bytes": input_bytes,
        "malformed_line_count": len(malformed),
        "malformed_lines": malformed[:50],
    }
    # ``write_report`` records the outcome inside the report before serializing,
    # so the file on disk states whether it was fully written.
    outcome = write_report(report, output_path, params)

    summary = {
        "state": report["verdict"]["state"],
        "quality_gate_passed": report["quality_gate"].get("passed"),
        "growth_metrics": report["verdict"]["growth_metrics"],
        "none_detected_metrics": report["verdict"]["none_detected_metrics"],
        "insufficient_metrics": report["verdict"]["insufficient_metrics"],
        "segments": report["quality_gate"].get("segments_total"),
        "output": outcome["status"],
        "analysis_seconds": round(report["analysis_seconds"], 3),
    }
    if not args.quiet:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not outcome["written"]:
        print(
            "ERROR: the report exceeded the output cap; bounded exit without touching old evidence",
            file=sys.stderr,
        )
        return 5
    if report["instrument"]["cap_exceeded"]:
        print(
            f"ERROR: analysis exceeded the {params.instrument_cap_seconds:.0f} s instrument cap; "
            "bounded exit with partial results only",
            file=sys.stderr,
        )
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
