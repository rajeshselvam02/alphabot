
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..models import Candle
from ..sessions.ndog import NDOG


@dataclass
class ReclaimConfirmSignal:
    direction: str               # "bullish" | "bearish" | "none"
    sweep_index: int
    reclaim_index: int
    confirm_index: int
    trigger_level: float
    reclaim_high: Optional[float]
    reclaim_low: Optional[float]
    reason: str


def detect_reclaim_confirm(
    candles: List[Candle],
    ndog: NDOG,
) -> ReclaimConfirmSignal:
    """
    Bullish:
      1) some candle sweeps below NDOG lower bound
      2) a later candle closes back above NDOG lower bound (reclaim candle)
      3) the next candle closes above reclaim candle high

    Bearish:
      1) some candle sweeps above NDOG upper bound
      2) a later candle closes back below NDOG upper bound (reclaim candle)
      3) the next candle closes below reclaim candle low
    """
    if len(candles) < 3:
        return ReclaimConfirmSignal(
            direction="none",
            sweep_index=-1,
            reclaim_index=-1,
            confirm_index=-1,
            trigger_level=0.0,
            reclaim_high=None,
            reclaim_low=None,
            reason="insufficient bars",
        )

    upper = max(ndog.close_17, ndog.open_18)
    lower = min(ndog.close_17, ndog.open_18)

    # --- Bullish path ---
    sweep_idx = -1
    reclaim_idx = -1

    for i, c in enumerate(candles):
        if sweep_idx < 0 and c.low < lower:
            sweep_idx = i
            continue

        if sweep_idx >= 0 and i > sweep_idx and reclaim_idx < 0:
            if c.close > lower:
                reclaim_idx = i
                continue

        if reclaim_idx >= 0 and i > reclaim_idx:
            reclaim = candles[reclaim_idx]
            if c.close > reclaim.high:
                return ReclaimConfirmSignal(
                    direction="bullish",
                    sweep_index=sweep_idx,
                    reclaim_index=reclaim_idx,
                    confirm_index=i,
                    trigger_level=lower,
                    reclaim_high=reclaim.high,
                    reclaim_low=reclaim.low,
                    reason="bullish reclaim continuation confirmed",
                )

    # --- Bearish path ---
    sweep_idx = -1
    reclaim_idx = -1

    for i, c in enumerate(candles):
        if sweep_idx < 0 and c.high > upper:
            sweep_idx = i
            continue

        if sweep_idx >= 0 and i > sweep_idx and reclaim_idx < 0:
            if c.close < upper:
                reclaim_idx = i
                continue

        if reclaim_idx >= 0 and i > reclaim_idx:
            reclaim = candles[reclaim_idx]
            if c.close < reclaim.low:
                return ReclaimConfirmSignal(
                    direction="bearish",
                    sweep_index=sweep_idx,
                    reclaim_index=reclaim_idx,
                    confirm_index=i,
                    trigger_level=upper,
                    reclaim_high=reclaim.high,
                    reclaim_low=reclaim.low,
                    reason="bearish reclaim continuation confirmed",
                )

    return ReclaimConfirmSignal(
        direction="none",
        sweep_index=-1,
        reclaim_index=-1,
        confirm_index=-1,
        trigger_level=0.0,
        reclaim_high=None,
        reclaim_low=None,
        reason="no reclaim continuation",
    )
