from __future__ import annotations

import argparse
import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

from backend.backtester.xaufx.backtest_xau_ndog_asia import run_backtest
from backend.core.analytics.validation_registry import validation_registry
from backend.core.xaufx.config import XAUFXConfig
from backend.core.xaufx.data_feeds.twelvedata_feed import (
    TwelveDataFeed,
    TwelveDataQuotaExceeded,
)
from backend.core.xaufx.models import Candle
from backend.backtester.xaufx.validation_governance import (
    PROMOTION_THRESHOLDS,
    bars_window,
    config_hash,
    dataclass_rows,
    evaluate_promotion,
    git_commit,
    run_id as new_run_id,
    safe_ratio,
    session_concentration_metrics,
    utc_now_iso,
    write_validation_artifact,
    write_csv,
    write_json,
)


@dataclass
class ExperimentResult:
    run_id: str
    runner: str
    config_hash: str
    code_version: str
    generated_at: str
    phase: str
    window_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    spread: float
    stop_buffer: float
    breakeven_r: float
    trail_r: float
    allow_hours: str
    max_risk_distance: float
    max_risk_to_range: float
    trades: int
    win_rate: float
    return_pct: float
    sharpe: float
    max_dd_pct: float
    equity: float
    avg_bars_held: float
    daily_bias: str
    score: float
    selection_rank: int
    validation_stage: str


@dataclass
class ValidationSummary:
    run_id: str
    runner: str
    config_hash: str
    selection_rank: int
    train_return_pct: float
    test_return_pct: float
    train_sharpe: float
    test_sharpe: float
    train_max_dd_pct: float
    test_max_dd_pct: float
    train_trades: int
    test_trades: int
    return_retention_ratio: float | None
    walk_forward_windows: int
    walk_forward_qualified_windows: int
    walk_forward_min_trades_per_window: int
    walk_forward_test_median_return_pct: float | None
    walk_forward_test_avg_return_pct: float | None
    walk_forward_positive_rate: float | None
    walk_forward_test_avg_sharpe: float | None
    cost_stress_spread: float
    cost_stress_return_pct: float | None
    cost_stress_retention_ratio: float | None
    slippage_stress_spread: float
    slippage_stress_return_pct: float | None
    slippage_stress_retention_ratio: float | None
    top_hour: int | None
    top_hour_trade_count: int
    top_hour_trade_share: float | None
    distinct_entry_hours: int
    verdict: str
    failure_reasons: str
    warning_reasons: str


def parse_csv_list(raw: str, cast) -> List[Any]:
    return [cast(x.strip()) for x in raw.split(",") if x.strip()]


def parse_hour_sets(raw: str) -> List[str]:
    vals = [x.strip() for x in raw.split(";") if x.strip()]
    return vals if vals else ["19,20"]


def allow_hours_from_string(raw: str):
    if not raw.strip():
        return None
    return {int(x.strip()) for x in raw.split(",") if x.strip()}


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
    force_daily_bias: str,
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
    run_backtest._force_daily_bias = force_daily_bias


def slice_recent_daily_candles(
    daily_candles: Sequence[Candle],
    end_ts,
    max_count: int = 200,
) -> List[Candle]:
    eligible = [c for c in daily_candles if c.ts <= end_ts]
    if len(eligible) > max_count:
        eligible = eligible[-max_count:]
    return eligible


def score_summary(summary: Dict[str, Any]) -> float:
    trades = int(summary.get("trades", 0))
    win_rate = float(summary.get("win_rate", 0.0))

    return (
        1.00 * float(summary["return_pct"])
        + 0.30 * float(summary["sharpe"])
        - 0.60 * float(summary["max_dd_pct"])
        + 0.05 * trades
        - 2.00 * abs(win_rate - 55.0) / 100.0
    )


