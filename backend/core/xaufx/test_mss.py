
from datetime import datetime, timedelta, timezone

from backend.core.xaufx.models import Candle
from backend.core.xaufx.detectors.mss import detect_mss, find_pivot_highs, find_pivot_lows


def make_candles():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # Handcrafted structure:
    # pivot high near idx 1
    # pivot low near idx 4
    # bullish break later
    rows = [
        (100.0, 101.0,  99.0, 100.5),
        (100.5, 105.0, 100.0, 104.0),  # pivot high candidate
        (104.0, 103.0, 100.5, 101.0),
        (101.0, 102.0,  99.5, 100.0),
        (100.0, 101.0,  96.0,  97.0),  # pivot low candidate
        (97.0,  99.0,  96.5,  98.5),
        (98.5, 104.0,  98.0, 103.5),
        (103.5, 106.5, 103.0, 106.0),  # bullish break above old pivot high
        (106.0, 107.0, 105.0, 106.5),
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
    candles = make_candles()

    highs = find_pivot_highs(candles, left=1, right=1)
    lows = find_pivot_lows(candles, left=1, right=1)

    print("Pivot highs:", highs)
    print("Pivot lows :", lows)

    sig = detect_mss(candles, pivot_left=1, pivot_right=1, min_displacement=0.0)
    print("MSS:", sig)
