from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Tuple

from backend.core.xaufx.config import XAUFXConfig
from backend.core.xaufx.data_feeds.twelvedata_feed import TwelveDataFeed, TwelveDataQuotaExceeded
from backend.core.xaufx.models import Candle
from backend.core.xaufx.sessions.clock import NYSessionClock
from backend.core.xaufx.strategies.xau_daily_momentum import daily_bias
from backend.core.xaufx.strategies.xau_ndog_asia import XAUNDOGAsiaStrategy
from backend.core.xaufx.detectors.demand_zone import detect_recent_demand_zone


@dataclass
class Trade:
    entry_idx: int
    exit_idx: int
    side: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    qty: float
    pnl: float
    return_pct: float
    bars_held: int
    reason: str
    daily_bias: str
    mfe: float
    mae: float
    mfe_r: float
    mae_r: float
    stop_subtype: str


def max_drawdown(equity_curve: List[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    mdd = 0.0
    for x in equity_curve:
        peak = max(peak, x)
        dd = (peak - x) / peak if peak > 0 else 0.0
        mdd = max(mdd, dd)
    return mdd


def sharpe_ratio(returns: List[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    var = sum((x - mean_r) ** 2 for x in returns) / (len(returns) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (mean_r / std) * math.sqrt(252.0)


def calc_pnl(side: str, qty: float, entry: float, exit_price: float) -> float:
    if side == "BUY":
        return (exit_price - entry) * qty
    return (entry - exit_price) * qty


def in_asia(clock: NYSessionClock, candle: Candle) -> bool:
    return clock.label(candle.ts) == "ASIA"


def export_trades_csv(trades: List[Trade], path: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(trades[0]).keys()) if trades else [
            "entry_idx", "exit_idx", "side", "entry_time", "exit_time",
            "entry_price", "exit_price", "stop_price", "target_price",
            "qty", "pnl", "return_pct", "bars_held", "reason", "daily_bias"
        ])
        writer.writeheader()
        for t in trades:
            writer.writerow(asdict(t))


def run_backtest(
    candles: List[Candle],
    daily_candles: List[Candle],
    starting_equity: float,
    risk_per_trade_pct: float,
    spread_points: float,
    target_r_multiple: float,
    timezone: str,
    session_cap: int,
) -> Tuple[List[Trade], List[float], dict]:
    bias = daily_bias("XAUUSD", daily_candles)
    demand_zone = detect_recent_demand_zone(daily_candles)

    strategy = XAUNDOGAsiaStrategy(
        timezone=timezone,
        mss_lookback=getattr(run_backtest, "_mss_lookback", 2),
        mss_displacement=getattr(run_backtest, "_mss_disp", 0.75),
        require_mss=not getattr(run_backtest, "_no_mss", False),
        require_fvg=not getattr(run_backtest, "_no_fvg", False),
        daily_bias=bias,
        require_pd_confluence=getattr(run_backtest, "_pd_confluence", False),
        pd_tolerance=getattr(run_backtest, "_pd_tolerance", 5.0),
        stop_buffer=getattr(run_backtest, "_stop_buffer", 1.0),
        max_entry_extension_r=getattr(run_backtest, "_max_entry_extension_r", 0.5),
        demand_zone=demand_zone,
        require_demand_zone=getattr(run_backtest, "_require_demand_zone", False),
        demand_zone_tolerance=getattr(run_backtest, "_demand_zone_tolerance", 10.0),
    )
    clock = NYSessionClock(timezone)

    equity = starting_equity
    equity_curve: List[float] = [equity]
    trades: List[Trade] = []

    diagnostics = {
        "windows_checked": 0,
        "ndog_found": 0,
        "asia_ready": 0,
        "sweep_reclaim": 0,
        "mss_match": 0,
        "fvg_match": 0,
        "entry_valid": 0,
        "risk_distance_reject": 0,
        "risk_to_range_reject": 0,
    }

    in_position = False
    session_trade_count = 0
    prev_session_label = None

    side = ""
    entry_idx = -1
    entry_price = 0.0
    stop_price = 0.0
    target_price = 0.0
    qty = 0.0
    entry_initial_r = 0.0
    trade_mfe = 0.0
    trade_mae = 0.0

    start_idx = 10

    for i in range(start_idx, len(candles)):
        window = candles[: i + 1]
        last = candles[i]
        diagnostics["windows_checked"] += 1

        current_session_label = clock.label(last.ts)
        if current_session_label != prev_session_label:
            if current_session_label == "ASIA":
                session_trade_count = 0
            prev_session_label = current_session_label

        if in_position:
            unreal = calc_pnl(side, qty, entry_price, last.close)
            equity_curve.append(equity + unreal)
        else:
            equity_curve.append(equity)

        if in_position:
            exit_reason = ""
            exit_price = None

            breakeven_r = getattr(run_backtest, "_breakeven_r", 1.0)
            trail_r = getattr(run_backtest, "_trail_r", 1.5)

            initial_r = abs(entry_price - stop_price)
            if initial_r > 0:
                if side == "BUY":
                    max_favorable = last.high - entry_price
                    achieved_r = max_favorable / initial_r

                    if achieved_r >= breakeven_r:
                        stop_price = max(stop_price, entry_price)

                    if achieved_r >= trail_r and i >= 2:
                        trail_stop = min(c.low for c in candles[max(0, i - 2): i])
                        stop_price = max(stop_price, trail_stop)

                else:
                    max_favorable = entry_price - last.low
                    achieved_r = max_favorable / initial_r

                    if achieved_r >= breakeven_r:
                        stop_price = min(stop_price, entry_price)

                    if achieved_r >= trail_r and i >= 2:
                        trail_stop = max(c.high for c in candles[max(0, i - 2): i])
                        stop_price = min(stop_price, trail_stop)

            if entry_initial_r > 0:
                if side == "BUY":
                    favorable = max(0.0, last.high - entry_price)
                    adverse = max(0.0, entry_price - last.low)
                else:
                    favorable = max(0.0, entry_price - last.low)
                    adverse = max(0.0, last.high - entry_price)

                trade_mfe = max(trade_mfe, favorable)
                trade_mae = max(trade_mae, adverse)

            progress_check_bars = getattr(run_backtest, "_progress_check_bars", 4)
            min_progress_r = getattr(run_backtest, "_min_progress_r", 0.3)

            if entry_initial_r > 0 and (i - entry_idx) >= progress_check_bars:
                if (trade_mfe / entry_initial_r) < min_progress_r:
                    exit_reason = "progress_stop"
                    if side == "BUY":
                        exit_price = last.close - spread_points / 2.0
                    else:
                        exit_price = last.close + spread_points / 2.0

            if exit_price is None and side == "BUY":
                if last.low <= stop_price:
                    exit_reason = "stop"
                    exit_price = stop_price - spread_points / 2.0
                elif last.high >= target_price:
                    exit_reason = "target"
                    exit_price = target_price - spread_points / 2.0
            else:
                if last.high >= stop_price:
                    exit_reason = "stop"
                    exit_price = stop_price + spread_points / 2.0
                elif last.low <= target_price:
                    exit_reason = "target"
                    exit_price = target_price + spread_points / 2.0

            if exit_price is None and not in_asia(clock, last):
                exit_reason = "session_end"
                if side == "BUY":
                    exit_price = last.close - spread_points / 2.0
                else:
                    exit_price = last.close + spread_points / 2.0

            if exit_price is not None:
                pnl = calc_pnl(side, qty, entry_price, exit_price)
                equity += pnl

                stop_subtype = ""
                if exit_reason == "stop":
                    if entry_initial_r > 0 and abs(stop_price - entry_price) < 1e-9:
                        stop_subtype = "breakeven_stop"
                    elif i - entry_idx <= 2:
                        stop_subtype = "immediate_stop"
                    else:
                        stop_subtype = "late_stop"

                trades.append(
                    Trade(
                        entry_idx=entry_idx,
                        exit_idx=i,
                        side=side,
                        entry_time=candles[entry_idx].ts.isoformat(),
                        exit_time=last.ts.isoformat(),
                        entry_price=entry_price,
                        exit_price=exit_price,
                        stop_price=stop_price,
                        target_price=target_price,
                        qty=qty,
                        pnl=pnl,
                        return_pct=(pnl / starting_equity) * 100.0,
                        bars_held=i - entry_idx,
                        reason=exit_reason,
                        daily_bias=bias,
                        mfe=trade_mfe,
                        mae=trade_mae,
                        mfe_r=(trade_mfe / entry_initial_r) if entry_initial_r > 0 else 0.0,
                        mae_r=(trade_mae / entry_initial_r) if entry_initial_r > 0 else 0.0,
                        stop_subtype=stop_subtype,
                    )
                )
                in_position = False
                side = ""
                entry_idx = -1
                entry_price = 0.0
                stop_price = 0.0
                target_price = 0.0
                qty = 0.0
                continue

        if not in_position:
            recent = window[-24:]
            setup = strategy.evaluate_setup("XAUUSD", recent)

            if setup["ndog_found"]:
                diagnostics["ndog_found"] += 1
            if setup["asia_ready"]:
                diagnostics["asia_ready"] += 1
            if setup["sweep_reclaim"]:
                diagnostics["sweep_reclaim"] += 1
            if setup["mss_match"]:
                diagnostics["mss_match"] += 1
            if setup["fvg_match"]:
                diagnostics["fvg_match"] += 1

            signal = strategy.generate("XAUUSD", recent)

            if signal.side in {"BUY", "SELL"} and signal.entry:
                if session_trade_count >= session_cap:
                    continue

                if not in_asia(clock, last):
                    continue

                allow_hours = getattr(run_backtest, "_allow_hours", None)
                if allow_hours is not None:
                    entry_hour = clock.to_ny(last.ts).hour
                    if entry_hour not in allow_hours:
                        continue

                diagnostics["entry_valid"] += 1

                stop = signal.stop if signal.stop is not None else (
                    min(c.low for c in recent[-3:]) if signal.side == "BUY"
                    else max(c.high for c in recent[-3:])
                )

                risk_distance = abs(signal.entry - stop)
                if risk_distance <= 0:
                    continue

                max_risk_distance = getattr(run_backtest, "_max_risk_distance", 80.0)
                if risk_distance > max_risk_distance:
                    diagnostics["risk_distance_reject"] += 1
                    continue

                recent_slice = recent[-8:] if len(recent) >= 8 else recent
                recent_range = (max(c.high for c in recent_slice) - min(c.low for c in recent_slice)) if recent_slice else 0.0
                if recent_range > 0:
                    risk_to_range = risk_distance / recent_range
                    max_risk_to_range = getattr(run_backtest, "_max_risk_to_range", 0.8)
                    if risk_to_range > max_risk_to_range:
                        diagnostics["risk_to_range_reject"] += 1
                        continue

                risk_amount = equity * risk_per_trade_pct
                qty_candidate = risk_amount / risk_distance
                if qty_candidate <= 0:
                    continue

                if signal.side == "BUY":
                    fill_entry = signal.entry + spread_points / 2.0
                    tgt = fill_entry + target_r_multiple * risk_distance
                else:
                    fill_entry = signal.entry - spread_points / 2.0
                    tgt = fill_entry - target_r_multiple * risk_distance

                in_position = True
                session_trade_count += 1
                side = signal.side
                entry_idx = i
                entry_price = fill_entry
                stop_price = stop
                target_price = tgt
                qty = qty_candidate
                entry_initial_r = abs(fill_entry - stop)
                trade_mfe = 0.0
                trade_mae = 0.0

    if in_position:
        final_close = candles[-1].close
        exit_price = final_close - spread_points / 2.0 if side == "BUY" else final_close + spread_points / 2.0
        pnl = calc_pnl(side, qty, entry_price, exit_price)
        equity += pnl
        trades.append(
            Trade(
                entry_idx=entry_idx,
                exit_idx=len(candles) - 1,
                side=side,
                entry_time=candles[entry_idx].ts.isoformat(),
                exit_time=candles[-1].ts.isoformat(),
                entry_price=entry_price,
                exit_price=exit_price,
                stop_price=stop_price,
                target_price=target_price,
                qty=qty,
                pnl=pnl,
                return_pct=(pnl / starting_equity) * 100.0,
                bars_held=(len(candles) - 1) - entry_idx,
                reason="final_mark",
                daily_bias=bias,
                mfe=trade_mfe,
                mae=trade_mae,
                mfe_r=(trade_mfe / entry_initial_r) if entry_initial_r > 0 else 0.0,
                mae_r=(trade_mae / entry_initial_r) if entry_initial_r > 0 else 0.0,
                stop_subtype="",
            )
        )
        equity_curve[-1] = equity

    wins = sum(1 for t in trades if t.pnl > 0)
    returns = [t.pnl / starting_equity for t in trades]
    long_count = sum(1 for t in trades if t.side == "BUY")
    short_count = sum(1 for t in trades if t.side == "SELL")

    reason_breakdown = {}
    for t in trades:
        bucket = reason_breakdown.setdefault(t.reason, {"count": 0, "pnl": 0.0})
        bucket["count"] += 1
        bucket["pnl"] += t.pnl

    for bucket in reason_breakdown.values():
        bucket["avg_pnl"] = bucket["pnl"] / bucket["count"] if bucket["count"] else 0.0

    stop_subtype_breakdown = {}
    for t in trades:
        if t.reason != "stop" or not t.stop_subtype:
            continue
        bucket = stop_subtype_breakdown.setdefault(t.stop_subtype, {"count": 0, "pnl": 0.0})
        bucket["count"] += 1
        bucket["pnl"] += t.pnl

    for bucket in stop_subtype_breakdown.values():
        bucket["avg_pnl"] = bucket["pnl"] / bucket["count"] if bucket["count"] else 0.0

    from zoneinfo import ZoneInfo
    ny_tz = ZoneInfo(timezone)
    hour_breakdown = {}
    for t in trades:
        entry_dt = candles[t.entry_idx].ts.astimezone(ny_tz)
        hour = entry_dt.hour
        bucket = hour_breakdown.setdefault(hour, {"count": 0, "wins": 0, "pnl": 0.0})
        bucket["count"] += 1
        bucket["wins"] += 1 if t.pnl > 0 else 0
        bucket["pnl"] += t.pnl

    for bucket in hour_breakdown.values():
        bucket["avg_pnl"] = bucket["pnl"] / bucket["count"] if bucket["count"] else 0.0
        bucket["win_rate"] = (bucket["wins"] / bucket["count"] * 100.0) if bucket["count"] else 0.0

    summary = {
        "trades": len(trades),
        "longs": long_count,
        "shorts": short_count,
        "win_rate": (wins / len(trades) * 100.0) if trades else 0.0,
        "return_pct": ((equity - starting_equity) / starting_equity) * 100.0,
        "sharpe": sharpe_ratio(returns),
        "max_dd_pct": max_drawdown(equity_curve) * 100.0,
        "equity": equity,
        "avg_bars_held": (sum(t.bars_held for t in trades) / len(trades)) if trades else 0.0,
        "daily_bias": bias,
        "diagnostics": diagnostics,
        "reason_breakdown": reason_breakdown,
        "stop_subtype_breakdown": stop_subtype_breakdown,
        "hour_breakdown": hour_breakdown,
    }
    return trades, equity_curve, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest XAU NDOG Asia strategy")
    parser.add_argument("--bars", type=int, default=1200, help="number of intraday bars to fetch")
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--risk", type=float, default=0.005, help="risk per trade fraction")
    parser.add_argument("--spread", type=float, default=0.5, help="spread in XAU points")
    parser.add_argument("--target-r", type=float, default=2.0, help="target multiple of risk")
    parser.add_argument("--no-mss", action="store_true", help="disable confirmation requirement")
    parser.add_argument("--no-fvg", action="store_true", help="disable FVG requirement")
    parser.add_argument("--mss-disp", type=float, default=0.75, help="confirmation displacement setting")
    parser.add_argument("--session-cap", type=int, default=1, help="max entries per Asia session")
    parser.add_argument("--mss-lookback", type=int, default=2, help="confirmation lookback")
    parser.add_argument("--pd-confluence", action="store_true", help="keep PD options enabled")
    parser.add_argument("--pd-tolerance", type=float, default=5.0, help="distance tolerance to PDH/PDL")
    parser.add_argument("--stop-buffer", type=float, default=1.0, help="extra stop buffer beyond sweep/reclaim structure")
    parser.add_argument("--max-entry-extension-r", type=float, default=0.5, help="max allowed extension from reclaim level in R units")
    parser.add_argument("--csv", type=str, default="reports/xau_ndog_asia_trades.csv", help="CSV export path")
    parser.add_argument("--breakeven-r", type=float, default=1.0, help="move stop to breakeven after this many R")
    parser.add_argument("--trail-r", type=float, default=1.5, help="start trailing after this many R")
    parser.add_argument("--allow-hours", type=str, default="", help="comma-separated NY entry hours, e.g. 19,20")
    parser.add_argument("--progress-check-bars", type=int, default=4, help="bars before enforcing min progress stop")
    parser.add_argument("--min-progress-r", type=float, default=0.3, help="minimum MFE in R units required by progress-check-bars")
    parser.add_argument("--max-risk-distance", type=float, default=80.0, help="max allowed initial stop distance in price units")
    parser.add_argument("--max-risk-to-range", type=float, default=0.8, help="max allowed initial risk distance divided by recent range")
    parser.add_argument("--require-demand-zone", action="store_true", help="require higher-timeframe demand-zone confluence")
    parser.add_argument("--demand-zone-tolerance", type=float, default=10.0, help="distance tolerance around HTF demand zone")
    args = parser.parse_args()

    cfg = XAUFXConfig()
    feed = TwelveDataFeed(cfg.twelvedata_api_key)

    run_backtest._no_mss = args.no_mss
    run_backtest._no_fvg = args.no_fvg
    run_backtest._mss_disp = args.mss_disp
    run_backtest._mss_lookback = args.mss_lookback
    run_backtest._pd_confluence = args.pd_confluence
    run_backtest._pd_tolerance = args.pd_tolerance
    run_backtest._stop_buffer = args.stop_buffer
    run_backtest._max_entry_extension_r = args.max_entry_extension_r
    run_backtest._breakeven_r = args.breakeven_r
    run_backtest._trail_r = args.trail_r
    run_backtest._allow_hours = {
        int(x.strip()) for x in args.allow_hours.split(",") if x.strip()
    } if args.allow_hours.strip() else None
    run_backtest._progress_check_bars = args.progress_check_bars
    run_backtest._min_progress_r = args.min_progress_r
    run_backtest._max_risk_distance = args.max_risk_distance
    run_backtest._max_risk_to_range = args.max_risk_to_range
    run_backtest._require_demand_zone = args.require_demand_zone
    run_backtest._demand_zone_tolerance = args.demand_zone_tolerance

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
        print("No bars returned from data provider; aborting backtest.")
        return

    trades, _, summary = run_backtest(
        candles=candles,
        daily_candles=daily_candles,
        starting_equity=args.capital,
        risk_per_trade_pct=args.risk,
        spread_points=args.spread,
        target_r_multiple=args.target_r,
        timezone=cfg.timezone,
        session_cap=args.session_cap,
    )

    print("\n── XAU NDOG Asia ─────────────────────────────")
    print(f"  Trades:   {summary['trades']}")
    print(f"  Longs:    {summary['longs']}")
    print(f"  Shorts:   {summary['shorts']}")
    print(f"  Win rate: {summary['win_rate']:.1f}%")
    print(f"  Return:   {summary['return_pct']:.3f}%")
    print(f"  Sharpe:   {summary['sharpe']:.3f}")
    print(f"  Max DD:   {summary['max_dd_pct']:.3f}%")
    print(f"  Equity:   ${summary['equity']:,.2f}")
    print(f"  Avg hold: {summary['avg_bars_held']:.2f} bars")
    print(f"  Daily bias: {summary['daily_bias']}")

    print("\nDiagnostics:")
    for k, v in summary["diagnostics"].items():
        print(f"  {k}: {v}")

    print("\nReason breakdown:")
    for reason, stats in summary["reason_breakdown"].items():
        print(
            f"  {reason}: count={stats['count']} "
            f"pnl={stats['pnl']:+.2f} avg={stats['avg_pnl']:+.2f}"
        )

    print("\nEntry-hour breakdown (NY time):")
    for hour in sorted(summary["hour_breakdown"]):
        stats = summary["hour_breakdown"][hour]
        print(
            f"  {hour:02d}:00 "
            f"count={stats['count']} "
            f"wr={stats['win_rate']:.1f}% "
            f"pnl={stats['pnl']:+.2f} "
            f"avg={stats['avg_pnl']:+.2f}"
        )

    if summary["stop_subtype_breakdown"]:
        print("\nStop subtype breakdown:")
        for subtype, stats in summary["stop_subtype_breakdown"].items():
            print(
                f"  {subtype}: count={stats['count']} "
                f"pnl={stats['pnl']:+.2f} avg={stats['avg_pnl']:+.2f}"
            )

    if trades:
        export_trades_csv(trades, args.csv)
        print(f"\nCSV exported: {args.csv}")
        print("\nLast 10 trades:")
        for t in trades[-10:]:
            print(
                f"  {t.side:>4} entry={t.entry_price:.2f} exit={t.exit_price:.2f} "
                f"stop={t.stop_price:.2f} target={t.target_price:.2f} "
                f"pnl={t.pnl:+.2f} held={t.bars_held} reason={t.reason}"
            )


if __name__ == "__main__":
    main()
