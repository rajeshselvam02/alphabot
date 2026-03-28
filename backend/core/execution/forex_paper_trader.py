"""
Forex Paper Trader
Pip-based P&L calculation + standard lot sizing for forex and XAU/USD.
Mirrors paper_trader.py interface so engine.py can treat both identically.

Lot sizing:
  - Standard lot  = 100,000 units
  - Mini lot      = 10,000 units   ← default for demo
  - Micro lot     = 1,000 units

Pip value (per mini lot):
  - USD quoted pairs (EUR/USD, GBP/USD, AUD/USD): $1.00/pip
  - JPY pairs (USD/JPY): ~$0.91/pip  (recalculated per trade)
  - CHF pairs (USD/CHF): ~$1.00/pip
  - CAD pairs (USD/CAD): ~$0.77/pip
  - XAU/USD: $1.00 per $0.01 move (not pip-based)
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Optional
from backend.db.redis_client import redis_client

logger = logging.getLogger("alphabot.forex_paper_trader")

# Pip size per instrument
PIP_SIZE = {
    "EURUSD": 0.0001,
    "GBPUSD": 0.0001,
    "AUDUSD": 0.0001,
    "USDCAD": 0.0001,
    "USDCHF": 0.0001,
    "USDJPY": 0.01,
    "XAUUSD": 0.01,   # Gold: $0.01 per unit
}

# USD value per pip per mini lot (10,000 units)
# For simplicity we use fixed pip values (good enough for paper trading)
PIP_VALUE_USD = {
    "EURUSD": 1.00,
    "GBPUSD": 1.00,
    "AUDUSD": 1.00,
    "USDCAD": 0.77,
    "USDCHF": 1.00,
    "USDJPY": 0.91,
    "XAUUSD": 10.00,  # $10 per $0.10 move (1 lot = 100 oz)
}

LOT_SIZE = 10_000   # mini lot units


class ForexPaperTrader:
    """
    Paper trading engine for forex + XAU/USD.
    Tracks positions in lot units, calculates P&L in USD pips.
    """

    def __init__(self, initial_capital: float = 10_000.0):
        self.initial_capital = initial_capital
        self.equity          = initial_capital
        self.cash            = initial_capital
        self.positions: Dict[str, dict]  = {}
        self.trades:    list             = []
        self._prices:   Dict[str, float] = {}

    # ── Execution ──────────────────────────────────────────────

    async def execute(
        self,
        symbol:   str,
        side:     str,          # "buy" | "sell"
        lots:     float,        # number of mini lots (e.g. 0.1, 0.5, 1.0)
        price:    float,
        strategy: str,
        meta:     dict = None,
    ) -> Optional[dict]:
        """Open or close a forex position."""
        meta = meta or {}
        key  = symbol.replace("/", "")

        # ── Closing an existing position ───────────────────────
        if key in self.positions:
            pos  = self.positions[key]
            # Only close if direction reverses or explicit exit
            closing = (
                (pos["side"] == "long"  and side == "sell") or
                (pos["side"] == "short" and side == "buy")  or
                meta.get("reason") == "exit"
            )
            if closing:
                pnl = self._calc_pnl(key, pos, price)
                self.equity += pnl
                self.cash   += pnl

                trade = {
                    "symbol":    key,
                    "side":      "close",
                    "lots":      pos["lots"],
                    "entry":     pos["entry_price"],
                    "exit":      price,
                    "pnl_usd":   round(pnl, 4),
                    "pnl_pips":  round(self._pnl_pips(key, pos["entry_price"], price, pos["side"]), 1),
                    "strategy":  strategy,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self.trades.append(trade)
                del self.positions[key]

                logger.info(
                    f"[FOREX CLOSE] {key} {pos['side']} {pos['lots']} lots "
                    f"@ {price:.5f} | PnL: ${pnl:+.2f} ({trade['pnl_pips']:+.1f} pips)"
                )
                await self._save_state()
                return trade

        # ── Opening a new position ─────────────────────────────
        pos_side = "long" if side == "buy" else "short"
        margin   = self._required_margin(key, lots, price)

        if margin > self.cash * 0.5:
            logger.warning(f"[FOREX] Insufficient margin for {key} — need ${margin:.2f}")
            return None

        self.positions[key] = {
            "symbol":      key,
            "side":        pos_side,
            "lots":        lots,
            "entry_price": price,
            "margin":      margin,
            "strategy":    strategy,
            "open_time":   datetime.now(timezone.utc).isoformat(),
        }
        self.cash -= margin

        logger.info(
            f"[FOREX OPEN] {key} {pos_side} {lots} lots @ {price:.5f} "
            f"| Margin: ${margin:.2f}"
        )
        await self._save_state()
        return self.positions[key]

    # ── Pricing ────────────────────────────────────────────────

    def update_prices(self, prices: dict):
        """Called by engine._price_update_loop() to refresh unrealized P&L."""
        self._prices.update(prices)
        self._update_equity()

    def _update_equity(self):
        """Recalculate equity from cash + unrealized P&L."""
        unrealized = 0.0
        for key, pos in self.positions.items():
            price = self._prices.get(key)
            if price:
                unrealized += self._calc_pnl(key, pos, price)
        self.equity = self.cash + unrealized

    # ── P&L Helpers ────────────────────────────────────────────

    def _calc_pnl(self, key: str, pos: dict, exit_price: float) -> float:
        """Calculate realized P&L in USD."""
        pips    = self._pnl_pips(key, pos["entry_price"], exit_price, pos["side"])
        pip_val = PIP_VALUE_USD.get(key, 1.0)
        return pips * pip_val * pos["lots"]

    def _pnl_pips(self, key: str, entry: float, exit_price: float, side: str) -> float:
        """Calculate P&L in pips."""
        pip = PIP_SIZE.get(key, 0.0001)
        if side == "long":
            return (exit_price - entry) / pip
        else:
            return (entry - exit_price) / pip

    def _required_margin(self, key: str, lots: float, price: float) -> float:
        """
        Margin = notional / leverage.
        Using 50:1 leverage (conservative for demo).
        """
        notional = lots * LOT_SIZE * price if key != "XAUUSD" else lots * 100 * price
        return notional / 50.0

    def required_margin(self, key: str, lots: float, price: float) -> float:
        return self._required_margin(key.replace("/", ""), lots, price)

    def lot_size_from_risk(self, key: str, price: float, risk_pct: float = 0.01) -> float:
        """
        Kelly-inspired lot sizing: risk X% of equity per trade.
        Returns lots rounded to nearest 0.01.
        """
        risk_usd = self.equity * risk_pct
        pip_val  = PIP_VALUE_USD.get(key, 1.0)
        stop_pips = 20   # assume 20-pip stop equivalent
        raw_lots  = risk_usd / (stop_pips * pip_val)
        # Clamp between 0.01 and 1.0 mini lots for demo safety
        return round(max(0.01, min(1.0, raw_lots)), 2)

    # ── State ──────────────────────────────────────────────────

    async def _save_state(self):
        state = {
            "equity":    self.equity,
            "cash":      self.cash,
            "positions": self.positions,
            "trades":    self.trades[-200:],  # keep last 200
        }
        await redis_client.set("forex:paper_state", state)

    async def load_state(self):
        state = await redis_client.get("forex:paper_state")
        if isinstance(state, str):
            import json
            state = json.loads(state)
        if state:
            try:
                self.equity    = state.get("equity",    self.initial_capital)
                self.cash      = state.get("cash",      self.initial_capital)
                self.positions = state.get("positions", {})
                self.trades    = state.get("trades",    [])
                logger.info(f"[FOREX] State restored — equity=${self.equity:,.2f}, "
                            f"{len(self.positions)} open positions")
            except Exception as e:
                logger.warning(f"[FOREX] State restore failed: {e}")

    def summary(self) -> dict:
        wins  = [t for t in self.trades if t.get("pnl_usd", 0) > 0]
        total = len(self.trades)
        return {
            "equity":       round(self.equity, 2),
            "cash":         round(self.cash, 2),
            "return_pct":   round((self.equity - self.initial_capital) / self.initial_capital * 100, 3),
            "open_positions": len(self.positions),
            "total_trades": total,
            "win_rate":     round(len(wins) / total * 100, 1) if total else 0,
            "positions":    self.positions,
        }


forex_paper_trader = ForexPaperTrader()
