
from datetime import datetime, timedelta, timezone

from backend.core.xaufx.models import Candle
from backend.core.xaufx.detectors.fvg import detect_fvgs, latest_fvg


def make_bullish_fvg_candles():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        (100.0, 101.0,  99.0, 100.5),
        (100.5, 104.0, 100.0, 103.5),
        (104.2, 106.0, 104.2, 105.5),  # candle3.low > candle1.high => bullish FVG
        (105.5, 106.5, 103.8, 104.0),  # later fill
    ]

    candles = []
    for i, (o, h, l, c) in enumerate(rows):
        candles.append(
            Candle(
                ts=base + timedelta(hours=i),
                open=o,
                high=h,
                low=l,
                close=c,
                volume=0.0,
            )
        )
    return candles


def make_bearish_fvg_candles():
    base = datetime(2026, 1, 2, tzinfo=timezone.utc)
    rows = [
        (110.0, 111.0, 109.0, 110.5),
        (110.5, 110.8, 106.0, 106.5),
        (105.8, 105.8, 103.5, 104.0),  # candle3.high < candle1.low => bearish FVG
        (104.0, 109.5, 103.8, 108.0),  # later fill
    ]

    candles = []
    for i, (o, h, l, c) in enumerate(rows):
        candles.append(
            Candle(
                ts=base + timedelta(hours=i),
                open=o,
                high=h,
                low=l,
                close=c,
                volume=0.0,
            )
        )
    return candles


if __name__ == "__main__":
    bull = make_bullish_fvg_candles()
    bear = make_bearish_fvg_candles()

    bull_fvgs = detect_fvgs(bull)
    bear_fvgs = detect_fvgs(bear)

    print("Bullish FVGs:", bull_fvgs)
    print("Latest bullish:", latest_fvg(bull, direction="bullish"))

    print("Bearish FVGs:", bear_fvgs)
    print("Latest bearish:", latest_fvg(bear, direction="bearish"))
