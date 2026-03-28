
from datetime import datetime, timedelta, timezone

from backend.core.xaufx.models import Candle
from backend.core.xaufx.detectors.previous_day_levels import previous_day_levels, detect_previous_day_sweep


def make_candles():
    base = datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc)
    candles = []

    # previous day
    rows_prev = [
        (100, 102,  99, 101),
        (101, 103, 100, 102),
        (102, 104, 101, 103),
        (103, 105, 102, 104),
    ]

    # current day
    rows_curr = [
        (104, 106, 103, 105),  # sweep PDH=105
        (105, 105.5, 98, 99),  # sweep PDL=99
    ]

    for i, (o, h, l, c) in enumerate(rows_prev):
        candles.append(Candle(ts=base - timedelta(days=1) + timedelta(hours=i*6), open=o, high=h, low=l, close=c, volume=0.0))
    for i, (o, h, l, c) in enumerate(rows_curr):
        candles.append(Candle(ts=base + timedelta(hours=i*6), open=o, high=h, low=l, close=c, volume=0.0))

    return candles


if __name__ == "__main__":
    candles = make_candles()
    levels = previous_day_levels(candles, tz_name="UTC")
    print("Levels:", levels)
    if levels:
        sweep = detect_previous_day_sweep(candles[-2:], levels)
        print("Sweep:", sweep)
