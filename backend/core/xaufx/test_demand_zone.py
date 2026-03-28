
from datetime import datetime, timedelta, timezone

from backend.core.xaufx.models import Candle
from backend.core.xaufx.detectors.demand_zone import detect_recent_demand_zone


if __name__ == "__main__":
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        (100, 102,  99, 101),
        (101, 103, 100, 102),
        (102, 103, 101, 102.2),   # base
        (102.1, 110, 102, 109),   # impulse
        (109, 111, 108, 110),
    ]

    candles = []
    for i, (o, h, l, c) in enumerate(rows):
        candles.append(Candle(
            ts=base + timedelta(days=i),
            open=o, high=h, low=l, close=c, volume=0.0
        ))

    zone = detect_recent_demand_zone(candles)
    print(zone)
