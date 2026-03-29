"""
Strategy 2: Cross-Sectional Momentum
════════════════════════════════════════════════════════
Theory base:
  - Khandani & Lo (2007) — original paper
  - Chan Ch.4: "completely linear, no parameters, dollar-neutral"
  - Platen Ch.10: GOP growth rate drives cross-sectional sorting

Signal logic (Khandani-Lo weights):
  wᵢ = -(rᵢ - mean(r)) / Σ|rᵢ - mean(r)|
  
  Buy recent underperformers, short recent outperformers.
  - Dollar neutral (Σwᵢ = 0)
  - Normalized (Σ|wᵢ| = 1)
  - No parameters to optimize → minimal data-snooping bias

For crypto (no shorting issues):
  - Run at each 1h bar
  - Enter positions in top/bottom N by relative return
  - Hold for HOLD_BARS bars, then rebalance
════════════════════════════════════════════════════════
"""
import logging
from typing import Dict, Optional
from backend.config.settings import settings
from backend.core.signals.quant_signals import cross_sectional_weights
from backend.core.execution.risk_manager import risk_manager
from backend.core.execution.paper_trader import paper_trader
from backend.db.redis_client import redis_client

logger = logging.getLogger("alphabot.cs_momentum")

HOLD_BARS = 24   # Hold positions for 24 bars (~24h at 1h interval)


class CrossSectionalStrategy:
    """
    Cross-sectional momentum: rank by relative return,
    buy losers (mean reversion) OR buy winners (momentum).
    
    The Khandani-Lo formulation is technically mean-reverting
    (short-term) — this is the dominant signal in crypto at 1h.
    """

    NAME = "cross_sectional"

    def __init__(self):
        self.is_active     = True
        self._prices:      Dict[str, list]  = {}
        self._bars:        Dict[str, int]   = {}
        self._positions:   Dict[str, str]   = {}  # symbol → "long"|"short"
        self._hold_counts: Dict[str, int]   = {}  # bars held
        self.trades_made   = 0
        self.bar_count     = 0
        self._universe     = settings.CS_UNIVERSE

    def _init(self, symbol: str):
        if symbol not in self._prices:
            self._prices[symbol]  = []
            self._bars[symbol]    = 0

    async def on_bar(self, bar: dict):
        """Called on each closed bar."""
        if not self.is_active:
            return

        symbol = bar["symbol"]
        if symbol not in self._universe:
            return

        self._init(symbol)
        self._prices[symbol].append(bar["close"])
        if len(self._prices[symbol]) > 200:
            self._prices[symbol].pop(0)
        self._bars[symbol]  += 1
        self.bar_count      += 1

        # Only rebalance when ALL symbols have enough data
        ready = [
            s for s in self._universe
            if s in self._prices and len(self._prices[s]) >= settings.CS_LOOKBACK + 1
        ]
        if len(ready) < max(2, len(self._universe) // 2):
            return

        # Run rebalance check every CS_LOOKBACK bars for lead symbol
        lead = self._universe[0]
        if self._bars.get(lead, 0) % settings.CS_LOOKBACK != 0:
            return

        await self._rebalance(ready)

    async def _rebalance(self, symbols: list):
        """Compute weights and adjust positions."""
        # Compute returns over lookback period
        returns = {}
        for sym in symbols:
            prices = self._prices[sym]
            if len(prices) >= settings.CS_LOOKBACK + 1:
                p_now  = prices[-1]
                p_prev = prices[-settings.CS_LOOKBACK - 1]
                if p_prev > 0:
                    returns[sym] = (p_now - p_prev) / p_prev

        if len(returns) < 2:
            return

        weights = cross_sectional_weights(returns)

        # Close positions we no longer want
        for sym in list(self._positions.keys()):
            if sym not in weights or abs(weights.get(sym, 0)) < 0.01:
                await self._close(sym)

        # Open/adjust new positions
        n    = settings.CS_TOP_N + settings.CS_BOTTOM_N
        ranked = sorted(weights.items(), key=lambda x: x[1])
        longs  = [s for s, w in ranked[-settings.CS_TOP_N:] if w < 0]   # underperformers
        shorts = [s for s, w in ranked[:settings.CS_BOTTOM_N] if w > 0] # outperformers

        for sym in longs:
            if sym not in self._positions:
                await self._open(sym, "buy", returns.get(sym, 0))
        for sym in shorts:
            if sym not in self._positions:
                await self._open(sym, "sell", returns.get(sym, 0))

        # Publish signal
        signal_payload = {
            "strategy": self.NAME,
            "weights":  {k: round(v, 4) for k, v in weights.items()},
            "longs":    longs,
            "shorts":   shorts,
            "returns":  {k: round(v, 4) for k, v in returns.items()},
        }
        await redis_client.set_signal(self.NAME, self.NAME, signal_payload)
        await redis_client.publish("signals", signal_payload)

    async def _open(self, symbol: str, side: str, ret: float):
        price = self._prices.get(symbol, [0])[-1]
        if price <= 0:
            return

        qty = risk_manager.zscore_size(abs(ret) / 0.02, price)  # normalize by 2% return
        if qty <= 0:
            return

        decision = risk_manager.check(
            symbol=symbol, side=side, quantity=qty, price=price,
            open_positions=paper_trader.positions, is_mean_reversion=False,
            strategy=self.NAME, asset_class="crypto",
            conviction=min(1.25, max(0.75, abs(ret) / 0.03))
        )
        if not decision.approved:
            return

        await paper_trader.execute(
            symbol, side, decision.adjusted_qty, price, self.NAME,
            {"return": round(ret, 4), "side": side},
            stop_price=decision.stop_price,
        )
        self._positions[symbol]   = "long" if side == "buy" else "short"
        self._hold_counts[symbol] = 0
        self.trades_made += 1

    async def _close(self, symbol: str):
        if symbol in self._positions:
            pos = paper_trader.positions.get(symbol)
            if pos:
                close_side = "sell" if pos["side"] == "long" else "buy"
                price = self._prices.get(symbol, [0])[-1]
                await paper_trader.execute(
                    symbol, close_side, pos["quantity"], price, self.NAME,
                    {"reason": "rebalance"}
                )
            del self._positions[symbol]
            self._hold_counts.pop(symbol, None)

    def get_stats(self) -> dict:
        return {
            "strategy":    self.NAME,
            "is_active":   self.is_active,
            "trades_made": self.trades_made,
            "bar_count":   self.bar_count,
            "positions":   dict(self._positions),
        }


cs_strategy = CrossSectionalStrategy()
