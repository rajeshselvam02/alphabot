#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


BENCHMARK_RELATED_PATHS = (
    "backend/backtester/xaufx/backtest_xau_ndog_asia.py",
    "backend/backtester/xaufx/benchmark_profiles.py",
    "backend/backtester/xaufx/benchmark_dataset.py",
    "backend/backtester/xaufx/benchmark_regression.py",
    "backend/backtester/xaufx/validation_governance.py",
    "backend/backtester/xaufx/experiment_runner.py",
    "backend/backtester/xaufx/out_of_sample_runner.py",
    "docs/xaufx_benchmark_profile.md",
    "reports/benchmarks/xauusd_best_validated_v1_dataset.json",
)

REGRESSION_MARKERS = (
    "benchmark_regression",
    "xau benchmark regression",
    "regression check passed",
)


def git_status(repo_root: str) -> str:
    proc = subprocess.run(
        ["git", "-C", repo_root, "status", "--short"],
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.stdout


def benchmark_related_changes(status_output: str) -> list[str]:
    matches: list[str] = []
    for line in status_output.splitlines():
        for path in BENCHMARK_RELATED_PATHS:
            if path in line:
                matches.append(path)
    return sorted(set(matches))


def main() -> int:
    payload = json.load(sys.stdin)
    repo_root = payload.get("cwd") or str(Path.cwd())
    last_message = (payload.get("last_assistant_message") or "").lower()
    if payload.get("stop_hook_active"):
        print(json.dumps({"continue": True}))
        return 0

    changed = benchmark_related_changes(git_status(repo_root))
    if not changed:
        print(json.dumps({"continue": True}))
        return 0

    if any(marker in last_message for marker in REGRESSION_MARKERS):
        print(json.dumps({"continue": True}))
        return 0

    reason = (
        "Benchmark-related AlphaBot files changed without mentioning the frozen XAU benchmark "
        "regression. Run "
        "`python -m backend.backtester.xaufx.benchmark_regression --profile "
        "xauusd_best_validated_v1` and summarize the result before ending the turn."
    )
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
