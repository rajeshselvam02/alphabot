"""
Trading Engine — Main orchestrator
Wires: data feeds → strategies → execution → monitoring
"""
import asyncio
import logging
from datetime import datetime, timezone
from backend.config.settings import settings
from backend.core.data_feeds.binance_feed import binance_feed
from backend.core.strategies.bollinger_mr import bollinger_strategy
from backend.core.notifications.telegram_bot import telegram, command_bot
from backend.core.strategies.cross_sectional import cs_strategy
from backend.core.execution.paper_trader import paper_trader
from backend.core.execution.risk_manager import risk_manager
from backend.db.redis_client import redis_client
from backend.db.database import init_db
from backend.core.strategies.funding_rate import funding_strategy
from backend.core.data_feeds.twelvedata_feed import twelvedata_feed
from backend.core.strategies.forex_mr import forex_strategy
from backend.core.execution.forex_paper_trader import forex_paper_trader

logger = logging.getLogger("alphabot.engine")

CRYPTO_WARMUP_BARS = 96
FOREX_WARMUP_BARS = 96
FOREX_PRELOAD_BARS = 120
FOREX_PRELOAD_FETCH_TIMEOUT_SEC = 12
FOREX_PRELOAD_SOFT_BUDGET_SEC = 35
FOREX_PRELOAD_INTER_SYMBOL_DELAY_SEC = 5


