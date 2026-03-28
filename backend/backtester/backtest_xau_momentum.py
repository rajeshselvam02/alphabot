"""
XAUUSD Momentum Backtester — Twelve Data
Dual MA crossover + RAVI + DXY macro filter + ATR-based position sizing.

Key upgrades from books:
  1. DXY macro filter (Global Macro Ch.10) — only long Gold when USD falling,
     only short Gold when USD rising. Gold/DXY correlation ~-0.7.
  2. ATR-based position sizing (Gliner Ch.2) — N units = 1% capital / (2×ATR).
     Auto-scales down in volatile regimes, up in calm ones.
  3. Daily bars support — larger avg move per trade vs 4h, spread cost negligible.

Strategy logic:
  Entry LONG:  fast_ma > slow_ma AND RAVI > ravi_thresh AND DXY falling
  Entry SHORT: fast_ma < slow_ma AND RAVI > ravi_thresh AND DXY rising
  Exit:        MA cross reversal OR RAVI < exit_thresh
  Stop loss:   2× ATR from entry (dynamic, not fixed %)

Usage:
    python -m backend.backtester.backtest_xau_momentum --days 365 --interval 1day --scramble
    python -m backend.backtester.backtest_xau_momentum --days 365 --interval 4h --sweep
    python -m backend.backtester.backtest_xau_momentum --days 365 --interval 1day --no-dxy --scramble
"""
import asyncio
import argparse
import aiohttp
import logging
import random
import math
from datetime import datetime, timezone
from typing import List, Optional, Dict
from backend.config.settings import settings

logging.basicConfig(level=logging.WARNING)

BASE_URL = "https://api.twelvedata.com"
SYMBOL   = "XAU/USD"
LOT_OZ   = 100       # 1 standard lot = 100 troy oz


# ── Data Fetching ─────────────────────────────────────────────

async def fetch_candles(symbol: str, interval: str, days: int) -> List[dict]:
    """Fetch OHLCV bars from Twelve Data for any symbol."""
    # Calculate bars needed based on interval
    interval_hours = {
        "1h": 1, "2h": 2, "4h": 4, "8h": 8,
        "1day": 24, "1week": 168,
    }
    hrs = interval_hours.get(interval, 4)
    bars_needed = min(int(days * 24 / hrs) + 50, 5000)

    params = {
        "symbol":     symbol,
        "interval":   interval,
        "outputsize": bars_needed,
        "apikey":     settings.TWELVEDATA_API_KEY,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{BASE_URL}/time_series",
            params=params,
            timeout=aiohttp.ClientTimeout(total=20)
        ) as r:
            data = await r.json()

    if data.get("status") == "error":
        raise RuntimeError(f"API error for {symbol}: {data.get('message')}")

    values = data.get("values", [])
    if not values:
        raise RuntimeError(f"No data returned for {symbol}")

    values.reverse()  # oldest first
    bars = []
    for v in values:
        try:
            dt = datetime.fromisoformat(v["datetime"]).replace(tzinfo=timezone.utc)
            bars.append({
                "open_time": int(dt.timestamp() * 1000),
                "open":  float(v["open"]),
                "high":  float(v["high"]),
                "low":   float(v["low"]),
                "close": float(v["close"]),
                "dt":    dt,
            })
        except (KeyError, ValueError):
            continue
    return bars


def align_series(bars_xau: List[dict], bars_dxy: List[dict]) -> tuple:
    """
    Align XAU and DXY bars by timestamp.
    Returns (xau_aligned, dxy_closes_aligned).
    DXY may have fewer bars (forex market hours) — forward-fill gaps.
    """
    dxy_map: Dict[int, float] = {}
    for b in bars_dxy:
        dxy_map[b["open_time"]] = b["close"]

    xau_aligned = []
    dxy_aligned  = []
    last_dxy     = None

    for b in bars_xau:
        ts = b["open_time"]
        dxy_close = dxy_map.get(ts)
        if dxy_close is not None:
            last_dxy = dxy_close
        if last_dxy is not None:
            xau_aligned.append(b)
            dxy_aligned.append(last_dxy)

    return xau_aligned, dxy_aligned


# ── Indicators ────────────────────────────────────────────────

