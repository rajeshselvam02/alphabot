from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class BenchmarkProfile:
    name: str
    description: str
    managed_args: Dict[str, Any]
    csv_path: str
    dataset_path: str
    dataset_hash: str
    expected_summary: Dict[str, Any]
    tolerances: Dict[str, float]
    notes: str = ""


XAUUSD_BEST_VALIDATED_V1 = BenchmarkProfile(
    name="xauusd_best_validated_v1",
    description=(
        "Locked benchmark for the best documented XAUUSD NDOG Asia validation case."
    ),
    managed_args={
        "bars": 10000,
        "capital": 10000.0,
        "risk": 0.005,
        "spread": 0.5,
        "target_r": 2.0,
        "no_mss": False,
        "no_fvg": True,
        "mss_disp": 0.75,
        "session_cap": 1,
        "mss_lookback": 2,
        "pd_confluence": False,
        "pd_tolerance": 5.0,
        "stop_buffer": 2.5,
        "max_entry_extension_r": 0.5,
        "breakeven_r": 1.0,
        "trail_r": 1.5,
        "allow_hours": "19,20",
        "progress_check_bars": 4,
        "min_progress_r": 0.3,
        "max_risk_distance": 60.0,
        "max_risk_to_range": 0.7,
        "require_demand_zone": False,
        "demand_zone_tolerance": 10.0,
        "force_daily_bias": "",
    },
    csv_path="reports/benchmarks/xauusd_best_validated_v1_trades.csv",
    dataset_path="reports/benchmarks/xauusd_best_validated_v1_dataset.json",
    dataset_hash="e4ef7288432aba280a3e5c905a3b163e04064b83de46e3e7fa9759d63d53221b",
    expected_summary={
        "trades": 9,
        "longs": 0,
        "shorts": 9,
        "win_rate": 11.11111111111111,
        "return_pct": -0.8452154136931493,
        "sharpe": -3.2057793026329793,
        "max_dd_pct": 2.6265015149314817,
        "equity": 9915.478458630685,
        "avg_bars_held": 12.333333333333334,
        "daily_bias": "bearish",
    },
    tolerances={
        "win_rate": 1e-9,
        "return_pct": 1e-9,
        "sharpe": 1e-9,
        "max_dd_pct": 1e-9,
        "equity": 1e-9,
        "avg_bars_held": 1e-9,
    },
    notes=(
        "Derived from the documented best validated XAUUSD test case in the project "
        "attachment note and promoted into the repo as the canonical benchmark."
    ),
)


BENCHMARK_PROFILES: Dict[str, BenchmarkProfile] = {
    XAUUSD_BEST_VALIDATED_V1.name: XAUUSD_BEST_VALIDATED_V1,
}


def get_profile(name: str) -> BenchmarkProfile:
    try:
        return BENCHMARK_PROFILES[name]
    except KeyError as exc:
        choices = ", ".join(sorted(BENCHMARK_PROFILES))
        raise KeyError(f"Unknown benchmark profile '{name}'. Available: {choices}") from exc
