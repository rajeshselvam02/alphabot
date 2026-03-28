from __future__ import annotations

import argparse
import csv
import itertools
from pathlib import Path
from typing import Any, Dict, Iterable, List

from backend.core.xaufx.config import XAUFXConfig
from backend.core.xaufx.data_feeds.twelvedata_feed import TwelveDataFeed, TwelveDataQuotaExceeded
from backend.backtester.xaufx.backtest_xau_ndog_asia import run_backtest


def parse_csv_list(raw: str, cast):
    return [cast(x.strip()) for x in raw.split(",") if x.strip()]


def parse_hour_sets(raw: str) -> List[str]:
    parts = [x.strip() for x in raw.split(";") if x.strip()]
    return parts if parts else ["19,20"]


def allow_hours_from_string(s: str):
    if not s.strip():
        return None
    return {int(x.strip()) for x in s.split(",") if x.strip()}


def set_run_backtest_flags(
    *,
    no_mss: bool,
    no_fvg: bool,
    mss_disp: float,
    mss_lookback: int,
    pd_confluence: bool,
    pd_tolerance: float,
    stop_buffer: float,
    max_entry_extension_r: float,
    breakeven_r: float,
    trail_r: float,
    allow_hours,
    progress_check_bars: int,
    min_progress_r: float,
    max_risk_distance: float,
    max_risk_to_range: float,
    require_demand_zone: bool,
    demand_zone_tolerance: float,
) -> None:
    run_backtest._no_mss = no_mss
    run_backtest._no_fvg = no_fvg
    run_backtest._mss_disp = mss_disp
    run_backtest._mss_lookback = mss_lookback
    run_backtest._pd_confluence = pd_confluence
    run_backtest._pd_tolerance = pd_tolerance
    run_backtest._stop_buffer = stop_buffer
    run_backtest._max_entry_extension_r = max_entry_extension_r
    run_backtest._breakeven_r = breakeven_r
    run_backtest._trail_r = trail_r
    run_backtest._allow_hours = allow_hours
    run_backtest._progress_check_bars = progress_check_bars
    run_backtest._min_progress_r = min_progress_r
    run_backtest._max_risk_distance = max_risk_distance
    run_backtest._max_risk_to_range = max_risk_to_range
    run_backtest._require_demand_zone = require_demand_zone
    run_backtest._demand_zone_tolerance = demand_zone_tolerance


