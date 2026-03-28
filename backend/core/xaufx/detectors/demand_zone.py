
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..models import Candle


@dataclass
class DemandZone:
    left_index: int
    base_index: int
    impulse_index: int
    low: float
    high: float
    midpoint: float
    strength: float
    reason: str


def detect_recent_demand_zone(
    candles: List[Candle],
    lookback: int = 40,
    impulse_factor: float = 1.5,
) -> Optional[DemandZone]:
    """
    Very simple HTF demand-zone model:
    - find a small-bodied base candle
    - followed by a strong bullish impulse candle
    - demand zone is base candle body/wick envelope
    """
    if len(candles) < 3:
        return None

    subset = candles[-lookback:] if len(candles) > lookback else candles
    if len(subset) < 3:
        return None

    ranges = [max(0.0, c.high - c.low) for c in subset]
    avg_range = sum(ranges) / len(ranges) if ranges else 0.0
    if avg_range <= 0:
        return None

    latest: Optional[DemandZone] = None

    for i in range(1, len(subset) - 1):
        base = subset[i]
        impulse = subset[i + 1]

        base_range = max(0.0, base.high - base.low)
        base_body = abs(base.close - base.open)
        impulse_range = max(0.0, impulse.high - impulse.low)
        impulse_body = impulse.close - impulse.open

        small_base = base_range <= avg_range * 0.9 and base_body <= max(base_range * 0.5, 1e-9)
        bullish_impulse = impulse.close > impulse.open and impulse_range >= avg_range * impulse_factor and impulse_body > 0

        if not (small_base and bullish_impulse):
            continue

        zone_low = min(base.low, base.open, base.close)
        zone_high = max(base.high, base.open, base.close)
        strength = impulse_range / avg_range if avg_range > 0 else 0.0

        latest = DemandZone(
            left_index=max(0, i - 1),
            base_index=i,
            impulse_index=i + 1,
            low=zone_low,
            high=zone_high,
            midpoint=(zone_low + zone_high) / 2.0,
            strength=strength,
            reason="base followed by bullish impulse",
        )

    return latest


def price_in_zone(price: float, zone: DemandZone) -> bool:
    return zone.low <= price <= zone.high


def near_zone(price: float, zone: DemandZone, tolerance: float) -> bool:
    return (zone.low - tolerance) <= price <= (zone.high + tolerance)
