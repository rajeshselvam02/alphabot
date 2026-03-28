
from __future__ import annotations

from typing import List

from ..models import Candle, Signal


def sma(values: List[float], period: int) -> float:
    if len(values) < period:
        return 0.0
    return sum(values[-period:]) / period


def atr(candles: List[Candle], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        curr = candles[i]
        prev = candles[i - 1]
        tr = max(
            curr.high - curr.low,
            abs(curr.high - prev.close),
            abs(curr.low - prev.close),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


def ravi_pct(closes: List[float]) -> float:
    if len(closes) < 65:
        return 0.0
    short = sma(closes, 7)
    long = sma(closes, 65)
    if long == 0:
        return 0.0
    return abs(short - long) / long * 100.0


class XAUDailyMomentumStrategy:
    name = "xau_daily_momentum"

    def __init__(self, fast_ma=20, slow_ma=100, ravi_threshold=5.0, atr_mult=2.0):
        self.fast_ma = fast_ma
        self.slow_ma = slow_ma
        self.ravi_threshold = ravi_threshold
        self.atr_mult = atr_mult

    def generate(self, symbol: str, candles: List[Candle]) -> Signal:
        if len(candles) < 120:
            return Signal(self.name, symbol, "FLAT", reason="not enough data")

        closes = [c.close for c in candles]
        fast = sma(closes, self.fast_ma)
        slow = sma(closes, self.slow_ma)
        ravi = ravi_pct(closes)
        current_atr = atr(candles)

        last = candles[-1]

        if fast > slow and ravi > self.ravi_threshold:
            stop = last.close - self.atr_mult * current_atr
            target = last.close + 3 * current_atr
            return Signal(self.name, symbol, "BUY", entry=last.close, stop=stop, target=target)

        if fast < slow:
            return Signal(self.name, symbol, "SELL", entry=last.close)

        return Signal(self.name, symbol, "FLAT")


def daily_bias(symbol: str, candles: List[Candle], fast_ma=20, slow_ma=100, ravi_threshold=5.0) -> str:
    strat = XAUDailyMomentumStrategy(
        fast_ma=fast_ma,
        slow_ma=slow_ma,
        ravi_threshold=ravi_threshold,
    )
    sig = strat.generate(symbol, candles)
    if sig.side == "BUY":
        return "bullish"
    if sig.side == "SELL":
        return "bearish"
    return "flat"
