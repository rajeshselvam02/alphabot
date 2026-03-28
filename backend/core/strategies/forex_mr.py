"""
Forex Mean Reversion Strategy
Bollinger Band + Kalman Filter Z-score on 1h bars.
Mirrors bollinger_mr.py interface for engine.py compatibility.

Adaptations vs crypto:
  1. Market hours guard — skip weekend bars (Sat/Sun UTC)
  2. Rollover guard — skip 21:45-22:15 UTC (daily rollover spread widening)
  3. Pip-based position sizing via forex_paper_trader
  4. XAU/USD gets wider Z threshold (2.5) — Gold trends more than FX

Filters active:
  1. RAVI regime filter (same as crypto — skip MR in trending markets)
  2. RSI filter — skip BUY RSI>70, skip SELL RSI<30
  3. BBW filter — skip when Bollinger Band Width too wide (trending)
  4. Market hours filter — skip weekends + rollover window
"""
import logging
import statistics
from datetime import datetime, timezone
from typing import Dict, List, Optional
from backend.config.settings import settings
from backend.core.signals.quant_signals import (
    KalmanFilter,
    compute_ravi_series,
    compute_rsi,
)
from backend.core.execution.risk_manager import risk_manager
from backend.core.execution.forex_paper_trader import forex_paper_trader
from backend.db.redis_client import redis_client

logger = logging.getLogger("alphabot.forex_mr")

# XAU/USD uses wider thresholds (Gold is more volatile)
GOLD_ENTRY_Z = 2.5
GOLD_EXIT_Z  = 0.5

# Forex pairs use standard thresholds
FX_ENTRY_Z = 2.0
FX_EXIT_Z  = 0.0

LOOKBACK    = 20    # bars for rolling stats
BBW_LIMIT   = 0.015 # skip when BBW > 1.5% of price (adapted for FX scale)
RSI_PERIOD  = 14
RAVI_THRESH = 3.5   # % threshold for trend/MR regime
RANGE_SHOCK_MULTIPLIER = 2.5
RETURN_SHOCK_MULTIPLIER = 2.25
QUALITY_LOOKBACK = 24


