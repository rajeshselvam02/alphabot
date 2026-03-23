import asyncio
"""
Strategy 1: Bollinger Band Mean Reversion
════════════════════════════════════════════════════════
Theory base:
  - Chan Ch.3: Bollinger bands + Kalman filter for dynamic mean/std
  - Platen Ch.4: OU process (half-life determines lookback)
  - Williams Ch.10: Supermartingale property validates MR approach

Signal logic:
  1. Kalman filter estimates dynamic mean μ(t) and std σ(t) of price
  2. Z-score = (price - μ) / σ
  3. Enter LONG  when z < -entryZ  (price below lower band)
  4. Enter SHORT when z > +entryZ  (price above upper band)
  5. Exit        when z crosses exitZ (defaults to 0, i.e., mean)
  6. NO stop-loss on MR positions (Chan Ch.8: logically contradictory)
     because larger deviation = stronger expected reversion

Per-symbol state:
  - KalmanFilter instance (persisted to Redis)
  - Current position side: "long" | "short" | None
  - Bar count (warm-up gate)
  - Stationarity metrics (Hurst, half-life)
════════════════════════════════════════════════════════
"""
import logging
from typing import Dict, Optional
from backend.config.settings import settings
from backend.core.notifications.telegram_bot import telegram
from backend.core.signals.quant_signals import (
    compute_dynamic_exits,
    meta_label,
    wyckoff_analysis,
    fetch_cumulative_delta,
    DQLAgent,
    dql_agent,
    logit_direction_filter,
    compute_rsi,
    compute_bbw,
    compute_macd,
    compute_ravi,
    KalmanFilter, compute_hurst, compute_half_life, adf_test,
    position_scale_from_risk
)
from backend.core.execution.risk_manager import risk_manager
from backend.core.execution.paper_trader import paper_trader
from backend.db.redis_client import redis_client

logger = logging.getLogger("alphabot.bollinger")


