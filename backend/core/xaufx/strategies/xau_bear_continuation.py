from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
from zoneinfo import ZoneInfo

from backend.core.xaufx.detectors.demand_zone import near_zone
from backend.core.xaufx.detectors.fvg import latest_fvg
from backend.core.xaufx.detectors.previous_day_levels import (
    detect_previous_day_sweep,
    previous_day_levels,
)
from backend.core.xaufx.detectors.reclaim_confirm import detect_reclaim_confirm
from backend.core.xaufx.models import Candle
from backend.core.xaufx.sessions.clock import NYSessionClock
from backend.core.xaufx.strategies.xau_ndog_asia import (
    asia_bars_only,
    detect_sweep_and_reclaim,
    latest_ndog_in_window,
)


@dataclass
class SetupResult:
    ok: bool
    reason: str
    direction: Optional[str]
    entry: Optional[float]
    stop: Optional[float]
    target: Optional[float]
    score: float
    ndog_found: bool
    asia_ready: bool
    sweep_reclaim: bool
    mss_match: bool
    fvg_match: bool


class XAUBearContinuationStrategy:
    """
    Dedicated bearish XAU continuation model.

    Idea:
    - Only short when higher timeframe is bearish or neutral.
    - Require bearish sweep + reclaim.
    - Require bearish MSS.
    - Prefer / require bearish FVG.
    - Prefer / require previous-day high sweep.
    """

    def __init__(
        self,
        timezone: str,
        daily_bias: str = "neutral",
        require_mss: bool = True,
        require_fvg: bool = True,
        require_pd_confluence: bool = True,
        pd_tolerance: float = 5.0,
        stop_buffer: float = 2.5,
        max_entry_extension_r: float = 0.4,
        demand_zone=None,
        require_demand_zone: bool = False,
        demand_zone_tolerance: float = 10.0,
        allowed_hours: Optional[set[int]] = None,
    ) -> None:
        self.clock = NYSessionClock(timezone)
        self.daily_bias = daily_bias
        self.require_mss = require_mss
        self.require_fvg = require_fvg
        self.require_pd_confluence = require_pd_confluence
        self.pd_tolerance = pd_tolerance
        self.stop_buffer = stop_buffer
        self.max_entry_extension_r = max_entry_extension_r
        self.demand_zone = demand_zone
        self.require_demand_zone = require_demand_zone
        self.demand_zone_tolerance = demand_zone_tolerance
        self.allowed_hours = allowed_hours
        self.fvg_min_gap = 0.0

    def _ny_hour(self, candle: Candle) -> int:
        return candle.ts.astimezone(ZoneInfo(self.clock.tz_name)).hour

    @staticmethod
    def _pd_swept_high(pd_sweep) -> bool:
        if pd_sweep is None:
            return False
        if hasattr(pd_sweep, "swept_high"):
            return bool(pd_sweep.swept_high)
        if isinstance(pd_sweep, dict):
            return bool(pd_sweep.get("swept_high", False))
        return False

    def evaluate_setup(self, symbol: str, candles: List[Candle]) -> dict:
        result = {
            "ok": False,
            "reason": "unknown",
            "direction": None,
            "entry": None,
            "stop": None,
            "target": None,
            "score": 0.0,
            "ndog_found": False,
            "asia_ready": False,
            "sweep_reclaim": False,
            "mss_match": False,
            "fvg_match": False,
        }

        if symbol != "XAUUSD":
            result["reason"] = "unsupported symbol"
            return result

        if len(candles) < 12:
            result["reason"] = "insufficient bars"
            return result

        last = candles[-1]

        if self.allowed_hours is not None and self._ny_hour(last) not in self.allowed_hours:
            result["reason"] = "entry hour blocked"
            return result

        if self.daily_bias == "bullish":
            result["reason"] = "daily bias blocks bearish setup"
            return result

        if self.daily_bias not in ("bearish", "neutral"):
            result["reason"] = f"unsupported daily bias for shorts: {self.daily_bias}"
            return result

        ndog = latest_ndog_in_window(candles[-24:], tz_name=self.clock.tz_name)
        if ndog is None:
            result["reason"] = "no NDOG in active window"
            return result
        result["ndog_found"] = True

        asia = asia_bars_only(self.clock, candles, lookback=12)
        if len(asia) < 4:
            result["reason"] = "not enough Asia bars"
            return result
        result["asia_ready"] = True

        pd_levels = previous_day_levels(candles, tz_name=self.clock.tz_name)
        pd_sweep = detect_previous_day_sweep(asia, pd_levels) if pd_levels is not None else None

        sweep = detect_sweep_and_reclaim(asia, ndog)
        if not (sweep.swept_above and sweep.reclaim_down):
            result["reason"] = "no bearish sweep reclaim"
            return result
        result["sweep_reclaim"] = True

        if self.require_pd_confluence and not self._pd_swept_high(pd_sweep):
            result["reason"] = "missing previous-day high sweep"
            return result

        confirm = detect_reclaim_confirm(asia, ndog)
        if getattr(confirm, "direction", None) == "bearish":
            result["mss_match"] = True
        else:
            result["reason"] = f"bearish reclaim confirm failed: {getattr(confirm, 'reason', 'unknown')}"
            if self.require_mss:
                return result

        bear_fvg = latest_fvg(
            asia,
            direction="bearish",
            min_gap=self.fvg_min_gap,
            only_unfilled=False,
        )
        if bear_fvg is not None:
            result["fvg_match"] = True
        elif self.require_fvg:
            result["reason"] = "missing bearish FVG"
            return result

        entry = last.close
        structure_high = max(c.high for c in asia[-3:])
        stop = structure_high + self.stop_buffer

        risk = stop - entry
        if risk <= 0:
            result["reason"] = "invalid bearish risk"
            return result

        reclaim_level = max(ndog.close_17, ndog.open_18)
        extension = max(0.0, reclaim_level - entry)
        if risk > 0 and (extension / risk) > self.max_entry_extension_r:
            result["reason"] = "bearish entry too extended from reclaim level"
            return result

        if self.demand_zone is not None and near_zone(last.close, self.demand_zone, self.demand_zone_tolerance):
            if self.require_demand_zone:
                result["reason"] = "too close to HTF demand zone"
                return result

        target = entry - 2.0 * risk

        score = 1.0
        if result["mss_match"]:
            score += 0.60
        if result["fvg_match"]:
            score += 0.45
        if self._pd_swept_high(pd_sweep):
            score += 0.30
        if self.daily_bias == "bearish":
            score += 0.25
        if self.demand_zone is not None and near_zone(last.close, self.demand_zone, self.demand_zone_tolerance):
            score -= 0.20

        result.update(
            {
                "ok": True,
                "reason": "bearish continuation setup",
                "direction": "SELL",
                "entry": entry,
                "stop": stop,
                "target": target,
                "score": score,
            }
        )
        return result
