from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo

from backend.core.xaufx.config import XAUFXConfig
from backend.core.xaufx.data_feeds.twelvedata_feed import (
    TwelveDataFeed,
    TwelveDataQuotaExceeded,
)
from backend.core.xaufx.detectors.demand_zone import detect_recent_demand_zone
from backend.core.xaufx.models import Candle
from backend.core.xaufx.sessions.clock import NYSessionClock
from backend.core.xaufx.strategies.xau_bear_continuation import XAUBearContinuationStrategy
from backend.core.xaufx.strategies.xau_daily_momentum import daily_bias


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


def calc_pnl(side: str, qty: float, entry: float, exit_price: float) -> float:
    if side == "BUY":
        return (exit_price - entry) * qty
    return (entry - exit_price) * qty


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


def export_trades_csv(trades: List[Trade], path: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=list(asdict(trades[0]).keys()) if trades else [
                "entry_idx", "exit_idx", "side", "entry_time", "exit_time",
                "entry_price", "exit_price", "stop_price", "target_price",
                "qty", "pnl", "return_pct", "bars_held", "reason", "daily_bias",
            ],
        )
        writer.writeheader()
        for t in trades:
            writer.writerow(asdict(t))


def ny_hour(ts, tz_name: str) -> int:
    return ts.astimezone(ZoneInfo(tz_name)).hour


