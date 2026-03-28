
from __future__ import annotations

from datetime import datetime, timezone

from .config import XAUFXConfig
from .data_feeds.twelvedata_feed import TwelveDataFeed, TwelveDataQuotaExceeded
from .execution.paper_trader import XAUFXPaperTrader
from .execution.position_sizer import PositionSizer
from .execution.risk_manager import XAUFXRiskManager
from .execution.spread_model import SpreadModel
from .models import PortfolioState
from .strategies.xau_daily_momentum import XAUDailyMomentumStrategy
from .strategies.xau_ndog_asia import XAUNDOGAsiaStrategy
from .analytics.performance import summarize_equity


class XAUFXEngine:
    def __init__(self):
        self.config = XAUFXConfig()
        self.feed = TwelveDataFeed(self.config.twelvedata_api_key)

        self.portfolio = PortfolioState(
            starting_equity=self.config.capital,
            equity=self.config.capital,
            cash=self.config.capital,
        )

        self.paper = XAUFXPaperTrader(self.portfolio, SpreadModel())
        self.sizer = PositionSizer(self.config.risk_per_trade_pct)
        self.risk = XAUFXRiskManager(self.config.max_daily_loss_pct, self.config.max_session_trades)

        self.daily = XAUDailyMomentumStrategy()
        self.ndog = XAUNDOGAsiaStrategy()

    def run_once(self):
        try:
            daily = []  # mock
            intraday = []  # mock
        except TwelveDataQuotaExceeded as e:
            self.portfolio.halted = True
            return {"error": str(e)}

        d_sig = self.daily.generate("XAUUSD", daily)
        i_sig = self.ndog.generate("XAUUSD", intraday)

        return {
            "portfolio": summarize_equity(self.portfolio),
            "daily": d_sig.side,
            "intraday": i_sig.side
        }
