
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..models import Candle


@dataclass
class FVG:
    direction: str          # "bullish" | "bearish"
    left_index: int         # candle 1 in the 3-candle pattern
    middle_index: int       # candle 2
    right_index: int        # candle 3
    top: float              # upper boundary of the gap
    bottom: float           # lower boundary of the gap
    midpoint: float         # consequent encroachment
    gap_size: float
    filled: bool = False
    fill_index: int = -1


def detect_fvgs(candles: List[Candle], min_gap: float = 0.0) -> List[FVG]:
    """
    Bullish FVG:
        candle3.low > candle1.high

    Bearish FVG:
        candle3.high < candle1.low
    """
    fvgs: List[FVG] = []

    if len(candles) < 3:
        return fvgs

    for i in range(2, len(candles)):
        a = candles[i - 2]
        b = candles[i - 1]
        c = candles[i]

        # Bullish FVG
        if c.low > a.high:
            bottom = a.high
            top = c.low
            gap = top - bottom
            if gap >= min_gap:
                fvgs.append(
                    FVG(
                        direction="bullish",
                        left_index=i - 2,
                        middle_index=i - 1,
                        right_index=i,
                        top=top,
                        bottom=bottom,
                        midpoint=(top + bottom) / 2.0,
                        gap_size=gap,
                    )
                )

        # Bearish FVG
        if c.high < a.low:
            top = a.low
            bottom = c.high
            gap = top - bottom
            if gap >= min_gap:
                fvgs.append(
                    FVG(
                        direction="bearish",
                        left_index=i - 2,
                        middle_index=i - 1,
                        right_index=i,
                        top=top,
                        bottom=bottom,
                        midpoint=(top + bottom) / 2.0,
                        gap_size=gap,
                    )
                )

    return fvgs


def mark_fvg_fills(candles: List[Candle], fvgs: List[FVG]) -> List[FVG]:
    """
    Marks whether later candles traded back into the gap.

    Bullish FVG is considered filled if later low <= top and high >= bottom.
    Bearish FVG is considered filled if later high >= bottom and low <= top.

    In both cases, any overlap into the gap marks it filled.
    """
    for fvg in fvgs:
        start = fvg.right_index + 1
        for i in range(start, len(candles)):
            c = candles[i]
            overlaps = c.high >= fvg.bottom and c.low <= fvg.top
            if overlaps:
                fvg.filled = True
                fvg.fill_index = i
                break
    return fvgs


def latest_fvg(
    candles: List[Candle],
    direction: Optional[str] = None,
    min_gap: float = 0.0,
    only_unfilled: bool = False,
) -> Optional[FVG]:
    fvgs = detect_fvgs(candles, min_gap=min_gap)
    fvgs = mark_fvg_fills(candles, fvgs)

    if direction is not None:
        fvgs = [x for x in fvgs if x.direction == direction]

    if only_unfilled:
        fvgs = [x for x in fvgs if not x.filled]

    if not fvgs:
        return None
    return fvgs[-1]


def fvgs_in_range(
    candles: List[Candle],
    start_index: int,
    end_index: int,
    direction: Optional[str] = None,
    min_gap: float = 0.0,
    only_unfilled: bool = False,
) -> List[FVG]:
    if start_index < 0:
        start_index = 0
    if end_index > len(candles):
        end_index = len(candles)

    subset = candles[start_index:end_index]
    fvgs = detect_fvgs(subset, min_gap=min_gap)
    fvgs = mark_fvg_fills(subset, fvgs)

    adjusted: List[FVG] = []
    for f in fvgs:
        adjusted.append(
            FVG(
                direction=f.direction,
                left_index=f.left_index + start_index,
                middle_index=f.middle_index + start_index,
                right_index=f.right_index + start_index,
                top=f.top,
                bottom=f.bottom,
                midpoint=f.midpoint,
                gap_size=f.gap_size,
                filled=f.filled,
                fill_index=(f.fill_index + start_index) if f.fill_index >= 0 else -1,
            )
        )

    if direction is not None:
        adjusted = [x for x in adjusted if x.direction == direction]

    if only_unfilled:
        adjusted = [x for x in adjusted if not x.filled]

    return adjusted


def price_in_fvg(price: float, fvg: FVG) -> bool:
    return fvg.bottom <= price <= fvg.top


def touch_consequent_encroachment(candle: Candle, fvg: FVG) -> bool:
    return candle.low <= fvg.midpoint <= candle.high
