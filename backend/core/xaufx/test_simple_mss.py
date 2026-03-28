
from datetime import datetime, timedelta, timezone

from backend.core.xaufx.models import Candle
from backend.core.xaufx.detectors.simple_mss import detect_simple_mss


def make_bullish():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        (100.0, 101.0,  99.0, 100.2),
        (100.2, 101.2,  99.8, 100.6),
        (100.6, 101.4, 100.0, 100.8),
        (100.8, 101.5, 100.3, 101.0),
        (101.0, 103.5, 100.9, 103.2),  # break above prior highs
    ]
    candles = []
    for i, (o, h, l, c) in enumerate(rows):
        candles.append(Candle(ts=base + timedelta(minutes=15*i), open=o, high=h, low=l, close=c, volume=0.0))
    return candles


def make_bearish():
    base = datetime(2026, 1, 2, tzinfo=timezone.utc)
    rows = [
        (110.0, 111.0, 109.3, 110.6),
        (110.6, 111.2, 109.8, 110.4),
        (110.4, 110.9, 109.7, 110.1),
        (110.1, 110.5, 109.5, 109.9),
        (109.9, 110.0, 107.1, 107.4),  # break below prior lows
    ]
    candles = []
    for i, (o, h, l, c) in enumerate(rows):
        candles.append(Candle(ts=base + timedelta(minutes=15*i), open=o, high=h, low=l, close=c, volume=0.0))
    return candles


if __name__ == "__main__":
    bull = detect_simple_mss(make_bullish(), direction_hint="bullish", lookback=3, min_displacement=0.0)
    bear = detect_simple_mss(make_bearish(), direction_hint="bearish", lookback=3, min_displacement=0.0)
    print("Bullish:", bull)
    print("Bearish:", bear)
