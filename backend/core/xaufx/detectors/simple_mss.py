
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..models import Candle


@dataclass
class SimpleMSSSignal:
    direction: str          # "bullish" | "bearish" | "none"
    trigger_index: int
    break_level: float
    lookback_high: Optional[float]
    lookback_low: Optional[float]
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


def body_displacement(candles: List[Candle], idx: int, atr_period: int = 14) -> float:
    if idx <= 0 or idx >= len(candles):
        return 0.0
    a = atr(candles[: idx + 1], atr_period)
    if a <= 0:
        return 0.0
    body = abs(candles[idx].close - candles[idx].open)
    return body / a


def detect_simple_mss(
    candles: List[Candle],
    direction_hint: Optional[str] = None,
    lookback: int = 4,
    min_displacement: float = 0.0,
) -> SimpleMSSSignal:
    """
    Session-break MSS:
    bullish: last close > highest high of prior N bars
    bearish: last close < lowest low of prior N bars
    """
    if len(candles) < lookback + 2:
        return SimpleMSSSignal(
            direction="none",
            trigger_index=-1,
            break_level=0.0,
            lookback_high=None,
            lookback_low=None,
            displacement=0.0,
            reason="insufficient bars",
        )

    idx = len(candles) - 1
    last = candles[idx]
    prior = candles[max(0, idx - lookback): idx]

    if not prior:
        return SimpleMSSSignal(
            direction="none",
            trigger_index=-1,
            break_level=0.0,
            lookback_high=None,
            lookback_low=None,
            displacement=0.0,
            reason="no prior window",
        )

    hh = max(c.high for c in prior)
    ll = min(c.low for c in prior)
    disp = body_displacement(candles, idx)

    bullish = last.close > hh and disp >= min_displacement
    bearish = last.close < ll and disp >= min_displacement

    if direction_hint == "bullish":
        bullish = bullish
        bearish = False
    elif direction_hint == "bearish":
        bearish = bearish
        bullish = False

    if bullish:
        return SimpleMSSSignal(
            direction="bullish",
            trigger_index=idx,
            break_level=hh,
            lookback_high=hh,
            lookback_low=ll,
            displacement=disp,
            reason="close broke above prior lookback high",
        )

    if bearish:
        return SimpleMSSSignal(
            direction="bearish",
            trigger_index=idx,
            break_level=ll,
            lookback_high=hh,
            lookback_low=ll,
            displacement=disp,
            reason="close broke below prior lookback low",
        )

    return SimpleMSSSignal(
        direction="none",
        trigger_index=-1,
        break_level=0.0,
        lookback_high=hh,
        lookback_low=ll,
        displacement=disp,
        reason="no simple MSS",
    )
