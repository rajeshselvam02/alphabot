"""
XAUUSD ICT Multi-Strategy Backtester
Tests all automatable ICT concepts and ranks by Sharpe + robustness.

Strategies tested:
  1. NDOG Gap Fill     — Daily 5PM/6PM NY gap, trade toward fill
  2. NWOG Gap Fill     — Weekly Friday/Monday NY gap, higher significance
  3. NY Killzone FVG   — 08:30-11:00 AM NY, enter at FVG in premium/discount
  4. PD Array FVG      — Session swing P/D zone filter + FVG CE entry
  5. Asian NDOG        — 06:00-09:00 PM NY, post-7PM algo activation

All times in UTC. NY offset:
  EST (Nov-Mar): UTC-5  → 5PM NY = 22:00 UTC, 6PM NY = 23:00 UTC
  EDT (Mar-Nov): UTC-4  → 5PM NY = 21:00 UTC, 6PM NY = 22:00 UTC

Usage:
    python -m backend.backtester.backtest_xau_ict --days 180 --scramble --trials 300
    python -m backend.backtester.backtest_xau_ict --days 180 --strategy ndog
    python -m backend.backtester.backtest_xau_ict --days 365 --all --scramble
"""
import asyncio
import argparse
import aiohttp
import logging
import math
import random
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple
from backend.config.settings import settings

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("alphabot.backtest_xau_ict")

BASE_URL = "https://api.twelvedata.com"
CAPITAL  = 10_000.0
LOTS     = 0.01        # 0.01 lot = 1 oz XAU per pip
PIP      = 0.01        # $0.01 per oz


# ── Data Fetch ─────────────────────────────────────────────────

async def fetch_candles(days: int, interval: str = "1h") -> List[dict]:
    bars_needed = min(days * 24 + 100, 5000)
    params = {
        "symbol":     "XAU/USD",
        "interval":   interval,
        "outputsize": bars_needed,
        "apikey":     settings.TWELVEDATA_API_KEY,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{BASE_URL}/time_series",
            params=params,
            timeout=aiohttp.ClientTimeout(total=30)
        ) as r:
            data = await r.json()

    if data.get("status") == "error":
        raise RuntimeError(f"API error: {data.get('message')}")

    values = data.get("values", [])
    if not values:
        raise RuntimeError("No data returned")

    values.reverse()
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
                "volume": float(v.get("volume") or 1.0),
            })
        except (KeyError, ValueError):
            continue
    return bars


# ── Time Helpers ───────────────────────────────────────────────

def is_dst(dt: datetime) -> bool:
    """Rough US DST check: 2nd Sunday March → 1st Sunday November."""
    month = dt.month
    if month < 3 or month > 11: return False
    if month > 3 and month < 11: return True
    day = dt.day
    dow = dt.weekday()  # 0=Mon, 6=Sun
    if month == 3:
        # 2nd Sunday in March
        second_sunday = 8 + (6 - datetime(dt.year, 3, 1).weekday()) % 7
        return day >= second_sunday
    if month == 11:
        # 1st Sunday in November
        first_sunday = 1 + (6 - datetime(dt.year, 11, 1).weekday()) % 7
        return day < first_sunday
    return False

def ny_hour(dt: datetime) -> float:
    """Get NY local hour (float) from UTC datetime."""
    offset = -4 if is_dst(dt) else -5
    ny = dt + timedelta(hours=offset)
    return ny.hour + ny.minute / 60.0

def ny_close_utc(dt: datetime) -> datetime:
    """5:00 PM NY in UTC for the given date."""
    offset = -4 if is_dst(dt) else -5
    # 5PM NY = 17:00 NY = (17 - offset) UTC
    utc_hour = 17 - offset  # 21 EDT or 22 EST
    d = dt.replace(hour=utc_hour, minute=0, second=0, microsecond=0)
    return d

def ny_open_utc(dt: datetime) -> datetime:
    """6:00 PM NY in UTC for the given date."""
    offset = -4 if is_dst(dt) else -5
    utc_hour = 18 - offset  # 22 EDT or 23 EST
    d = dt.replace(hour=utc_hour, minute=0, second=0, microsecond=0)
    return d

def is_weekend(dt: datetime) -> bool:
    w = dt.weekday()
    return w == 5 or (w == 6 and ny_hour(dt) < 18)


