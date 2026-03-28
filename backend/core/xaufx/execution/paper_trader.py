from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..models import Fill, PortfolioState, Position
from .spread_model import SpreadModel


class XAUFXPaperTrader:
    def __init__(self, portfolio: PortfolioState, spread_model: SpreadModel) -> None:
        self.portfolio = portfolio
        self.spread_model = spread_model

    def open_position(
        self,
        symbol: str,
        side: str,
        qty: float,
        mid_price: float,
        spread: float,
        stop_price: Optional[float],
        target_price: Optional[float],
        strategy: str,
        ts: datetime,
    ) -> Optional[Fill]:
        if qty <= 0:
            return None
        if symbol in self.portfolio.positions:
            return None
        if not self.spread_model.allowed(symbol, spread):
            return None

        fill_price = self.spread_model.apply_slippage(side, mid_price, spread)
        self.portfolio.positions[symbol] = Position(
            symbol=symbol,
            side=side,
            qty=qty,
            entry_price=fill_price,
            stop_price=stop_price,
            target_price=target_price,
            opened_at=ts,
        )
        self.portfolio.trades_today += 1

        return Fill(
            symbol=symbol,
            side=side,
            qty=qty,
            price=fill_price,
            ts=ts,
            strategy=strategy,
        )

    def close_position(
        self,
        symbol: str,
        mid_price: float,
        spread: float,
        strategy: str,
        ts: datetime,
    ) -> Optional[Fill]:
        pos = self.portfolio.positions.get(symbol)
        if pos is None:
            return None

        closing_side = "SELL" if pos.side == "BUY" else "BUY"
        fill_price = self.spread_model.apply_slippage(closing_side, mid_price, spread)

        pnl = self._calc_pnl(pos.side, pos.qty, pos.entry_price, fill_price)
        self.portfolio.realized_pnl += pnl
        self.portfolio.equity += pnl
        self.portfolio.cash += pnl
        del self.portfolio.positions[symbol]

        return Fill(
            symbol=symbol,
            side=closing_side,
            qty=pos.qty,
            price=fill_price,
            ts=ts,
            strategy=strategy,
        )

    @staticmethod
    def _calc_pnl(side: str, qty: float, entry: float, exit_price: float) -> float:
        if side == "BUY":
            return (exit_price - entry) * qty
        return (entry - exit_price) * qty

    def mark_to_market(self, symbol: str, mid_price: float) -> float:
        pos = self.portfolio.positions.get(symbol)
        if pos is None:
            return 0.0
        unrealized = self._calc_pnl(pos.side, pos.qty, pos.entry_price, mid_price)
        return unrealized
