"""
XAUUSD Intraday Mean Reversion Backtester
Anchor: VWAP (24h rolling) vs Daily Open — whichever is closer to price
Timeframe: 1h bars
Filters: RAVI < threshold, ADX < 25
Sizing: Fixed lots with ATR-based stop loss
"""
import asyncio
import argparse
import aiohttp
import logging
import math
import random
from datetime import datetime, timezone
from typing import List, Optional
from backend.config.settings import settings

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("alphabot.backtest_xau_vwap")

TWELVEDATA_URL = "https://api.twelvedata.com/time_series"

# ── Data Fetch ─────────────────────────────────────────────────

async def fetch_candles(days: int) -> List[dict]:
    """Fetch XAUUSD 1h bars from Twelve Data."""
    bars_needed = min(days * 24, 5000)
    params = {
        "symbol":     "XAU/USD",
        "interval":   "1h",
        "outputsize": bars_needed,
        "apikey":     settings.TWELVEDATA_API_KEY,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(
            TWELVEDATA_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=30)
        ) as r:
            data = await r.json()

    if data.get("status") == "error":
        raise RuntimeError(f"API error: {data.get('message')}")

    values = data.get("values", [])
    values.reverse()  # oldest first

    bars = []
    for v in values:
        try:
            dt = datetime.fromisoformat(v["datetime"]).replace(tzinfo=timezone.utc)
            bars.append({
                "dt":     dt,
                "open":   float(v["open"]),
                "high":   float(v["high"]),
                "low":    float(v["low"]),
                "close":  float(v["close"]),
                "volume": float(v.get("volume") or 1.0),  # fallback 1.0 to avoid /0
            })
        except (KeyError, ValueError):
            continue
    return bars


# ── Indicators ─────────────────────────────────────────────────

def rolling_vwap(bars: List[dict], lookback: int = 24) -> float:
    """Volume-weighted average price over last `lookback` bars."""
    window = bars[-lookback:]
    total_vol = sum(b["volume"] for b in window)
    if total_vol < 1e-10:
        return bars[-1]["close"]
    return sum(((b["high"] + b["low"] + b["close"]) / 3) * b["volume"] for b in window) / total_vol


def daily_open(bars: List[dict]) -> float:
    """Price of the first bar of the current UTC day."""
    today = bars[-1]["dt"].date()
    for b in reversed(bars):
        if b["dt"].date() == today:
            first = b
    return first["open"]


def rolling_std(prices: List[float], lookback: int = 24) -> float:
    if len(prices) < lookback:
        return 1.0
    window = prices[-lookback:]
    mean = sum(window) / len(window)
    variance = sum((p - mean) ** 2 for p in window) / len(window)
    return max(variance ** 0.5, 1e-10)


