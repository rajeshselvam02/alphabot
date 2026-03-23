from dataclasses import dataclass
from typing import Optional
import logging
import numpy as np
from backend.config.settings import settings
from backend.core.signals.quant_signals import jump_diffusion_var, prob_of_loss, zscore_to_prob, prob_bet_size

logger = logging.getLogger("alphabot.risk")


@dataclass
class TradeDecision:
    approved: bool
    reason: str = ""
    adjusted_qty: float = 0.0
    stop_price: Optional[float] = None
    risk_pct: float = 0.0


class RiskManager:
    def __init__(self):
        self.equity             = settings.INITIAL_CAPITAL
        self.peak_equity        = settings.INITIAL_CAPITAL
        self.daily_start_equity = settings.INITIAL_CAPITAL
        self.is_halted          = False
        self.halt_reason        = ""
        self._daily_returns     = []
        self._trade_count       = 0

    def update_equity(self, new_equity: float):
        if new_equity <= 0:
            return
        self.equity = new_equity
        if new_equity > self.peak_equity:
            self.peak_equity = new_equity

    def reset_peak(self):
        self.peak_equity        = self.equity
        self.daily_start_equity = self.equity
        logger.info(f"Peak reset to ${self.equity:,.2f}")

    def daily_close(self, closing_equity: float):
        if self.daily_start_equity > 0:
            r = (closing_equity - self.daily_start_equity) / self.daily_start_equity
            self._daily_returns.append(r)
            if len(self._daily_returns) > 252:
                self._daily_returns.pop(0)
        self.daily_start_equity = closing_equity

    @property
    def drawdown(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity)

    @property
    def daily_loss(self) -> float:
        if self.daily_start_equity <= 0:
            return 0.0
        return max(0.0, (self.daily_start_equity - self.equity) / self.daily_start_equity)

    def check_halts(self) -> bool:
        if self.drawdown >= settings.MAX_DRAWDOWN_HALT:
            self._halt(f"Max drawdown {self.drawdown:.1%}")
            return True
        if self.daily_loss >= settings.MAX_DAILY_LOSS_PCT:
            self._halt(f"Daily loss {self.daily_loss:.1%}")
            return True
        return False

    def _halt(self, reason: str):
        if not self.is_halted:
            self.is_halted   = True
            self.halt_reason = reason
            logger.critical(f"RISK HALT: {reason}")
            # Persist halt to Redis so restart does not auto-resume
            try:
                import asyncio, json
                from datetime import datetime, timezone
                from backend.db.redis_client import redis_client
                halt_data = json.dumps({
                    "reason": reason,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                asyncio.create_task(redis_client.set("risk:halted", halt_data))
            except Exception as e:
                logger.error(f"[RISK] Failed to persist halt: {e}")

    def resume(self):
        self.is_halted   = False
        self.halt_reason = ""
        logger.info("Risk halt cleared")
        # Clear halt from Redis
        try:
            import asyncio
            from backend.db.redis_client import redis_client
            asyncio.create_task(redis_client.delete("risk:halted"))
        except Exception:
            pass

    def zscore_size(self, zscore: float, price: float, prob: float = None) -> float:
        """Probability-based bet sizing (Lopez de Prado Ch.10)."""
        if price <= 0:
            return 0.0
        if prob is None:
            prob = zscore_to_prob(zscore)
        pct = prob_bet_size(
            prob_correct=prob,
            max_bet_pct=settings.MAX_POSITION_PCT,
            kelly_fraction=settings.KELLY_FRACTION,
        )
        if pct <= 0:
            return 0.0
        dollars = pct * self.equity
        return round(dollars / price, 6)

    def check(self, symbol, side, quantity, price,
              open_positions, is_mean_reversion=True) -> TradeDecision:
        if self.is_halted:
            return TradeDecision(False, f"Halted: {self.halt_reason}")
        if self.check_halts():
            return TradeDecision(False, self.halt_reason)
        if self.equity <= 0:
            return TradeDecision(False, "Equity exhausted")
        trade_value = quantity * price
        max_value   = self.equity * settings.MAX_POSITION_PCT
        if trade_value > max_value:
            quantity    = max_value / price
            trade_value = max_value
        exposure = sum(
            abs(p.get("quantity", 0)) * p.get("current_price", p.get("entry_price", 0))
            for p in open_positions.values()
        )
        if exposure + trade_value > self.equity * settings.MAX_LEVERAGE:
            return TradeDecision(False, "Max leverage exceeded")
        stop_price = None
        if not is_mean_reversion:
            stop_price = price * (1 - settings.MOMENTUM_STOP_PCT) if side == "buy" \
                    else price * (1 + settings.MOMENTUM_STOP_PCT)
        self._trade_count += 1
        return TradeDecision(
            approved=True, reason="OK",
            adjusted_qty=round(quantity, 6),
            stop_price=stop_price,
            risk_pct=trade_value / self.equity if self.equity > 0 else 0,
        )

    def sharpe_ratio(self) -> Optional[float]:
        if len(self._daily_returns) < 10:
            return None
        r = np.array(self._daily_returns)
        if r.std() < 1e-10:
            return None
        return float(r.mean() / r.std() * np.sqrt(252))

    def to_dict(self) -> dict:
        var = jump_diffusion_var(self.equity)
        return {
            "equity":      round(self.equity, 2),
            "peak_equity": round(self.peak_equity, 2),
            "drawdown":    round(self.drawdown, 4),
            "daily_loss":  round(self.daily_loss, 4),
            "is_halted":   self.is_halted,
            "halt_reason": self.halt_reason,
            "sharpe":      self.sharpe_ratio(),
            "trade_count": self._trade_count,
            "var_24h":     var["var_total"],
            "var_pct":     var["var_pct"],
            "var_jump":    var["var_jump"],
            "prob_loss":   prob_of_loss(
                sharpe=self.sharpe_ratio() or 0.0,
                volatility=var.get("daily_vol", 0.02),
                horizon_days=30,
                loss_threshold=0.05,
            ),
        }

risk_manager = RiskManager()
