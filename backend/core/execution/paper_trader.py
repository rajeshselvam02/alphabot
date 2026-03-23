"""
Paper Trader — Simulated order execution for paper trading mode.
Tracks virtual portfolio with slippage simulation.
All state kept in memory + Redis.
"""
import asyncio
import uuid
from backend.core.notifications.telegram_bot import telegram
import logging
from datetime import datetime, timezone
from typing import Dict, Optional
from backend.config.settings import settings
from backend.db.redis_client import redis_client
from backend.core.execution.risk_manager import risk_manager

logger = logging.getLogger("alphabot.paper")

SLIPPAGE = 0.0008   # 0.08% market order slippage (realistic for crypto)
TAKER_FEE = 0.001   # 0.1% Binance taker fee


class PaperTrader:
    """
    Simulates a real trading account without touching real money.
    
    Features:
    - Slippage simulation (0.08%)
    - Exchange fee simulation (0.1%)
    - Position tracking with unrealized P&L
    - Trade history
    - Real-time equity calculation
    """

    def __init__(self):
        self.cash:       float              = settings.INITIAL_CAPITAL
        self.equity:     float              = settings.INITIAL_CAPITAL
        self.positions:  Dict[str, dict]    = {}
        self.trades:     list               = []
        self.total_fees: float              = 0.0
        self.wins:       int                = 0
        self.losses:     int                = 0

    # ── Order execution ───────────────────────────────────────────

    async def execute(
        self,
        symbol:      str,
        side:        str,        # "buy" | "sell"
        quantity:    float,
        price:       float,
        strategy:    str,
        signal_data: dict = None,
        stop_price:  float = None,
    ) -> dict:
        """
        Simulate a market order fill with slippage + fee.
        Returns the trade record.
        """
        # Apply slippage
        if side == "buy":
            fill_price = price * (1 + SLIPPAGE)
        else:
            fill_price = price * (1 - SLIPPAGE)

        # Apply fee
        fee         = quantity * fill_price * TAKER_FEE
        trade_value = quantity * fill_price
        self.total_fees += fee
        trade_id    = str(uuid.uuid4())[:8]
        now         = datetime.now(timezone.utc)

        pnl = None

        if side == "buy":
            # ── Open or add to long position ──────────────────────
            cost = trade_value + fee
            if cost > self.cash:
                quantity    = (self.cash - fee) / fill_price
                trade_value = quantity * fill_price
                cost        = trade_value + fee
                if quantity <= 0:
                    logger.warning(f"[PAPER] Insufficient cash for {symbol} buy")
                    return {}

            self.cash -= cost

            if symbol in self.positions:
                pos  = self.positions[symbol]
                tq   = pos["quantity"] + quantity
                avg  = (pos["entry_price"] * pos["quantity"] + fill_price * quantity) / tq
                pos["quantity"]    = tq
                pos["entry_price"] = avg
            else:
                self.positions[symbol] = {
                    "id":            trade_id,
                    "symbol":        symbol,
                    "strategy":      strategy,
                    "side":          "long",
                    "quantity":      quantity,
                    "entry_price":   fill_price,
                    "current_price": fill_price,
                    "entry_time":    now.isoformat(),
                    "stop_price":    stop_price,
                    "unrealized_pnl": 0.0,
                    "unrealized_pct": 0.0,
                    "signal_data":   signal_data or {},
                }
                sd = signal_data or {}
                asyncio.create_task(telegram.alert_trade_open(
                    side=side, symbol=symbol, price=fill_price, qty=quantity,
                    zscore=sd.get("zscore", 0.0),
                    tp=sd.get("take_profit", 0.0) or 0.0,
                    sl=sd.get("stop_loss", 0.0) or 0.0
                ))

        else:
            # ── Close position (or open short) ────────────────────
            if symbol in self.positions:
                pos      = self.positions[symbol]
                close_qty = min(quantity, pos["quantity"])
                if pos["side"] == "long":
                    gross_pnl = (fill_price - pos["entry_price"]) * close_qty
                else:  # short position
                    gross_pnl = (pos["entry_price"] - fill_price) * close_qty
                pnl       = gross_pnl - fee
                self.cash += close_qty * fill_price - fee

                if pnl > 0:
                    self.wins  += 1
                else:
                    self.losses += 1

                if close_qty >= pos["quantity"]:
                    del self.positions[symbol]
                    logger.info(f"[PAPER] {symbol} closed. PnL: ${pnl:+.2f}")
                    asyncio.create_task(telegram.alert_trade_close(symbol=symbol, price=fill_price, qty=close_qty, pnl=pnl, reason="signal"))
                else:
                    pos["quantity"] -= close_qty
            else:
                # Short position (no existing long)
                self.cash += trade_value - fee
                self.positions[symbol] = {
                    "id":            trade_id,
                    "symbol":        symbol,
                    "strategy":      strategy,
                    "side":          "short",
                    "quantity":      quantity,
                    "entry_price":   fill_price,
                    "current_price": fill_price,
                    "entry_time":    now.isoformat(),
                    "stop_price":    stop_price,
                    "unrealized_pnl": 0.0,
                    "unrealized_pct": 0.0,
                    "signal_data":   signal_data or {},
                }

        self._recalc_equity()
        risk_manager.update_equity(self.equity)

        trade = {
            "id":          trade_id,
            "symbol":      symbol,
            "strategy":    strategy,
            "side":        side,
            "quantity":    round(quantity, 6),
            "fill_price":  round(fill_price, 6),
            "fee":         round(fee, 4),
            "trade_value": round(trade_value, 2),
            "pnl":         round(pnl, 2) if pnl is not None else None,
            "timestamp":   now.isoformat(),
            "mode":        "paper",
        }
        self.trades.append(trade)
        if len(self.trades) > 1000:            # keep last 1000
            self.trades.pop(0)

        # Push to Redis for dashboard
        await redis_client.publish("trades", trade)
        await self._push_state()

        logger.info(
            f"[PAPER] {'BUY ' if side == 'buy' else 'SELL'} "
            f"{quantity:.4f} {symbol} @ ${fill_price:.4f} "
            f"| fee: ${fee:.3f}"
            f"{f' | PnL: ${pnl:+.2f}' if pnl is not None else ''}"
        )
        return trade

    # ── Price updates (call from engine loop) ─────────────────────

    def update_prices(self, prices: dict):
        """Update unrealized P&L for all open positions."""
        for sym, price in prices.items():
            if sym in self.positions:
                pos = self.positions[sym]
                pos["current_price"] = price
                qty = pos["quantity"]
                if pos["side"] == "long":
                    pos["unrealized_pnl"] = (price - pos["entry_price"]) * qty
                else:
                    pos["unrealized_pnl"] = (pos["entry_price"] - price) * qty
                if pos["entry_price"] > 0:
                    pos["unrealized_pct"] = pos["unrealized_pnl"] / (pos["entry_price"] * qty)
        self._recalc_equity()

    def _recalc_equity(self):
        pos_value = sum(
            p["quantity"] * p["current_price"]
            for p in self.positions.values()
            if p["side"] == "long"
        )
        self.equity = self.cash + pos_value

    # ── Portfolio summary ─────────────────────────────────────────

    def summary(self) -> dict:
        total      = self.wins + self.losses
        win_rate   = self.wins / total if total > 0 else 0
        total_ret  = (self.equity - settings.INITIAL_CAPITAL) / settings.INITIAL_CAPITAL
        unreal_pnl = sum(p["unrealized_pnl"] for p in self.positions.values())

        return {
            "equity":            round(self.equity, 2),
            "cash":              round(self.cash, 2),
            "initial_capital":   settings.INITIAL_CAPITAL,
            "total_return_pct":  round(total_ret * 100, 2),
            "unrealized_pnl":    round(unreal_pnl, 2),
            "total_fees":        round(self.total_fees, 2),
            "open_positions":    len(self.positions),
            "total_trades":      len(self.trades),
            "wins":              self.wins,
            "losses":            self.losses,
            "win_rate_pct":      round(win_rate * 100, 2),
            "drawdown_pct":      round(risk_manager.drawdown * 100, 2),
            "sharpe":            risk_manager.sharpe_ratio(),
            "mode":              "paper",
            "positions":         list(self.positions.values()),
        }

    async def _push_state(self):
        state = self.summary()
        await redis_client.set_portfolio(state)


paper_trader = PaperTrader()