# ── ICT Indicators ─────────────────────────────────────────────

def detect_fvg(bars: List[dict], idx: int) -> Optional[dict]:
    """
    Fair Value Gap: 3-candle formation where gap exists between
    candle[i-2] wick and candle[i] wick.
    Bullish FVG: candle[i].low > candle[i-2].high
    Bearish FVG: candle[i].high < candle[i-2].low
    """
    if idx < 2:
        return None
    b0 = bars[idx - 2]
    b2 = bars[idx]

    if b2["low"] > b0["high"]:
        # Bullish FVG — gap above b0, below b2
        return {
            "type":   "bullish",
            "top":    b2["low"],
            "bottom": b0["high"],
            "ce":     (b2["low"] + b0["high"]) / 2,
            "idx":    idx,
        }
    if b2["high"] < b0["low"]:
        # Bearish FVG — gap below b0, above b2
        return {
            "type":   "bearish",
            "top":    b0["low"],
            "bottom": b2["high"],
            "ce":     (b0["low"] + b2["high"]) / 2,
            "idx":    idx,
        }
    return None


def swing_high(bars: List[dict], lookback: int = 10) -> float:
    return max(b["high"] for b in bars[-lookback:])

def swing_low(bars: List[dict], lookback: int = 10) -> float:
    return min(b["low"] for b in bars[-lookback:])

def premium_discount(price: float, high: float, low: float) -> str:
    """Premium = above 50% of swing range, Discount = below."""
    mid = (high + low) / 2
    return "premium" if price > mid else "discount"

def compute_atr(bars: List[dict], period: int = 14) -> float:
    if len(bars) < period + 1:
        return bars[-1]["high"] - bars[-1]["low"]
    trs = []
    for i in range(1, period + 1):
        b = bars[-i]
        prev = bars[-i-1]["close"]
        trs.append(max(b["high"] - b["low"],
                       abs(b["high"] - prev),
                       abs(b["low"] - prev)))
    return sum(trs) / len(trs)


# ── PnL Helper ─────────────────────────────────────────────────

def pnl_usd(entry: float, exit_: float, side: str, lots: float = LOTS) -> float:
    """XAU/USD: 1 standard lot = 100 oz. 0.01 lot = 1 oz."""
    oz = lots * 100
    if side == "long":
        return (exit_ - entry) * oz
    else:
        return (entry - exit_) * oz


# ── Metrics ────────────────────────────────────────────────────

def compute_metrics(trades: List[dict], equity_curve: List[float], capital: float) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": 0, "return_pct": 0,
                "sharpe": 0, "max_dd_pct": 0, "equity_final": capital}

    wins = [t for t in trades if t["pnl"] > 0]
    total_return = (equity_curve[-1] - capital) / capital * 100

    returns = [(equity_curve[i] - equity_curve[i-1]) / equity_curve[i-1]
               for i in range(1, len(equity_curve))]

    sharpe = 0.0
    if returns:
        mean_r = sum(returns) / len(returns)
        std_r  = (sum((r - mean_r)**2 for r in returns) / len(returns)) ** 0.5
        if std_r > 0:
            sharpe = round((mean_r / std_r) * math.sqrt(24 * 252), 3)

    peak = capital
    max_dd = 0.0
    for e in equity_curve:
        peak = max(peak, e)
        max_dd = max(max_dd, (peak - e) / peak * 100)

    return {
        "trades":       n,
        "win_rate":     round(len(wins) / n * 100, 1),
        "return_pct":   round(total_return, 3),
        "sharpe":       sharpe,
        "max_dd_pct":   round(max_dd, 3),
        "equity_final": round(equity_curve[-1], 2),
    }


def scramble_test(bars: List[dict], strategy_fn, n_trials: int = 300, **kwargs) -> dict:
    original    = strategy_fn(bars, **kwargs)
    orig_sharpe = original["sharpe"]
    bar_copy    = bars.copy()
    scrambled   = []
    for _ in range(n_trials):
        random.shuffle(bar_copy)
        r = strategy_fn(bar_copy, **kwargs)
        scrambled.append(r["sharpe"])
    beats   = sum(1 for s in scrambled if s >= orig_sharpe)
    p_value = beats / n_trials
    return {
        "original_sharpe": orig_sharpe,
        "p_value":         round(p_value, 4),
        "is_robust":       p_value < 0.05,
        "verdict":         "ROBUST ✓" if p_value < 0.05 else "NOT ROBUST ✗",
    }


