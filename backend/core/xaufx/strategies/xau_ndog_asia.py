
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..models import Candle, Signal
from ..sessions.clock import NYSessionClock
from ..sessions.ndog import NDOG, compute_ndog
from ..detectors.reclaim_confirm import detect_reclaim_confirm
from ..detectors.previous_day_levels import previous_day_levels, detect_previous_day_sweep, near_level
from ..detectors.demand_zone import DemandZone, near_zone
from ..detectors.fvg import latest_fvg, price_in_fvg


@dataclass
class SweepInfo:
    swept_above: bool = False
    swept_below: bool = False
    reclaim_up: bool = False
    reclaim_down: bool = False


def detect_sweep_and_reclaim(candles: List[Candle], ndog: NDOG) -> SweepInfo:
    """
    Relaxed sweep/reclaim logic for 1h bars.

    Bullish reclaim:
      - some candle sweeps below NDOG lower bound
      - later candle closes back above NDOG lower bound

    Bearish reclaim:
      - some candle sweeps above NDOG upper bound
      - later candle closes back below NDOG upper bound
    """
    info = SweepInfo()
    upper = max(ndog.close_17, ndog.open_18)
    lower = min(ndog.close_17, ndog.open_18)

    swept_below_seen = False
    swept_above_seen = False

    for c in candles:
        if c.low < lower:
            swept_below_seen = True
            info.swept_below = True

        if c.high > upper:
            swept_above_seen = True
            info.swept_above = True

        if swept_below_seen and c.close > lower:
            info.reclaim_up = True

        if swept_above_seen and c.close < upper:
            info.reclaim_down = True

    return info


def asia_bars_only(clock: NYSessionClock, candles: List[Candle], lookback: int = 12) -> List[Candle]:
    subset = candles[-lookback:] if len(candles) > lookback else candles
    return [c for c in subset if clock.label(c.ts) == "ASIA"]


def choose_entry_from_fvg(last_bar: Candle, fvg) -> float:
    """
    Prefer CE if current bar traded through midpoint.
    Else if current close is inside the FVG, use close.
    Else use midpoint as the intended refined entry.
    Always returns a float.
    """
    if fvg is None:
        return float(last_bar.close)

    if getattr(fvg, "midpoint", None) is None:
        return float(last_bar.close)

    if fvg.bottom <= last_bar.low <= fvg.top or fvg.bottom <= last_bar.high <= fvg.top:
        if last_bar.low <= fvg.midpoint <= last_bar.high:
            return float(fvg.midpoint)
        if price_in_fvg(last_bar.close, fvg):
            return float(last_bar.close)

    return float(fvg.midpoint)


def latest_ndog_in_window(candles: List[Candle], tz_name: str) -> Optional[NDOG]:
    """
    Scan the window and return the most recent valid NDOG pair.
    This lets Asia-session logic use the NDOG formed earlier in the session.
    """
    if len(candles) < 2:
        return None

    latest = None
    for i in range(1, len(candles)):
        nd = compute_ndog(candles[max(0, i - 1): i + 1], tz_name=tz_name)
        if nd is not None:
            latest = nd
    return latest


