
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

from ..models import Candle


@dataclass
class PreviousDayLevels:
    day_start: datetime
    day_end: datetime
    high: float
    low: float
    midpoint: float


@dataclass
class PreviousDaySweep:
    swept_pdh: bool
    swept_pdl: bool
    pdh_index: int
    pdl_index: int
    reason: str


def _local_day_bounds(ts: datetime, tz_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    local = ts.astimezone(tz)
    current_day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    prev_day_start = current_day_start - timedelta(days=1)
    prev_day_end = current_day_start
    return prev_day_start, prev_day_end


def previous_day_levels(candles: List[Candle], tz_name: str = "America/New_York") -> Optional[PreviousDayLevels]:
    if not candles:
        return None

    prev_day_start, prev_day_end = _local_day_bounds(candles[-1].ts, tz_name)
    tz = ZoneInfo(tz_name)

    bucket = []
    for c in candles:
        local = c.ts.astimezone(tz)
        if prev_day_start <= local < prev_day_end:
            bucket.append(c)

    if not bucket:
        return None

    high = max(c.high for c in bucket)
    low = min(c.low for c in bucket)

    return PreviousDayLevels(
        day_start=prev_day_start,
        day_end=prev_day_end,
        high=high,
        low=low,
        midpoint=(high + low) / 2.0,
    )


def detect_previous_day_sweep(
    candles: List[Candle],
    levels: PreviousDayLevels,
) -> PreviousDaySweep:
    pdh_index = -1
    pdl_index = -1

    for i, c in enumerate(candles):
        if pdh_index < 0 and c.high > levels.high:
            pdh_index = i
        if pdl_index < 0 and c.low < levels.low:
            pdl_index = i

    return PreviousDaySweep(
        swept_pdh=(pdh_index >= 0),
        swept_pdl=(pdl_index >= 0),
        pdh_index=pdh_index,
        pdl_index=pdl_index,
        reason=(
            "swept both previous-day extremes"
            if pdh_index >= 0 and pdl_index >= 0 else
            "swept PDH"
            if pdh_index >= 0 else
            "swept PDL"
            if pdl_index >= 0 else
            "no previous-day sweep"
        ),
    )


def near_level(price: float, level: float, tolerance: float) -> bool:
    return abs(price - level) <= tolerance
