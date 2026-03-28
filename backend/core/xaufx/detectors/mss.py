
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..models import Candle


@dataclass
class Pivot:
    index: int
    price: float
    kind: str  # "high" | "low"


@dataclass
class MSSSignal:
    direction: str          # "bullish" | "bearish" | "none"
    trigger_index: int
    break_level: float
    pivot_high: Optional[Pivot]
    pivot_low: Optional[Pivot]
    displacement: float
    reason: str


def true_range(curr: Candle, prev: Candle) -> float:
    return max(
        curr.high - curr.low,
        abs(curr.high - prev.close),
        abs(curr.low - prev.close),
    )


def atr(candles: List[Candle], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = [true_range(candles[i], candles[i - 1]) for i in range(1, len(candles))]
    return sum(trs[-period:]) / period


def find_pivot_highs(candles: List[Candle], left: int = 2, right: int = 2) -> List[Pivot]:
    pivots: List[Pivot] = []
    n = len(candles)
    for i in range(left, n - right):
        h = candles[i].high
        ok = True
        for j in range(i - left, i + right + 1):
            if j == i:
                continue
            if candles[j].high >= h:
                ok = False
                break
        if ok:
            pivots.append(Pivot(index=i, price=h, kind="high"))
    return pivots


def find_pivot_lows(candles: List[Candle], left: int = 2, right: int = 2) -> List[Pivot]:
    pivots: List[Pivot] = []
    n = len(candles)
    for i in range(left, n - right):
        l = candles[i].low
        ok = True
        for j in range(i - left, i + right + 1):
            if j == i:
                continue
            if candles[j].low <= l:
                ok = False
                break
        if ok:
            pivots.append(Pivot(index=i, price=l, kind="low"))
    return pivots


def latest_pivot_before(pivots: List[Pivot], idx: int) -> Optional[Pivot]:
    out = None
    for p in pivots:
        if p.index < idx:
            out = p
        else:
            break
    return out


def displacement_score(candles: List[Candle], idx: int, atr_period: int = 14) -> float:
    """
    Returns body size / ATR as a simple displacement measure.
    """
    if idx <= 0 or idx >= len(candles):
        return 0.0
    a = atr(candles[: idx + 1], atr_period)
    if a <= 0:
        return 0.0
    body = abs(candles[idx].close - candles[idx].open)
    return body / a


def detect_mss(
    candles: List[Candle],
    pivot_left: int = 2,
    pivot_right: int = 2,
    min_displacement: float = 0.0,
) -> MSSSignal:
    """
    Bullish MSS:
      - identify latest pivot high and pivot low before trigger
      - a later candle closes above latest pivot high
      - optional displacement filter

    Bearish MSS:
      - identify latest pivot low and pivot high before trigger
      - a later candle closes below latest pivot low
      - optional displacement filter
    """
    if len(candles) < max(8, pivot_left + pivot_right + 5):
        return MSSSignal(
            direction="none",
            trigger_index=-1,
            break_level=0.0,
            pivot_high=None,
            pivot_low=None,
            displacement=0.0,
            reason="insufficient bars",
        )

    highs = find_pivot_highs(candles, pivot_left, pivot_right)
    lows = find_pivot_lows(candles, pivot_left, pivot_right)

    if not highs or not lows:
        return MSSSignal(
            direction="none",
            trigger_index=-1,
            break_level=0.0,
            pivot_high=None,
            pivot_low=None,
            displacement=0.0,
            reason="no pivots",
        )

    # Scan from left to right, track most recent pivots, and detect first valid break
    last_high: Optional[Pivot] = None
    last_low: Optional[Pivot] = None

    high_map = {p.index: p for p in highs}
    low_map = {p.index: p for p in lows}

    for i in range(len(candles)):
        if i in high_map:
            last_high = high_map[i]
        if i in low_map:
            last_low = low_map[i]

        c = candles[i]
        disp = displacement_score(candles, i)

        if last_high is not None and last_low is not None:
            # bullish MSS: close breaks above last pivot high
            if i > last_high.index and c.close > last_high.price:
                if disp >= min_displacement:
                    return MSSSignal(
                        direction="bullish",
                        trigger_index=i,
                        break_level=last_high.price,
                        pivot_high=last_high,
                        pivot_low=last_low,
                        displacement=disp,
                        reason="close broke above pivot high",
                    )

            # bearish MSS: close breaks below last pivot low
            if i > last_low.index and c.close < last_low.price:
                if disp >= min_displacement:
                    return MSSSignal(
                        direction="bearish",
                        trigger_index=i,
                        break_level=last_low.price,
                        pivot_high=last_high,
                        pivot_low=last_low,
                        displacement=disp,
                        reason="close broke below pivot low",
                    )

    return MSSSignal(
        direction="none",
        trigger_index=-1,
        break_level=0.0,
        pivot_high=highs[-1] if highs else None,
        pivot_low=lows[-1] if lows else None,
        displacement=0.0,
        reason="no structure shift detected",
    )


def detect_recent_mss(
    candles: List[Candle],
    lookback: int = 20,
    pivot_left: int = 2,
    pivot_right: int = 2,
    min_displacement: float = 0.0,
) -> MSSSignal:
    """
    Restrict MSS detection to the last N candles for intraday execution logic.
    """
    if len(candles) <= lookback:
        subset = candles
        offset = 0
    else:
        subset = candles[-lookback:]
        offset = len(candles) - lookback

    sig = detect_mss(
        subset,
        pivot_left=pivot_left,
        pivot_right=pivot_right,
        min_displacement=min_displacement,
    )

    if sig.trigger_index >= 0:
        sig.trigger_index += offset
        if sig.pivot_high is not None:
            sig.pivot_high = Pivot(
                index=sig.pivot_high.index + offset,
                price=sig.pivot_high.price,
                kind=sig.pivot_high.kind,
            )
        if sig.pivot_low is not None:
            sig.pivot_low = Pivot(
                index=sig.pivot_low.index + offset,
                price=sig.pivot_low.price,
                kind=sig.pivot_low.kind,
            )
    return sig
