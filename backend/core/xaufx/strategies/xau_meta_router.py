from __future__ import annotations

from dataclasses import asdict
from typing import List, Optional

from backend.core.xaufx.detectors.demand_zone import detect_recent_demand_zone
from backend.core.xaufx.models import Candle
from backend.core.xaufx.regime.xau_regime_classifier import XAURegimeClassifier
from backend.core.xaufx.strategies.xau_ndog_asia import XAUNDOGAsiaStrategy
from backend.core.xaufx.strategies.xau_ndog_asia_short import XAUNDOGAsiaShortStrategy


class XAUMetaRouter:
    """
    Meta-system v2

    Routing:
    - bull_trend  -> NDOG Asia long, confidence >= bull_min_conf
    - bear_trend  -> NDOG Asia short, confidence >= bear_min_conf
    - else        -> no trade

    v2 changes:
    - stricter bear gate
    - stricter short strategy requirements
    - optional previous-day confluence for shorts
    """

    def __init__(
        self,
        timezone: str,
        mss_lookback: int = 2,
        mss_displacement: float = 0.75,
        require_mss: bool = True,
        require_fvg_long: bool = False,
        require_fvg_short: bool = True,
        require_pd_confluence_short: bool = True,
        bull_min_conf: float = 0.60,
        bear_min_conf: float = 0.75,
        stop_buffer: float = 2.5,
        max_entry_extension_r: float = 0.5,
        require_demand_zone: bool = False,
        demand_zone_tolerance: float = 10.0,
    ) -> None:
        self.classifier = XAURegimeClassifier()
        self.timezone = timezone
        self.mss_lookback = mss_lookback
        self.mss_displacement = mss_displacement
        self.require_mss = require_mss
        self.require_fvg_long = require_fvg_long
        self.require_fvg_short = require_fvg_short
        self.require_pd_confluence_short = require_pd_confluence_short
        self.bull_min_conf = bull_min_conf
        self.bear_min_conf = bear_min_conf
        self.stop_buffer = stop_buffer
        self.max_entry_extension_r = max_entry_extension_r
        self.require_demand_zone = require_demand_zone
        self.demand_zone_tolerance = demand_zone_tolerance

    def evaluate(
        self,
        symbol: str,
        intraday_candles: List[Candle],
        daily_xau: List[Candle],
        daily_dxy: Optional[List[Candle]] = None,
    ) -> dict:
        regime = self.classifier.classify(daily_xau, daily_dxy)
        demand_zone = detect_recent_demand_zone(daily_xau)

        # ---- Bull trend branch ----
        if regime.regime == "bull_trend":
            if regime.confidence < self.bull_min_conf:
                return {
                    "ok": False,
                    "reason": f"router blocked low bull confidence={regime.confidence:.2f}",
                    "direction": None,
                    "entry": None,
                    "stop": None,
                    "target": None,
                    "score": 0.0,
                    "regime": regime.regime,
                    "regime_confidence": regime.confidence,
                    "regime_features": asdict(regime),
                }

            strat = XAUNDOGAsiaStrategy(
                timezone=self.timezone,
                mss_lookback=self.mss_lookback,
                mss_displacement=self.mss_displacement,
                require_mss=self.require_mss,
                require_fvg=self.require_fvg_long,
                daily_bias="bullish",
                stop_buffer=self.stop_buffer,
                max_entry_extension_r=self.max_entry_extension_r,
                demand_zone=demand_zone,
                require_demand_zone=self.require_demand_zone,
                demand_zone_tolerance=self.demand_zone_tolerance,
            )
            setup = strat.evaluate_setup(symbol, intraday_candles)
            setup["regime"] = regime.regime
            setup["regime_confidence"] = regime.confidence
            setup["regime_features"] = asdict(regime)
            return setup

        # ---- Bear trend branch ----
        if regime.regime == "bear_trend":
            if regime.confidence < self.bear_min_conf:
                return {
                    "ok": False,
                    "reason": f"router blocked low bear confidence={regime.confidence:.2f}",
                    "direction": None,
                    "entry": None,
                    "stop": None,
                    "target": None,
                    "score": 0.0,
                    "regime": regime.regime,
                    "regime_confidence": regime.confidence,
                    "regime_features": asdict(regime),
                }

            strat = XAUNDOGAsiaShortStrategy(
                timezone=self.timezone,
                mss_lookback=self.mss_lookback,
                mss_displacement=self.mss_displacement,
                require_mss=self.require_mss,
                require_fvg=self.require_fvg_short,
                daily_bias="bearish",
                require_pd_confluence=self.require_pd_confluence_short,
                stop_buffer=self.stop_buffer,
                max_entry_extension_r=self.max_entry_extension_r,
                demand_zone=demand_zone,
                require_demand_zone=False,
                demand_zone_tolerance=self.demand_zone_tolerance,
            )
            setup = strat.evaluate_setup(symbol, intraday_candles)
            setup["regime"] = regime.regime
            setup["regime_confidence"] = regime.confidence
            setup["regime_features"] = asdict(regime)
            return setup

        return {
            "ok": False,
            "reason": f"router blocked regime={regime.regime}",
            "direction": None,
            "entry": None,
            "stop": None,
            "target": None,
            "score": 0.0,
            "regime": regime.regime,
            "regime_confidence": regime.confidence,
            "regime_features": asdict(regime),
        }