class TradingEngine:
    def __init__(self):
        self.running    = False
        self.start_time = None
        self.mode       = settings.TRADING_MODE
        self._tasks: list = []
        self.phase = "booting"
        self.phase_detail = "Process created"
        self._state_restored = False
        self._warmup_complete = False
        self._startup_warnings: list[str] = []
        self._degraded_reasons: list[str] = []
        self._forex_preload_status = {
            "attempted": 0,
            "loaded": 0,
            "failed": 0,
            "skipped": 0,
            "bars": FOREX_PRELOAD_BARS,
        }

    def _set_phase(self, phase: str, detail: str):
        self.phase = phase
        self.phase_detail = detail

    def _warn_startup(self, message: str):
        if message not in self._startup_warnings:
            self._startup_warnings.append(message)
        logger.warning(f"[STARTUP] {message}")

    def _mark_degraded(self, reason: str):
        if reason not in self._degraded_reasons:
            self._degraded_reasons.append(reason)
        self._warn_startup(reason)

    def engine_state(self) -> dict:
        return {
            "phase": self.phase,
            "detail": self.phase_detail,
            "ready": self.phase in {"live", "live_degraded"},
            "degraded": bool(self._degraded_reasons),
            "warnings": list(self._startup_warnings),
            "degraded_reasons": list(self._degraded_reasons),
            "forex_preload": dict(self._forex_preload_status),
            "state_restored": self._state_restored,
            "warmup_complete": self._warmup_complete,
            "started_at": self.start_time.isoformat() if self.start_time else None,
        }

    async def startup(self):
        """Initialize all components — called once at startup."""
        self._state_restored = False
        self._warmup_complete = False
        self._startup_warnings = []
        self._degraded_reasons = []
        self._forex_preload_status = {
            "attempted": 0,
            "loaded": 0,
            "failed": 0,
            "skipped": 0,
            "bars": FOREX_PRELOAD_BARS,
        }
        self._set_phase("initializing", "Preparing engine startup")
        logger.info("=" * 55)
        logger.info(f"  ALPHABOT STARTING — Mode: {self.mode.upper()}")
        logger.info(f"  Capital: ${settings.INITIAL_CAPITAL:,.0f}")
        logger.info("=" * 55)

        self._set_phase("initializing_db", "Opening SQLite database")
        await init_db()
        logger.info("Database initialized (SQLite)")

        # ── Startup validation ──────────────────────────────────
        self._set_phase("validating", "Checking dependencies and settings")
        await self._validate_startup()
        self._set_phase("restoring_state", "Loading saved trader state")
        await paper_trader.load_state()
        await forex_paper_trader.load_state()
        self._state_restored = True

        # Register strategy callbacks
        self._set_phase("subscribing", "Registering feeds and strategy callbacks")
        binance_feed.register(bollinger_strategy.on_bar)
        binance_feed.register(cs_strategy.on_bar)

        # Subscribe to symbols (Strategy 1 + Strategy 2 combined)
        all_symbols = list(set(
            settings.BOLLINGER_PAIRS + settings.CS_UNIVERSE
        ))
        for sym in all_symbols:
            # Bollinger uses 1h, CS also uses 1h — safe to merge
            binance_feed.subscribe(sym, settings.BOLLINGER_INTERVAL)

        # Register forex callbacks
        twelvedata_feed.register(forex_strategy.on_bar)
        for sym in settings.FOREX_PAIRS:
            twelvedata_feed.subscribe(sym, settings.FOREX_INTERVAL)
        await self._preload_forex_history()
        self._set_phase("preloading_crypto", "Preparing crypto historical preload")
        logger.info("Pre-loading historical bars...")
        for i, sym in enumerate(settings.BOLLINGER_PAIRS, start=1):
            self._set_phase("preloading_crypto", f"{i}/{len(settings.BOLLINGER_PAIRS)} {sym}")
            await binance_feed.fetch_historical(sym, settings.BOLLINGER_INTERVAL, limit=300)

        # Warm up strategies by replaying historical bars
        self._set_phase("warming_up", "Replaying historical bars into strategies")
        await self._warmup()
        self._warmup_complete = True

        self.running    = True
        self.start_time = datetime.now(timezone.utc)
        if self._degraded_reasons:
            self._set_phase("live_degraded", "Live with partial startup coverage")
        else:
            self._set_phase("live", "Feeds and strategies are active")
        await self._publish_status()
        await telegram.init()
        command_bot.register_callbacks(
            portfolio_fn=lambda: paper_trader.__dict__,
            strategies_fn=lambda: bollinger_strategy.__dict__,
            positions_fn=lambda: paper_trader.positions,
        )
        await command_bot.start()
        logger.info("Engine startup complete. Starting live feed...")

    async def _preload_forex_history(self):
        """
        Preload only the minimum viable forex history budget needed for warmup.

        Twelve Data latency and free-tier pacing should not block the whole engine
        from entering a live state. When preload coverage is partial, startup
        proceeds in a degraded mode and the forex strategy will stay in warmup/hold
        for symbols that still lack enough bars.
        """
        logger.info("Pre-loading bounded forex historical bars...")
        deadline = asyncio.get_running_loop().time() + FOREX_PRELOAD_SOFT_BUDGET_SEC
        total = len(settings.FOREX_PAIRS)

        for i, sym in enumerate(settings.FOREX_PAIRS, start=1):
            self._set_phase("preloading_forex", f"{i}/{total} {sym}")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                skipped = total - self._forex_preload_status["attempted"]
                self._forex_preload_status["skipped"] = max(0, skipped)
                self._mark_degraded(
                    f"forex preload budget exceeded after {self._forex_preload_status['loaded']} symbols"
                )
                break

            self._forex_preload_status["attempted"] += 1
            try:
                await asyncio.wait_for(
                    twelvedata_feed.fetch_historical(
                        sym,
                        settings.FOREX_INTERVAL,
                        limit=FOREX_PRELOAD_BARS,
                    ),
                    timeout=min(FOREX_PRELOAD_FETCH_TIMEOUT_SEC, max(1, int(remaining))),
                )
                loaded = await redis_client.bar_count(sym, settings.FOREX_INTERVAL)
                if loaded >= FOREX_WARMUP_BARS:
                    self._forex_preload_status["loaded"] += 1
                else:
                    self._forex_preload_status["failed"] += 1
                    self._mark_degraded(f"{sym} preload incomplete ({loaded}/{FOREX_WARMUP_BARS} bars)")
            except asyncio.TimeoutError:
                self._forex_preload_status["failed"] += 1
                self._mark_degraded(f"{sym} preload timed out")
            except Exception as exc:
                self._forex_preload_status["failed"] += 1
                self._mark_degraded(f"{sym} preload failed: {exc}")

            if i < total:
                await asyncio.sleep(FOREX_PRELOAD_INTER_SYMBOL_DELAY_SEC)

        if self._forex_preload_status["loaded"] == 0:
            self._mark_degraded("forex preload unavailable at startup")

    async def _validate_startup(self):
        """Validate critical dependencies and restore durable state."""
        import json
        # Check for durable risk halt
        halt_raw = await redis_client.get("risk:halted")
        if halt_raw:
            try:
                h = json.loads(halt_raw)
                logger.critical(
                    f"[STARTUP] RISK HALT DETECTED from previous session: "
                    f"{h.get('reason')} at {h.get('timestamp','?')}"
                )
                risk_manager.is_halted   = True
                risk_manager.halt_reason = h.get("reason", "Previous session halt")
                logger.critical("[STARTUP] Bot starting in HALTED state — use /api/control/resume to re-enable")
            except Exception:
                pass

        # Check Redis connectivity
        try:
            await redis_client.get("ping_test")
            logger.info("[STARTUP] Redis connection OK")
        except Exception as e:
            logger.critical(f"[STARTUP] Redis unavailable: {e}")

        # Check required settings
        missing = []
        if not settings.TELEGRAM_TOKEN:
            missing.append("TELEGRAM_TOKEN")
        if not settings.TELEGRAM_CHAT_ID:
            missing.append("TELEGRAM_CHAT_ID")
        if missing:
            logger.warning(f"[STARTUP] Missing optional settings: {missing}")
        else:
            logger.info("[STARTUP] All settings validated")

    async def _warmup(self):
        """
        Replay only the recent history needed to seed indicators.

        Replaying the full cached history was blocking startup for far too long,
        especially once forex diagnostics began persisting on every replayed bar.
        """
        logger.info("Warming up strategy signals...")
        bollinger_strategy.is_active = False
        cs_strategy.is_active        = False
        # Warm ETH first so pair prices are available for other symbols
        ordered = ["ETHUSDT"] + [s for s in settings.BOLLINGER_PAIRS if s != "ETHUSDT"]
        for idx, sym in enumerate(ordered, start=1):
            self._set_phase(
                "warming_up",
                f"crypto {idx}/{len(ordered)} {sym} ({CRYPTO_WARMUP_BARS} bars)"
            )
            bars = await redis_client.get_bars(
                sym,
                settings.BOLLINGER_INTERVAL,
                n=CRYPTO_WARMUP_BARS,
            )
            for bar_idx, bar in enumerate(bars, start=1):
                bar["is_closed"] = True
                await bollinger_strategy.on_bar(bar)
                await cs_strategy.on_bar(bar)
                if bar_idx % 24 == 0:
                    await asyncio.sleep(0)
        bollinger_strategy.is_active = True
        cs_strategy.is_active        = True
        risk_manager.reset_peak()
        # Seed bar counts and Kalman state from Redis after warmup
        for sym in settings.BOLLINGER_PAIRS:
            count = await redis_client.bar_count(sym, settings.BOLLINGER_INTERVAL)
            bollinger_strategy._bars[sym] = count
            # total_bars already incremented during warmup replay
            pass  # _last_z populated during warmup replay below
        # Warmup forex strategy
        forex_strategy.is_active = False
        for idx, sym in enumerate(settings.FOREX_PAIRS, start=1):
            self._set_phase(
                "warming_up",
                f"forex {idx}/{len(settings.FOREX_PAIRS)} {sym} ({FOREX_WARMUP_BARS} bars)"
            )
            bars = await redis_client.get_bars(
                sym,
                settings.FOREX_INTERVAL,
                n=FOREX_WARMUP_BARS,
            )
            for bar_idx, bar in enumerate(bars, start=1):
                bar["is_closed"] = True
                await forex_strategy.on_bar(bar)
                if bar_idx % 24 == 0:
                    await asyncio.sleep(0)
        forex_strategy.is_active = True

        logger.info("Warmup complete")    

    async def run(self):
        """Main run loop — starts all async tasks."""
        try:
            await self.startup()

            self._tasks = [
                asyncio.create_task(binance_feed.start(),      name="binance_feed"),
                asyncio.create_task(twelvedata_feed.start(),    name="twelvedata_feed"),
                asyncio.create_task(self._heartbeat_loop(),    name="heartbeat"),
                asyncio.create_task(self._price_update_loop(), name="price_updater"),
                asyncio.create_task(self._daily_close_loop(),  name="daily_close"),
                asyncio.create_task(self._funding_rate_loop(), name="funding_rates"),
            ]

            logger.info("All tasks running. Bot is live.")
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            logger.info("Tasks cancelled")
        except Exception as e:
            self._set_phase("error", str(e))
            logger.critical(f"Fatal engine error: {e}", exc_info=True)
        finally:
            await self.shutdown()

    async def _heartbeat_loop(self):
        """Publish engine status every 30s."""
        while self.running:
            await self._publish_status()
            await asyncio.sleep(30)

    async def _price_update_loop(self):
        """Update unrealized P&L every 10s from live prices."""
        while self.running:
            prices = await redis_client.get_prices()
            if prices:
                paper_trader.update_prices(prices)
                # Check stop-losses for momentum positions
                await self._check_stops(prices)
            await asyncio.sleep(10)

    async def _check_stops(self, prices: dict):
        """Check stop-loss levels for open positions (momentum only)."""
        for sym, pos in list(paper_trader.positions.items()):
            stop = pos.get("stop_price")
            if not stop:
                continue
            price = prices.get(sym)
            if not price:
                continue
            triggered = (
                (pos["side"] == "long"  and price <= stop) or
                (pos["side"] == "short" and price >= stop)
            )
            if triggered:
                logger.warning(f"[STOP] {sym} stop triggered @ ${price:.4f}")
                side = "sell" if pos["side"] == "long" else "buy"
                await paper_trader.execute(sym, side, pos["quantity"], price,
                                           pos["strategy"], {"reason": "stop_loss"})

    async def _daily_close_loop(self):
        """Record daily performance at UTC midnight."""
        while self.running:
            now = datetime.now(timezone.utc)
            # Sleep until next midnight
            secs_to_midnight = (24 * 3600) - (now.hour * 3600 + now.minute * 60 + now.second)
            await asyncio.sleep(secs_to_midnight)

            equity = paper_trader.equity
            risk_manager.daily_close(equity)
            logger.info(f"Daily close recorded. Equity: ${equity:,.2f}")

    async def _publish_status(self):
        portfolio = paper_trader.summary()
        status = {
            "running":    self.running,
            "mode":       self.mode,
            "engine":     self.engine_state(),
            "uptime_sec": int((datetime.now(timezone.utc) - self.start_time).total_seconds())
                          if self.start_time else 0,
            "portfolio":  portfolio,
            "risk":       risk_manager.to_dict(),
            "strategies": [
                bollinger_strategy.get_stats(),
                cs_strategy.get_stats(),
                forex_strategy.get_stats(),
            ],
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        }
        await redis_client.set_status(status)
        await redis_client.publish("status", status)

    async def pause(self, reason: str = "Manual"):
        bollinger_strategy.is_active = False
        cs_strategy.is_active        = False
        forex_strategy.is_active     = False
        logger.info(f"Trading paused: {reason}")
        await redis_client.publish("status", {"event": "paused", "reason": reason})

    async def resume(self):
        bollinger_strategy.is_active = True
        cs_strategy.is_active        = True
        forex_strategy.is_active     = True
        risk_manager.resume()
        logger.info("Trading resumed")
        await redis_client.publish("status", {"event": "resumed"})


    async def _funding_rate_loop(self):
        """Fetch funding rates every 8 hours from Binance."""
        import aiohttp
        symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
        base    = "https://fapi.binance.com"

        while self.running:
            try:
                async with aiohttp.ClientSession() as session:
                    for sym in symbols:
                        url    = f"{base}/fapi/v1/fundingRate"
                        params = {"symbol": sym, "limit": 1}
                        async with session.get(url, params=params,
                                               timeout=aiohttp.ClientTimeout(total=10)) as r:
                            if r.status == 200:
                                data = await r.json()
                                if data:
                                    rate = float(data[0]["fundingRate"])
                                    await funding_strategy.on_funding_rate(sym, rate)
            except Exception as e:
                logger.warning(f"Funding rate fetch error: {e}")
            await asyncio.sleep(8 * 3600)

    async def shutdown(self):
        self.running = False
        self._set_phase("stopped", "Engine shutdown complete")
        await binance_feed.stop()
        await twelvedata_feed.stop()
        for t in self._tasks:
            t.cancel()
        logger.info("Engine shutdown complete")


engine = TradingEngine()
