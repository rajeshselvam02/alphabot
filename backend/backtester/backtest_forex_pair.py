"""
Forex Pair Spread Backtester — Twelve Data
Tests cointegrated forex pair spread trading using Kalman filter Z-score.
Mirrors backtest_pair.py (crypto) but adapted for forex pip math.

Usage:
    python -m backend.backtester.backtest_forex_pair --sym1 EURUSD --sym2 GBPUSD --days 120
    python -m backend.backtester.backtest_forex_pair --all --days 120 --scramble --trials 300

Pairs tested:
    EURUSD/GBPUSD   — both EUR-correlated
    USDCHF/USDJPY   — both safe-haven USD pairs
    AUDUSD/NZDUSD   — commodity currency cousins (if available)
    USDCHF/USDCAD   — best single-pair performers
"""
import asyncio
import argparse
import aiohttp
import logging
import random
import math
from datetime import datetime, timezone
from typing import List, Dict, Tuple, Optional
from backend.config.settings import settings

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("alphabot.backtest_forex_pair")

# ── API helpers ───────────────────────────────────────────────

API_SYMBOLS = {
    "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY",
    "USDCHF": "USD/CHF", "AUDUSD": "AUD/USD", "USDCAD": "USD/CAD",
    "NZDUSD": "NZD/USD",
}

PIP_SIZE = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001, "NZDUSD": 0.0001,
    "USDCAD": 0.0001, "USDCHF": 0.0001, "USDJPY": 0.01,
}

# Test pairs: (sym1, sym2)
ALL_PAIRS = [
    ("EURUSD", "GBPUSD"),
    ("USDCHF", "USDJPY"),
    ("USDCHF", "USDCAD"),
    ("AUDUSD", "NZDUSD"),
]


