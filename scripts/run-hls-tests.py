#!/usr/bin/env python3
"""Discover and run every HLS Companion test file.

Why this exists
---------------
The previous npm script hand-listed 25 of the 43 test files that actually exist,
so 18 files -- including the core segmentation tests
(test_punctuation_boundaries.py, test_caption_candidate_scorer.py) -- never ran
in CI. It also invoked a bare ``python`` without ``PYTHONPATH``, so on a machine
whose default interpreter is older than 3.10 it produced five false ERRORs, and
five more files failed to import the ``companion`` package at all.

This runner:
  * discovers tests instead of listing them, so a new file cannot be forgotten;
  * puts the package root on ``sys.path`` so ``from companion... import`` works;
  * runs each file in its own process with a timeout;
  * reports tests that need an optional third-party package as SKIP, not FAIL.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "prototype" / "hls-companion"
TESTS_DIR = PKG_ROOT / "tests"

# The Companion requires Python 3.10+ (asyncio.Event() binds a loop in 3.9 and
# fails outside a running one, which produced five false ERRORs in
# test_subtitle_pipeline.py). A bare `python` on a developer machine is often
# older, so re-exec under a suitable interpreter instead of reporting failures
# that are not code defects.
MIN_PYTHON = (3, 10)
_REEXEC_FLAG = "LAGLINGO_TEST_REEXEC"


def _find_modern_interpreter() -> str | None:
    import shutil

    candidates: list[str] = []
    launcher = shutil.which("py")
    if launcher:
        candidates.extend([f"{launcher} -3.13", f"{launcher} -3.12", f"{launcher} -3.11", f"{launcher} -3.10"])
    for name in ("python3.13", "python3.12", "python3.11", "python3.10", "python3"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    for candidate in candidates:
        parts = candidate.split()
        try:
            proc = subprocess.run(
                [*parts, "-c", "import sys; print(sys.version_info[:2])"],
                capture_output=True, text=True, timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode != 0:
            continue
        try:
            version = tuple(int(part) for part in proc.stdout.strip().strip("()").split(","))
        except ValueError:
            continue
        if version >= MIN_PYTHON:
            return candidate
    return None


def _ensure_interpreter() -> None:
    if sys.version_info >= MIN_PYTHON or os.environ.get(_REEXEC_FLAG):
        return
    replacement = _find_modern_interpreter()
    if replacement is None:
        print(
            f"ERROR: Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required "
            f"(running {sys.version.split()[0]}) and no newer interpreter was found.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    print(f"Re-running under {replacement} (this interpreter is {sys.version.split()[0]}).")
    env = dict(os.environ, **{_REEXEC_FLAG: "1"})
    completed = subprocess.run([*replacement.split(), str(Path(__file__).resolve()), *sys.argv[1:]], env=env)
    raise SystemExit(completed.returncode)

# Third-party packages a test may legitimately need but that are not runtime
# dependencies of the Companion. A missing one is a SKIP, not a failure.
OPTIONAL_MODULES = ("playwright", "streamlink")

SUMMARY_RE = re.compile(r"^(OK|FAILED)\b.*$", re.MULTILINE)
RAN_RE = re.compile(r"^Ran (\d+) tests?", re.MULTILINE)


def discover() -> list[Path]:
    return sorted(TESTS_DIR.glob("test_*.py"))


def uses_unittest(path: Path) -> bool:
    try:
        return "unittest" in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def run_file(path: Path, timeout: float) -> tuple[str, str, float, int]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(PKG_ROOT), existing) if p)
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "TIMEOUT", "", time.perf_counter() - started, 0
    elapsed = time.perf_counter() - started
    output = (proc.stdout or "") + (proc.stderr or "")
    ran_match = RAN_RE.search(output)
    count = int(ran_match.group(1)) if ran_match else 0
    if proc.returncode == 0:
        if count == 0 and uses_unittest(path):
            # A unittest file that exits 0 without running a single test is
            # silently green. test_recovery_policy.py did exactly this: it
            # defined five tests and never called unittest.main(). Report it
            # rather than counting it as a pass.
            return "EMPTY", output, elapsed, count
        # A script-style smoke test (e.g. test_browser_smoke.py) reports success
        # in its own words, so exit 0 is the signal and there is nothing to count.
        return "PASS", output, elapsed, count
    for module in OPTIONAL_MODULES:
        if f"No module named '{module}'" in output:
            return "SKIP", output, elapsed, count
    if "There is no current event loop" in output:
        return "PYVER", output, elapsed, count
    return "FAIL", output, elapsed, count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--only", default=None, help="substring filter on file name")
    args = parser.parse_args()

    _ensure_interpreter()

    files = discover()
    if args.only:
        files = [f for f in files if args.only in f.name]
    if not files:
        print("No test files discovered.", file=sys.stderr)
        return 2

    print(f"Running {len(files)} test files from {TESTS_DIR}")
    print(f"interpreter: {sys.executable}")
    print("=" * 78)

    counts: dict[str, int] = {}
    failures: list[tuple[str, str]] = []
    total_tests = 0
    started = time.perf_counter()

    for path in files:
        status, output, elapsed, count = run_file(path, args.timeout)
        counts[status] = counts.get(status, 0) + 1
        total_tests += count if status in {"PASS", "FAIL"} else 0
        label = f"{status:<8}"
        detail = f"{count:>4} tests" if count else "        "
        print(f"{label} {path.name:<44} {detail}  {elapsed:6.2f}s")
        if status in {"FAIL", "TIMEOUT", "PYVER", "EMPTY"}:
            failures.append((path.name, output))
        elif status == "SKIP" and not args.quiet:
            first = next(
                (ln for ln in output.splitlines() if "No module named" in ln), ""
            ).strip()
            print(f"         skipped: {first}")

    print("=" * 78)
    print(
        "  ".join(f"{k}={v}" for k, v in sorted(counts.items())),
        f"|  tests run={total_tests}",
        f"|  {time.perf_counter() - started:.1f}s",
    )

    if failures:
        print("\n" + "=" * 78)
        print("FAILURE DETAIL")
        print("=" * 78)
        for name, output in failures:
            print(f"\n----- {name} -----")
            tail = output.strip().splitlines()
            print("\n".join(tail[-60:]))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
