from __future__ import annotations

from typing import Dict

from ..models import PortfolioState


def summarize_equity(portfolio: PortfolioState) -> Dict[str, float | int | bool | str]:
    total_return_pct = 0.0
    if portfolio.starting_equity > 0:
        total_return_pct = (portfolio.equity - portfolio.starting_equity) / portfolio.starting_equity * 100.0

    return {
        "starting_equity": portfolio.starting_equity,
        "equity": portfolio.equity,
        "cash": portfolio.cash,
        "realized_pnl": portfolio.realized_pnl,
        "unrealized_pnl": portfolio.unrealized_pnl,
        "open_positions": len(portfolio.positions),
        "trades_today": portfolio.trades_today,
        "halted": portfolio.halted,
        "halt_reason": portfolio.halt_reason,
        "total_return_pct": total_return_pct,
    }