def compute_atr(bars: List[dict], period: int = 14) -> float:
    """Average True Range."""
    if len(bars) < period + 1:
        return bars[-1]["high"] - bars[-1]["low"]
    trs = []
    for i in range(1, period + 1):
        b = bars[-i]
        prev_close = bars[-i - 1]["close"]
        tr = max(b["high"] - b["low"],
                 abs(b["high"] - prev_close),
                 abs(b["low"]  - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs)


def compute_adx(bars: List[dict], period: int = 14) -> float:
    """Average Directional Index — trend strength."""
    if len(bars) < period * 2:
        return 25.0  # neutral default

    plus_dm_list, minus_dm_list, tr_list = [], [], []
    for i in range(1, len(bars)):
        h_diff = bars[i]["high"] - bars[i-1]["high"]
        l_diff = bars[i-1]["low"] - bars[i]["low"]
        plus_dm_list.append(h_diff if h_diff > l_diff and h_diff > 0 else 0)
        minus_dm_list.append(l_diff if l_diff > h_diff and l_diff > 0 else 0)
        prev_close = bars[i-1]["close"]
        tr = max(bars[i]["high"] - bars[i]["low"],
                 abs(bars[i]["high"] - prev_close),
                 abs(bars[i]["low"]  - prev_close))
        tr_list.append(tr)

    def smooth(data, p):
        s = sum(data[:p])
        result = [s]
        for v in data[p:]:
            s = s - s/p + v
            result.append(s)
        return result

    atr_s   = smooth(tr_list, period)
    plus_s  = smooth(plus_dm_list, period)
    minus_s = smooth(minus_dm_list, period)

    dx_list = []
    for a, p, m in zip(atr_s, plus_s, minus_s):
        if a < 1e-10:
            continue
        plus_di  = 100 * p / a
        minus_di = 100 * m / a
        denom = plus_di + minus_di
        if denom < 1e-10:
            continue
        dx_list.append(100 * abs(plus_di - minus_di) / denom)

    if not dx_list:
        return 25.0
    return sum(dx_list[-period:]) / min(len(dx_list), period)


def compute_ravi(prices: List[float], short: int = 7, long: int = 65) -> float:
    """RAVI regime filter — low value = mean-reverting, high = trending."""
    if len(prices) < long:
        return 10.0  # assume trending if not enough data
    ma_short = sum(prices[-short:]) / short
    ma_long  = sum(prices[-long:])  / long
    if ma_long < 1e-10:
        return 0.0
    return abs((ma_short - ma_long) / ma_long) * 100


# ── Core Backtest ──────────────────────────────────────────────

def run_backtest(
    bars:        List[dict],
    entry_z:     float = 2.0,
    exit_z:      float = 0.3,
    lookback:    int   = 24,
    adx_limit:   float = 25.0,
    ravi_limit:  float = 5.0,
    atr_stop:    float = 2.0,
    lots:        float = 0.01,
    capital:     float = 10_000.0,
    use_filters: bool  = True,
) -> dict:

    equity       = capital
    position     = None
    trades       = []
    equity_curve = [capital]
    prices       = []
    warmup       = max(lookback, 65, 28) + 2  # enough for all indicators

    for i, bar in enumerate(bars):
        close = bar["close"]
        prices.append(close)

        if i < warmup:
            equity_curve.append(equity)
            continue

        # ── Indicators ─────────────────────────────────────────
        vwap      = rolling_vwap(bars[:i+1], lookback)
        d_open    = daily_open(bars[:i+1])
        std       = rolling_std(prices, lookback)
        atr       = compute_atr(bars[:i+1], 14)

        # Use whichever anchor is closer to current price
        anchor = vwap if abs(close - vwap) < abs(close - d_open) else d_open
        zscore = (close - anchor) / std

        # ── Close position ─────────────────────────────────────
        if position:
            # ATR stop loss
            if position["side"] == "long":
                stop_hit = close < position["entry"] - atr_stop * position["atr"]
                target   = zscore >= exit_z
            else:
                stop_hit = close > position["entry"] + atr_stop * position["atr"]
                target   = zscore <= -exit_z

            if target or stop_hit:
                raw_pnl = (close - position["entry"]) if position["side"] == "long" \
                          else (position["entry"] - close)
                pnl_usd = raw_pnl * 100 * lots  # XAU: $1/pip × 100 oz per lot × 0.01 lot
                equity += pnl_usd
                trades.append({
                    "entry":      position["entry"],
                    "exit":       close,
                    "side":       position["side"],
                    "pnl_usd":    pnl_usd,
                    "bars_held":  position["bars_held"],
                    "exit_reason": "target" if target else "stop",
                    "anchor":     position["anchor"],
                })
                position = None

        if position:
            position["bars_held"] += 1

        equity_curve.append(equity)

        # ── Open position ──────────────────────────────────────
        if position or abs(zscore) < entry_z:
            continue

        if use_filters:
            adx  = compute_adx(bars[:i+1], 14)
            ravi = compute_ravi(prices, 7, 65)
            if adx  > adx_limit:  continue   # trending — skip
            if ravi > ravi_limit: continue   # trending — skip

        side = "long" if zscore < -entry_z else "short"
        position = {
            "side":      side,
            "entry":     close,
            "atr":       atr,
            "bars_held": 0,
            "anchor":    "vwap" if anchor == vwap else "daily_open",
        }

    # Close any open position at end
    if position and prices:
        raw_pnl = (prices[-1] - position["entry"]) if position["side"] == "long" \
                  else (position["entry"] - prices[-1])
        pnl_usd = raw_pnl * 100 * lots
        equity += pnl_usd
        trades.append({
            "entry":      position["entry"],
            "exit":       prices[-1],
            "side":       position["side"],
            "pnl_usd":    pnl_usd,
            "bars_held":  position["bars_held"],
            "exit_reason": "end",
            "anchor":     position["anchor"],
        })

    # ── Metrics ────────────────────────────────────────────────
    n      = len(trades)
    wins   = [t for t in trades if t["pnl_usd"] > 0]
    total_return = (equity - capital) / capital * 100

    returns = []
    for i in range(1, len(equity_curve)):
        r = (equity_curve[i] - equity_curve[i-1]) / equity_curve[i-1]
        returns.append(r)

    sharpe = 0.0
    if returns:
        mean_r = sum(returns) / len(returns)
        std_r  = (sum((r - mean_r)**2 for r in returns) / len(returns)) ** 0.5
        if std_r > 0:
            sharpe = round((mean_r / std_r) * math.sqrt(24 * 252), 3)

    max_dd = 0.0
    peak   = capital
    for e in equity_curve:
        peak   = max(peak, e)
        dd     = (peak - e) / peak * 100
        max_dd = max(max_dd, dd)

    # Anchor breakdown
    vwap_trades  = [t for t in trades if t.get("anchor") == "vwap"]
    dopen_trades = [t for t in trades if t.get("anchor") == "daily_open"]
    stop_outs    = [t for t in trades if t.get("exit_reason") == "stop"]

    return {
        "bars":           len(bars),
        "trades":         n,
        "win_rate":       round(len(wins) / n * 100, 1) if n else 0,
        "return_pct":     round(total_return, 3),
        "sharpe":         sharpe,
        "max_dd_pct":     round(max_dd, 3),
        "equity_final":   round(equity, 2),
        "vwap_trades":    len(vwap_trades),
        "dopen_trades":   len(dopen_trades),
        "stop_outs":      len(stop_outs),
        "stop_out_pct":   round(len(stop_outs) / n * 100, 1) if n else 0,
    }


# ── Scrambling Test ────────────────────────────────────────────

def scramble_test(bars: List[dict], n_trials: int = 300, **kwargs) -> dict:
    original     = run_backtest(bars, **kwargs)
    orig_sharpe  = original["sharpe"]
    bar_copy     = bars.copy()

    scrambled = []
    for _ in range(n_trials):
        random.shuffle(bar_copy)
        r = run_backtest(bar_copy, **kwargs)
        scrambled.append(r["sharpe"])

    beats   = sum(1 for s in scrambled if s >= orig_sharpe)
    p_value = beats / n_trials
    return {
        "original_sharpe": orig_sharpe,
        "p_value":         round(p_value, 4),
        "is_robust":       p_value < 0.05,
        "verdict":         "ROBUST ✓" if p_value < 0.05 else "NOT ROBUST ✗",
    }


# ── CLI ────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="XAUUSD VWAP Mean Reversion Backtester")
    parser.add_argument("--days",       type=int,   default=90)
    parser.add_argument("--entry-z",    type=float, default=2.0)
    parser.add_argument("--exit-z",     type=float, default=0.3)
    parser.add_argument("--lookback",   type=int,   default=24)
    parser.add_argument("--adx-limit",  type=float, default=25.0)
    parser.add_argument("--ravi-limit", type=float, default=5.0)
    parser.add_argument("--atr-stop",   type=float, default=2.0)
    parser.add_argument("--no-filters", action="store_true", help="Disable RAVI+ADX filters")
    parser.add_argument("--scramble",   action="store_true")
    parser.add_argument("--trials",     type=int,   default=300)
    parser.add_argument("--optimize",   action="store_true", help="Grid search best params")
    args = parser.parse_args()

    print(f"\nFetching XAUUSD 1h bars ({args.days} days)...")
    bars = await fetch_candles(args.days)
    print(f"Loaded {len(bars)} bars\n")

    kwargs = dict(
        entry_z     = args.entry_z,
        exit_z      = args.exit_z,
        lookback    = args.lookback,
        adx_limit   = args.adx_limit,
        ravi_limit  = args.ravi_limit,
        atr_stop    = args.atr_stop,
        use_filters = not args.no_filters,
    )

    if args.optimize:
        print("── Grid Search ──────────────────────────────────────")
        best = None
        best_sharpe = -999
        for ez in [1.5, 2.0, 2.5]:
            for lb in [12, 24, 48]:
                for adx in [20.0, 25.0, 30.0]:
                    for atr in [1.5, 2.0, 3.0]:
                        kw = dict(entry_z=ez, exit_z=0.3, lookback=lb,
                                  adx_limit=adx, ravi_limit=5.0, atr_stop=atr,
                                  use_filters=True)
                        r = run_backtest(bars, **kw)
                        if r["trades"] >= 10 and r["sharpe"] > best_sharpe:
                            best_sharpe = r["sharpe"]
                            best = (kw, r)
        if best:
            kw, r = best
            print(f"Best params: entry_z={kw['entry_z']} lookback={kw['lookback']} "
                  f"adx={kw['adx_limit']} atr_stop={kw['atr_stop']}")
            print(f"Sharpe={r['sharpe']} | Return={r['return_pct']}% | "
                  f"Trades={r['trades']} | WinRate={r['win_rate']}% | MaxDD={r['max_dd_pct']}%")
        return

    r = run_backtest(bars, **kwargs)

    print("── XAUUSD VWAP Mean Reversion Backtest ─────────────────")
    print(f"Bars:        {r['bars']} ({args.days}d × 1h)")
    print(f"Trades:      {r['trades']}")
    print(f"Win rate:    {r['win_rate']}%")
    print(f"Return:      {r['return_pct']}%")
    print(f"Sharpe:      {r['sharpe']}")
    print(f"Max DD:      {r['max_dd_pct']}%")
    print(f"Equity:      ${r['equity_final']:,.2f}")
    print(f"")
    print(f"Anchor breakdown:")
    print(f"  VWAP trades:       {r['vwap_trades']}")
    print(f"  Daily open trades: {r['dopen_trades']}")
    print(f"  Stop outs:         {r['stop_outs']} ({r['stop_out_pct']}%)")

    if not args.no_filters:
        print(f"\nFilters: ADX<{args.adx_limit}, RAVI<{args.ravi_limit}, ATR stop×{args.atr_stop}")
    else:
        print(f"\nFilters: DISABLED")

    if args.scramble:
        print(f"\nRunning scrambling test ({args.trials} trials)...")
        sc = scramble_test(bars, n_trials=args.trials, **kwargs)
        print(f"Original Sharpe: {sc['original_sharpe']}")
        print(f"P-value:         {sc['p_value']}")
        print(f"Verdict:         {sc['verdict']}")


if __name__ == "__main__":
    asyncio.run(main())
