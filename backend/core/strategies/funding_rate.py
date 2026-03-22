"""
Strategy 3: Vasicek Funding Rate Mean Reversion
════════════════════════════════════════════════
Theory: Shreve Ch.10 — Term Structure Models
        Vasicek: dr = κ(θ-r)dt + σdW

Crypto perpetual futures have an 8-hour funding rate.
When funding is extreme → positions are overcrowded
→ mean reversion expected → trade the reversion.

Currently: Signal generator only (no live execution).
           Add futures broker to execute.
"""
import logging
from backend.core.signals.quant_signals import VasicekFundingModel
from backend.db.redis_client import redis_client

logger = logging.getLogger("alphabot.funding")


class FundingRateStrategy:

    NAME = "funding_rate_vasicek"

    def __init__(self):
        self.is_active = True
        self._models   = {}   # symbol → VasicekFundingModel
        self._signals  = {}
        self.bar_count = 0

    def _init(self, symbol: str):
        if symbol not in self._models:
            self._models[symbol] = VasicekFundingModel(lookback=72)

    async def on_funding_rate(self, symbol: str, rate: float):
        """Call this every 8 hours with new funding rate data."""
        if not self.is_active:
            return

        self._init(symbol)
        result = self._models[symbol].update(rate)
        self._signals[symbol] = result
        self.bar_count += 1

        if result["signal"] not in ("none", "hold"):
            logger.info(
                f"[FUNDING] {symbol} | rate={rate:.4%} | "
                f"z={result['zscore']:+.2f} | "
                f"signal={result['signal']} | "
                f"θ={result['theta']:.4%} | "
                f"HL={result['half_life']}h"
            )

        await redis_client.publish("signals", {
            "strategy": self.NAME,
            "symbol":   symbol,
            "rate":     rate,
            **result,
        })

        return result

    def get_stats(self) -> dict:
        return {
            "strategy":  self.NAME,
            "is_active": self.is_active,
            "bar_count": self.bar_count,
            "signals":   {
                sym: {
                    "zscore": s["zscore"],
                    "signal": s["signal"],
                    "rate":   s.get("rate", 0),
                    "theta":  s.get("theta", 0),
                }
                for sym, s in self._signals.items()
            },
        }


funding_strategy = FundingRateStrategy()
