import argparse, asyncio, sys, statistics
sys.path.insert(0, '/root/alphabot')
from backend.core.signals.quant_signals import (
    KalmanFilter, compute_half_life, compute_hurst,
    adf_test, compute_ravi_series, data_scrambling_test,
    position_scale_from_risk
)

async def fetch_ohlcv(symbol, interval, days):
    import httpx
    limit = min(days * 24, 1000)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit}
        )
        r.raise_for_status()
        return [{"close": float(k[4])} for k in r.json()]

def run_strategy(bars, lookback=5, entry_z=2.0, use_ravi=True, capital=10000.0, max_hl=9999):
    closes = [b["close"] for b in bars]
    kf = KalmanFilter(delta=0.0001, Ve=0.001)
    pos, entry, trades, equity, peak, max_dd = 0, 0.0, [], [capital], capital, 0.0
    warmup = lookback * 2
    ravi_s = compute_ravi_series(closes) if use_ravi and len(closes) >= 65 else []

    for i in range(1, len(bars)):
        price = closes[i]
        state = kf.update(closes[i-1], price)
        z = state["zscore"]
        uc = state.get("update_count", i)

        if i < warmup or (abs(z) > 4.0 and uc > 50):
            equity.append(capital)
            continue

        if ravi_s and i < len(ravi_s) and ravi_s[i] is not None and ravi_s[i] >= 3.5:
            if pos != 0:
                capital += (price - entry) * pos
                trades.append((price - entry) * pos)
                pos = 0
            equity.append(capital)
            continue

        hl = compute_half_life(closes[max(0, i-30):i]) or 10
        scale = position_scale_from_risk(z, hl)
        size = capital * 0.10 * scale / price

        hl = compute_half_life(closes[max(0,i-30):i]) or 10
        if pos == 0 and hl > max_hl:
            equity.append(capital)
            continue
        if pos == 0:
            if z < -entry_z:
                pos, entry = 1, price
            elif z > entry_z:
                pos, entry = -1, price
        elif pos == 1 and z >= 0:
            pnl = (price - entry) * size
            capital += pnl
            trades.append(pnl)
            pos = 0
        elif pos == -1 and z <= 0:
            pnl = (entry - price) * size
            capital += pnl
            trades.append(pnl)
            pos = 0

        mtm = capital + (price - entry) * size * pos if pos != 0 else capital
        equity.append(mtm)
        if mtm > peak:
            peak = mtm
        dd = (peak - mtm) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    rets = [
        (equity[i] - equity[i-1]) / equity[i-1]
        for i in range(1, len(equity)) if equity[i-1] > 0
    ]
    if len(rets) > 1 and statistics.stdev(rets) > 0:
        sharpe = (statistics.mean(rets) / statistics.stdev(rets)) * (8760 ** 0.5)
    else:
        sharpe = 0.0

    wins = [t for t in trades if t > 0]
    return {
        "trades": len(trades),
        "win_rate": len(wins) / len(trades) if trades else 0,
        "return_pct": (capital - 10000) / 10000 * 100,
        "max_dd_pct": max_dd * 100,
        "sharpe": round(sharpe, 4),
        "final": round(capital, 2),
        "closes": closes,
    }

async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--symbol2", default="")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--interval", default="1h")
    p.add_argument("--scramble", action="store_true")
    p.add_argument("--trials", type=int, default=300)
    p.add_argument("--no-ravi", action="store_true")
    p.add_argument("--entry-z", type=float, default=2.0)
    p.add_argument("--max-hl", type=int, default=9999)
    args = p.parse_args()

    print("\n" + "="*50)
    print(f"  AlphaBot Backtester | {args.symbol} | {args.days}d")
    print("="*50 + "\n")

    print("Fetching data from Binance...")
    bars = await fetch_ohlcv(args.symbol, args.interval, args.days)
    closes = [b["close"] for b in bars]
    print(f"Got {len(bars)} bars | ${min(closes):,.0f} - ${max(closes):,.0f}\n")

    hl = compute_half_life(closes)
    hurst = compute_hurst(closes)
    adf_p, adf_stat = adf_test(closes)
    print("--- Diagnostics ---")
    print(f"  Half-life : {hl:.1f} bars" if hl else "  Half-life : N/A")
    print(f"  Hurst     : {hurst:.4f} ({'mean-reverting' if hurst < 0.5 else 'trending'})")
    print(f"  ADF       : p={adf_p:.4f} ({'STATIONARY' if adf_stat else 'non-stationary'})\n")

    r = run_strategy(bars, use_ravi=not args.no_ravi, entry_z=args.entry_z, max_hl=args.max_hl)
    print("--- Strategy Results ---")
    print(f"  Trades     : {r['trades']}")
    print(f"  Win rate   : {r['win_rate']*100:.1f}%")
    print(f"  Return     : {r['return_pct']:+.2f}%")
    print(f"  Max DD     : {r['max_dd_pct']:.2f}%")
    print(f"  Sharpe     : {r['sharpe']:.4f}")
    print(f"  Equity     : ${r['final']:,.2f}\n")

    if args.scramble:
        print(f"--- Scrambling Test | {args.trials} trials ---")
        sr = data_scrambling_test(closes, n_trials=args.trials)
        print(f"  Original Sharpe : {sr.get('original_sharpe', 'N/A')}")
        print(f"  p-value         : {sr.get('p_value', 'N/A')}")
        print(f"  Robust          : {'YES' if sr.get('is_robust') else 'NO'}\n")

    print("--- vs Targets ---")
    print(f"  Sharpe>0.8  : {'PASS' if r['sharpe']>0.8 else 'FAIL'} ({r['sharpe']:.4f})")
    print(f"  MaxDD<15%   : {'PASS' if r['max_dd_pct']<15 else 'FAIL'} ({r['max_dd_pct']:.2f}%)")
    print(f"  WinRate>45% : {'PASS' if r['win_rate']>0.45 else 'FAIL'} ({r['win_rate']*100:.1f}%)\n")

asyncio.run(main())