# ══════════════════════════════════════════════════════════════
# STRATEGY 1: NDOG Gap Fill
# ══════════════════════════════════════════════════════════════

def run_ndog(
    bars: List[dict],
    min_gap_pips: float = 20.0,
    atr_stop: float     = 2.0,
    capital: float      = CAPITAL,
) -> dict:
    """
    New Day Opening Gap fill strategy.
    Gap = 5PM NY close vs 6PM NY open.
    Trade: if price is above gap → short toward fill; below → long toward fill.
    Exit: gap midpoint (CE) or ATR stop.
    """
    equity       = capital
    equity_curve = [capital]
    trades       = []
    position     = None

    # Build daily gap levels
    daily_gaps: Dict[str, dict] = {}  # date_str → {high, low, ce, direction}

    for i, bar in enumerate(bars):
        dt  = bar["dt"]
        h   = ny_hour(dt)

        # Detect 5PM close bar and store price
        if 16.9 <= h <= 17.1:
            key = dt.date().isoformat()
            daily_gaps[key] = {"close_5pm": bar["close"], "dt_close": dt}

        # Detect 6PM open bar and form gap
        if 17.9 <= h <= 18.1:
            key = dt.date().isoformat()
            if key in daily_gaps and "close_5pm" in daily_gaps[key]:
                close_5pm = daily_gaps[key]["close_5pm"]
                open_6pm  = bar["open"]
                gap_size  = abs(open_6pm - close_5pm) / PIP
                if gap_size >= min_gap_pips:
                    high = max(close_5pm, open_6pm)
                    low  = min(close_5pm, open_6pm)
                    daily_gaps[key].update({
                        "high": high, "low": low,
                        "ce":   (high + low) / 2,
                        "gap_size": gap_size,
                        "open_6pm": open_6pm,
                        "filled":   False,
                    })

        close = bar["close"]

        # Close position
        if position:
            atr  = compute_atr(bars[:i+1])
            stop = position["stop"]
            tgt  = position["target"]
            stop_hit   = (position["side"] == "long"  and close < stop) or \
                         (position["side"] == "short" and close > stop)
            target_hit = (position["side"] == "long"  and close >= tgt) or \
                         (position["side"] == "short" and close <= tgt)

            if stop_hit or target_hit:
                p = pnl_usd(position["entry"], close, position["side"])
                equity += p
                trades.append({"pnl": p, "exit_reason": "target" if target_hit else "stop"})
                position = None

        if position:
            pass

        equity_curve.append(equity)

        # Skip weekends
        if is_weekend(dt):
            continue

        # Look for entry using today's gap
        if not position:
            key = dt.date().isoformat()
            gap = daily_gaps.get(key)
            if gap and "ce" in gap and not gap.get("filled"):
                atr = compute_atr(bars[:i+1])
                # Price below gap → long toward CE
                if close < gap["low"]:
                    position = {
                        "side":   "long",
                        "entry":  close,
                        "target": gap["ce"],
                        "stop":   close - atr_stop * atr,
                    }
                # Price above gap → short toward CE
                elif close > gap["high"]:
                    position = {
                        "side":   "short",
                        "entry":  close,
                        "target": gap["ce"],
                        "stop":   close + atr_stop * atr,
                    }

    # Close open position
    if position and bars:
        p = pnl_usd(position["entry"], bars[-1]["close"], position["side"])
        equity += p
        trades.append({"pnl": p, "exit_reason": "end"})

    return compute_metrics(trades, equity_curve, capital)


# ══════════════════════════════════════════════════════════════
# STRATEGY 2: NWOG Gap Fill
# ══════════════════════════════════════════════════════════════

