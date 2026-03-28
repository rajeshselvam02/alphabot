from __future__ import annotations

import os
from dotenv import load_dotenv
from dataclasses import dataclass, field
from typing import List

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value is not None else default


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value is not None else default


def _get_list(name: str, default: List[str]) -> List[str]:
    value = os.getenv(name)
    if not value:
        return default
    return [x.strip() for x in value.split(",") if x.strip()]


@dataclass
class XAUFXConfig:
    enabled: bool = field(default_factory=lambda: _get_bool("XAUFX_ENABLED", True))
    mode: str = field(default_factory=lambda: os.getenv("XAUFX_MODE", "paper"))
    capital: float = field(default_factory=lambda: _get_float("XAUFX_CAPITAL", 10000.0))

    provider: str = field(default_factory=lambda: os.getenv("XAUFX_DATA_PROVIDER", "TWELVEDATA"))
    twelvedata_api_key: str = field(default_factory=lambda: os.getenv("TWELVEDATA_API_KEY", ""))

    symbols: List[str] = field(default_factory=lambda: _get_list("XAU_SYMBOLS", ["XAUUSD"]))
    timezone: str = field(default_factory=lambda: os.getenv("NY_TIMEZONE", "America/New_York"))

    daily_interval: str = field(default_factory=lambda: os.getenv("XAU_DAILY_INTERVAL", "1day"))
    intraday_interval: str = field(default_factory=lambda: os.getenv("XAU_INTRADAY_INTERVAL", "1h"))

    fast_ma: int = field(default_factory=lambda: _get_int("XAU_DAILY_FAST_MA", 20))
    slow_ma: int = field(default_factory=lambda: _get_int("XAU_DAILY_SLOW_MA", 100))
    ravi_threshold: float = field(default_factory=lambda: _get_float("XAU_DAILY_RAVI", 5.0))
    adx_threshold: float = field(default_factory=lambda: _get_float("XAU_DAILY_ADX", 25.0))
    atr_mult: float = field(default_factory=lambda: _get_float("XAU_DAILY_ATR_MULT", 2.0))

    risk_per_trade_pct: float = field(default_factory=lambda: _get_float("XAUFX_MAX_RISK_PCT", 0.005))
    max_daily_loss_pct: float = field(default_factory=lambda: _get_float("XAUFX_MAX_DAILY_LOSS_PCT", 0.01))
    max_session_trades: int = field(default_factory=lambda: _get_int("XAUFX_MAX_SESSION_TRADES", 2))

    max_spread_xau: float = field(default_factory=lambda: _get_float("XAUFX_MAX_SPREAD_XAU", 1.0))
    max_spread_fx_pips: float = field(default_factory=lambda: _get_float("XAUFX_MAX_SPREAD_FX_PIPS", 2.0))

    def validate(self) -> None:
        if self.provider.upper() == "TWELVEDATA" and not self.twelvedata_api_key:
            raise ValueError("TWELVEDATA_API_KEY is required")
        if self.fast_ma >= self.slow_ma:
            raise ValueError("XAU_DAILY_FAST_MA must be less than XAU_DAILY_SLOW_MA")
        if self.capital <= 0:
            raise ValueError("XAUFX_CAPITAL must be > 0")
