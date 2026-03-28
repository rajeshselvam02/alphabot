from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from ..models import Candle


@dataclass
class NDOG:
    start_ts: datetime
    end_ts: datetime
    close_17: float
    open_18: float
    gap: float
    midpoint: float


def compute_ndog(candles: Iterable[Candle], tz_name: str = "America/New_York") -> Optional[NDOG]:
    tz = ZoneInfo(tz_name)
    rows = list(candles)
    if len(rows) < 2:
        return None

    prev = rows[-2]
    curr = rows[-1]

    prev_ny = prev.ts.astimezone(tz) if prev.ts.tzinfo else prev.ts.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    curr_ny = curr.ts.astimezone(tz) if curr.ts.tzinfo else curr.ts.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)

    if prev_ny.hour != 17 or curr_ny.hour != 18:
        return None

    gap = curr.open - prev.close
    midpoint = (curr.open + prev.close) / 2.0

    return NDOG(
        start_ts=prev_ny,
        end_ts=curr_ny,
        close_17=prev.close,
        open_18=curr.open,
        gap=gap,
        midpoint=midpoint,
    )