def run_nwog(
    bars: List[dict],
    min_gap_pips: float = 50.0,
    atr_stop: float     = 2.5,
    capital: float      = CAPITAL,
) -> dict:
    """
    New Week Opening Gap fill strategy.
    Gap = Friday 5PM NY close vs Monday 6PM NY open.
    Higher threshold — weekly gaps are more significant.
    """
    equity       = capital
    equity_curve = [capital]
    trades       = []
    position     = None
    weekly_gaps  = {}  # week_key → gap dict

    for i, bar in enumerate(bars):
        dt = bar["dt"]
        h  = ny_hour(dt)
        wd = dt.weekday()  # 0=Mon, 4=Fri, 6=Sun

        # Friday 5PM close
        if wd == 4 and 16.9 <= h <= 17.1:
            week_key = f"{dt.isocalendar()[0]}-{dt.isocalendar()[1]}"
            weekly_gaps[week_key] = {"fri_close": bar["close"]}

        # Monday 6PM open (or Sunday evening resumption)
        if wd == 0 and 17.9 <= h <= 18.1:
            # Find last week's Friday close
            prev_iso = dt.isocalendar()
            week_key = f"{prev_iso[0]}-{prev_iso[1]}"
            if week_key in weekly_gaps and "fri_close" in weekly_gaps[week_key]:
                fri_close = weekly_gaps[week_key]["fri_close"]
                mon_open  = bar["open"]
                gap_size  = abs(mon_open - fri_close) / PIP
                if gap_size >= min_gap_pips:
                    high = max(fri_close, mon_open)
                    low  = min(fri_close, mon_open)
                    weekly_gaps[week_key].update({
                        "high": high, "low": low,
                        "ce":   (high + low) / 2,
                        "gap_size": gap_size,
                        "filled": False,
                        "week_start": dt,
                    })

        close = bar["close"]

        # Close position
        if position:
            stop_hit   = (position["side"] == "long"  and close < position["stop"]) or \
                         (position["side"] == "short" and close > position["stop"])
            target_hit = (position["side"] == "long"  and close >= position["target"]) or \
                         (position["side"] == "short" and close <= position["target"])
            if stop_hit or target_hit:
                p = pnl_usd(position["entry"], close, position["side"])
                equity += p
                trades.append({"pnl": p, "exit_reason": "target" if target_hit else "stop"})
                position = None

        equity_curve.append(equity)

        if is_weekend(dt) or position:
            continue

        # Find active weekly gap
        iso = dt.isocalendar()
        week_key = f"{iso[0]}-{iso[1]}"
        gap = weekly_gaps.get(week_key)
        if gap and "ce" in gap and not gap.get("filled"):
            atr = compute_atr(bars[:i+1])
            if close < gap["low"]:
                position = {
                    "side":   "long",
                    "entry":  close,
                    "target": gap["ce"],
                    "stop":   close - atr_stop * atr,
                }
            elif close > gap["high"]:
                position = {
                    "side":   "short",
                    "entry":  close,
                    "target": gap["ce"],
                    "stop":   close + atr_stop * atr,
                }

    if position and bars:
        p = pnl_usd(position["entry"], bars[-1]["close"], position["side"])
        equity += p
        trades.append({"pnl": p, "exit_reason": "end"})

    return compute_metrics(trades, equity_curve, capital)


# ══════════════════════════════════════════════════════════════
# STRATEGY 3: NY Killzone FVG
# ══════════════════════════════════════════════════════════════

