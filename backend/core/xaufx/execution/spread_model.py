from __future__ import annotations


class SpreadModel:
    def __init__(self, max_spread_xau: float = 1.0, max_spread_fx_pips: float = 2.0) -> None:
        self.max_spread_xau = max_spread_xau
        self.max_spread_fx_pips = max_spread_fx_pips

    def allowed(self, symbol: str, spread: float) -> bool:
        if symbol == "XAUUSD":
            return spread <= self.max_spread_xau
        return spread <= self.max_spread_fx_pips

    def apply_slippage(self, side: str, price: float, spread: float) -> float:
        half = spread / 2.0
        if side.upper() == "BUY":
            return price + half
        return price - half