def summarize_result(
    *,
    run_id: str,
    runner: str,
    selection_rank: int,
    phase: str,
    window_id: int,
    train_bars: Sequence[Candle],
    test_bars: Sequence[Candle],
    params: Dict[str, Any],
    summary: Dict[str, Any],
) -> ExperimentResult:
    train_start = train_bars[0].ts.isoformat() if train_bars else ""
    train_end = train_bars[-1].ts.isoformat() if train_bars else ""
    test_start = test_bars[0].ts.isoformat() if test_bars else ""
    test_end = test_bars[-1].ts.isoformat() if test_bars else ""

    return ExperimentResult(
        run_id=run_id,
        runner=runner,
        config_hash=config_hash(params),
        code_version=git_commit(),
        generated_at=utc_now_iso(),
        phase=phase,
        window_id=window_id,
        train_start=train_start,
        train_end=train_end,
        test_start=test_start,
        test_end=test_end,
        spread=float(params.get("spread", 0.0)),
        stop_buffer=float(params["stop_buffer"]),
        breakeven_r=float(params["breakeven_r"]),
        trail_r=float(params["trail_r"]),
        allow_hours=str(params["allow_hours"]),
        max_risk_distance=float(params["max_risk_distance"]),
        max_risk_to_range=float(params["max_risk_to_range"]),
        trades=int(summary["trades"]),
        win_rate=float(summary["win_rate"]),
        return_pct=float(summary["return_pct"]),
        sharpe=float(summary["sharpe"]),
        max_dd_pct=float(summary["max_dd_pct"]),
        equity=float(summary["equity"]),
        avg_bars_held=float(summary["avg_bars_held"]),
        daily_bias=str(summary["daily_bias"]),
        score=score_summary(summary),
        selection_rank=selection_rank,
        validation_stage="train_test" if window_id == 0 else "walk_forward",
    )


def generate_param_grid(args) -> List[Dict[str, Any]]:
    stop_buffers = parse_csv_list(args.stop_buffers, float)
    breakeven_rs = parse_csv_list(args.breakeven_rs, float)
    trail_rs = parse_csv_list(args.trail_rs, float)
    hour_sets = parse_hour_sets(args.hour_sets)
    max_risk_distances = parse_csv_list(args.max_risk_distances, float)
    max_risk_to_ranges = parse_csv_list(args.max_risk_to_ranges, float)

    if args.spread_values.strip():
        spread_values = parse_csv_list(args.spread_values, float)
    else:
        spread_values = [args.spread]

    combos = itertools.product(
        stop_buffers,
        breakeven_rs,
        trail_rs,
        hour_sets,
        max_risk_distances,
        max_risk_to_ranges,
        spread_values,
    )

    grid: List[Dict[str, Any]] = []
    for (
        stop_buffer,
        breakeven_r,
        trail_r,
        hour_set,
        max_risk_distance,
        max_risk_to_range,
        spread,
    ) in combos:
        grid.append(
            {
                "stop_buffer": stop_buffer,
                "breakeven_r": breakeven_r,
                "trail_r": trail_r,
                "allow_hours": hour_set,
                "max_risk_distance": max_risk_distance,
                "max_risk_to_range": max_risk_to_range,
                "spread": spread,
                "force_daily_bias": args.force_daily_bias or "inferred",
            }
        )
    return grid