def run_ny_killzone_fvg(
    bars: List[dict],
    atr_stop: float  = 1.5,
    rr_target: float = 2.0,
    capital: float   = CAPITAL,
) -> dict:
    """
    NY Killzone (08:30-11:00 AM NY) Fair Value Gap entry.
    - Detect FVG on 1h bars during killzone
    - Only enter in discount zone (bullish FVG) or premium (bearish FVG)
    - Entry at FVG CE (50% of gap)
    - Target: R:R based on ATR
    - Stop: beyond FVG extreme + ATR buffer
    """
    equity       = capital
    equity_curve = [capital]
    trades       = []
    position     = None
    pending_fvg  = None  # FVG waiting for price to return to CE

    for i, bar in enumerate(bars):
        dt    = bar["dt"]
        h     = ny_hour(dt)
        close = bar["close"]
        low   = bar["low"]
        high  = bar["high"]

        # NY Killzone: 08:30 AM to 11:00 AM NY
        in_killzone = 8.4 <= h <= 11.0

        # Close position
        if position:
            stop_hit   = (position["side"] == "long"  and low  < position["stop"]) or \
                         (position["side"] == "short" and high > position["stop"])
            target_hit = (position["side"] == "long"  and high >= position["target"]) or \
                         (position["side"] == "short" and low  <= position["target"])
            if stop_hit or target_hit:
                exit_price = position["target"] if target_hit else position["stop"]
                p = pnl_usd(position["entry"], exit_price, position["side"])
                equity += p
                trades.append({"pnl": p, "exit_reason": "target" if target_hit else "stop"})
                position = None
                pending_fvg = None

        equity_curve.append(equity)

        if is_weekend(dt) or position or i < 20:
            continue

        # Detect FVG during killzone
        if in_killzone:
            fvg = detect_fvg(bars, i)
            if fvg:
                # Determine premium/discount context
                sh = swing_high(bars[:i+1], lookback=20)
                sl = swing_low(bars[:i+1],  lookback=20)
                pd = premium_discount(close, sh, sl)

                # Only trade FVG aligned with P/D
                if fvg["type"] == "bullish" and pd == "discount":
                    pending_fvg = fvg
                    pending_fvg["trade_side"] = "long"
                elif fvg["type"] == "bearish" and pd == "premium":
                    pending_fvg = fvg
                    pending_fvg["trade_side"] = "short"

        # Try to enter at FVG CE
        if pending_fvg and not position and in_killzone:
            ce   = pending_fvg["ce"]
            atr  = compute_atr(bars[:i+1])
            side = pending_fvg["trade_side"]

            # Price has returned to CE level
            if side == "long"  and low <= ce <= high:
                stop   = pending_fvg["bottom"] - 0.5 * atr
                target = ce + rr_target * (ce - stop)
                position = {"side": "long",  "entry": ce, "stop": stop, "target": target}
                pending_fvg = None
            elif side == "short" and low <= ce <= high:
                stop   = pending_fvg["top"] + 0.5 * atr
                target = ce - rr_target * (stop - ce)
                position = {"side": "short", "entry": ce, "stop": stop, "target": target}
                pending_fvg = None

        # Cancel stale FVG if out of killzone
        if not in_killzone and pending_fvg:
            pending_fvg = None

    if position and bars:
        p = pnl_usd(position["entry"], bars[-1]["close"], position["side"])
        equity += p
        trades.append({"pnl": p, "exit_reason": "end"})

    return compute_metrics(trades, equity_curve, capital)


# ══════════════════════════════════════════════════════════════
# STRATEGY 4: PD Array FVG (Premium/Discount + FVG)
# ══════════════════════════════════════════════════════════════

def run_pd_fvg(
    bars: List[dict],
    atr_stop: float  = 1.5,
    rr_target: float = 2.0,
    lookback: int    = 24,
    capital: float   = CAPITAL,
) -> dict:
    """
    Session-wide Premium/Discount filter + FVG entry.
    No time filter — uses P/D context from session swing.
    Enter bullish FVG only in discount, bearish only in premium.
    """
    equity       = capital
    equity_curve = [capital]
    trades       = []
    position     = None
    pending_fvg  = None

    for i, bar in enumerate(bars):
        dt    = bar["dt"]
        close = bar["close"]
        low   = bar["low"]
        high  = bar["high"]

        # Close position
        if position:
            stop_hit   = (position["side"] == "long"  and low  < position["stop"]) or \
                         (position["side"] == "short" and high > position["stop"])
            target_hit = (position["side"] == "long"  and high >= position["target"]) or \
                         (position["side"] == "short" and low  <= position["target"])
            if stop_hit or target_hit:
                exit_price = position["target"] if target_hit else position["stop"]
                p = pnl_usd(position["entry"], exit_price, position["side"])
                equity += p
                trades.append({"pnl": p, "exit_reason": "target" if target_hit else "stop"})
                position = None
                pending_fvg = None

        equity_curve.append(equity)

        if is_weekend(dt) or position or i < lookback:
            continue

        # Session context
        sh = swing_high(bars[:i+1], lookback)
        sl = swing_low(bars[:i+1],  lookback)
        pd = premium_discount(close, sh, sl)

        # Detect FVG
        fvg = detect_fvg(bars, i)
        if fvg:
            if fvg["type"] == "bullish" and pd == "discount":
                pending_fvg = {**fvg, "trade_side": "long"}
            elif fvg["type"] == "bearish" and pd == "premium":
                pending_fvg = {**fvg, "trade_side": "short"}

        # Entry at CE
        if pending_fvg and not position:
            ce   = pending_fvg["ce"]
            atr  = compute_atr(bars[:i+1])
            side = pending_fvg["trade_side"]
            # Expire after 4 bars
            if i - pending_fvg["idx"] > 4:
                pending_fvg = None
            elif side == "long" and low <= ce <= high:
                stop   = pending_fvg["bottom"] - 0.5 * atr
                target = ce + rr_target * (ce - stop)
                position = {"side": "long",  "entry": ce, "stop": stop, "target": target}
                pending_fvg = None
            elif side == "short" and low <= ce <= high:
                stop   = pending_fvg["top"] + 0.5 * atr
                target = ce - rr_target * (stop - ce)
                position = {"side": "short", "entry": ce, "stop": stop, "target": target}
                pending_fvg = None

    if position and bars:
        p = pnl_usd(position["entry"], bars[-1]["close"], position["side"])
        equity += p
        trades.append({"pnl": p, "exit_reason": "end"})

    return compute_metrics(trades, equity_curve, capital)