def flatten_result(
    params: Dict[str, Any],
    summary: Dict[str, Any],
) -> Dict[str, Any]:
    row: Dict[str, Any] = {}

    for k, v in params.items():
        if isinstance(v, set):
            row[k] = ",".join(str(x) for x in sorted(v))
        else:
            row[k] = v

    row.update(
        {
            "trades": summary["trades"],
            "longs": summary["longs"],
            "shorts": summary["shorts"],
            "win_rate": round(summary["win_rate"], 4),
            "return_pct": round(summary["return_pct"], 6),
            "sharpe": round(summary["sharpe"], 6),
            "max_dd_pct": round(summary["max_dd_pct"], 6),
            "equity": round(summary["equity"], 6),
            "avg_bars_held": round(summary["avg_bars_held"], 6),
            "daily_bias": summary["daily_bias"],
        }
    )

    diagnostics = summary.get("diagnostics", {})
    for k, v in diagnostics.items():
        row[f"diag_{k}"] = v

    reason_breakdown = summary.get("reason_breakdown", {})
    for reason in ("target", "session_end", "stop", "progress_stop", "final_mark"):
        stats = reason_breakdown.get(reason, {})
        row[f"reason_{reason}_count"] = stats.get("count", 0)
        row[f"reason_{reason}_pnl"] = round(stats.get("pnl", 0.0), 6)
        row[f"reason_{reason}_avg"] = round(stats.get("avg_pnl", 0.0), 6)

    stop_subtype_breakdown = summary.get("stop_subtype_breakdown", {})
    for subtype in ("breakeven_stop", "immediate_stop", "late_stop"):
        stats = stop_subtype_breakdown.get(subtype, {})
        row[f"stop_{subtype}_count"] = stats.get("count", 0)
        row[f"stop_{subtype}_pnl"] = round(stats.get("pnl", 0.0), 6)
        row[f"stop_{subtype}_avg"] = round(stats.get("avg_pnl", 0.0), 6)

    return row


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        with path.open("w", newline="", encoding="utf-8") as f:
            f.write("")
        return

    fieldnames: List[str] = []
    seen = set()
    for row in rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sweep XAU NDOG Asia configs and export summary CSV")
    parser.add_argument("--bars", type=int, default=10000)
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--risk", type=float, default=0.005)
    parser.add_argument("--spread", type=float, default=0.5)
    parser.add_argument("--target-r", type=float, default=2.0)
    parser.add_argument("--out", type=str, default="reports/xau_ndog_experiments.csv")

    parser.add_argument("--stop-buffers", type=str, default="2.0")
    parser.add_argument("--breakeven-rs", type=str, default="0.75")
    parser.add_argument("--trail-rs", type=str, default="1.5")
    parser.add_argument("--hour-sets", type=str, default="19,20")
    parser.add_argument("--max-risk-distances", type=str, default="60")
    parser.add_argument("--max-risk-to-ranges", type=str, default="0.7")

    parser.add_argument("--no-fvg", action="store_true")
    parser.add_argument("--no-mss", action="store_true")

    parser.add_argument("--mss-disp", type=float, default=0.75)
    parser.add_argument("--mss-lookback", type=int, default=2)

    parser.add_argument("--pd-confluence", action="store_true")
    parser.add_argument("--pd-tolerance", type=float, default=5.0)

    parser.add_argument("--max-entry-extension-r", type=float, default=0.5)
    parser.add_argument("--progress-check-bars", type=int, default=4)
    parser.add_argument("--min-progress-r", type=float, default=0.3)

    parser.add_argument("--require-demand-zone", action="store_true")
    parser.add_argument("--demand-zone-tolerance", type=float, default=10.0)

    args = parser.parse_args()

    stop_buffers = parse_csv_list(args.stop_buffers, float)
    breakeven_rs = parse_csv_list(args.breakeven_rs, float)
    trail_rs = parse_csv_list(args.trail_rs, float)
    hour_sets = parse_hour_sets(args.hour_sets)
    max_risk_distances = parse_csv_list(args.max_risk_distances, float)
    max_risk_to_ranges = parse_csv_list(args.max_risk_to_ranges, float)

    cfg = XAUFXConfig()
    feed = TwelveDataFeed(cfg.twelvedata_api_key)

    print(f"Fetching XAUUSD {cfg.intraday_interval} bars ({args.bars})...")
    try:
        candles = feed.fetch_bars("XAUUSD", cfg.intraday_interval, outputsize=args.bars)
        daily_candles = feed.fetch_bars("XAUUSD", cfg.daily_interval, outputsize=200)
    except TwelveDataQuotaExceeded as exc:
        print(f"Quota exhausted: {exc}")
        return

    print(f"Loaded {len(candles)} intraday bars")
    print(f"Loaded {len(daily_candles)} daily bars")
    if not candles or not daily_candles:
        print("No bars returned; aborting.")
        return

    rows: List[Dict[str, Any]] = []

    combos = list(
        itertools.product(
            stop_buffers,
            breakeven_rs,
            trail_rs,
            hour_sets,
            max_risk_distances,
            max_risk_to_ranges,
        )
    )

    print(f"Running {len(combos)} experiment(s)...")

    for idx, (
        stop_buffer,
        breakeven_r,
        trail_r,
        hour_set_str,
        max_risk_distance,
        max_risk_to_range,
    ) in enumerate(combos, start=1):
        allow_hours = allow_hours_from_string(hour_set_str)

        params = {
            "stop_buffer": stop_buffer,
            "breakeven_r": breakeven_r,
            "trail_r": trail_r,
            "allow_hours": allow_hours,
            "max_risk_distance": max_risk_distance,
            "max_risk_to_range": max_risk_to_range,
            "no_fvg": args.no_fvg,
            "no_mss": args.no_mss,
            "mss_disp": args.mss_disp,
            "mss_lookback": args.mss_lookback,
            "pd_confluence": args.pd_confluence,
            "pd_tolerance": args.pd_tolerance,
            "max_entry_extension_r": args.max_entry_extension_r,
            "progress_check_bars": args.progress_check_bars,
            "min_progress_r": args.min_progress_r,
            "require_demand_zone": args.require_demand_zone,
            "demand_zone_tolerance": args.demand_zone_tolerance,
            "bars": args.bars,
            "spread": args.spread,
            "target_r": args.target_r,
            "risk": args.risk,
        }

        print(
            f"[{idx}/{len(combos)}] "
            f"hours={hour_set_str} stop_buffer={stop_buffer} be={breakeven_r} "
            f"trail={trail_r} max_risk={max_risk_distance} risk_to_range={max_risk_to_range}"
        )

        set_run_backtest_flags(
            no_mss=args.no_mss,
            no_fvg=args.no_fvg,
            mss_disp=args.mss_disp,
            mss_lookback=args.mss_lookback,
            pd_confluence=args.pd_confluence,
            pd_tolerance=args.pd_tolerance,
            stop_buffer=stop_buffer,
            max_entry_extension_r=args.max_entry_extension_r,
            breakeven_r=breakeven_r,
            trail_r=trail_r,
            allow_hours=allow_hours,
            progress_check_bars=args.progress_check_bars,
            min_progress_r=args.min_progress_r,
            max_risk_distance=max_risk_distance,
            max_risk_to_range=max_risk_to_range,
            require_demand_zone=args.require_demand_zone,
            demand_zone_tolerance=args.demand_zone_tolerance,
        )

        _, _, summary = run_backtest(
            candles=candles,
            daily_candles=daily_candles,
            starting_equity=args.capital,
            risk_per_trade_pct=args.risk,
            spread_points=args.spread,
            target_r_multiple=args.target_r,
            timezone=cfg.timezone,
            session_cap=1,
        )

        row = flatten_result(params, summary)
        rows.append(row)

    rows.sort(key=lambda r: (r["return_pct"], r["sharpe"]), reverse=True)

    out = Path(args.out)
    write_csv(out, rows)
    print(f"\nSaved summary CSV: {out}")

    if rows:
        print("\nTop 10:")
        for row in rows[:10]:
            print(
                f"  return={row['return_pct']:.3f}% "
                f"sharpe={row['sharpe']:.3f} "
                f"dd={row['max_dd_pct']:.3f}% "
                f"trades={row['trades']} "
                f"hours={row['allow_hours']} "
                f"stop_buffer={row['stop_buffer']} "
                f"be={row['breakeven_r']} "
                f"trail={row['trail_r']} "
                f"max_risk={row['max_risk_distance']} "
                f"risk_to_range={row['max_risk_to_range']}"
            )


if __name__ == "__main__":
    main()
