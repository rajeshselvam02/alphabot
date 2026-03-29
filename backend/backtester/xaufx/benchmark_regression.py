from __future__ import annotations

import argparse
import math
from typing import Any, Dict

from backend.backtester.xaufx.backtest_xau_ndog_asia import run_backtest
from backend.backtester.xaufx.benchmark_dataset import load_snapshot
from backend.backtester.xaufx.benchmark_profiles import get_profile
from backend.core.xaufx.config import XAUFXConfig


def apply_profile_to_run_backtest(args: Dict[str, Any]) -> None:
    run_backtest._no_mss = args["no_mss"]
    run_backtest._no_fvg = args["no_fvg"]
    run_backtest._mss_disp = args["mss_disp"]
    run_backtest._mss_lookback = args["mss_lookback"]
    run_backtest._pd_confluence = args["pd_confluence"]
    run_backtest._pd_tolerance = args["pd_tolerance"]
    run_backtest._stop_buffer = args["stop_buffer"]
    run_backtest._max_entry_extension_r = args["max_entry_extension_r"]
    run_backtest._breakeven_r = args["breakeven_r"]
    run_backtest._trail_r = args["trail_r"]
    run_backtest._allow_hours = {
        int(x.strip()) for x in args["allow_hours"].split(",") if x.strip()
    } if args["allow_hours"].strip() else None
    run_backtest._progress_check_bars = args["progress_check_bars"]
    run_backtest._min_progress_r = args["min_progress_r"]
    run_backtest._max_risk_distance = args["max_risk_distance"]
    run_backtest._max_risk_to_range = args["max_risk_to_range"]
    run_backtest._require_demand_zone = args["require_demand_zone"]
    run_backtest._demand_zone_tolerance = args["demand_zone_tolerance"]
    run_backtest._force_daily_bias = args["force_daily_bias"]


def compare_summary(summary: Dict[str, Any], expected: Dict[str, Any], tolerances: Dict[str, float]) -> list[str]:
    failures: list[str] = []
    for key, expected_value in expected.items():
        actual = summary[key]
        if isinstance(expected_value, str):
            if actual != expected_value:
                failures.append(f"{key}: expected {expected_value!r}, got {actual!r}")
            continue

        tolerance = tolerances.get(key, 0.0)
        if math.fabs(float(actual) - float(expected_value)) > tolerance:
            failures.append(
                f"{key}: expected {expected_value:.12f}, got {float(actual):.12f}, tolerance {tolerance:.12f}"
            )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Run frozen XAU benchmark regression checks")
    parser.add_argument("--profile", type=str, default="xauusd_best_validated_v1")
    parser.add_argument("--snapshot", type=str, default="@profile")
    args = parser.parse_args()

    profile = get_profile(args.profile)
    snapshot_path = profile.dataset_path if args.snapshot == "@profile" else args.snapshot
    snapshot = load_snapshot(snapshot_path)

    if snapshot["dataset_hash"] != profile.dataset_hash:
        raise SystemExit(
            "dataset hash mismatch: "
            f"expected {profile.dataset_hash}, got {snapshot['dataset_hash']}"
        )

    apply_profile_to_run_backtest(profile.managed_args)
    summary = run_backtest(
        candles=snapshot["intraday_candles"],
        daily_candles=snapshot["daily_candles"],
        starting_equity=profile.managed_args["capital"],
        risk_per_trade_pct=profile.managed_args["risk"],
        spread_points=profile.managed_args["spread"],
        target_r_multiple=profile.managed_args["target_r"],
        timezone=XAUFXConfig().timezone,
        session_cap=profile.managed_args["session_cap"],
    )[2]

    failures = compare_summary(summary, profile.expected_summary, profile.tolerances)

    print(f"Profile: {profile.name}")
    print(f"Snapshot: {snapshot_path}")
    print(f"Dataset hash: {snapshot['dataset_hash']}")
    print("Checked metrics:")
    for key in profile.expected_summary:
        print(f"  {key}: {summary[key]!r}")

    if failures:
        print("\nRegression check failed:")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)

    print("\nRegression check passed.")


if __name__ == "__main__":
    main()