def sma(prices: List[float], period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def compute_ravi(prices: List[float], short: int = 7, long: int = 65) -> Optional[float]:
    """RAVI = |SMA_short - SMA_long| / SMA_long × 100 (Chande Ch.3)"""
    if len(prices) < long:
        return None
    s = sma(prices, short)
    l = sma(prices, long)
    if s is None or l is None or l == 0:
        return None
    return abs(s - l) / l * 100


def compute_atr(bars: List[dict], period: int = 14) -> Optional[float]:
    """Average True Range — used for dynamic stop sizing (Gliner Ch.2)"""
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        h    = bars[i]["high"]
        l    = bars[i]["low"]
        prev = bars[i-1]["close"]
        tr   = max(h - l, abs(h - prev), abs(l - prev))
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def compute_adx(bars: List[dict], period: int = 14) -> Optional[float]:
    """ADX — trend strength filter. > 25 = strong trend."""
    if len(bars) < period * 2:
        return None
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(bars)):
        h, l   = bars[i]["high"],   bars[i]["low"]
        ph, pl = bars[i-1]["high"], bars[i-1]["low"]
        pc     = bars[i-1]["close"]
        up   = h - ph
        down = pl - l
        plus_dm.append(up   if up > down and up > 0   else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    def smooth(vals, p):
        s = sum(vals[:p])
        result = [s]
        for v in vals[p:]:
            s = s - s / p + v
            result.append(s)
        return result

    str_  = smooth(trs,      period)
    spdm  = smooth(plus_dm,  period)
    smdm  = smooth(minus_dm, period)
    dx    = []
    for i in range(len(str_)):
        if str_[i] == 0:
            continue
        pdi = 100 * spdm[i] / str_[i]
        mdi = 100 * smdm[i] / str_[i]
        s   = pdi + mdi
        if s == 0:
            continue
        dx.append(100 * abs(pdi - mdi) / s)
    if len(dx) < period:
        return None
    return sum(dx[-period:]) / period


# ── ATR-Based Lot Sizing ──────────────────────────────────────

def atr_lot_size(
    capital:    float,
    atr_val:    float,
    risk_pct:   float = 0.01,
    atr_mult:   float = 2.0,
    min_lots:   float = 0.01,
    max_lots:   float = 0.10,
) -> float:
    """
    Gliner Ch.2: N units = risk_amount / (atr_mult × ATR × oz_per_lot)
    Risk 1% of capital per trade. Stop = 2×ATR.
    """
    risk_usd      = capital * risk_pct
    stop_distance = atr_mult * atr_val   # $ move for stop
    lot_dollar_risk = stop_distance * LOT_OZ
    if lot_dollar_risk <= 0:
        return min_lots
    lots = risk_usd / lot_dollar_risk
    return round(max(min_lots, min(max_lots, lots)), 3)


# ── DXY Trend Check ───────────────────────────────────────────

def dxy_trend(dxy_closes: List[float], fast: int = 20, slow: int = 50) -> Optional[str]:
    """
    Global Macro Ch.10: Gold and DXY are ~-0.7 correlated.
    DXY falling (SMA20 < SMA50) → Gold bullish environment.
    DXY rising (SMA20 > SMA50) → Gold bearish environment.
    Returns 'falling', 'rising', or None if not enough data.
    """
    f = sma(dxy_closes, fast)
    s = sma(dxy_closes, slow)
    if f is None or s is None:
        return None
    return "falling" if f < s else "rising"


# ── Core Backtest ─────────────────────────────────────────────

def run_backtest(
    bars:        List[dict],
    dxy_closes:  Optional[List[float]] = None,
    fast_period: int   = 20,
    slow_period: int   = 50,
    ravi_entry:  float = 3.5,
    ravi_exit:   float = 1.5,
    adx_min:     float = 25.0,
    atr_mult:    float = 2.0,
    risk_pct:    float = 0.01,
    use_dxy:     bool  = True,
    capital:     float = 10_000.0,
) -> dict:
    """
    Run XAUUSD momentum backtest.
    dxy_closes: aligned DXY close prices (same length as bars after alignment).
                If None or use_dxy=False, DXY filter is skipped.
    """
    equity       = capital
    position     = None
    trades       = []
    equity_curve = [capital]
    closes       = []
    filtered_dxy = 0   # count of trades filtered by DXY

    n = len(bars)

    for i, bar in enumerate(bars):
        close = bar["close"]
        closes.append(close)

        # Need enough bars for slowest indicator
        warmup = max(slow_period + 5, 65 + 5)  # RAVI needs 65 bars max
        if len(closes) < warmup:
            equity_curve.append(equity)
            continue

        fast_ma = sma(closes, fast_period)
        slow_ma = sma(closes, slow_period)
        ravi    = compute_ravi(closes)
        adx     = compute_adx(bars[:i+1])
        atr     = compute_atr(bars[:i+1])

        if fast_ma is None or slow_ma is None or ravi is None or atr is None:
            equity_curve.append(equity)
            continue

        ma_bull = fast_ma > slow_ma
        ma_bear = fast_ma < slow_ma

        # DXY macro filter (Global Macro Ch.10)
        dxy_direction = None
        if use_dxy and dxy_closes and i < len(dxy_closes):
            dxy_window = dxy_closes[max(0, i - slow_period):i + 1]
            dxy_direction = dxy_trend(dxy_window)

        # ── Stop loss ──────────────────────────────────────────
        if position:
            stop_hit = (
                (position["side"] == "long"  and close <= position["stop"]) or
                (position["side"] == "short" and close >= position["stop"])
            )
            if stop_hit:
                pnl = self_pnl(position, close)
                equity += pnl
                trades.append(make_trade(position, close, pnl, "stop_loss"))
                position = None

        # ── Exit on MA cross or RAVI collapse ─────────────────
        if position:
            exit_long  = position["side"] == "long"  and (ma_bear or ravi < ravi_exit)
            exit_short = position["side"] == "short" and (ma_bull or ravi < ravi_exit)
            if exit_long or exit_short:
                pnl = self_pnl(position, close)
                equity += pnl
                reason = "ma_cross" if (ma_bear or ma_bull) else "ravi_exit"
                trades.append(make_trade(position, close, pnl, reason))
                position = None

        if position:
            position["bars_held"] += 1

        equity_curve.append(equity)

        if position:
            continue

        # ── Entry conditions ───────────────────────────────────
        trend_ok = ravi > ravi_entry
        adx_ok   = adx is None or adx > adx_min

        if not (trend_ok and adx_ok):
            continue

        # ATR-based lot sizing (Gliner Ch.2)
        lots = atr_lot_size(equity, atr, risk_pct=risk_pct, atr_mult=atr_mult)

        if ma_bull:
            # DXY filter: skip long if DXY rising (USD strengthening = Gold headwind)
            if use_dxy and dxy_direction == "rising":
                filtered_dxy += 1
                continue
            stop = close - atr * atr_mult
            position = {
                "side": "long", "entry": close, "stop": stop,
                "lots": lots, "bars_held": 0,
            }

        elif ma_bear:
            # DXY filter: skip short if DXY falling (USD weakening = Gold tailwind)
            if use_dxy and dxy_direction == "falling":
                filtered_dxy += 1
                continue
            stop = close + atr * atr_mult
            position = {
                "side": "short", "entry": close, "stop": stop,
                "lots": lots, "bars_held": 0,
            }

    # Close open position at end
    if position:
        close = bars[-1]["close"]
        pnl   = self_pnl(position, close)
        equity += pnl
        trades.append(make_trade(position, close, pnl, "end_of_data"))

    return _metrics(trades, equity, capital, equity_curve, bars, filtered_dxy)


def self_pnl(position: dict, exit_price: float) -> float:
    raw = (exit_price - position["entry"]) if position["side"] == "long" \
          else (position["entry"] - exit_price)
    return raw * position["lots"] * LOT_OZ


def make_trade(position: dict, exit_price: float, pnl: float, reason: str) -> dict:
    return {
        "side":        position["side"],
        "entry":       position["entry"],
        "exit":        exit_price,
        "lots":        position["lots"],
        "pnl_usd":     round(pnl, 2),
        "pnl_pips":    round(abs(exit_price - position["entry"]) / 0.01, 0),
        "bars_held":   position["bars_held"],
        "exit_reason": reason,
    }


def _metrics(trades, equity, capital, equity_curve, bars, filtered_dxy) -> dict:
    n      = len(trades)
    wins   = [t for t in trades if t["pnl_usd"] > 0]
    stops  = [t for t in trades if t.get("exit_reason") == "stop_loss"]
    ret    = (equity - capital) / capital * 100

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
            # Annualize based on bar count per year
            bars_per_year = len(bars) / max(1, (
                (bars[-1]["dt"] - bars[0]["dt"]).days / 365
            )) if len(bars) > 1 else 252
            sharpe = round((mean_r / std_r) * math.sqrt(bars_per_year), 3)

    max_dd = 0.0
    peak   = capital
    for e in equity_curve:
        peak   = max(peak, e)
        dd     = (peak - e) / peak * 100
        max_dd = max(max_dd, dd)

    avg_pips = sum(t["pnl_pips"] for t in trades) / n if n else 0
    avg_hold = sum(t["bars_held"] for t in trades) / n if n else 0
    avg_lots = sum(t["lots"] for t in trades) / n if n else 0

    return {
        "bars":           len(bars),
        "trades":         n,
        "win_rate":       round(len(wins) / n * 100, 1) if n else 0,
        "stop_rate":      round(len(stops) / n * 100, 1) if n else 0,
        "return_pct":     round(ret, 3),
        "sharpe":         sharpe,
        "max_dd_pct":     round(max_dd, 3),
        "avg_pips":       round(avg_pips, 0),
        "avg_hold_bars":  round(avg_hold, 1),
        "avg_lots":       round(avg_lots, 4),
        "equity_final":   round(equity, 2),
        "filtered_by_dxy": filtered_dxy,
        "equity_curve":   equity_curve,
    }


# ── Scramble Test ─────────────────────────────────────────────

def scramble_test(
    bars:       List[dict],
    dxy_closes: Optional[List[float]],
    n_trials:   int = 300,
    **kwargs
) -> dict:
    orig        = run_backtest(bars, dxy_closes, **kwargs)
    orig_sharpe = orig["sharpe"]
    beats       = 0
    bars_copy   = bars.copy()
    dxy_copy    = dxy_closes.copy() if dxy_closes else None

    for _ in range(n_trials):
        random.shuffle(bars_copy)
        if dxy_copy:
            random.shuffle(dxy_copy)
        r = run_backtest(bars_copy, dxy_copy, **kwargs)
        if r["sharpe"] >= orig_sharpe:
            beats += 1

    p_value = beats / n_trials
    return {
        "original_sharpe": orig_sharpe,
        "p_value":         round(p_value, 4),
        "is_robust":       p_value < 0.05,
        "verdict":         "ROBUST ✓" if p_value < 0.05 else "NOT ROBUST ✗",
    }


# ── Parameter Sweep ───────────────────────────────────────────

def sweep(bars: List[dict], dxy_closes: Optional[List[float]], use_dxy: bool) -> None:
    print(f"\n  Parameter Sweep — DXY filter: {'ON' if use_dxy else 'OFF'}")
    print(f"  {'Fast':>4} {'Slow':>4} {'Trades':>6} {'Win%':>6} "
          f"{'Sharpe':>7} {'Return%':>8} {'MaxDD%':>7}")
    print(f"  {'-'*50}")
    best = None
    for fast in [10, 20, 30]:
        for slow in [40, 50, 65, 100]:
            if fast >= slow:
                continue
            r = run_backtest(bars, dxy_closes, fast_period=fast,
                             slow_period=slow, use_dxy=use_dxy)
            if r["trades"] < 5:
                continue
            print(f"  {fast:>4} {slow:>4} {r['trades']:>6} "
                  f"{r['win_rate']:>6}% {r['sharpe']:>7} "
                  f"{r['return_pct']:>+8.3f}% {r['max_dd_pct']:>7.3f}%")
            if best is None or r["sharpe"] > best["sharpe"]:
                best = {"fast": fast, "slow": slow, **r}
    if best:
        print(f"\n  Best: fast={best['fast']}, slow={best['slow']}, "
              f"Sharpe={best['sharpe']}, Return={best['return_pct']:+.3f}%")


# ── CLI ───────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="AlphaBot XAUUSD Momentum Backtester")
    parser.add_argument("--days",       type=int,   default=365)
    parser.add_argument("--interval",   default="1day",
                        choices=["1h","2h","4h","8h","1day"])
    parser.add_argument("--fast",       type=int,   default=20)
    parser.add_argument("--slow",       type=int,   default=50)
    parser.add_argument("--ravi-entry", type=float, default=3.5)
    parser.add_argument("--ravi-exit",  type=float, default=1.5)
    parser.add_argument("--adx-min",    type=float, default=25.0)
    parser.add_argument("--atr-mult",   type=float, default=2.0)
    parser.add_argument("--risk-pct",   type=float, default=0.01)
    parser.add_argument("--no-dxy",     action="store_true",
                        help="Disable DXY macro filter (compare with/without)")
    parser.add_argument("--scramble",   action="store_true")
    parser.add_argument("--trials",     type=int,   default=300)
    parser.add_argument("--sweep",      action="store_true")
    args = parser.parse_args()

    use_dxy = not args.no_dxy

    print(f"\n{'='*62}")
    print(f"  AlphaBot XAUUSD Momentum — {args.days}d | {args.interval}")
    print(f"  MA({args.fast},{args.slow}) | ATR×{args.atr_mult} stop | "
          f"Risk {args.risk_pct*100:.1f}%/trade")
    print(f"  DXY macro filter: {'ON ✓' if use_dxy else 'OFF'}")
    print(f"{'='*62}\n")

    # Fetch XAU/USD
    print(f"Fetching XAU/USD {args.interval}...", end=" ", flush=True)
    bars_xau = await fetch_candles("XAU/USD", args.interval, args.days)
    print(f"{len(bars_xau)} bars")

    # Fetch DXY
    dxy_closes = None
    if use_dxy:
        print(f"Fetching DXY {args.interval}...", end=" ", flush=True)
        try:
            # DXY not on Twelve Data free tier — EUR/USD inverted is 57.6% DXY weight
            bars_dxy = await fetch_candles("EUR/USD", args.interval, args.days)
            # Invert EUR/USD to get USD direction (DXY proxy)
            for b in bars_dxy:
                b["close"] = 1.0 / b["close"]
            bars_xau, dxy_closes = align_series(bars_xau, bars_dxy)
            print(f"{len(bars_dxy)} bars → {len(bars_xau)} aligned")
        except Exception as e:
            print(f"FAILED ({e}) — running without DXY filter")
            use_dxy = False

    kwargs = {
        "fast_period": args.fast,
        "slow_period": args.slow,
        "ravi_entry":  args.ravi_entry,
        "ravi_exit":   args.ravi_exit,
        "adx_min":     args.adx_min,
        "atr_mult":    args.atr_mult,
        "risk_pct":    args.risk_pct,
        "use_dxy":     use_dxy,
    }

    if args.sweep:
        sweep(bars_xau, dxy_closes, use_dxy)
        return

    result = run_backtest(bars_xau, dxy_closes, **kwargs)

    print(f"\n── XAUUSD Momentum {'(+DXY filter)' if use_dxy else '(no DXY)'} ──")
    print(f"  Bars:            {result['bars']}")
    print(f"  Trades:          {result['trades']}")
    if use_dxy:
        print(f"  Filtered by DXY: {result['filtered_by_dxy']} trades skipped")
    print(f"  Win rate:        {result['win_rate']}%")
    print(f"  Stop rate:       {result['stop_rate']}%")
    print(f"  Return:          {result['return_pct']:+.3f}%")
    print(f"  Sharpe:          {result['sharpe']}")
    print(f"  Max DD:          {result['max_dd_pct']:.3f}%")
    print(f"  Avg pips/trade:  {result['avg_pips']:+.0f}")
    print(f"  Avg hold:        {result['avg_hold_bars']:.1f} bars")
    print(f"  Avg lots:        {result['avg_lots']:.4f}")
    print(f"  Final equity:    ${result['equity_final']:,.2f}")

    if args.scramble and result["trades"] >= 5:
        print(f"\n  Scrambling ({args.trials} trials)...", end=" ", flush=True)
        sc = scramble_test(bars_xau, dxy_closes, n_trials=args.trials, **kwargs)
        print(f"p={sc['p_value']} → {sc['verdict']}")

    # Also run WITHOUT DXY for comparison if DXY was used
    if use_dxy and result["trades"] >= 5:
        print(f"\n── Comparison: WITHOUT DXY filter ──")
        r2 = run_backtest(bars_xau, None, **{**kwargs, "use_dxy": False})
        print(f"  Trades: {r2['trades']} | Win: {r2['win_rate']}% | "
              f"Sharpe: {r2['sharpe']} | Return: {r2['return_pct']:+.3f}%")
        if result['trades'] > 0 and r2['trades'] > 0:
            sharpe_lift = result['sharpe'] - r2['sharpe']
            print(f"  DXY filter Sharpe lift: {sharpe_lift:+.3f}")

    print(f"\n{'='*62}\n")


if __name__ == "__main__":
    asyncio.run(main())