class XAUNDOGAsiaStrategy:

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
        dz_bonus = 0.0
        dz_hint = ""
        if symbol != "XAUUSD":
            result["reason"] = "unsupported symbol"
            return result

        if len(candles) < 12:
            result["reason"] = "insufficient bars"
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
        last = asia[-1]

        # Bullish branch
        if sweep.swept_below and sweep.reclaim_up:
            if self.daily_bias == "bearish":
                result["reason"] = "daily bias blocks bullish setup"
                return result

            dz_bonus = 0.0
            dz_hint = ""
            if self.demand_zone is not None:
                if near_zone(last.close, self.demand_zone, self.demand_zone_tolerance):
                    dz_bonus += 0.30
                    dz_hint = (
                        f"near_demand[{self.demand_zone.low:.2f},{self.demand_zone.high:.2f}] "
                        f"strength={self.demand_zone.strength:.2f} "
                    )
                elif self.require_demand_zone:
                    result["reason"] = "bullish setup lacks HTF demand-zone confluence"
                    return result
            elif self.require_demand_zone:
                result["reason"] = "no HTF demand zone available"
                return result

            result["sweep_reclaim"] = True

            confirm = detect_reclaim_confirm(asia, ndog)
            if confirm.direction == "bullish":
                result["mss_match"] = True
            else:
                result["reason"] = f"bullish reclaim confirm failed: {confirm.reason}"
                if self.require_mss:
                    return result

            bull_fvg = latest_fvg(
                asia,
                direction="bullish",
                min_gap=self.fvg_min_gap,
                only_unfilled=False,
            )
            if bull_fvg is not None:
                result["fvg_match"] = True
            elif self.require_fvg:
                result["reason"] = "bullish MSS present but no bullish FVG"
                return result

            entry = choose_entry_from_fvg(last, bull_fvg if bull_fvg is not None else None)

            sweep_candle = asia[confirm.sweep_index] if 0 <= confirm.sweep_index < len(asia) else None
            reclaim_candle = asia[confirm.reclaim_index] if 0 <= confirm.reclaim_index < len(asia) else None

            stop_candidates = []
            if sweep_candle is not None:
                stop_candidates.append(sweep_candle.low)
            if reclaim_candle is not None:
                stop_candidates.append(reclaim_candle.low)
            if not stop_candidates:
                stop_candidates.append(min(c.low for c in asia[-3:]))

            stop = min(stop_candidates) - self.stop_buffer

            if entry is None or stop >= entry:
                result["reason"] = "invalid bullish risk geometry"
                return result

            risk = entry - stop
            if reclaim_candle is not None and risk > 0:
                entry_extension_r = max(0.0, entry - reclaim_candle.high) / risk
                if entry_extension_r > self.max_entry_extension_r:
                    result["reason"] = (
                        f"bullish entry too extended from reclaim high: "
                        f"{entry_extension_r:.3f}R > {self.max_entry_extension_r:.3f}R"
                    )
                    return result

            target = entry + self.risk_reward * risk

            pd_bonus = 0.0
            pd_hint = ""
            if pd_levels is not None:
                touched_pdl = (pd_sweep is not None and pd_sweep.swept_pdl)
                near_pdl = near_level(last.close, pd_levels.low, self.pd_tolerance)
                if touched_pdl:
                    pd_bonus += 0.35
                    pd_hint += "swept_PDL "
                elif near_pdl:
                    pd_bonus += 0.20
                    pd_hint += "near_PDL "

                # use PDH as a stretch target hint for bullish setups
                if pd_levels.high > target:
                    target = min(pd_levels.high, target + 0.5 * risk)
                    pd_hint += f"PDH_target={pd_levels.high:.2f} "

            result.update({
                "ok": True,
                "reason": (
                    (f"bullish NDOG Asia | gap={ndog.gap:.2f} mid={ndog.midpoint:.2f} ")
                    + (f"PDL={pd_levels.low:.2f} PDH={pd_levels.high:.2f} " if pd_levels is not None else "")
                    + (pd_hint if pd_hint else "")
                    + (dz_hint if dz_hint else "")
                    + (f"confirm={confirm.reason} ")
                    + (
                        f"FVG[{bull_fvg.bottom:.2f},{bull_fvg.top:.2f}] CE={bull_fvg.midpoint:.2f}"
                        if bull_fvg is not None else "FVG[none]"
                    )
                ),
                "direction": "BUY",
                "entry": entry,
                "stop": stop,
                "target": target,
                "score": 1.0 + pd_bonus + dz_bonus,
            })
            return result

        # Bearish branch
        if sweep.swept_above and sweep.reclaim_down:
            if self.daily_bias == "bullish":
                result["reason"] = "daily bias blocks bearish setup"
                return result

            result["sweep_reclaim"] = True

            confirm = detect_reclaim_confirm(asia, ndog)
            if confirm.direction == "bearish":
                result["mss_match"] = True
            else:
                result["reason"] = f"bearish reclaim confirm failed: {confirm.reason}"
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
                result["reason"] = "bearish MSS present but no bearish FVG"
                return result

            entry = choose_entry_from_fvg(last, bear_fvg if bear_fvg is not None else None)

            sweep_candle = asia[confirm.sweep_index] if 0 <= confirm.sweep_index < len(asia) else None
            reclaim_candle = asia[confirm.reclaim_index] if 0 <= confirm.reclaim_index < len(asia) else None

            stop_candidates = []
            if sweep_candle is not None:
                stop_candidates.append(sweep_candle.high)
            if reclaim_candle is not None:
                stop_candidates.append(reclaim_candle.high)
            if not stop_candidates:
                stop_candidates.append(max(c.high for c in asia[-3:]))

            stop = max(stop_candidates) + self.stop_buffer

            if entry is None or stop <= entry:
                result["reason"] = "invalid bearish risk geometry"
                return result

            risk = stop - entry
            if reclaim_candle is not None and risk > 0:
                entry_extension_r = max(0.0, reclaim_candle.low - entry) / risk
                if entry_extension_r > self.max_entry_extension_r:
                    result["reason"] = (
                        f"bearish entry too extended from reclaim low: "
                        f"{entry_extension_r:.3f}R > {self.max_entry_extension_r:.3f}R"
                    )
                    return result

            target = entry - self.risk_reward * risk

            pd_bonus = 0.0
            pd_hint = ""
            if pd_levels is not None:
                touched_pdh = (pd_sweep is not None and pd_sweep.swept_pdh)
                near_pdh = near_level(last.close, pd_levels.high, self.pd_tolerance)
                if touched_pdh:
                    pd_bonus += 0.35
                    pd_hint += "swept_PDH "
                elif near_pdh:
                    pd_bonus += 0.20
                    pd_hint += "near_PDH "

                # use PDL as a stretch target hint for bearish setups
                if pd_levels.low < target:
                    target = max(pd_levels.low, target - 0.5 * risk)
                    pd_hint += f"PDL_target={pd_levels.low:.2f} "

            result.update({
                "ok": True,
                "reason": (
                    (f"bearish NDOG Asia | gap={ndog.gap:.2f} mid={ndog.midpoint:.2f} ")
                    + (f"PDL={pd_levels.low:.2f} PDH={pd_levels.high:.2f} " if pd_levels is not None else "")
                    + (pd_hint if pd_hint else "")
                    + (dz_hint if dz_hint else "")
                    + (f"confirm={confirm.reason} ")
                    + (
                        f"FVG[{bear_fvg.bottom:.2f},{bear_fvg.top:.2f}] CE={bear_fvg.midpoint:.2f}"
                        if bear_fvg is not None else "FVG[none]"
                    )
                ),
                "direction": "SELL",
                "entry": entry,
                "stop": stop,
                "target": target,
                "score": -(1.0 + pd_bonus),
            })
            return result

        result["reason"] = "no qualified NDOG Asia setup"
        return result

    name = "xau_ndog_asia"

    def __init__(
        self,
        timezone: str = "America/New_York",
        mss_lookback: int = 2,
        mss_displacement: float = 0.75,
        fvg_min_gap: float = 0.0,
        risk_reward: float = 2.0,
        require_mss: bool = True,
        require_fvg: bool = True,
        daily_bias: str = "flat",
        require_pd_confluence: bool = False,
        pd_tolerance: float = 5.0,
        stop_buffer: float = 1.0,
        max_entry_extension_r: float = 0.5,
        demand_zone: DemandZone | None = None,
        require_demand_zone: bool = False,
        demand_zone_tolerance: float = 10.0,
    ) -> None:
        self.clock = NYSessionClock(timezone)
        self.mss_lookback = mss_lookback
        self.mss_displacement = mss_displacement
        self.fvg_min_gap = fvg_min_gap
        self.risk_reward = risk_reward
        self.require_mss = require_mss
        self.require_fvg = require_fvg
        self.daily_bias = daily_bias
        self.require_pd_confluence = require_pd_confluence
        self.pd_tolerance = pd_tolerance
        self.stop_buffer = stop_buffer
        self.max_entry_extension_r = max_entry_extension_r
        self.demand_zone = demand_zone
        self.require_demand_zone = require_demand_zone
        self.demand_zone_tolerance = demand_zone_tolerance

    def generate(self, symbol: str, candles: List[Candle]) -> Signal:
        setup = self.evaluate_setup(symbol, candles)
        if not setup["ok"]:
            return Signal(
                strategy=self.name,
                symbol=symbol,
                side="FLAT",
                reason=setup["reason"],
            )
        return Signal(
            strategy=self.name,
            symbol=symbol,
            side=setup["direction"],
            score=setup["score"],
            entry=setup["entry"],
            stop=setup["stop"],
            target=setup["target"],
            reason=setup["reason"],
        )
