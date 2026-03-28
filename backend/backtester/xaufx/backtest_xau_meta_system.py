from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend.core.xaufx.config import XAUFXConfig
from backend.core.xaufx.data_feeds.twelvedata_feed import TwelveDataFeed, TwelveDataQuotaExceeded
from backend.core.xaufx.models import Candle
from backend.core.xaufx.strategies.xau_meta_router import XAUMetaRouter


@dataclass
class Trade:
    entry_idx: int
    exit_idx: int
    regime: str
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
    mfe: float
    mae: float


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

    if not trades:
        out.write_text("")
        return

    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(trades[0]).keys()))
        writer.writeheader()
        for t in trades:
            writer.writerow(asdict(t))


def fetch_dxy_daily(feed: TwelveDataFeed, cfg: XAUFXConfig, outputsize: int = 400) -> List[Candle]:
    try:
        return feed.fetch_bars("DXY", cfg.daily_interval, outputsize=outputsize)
    except Exception:
        return []


def run_backtest(
    candles: List[Candle],
    daily_xau: List[Candle],
    daily_dxy: List[Candle],
    starting_equity: float,
    risk_per_trade_pct: float,
    spread_points: float,
    target_r_multiple: float,
    timezone: str,
    stop_buffer: float,
    breakeven_r: float,
    trail_r: float,
    max_risk_distance: float,
    max_risk_to_range: float,
) -> Tuple[List[Trade], List[float], dict]:
    router = XAUMetaRouter(
    timezone=timezone,
    require_mss=True,
    require_fvg_long=False,
    require_fvg_short=True,
    require_pd_confluence_short=True,
    bull_min_conf=0.60,
    bear_min_conf=0.75,
    stop_buffer=stop_buffer,
)
    # Attach dynamic flags on the existing NDOG runner internals.
    from backend.backtester.xaufx.backtest_xau_ndog_asia import run_backtest as ndog_run_backtest
    ndog_run_backtest._breakeven_r = breakeven_r
    ndog_run_backtest._trail_r = trail_r
    ndog_run_backtest._max_risk_distance = max_risk_distance
    ndog_run_backtest._max_risk_to_range = max_risk_to_range
    ndog_run_backtest._allow_hours = {19, 20}
    ndog_run_backtest._no_fvg = True
    ndog_run_backtest._stop_buffer = stop_buffer

    trades: List[Trade] = []
    equity = starting_equity
    equity_curve: List[float] = [equity]
    trade_returns: List[float] = []

    regime_counts: Dict[str, int] = {}
    regime_pnl: Dict[str, float] = {}
    no_trade_count = 0

    in_position = False
    side = ""
    regime = ""
    entry_idx = -1
    entry_price = 0.0
    stop_price = 0.0
    target_price = 0.0
    qty = 0.0
    trade_mfe = 0.0
    trade_mae = 0.0
    entry_risk = 0.0

    for i in range(60, len(candles)):
        recent = candles[: i + 1]
        last = candles[i]

        eligible_daily_xau = [c for c in daily_xau if c.ts <= last.ts]
        eligible_daily_dxy = [c for c in daily_dxy if c.ts <= last.ts]

        if len(eligible_daily_xau) < 60:
            equity_curve.append(equity)
            continue

        if in_position:
            unreal = calc_pnl(side, qty, entry_price, last.close)
            equity_curve.append(equity + unreal)
        else:
            equity_curve.append(equity)

        if in_position:
            if side == "BUY":
                trade_mfe = max(trade_mfe, max(0.0, last.high - entry_price))
                trade_mae = max(trade_mae, max(0.0, entry_price - last.low))
            else:
                trade_mfe = max(trade_mfe, max(0.0, entry_price - last.low))
                trade_mae = max(trade_mae, max(0.0, last.high - entry_price))

            exit_reason = ""
            exit_price = None

            if entry_risk > 0:
                if side == "BUY":
                    achieved_r = max(0.0, last.high - entry_price) / entry_risk
                    if achieved_r >= breakeven_r:
                        stop_price = max(stop_price, entry_price)
                    if achieved_r >= trail_r and i >= 2:
                        trail_stop = min(c.low for c in candles[max(0, i - 2): i])
                        stop_price = max(stop_price, trail_stop)
                else:
                    achieved_r = max(0.0, entry_price - last.low) / entry_risk
                    if achieved_r >= breakeven_r:
                        stop_price = min(stop_price, entry_price)
                    if achieved_r >= trail_r and i >= 2:
                        trail_stop = max(c.high for c in candles[max(0, i - 2): i])
                        stop_price = min(stop_price, trail_stop)

            if side == "BUY":
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

            if exit_price is None:
                current_regime = router.classifier.classify(eligible_daily_xau[-80:], eligible_daily_dxy[-80:] if eligible_daily_dxy else None).regime
                if current_regime != regime:
                    exit_reason = "regime_change"
                    exit_price = last.close - spread_points / 2.0 if side == "BUY" else last.close + spread_points / 2.0

            if exit_price is not None:
                pnl = calc_pnl(side, qty, entry_price, exit_price)
                equity += pnl
                ret = pnl / max(starting_equity, 1e-9) * 100.0
                trade_returns.append(ret)

                trades.append(
                    Trade(
                        entry_idx=entry_idx,
                        exit_idx=i,
                        regime=regime,
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
                        mfe=trade_mfe,
                        mae=trade_mae,
                    )
                )

                regime_pnl[regime] = regime_pnl.get(regime, 0.0) + pnl

                in_position = False
                side = ""
                regime = ""
                entry_idx = -1
                entry_price = 0.0
                stop_price = 0.0
                target_price = 0.0
                qty = 0.0
                entry_risk = 0.0
                trade_mfe = 0.0
                trade_mae = 0.0

            continue

        setup = router.evaluate(
            "XAUUSD",
            intraday_candles=recent,
            daily_xau=eligible_daily_xau[-120:],
            daily_dxy=eligible_daily_dxy[-120:] if eligible_daily_dxy else None,
        )

        current_regime = setup.get("regime", "unknown")
        regime_counts[current_regime] = regime_counts.get(current_regime, 0) + 1

        if not setup.get("ok"):
            no_trade_count += 1
            continue

        entry = float(setup["entry"])
        stop = float(setup["stop"])
        target = float(setup["target"])
        direction = str(setup["direction"])

        risk_dist = abs(entry - stop)
        if risk_dist <= 0:
            continue
        if risk_dist > max_risk_distance:
            continue

        recent_range = max(c.high for c in recent[-12:]) - min(c.low for c in recent[-12:])
        if recent_range > 0 and (risk_dist / recent_range) > max_risk_to_range:
            continue

        risk_amount = equity * risk_per_trade_pct
        qty = risk_amount / risk_dist
        if qty <= 0:
            continue

        in_position = True
        regime = current_regime
        side = direction
        entry_idx = i
        entry_price = entry
        stop_price = stop
        target_price = target
        entry_risk = risk_dist
        trade_mfe = 0.0
        trade_mae = 0.0

    summary = {
        "trades": len(trades),
        "longs": sum(1 for t in trades if t.side == "BUY"),
        "shorts": sum(1 for t in trades if t.side == "SELL"),
        "win_rate": (sum(1 for t in trades if t.pnl > 0) / len(trades) * 100.0) if trades else 0.0,
        "return_pct": (equity - starting_equity) / starting_equity * 100.0,
        "sharpe": sharpe_ratio(trade_returns),
        "max_dd_pct": max_drawdown(equity_curve) * 100.0,
        "equity": equity,
        "avg_bars_held": (sum(t.bars_held for t in trades) / len(trades)) if trades else 0.0,
        "regime_counts": regime_counts,
        "regime_pnl": regime_pnl,
        "no_trade_count": no_trade_count,
    }
    return trades, equity_curve, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest XAU regime-aware meta system")
    parser.add_argument("--bars", type=int, default=10000)
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--risk", type=float, default=0.005)
    parser.add_argument("--spread", type=float, default=0.75)
    parser.add_argument("--target-r", type=float, default=2.0)
    parser.add_argument("--stop-buffer", type=float, default=2.5)
    parser.add_argument("--breakeven-r", type=float, default=1.25)
    parser.add_argument("--trail-r", type=float, default=1.5)
    parser.add_argument("--max-risk-distance", type=float, default=50.0)
    parser.add_argument("--max-risk-to-range", type=float, default=0.8)
    parser.add_argument("--csv", type=str, default="reports/xau_meta_system.csv")
    args = parser.parse_args()

    cfg = XAUFXConfig()
    feed = TwelveDataFeed(cfg.twelvedata_api_key)

    print(f"Fetching XAUUSD {cfg.intraday_interval} bars ({args.bars})...")
    try:
        candles = feed.fetch_bars("XAUUSD", cfg.intraday_interval, outputsize=args.bars)
        daily_xau = feed.fetch_bars("XAUUSD", cfg.daily_interval, outputsize=400)
        daily_dxy = fetch_dxy_daily(feed, cfg, 400)
    except TwelveDataQuotaExceeded as exc:
        print(f"Quota exhausted: {exc}")
        return

    print(f"Loaded {len(candles)} intraday bars")
    print(f"Loaded {len(daily_xau)} XAU daily bars")
    print(f"Loaded {len(daily_dxy)} DXY daily bars")

    trades, _, summary = run_backtest(
        candles=candles,
        daily_xau=daily_xau,
        daily_dxy=daily_dxy,
        starting_equity=args.capital,
        risk_per_trade_pct=args.risk,
        spread_points=args.spread,
        target_r_multiple=args.target_r,
        timezone=cfg.timezone,
        stop_buffer=args.stop_buffer,
        breakeven_r=args.breakeven_r,
        trail_r=args.trail_r,
        max_risk_distance=args.max_risk_distance,
        max_risk_to_range=args.max_risk_to_range,
    )

    print("\n── XAU Meta System ─────────────────────────────")
    print(f"  Trades:   {summary['trades']}")
    print(f"  Longs:    {summary['longs']}")
    print(f"  Shorts:   {summary['shorts']}")
    print(f"  Win rate: {summary['win_rate']:.1f}%")
    print(f"  Return:   {summary['return_pct']:.3f}%")
    print(f"  Sharpe:   {summary['sharpe']:.3f}")
    print(f"  Max DD:   {summary['max_dd_pct']:.3f}%")
    print(f"  Equity:   ${summary['equity']:.2f}")
    print(f"  Avg hold: {summary['avg_bars_held']:.2f} bars")

    print("\nRegime counts:")
    for k, v in sorted(summary["regime_counts"].items()):
        print(f"  {k}: {v}")

    print("\nRegime pnl:")
    for k, v in sorted(summary["regime_pnl"].items()):
        print(f"  {k}: {v:+.2f}")

    print(f"\nNo-trade router blocks: {summary['no_trade_count']}")

    export_trades_csv(trades, args.csv)
    print(f"\nCSV exported: {args.csv}")


if __name__ == "__main__":
    main()
