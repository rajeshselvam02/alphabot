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
