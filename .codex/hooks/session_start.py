#!/usr/bin/env python3
from __future__ import annotations

import json
import sys


def main() -> int:
    _payload = json.load(sys.stdin)
    context = (
        "AlphaBot repo conventions: use /sdcard/DCIM/attachmentstermux as the project "
        "attachment folder for research artifacts in this session. Treat XAU/FX benchmark "
        "files as controlled assets: "
        "backend/backtester/xaufx/benchmark_profiles.py, "
        "backend/backtester/xaufx/benchmark_dataset.py, "
        "backend/backtester/xaufx/benchmark_regression.py, "
        "reports/benchmarks/xauusd_best_validated_v1_dataset.json. "
        "When those files or related XAU/FX benchmark runner files change, run the frozen "
        "benchmark regression before ending the turn."
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
