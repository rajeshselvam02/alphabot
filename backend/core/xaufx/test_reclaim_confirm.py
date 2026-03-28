
from datetime import datetime, timedelta, timezone

from backend.core.xaufx.models import Candle
from backend.core.xaufx.sessions.ndog import NDOG
from backend.core.xaufx.detectors.reclaim_confirm import detect_reclaim_confirm


def make_bullish():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ndog = NDOG(
        start_ts=base,
        end_ts=base + timedelta(hours=1),
        close_17=100.0,
        open_18=101.0,
        gap=1.0,
        midpoint=100.5,
    )
    rows = [
        (101.0, 101.3,  99.4, 100.2),  # sweep below lower=100.0
        (100.2, 100.9, 100.1, 100.4),  # reclaim above lower
        (100.4, 101.4, 100.3, 101.1),  # confirm above reclaim high
    ]
    candles = []
    for i, (o, h, l, c) in enumerate(rows):
        candles.append(Candle(ts=base + timedelta(minutes=15*i), open=o, high=h, low=l, close=c, volume=0.0))
    return ndog, candles


def make_bearish():
    base = datetime(2026, 1, 2, tzinfo=timezone.utc)
    ndog = NDOG(
        start_ts=base,
        end_ts=base + timedelta(hours=1),
        close_17=100.0,
        open_18=101.0,
        gap=1.0,
        midpoint=100.5,
    )
    rows = [
        (100.8, 101.4, 100.6, 100.7),  # sweep above upper=101.0
        (100.7, 100.9, 100.0, 100.6),  # reclaim below upper
        (100.6, 100.5,  99.6,  99.8),  # confirm below reclaim low
    ]
    candles = []
    for i, (o, h, l, c) in enumerate(rows):
        candles.append(Candle(ts=base + timedelta(minutes=15*i), open=o, high=h, low=l, close=c, volume=0.0))
    return ndog, candles


if __name__ == "__main__":
    bull_ndog, bull_candles = make_bullish()
    bear_ndog, bear_candles = make_bearish()

    print("Bullish:", detect_reclaim_confirm(bull_candles, bull_ndog))
    print("Bearish:", detect_reclaim_confirm(bear_candles, bear_ndog))