def run_single_config(
    *,
    candles: Sequence[Candle],
    daily_candles: Sequence[Candle],
    cfg: XAUFXConfig,
    capital: float,
    risk: float,
    spread: float,
    target_r: float,
    no_mss: bool,
    no_fvg: bool,
    mss_disp: float,
    mss_lookback: int,
    pd_confluence: bool,
    pd_tolerance: float,
    max_entry_extension_r: float,
    progress_check_bars: int,
    min_progress_r: float,
    require_demand_zone: bool,
    demand_zone_tolerance: float,
    force_daily_bias: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    set_run_backtest_flags(
        no_mss=no_mss,
        no_fvg=no_fvg,
        mss_disp=mss_disp,
        mss_lookback=mss_lookback,
        pd_confluence=pd_confluence,
        pd_tolerance=pd_tolerance,
        stop_buffer=params["stop_buffer"],
        max_entry_extension_r=max_entry_extension_r,
        breakeven_r=params["breakeven_r"],
        trail_r=params["trail_r"],
        allow_hours=allow_hours_from_string(params["allow_hours"]),
        progress_check_bars=progress_check_bars,
        min_progress_r=min_progress_r,
        max_risk_distance=params["max_risk_distance"],
        max_risk_to_range=params["max_risk_to_range"],
        require_demand_zone=require_demand_zone,
        demand_zone_tolerance=demand_zone_tolerance,
        force_daily_bias=force_daily_bias,
    )

    _, _, summary = run_backtest(
        candles=list(candles),
        daily_candles=list(daily_candles),
        starting_equity=capital,
        risk_per_trade_pct=risk,
        spread_points=float(params.get("spread", spread)),
        target_r_multiple=target_r,
        timezone=cfg.timezone,
        session_cap=1,
    )
    return summary


def choose_best_params(
    *,
    train_bars: Sequence[Candle],
    daily_candles: Sequence[Candle],
    cfg: XAUFXConfig,
    args,
    grid: List[Dict[str, Any]],
) -> tuple[Dict[str, Any], Dict[str, Any], int]:
    best_params = None
    best_summary = None
    best_score = float("-inf")
    best_rank = 0

    for idx, params in enumerate(grid, start=1):
        print(
            f"[grid {idx}/{len(grid)}] "
            f"hours={params['allow_hours']} "
            f"sb={params['stop_buffer']} "
            f"be={params['breakeven_r']} "
            f"trail={params['trail_r']} "
            f"maxrisk={params['max_risk_distance']} "
            f"r/range={params['max_risk_to_range']} "
            f"spread={params.get('spread', args.spread)}",
            flush=True,
        )

        summary = run_single_config(
            candles=train_bars,
            daily_candles=daily_candles,
            cfg=cfg,
            capital=args.capital,
            risk=args.risk,
            spread=args.spread,
            target_r=args.target_r,
            no_mss=args.no_mss,
            no_fvg=args.no_fvg,
            mss_disp=args.mss_disp,
            mss_lookback=args.mss_lookback,
            pd_confluence=args.pd_confluence,
            pd_tolerance=args.pd_tolerance,
            max_entry_extension_r=args.max_entry_extension_r,
            progress_check_bars=args.progress_check_bars,
            min_progress_r=args.min_progress_r,
            require_demand_zone=args.require_demand_zone,
            demand_zone_tolerance=args.demand_zone_tolerance,
            force_daily_bias=args.force_daily_bias,
            params=params,
        )
        score = score_summary(summary)

        if score > best_score:
            best_score = score
            best_params = params
            best_summary = summary
            best_rank = idx

    assert best_params is not None and best_summary is not None
    return best_params, best_summary, best_rank


def build_validation_summary(
    *,
    run_id: str,
    runner: str,
    best_params: Dict[str, Any],
    selection_rank: int,
    train_summary: Dict[str, Any],
    test_summary: Dict[str, Any],
    wf_rows: List[ExperimentResult],
    test_bars: Sequence[Candle],
    daily_candles: Sequence[Candle],
    cfg: XAUFXConfig,
    args,
) -> ValidationSummary:
    wf_test_rows = [row for row in wf_rows if row.phase == "test"]
    min_wf_trades = int(PROMOTION_THRESHOLDS["min_walk_forward_test_trades_per_window"])
    qualified_wf_test_rows = [row for row in wf_test_rows if row.trades >= min_wf_trades]
    wf_returns = [row.return_pct for row in qualified_wf_test_rows]
    wf_sharpes = [row.sharpe for row in qualified_wf_test_rows]
    positive_rate = safe_ratio(sum(1 for value in wf_returns if value > 0), len(wf_returns)) if wf_returns else None
    avg_return = sum(wf_returns) / len(wf_returns) if wf_returns else None
    avg_sharpe = sum(wf_sharpes) / len(wf_sharpes) if wf_sharpes else None
    median_return = None
    if wf_returns:
        ordered = sorted(wf_returns)
        mid = len(ordered) // 2
        median_return = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
    retention = safe_ratio(float(test_summary["return_pct"]), float(train_summary["return_pct"]))
    session_metrics = session_concentration_metrics(test_summary)

    cost_stress_spread = float(best_params.get("spread", args.spread)) + float(PROMOTION_THRESHOLDS["cost_stress_spread_add"])
    slippage_stress_spread = float(best_params.get("spread", args.spread)) + float(PROMOTION_THRESHOLDS["slippage_stress_spread_add"])

    cost_stress_params = dict(best_params)
    cost_stress_params["spread"] = cost_stress_spread
    slippage_stress_params = dict(best_params)
    slippage_stress_params["spread"] = slippage_stress_spread

    cost_stress_summary = run_single_config(
        candles=test_bars,
        daily_candles=daily_candles,
        cfg=cfg,
        capital=args.capital,
        risk=args.risk,
        spread=cost_stress_spread,
        target_r=args.target_r,
        no_mss=args.no_mss,
        no_fvg=args.no_fvg,
        mss_disp=args.mss_disp,
        mss_lookback=args.mss_lookback,
        pd_confluence=args.pd_confluence,
        pd_tolerance=args.pd_tolerance,
        max_entry_extension_r=args.max_entry_extension_r,
        progress_check_bars=args.progress_check_bars,
        min_progress_r=args.min_progress_r,
        require_demand_zone=args.require_demand_zone,
        demand_zone_tolerance=args.demand_zone_tolerance,
        force_daily_bias=args.force_daily_bias,
        params=cost_stress_params,
    )
    slippage_stress_summary = run_single_config(
        candles=test_bars,
        daily_candles=daily_candles,
        cfg=cfg,
        capital=args.capital,
        risk=args.risk,
        spread=slippage_stress_spread,
        target_r=args.target_r,
        no_mss=args.no_mss,
        no_fvg=args.no_fvg,
        mss_disp=args.mss_disp,
        mss_lookback=args.mss_lookback,
        pd_confluence=args.pd_confluence,
        pd_tolerance=args.pd_tolerance,
        max_entry_extension_r=args.max_entry_extension_r,
        progress_check_bars=args.progress_check_bars,
        min_progress_r=args.min_progress_r,
        require_demand_zone=args.require_demand_zone,
        demand_zone_tolerance=args.demand_zone_tolerance,
        force_daily_bias=args.force_daily_bias,
        params=slippage_stress_params,
    )

    cost_stress_retention = safe_ratio(float(cost_stress_summary["return_pct"]), float(test_summary["return_pct"]))
    slippage_stress_retention = safe_ratio(float(slippage_stress_summary["return_pct"]), float(test_summary["return_pct"]))
    evaluation = evaluate_promotion(
        train_trades=int(train_summary["trades"]),
        test_trades=int(test_summary["trades"]),
        train_return_pct=float(train_summary["return_pct"]),
        test_return_pct=float(test_summary["return_pct"]),
        return_retention_ratio=retention,
        test_max_drawdown_pct=float(test_summary["max_dd_pct"]),
        walk_forward_windows=len(wf_test_rows),
        qualified_walk_forward_windows=len(qualified_wf_test_rows),
        walk_forward_positive_rate=positive_rate,
        walk_forward_median_return_pct=median_return,
        cost_stress_return_pct=float(cost_stress_summary["return_pct"]),
        cost_stress_retention_ratio=cost_stress_retention,
        slippage_stress_return_pct=float(slippage_stress_summary["return_pct"]),
        slippage_stress_retention_ratio=slippage_stress_retention,
        session_top_hour_trade_share=session_metrics["top_hour_trade_share"],
        distinct_entry_hours=session_metrics["distinct_entry_hours"],
    )
    return ValidationSummary(
        run_id=run_id,
        runner=runner,
        config_hash=config_hash(best_params),
        selection_rank=selection_rank,
        train_return_pct=float(train_summary["return_pct"]),
        test_return_pct=float(test_summary["return_pct"]),
        train_sharpe=float(train_summary["sharpe"]),
        test_sharpe=float(test_summary["sharpe"]),
        train_max_dd_pct=float(train_summary["max_dd_pct"]),
        test_max_dd_pct=float(test_summary["max_dd_pct"]),
        train_trades=int(train_summary["trades"]),
        test_trades=int(test_summary["trades"]),
        return_retention_ratio=retention,
        walk_forward_windows=len(wf_test_rows),
        walk_forward_qualified_windows=len(qualified_wf_test_rows),
        walk_forward_min_trades_per_window=min_wf_trades,
        walk_forward_test_median_return_pct=median_return,
        walk_forward_test_avg_return_pct=avg_return,
        walk_forward_positive_rate=positive_rate,
        walk_forward_test_avg_sharpe=avg_sharpe,
        cost_stress_spread=cost_stress_spread,
        cost_stress_return_pct=float(cost_stress_summary["return_pct"]),
        cost_stress_retention_ratio=cost_stress_retention,
        slippage_stress_spread=slippage_stress_spread,
        slippage_stress_return_pct=float(slippage_stress_summary["return_pct"]),
        slippage_stress_retention_ratio=slippage_stress_retention,
        top_hour=session_metrics["top_hour"],
        top_hour_trade_count=session_metrics["top_hour_trade_count"],
        top_hour_trade_share=session_metrics["top_hour_trade_share"],
        distinct_entry_hours=session_metrics["distinct_entry_hours"],
        verdict=evaluation["verdict"],
        failure_reasons=";".join(evaluation["failure_reasons"]),
        warning_reasons=";".join(evaluation["warning_reasons"]),
    )


def walk_forward_windows(
    candles: Sequence[Candle],
    train_size: int,
    test_size: int,
    step_size: int,
) -> List[tuple[List[Candle], List[Candle]]]:
    windows = []
    n = len(candles)
    start = 0
    while start + train_size + test_size <= n:
        train_bars = list(candles[start : start + train_size])
        test_bars = list(candles[start + train_size : start + train_size + test_size])
        windows.append((train_bars, test_bars))
        start += step_size
    return windows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train/test split and walk-forward evaluation for XAU NDOG Asia"
    )
    parser.add_argument("--bars", type=int, default=10000)
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--risk", type=float, default=0.005)
    parser.add_argument("--spread", type=float, default=0.5)
    parser.add_argument("--spread-values", type=str, default="")
    parser.add_argument("--target-r", type=float, default=2.0)

    parser.add_argument(
        "--out-train-test",
        type=str,
        default="reports/xau_ndog_train_test.csv",
    )
    parser.add_argument(
        "--out-walk-forward",
        type=str,
        default="reports/xau_ndog_walk_forward.csv",
    )

    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument("--wf-train-bars", type=int, default=4000)
    parser.add_argument("--wf-test-bars", type=int, default=1000)
    parser.add_argument("--wf-step-bars", type=int, default=1000)

    parser.add_argument("--stop-buffers", type=str, default="1.5,2.0,2.5")
    parser.add_argument("--breakeven-rs", type=str, default="0.5,0.75,1.0")
    parser.add_argument("--trail-rs", type=str, default="1.5")
    parser.add_argument("--hour-sets", type=str, default="19;19,20")
    parser.add_argument("--max-risk-distances", type=str, default="50,60,70")
    parser.add_argument("--max-risk-to-ranges", type=str, default="0.6,0.7,0.8")

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
    parser.add_argument(
        "--force-daily-bias",
        type=str,
        choices=["bullish", "bearish", "flat"],
        default="",
    )

    args = parser.parse_args()

    grid = generate_param_grid(args)

    cfg = XAUFXConfig()
    feed = TwelveDataFeed(cfg.twelvedata_api_key)
    current_run_id = new_run_id("xaufx_oos")

    print(f"Fetching XAUUSD {cfg.intraday_interval} bars ({args.bars})...")
    try:
        candles = feed.fetch_bars("XAUUSD", cfg.intraday_interval, outputsize=args.bars)
        daily_candles = feed.fetch_bars("XAUUSD", cfg.daily_interval, outputsize=400)
    except TwelveDataQuotaExceeded as exc:
        print(f"Quota exhausted: {exc}")
        return

    print(f"Loaded {len(candles)} intraday bars")
    print(f"Loaded {len(daily_candles)} daily bars")
    if not candles or not daily_candles:
        print("No bars returned; aborting.")
        return

    split_idx = int(len(candles) * args.train_frac)
    train_bars = candles[:split_idx]
    test_bars = candles[split_idx:]

    train_daily = slice_recent_daily_candles(daily_candles, train_bars[-1].ts, max_count=200)
    test_daily = slice_recent_daily_candles(daily_candles, test_bars[-1].ts, max_count=200)

    print(f"\nTrain/test split: train={len(train_bars)} bars, test={len(test_bars)} bars")
    print(f"Grid size: {len(grid)}")

    best_params, best_train_summary, best_rank = choose_best_params(
        train_bars=train_bars,
        daily_candles=train_daily,
        cfg=cfg,
        args=args,
        grid=grid,
    )

    best_test_summary = run_single_config(
        candles=test_bars,
        daily_candles=test_daily,
        cfg=cfg,
        capital=args.capital,
        risk=args.risk,
        spread=args.spread,
        target_r=args.target_r,
        no_mss=args.no_mss,
        no_fvg=args.no_fvg,
        mss_disp=args.mss_disp,
        mss_lookback=args.mss_lookback,
        pd_confluence=args.pd_confluence,
        pd_tolerance=args.pd_tolerance,
        max_entry_extension_r=args.max_entry_extension_r,
        progress_check_bars=args.progress_check_bars,
        min_progress_r=args.min_progress_r,
        require_demand_zone=args.require_demand_zone,
        demand_zone_tolerance=args.demand_zone_tolerance,
        force_daily_bias=args.force_daily_bias,
        params=best_params,
    )

    train_test_rows = [
        summarize_result(
            run_id=current_run_id,
            runner="out_of_sample_runner",
            selection_rank=best_rank,
            phase="train",
            window_id=0,
            train_bars=train_bars,
            test_bars=test_bars,
            params=best_params,
            summary=best_train_summary,
        ),
        summarize_result(
            run_id=current_run_id,
            runner="out_of_sample_runner",
            selection_rank=best_rank,
            phase="test",
            window_id=0,
            train_bars=train_bars,
            test_bars=test_bars,
            params=best_params,
            summary=best_test_summary,
        ),
    ]
    write_csv(Path(args.out_train_test), dataclass_rows(train_test_rows))

    print("\nBest train params:")
    print(best_params)
    print(
        f"Train: return={best_train_summary['return_pct']:.3f}% "
        f"sharpe={best_train_summary['sharpe']:.3f} "
        f"dd={best_train_summary['max_dd_pct']:.3f}% "
        f"trades={best_train_summary['trades']} "
        f"score={score_summary(best_train_summary):.3f}"
    )
    print(
        f"Test : return={best_test_summary['return_pct']:.3f}% "
        f"sharpe={best_test_summary['sharpe']:.3f} "
        f"dd={best_test_summary['max_dd_pct']:.3f}% "
        f"trades={best_test_summary['trades']} "
        f"score={score_summary(best_test_summary):.3f}"
    )
    print(f"Saved: {args.out_train_test}")

    windows = walk_forward_windows(
        candles=candles,
        train_size=args.wf_train_bars,
        test_size=args.wf_test_bars,
        step_size=args.wf_step_bars,
    )
    print(f"\nWalk-forward windows: {len(windows)}")

    wf_rows: List[ExperimentResult] = []
    for window_id, (wf_train_bars, wf_test_bars) in enumerate(windows, start=1):
        wf_train_daily = slice_recent_daily_candles(daily_candles, wf_train_bars[-1].ts, max_count=200)
        wf_test_daily = slice_recent_daily_candles(daily_candles, wf_test_bars[-1].ts, max_count=200)

        wf_best_params, wf_train_summary, wf_rank = choose_best_params(
            train_bars=wf_train_bars,
            daily_candles=wf_train_daily,
            cfg=cfg,
            args=args,
            grid=grid,
        )

        wf_test_summary = run_single_config(
            candles=wf_test_bars,
            daily_candles=wf_test_daily,
            cfg=cfg,
            capital=args.capital,
            risk=args.risk,
            spread=args.spread,
            target_r=args.target_r,
            no_mss=args.no_mss,
            no_fvg=args.no_fvg,
            mss_disp=args.mss_disp,
            mss_lookback=args.mss_lookback,
            pd_confluence=args.pd_confluence,
            pd_tolerance=args.pd_tolerance,
            max_entry_extension_r=args.max_entry_extension_r,
            progress_check_bars=args.progress_check_bars,
            min_progress_r=args.min_progress_r,
            require_demand_zone=args.require_demand_zone,
            demand_zone_tolerance=args.demand_zone_tolerance,
            force_daily_bias=args.force_daily_bias,
            params=wf_best_params,
        )

        wf_rows.append(
            summarize_result(
                run_id=current_run_id,
                runner="out_of_sample_runner",
                selection_rank=wf_rank,
                phase="train",
                window_id=window_id,
                train_bars=wf_train_bars,
                test_bars=wf_test_bars,
                params=wf_best_params,
                summary=wf_train_summary,
            )
        )
        wf_rows.append(
            summarize_result(
                run_id=current_run_id,
                runner="out_of_sample_runner",
                selection_rank=wf_rank,
                phase="test",
                window_id=window_id,
                train_bars=wf_train_bars,
                test_bars=wf_test_bars,
                params=wf_best_params,
                summary=wf_test_summary,
            )
        )

        print(
            f"[WF {window_id}] "
            f"train_ret={wf_train_summary['return_pct']:.3f}% "
            f"test_ret={wf_test_summary['return_pct']:.3f}% "
            f"test_sharpe={wf_test_summary['sharpe']:.3f} "
            f"test_dd={wf_test_summary['max_dd_pct']:.3f}% "
            f"test_score={score_summary(wf_test_summary):.3f} "
            f"hours={wf_best_params['allow_hours']} "
            f"stop_buffer={wf_best_params['stop_buffer']} "
            f"be={wf_best_params['breakeven_r']} "
            f"risk_to_range={wf_best_params['max_risk_to_range']} "
            f"spread={wf_best_params.get('spread', args.spread)}"
        )

    write_csv(Path(args.out_walk_forward), dataclass_rows(wf_rows))
    print(f"Saved: {args.out_walk_forward}")

    summary_row = build_validation_summary(
        run_id=current_run_id,
        runner="out_of_sample_runner",
        best_params=best_params,
        selection_rank=best_rank,
        train_summary=best_train_summary,
        test_summary=best_test_summary,
        wf_rows=wf_rows,
        test_bars=test_bars,
        daily_candles=test_daily,
        cfg=cfg,
        args=args,
    )
    summary_path = Path(args.out_train_test).with_name(Path(args.out_train_test).stem + "_summary.csv")
    write_csv(summary_path, dataclass_rows([summary_row]))
    print(f"Saved: {summary_path}")

    manifest_path = Path(args.out_train_test).with_name(Path(args.out_train_test).stem + "_manifest.json")
    manifest_payload = {
        "run_id": current_run_id,
        "runner": "out_of_sample_runner",
        "intraday_window": bars_window(candles),
        "daily_window": bars_window(daily_candles),
        "best_config_hash": config_hash(best_params),
        "best_params": best_params,
        "selection_rank": best_rank,
        "train_test_rows": len(train_test_rows),
        "walk_forward_rows": len(wf_rows),
        "summary": dataclass_rows([summary_row])[0],
    }
    write_json(manifest_path, manifest_payload)
    print(f"Saved: {manifest_path}")

    artifact_base = Path("reports/validation_artifacts/xaufx")
    code_version = git_commit()
    artifact_payload = {
        "artifact_kind": "xaufx_validation",
        "runner": "out_of_sample_runner",
        "run_id": current_run_id,
        "config_hash": config_hash(best_params),
        "code_version": code_version,
        "generated_at": utc_now_iso(),
        "intraday_window": bars_window(candles),
        "daily_window": bars_window(daily_candles),
        "best_config": best_params,
        "selection_rank": best_rank,
        "summary": dataclass_rows([summary_row])[0],
        "report_paths": {
            "train_test_csv": str(Path(args.out_train_test)),
            "walk_forward_csv": str(Path(args.out_walk_forward)),
            "summary_csv": str(summary_path),
            "manifest_json": str(manifest_path),
        },
    }
    artifact_path = write_validation_artifact(
        base_dir=artifact_base,
        runner="out_of_sample_runner",
        config_hash_value=artifact_payload["config_hash"],
        code_version=code_version,
        run_id_value=current_run_id,
        payload=artifact_payload,
    )
    print(f"Saved validation artifact: {artifact_path}")
    validation_registry.register_validation_artifact(
        artifact_path=str(artifact_path),
        runner="out_of_sample_runner",
        config_hash=artifact_payload["config_hash"],
        code_version=code_version,
        verdict=artifact_payload["summary"].get("verdict"),
        metrics=artifact_payload["summary"],
        config=best_params,
        notes="Registered from XAU/FX out-of-sample validation artifact.",
    )


if __name__ == "__main__":
    main()