# ══════════════════════════════════════════════════════════════
# STRATEGY 5: Asian Session NDOG (Lecture 5)
# ══════════════════════════════════════════════════════════════

def run_asian_ndog(
    bars: List[dict],
    min_gap_pips: float = 20.0,
    atr_stop: float     = 1.5,
    rr_target: float    = 2.0,
    capital: float      = CAPITAL,
) -> dict:
    """
    Asian Session NDOG model (ICT Lecture 5).
    - Mark NDOG at 6PM NY open
    - Wait for 7PM algorithm activation
    - After 7PM: if price closes above NDOG → look for bullish setup
                 if price closes below NDOG → look for bearish setup
    - Trade window: 7PM - 9PM NY
    - Target: initial buy/sell-side liquidity (swing high/low)
    """
    equity       = capital
    equity_curve = [capital]
    trades       = []
    position     = None

    # Track daily NDOG and 7PM bias
    daily_state: Dict[str, dict] = {}

    for i, bar in enumerate(bars):
        dt    = bar["dt"]
        h     = ny_hour(dt)
        close = bar["close"]
        low   = bar["low"]
        high  = bar["high"]

        key = dt.date().isoformat()

        # 5PM NY close
        if 16.9 <= h <= 17.1:
            daily_state[key] = {"close_5pm": bar["close"]}

        # 6PM NY open — form NDOG
        if 17.9 <= h <= 18.1:
            if key in daily_state and "close_5pm" in daily_state[key]:
                c5 = daily_state[key]["close_5pm"]
                o6 = bar["open"]
                gap_pips = abs(o6 - c5) / PIP
                if gap_pips >= min_gap_pips:
                    ndog_high = max(c5, o6)
                    ndog_low  = min(c5, o6)
                    daily_state[key].update({
                        "ndog_high": ndog_high,
                        "ndog_low":  ndog_low,
                        "ndog_ce":   (ndog_high + ndog_low) / 2,
                        "gap_pips":  gap_pips,
                        "bias":      None,  # set after 7PM
                    })

        # 7PM NY — algorithm activation, set bias
        if 18.9 <= h <= 19.1:
            if key in daily_state and "ndog_high" in daily_state[key]:
                ndog = daily_state[key]
                if close > ndog["ndog_high"]:
                    daily_state[key]["bias"] = "bullish"
                elif close < ndog["ndog_low"]:
                    daily_state[key]["bias"] = "bearish"

        # Close position
        if position:
            stop_hit   = (position["side"] == "long"  and low  < position["stop"]) or \
                         (position["side"] == "short" and high > position["stop"])
            target_hit = (position["side"] == "long"  and high >= position["target"]) or \
                         (position["side"] == "short" and low  <= position["target"])
            # Also close if out of window (past 9PM NY)
            out_of_window = h > 21.0
            if stop_hit or target_hit or out_of_window:
                exit_p = position["target"] if target_hit else \
                         (position["stop"] if stop_hit else close)
                p = pnl_usd(position["entry"], exit_p, position["side"])
                equity += p
                trades.append({
                    "pnl": p,
                    "exit_reason": "target" if target_hit else
                                   ("stop" if stop_hit else "timeout")
                })
                position = None

        equity_curve.append(equity)

        # Trade window: 7PM-9PM NY
        in_window = 19.0 <= h <= 21.0
        if not in_window or position or is_weekend(dt):
            continue

        state = daily_state.get(key)
        if not state or state.get("bias") is None:
            continue

        bias = state["bias"]
        atr  = compute_atr(bars[:i+1])

        # Swing liquidity targets
        sh = swing_high(bars[:i+1], lookback=8)
        sl = swing_low(bars[:i+1],  lookback=8)

        if bias == "bullish" and close > state["ndog_high"]:
            # Enter long after NDOG confirmed bullish
            stop   = state["ndog_low"] - 0.5 * atr
            target = sh  # initial buyside liquidity
            if target > close:  # only if target is above entry
                position = {"side": "long", "entry": close,
                            "stop": stop, "target": target}

        elif bias == "bearish" and close < state["ndog_low"]:
            stop   = state["ndog_high"] + 0.5 * atr
            target = sl  # initial sellside liquidity
            if target < close:
                position = {"side": "short", "entry": close,
                            "stop": stop, "target": target}

    if position and bars:
        p = pnl_usd(position["entry"], bars[-1]["close"], position["side"])
        equity += p
        trades.append({"pnl": p, "exit_reason": "end"})

    return compute_metrics(trades, equity_curve, capital)


