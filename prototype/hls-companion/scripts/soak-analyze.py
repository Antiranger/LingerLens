#!/usr/bin/env python3
"""Read-only analysis of one soak-live-monitor.py run; no provider calls.

Takes the run directory the monitor wrote and prints the handful of views that
answer "was this hour actually healthy?":

* ``TIME``                 -- first and last sample wall clock
* ``LATEST_CUES``          -- terminal state of every cue, deduplicated by id
* ``STALL_GROUPS``         -- every contiguous run of source stalls, showing the
                              sample before it, the peak, and the sample after
* ``TEN_MINUTE_PROGRESS``  -- the sample nearest each 10-minute mark
* ``COUNTER_CHANGES``      -- every change to (translationDropped,
                              translationFailures) with the error that came with it
* ``ERROR_SIGNATURES``     -- each distinct ingest log line that looks like an
                              error, deduplicated by (role, line)
* ``FINAL``                -- the last sample's subtitle status, verbatim

Every URL that can reach stdout goes through :func:`redact` first: ingest log
tails and provider error strings can carry signed, expiring manifest URLs, and a
soak log is a file people paste into issues.

Usage:
    py -3.10 prototype\\hls-companion\\scripts\\soak-analyze.py output\\soak\\20260915-180000
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import re
from pathlib import Path

URL_PATTERN = re.compile(r"https?://\S+")
# Free-text subtitle-status values that can carry a provider or manifest URL.
ERROR_FIELDS = ("lastError", "lastTranslationError", "startError")


def redact(text: str) -> str:
    """Replace every URL in *text* with ``<URL>``."""
    return URL_PATTERN.sub("<URL>", text)


def redact_error_fields(status: dict) -> dict:
    """Redact URLs inside the free-text error values of a subtitle status block.

    Only the values are rewritten. Redacting the serialized JSON instead would
    eat the surrounding quoting, because ``\\S+`` also matches ``"`` and ``,``.
    """
    result = dict(status)
    for field in ERROR_FIELDS:
        value = result.get(field)
        if isinstance(value, str):
            result[field] = redact(value)
    return result


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        raise SystemExit(f"missing {path.name} in {path.parent}: run soak-live-monitor.py first")
    rows: list[dict] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise SystemExit(f"{path.name}:{number}: not valid JSON: {error}") from error
    return rows


def seconds(row: dict) -> float:
    return float(row.get("elapsed") or 0.0)


def compact(row: dict) -> dict:
    sub = row.get("subtitles") or {}
    return dict(t=round(seconds(row), 1), media=row.get("privateMediaSeconds"),
                stall=row.get("sourceStallSeconds"), asr=sub.get("asrSeconds"),
                dropped=sub.get("translationDropped"), failed=sub.get("translationFailures"),
                ingest=[dict(role=x.get("role"), idle=x.get("sourceIdleSeconds"),
                             error=x.get("sourceError"), running=x.get("running"),
                             legs=[{k: leg.get(k) for k in ('label', 'forwardedBytes', 'sourcePtsLast')}
                                   for leg in x.get('legThroughput') or []]) for x in row.get('ingest') or []])


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only analysis of one soak-live-monitor.py run")
    parser.add_argument("run_dir", type=Path,
                        help="Run directory written by soak-live-monitor.py (holds samples.jsonl and cues.jsonl)")
    args = parser.parse_args()

    run_dir: Path = args.run_dir
    rows = load_jsonl(run_dir / "samples.jsonl")
    cues = load_jsonl(run_dir / "cues.jsonl")
    if not rows:
        raise SystemExit(f"no samples in {run_dir / 'samples.jsonl'}; nothing to analyse")
    latest = {cue.get('id'): cue for cue in sorted(cues, key=lambda cue: cue.get('seq') or 0)}

    groups: list[list[int]] = []
    for index, row in enumerate(rows):
        if (row.get('sourceStallSeconds') or 0) > 10:
            if not groups or index != groups[-1][-1] + 1:
                groups.append([])
            groups[-1].append(index)

    print('TIME', datetime.datetime.fromtimestamp(rows[0].get('wallTime') or 0.0).isoformat(),
          datetime.datetime.fromtimestamp(rows[-1].get('wallTime') or 0.0).isoformat())
    print('LATEST_CUES', dict(collections.Counter(cue.get('state') for cue in latest.values())), 'unique=', len(latest))

    print('STALL_GROUPS')
    for group in groups:
        peak = max(group, key=lambda i: rows[i].get('sourceStallSeconds') or 0.0)
        for i in sorted({max(0, group[0] - 1), group[0], peak, min(len(rows) - 1, group[-1] + 1)}):
            print(json.dumps(compact(rows[i])))

    print('TEN_MINUTE_PROGRESS')
    for minute in range(0, 61, 10):
        r = min(rows, key=lambda r: abs(seconds(r) - minute * 60))
        print(json.dumps(compact(r)))

    print('COUNTER_CHANGES')
    previous = (0, 0)
    for r in rows:
        s = r.get('subtitles') or {}
        current = (s.get('translationDropped', 0), s.get('translationFailures', 0))
        if current != previous:
            translation_error = s.get('lastTranslationError')
            print(round(seconds(r), 1), current,
                  redact(translation_error) if isinstance(translation_error, str) else translation_error,
                  'providerP95', s.get('translationProviderDelayP95'), 'readyP95', s.get('totalReadyDelayP95'))
        previous = current

    print('ERROR_SIGNATURES')
    seen = set()
    for r in rows:
        for ingest in r.get('ingest') or []:
            for line in ingest.get('logTail') or []:
                for part in line.splitlines():
                    if re.search(r'error|failed|timed out|reconnect|HTTP error|skipping|expired', part, re.I):
                        safe = redact(part)
                        key = (ingest.get('role'), safe)
                        if key not in seen:
                            print(round(seconds(r), 1), *key)
                            seen.add(key)

    print('FINAL', json.dumps(redact_error_fields(rows[-1].get('subtitles') or {})))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