class ForexMRStrategy:
    """
    Bollinger MR for forex + XAU/USD pairs.
    Registered with twelvedata_feed via engine.py.
    """

    def __init__(self):
        self.is_active = False

        # Per-symbol state
        self._prices:  Dict[str, List[float]] = {}
        self._bars:    Dict[str, int]          = {}
        self._last_z:  Dict[str, float]        = {}
        self._kalman:  Dict[str, KalmanFilter] = {}
        self._signals: Dict[str, dict]         = {}
        self._ranges:  Dict[str, List[float]]  = {}
        self._returns: Dict[str, List[float]]  = {}

        self.symbols = [
            "EURUSD", "GBPUSD", "USDJPY", "USDCHF",
            "AUDUSD", "USDCAD", "XAUUSD"
        ]

    # ── Main entry point ───────────────────────────────────────

    async def on_bar(self, bar: dict):
        """Called by twelvedata_feed for every new closed bar."""
        sym = bar.get("symbol", "").replace("/", "")
        if sym not in self.symbols:
            return
        if not bar.get("is_closed"):
            return

        close = float(bar["close"])
        ts    = bar.get("open_time", 0)

        # Init per-symbol structures
        if sym not in self._prices:
            self._prices[sym] = []
            self._bars[sym]   = 0
            self._kalman[sym] = KalmanFilter(
                delta=settings.BOLLINGER_DELTA,
                Ve=settings.BOLLINGER_VE
            )
            self._ranges[sym] = []
            self._returns[sym] = []

        self._prices[sym].append(close)
        if len(self._prices[sym]) > 500:
            self._prices[sym] = self._prices[sym][-500:]
        bar_range = max(0.0, float(bar.get("high", close)) - float(bar.get("low", close)))
        self._ranges[sym].append(bar_range)
        if len(self._ranges[sym]) > 500:
            self._ranges[sym] = self._ranges[sym][-500:]
        if len(self._prices[sym]) >= 2:
            prev = self._prices[sym][-2]
            ret = (close - prev) / prev if prev else 0.0
            self._returns[sym].append(ret)
            if len(self._returns[sym]) > 500:
                self._returns[sym] = self._returns[sym][-500:]
        self._bars[sym] += 1

        # Need minimum bars for indicators
        if len(self._prices[sym]) < LOOKBACK + 2:
            warmup_signal = {
                "action": "hold",
                "reason": [f"warming_up({len(self._prices[sym])}/{LOOKBACK + 2})"],
                "zscore": None,
                "quality": 0.0,
                "session": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%H:%M") if ts else None,
            }
            self._signals[sym] = warmup_signal
            await redis_client.set_signal("forex_mr", sym, warmup_signal)
            return

        # Compute Z-score via Kalman filter
        prices = self._prices[sym]
        x = prices[-2]
        y = prices[-1]
        kf_result = self._kalman[sym].update(x, y)
        zscore = kf_result.get("zscore", 0.0)
        self._last_z[sym] = zscore

        # ── Filters ────────────────────────────────────────────
        if not self._market_hours_ok(ts):
            signal = {
                "action": "hold",
                "reason": ["market_closed"],
                "zscore": zscore,
                "quality": 0.0,
                "session": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%H:%M") if ts else None,
            }
            self._signals[sym] = signal
            await redis_client.set_signal("forex_mr", sym, signal)
            return

        if risk_manager.is_halted:
            signal = {
                "action": "hold",
                "reason": [f"risk_halted({risk_manager.halt_reason or 'manual'})"],
                "zscore": zscore,
                "quality": 0.0,
                "session": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%H:%M") if ts else None,
            }
            self._signals[sym] = signal
            await redis_client.set_signal("forex_mr", sym, signal)
            return

        entry_z = GOLD_ENTRY_Z if sym == "XAUUSD" else FX_ENTRY_Z
        exit_z  = GOLD_EXIT_Z  if sym == "XAUUSD" else FX_EXIT_Z

        signal = await self._signal(sym, zscore, prices, close, entry_z, exit_z, ts)
        if not self.is_active and signal["action"] != "hold":
            signal = {
                **signal,
                "action": "hold",
                "reason": ["strategy_inactive", signal.get("reason")],
                "quality": 0.0,
            }
        self._signals[sym] = signal

        if self.is_active and signal["action"] in ("buy", "sell"):
            await self._execute(sym, signal, close)
        await redis_client.set_signal("forex_mr", sym, signal)
        if self.is_active:
            await redis_client.publish("signals", {
                "strategy": "forex_mr",
                "symbol": sym,
                **signal,
            })

    # ── Signal Logic ───────────────────────────────────────────

    async def _signal(
        self,
        sym: str,
        zscore: float,
        prices: List[float],
        current_price: float,
        entry_z: float,
        exit_z: float,
        open_time_ms: int,
    ) -> dict:

        action = "hold"
        reason = []
        dt = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc) if open_time_ms else None

        # ── Exit logic ─────────────────────────────────────────
        pos = forex_paper_trader.positions.get(sym)
        if pos:
            if pos["side"] == "long"  and zscore >= exit_z:
                return {"action": "sell", "reason": "exit_long",  "zscore": zscore}
            if pos["side"] == "short" and zscore <= -exit_z:
                return {"action": "buy",  "reason": "exit_short", "zscore": zscore}
            return {"action": "hold", "reason": "in_position", "zscore": zscore}

        # ── Entry logic ────────────────────────────────────────
        if abs(zscore) < entry_z:
            return {"action": "hold", "reason": "no_signal", "zscore": zscore}

        candidate = "buy" if zscore < -entry_z else "sell"

        # Filter 0: liquid session quality
        if dt and not self._session_quality_ok(sym, dt):
            reason.append(f"session_block({dt.hour:02d}:{dt.minute:02d} UTC)")
            action = "hold"

        # Filter 1: RAVI regime
        ravi_series = compute_ravi_series(prices[-70:])
        if ravi_series:
            latest_ravi = ravi_series[-1]
            ravi_val = None
            if isinstance(latest_ravi, dict):
                ravi_val = latest_ravi.get("ravi")
            elif latest_ravi is not None:
                ravi_val = float(latest_ravi)
            if ravi_val is not None and abs(ravi_val) > RAVI_THRESH:
                reason.append(f"ravi_trending({ravi_val:.2f})")
                action = "hold"

        # Filter 2: RSI
        if len(prices) >= RSI_PERIOD + 1:
            try:
                rsi = compute_rsi(prices, RSI_PERIOD)
                if isinstance(rsi, list):
                    rsi = rsi[-1]
                if candidate == "buy"  and rsi > 70:
                    reason.append(f"rsi_overbought({rsi:.1f})")
                    action = "hold"
                if candidate == "sell" and rsi < 30:
                    reason.append(f"rsi_oversold({rsi:.1f})")
                    action = "hold"
            except Exception:
                pass

        # Filter 3: Bollinger Band Width (skip wide/trending)
        if len(prices) >= LOOKBACK:
            window = prices[-LOOKBACK:]
            mean   = sum(window) / len(window)
            std    = (sum((p - mean) ** 2 for p in window) / len(window)) ** 0.5
            bbw    = (2 * std) / mean if mean > 0 else 0
            if bbw > BBW_LIMIT:
                reason.append(f"bbw_wide({bbw:.4f})")
                action = "hold"

        # Filter 4: avoid news/shock bars and thin liquidity proxies
        if self._is_range_shock(sym):
            reason.append("range_shock")
            action = "hold"
        if self._is_return_shock(sym, candidate):
            reason.append("return_shock")
            action = "hold"

        if action == "hold" and reason:
            logger.debug(f"[{sym}] Entry blocked: {', '.join(reason)}")
            return {
                "action": "hold",
                "reason": reason,
                "zscore": zscore,
                "session": dt.strftime("%H:%M") if dt else None,
            }

        quality = round(self._quality_score(sym, zscore, entry_z), 3)
        return {
            "action": candidate,
            "reason": "signal_ok",
            "zscore": zscore,
            "quality": quality,
            "session": dt.strftime("%H:%M") if dt else None,
        }

    async def _execute(self, sym: str, signal: dict, price: float):
        """Size and fire the trade through forex_paper_trader."""
        action = signal["action"]
        zscore = signal.get("zscore", 0.0)
        quality = signal.get("quality", 1.0)
        asset_class = "metal" if sym == "XAUUSD" else "forex"

        # Lot sizing: combine z-score conviction with quality and risk budget.
        base_risk_pct = 0.008 if sym == "XAUUSD" else 0.006
        conviction = min(1.5, max(0.6, (abs(zscore) / max(FX_ENTRY_Z, 0.1)) * max(0.75, quality)))
        base_lots = forex_paper_trader.lot_size_from_risk(sym, price, risk_pct=base_risk_pct * conviction)
        z_scale   = min(abs(zscore) / 2.0, 1.5)
        lots      = round(base_lots * z_scale, 2)
        lots      = max(0.01, lots)
        margin    = forex_paper_trader.required_margin(sym, lots, price)

        decision = risk_manager.check(
            symbol=sym,
            side=action,
            quantity=lots,
            price=price,
            open_positions=forex_paper_trader.positions,
            is_mean_reversion=True,
            strategy="forex_mr",
            asset_class=asset_class,
            conviction=conviction,
            trade_value_override=margin,
        )
        if not decision.approved:
            logger.info(f"[FOREX MR] {sym} {action} REJECTED: {decision.reason}")
            return
        lots = round(max(0.01, decision.adjusted_qty), 2)

        trade = await forex_paper_trader.execute(
            symbol=sym,
            side=action,
            lots=lots,
            price=price,
            strategy="forex_mr",
            meta={"zscore": zscore, "reason": signal.get("reason"), "quality": quality},
        )

        if trade:
            logger.info(
                f"[FOREX MR] {action.upper()} {sym} | Z={zscore:+.3f} "
                f"| {lots} lots @ {price:.5f}"
            )

    # ── Market Hours Filter ────────────────────────────────────

    def _market_hours_ok(self, open_time_ms: int) -> bool:
        """
        Returns False on:
        - Saturday (all day)
        - Sunday before 21:00 UTC (market closed)
        - 21:45-22:15 UTC daily rollover window
        """
        if not open_time_ms:
            return True
        dt = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)
        weekday = dt.weekday()  # 0=Mon ... 5=Sat, 6=Sun

        # Saturday — market closed
        if weekday == 5:
            return False

        # Sunday before 21:00 UTC — market not yet open
        if weekday == 6 and dt.hour < 21:
            return False

        # Daily rollover: 21:45-22:15 UTC — spreads widen, skip entries
        hour, minute = dt.hour, dt.minute
        total_min = hour * 60 + minute
        if 21 * 60 + 45 <= total_min <= 22 * 60 + 15:
            return False

        return True

    def _session_quality_ok(self, sym: str, dt: datetime) -> bool:
        if not self._market_hours_ok(int(dt.timestamp() * 1000)):
            return False
        # Sunday open and late Friday are usually poor execution windows.
        if dt.weekday() == 0 and dt.hour < 1:
            return False
        if dt.weekday() == 4 and dt.hour >= 20:
            return False
        # Focus liquid windows for mean reversion. JPY/AUD tolerate earlier Asia.
        if sym in {"USDJPY", "AUDUSD"}:
            return 0 <= dt.hour <= 20
        return 6 <= dt.hour <= 20

    def _is_range_shock(self, sym: str) -> bool:
        ranges = self._ranges.get(sym, [])
        if len(ranges) < QUALITY_LOOKBACK:
            return False
        latest = ranges[-1]
        baseline = statistics.median(ranges[-QUALITY_LOOKBACK:-1] or ranges[-QUALITY_LOOKBACK:])
        return baseline > 0 and latest > baseline * RANGE_SHOCK_MULTIPLIER

    def _is_return_shock(self, sym: str, candidate: str) -> bool:
        rets = self._returns.get(sym, [])
        if len(rets) < QUALITY_LOOKBACK:
            return False
        latest = rets[-1]
        baseline = statistics.median(abs(r) for r in rets[-QUALITY_LOOKBACK:-1] or rets[-QUALITY_LOOKBACK:])
        directional_conflict = (candidate == "buy" and latest < 0) or (candidate == "sell" and latest > 0)
        return baseline > 0 and abs(latest) > baseline * RETURN_SHOCK_MULTIPLIER and directional_conflict

    def _quality_score(self, sym: str, zscore: float, entry_z: float) -> float:
        overshoot = max(0.0, abs(zscore) - entry_z)
        overshoot_score = min(1.0, overshoot / max(entry_z, 0.1) + 0.5)
        range_penalty = 0.7 if self._is_range_shock(sym) else 1.0
        return overshoot_score * range_penalty

    # ── Stats for API ──────────────────────────────────────────

    def get_stats(self) -> dict:
        return {
            "name":       "forex_mr",
            "active":     self.is_active,
            "symbols":    self.symbols,
            "total_bars": sum(self._bars.values()),
            "last_z":     {k: round(v, 4) for k, v in self._last_z.items()},
            "signals":    self._signals,
            "positions":  forex_paper_trader.positions,
            "summary":    forex_paper_trader.summary(),
        }


forex_strategy = ForexMRStrategy()
