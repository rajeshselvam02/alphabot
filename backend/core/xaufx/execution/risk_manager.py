from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskCheckResult:
    allowed: bool
    reason: str = ""


class XAUFXRiskManager:
    def __init__(self, max_daily_loss_pct: float, max_session_trades: int) -> None:
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_session_trades = max_session_trades

    def can_open(self, starting_equity: float, equity: float, trades_today: int, halted: bool) -> RiskCheckResult:
        if halted:
            return RiskCheckResult(False, "system halted")
        if trades_today >= self.max_session_trades:
            return RiskCheckResult(False, "max session trades reached")

        drawdown_pct = (starting_equity - equity) / starting_equity if starting_equity > 0 else 0.0
        if drawdown_pct >= self.max_daily_loss_pct:
            return RiskCheckResult(False, "max daily loss reached")

        return RiskCheckResult(True, "")
