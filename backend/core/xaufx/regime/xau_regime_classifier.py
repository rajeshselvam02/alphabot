from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Optional

from backend.core.xaufx.models import Candle


@dataclass
class RegimeFeatures:
    ema_fast: float
    ema_slow: float
    adx: float
    atr: float
    atr_pct: float
    dxy_fast: Optional[float]
    dxy_slow: Optional[float]
    dxy_trend: str
    regime: str
    confidence: float


def _close_series(candles: List[Candle]) -> List[float]:
    return [c.close for c in candles]


def sma(vals: List[float], period: int) -> Optional[float]:
    if len(vals) < period:
        return None
    return sum(vals[-period:]) / period


def ema(vals: List[float], period: int) -> Optional[float]:
    if len(vals) < period:
        return None
    k = 2.0 / (period + 1.0)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1.0 - k)
    return e


def compute_atr(candles: List[Candle], period: int = 14) -> Optional[float]:
    if len(candles) < period + 1:
        return None

    trs = []
    for i in range(1, len(candles)):
        h = candles[i].high
        l = candles[i].low
        pc = candles[i - 1].close
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)

    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def compute_atr_percentile(candles: List[Candle], atr_period: int = 14, lookback: int = 60) -> Optional[float]:
    if len(candles) < lookback + atr_period + 1:
        return None

    atrs = []
    for i in range(atr_period + 1, len(candles) + 1):
        sub = candles[:i]
        a = compute_atr(sub, atr_period)
        if a is not None:
            atrs.append(a)

    if len(atrs) < lookback:
        return None

    sample = atrs[-lookback:]
    current = sample[-1]
    below = sum(1 for x in sample if x <= current)
    return below / len(sample)


def compute_adx(candles: List[Candle], period: int = 14) -> Optional[float]:
    if len(candles) < period * 2:
        return None

    plus_dm = []
    minus_dm = []
    trs = []

    for i in range(1, len(candles)):
        h = candles[i].high
        l = candles[i].low
        ph = candles[i - 1].high
        pl = candles[i - 1].low
        pc = candles[i - 1].close

        up = h - ph
        down = pl - l

        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    def smooth(vals: List[float], p: int) -> List[float]:
        s = sum(vals[:p])
        out = [s]
        for v in vals[p:]:
            s = s - s / p + v
            out.append(s)
        return out

    str_ = smooth(trs, period)
    spdm = smooth(plus_dm, period)
    smdm = smooth(minus_dm, period)

    dx = []
    for i in range(len(str_)):
        if str_[i] == 0:
            continue
        pdi = 100.0 * spdm[i] / str_[i]
        mdi = 100.0 * smdm[i] / str_[i]
        s = pdi + mdi
        if s == 0:
            continue
        dx.append(100.0 * abs(pdi - mdi) / s)

    if len(dx) < period:
        return None
    return sum(dx[-period:]) / period


class XAURegimeClassifier:
    """
    Minimal regime classifier.

    Regimes:
    - bull_trend
    - bear_trend
    - range
    - breakout
    - no_trade
    """

    def __init__(
        self,
        fast_period: int = 20,
        slow_period: int = 50,
        adx_period: int = 14,
        atr_period: int = 14,
        atr_lookback: int = 60,
        bull_adx: float = 20.0,
        range_adx: float = 18.0,
        breakout_atr_pct: float = 0.85,
        range_atr_pct: float = 0.65,
    ) -> None:
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.adx_period = adx_period
        self.atr_period = atr_period
        self.atr_lookback = atr_lookback
        self.bull_adx = bull_adx
        self.range_adx = range_adx
        self.breakout_atr_pct = breakout_atr_pct
        self.range_atr_pct = range_atr_pct

    def classify(
        self,
        daily_xau: List[Candle],
        daily_dxy: Optional[List[Candle]] = None,
    ) -> RegimeFeatures:
        closes = _close_series(daily_xau)
        ema_fast = ema(closes, self.fast_period) or closes[-1]
        ema_slow = ema(closes, self.slow_period) or closes[-1]
        adx = compute_adx(daily_xau, self.adx_period) or 0.0
        atr = compute_atr(daily_xau, self.atr_period) or 0.0
        atr_pct = compute_atr_percentile(daily_xau, self.atr_period, self.atr_lookback) or 0.5

        dxy_fast = None
        dxy_slow = None
        dxy_trend = "unknown"

        if daily_dxy:
            dxy_closes = _close_series(daily_dxy)
            dxy_fast = ema(dxy_closes, self.fast_period)
            dxy_slow = ema(dxy_closes, self.slow_period)
            if dxy_fast is not None and dxy_slow is not None:
                if dxy_fast > dxy_slow:
                    dxy_trend = "up"
                elif dxy_fast < dxy_slow:
                    dxy_trend = "down"
                else:
                    dxy_trend = "flat"

        regime = "no_trade"
        confidence = 0.0

        bull_ok = ema_fast > ema_slow and adx >= self.bull_adx and dxy_trend in ("down", "unknown", "flat")
        bear_ok = ema_fast < ema_slow and adx >= self.bull_adx and dxy_trend in ("up", "unknown", "flat")

        if bull_ok:
            regime = "bull_trend"
            confidence = min(1.0, 0.45 + 0.02 * max(0.0, adx - self.bull_adx) + (0.15 if dxy_trend == "down" else 0.0))
        elif bear_ok:
            regime = "bear_trend"
            confidence = min(1.0, 0.45 + 0.02 * max(0.0, adx - self.bull_adx) + (0.15 if dxy_trend == "up" else 0.0))
        elif adx < self.range_adx and atr_pct < self.range_atr_pct:
            regime = "range"
            confidence = min(1.0, 0.50 + 0.20 * (self.range_adx - adx) / max(self.range_adx, 1.0))
        elif atr_pct > self.breakout_atr_pct and adx >= self.range_adx:
            regime = "breakout"
            confidence = min(1.0, 0.55 + 0.25 * (atr_pct - self.breakout_atr_pct) / max(1.0 - self.breakout_atr_pct, 1e-9))
        else:
            regime = "no_trade"
            confidence = 0.25

        return RegimeFeatures(
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            adx=adx,
            atr=atr,
            atr_pct=atr_pct,
            dxy_fast=dxy_fast,
            dxy_slow=dxy_slow,
            dxy_trend=dxy_trend,
            regime=regime,
            confidence=confidence,
        )

    def classify_dict(
        self,
        daily_xau: List[Candle],
        daily_dxy: Optional[List[Candle]] = None,
    ) -> dict:
        return asdict(self.classify(daily_xau, daily_dxy))
