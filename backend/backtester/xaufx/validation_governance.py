from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Sequence


PROMOTION_THRESHOLDS = {
    "min_train_trades": 12,
    "min_test_trades": 5,
    "min_walk_forward_test_trades_per_window": 2,
    "min_qualified_walk_forward_windows": 4,
    "min_train_return_pct": 0.0,
    "min_test_return_pct": 0.0,
    "min_return_retention": 0.35,
    "max_test_drawdown_pct": 3.0,
    "min_walk_forward_windows": 4,
    "min_walk_forward_positive_rate": 0.60,
    "min_walk_forward_median_return_pct": 0.0,
    "max_session_trade_share": 0.85,
    "min_distinct_entry_hours": 2,
    "cost_stress_spread_add": 0.25,
    "slippage_stress_spread_add": 0.50,
    "min_cost_stress_retention": 0.50,
    "min_slippage_stress_retention": 0.35,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: normalize_value(value[k]) for k in sorted(value)}
    if isinstance(value, set):
        return [normalize_value(v) for v in sorted(value)]
    if isinstance(value, (list, tuple)):
        return [normalize_value(v) for v in value]
    return value


def config_hash(config: Dict[str, Any]) -> str:
    payload = json.dumps(normalize_value(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def run_id(prefix: str = "xaufx") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def git_commit() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        return out
    except Exception:
        return "unknown"


def bars_window(candles: Sequence[Any]) -> Dict[str, Any]:
    if not candles:
        return {"bars": 0, "start": "", "end": ""}
    start = getattr(candles[0], "ts", None)
    end = getattr(candles[-1], "ts", None)
    return {
        "bars": len(candles),
        "start": start.isoformat() if start else "",
        "end": end.isoformat() if end else "",
    }


def with_metadata(
    row: Dict[str, Any],
    *,
    run_id_value: str,
    config: Dict[str, Any],
    intraday_window: Dict[str, Any],
    daily_window: Dict[str, Any],
    runner_name: str,
    notes: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    enriched = dict(row)
    enriched.update(
        {
            "run_id": run_id_value,
            "runner": runner_name,
            "generated_at": utc_now_iso(),
            "config_hash": config_hash(config),
            "code_version": git_commit(),
            "intraday_bars": intraday_window.get("bars", 0),
            "intraday_start": intraday_window.get("start", ""),
            "intraday_end": intraday_window.get("end", ""),
            "daily_bars": daily_window.get("bars", 0),
            "daily_start": daily_window.get("start", ""),
            "daily_end": daily_window.get("end", ""),
        }
    )
    if notes:
        enriched.update(notes)
    return enriched


def write_csv(path: Path, rows: list[dict]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return

    fieldnames: list[str] = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")


def validation_artifact_path(
    *,
    base_dir: Path,
    runner: str,
    config_hash_value: str,
    code_version: str,
    run_id_value: str,
) -> Path:
    safe_code_version = (code_version or "unknown")[:12]
    filename = f"{runner}__{config_hash_value}__{safe_code_version}__{run_id_value}.json"
    return base_dir / filename


def write_validation_artifact(
    *,
    base_dir: Path,
    runner: str,
    config_hash_value: str,
    code_version: str,
    run_id_value: str,
    payload: Dict[str, Any],
) -> Path:
    path = validation_artifact_path(
        base_dir=base_dir,
        runner=runner,
        config_hash_value=config_hash_value,
        code_version=code_version,
        run_id_value=run_id_value,
    )
    write_json(path, payload)
    return path


def dataclass_rows(items: Iterable[Any]) -> list[dict]:
    rows = []
    for item in items:
        if is_dataclass(item):
            rows.append(asdict(item))
        else:
            rows.append(dict(item))
    return rows


def safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def session_concentration_metrics(summary: Dict[str, Any]) -> Dict[str, Any]:
    hour_breakdown = summary.get("hour_breakdown", {}) or {}
    if not hour_breakdown:
        return {
            "top_hour": None,
            "top_hour_trade_count": 0,
            "top_hour_trade_share": None,
            "distinct_entry_hours": 0,
        }

    top_hour = None
    top_count = -1
    total = int(summary.get("trades", 0))
    for hour, stats in hour_breakdown.items():
        count = int(stats.get("count", 0))
        if count > top_count:
            top_hour = int(hour)
            top_count = count

    return {
        "top_hour": top_hour,
        "top_hour_trade_count": top_count,
        "top_hour_trade_share": safe_ratio(top_count, total),
        "distinct_entry_hours": len(hour_breakdown),
    }


def evaluate_promotion(
    *,
    train_trades: int,
    test_trades: int,
    train_return_pct: float,
    test_return_pct: float,
    return_retention_ratio: float | None,
    test_max_drawdown_pct: float,
    walk_forward_windows: int,
    qualified_walk_forward_windows: int,
    walk_forward_positive_rate: float | None,
    walk_forward_median_return_pct: float | None,
    cost_stress_return_pct: float | None = None,
    cost_stress_retention_ratio: float | None = None,
    slippage_stress_return_pct: float | None = None,
    slippage_stress_retention_ratio: float | None = None,
    session_top_hour_trade_share: float | None = None,
    distinct_entry_hours: int | None = None,
) -> Dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []

    if train_trades < PROMOTION_THRESHOLDS["min_train_trades"]:
        failures.append(f"train_trades<{PROMOTION_THRESHOLDS['min_train_trades']}")
    if test_trades < PROMOTION_THRESHOLDS["min_test_trades"]:
        failures.append(f"test_trades<{PROMOTION_THRESHOLDS['min_test_trades']}")
    if train_return_pct <= PROMOTION_THRESHOLDS["min_train_return_pct"]:
        failures.append("train_return<=0")
    if test_return_pct <= PROMOTION_THRESHOLDS["min_test_return_pct"]:
        failures.append("test_return<=0")
    if return_retention_ratio is None or return_retention_ratio < PROMOTION_THRESHOLDS["min_return_retention"]:
        failures.append(f"retention<{PROMOTION_THRESHOLDS['min_return_retention']}")
    if test_max_drawdown_pct > PROMOTION_THRESHOLDS["max_test_drawdown_pct"]:
        failures.append(f"test_dd>{PROMOTION_THRESHOLDS['max_test_drawdown_pct']}")
    if walk_forward_windows < PROMOTION_THRESHOLDS["min_walk_forward_windows"]:
        failures.append(f"wf_windows<{PROMOTION_THRESHOLDS['min_walk_forward_windows']}")
    if qualified_walk_forward_windows < PROMOTION_THRESHOLDS["min_qualified_walk_forward_windows"]:
        failures.append(
            f"wf_qualified_windows<{PROMOTION_THRESHOLDS['min_qualified_walk_forward_windows']}"
        )
    if walk_forward_positive_rate is None or walk_forward_positive_rate < PROMOTION_THRESHOLDS["min_walk_forward_positive_rate"]:
        failures.append(f"wf_positive_rate<{PROMOTION_THRESHOLDS['min_walk_forward_positive_rate']}")
    if walk_forward_median_return_pct is None or walk_forward_median_return_pct <= PROMOTION_THRESHOLDS["min_walk_forward_median_return_pct"]:
        failures.append("wf_median_return<=0")
    if session_top_hour_trade_share is None:
        warnings.append("session_share_missing")
    elif session_top_hour_trade_share > PROMOTION_THRESHOLDS["max_session_trade_share"]:
        failures.append(f"top_hour_share>{PROMOTION_THRESHOLDS['max_session_trade_share']}")
    if distinct_entry_hours is None:
        warnings.append("distinct_hours_missing")
    elif distinct_entry_hours < PROMOTION_THRESHOLDS["min_distinct_entry_hours"]:
        failures.append(f"distinct_hours<{PROMOTION_THRESHOLDS['min_distinct_entry_hours']}")

    if test_return_pct > 0:
        if cost_stress_return_pct is None:
            warnings.append("cost_stress_missing")
        elif cost_stress_return_pct <= 0:
            failures.append("cost_stress_return<=0")

        if cost_stress_retention_ratio is None:
            warnings.append("cost_stress_retention_missing")
        elif cost_stress_retention_ratio < PROMOTION_THRESHOLDS["min_cost_stress_retention"]:
            failures.append(f"cost_stress_retention<{PROMOTION_THRESHOLDS['min_cost_stress_retention']}")

        if slippage_stress_return_pct is None:
            warnings.append("slippage_stress_missing")
        elif slippage_stress_return_pct <= 0:
            failures.append("slippage_stress_return<=0")

        if slippage_stress_retention_ratio is None:
            warnings.append("slippage_stress_retention_missing")
        elif slippage_stress_retention_ratio < PROMOTION_THRESHOLDS["min_slippage_stress_retention"]:
            failures.append(f"slippage_stress_retention<{PROMOTION_THRESHOLDS['min_slippage_stress_retention']}")

    if not failures and test_return_pct > 0 and train_return_pct > 0:
        if (
            return_retention_ratio is not None and return_retention_ratio >= 0.75
            and walk_forward_positive_rate is not None and walk_forward_positive_rate >= 0.70
            and session_top_hour_trade_share is not None and session_top_hour_trade_share <= 0.70
            and cost_stress_retention_ratio is not None and cost_stress_retention_ratio >= 0.70
            and slippage_stress_retention_ratio is not None and slippage_stress_retention_ratio >= 0.50
        ):
            verdict = "research_winner"
        else:
            verdict = "promotable_baseline"
    elif test_return_pct > 0 and walk_forward_positive_rate is not None and walk_forward_positive_rate >= 0.5:
        verdict = "candidate"
    else:
        verdict = "reject"

    return {
        "verdict": verdict,
        "failure_reasons": failures,
        "warning_reasons": warnings,
        "thresholds": dict(PROMOTION_THRESHOLDS),
    }
