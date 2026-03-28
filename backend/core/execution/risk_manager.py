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
    budget_remaining: float = 0.0
    strategy_budget_pct: float = 0.0
    asset_budget_pct: float = 0.0


class RiskManager:
    def __init__(self):
        self.equity             = settings.INITIAL_CAPITAL
        self.peak_equity        = settings.INITIAL_CAPITAL
        self.daily_start_equity = settings.INITIAL_CAPITAL
        self.is_halted          = False
        self.halt_reason        = ""
        self._daily_returns     = []
        self._trade_count       = 0
        self._last_budget_state = {}

        # Risk budgets: strategy and asset-class sleeves.
        self._strategy_budgets = {
            "bollinger_mr": 0.35,
            "cross_sectional": 0.30,
            "forex_mr": 0.25,
        }
        self._asset_budgets = {
            "crypto": 0.65,
            "forex": 0.30,
            "metal": 0.20,
            "other": 0.25,
        }

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

    def _budget_scale(self) -> float:
        scale = 1.0
        if self.drawdown >= settings.MAX_DRAWDOWN_HALT * 0.5:
            scale *= 0.5
        elif self.drawdown >= settings.MAX_DRAWDOWN_HALT * 0.25:
            scale *= 0.75

        if self.daily_loss >= settings.MAX_DAILY_LOSS_PCT * 0.5:
            scale *= 0.5
        elif self.daily_loss >= settings.MAX_DAILY_LOSS_PCT * 0.25:
            scale *= 0.75
        return max(0.25, min(1.0, scale))

    def _iter_positions(self, open_positions):
        if isinstance(open_positions, dict):
            return open_positions.values()
        if isinstance(open_positions, list):
            return open_positions
        return []

    def _position_value(self, pos: dict) -> float:
        if not isinstance(pos, dict):
            return 0.0
        if "margin" in pos:
            return abs(float(pos.get("margin", 0.0)))
        qty = float(pos.get("quantity", 0.0))
        px = float(pos.get("current_price", pos.get("entry_price", 0.0)) or 0.0)
        if qty and px:
            return abs(qty * px)
        lots = float(pos.get("lots", 0.0))
        margin = float(pos.get("margin", 0.0) or 0.0)
        if margin:
            return abs(margin)
        return abs(lots)

    def _strategy_exposure(self, open_positions, strategy: str) -> float:
        return sum(
            self._position_value(pos)
            for pos in self._iter_positions(open_positions)
            if isinstance(pos, dict) and pos.get("strategy") == strategy
        )

    def _asset_exposure(self, open_positions, asset_class: str) -> float:
        target = asset_class or "other"
        total = 0.0
        for pos in self._iter_positions(open_positions):
            if not isinstance(pos, dict):
                continue
            symbol = pos.get("symbol", "")
            if self._classify_asset(symbol) == target:
                total += self._position_value(pos)
        return total

    def _symbol_exposure(self, open_positions, symbol: str) -> float:
        return sum(
            self._position_value(pos)
            for pos in self._iter_positions(open_positions)
            if isinstance(pos, dict) and pos.get("symbol") == symbol
        )

    def _classify_asset(self, symbol: str) -> str:
        sym = (symbol or "").upper()
        if sym == "XAUUSD":
            return "metal"
        if any(sym.endswith(suffix) for suffix in ("USD", "JPY", "CHF", "CAD")) and len(sym) <= 7:
            return "forex"
        return "crypto" if sym.endswith("USDT") else "other"

    def _effective_budget(self, strategy: str, asset_class: str):
        scale = self._budget_scale()
        strategy_budget = self._strategy_budgets.get(strategy or "", 0.20) * scale
        asset_budget = self._asset_budgets.get(asset_class or "other", self._asset_budgets["other"]) * scale
        return strategy_budget, asset_budget, scale

    def check(self, symbol, side, quantity, price,
              open_positions, is_mean_reversion=True, strategy: str = "",
              asset_class: Optional[str] = None, conviction: float = 1.0,
              trade_value_override: Optional[float] = None) -> TradeDecision:
        if self.is_halted:
            return TradeDecision(False, f"Halted: {self.halt_reason}")
        if self.check_halts():
            return TradeDecision(False, self.halt_reason)
        if self.equity <= 0:
            return TradeDecision(False, "Equity exhausted")
        trade_value = float(trade_value_override if trade_value_override is not None else quantity * price)
        asset_class = asset_class or self._classify_asset(symbol)
        strategy_budget, asset_budget, scale = self._effective_budget(strategy, asset_class)
        exposure = sum(self._position_value(p) for p in self._iter_positions(open_positions))
        strategy_exposure = self._strategy_exposure(open_positions, strategy) if strategy else 0.0
        asset_exposure = self._asset_exposure(open_positions, asset_class)
        symbol_exposure = self._symbol_exposure(open_positions, symbol)

        global_budget_value = self.equity * settings.MAX_LEVERAGE
        strategy_budget_value = self.equity * strategy_budget
        asset_budget_value = self.equity * asset_budget
        conviction = max(0.5, min(1.5, conviction))
        symbol_cap_pct = min(settings.MAX_POSITION_PCT, strategy_budget * 0.60 * conviction)
        symbol_cap_value = self.equity * symbol_cap_pct

        allowed_trade_value = min(
            global_budget_value - exposure,
            strategy_budget_value - strategy_exposure if strategy else global_budget_value - exposure,
            asset_budget_value - asset_exposure,
            symbol_cap_value - symbol_exposure,
        )
        self._last_budget_state = {
            "scale": round(scale, 4),
            "strategy": strategy or "default",
            "asset_class": asset_class,
            "strategy_budget_pct": round(strategy_budget, 4),
            "asset_budget_pct": round(asset_budget, 4),
            "strategy_exposure": round(strategy_exposure, 2),
            "asset_exposure": round(asset_exposure, 2),
            "symbol_exposure": round(symbol_exposure, 2),
            "global_exposure": round(exposure, 2),
        }

        if allowed_trade_value <= 0:
            return TradeDecision(
                False,
                f"Risk budget exhausted for {strategy or asset_class}",
                strategy_budget_pct=strategy_budget,
                asset_budget_pct=asset_budget,
            )

        if trade_value > allowed_trade_value:
            if trade_value <= 0:
                return TradeDecision(False, "Invalid trade value")
            scale_down = allowed_trade_value / trade_value
            quantity *= scale_down
            trade_value = allowed_trade_value

        if exposure + trade_value > global_budget_value:
            return TradeDecision(False, "Max leverage exceeded")

        if quantity <= 0:
            return TradeDecision(False, "Budget reduced trade below minimum size")
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
            budget_remaining=max(0.0, allowed_trade_value - trade_value),
            strategy_budget_pct=strategy_budget,
            asset_budget_pct=asset_budget,
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
            "budget":      self._last_budget_state,
            "prob_loss":   prob_of_loss(
                sharpe=self.sharpe_ratio() or 0.0,
                volatility=var.get("daily_vol", 0.02),
                horizon_days=30,
                loss_threshold=0.05,
            ),
        }

risk_manager = RiskManager()
