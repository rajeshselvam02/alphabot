"""
Forex Backtester — Twelve Data
Tests Bollinger MR strategy on historical 1h bars.
Usage:
    python -m backend.backtester.backtest_forex --symbol EURUSD --days 60
    python -m backend.backtester.backtest_forex --all --days 60 --scramble
"""
import asyncio
import argparse
import aiohttp
import logging
from datetime import datetime, timezone
from typing import List, Dict
from backend.config.settings import settings

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("alphabot.backtest_forex")

# ── Twelve Data helpers ────────────────────────────────────────

API_SYMBOLS = {
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "USDCHF": "USD/CHF",
    "AUDUSD": "AUD/USD",
    "USDCAD": "USD/CAD",
    "XAUUSD": "XAU/USD",
}

PIP_SIZE = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
    "USDCAD": 0.0001, "USDCHF": 0.0001, "USDJPY": 0.01,
    "XAUUSD": 0.01,
}
PIP_VALUE = {
    "EURUSD": 1.0, "GBPUSD": 1.0, "AUDUSD": 1.0,
    "USDCAD": 0.77, "USDCHF": 1.0, "USDJPY": 0.91,
    "XAUUSD": 1.0,
}


async def fetch_candles(symbol: str, days: int) -> List[dict]:
    """Fetch historical 1h OHLCV bars from Twelve Data."""
    bars_needed = min(days * 24, 5000)
    api_sym = API_SYMBOLS.get(symbol, symbol)
    params = {
        "symbol":     api_sym,
        "interval":   "1h",
        "outputsize": bars_needed,
        "apikey":     settings.TWELVEDATA_API_KEY,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://api.twelvedata.com/time_series",
            params=params,
            timeout=aiohttp.ClientTimeout(total=20)
        ) as r:
            data = await r.json()

    if data.get("status") == "error":
        raise RuntimeError(f"API error for {symbol}: {data.get('message')}")

    values = data.get("values", [])
    values.reverse()  # oldest first

    bars = []
    for v in values:
        try:
            dt = datetime.fromisoformat(v["datetime"]).replace(tzinfo=timezone.utc)
            bars.append({
                "open_time": int(dt.timestamp() * 1000),
                "open":      float(v["open"]),
                "high":      float(v["high"]),
                "low":       float(v["low"]),
                "close":     float(v["close"]),
                "volume":    float(v.get("volume") or 0),
                "dt":        dt,
            })
        except (KeyError, ValueError):
            continue
    return bars


# ── Indicators ────────────────────────────────────────────────

def rolling_zscore(prices: List[float], lookback: int = 20) -> float:
    if len(prices) < lookback:
        return 0.0
    window = prices[-lookback:]
    mean   = sum(window) / len(window)
    std    = (sum((p - mean) ** 2 for p in window) / len(window)) ** 0.5
    if std < 1e-10:
        return 0.0
    return (prices[-1] - mean) / std