def run_backtest(
    candles: List[Candle],
    daily_candles: List[Candle],
    starting_equity: float,
    risk_per_trade_pct: float,
    spread_points: float,
    target_r_multiple: float,
    timezone: str,
    stop_buffer: float,
    breakeven_r: float,
    trail_r: float,
    allow_hours: set[int],
    max_risk_distance: float,
    max_risk_to_range: float,
    max_entry_extension_r: float,
) -> Tuple[List[Trade], List[float], dict]:
    clock = NYSessionClock(timezone)

    equity = starting_equity
    equity_curve: List[float] = [equity]
    trades: List[Trade] = []
    trade_returns: List[float] = []

    diagnostics = {
        "windows_checked": 0,
        "entry_valid": 0,
        "risk_distance_reject": 0,
        "risk_to_range_reject": 0,
        "hour_reject": 0,
        "router_reject": 0,
    }

    in_position = False
    side = ""
    entry_idx = -1
    entry_price = 0.0
    stop_price = 0.0
    target_price = 0.0
    qty = 0.0
    entry_initial_r = 0.0
    trade_mfe = 0.0
    trade_mae = 0.0
    current_bias = "rolling"

    start_idx = 60

    for i in range(start_idx, len(candles)):
        recent = candles[: i + 1]
        last = candles[i]
        diagnostics["windows_checked"] += 1

        if in_position:
            unreal = calc_pnl(side, qty, entry_price, last.close)
            equity_curve.append(equity + unreal)
        else:
            equity_curve.append(equity)

        if in_position:
            exit_reason = ""
            exit_price = None

            if entry_initial_r > 0:
                if side == "SELL":
                    favorable = max(0.0, entry_price - last.low)
                    adverse = max(0.0, last.high - entry_price)
                else:
                    favorable = max(0.0, last.high - entry_price)
                    adverse = max(0.0, entry_price - last.low)

                trade_mfe = max(trade_mfe, favorable)
                trade_mae = max(trade_mae, adverse)

                if side == "SELL":
                    achieved_r = favorable / entry_initial_r
                    if achieved_r >= breakeven_r:
                        stop_price = min(stop_price, entry_price)
                    if achieved_r >= trail_r and i >= 2:
                        trail_stop = max(c.high for c in candles[max(0, i - 2): i])
                        stop_price = min(stop_price, trail_stop)
                else:
                    achieved_r = favorable / entry_initial_r
                    if achieved_r >= breakeven_r:
                        stop_price = max(stop_price, entry_price)
                    if achieved_r >= trail_r and i >= 2:
                        trail_stop = min(c.low for c in candles[max(0, i - 2): i])
                        stop_price = max(stop_price, trail_stop)

            if side == "SELL":
                if last.high >= stop_price:
                    exit_reason = "stop"
                    exit_price = stop_price + spread_points / 2.0
                elif last.low <= target_price:
                    exit_reason = "target"
                    exit_price = target_price + spread_points / 2.0
            else:
                if last.low <= stop_price:
                    exit_reason = "stop"
                    exit_price = stop_price - spread_points / 2.0
                elif last.high >= target_price:
                    exit_reason = "target"
                    exit_price = target_price - spread_points / 2.0

            if exit_price is None:
                label = clock.label(last.ts)
                if label not in ("ASIA", "LONDON", "NEW_YORK"):
                    exit_reason = "session_end"
                    if side == "SELL":
                        exit_price = last.close + spread_points / 2.0
                    else:
                        exit_price = last.close - spread_points / 2.0

            if exit_price is not None:
                pnl = calc_pnl(side, qty, entry_price, exit_price)
                equity += pnl
                ret = pnl / starting_equity * 100.0
                trade_returns.append(ret)

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
                        return_pct=ret,
                        bars_held=i - entry_idx,
                        reason=exit_reason,
                        daily_bias=current_bias,
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
                entry_initial_r = 0.0
                trade_mfe = 0.0
                trade_mae = 0.0

            continue

        if ny_hour(last.ts, timezone) not in allow_hours:
            diagnostics["hour_reject"] += 1
            continue

        eligible_daily = [c for c in daily_candles if c.ts <= last.ts]
        if len(eligible_daily) < 20:
            diagnostics["router_reject"] += 1
            continue

        current_bias = daily_bias("XAUUSD", eligible_daily[-120:])
        rolling_demand_zone = detect_recent_demand_zone(eligible_daily[-120:])

        strategy = XAUBearContinuationStrategy(
            timezone=timezone,
            daily_bias=current_bias,
            require_mss=True,
            require_fvg=True,
            require_pd_confluence=True,
            stop_buffer=stop_buffer,
            max_entry_extension_r=max_entry_extension_r,
            demand_zone=rolling_demand_zone,
            require_demand_zone=False,
            allowed_hours=allow_hours,
        )

        setup = strategy.evaluate_setup("XAUUSD", recent)
        if not setup.get("ok"):
            diagnostics["router_reject"] += 1
            continue

        entry = float(setup["entry"])
        stop = float(setup["stop"])
        target = float(setup["target"])
        side = str(setup["direction"])

        risk_dist = abs(stop - entry)
        if risk_dist <= 0:
            diagnostics["risk_distance_reject"] += 1
            continue
        if risk_dist > max_risk_distance:
            diagnostics["risk_distance_reject"] += 1
            continue

        recent_range = max(c.high for c in recent[-12:]) - min(c.low for c in recent[-12:])
        if recent_range > 0 and (risk_dist / recent_range) > max_risk_to_range:
            diagnostics["risk_to_range_reject"] += 1
            continue

        risk_amount = equity * risk_per_trade_pct
        qty = risk_amount / risk_dist
        if qty <= 0:
            diagnostics["risk_distance_reject"] += 1
            continue

        diagnostics["entry_valid"] += 1

        in_position = True
        entry_idx = i
        entry_price = entry
        stop_price = stop
        target_price = target
        entry_initial_r = risk_dist
        trade_mfe = 0.0
        trade_mae = 0.0

    summary = {
        "trades": len(trades),
        "shorts": sum(1 for t in trades if t.side == "SELL"),
        "longs": sum(1 for t in trades if t.side == "BUY"),
        "win_rate": (sum(1 for t in trades if t.pnl > 0) / len(trades) * 100.0) if trades else 0.0,
        "return_pct": (equity - starting_equity) / starting_equity * 100.0,
        "sharpe": sharpe_ratio(trade_returns),
        "max_dd_pct": max_drawdown(equity_curve) * 100.0,
        "equity": equity,
        "avg_bars_held": (sum(t.bars_held for t in trades) / len(trades)) if trades else 0.0,
        "daily_bias": "rolling",
        "diagnostics": diagnostics,
    }
    return trades, equity_curve, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest XAU bear continuation strategy")
    parser.add_argument("--bars", type=int, default=10000)
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--risk", type=float, default=0.005)
    parser.add_argument("--spread", type=float, default=0.75)
    parser.add_argument("--target-r", type=float, default=2.0)
    parser.add_argument("--stop-buffer", type=float, default=2.5)
    parser.add_argument("--breakeven-r", type=float, default=1.0)
    parser.add_argument("--trail-r", type=float, default=1.5)
    parser.add_argument("--allow-hours", type=str, default="19,20")
    parser.add_argument("--max-risk-distance", type=float, default=50.0)
    parser.add_argument("--max-risk-to-range", type=float, default=0.8)
    parser.add_argument("--max-entry-extension-r", type=float, default=0.4)
    parser.add_argument("--csv", type=str, default="reports/xau_bear_continuation.csv")
    args = parser.parse_args()

    cfg = XAUFXConfig()
    feed = TwelveDataFeed(cfg.twelvedata_api_key)

    print(f"Fetching XAUUSD {cfg.intraday_interval} bars ({args.bars})...")
    try:
        candles = feed.fetch_bars("XAUUSD", cfg.intraday_interval, outputsize=args.bars)
        daily_candles = feed.fetch_bars("XAUUSD", cfg.daily_interval, outputsize=400)
    except TwelveDataQuotaExceeded as exc:
        print(f"Quota exhausted: {exc}")
        return

    print(f"Loaded {len(candles)} intraday bars")
    print(f"Loaded {len(daily_candles)} daily bars")

    allow_hours = {int(x.strip()) for x in args.allow_hours.split(",") if x.strip()}

    trades, _, summary = run_backtest(
        candles=candles,
        daily_candles=daily_candles,
        starting_equity=args.capital,
        risk_per_trade_pct=args.risk,
        spread_points=args.spread,
        target_r_multiple=args.target_r,
        timezone=cfg.timezone,
        stop_buffer=args.stop_buffer,
        breakeven_r=args.breakeven_r,
        trail_r=args.trail_r,
        allow_hours=allow_hours,
        max_risk_distance=args.max_risk_distance,
        max_risk_to_range=args.max_risk_to_range,
        max_entry_extension_r=args.max_entry_extension_r,
    )

    print("\n── XAU Bear Continuation ─────────────────────────────")
    print(f"  Trades:   {summary['trades']}")
    print(f"  Shorts:   {summary['shorts']}")
    print(f"  Longs:    {summary['longs']}")
    print(f"  Win rate: {summary['win_rate']:.1f}%")
    print(f"  Return:   {summary['return_pct']:.3f}%")
    print(f"  Sharpe:   {summary['sharpe']:.3f}")
    print(f"  Max DD:   {summary['max_dd_pct']:.3f}%")
    print(f"  Equity:   ${summary['equity']:.2f}")
    print(f"  Avg hold: {summary['avg_bars_held']:.2f} bars")
    print(f"  Daily bias: {summary['daily_bias']}")

    print("\nDiagnostics:")
    for k, v in summary["diagnostics"].items():
        print(f"  {k}: {v}")

    reason_breakdown: Dict[str, Dict[str, float]] = {}
    for t in trades:
        bucket = reason_breakdown.setdefault(t.reason, {"count": 0, "pnl": 0.0})
        bucket["count"] += 1
        bucket["pnl"] += t.pnl

    if reason_breakdown:
        print("\nReason breakdown:")
        for reason, bucket in reason_breakdown.items():
            avg = bucket["pnl"] / bucket["count"]
            print(f"  {reason}: count={bucket['count']} pnl={bucket['pnl']:+.2f} avg={avg:+.2f}")

    export_trades_csv(trades, args.csv)
    print(f"\nCSV exported: {args.csv}")

    if trades:
        print("\nLast 10 trades:")
        for t in trades[-10:]:
            print(
                f"   {t.side} entry={t.entry_price:.2f} exit={t.exit_price:.2f} "
                f"stop={t.stop_price:.2f} target={t.target_price:.2f} "
                f"pnl={t.pnl:+.2f} held={t.bars_held} reason={t.reason}"
            )


if __name__ == "__main__":
    main()
