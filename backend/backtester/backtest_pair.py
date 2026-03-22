"""
AlphaBot Pair Backtester — Kalman filter on cointegrated spread (Chan Ch.3)
Usage: python -m backend.backtester.backtest_pair --sym1 BTCUSDT --sym2 ETHUSDT --days 30
"""
import argparse, asyncio, sys, statistics
sys.path.insert(0, '/root/alphabot')
from backend.core.signals.quant_signals import (
    KalmanFilter, compute_half_life, compute_hurst,
    data_scrambling_test, position_scale_from_risk, compute_ravi_series
)

async def fetch_closes(symbol, interval, days):
    import httpx
    limit = min(days * 24, 1000)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit}
        )
        r.raise_for_status()
        return [float(k[4]) for k in r.json()]

def run_pair_strategy(closes1, closes2, entry_z=2.0, capital=10000.0, use_ravi=True):
    """
    Kalman filter MR on spread: sym1 - hedge_ratio * sym2
    x = sym2 (independent), y = sym1 (dependent)
    """
    n = min(len(closes1), len(closes2))
    closes1, closes2 = closes1[:n], closes2[:n]
    kf = KalmanFilter(delta=0.0001, Ve=0.001)

    pos, entry_spread, trades = 0, 0.0, []
    equity, peak, max_dd = [capital], capital, 0.0
    warmup = 10
    hedge_ratio = 1.0
    zscores = []

    ravi_s = compute_ravi_series(closes1) if use_ravi and len(closes1) >= 65 else []

    for i in range(1, n):
        x = closes2[i]   # ETH as independent
        y = closes1[i]   # BTC as dependent
        state = kf.update(x, y)
        z = state["zscore"]
        hr = state.get("hedge_ratio", 1.0)
        uc = state.get("update_count", i)
        zscores.append(z)

        if i < warmup or (abs(z) > 4.0 and uc > 50):
            equity.append(capital)
            continue

        if ravi_s and i < len(ravi_s) and ravi_s[i] is not None and ravi_s[i] >= 3.5:
            if pos != 0:
                spread_now = closes1[i] - hr * closes2[i]
                pnl = (spread_now - entry_spread) * pos * (capital * 0.10 / abs(entry_spread) if entry_spread != 0 else 1)
                capital += pnl
                trades.append(pnl)
                pos = 0
            equity.append(capital)
            continue

        spread = y - hr * x
        size = (capital * 0.10) / max(closes1[i], 1)

        if pos == 0:
            if z < -entry_z:
                pos, entry_spread, hedge_ratio = 1, spread, hr
            elif z > entry_z:
                pos, entry_spread, hedge_ratio = -1, spread, hr
        elif pos == 1 and z >= 0:
            spread_now = closes1[i] - hedge_ratio * closes2[i]
            pnl = (spread_now - entry_spread) * size
            capital += pnl
            trades.append(pnl)
            pos = 0
        elif pos == -1 and z <= 0:
            spread_now = closes1[i] - hedge_ratio * closes2[i]
            pnl = (entry_spread - spread_now) * size
            capital += pnl
            trades.append(pnl)
            pos = 0

        mtm = capital
        if pos != 0:
            spread_now = closes1[i] - hedge_ratio * closes2[i]
            mtm = capital + (spread_now - entry_spread) * size * pos
        equity.append(mtm)
        if mtm > peak: peak = mtm
        dd = (peak - mtm) / peak if peak > 0 else 0
        if dd > max_dd: max_dd = dd

    rets = [(equity[i]-equity[i-1])/equity[i-1] for i in range(1,len(equity)) if equity[i-1]>0]
    if len(rets) > 1 and statistics.stdev(rets) > 0:
        sharpe = (statistics.mean(rets) / statistics.stdev(rets)) * (8760**0.5)
    else:
        sharpe = 0.0

    wins = [t for t in trades if t > 0]
    spread_series = [closes1[i] - closes2[i] for i in range(n)]
    hl = compute_half_life(spread_series)
    hurst = compute_hurst(spread_series)

    return {
        "trades": len(trades),
        "win_rate": len(wins)/len(trades) if trades else 0,
        "return_pct": (capital-10000)/10000*100,
        "max_dd_pct": max_dd*100,
        "sharpe": round(sharpe, 4),
        "final": round(capital, 2),
        "spread_hl": hl,
        "spread_hurst": hurst,
        "spread_series": spread_series,
    }

async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sym1", default="BTCUSDT")
    p.add_argument("--sym2", default="ETHUSDT")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--interval", default="1h")
    p.add_argument("--entry-z", type=float, default=2.0)
    p.add_argument("--scramble", action="store_true")
    p.add_argument("--trials", type=int, default=300)
    p.add_argument("--no-ravi", action="store_true")
    args = p.parse_args()

    print(f"\n{'='*52}")
    print(f"  AlphaBot Pair Backtester | {args.sym1}/{args.sym2} | {args.days}d")
    print(f"{'='*52}\n")

    print("Fetching data from Binance...")
    c1, c2 = await asyncio.gather(
        fetch_closes(args.sym1, args.interval, args.days),
        fetch_closes(args.sym2, args.interval, args.days),
    )
    print(f"  {args.sym1}: {len(c1)} bars | ${min(c1):,.0f} - ${max(c1):,.0f}")
    print(f"  {args.sym2}: {len(c2)} bars | ${min(c2):,.0f} - ${max(c2):,.0f}\n")

    r = run_pair_strategy(c1, c2, entry_z=args.entry_z, use_ravi=not args.no_ravi)

    print("--- Spread Diagnostics ---")
    print(f"  Half-life  : {r['spread_hl']:.1f} bars" if r['spread_hl'] else "  Half-life : N/A")
    print(f"  Hurst      : {r['spread_hurst']:.4f} ({'mean-reverting' if r['spread_hurst'] < 0.5 else 'trending'})\n")

    print("--- Strategy Results ---")
    print(f"  Trades     : {r['trades']}")
    print(f"  Win rate   : {r['win_rate']*100:.1f}%")
    print(f"  Return     : {r['return_pct']:+.2f}%")
    print(f"  Max DD     : {r['max_dd_pct']:.2f}%")
    print(f"  Sharpe     : {r['sharpe']:.4f}")
    print(f"  Equity     : ${r['final']:,.2f}\n")

    if args.scramble:
        print(f"--- Scrambling Test | {args.trials} trials ---")
        from backend.core.signals.quant_signals import compute_half_life
        eq=r['equity_curve'] if 'equity_curve' in r else []
        rets=[eq[i]/eq[i-1]-1 for i in range(1,len(eq)) if eq[i-1]>0]
        sr = data_scrambling_test(rets, n_trials=args.trials) if rets else {}
        print(f"  Original Sharpe : {sr.get('original_sharpe', 'N/A')}")
        print(f"  p-value         : {sr.get('p_value', 'N/A')}")
        print(f"  Robust          : {'YES ✓' if sr.get('is_robust') else 'NO'}\n")

    print("--- vs Targets ---")
    print(f"  Sharpe>0.8  : {'PASS' if r['sharpe']>0.8 else 'FAIL'} ({r['sharpe']:.4f})")
    print(f"  MaxDD<15%   : {'PASS' if r['max_dd_pct']<15 else 'FAIL'} ({r['max_dd_pct']:.2f}%)")
    print(f"  WinRate>45% : {'PASS' if r['win_rate']>0.45 else 'FAIL'} ({r['win_rate']*100:.1f}%)\n")

asyncio.run(main())