def compute_rsi(prices: List[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, period + 1):
        d = prices[-i] - prices[-i - 1]
        (gains if d > 0 else losses).append(abs(d))
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 1e-10
    if avg_loss == 0: return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def bbw(prices: List[float], lookback: int = 20) -> float:
    if len(prices) < lookback:
        return 0.0
    window = prices[-lookback:]
    mean   = sum(window) / len(window)
    std    = (sum((p - mean) ** 2 for p in window) / len(window)) ** 0.5
    return (2 * std) / mean if mean > 0 else 0.0


def is_weekend(dt: datetime) -> bool:
    w = dt.weekday()
    if w == 5:
        return True
    if w == 6 and dt.hour < 21:
        return True
    return False


def is_rollover(dt: datetime) -> bool:
    total_min = dt.hour * 60 + dt.minute
    return 21 * 60 + 45 <= total_min <= 22 * 60 + 15


# ── Backtester Core ───────────────────────────────────────────

def run_backtest(
    bars:      List[dict],
    symbol:    str,
    entry_z:   float = 2.0,
    exit_z:    float = 0.0,
    lookback:  int   = 20,
    bbw_limit: float = 0.015,
    capital:   float = 10_000.0,
) -> dict:

    equity       = capital
    position     = None   # dict or None
    trades       = []
    equity_curve = [capital]
    prices       = []
    pip          = PIP_SIZE.get(symbol, 0.0001)
    pip_val      = PIP_VALUE.get(symbol, 1.0)

    for bar in bars:
        close = bar["close"]
        dt    = bar["dt"]
        prices.append(close)

        if len(prices) < lookback + 2:
            equity_curve.append(equity)
            continue

        zscore = rolling_zscore(prices, lookback)
        rsi    = compute_rsi(prices)
        bw     = bbw(prices, lookback)

        # ── Close position ─────────────────────────────────────
        if position:
            close_long  = position["side"] == "long"  and zscore >= exit_z
            close_short = position["side"] == "short" and zscore <= -exit_z
            if close_long or close_short:
                if position["side"] == "long":
                    pnl_pips = (close - position["entry"]) / pip
                else:
                    pnl_pips = (position["entry"] - close) / pip
                pnl_usd  = pnl_pips * pip_val * position["lots"]
                equity  += pnl_usd
                trades.append({
                    "entry":    position["entry"],
                    "exit":     close,
                    "side":     position["side"],
                    "pnl_pips": pnl_pips,
                    "pnl_usd":  pnl_usd,
                    "bars_held": position["bars_held"],
                })
                position = None

        # Update bars held
        if position:
            position["bars_held"] += 1

        equity_curve.append(equity)

        # ── Open position ──────────────────────────────────────
        if position:
            continue

        if abs(zscore) < entry_z:
            continue

        # Market hours filter
        if is_weekend(dt) or is_rollover(dt):
            continue

        # BBW filter
        if bw > bbw_limit:
            continue

        # RSI filter
        candidate = "buy" if zscore < -entry_z else "sell"
        if candidate == "buy"  and rsi > 70: continue
        if candidate == "sell" and rsi < 30: continue

        side = "long" if candidate == "buy" else "short"
        lots = 0.01 if symbol == "XAUUSD" else 0.1

        position = {
            "side":       side,
            "entry":      close,
            "lots":       lots,
            "bars_held":  0,
        }

    # Close any open position at end
    if position and prices:
        close = prices[-1]
        if symbol == "XAUUSD":
            raw = (close - position["entry"]) if position["side"] == "long" else (position["entry"] - close)
            pnl_usd  = raw * 100 * position["lots"]
            pnl_pips = raw / pip
        else:
            pnl_pips = (close - position["entry"]) / pip if position["side"] == "long" else (position["entry"] - close) / pip
            pnl_usd  = pnl_pips * pip_val * position["lots"]
        equity += pnl_usd
        trades.append({
            "entry":     position["entry"],
            "exit":      close,
            "side":      position["side"],
            "pnl_pips":  pnl_pips,
            "pnl_usd":   pnl_usd,
            "bars_held": position["bars_held"],
        })

    # ── Metrics ────────────────────────────────────────────────
    n      = len(trades)
    wins   = [t for t in trades if t["pnl_usd"] > 0]
    losses = [t for t in trades if t["pnl_usd"] <= 0]
    total_return = (equity - capital) / capital * 100

    returns = []
    for i in range(1, len(equity_curve)):
        r = (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
        returns.append(r)

    sharpe = 0.0
    if returns:
        import math
        mean_r = sum(returns) / len(returns)
        std_r  = (sum((r - mean_r) ** 2 for r in returns) / len(returns)) ** 0.5
        if std_r > 0:
            sharpe = round((mean_r / std_r) * math.sqrt(24 * 252), 3)

    max_dd = 0.0
    peak   = capital
    for e in equity_curve:
        peak   = max(peak, e)
        dd     = (peak - e) / peak * 100
        max_dd = max(max_dd, dd)

    avg_pips = sum(t["pnl_pips"] for t in trades) / n if n else 0

    return {
        "symbol":        symbol,
        "bars":          len(bars),
        "trades":        n,
        "win_rate":      round(len(wins) / n * 100, 1) if n else 0,
        "return_pct":    round(total_return, 3),
        "sharpe":        sharpe,
        "max_dd_pct":    round(max_dd, 3),
        "avg_pips":      round(avg_pips, 1),
        "total_pips":    round(sum(t["pnl_pips"] for t in trades), 1),
        "equity_final":  round(equity, 2),
    }


def scramble_test(bars: List[dict], symbol: str, n_trials: int = 300, **kwargs) -> dict:
    """Data scrambling test — shuffles bar order, reruns backtest."""
    import random
    import math

    original = run_backtest(bars, symbol, **kwargs)
    orig_sharpe = original["sharpe"]

    scrambled_sharpes = []
    bar_copy = bars.copy()
    for _ in range(n_trials):
        random.shuffle(bar_copy)
        result = run_backtest(bar_copy, symbol, **kwargs)
        scrambled_sharpes.append(result["sharpe"])

    beats = sum(1 for s in scrambled_sharpes if s >= orig_sharpe)
    p_value = beats / n_trials

    return {
        "original_sharpe": orig_sharpe,
        "p_value":         round(p_value, 4),
        "is_robust":       p_value < 0.05,
        "verdict":         "ROBUST ✓" if p_value < 0.05 else "NOT ROBUST ✗",
    }


# ── CLI ───────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="AlphaBot Forex Backtester")
    parser.add_argument("--symbol",  default="EURUSD", help="Symbol e.g. EURUSD, XAUUSD")
    parser.add_argument("--all",     action="store_true", help="Backtest all 7 pairs")
    parser.add_argument("--days",    type=int,   default=60)
    parser.add_argument("--entry-z", type=float, default=2.0)
    parser.add_argument("--exit-z",  type=float, default=0.0)
    parser.add_argument("--lookback",type=int,   default=20)
    parser.add_argument("--scramble",action="store_true")
    parser.add_argument("--trials",  type=int,   default=300)
    args = parser.parse_args()

    symbols = list(API_SYMBOLS.keys()) if args.all else [args.symbol.upper()]

    kwargs = {
        "entry_z":  args.entry_z,
        "exit_z":   args.exit_z,
        "lookback": args.lookback,
    }

    print(f"\n{'='*60}")
    print(f"  AlphaBot Forex Backtester — {args.days} days | 1h bars")
    print(f"{'='*60}\n")

    for sym in symbols:
        print(f"Fetching {sym}...", end=" ", flush=True)
        try:
            bars = await fetch_candles(sym, args.days)
            print(f"{len(bars)} bars")
        except Exception as e:
            print(f"FAILED: {e}")
            continue

        result = run_backtest(bars, sym, **kwargs)

        print(f"\n── {sym} ──────────────────────────────")
        print(f"  Bars:       {result['bars']}")
        print(f"  Trades:     {result['trades']}")
        print(f"  Win rate:   {result['win_rate']}%")
        print(f"  Return:     {result['return_pct']:+.3f}%")
        print(f"  Sharpe:     {result['sharpe']}")
        print(f"  Max DD:     {result['max_dd_pct']:.3f}%")
        print(f"  Avg pips:   {result['avg_pips']:+.1f}")
        print(f"  Total pips: {result['total_pips']:+.1f}")

        if args.scramble and result["trades"] >= 5:
            print(f"  Scrambling ({args.trials} trials)...", end=" ", flush=True)
            sc = scramble_test(bars, sym, n_trials=args.trials, **kwargs)
            print(f"p={sc['p_value']} → {sc['verdict']}")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