# ── Strategy Registry ──────────────────────────────────────────

STRATEGIES = {
    "ndog":        ("NDOG Gap Fill",          run_ndog),
    "nwog":        ("NWOG Gap Fill",           run_nwog),
    "ny_killzone": ("NY Killzone FVG",         run_ny_killzone_fvg),
    "pd_fvg":      ("PD Array FVG",            run_pd_fvg),
    "asian_ndog":  ("Asian Session NDOG",      run_asian_ndog),
}


# ── CLI ────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="ICT XAUUSD Multi-Strategy Backtester")
    parser.add_argument("--days",     type=int, default=180)
    parser.add_argument("--strategy", type=str, default="all",
                        choices=["all"] + list(STRATEGIES.keys()))
    parser.add_argument("--scramble", action="store_true")
    parser.add_argument("--trials",   type=int, default=300)
    args = parser.parse_args()

    print(f"\nFetching XAUUSD 1h bars ({args.days} days)...")
    bars = await fetch_candles(args.days, "1h")
    print(f"Loaded {len(bars)} bars\n")

    to_run = STRATEGIES.items() if args.strategy == "all" else \
             [(args.strategy, STRATEGIES[args.strategy])]

    results = []
    for key, (name, fn) in to_run:
        print(f"── {name} {'─' * (45 - len(name))}")
        r = fn(bars)
        print(f"  Trades:   {r['trades']}")
        print(f"  Win rate: {r['win_rate']}%")
        print(f"  Return:   {r['return_pct']}%")
        print(f"  Sharpe:   {r['sharpe']}")
        print(f"  Max DD:   {r['max_dd_pct']}%")
        print(f"  Equity:   ${r['equity_final']:,.2f}")

        if args.scramble and r["trades"] >= 5:
            print(f"  Scrambling ({args.trials} trials)...")
            sc = scramble_test(bars, fn, n_trials=args.trials)
            print(f"  P-value:  {sc['p_value']} | {sc['verdict']}")
            r["scramble"] = sc
        else:
            r["scramble"] = None

        r["name"] = name
        r["key"]  = key
        results.append(r)
        print()

    if args.strategy == "all":
        print("══ RANKING (by Sharpe) ══════════════════════════════")
        ranked = sorted(results, key=lambda x: x["sharpe"], reverse=True)
        for rank, r in enumerate(ranked, 1):
            robust = ""
            if r["scramble"]:
                robust = f" | p={r['scramble']['p_value']} {r['scramble']['verdict']}"
            print(f"  {rank}. {r['name']:<30} Sharpe={r['sharpe']:>7} "
                  f"WR={r['win_rate']}% Trades={r['trades']}{robust}")


if __name__ == "__main__":
    asyncio.run(main())