async def fetch_candles(symbol: str, interval: str, days: int) -> List[dict]:
    bars_needed = min(days * 24, 5000)
    api_sym = API_SYMBOLS.get(symbol, symbol)
    params = {
        "symbol":     api_sym,
        "interval":   interval,
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
    values.reverse()
    bars = []
    for v in values:
        try:
            dt = datetime.fromisoformat(v["datetime"]).replace(tzinfo=timezone.utc)
            bars.append({
                "open_time": int(dt.timestamp() * 1000),
                "close":     float(v["close"]),
                "dt":        dt,
            })
        except (KeyError, ValueError):
            continue
    return bars


def align_bars(bars1: List[dict], bars2: List[dict]) -> Tuple[List[float], List[float]]:
    """Align two bar series by timestamp — only keep matching timestamps."""
    ts1 = {b["open_time"]: b["close"] for b in bars1}
    ts2 = {b["open_time"]: b["close"] for b in bars2}
    common = sorted(set(ts1.keys()) & set(ts2.keys()))
    p1 = [ts1[t] for t in common]
    p2 = [ts2[t] for t in common]
    return p1, p2


# ── Kalman Filter ─────────────────────────────────────────────

class KalmanSpread:
    """
    Kalman filter for dynamic hedge ratio estimation.
    Same as quant_signals.KalmanFilter but self-contained for backtester.
    Chan Ch.3, Eqs 3.7-3.13
    """
    def __init__(self, delta: float = 0.0001, Ve: float = 0.001):
        self.delta = delta
        self.Ve    = Ve
        self.Vw    = delta / (1 - delta)
        self.P     = 0.0
        self.R     = None
        self.beta  = None   # [intercept, hedge_ratio]
        self.errors: List[float] = []

    def update(self, x: float, y: float) -> Optional[float]:
        """
        x = price of sym1 (independent)
        y = price of sym2 (dependent)
        Returns rolling Z-score of forecast errors, or None if not warmed up.
        """
        F = [1.0, x]

        if self.beta is None:
            self.beta = [0.0, 1.0]
            self.P    = 1.0
            return None

        # Prediction
        y_hat = self.beta[0] * F[0] + self.beta[1] * F[1]
        e     = y - y_hat

        # Update R
        if self.R is None:
            self.R = self.P + self.Vw
        else:
            self.R = self.P + self.Vw

        # Kalman gain
        denom = self.R * (F[0]**2 + F[1]**2) + self.Ve
        K = [self.R * f / denom for f in F]

        # Update beta
        self.beta = [self.beta[i] + K[i] * e for i in range(2)]
        self.P    = (1 - K[0] * F[0] - K[1] * F[1]) * self.R

        # Rolling Z-score of forecast errors
        self.errors.append(e)
        if len(self.errors) > 50:
            self.errors = self.errors[-50:]
        if len(self.errors) < 20:
            return None

        mean_e = sum(self.errors) / len(self.errors)
        std_e  = (sum((err - mean_e)**2 for err in self.errors) / len(self.errors))**0.5
        if std_e < 1e-10:
            return None

        return (e - mean_e) / std_e


# ── Indicators ────────────────────────────────────────────────

def compute_hurst(prices: List[float]) -> float:
    """Hurst exponent — < 0.5 = mean-reverting."""
    if len(prices) < 20:
        return 0.5
    lags = range(2, min(20, len(prices) // 2))
    tau  = []
    for lag in lags:
        diffs = [prices[i] - prices[i - lag] for i in range(lag, len(prices))]
        std   = (sum(d**2 for d in diffs) / len(diffs))**0.5
        tau.append(std)
    if len(tau) < 2:
        return 0.5
    try:
        x = [math.log(l) for l in lags]
        y = [math.log(t) if t > 0 else 0 for t in tau]
        n = len(x)
        sx = sum(x); sy = sum(y)
        sxx = sum(xi**2 for xi in x)
        sxy = sum(x[i]*y[i] for i in range(n))
        denom = n * sxx - sx**2
        if denom == 0:
            return 0.5
        return (n * sxy - sx * sy) / denom
    except Exception:
        return 0.5


def compute_half_life(prices: List[float]) -> float:
    """OU half-life in bars."""
    if len(prices) < 10:
        return 999.0
    diffs  = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    lagged = prices[:-1]
    n      = len(diffs)
    if n < 2:
        return 999.0
    mean_l = sum(lagged) / n
    mean_d = sum(diffs)  / n
    num    = sum((lagged[i] - mean_l) * (diffs[i] - mean_d) for i in range(n))
    den    = sum((lagged[i] - mean_l)**2 for i in range(n))
    if den == 0 or num == 0:
        return 999.0
    lam = num / den
    if lam >= 0:
        return 999.0
    return round(-math.log(2) / lam, 1)


def adf_pvalue(prices: List[float]) -> float:
    """Simplified ADF p-value approximation."""
    if len(prices) < 20:
        return 1.0
    hl = compute_half_life(prices)
    n  = len(prices)
    # Approximate: shorter half-life relative to series length = more stationary
    ratio = hl / n
    if ratio < 0.1:   return 0.01
    if ratio < 0.2:   return 0.05
    if ratio < 0.4:   return 0.10
    return 0.50


def is_weekend(dt: datetime) -> bool:
    w = dt.weekday()
    return w == 5 or (w == 6 and dt.hour < 21)


# ── Pair Backtester ───────────────────────────────────────────

def run_pair_backtest(
    prices1:  List[float],
    prices2:  List[float],
    sym1:     str,
    sym2:     str,
    entry_z:  float = 2.0,
    exit_z:   float = 0.0,
    capital:  float = 10_000.0,
    lots:     float = 0.1,
) -> dict:
    """
    Spread trading: when Z > +entry_z → sell sym1, buy sym2
                    when Z < -entry_z → buy sym1, sell sym2
    Exit when Z crosses back to exit_z.
    P&L calculated in pips on sym1 leg (primary).
    """
    kf           = KalmanSpread(delta=0.0001, Ve=0.001)
    equity       = capital
    position     = None
    trades       = []
    equity_curve = [capital]
    pip1         = PIP_SIZE.get(sym1, 0.0001)

    n = min(len(prices1), len(prices2))

    for i in range(n):
        p1 = prices1[i]
        p2 = prices2[i]

        zscore = kf.update(p1, p2)

        if zscore is None:
            equity_curve.append(equity)
            continue

        # ── Close position ─────────────────────────────────────
        if position:
            close_long  = position["side"] == "long"  and zscore >= exit_z
            close_short = position["side"] == "short" and zscore <= -exit_z

            if close_long or close_short:
                # P&L on sym1 leg (primary)
                if position["side"] == "long":
                    pnl_pips = (p1 - position["entry1"]) / pip1
                else:
                    pnl_pips = (position["entry1"] - p1) / pip1

                pnl_usd  = pnl_pips * lots * 1.0  # $1/pip/mini lot
                equity  += pnl_usd
                trades.append({
                    "side":      position["side"],
                    "entry1":    position["entry1"],
                    "exit1":     p1,
                    "pnl_pips":  round(pnl_pips, 1),
                    "pnl_usd":   round(pnl_usd, 4),
                    "bars_held": position["bars_held"],
                })
                position = None

        if position:
            position["bars_held"] += 1

        equity_curve.append(equity)

        if position:
            continue

        if abs(zscore) < entry_z:
            continue

        side = "long" if zscore < -entry_z else "short"
        position = {
            "side":      side,
            "entry1":    p1,
            "entry2":    p2,
            "bars_held": 0,
        }

    # Close open position at end
    if position:
        p1 = prices1[-1]
        if position["side"] == "long":
            pnl_pips = (p1 - position["entry1"]) / pip1
        else:
            pnl_pips = (position["entry1"] - p1) / pip1
        pnl_usd = pnl_pips * lots * 1.0
        equity += pnl_usd
        trades.append({
            "side":      position["side"],
            "entry1":    position["entry1"],
            "exit1":     p1,
            "pnl_pips":  round(pnl_pips, 1),
            "pnl_usd":   round(pnl_usd, 4),
            "bars_held": position["bars_held"],
        })

    # ── Metrics ────────────────────────────────────────────────
    n_trades  = len(trades)
    wins      = [t for t in trades if t["pnl_usd"] > 0]
    total_ret = (equity - capital) / capital * 100

    returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i-1]
        if prev > 0:
            returns.append((equity_curve[i] - prev) / prev)

    sharpe = 0.0
    if returns:
        mean_r = sum(returns) / len(returns)
        std_r  = (sum((r - mean_r)**2 for r in returns) / len(returns))**0.5
        if std_r > 0:
            sharpe = round((mean_r / std_r) * math.sqrt(24 * 252), 3)

    max_dd = 0.0
    peak   = capital
    for e in equity_curve:
        peak   = max(peak, e)
        dd     = (peak - e) / peak * 100
        max_dd = max(max_dd, dd)

    spread = [prices2[i] - prices1[i] for i in range(min(len(prices1), len(prices2)))]
    hurst  = compute_hurst(spread)
    hl     = compute_half_life(spread)
    adf_p  = adf_pvalue(spread)

    return {
        "pair":        f"{sym1}/{sym2}",
        "bars":        n,
        "hurst":       round(hurst, 4),
        "half_life":   hl,
        "adf_pvalue":  adf_p,
        "cointegrated": adf_p < 0.05,
        "trades":      n_trades,
        "win_rate":    round(len(wins) / n_trades * 100, 1) if n_trades else 0,
        "return_pct":  round(total_ret, 3),
        "sharpe":      sharpe,
        "max_dd_pct":  round(max_dd, 3),
        "avg_pips":    round(sum(t["pnl_pips"] for t in trades) / n_trades, 1) if n_trades else 0,
        "total_pips":  round(sum(t["pnl_pips"] for t in trades), 1),
    }


def scramble_test(
    prices1: List[float],
    prices2: List[float],
    sym1: str,
    sym2: str,
    n_trials: int = 300,
    **kwargs
) -> dict:
    orig        = run_pair_backtest(prices1, prices2, sym1, sym2, **kwargs)
    orig_sharpe = orig["sharpe"]
    p1 = prices1.copy()
    p2 = prices2.copy()
    beats = 0
    for _ in range(n_trials):
        random.shuffle(p1)
        random.shuffle(p2)
        r = run_pair_backtest(p1, p2, sym1, sym2, **kwargs)
        if r["sharpe"] >= orig_sharpe:
            beats += 1
    p_value = beats / n_trials
    return {
        "original_sharpe": orig_sharpe,
        "p_value":         round(p_value, 4),
        "is_robust":       p_value < 0.05,
        "verdict":         "ROBUST ✓" if p_value < 0.05 else "NOT ROBUST ✗",
    }


# ── CLI ───────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="AlphaBot Forex Pair Spread Backtester")
    parser.add_argument("--sym1",    default="EURUSD")
    parser.add_argument("--sym2",    default="GBPUSD")
    parser.add_argument("--all",     action="store_true", help="Test all predefined pairs")
    parser.add_argument("--days",    type=int,   default=120)
    parser.add_argument("--interval",default="1h")
    parser.add_argument("--entry-z", type=float, default=2.0)
    parser.add_argument("--exit-z",  type=float, default=0.0)
    parser.add_argument("--scramble",action="store_true")
    parser.add_argument("--trials",  type=int,   default=300)
    args = parser.parse_args()

    pairs = ALL_PAIRS if args.all else [(args.sym1.upper(), args.sym2.upper())]
    kwargs = {"entry_z": args.entry_z, "exit_z": args.exit_z}

    print(f"\n{'='*60}")
    print(f"  AlphaBot Forex Pair Backtester — {args.days}d | {args.interval}")
    print(f"{'='*60}\n")

    for sym1, sym2 in pairs:
        print(f"Fetching {sym1}...", end=" ", flush=True)
        try:
            bars1 = await fetch_candles(sym1, args.interval, args.days)
            print(f"{len(bars1)} bars")
        except Exception as e:
            print(f"FAILED: {e}")
            continue

        print(f"Fetching {sym2}...", end=" ", flush=True)
        try:
            bars2 = await fetch_candles(sym2, args.interval, args.days)
            print(f"{len(bars2)} bars")
        except Exception as e:
            print(f"FAILED: {e}")
            continue

        p1, p2 = align_bars(bars1, bars2)
        if len(p1) < 100:
            print(f"  Not enough aligned bars ({len(p1)}) — skipping")
            continue

        result = run_pair_backtest(p1, p2, sym1, sym2, **kwargs)

        print(f"\n── {sym1}/{sym2} {'─'*35}")
        print(f"  Aligned bars:  {result['bars']}")
        print(f"  Hurst:         {result['hurst']} ({'mean-reverting ✓' if result['hurst'] < 0.5 else 'trending ✗'})")
        print(f"  Half-life:     {result['half_life']} bars")
        print(f"  ADF p-value:   {result['adf_pvalue']} ({'cointegrated ✓' if result['cointegrated'] else 'not cointegrated ✗'})")
        print(f"  Trades:        {result['trades']}")
        print(f"  Win rate:      {result['win_rate']}%")
        print(f"  Return:        {result['return_pct']:+.3f}%")
        print(f"  Sharpe:        {result['sharpe']}")
        print(f"  Max DD:        {result['max_dd_pct']:.3f}%")
        print(f"  Avg pips:      {result['avg_pips']:+.1f}")
        print(f"  Total pips:    {result['total_pips']:+.1f}")

        if args.scramble and result["trades"] >= 5:
            print(f"  Scrambling ({args.trials} trials)...", end=" ", flush=True)
            sc = scramble_test(p1, p2, sym1, sym2, n_trials=args.trials, **kwargs)
            print(f"p={sc['p_value']} → {sc['verdict']}")

        print()

    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
