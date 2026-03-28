from __future__ import annotations


class PositionSizer:
    def __init__(self, risk_per_trade_pct: float) -> None:
        self.risk_per_trade_pct = risk_per_trade_pct

    def size_from_stop_distance(self, equity: float, entry: float, stop: float) -> float:
        distance = abs(entry - stop)
        if distance <= 0:
            return 0.0
        risk_amount = equity * self.risk_per_trade_pct
        qty = risk_amount / distance
        return max(qty, 0.0)
