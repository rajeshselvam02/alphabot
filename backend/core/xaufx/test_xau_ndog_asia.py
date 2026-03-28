
from datetime import datetime, timedelta, timezone

from backend.core.xaufx.models import Candle
from backend.core.xaufx.strategies.xau_ndog_asia import XAUNDOGAsiaStrategy


def make_sequence():
    """
    Synthetic 1h candles.

    We try to create:
    - 17:00 NY close
    - 18:00 NY reopen => NDOG
    - Asia sweep below NDOG lower
    - reclaim above midpoint
    - bullish MSS
    - bullish FVG
    """
    # Use UTC times that roughly map around NY hours depending on DST.
    # For detector smoke testing, what matters is consistent hour labeling after TZ conversion.
    base = datetime(2026, 1, 5, 21, 0, tzinfo=timezone.utc)

    rows = [
        # extra context bars
        (99.6, 100.2,  99.2,  99.9),
        (99.9, 100.4,  99.4, 100.1),
        (100.1, 100.6, 99.7, 100.0),
        (100.0, 100.7, 99.6, 100.3),
        # pre-NDOG context
        (100.0, 101.0,  99.5, 100.8),  # 21:00 UTC
        (100.8, 101.2, 100.1, 100.4),  # 22:00 UTC ~ 17:00 NY candidate
        (101.2, 101.8, 100.9, 101.5),  # 23:00 UTC ~ 18:00 NY candidate (gap up)
        # Asia sweep below lower bound then reclaim
        (101.5, 101.7,  99.4, 100.2),
        (100.2, 100.8,  98.8, 100.9),
        (100.9, 103.2, 100.7, 102.8),  # structure push
        (102.9, 104.2, 102.9, 103.8),  # bullish FVG candidate against candle 4
        (103.8, 104.0, 102.4, 103.0),  # touch back toward CE area
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
    strat = XAUNDOGAsiaStrategy()
    sig = strat.generate("XAUUSD", make_sequence())
    print(sig)