class BollingerMRStrategy:
    """
    Bollinger Band Mean Reversion — single-asset Kalman-enhanced.
    Each symbol traded independently.
    """

    NAME = "bollinger_mr"

    def __init__(self):
        self.is_active  = True
        self.is_halted  = False

        self._kf:       Dict[str, KalmanFilter] = {}
        self._prices:   Dict[str, list]         = {}
        self._pos_side: Dict[str, Optional[str]] = {}  # "long"|"short"|None
        self._bars:     Dict[str, int]          = {}
        self._last_z:   Dict[str, float]        = {}
        self._stats:    Dict[str, dict]         = {}

        self.signals_fired  = 0
        self._paper_trading_enabled = True
        self._kf_warmed: dict = {}
        self._bars_ohlcv: dict = {}
        self._kf_errors: Dict[str, list] = {}
        self._volumes:  Dict[str, list]         = {}
        self.trades_made    = 0
        self.total_bars     = 0

    def _init(self, symbol: str):
        if symbol not in self._kf:
            self._kf[symbol]       = KalmanFilter(
                delta=settings.BOLLINGER_DELTA,
                Ve=settings.BOLLINGER_VE,
            )
            self._prices[symbol]   = []
            self._pos_side[symbol] = None
            # Bar count seeded by engine.py after warmup — don't overwrite
            if not (symbol in self._bars and self._bars[symbol] > 20):
                self._bars[symbol] = 0
            self._last_z[symbol]   = 0.0
            self._volumes[symbol]  = []
            self._stats[symbol]    = {"hurst": 0.5, "half_life": None, "adf_t": 0.0}

    async def on_bar(self, bar: dict):
        """Entry point — called by engine on each closed bar."""
        symbol = bar["symbol"]
        close  = bar["close"]
        volume = bar.get("volume", 0.0)
        self._init(symbol)

        # Store full OHLCV bars for Wyckoff analysis
        if symbol not in self._bars_ohlcv:
            self._bars_ohlcv[symbol] = []
        self._bars_ohlcv[symbol].append({
            'open': bar.get('open', close),
            'high': bar.get('high', close),
            'low':  bar.get('low', close),
            'close': close,
            'volume': volume,
        })
        if len(self._bars_ohlcv[symbol]) > 500:
            self._bars_ohlcv[symbol].pop(0)

        # Always accumulate prices and volumes (even during warmup)
        self._prices[symbol].append(close)
        if len(self._prices[symbol]) > 500:
            self._prices[symbol].pop(0)
        self._volumes[symbol].append(volume)
        if len(self._volumes[symbol]) > 50:
            self._volumes[symbol].pop(0)

        if not self.is_active or self.is_halted:
            return

        self._bars[symbol]  += 1
        self.total_bars     += 1
        # Persist to Redis so restarts don't reset counter
        asyncio.create_task(
            redis_client.set(f"bars_count:{symbol}", self._bars[symbol])
        )

        # Warm-up: need at least lookback*2 bars before trading
        warmup = settings.BOLLINGER_LOOKBACK * 2
        kf_out = self._run_kalman(symbol, close)
        
       # Jump detection (Shreve Ch.11)
        if False:  # Jump detection disabled during pair Kalman convergence
            logger.warning(
                f"[JUMP] {symbol} — magnitude {kf_out['jump_magnitude']}σ "
                f"— skipping bar, filter state preserved"
            )
            await redis_client.publish("signals", {
                "strategy": self.NAME,
                "symbol":   symbol,
                "close":    round(close, 4),
                "zscore":   0.0,
                "is_jump":  True,
                "jump_magnitude": kf_out["jump_magnitude"],
                "position": self._pos_side.get(symbol),
            })
            return

        if self._bars[symbol] < warmup:
            return

        zscore = kf_out["zscore"]
        self._last_z[symbol] = zscore

        # ── RAVI regime filter (Chande Ch.3) ──────────────────
        # Only trade in ranging markets (RAVI < 3.5%)
        # Skip MR signals during trending conditions
        ravi_out = compute_ravi(self._prices[symbol])
        if ravi_out["signal"] == "skip_mr":
            logger.debug(
                f"[RAVI] {symbol} trending (RAVI={ravi_out['ravi']:.1f}%) "
                f"— skipping MR signal"
            )
            await redis_client.publish("signals", {
                "strategy": self.NAME,
                "symbol":   symbol,
                "close":    round(close, 4),
                "zscore":   zscore,
                "ravi":     ravi_out["ravi"],
                "regime":   ravi_out["regime"],
                "signal":   "skip_mr",
                "position": self._pos_side.get(symbol),
            })
            return

        # ── Volume Confirmation Filter (Velu Ch.5) ──────────
        # Only enter new positions when volume >= 80% of 20-bar avg
        # Exits are always allowed regardless of volume
        vols = self._volumes[symbol]
        vol_filter_ok = True
        avg_vol = None
        if len(vols) >= 20:
            avg_vol = sum(vols[-20:]) / 20
            if avg_vol > 0 and volume < 0.2 * avg_vol:
                vol_filter_ok = False
                logger.debug(
                    f"[VOL] {symbol} low volume ({volume:.1f} < 80% of {avg_vol:.1f}) "
                    f"— skipping entry signals"
                )

        # ── Logit Directional Filter (Dunis Ch.1) ──────────
        # Only enter in direction logit model predicts
        logit_out = logit_direction_filter(
            self._prices[symbol],
            zscores=[zscore],
            lookback=20,
        )
        logit_signal = logit_out["signal"]

        # ── Bollinger Band Width Filter (Murphy p.211) ─────
        # Skip MR entries when bands are wide (trending regime)
        bbw_out = compute_bbw(self._prices[symbol], period=20)
        bbw_signal = bbw_out["signal"]

        # ── MACD Histogram Filter (Murphy p.255) ───────────
        # Only enter in direction histogram is pointing
        macd_out = compute_macd(self._prices[symbol])
        macd_signal = macd_out["signal"]

        # ── Wyckoff Filter (Wyckoff Method) ────────────────
        wyckoff_out = wyckoff_analysis(self._bars_ohlcv.get(symbol, []))
        wyckoff_bias = wyckoff_out['wyckoff_bias']

        # ── Cumulative Delta Filter (Trader Dale p.41) ─────
        # Skip BUY when strong selling, skip SELL when strong buying
        cd_out = await fetch_cumulative_delta(symbol, limit=100)
        cd_signal = cd_out.get("signal", "neutral")
        cd_divergence = cd_out.get("divergence", False)

        # ── RSI Filter (Murphy p.239) ──────────────────────
        # Skip BUY when overbought (RSI > 70), skip SELL when oversold (RSI < 30)
        rsi_out = compute_rsi(self._prices[symbol], period=14)
        rsi_signal = rsi_out["signal"]

        # Periodic stationarity check (every 50 bars)
        if self._bars[symbol] % 50 == 0:
            await self._check_stationarity(symbol)

        # Generate signal
        signal = self._signal(symbol, zscore, current_price=close, vol_filter_ok=vol_filter_ok, logit_signal=logit_signal, rsi_signal=rsi_signal, bbw_signal=bbw_signal, macd_signal=macd_signal, wyckoff_bias=wyckoff_bias, cd_signal=cd_signal, cd_divergence=cd_divergence)

        # DQL Agent advisory (Hilpisch Ch.3) — runs alongside filters
        dql_actions = ['HOLD', 'BUY', 'SELL']
        hurst_val = self._stats.get(symbol, {}).get('hurst', 0.5) or 0.5
        hl_val = self._stats.get(symbol, {}).get('half_life', 20.0) or 20.0
        dql_state = dql_agent.build_state(
            zscore=zscore,
            rsi=rsi_out.get('rsi', 50.0),
            vol_ratio=volume / (avg_vol if avg_vol and avg_vol > 0 else 1),
            cd_delta=cd_out.get('delta_pct', 0.0),
            hurst=hurst_val,
            half_life=hl_val,
        )
        dql_action = dql_agent.predict(dql_state)
        dql_label  = dql_actions[dql_action]
        if signal:
            logger.info(f'[DQL] {symbol} filter_signal={signal} dql={dql_label} z={zscore:.2f}')

        if signal and self._paper_trading_enabled:
            # Meta-labeling (Lopez de Prado Ch.3) — secondary filter
            ml_out = meta_label(
                self._prices[symbol],
                zscore=zscore,
                volume=volume,
                avg_volume=avg_vol,
                rsi=rsi_out["rsi"],
                bbw=bbw_out.get("bbw") or 4.0,
            )
            if ml_out["prob_take"] < 0.45 and signal in ("buy", "sell"):
                logger.info(f"[META] {symbol} signal REJECTED by meta-label (prob={ml_out['prob_take']:.2f})")
            else:
                await self._execute(symbol, signal, close, zscore, kf_out, prob=ml_out["prob_take"])

        # Publish live signal to dashboard
        await redis_client.publish("signals", {
            "strategy":  self.NAME,
            "symbol":    symbol,
            "close":     round(close, 4),
            "zscore":    round(zscore, 3),
            "mean":      round(kf_out["mean"], 4),
            "std":       round(kf_out["forecast_std"], 4),
            "upper":     round(kf_out["mean"] + settings.BOLLINGER_ENTRY_Z * kf_out["forecast_std"], 4),
            "lower":     round(kf_out["mean"] - settings.BOLLINGER_ENTRY_Z * kf_out["forecast_std"], 4),
            "position":  self._pos_side.get(symbol),
            "signal":    signal,
            "hurst":     round(self._stats[symbol]["hurst"], 3),
            "vol_ok":    vol_filter_ok,
            "avg_vol":   round(avg_vol, 2) if avg_vol else None,
            "rsi":       rsi_out["rsi"],
            "rsi_signal": rsi_signal,
            "bbw":       bbw_out.get("bbw"),
            "bbw_signal": bbw_signal,
            "macd":      macd_out.get("histogram"),
            "macd_signal": macd_signal,
            "wyckoff":    wyckoff_bias,
            "cd_signal":  cd_signal,
            "cd_delta":   cd_out.get("delta_pct"),
            "cd_div":     cd_divergence,
        })

    # Pair definitions — y=dependent, x=independent
    PAIRS = {
        "BTCUSDT": "ETHUSDT",
        "BNBUSDT": "ETHUSDT",
        "SOLUSDT": "ETHUSDT",
        "LINKUSDT": "ETHUSDT",
        "DOTUSDT": "ETHUSDT",
        "ADAUSDT": "ETHUSDT",
        "MATICUSDT": "ETHUSDT",
    }

    def _run_kalman(self, symbol: str, price: float) -> dict:
        """Pair Kalman with rolling zscore of forecast errors."""
        prices_y = self._prices[symbol]
        pair_sym = self.PAIRS.get(symbol)
        prices_x = self._prices.get(pair_sym, []) if pair_sym else []

        # Fall back to single-asset if pair not available
        if len(prices_x) < 2 or len(prices_y) < 2:
            if len(prices_y) < 2:
                return {"zscore": 0.0, "mean": price, "forecast_std": 1.0,
                        "hedge_ratio": 1.0, "forecast_error": 0.0}
            prices_x = prices_y  # single asset fallback
            use_pair = False
        else:
            use_pair = True

        kf = self._kf[symbol]
        n = min(len(prices_y), len(prices_x))

        # Warm up if not done or if pair just became available
        warmed_mode = self._kf_warmed.get(symbol, None)
        current_mode = "pair" if use_pair else "single"
        if warmed_mode != current_mode:
            kf.reset()
            if symbol not in self._kf_errors:
                self._kf_errors[symbol] = []
            self._kf_errors[symbol] = []
            for i in range(1, n - 1):
                x = prices_x[i-1] if use_pair else prices_y[i-1]
                y = prices_y[i]
                r = kf.update(x, y)
                self._kf_errors[symbol].append(r.get("forecast_error", 0.0))
            if len(self._kf_errors[symbol]) > 50:
                self._kf_errors[symbol] = self._kf_errors[symbol][-50:]
            self._kf_warmed[symbol] = current_mode
            logger.info(f"[KF] {symbol} warmed as {current_mode} ({n} bars, {len(self._kf_errors[symbol])} errors)")

        x_now = prices_x[-2] if use_pair and len(prices_x) >= 2 else prices_y[-2]
        raw = kf.update(x_now, price)

        # Rolling zscore of forecast errors (more stable than instantaneous)
        if symbol not in self._kf_errors:
            self._kf_errors[symbol] = []
        err = raw.get("forecast_error", 0.0)
        self._kf_errors[symbol].append(err)
        if len(self._kf_errors[symbol]) > 50:
            self._kf_errors[symbol].pop(0)

        errs = self._kf_errors[symbol]
        if len(errs) >= 10:
            import statistics
            mu_e = statistics.mean(errs)
            sd_e = statistics.stdev(errs)
            rolling_z = (err - mu_e) / sd_e if sd_e > 0 else 0.0
            raw["zscore"] = rolling_z

        return raw

    def _signal(self, symbol: str, zscore: float, current_price: float = 0.0, vol_filter_ok: bool = True, logit_signal: str = "neutral", rsi_signal: str = "neutral", bbw_signal: str = "neutral", macd_signal: str = "neutral", wyckoff_bias: str = "neutral", cd_signal: str = "neutral", cd_divergence: bool = False) -> Optional[str]:
        """Generate trading signal from z-score."""
        ez = settings.BOLLINGER_ENTRY_Z
        xz = settings.BOLLINGER_EXIT_Z
        side = self._pos_side.get(symbol)

        # ── Triple Barrier Exit Check (Lopez de Prado Ch.3) ────
        if side is not None:
            pos = paper_trader.positions.get(symbol)
            if pos:
                tp = pos.get("signal_data", {}).get("take_profit")
                sl = pos.get("signal_data", {}).get("stop_loss")
                mb = pos.get("signal_data", {}).get("max_bars", 20)
                eb = pos.get("signal_data", {}).get("entry_bar", 0)
                bars_held = self._bars.get(symbol, 0) - eb
                if side == "long":
                    if tp and current_price >= tp:
                        return "close_long"   # upper barrier
                    if sl and current_price <= sl:
                        return "close_long"   # lower barrier
                elif side == "short":
                    if tp and current_price <= tp:
                        return "close_short"  # upper barrier
                    if sl and current_price >= sl:
                        return "close_short"  # lower barrier
                if bars_held >= mb:
                    return "close_long" if side == "long" else "close_short"  # vertical barrier

        if side is None:
            if not vol_filter_ok or bbw_signal == "wide":
                return None
            if wyckoff_bias == "strong_bearish" and zscore < -ez:
                return None  # Wyckoff says bearish, skip buy
            if wyckoff_bias == "strong_bullish" and zscore > ez:
                return None  # Wyckoff says bullish, skip sell
            if zscore < -ez and logit_signal in ("long", "neutral") and rsi_signal != "oversold" and macd_signal != "bullish":
                self.signals_fired += 1
                return "buy"
            if zscore > ez and logit_signal in ("short", "neutral") and rsi_signal != "overbought" and macd_signal != "bearish":
                self.signals_fired += 1
                return "sell"
        elif side == "long" and zscore >= -xz:
            return "close_long"
        elif side == "short" and zscore <= xz:
            return "close_short"
        return None

    async def _execute(
        self, symbol: str, signal: str,
        price: float, zscore: float, kf_out: dict, prob: float = None
    ):
        """Route signal through risk check → paper trader."""
        if risk_manager.is_halted:
            return

       # Base size from Z-score
        qty = risk_manager.zscore_size(zscore, price, prob=prob)

        # Scale by first passage risk (Shreve Ch.3)
        half_life = self._stats.get(symbol, {}).get("half_life") or 20.0
        scale     = position_scale_from_risk(zscore, half_life)
        qty       = qty * scale

        if scale < 1.0:
            logger.info(
                f"[RISK] {symbol} position scaled to {scale:.0%} "
                f"due to first passage probability "
                f"(half_life={half_life})"
            )
        if qty <= 0:
            return

        side = "buy" if signal in ("buy", "close_short") else "sell"
        decision = risk_manager.check(
            symbol=symbol,
            side=side,
            quantity=qty,
            price=price,
            open_positions=paper_trader.positions,
            is_mean_reversion=True,    # no stop-loss
        )

        if not decision.approved:
            logger.warning(f"[BOLLINGER] {symbol} {signal} REJECTED: {decision.reason}")
            return

        qty = decision.adjusted_qty
        signal_meta = {
            "zscore":       round(zscore, 3),
            "mean":         round(kf_out["mean"], 4),
            "std":          round(kf_out["forecast_std"], 4),
            "hedge_ratio":  round(kf_out["hedge_ratio"], 4),
            "hurst":        round(self._stats[symbol]["hurst"], 3),
        }

        asyncio.create_task(telegram.alert_signal(symbol, zscore, signal))
        if signal == "buy":
            tb = compute_dynamic_exits(self._prices[symbol], zscore, pt_multiplier=2.0, sl_multiplier=1.0, max_bars=20)
            signal_meta["take_profit"] = tb.get("take_profit")
            signal_meta["stop_loss"]   = tb.get("stop_loss")
            signal_meta["max_bars"]    = tb.get("max_bars", 20)
            signal_meta["entry_bar"]   = self._bars.get(symbol, 0)
            await paper_trader.execute(symbol, "buy", qty, price, self.NAME, signal_meta)
            self._pos_side[symbol] = "long"
            self.trades_made += 1

        elif signal == "sell":
            tb = compute_dynamic_exits(self._prices[symbol], zscore, pt_multiplier=2.0, sl_multiplier=1.0, max_bars=20)
            signal_meta["take_profit"] = tb.get("take_profit")
            signal_meta["stop_loss"]   = tb.get("stop_loss")
            signal_meta["max_bars"]    = tb.get("max_bars", 20)
            signal_meta["entry_bar"]   = self._bars.get(symbol, 0)
            await paper_trader.execute(symbol, "sell", qty, price, self.NAME, signal_meta)
            self._pos_side[symbol] = "short"
            self.trades_made += 1

        elif signal == "close_long":
            pos = paper_trader.positions.get(symbol)
            if pos:
                await paper_trader.execute(symbol, "sell", pos["quantity"], price, self.NAME,
                                           {**signal_meta, "reason": "exit_mean"})
            self._pos_side[symbol] = None
            self.trades_made += 1

        elif signal == "close_short":
            pos = paper_trader.positions.get(symbol)
            if pos:
                await paper_trader.execute(symbol, "buy", pos["quantity"], price, self.NAME,
                                           {**signal_meta, "reason": "exit_mean"})
            self._pos_side[symbol] = None
            self.trades_made += 1

        logger.info(
            f"[BOLLINGER] {symbol} {signal.upper()} "
            f"z={zscore:.2f} qty={qty:.4f} @${price:.2f}"
        )

    async def _check_stationarity(self, symbol: str):
        """Periodically verify the price series is still mean-reverting."""
        prices = self._prices[symbol]
        if len(prices) < 50:
            return

        hurst     = compute_hurst(prices[-200:] if len(prices) >= 200 else prices)
        half_life = compute_half_life(prices[-100:] if len(prices) >= 100 else prices)
        t_stat, is_mr = adf_test(prices[-100:] if len(prices) >= 100 else prices)

        self._stats[symbol] = {
            "hurst":     round(hurst, 3),
            "half_life": round(half_life, 1) if half_life else None,
            "adf_t":     round(t_stat, 3),
            "is_mr":     is_mr,
        }

        status = "MR" if hurst < 0.45 else ("TRD" if hurst > 0.55 else "RW")
        logger.info(
            f"[BOLLINGER] {symbol} | Hurst={hurst:.3f}({status}) "
            f"| HL={half_life:.1f}bars" if half_life else
            f"[BOLLINGER] {symbol} | Hurst={hurst:.3f}({status}) | HL=N/A"
        )

        if hurst > 0.58:
            logger.warning(
                f"[BOLLINGER] ⚠️  {symbol} Hurst={hurst:.3f} — "
                f"trending. Consider pausing."
            )

    def get_stats(self) -> dict:
        return {
            "strategy":      self.NAME,
            "is_active":     self.is_active,
            "is_halted":     self.is_halted,
            "signals_fired": self.signals_fired,
            "trades_made":   self.trades_made,
            "total_bars":    self.total_bars,
            "symbols":       list(self._bars.keys()),
            "positions":     {k: v for k, v in self._pos_side.items() if v},
            "bar_counts":    dict(self._bars),
            "last_z":        {k: round(v, 3) for k, v in self._last_z.items()},
            "stats":         dict(self._stats),
        }


bollinger_strategy = BollingerMRStrategy()
