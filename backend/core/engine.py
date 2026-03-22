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
from backend.core.notifications.telegram_bot import telegram
from backend.core.strategies.cross_sectional import cs_strategy
from backend.core.execution.paper_trader import paper_trader
from backend.core.execution.risk_manager import risk_manager
from backend.db.redis_client import redis_client
from backend.db.database import init_db
from backend.core.strategies.funding_rate import funding_strategy

logger = logging.getLogger("alphabot.engine")


class TradingEngine:
    def __init__(self):
        self.running    = False
        self.start_time = None
        self.mode       = settings.TRADING_MODE
        self._tasks: list = []

    async def startup(self):
        """Initialize all components — called once at startup."""
        logger.info("=" * 55)
        logger.info(f"  ALPHABOT STARTING — Mode: {self.mode.upper()}")
        logger.info(f"  Capital: ${settings.INITIAL_CAPITAL:,.0f}")
        logger.info("=" * 55)

        await init_db()
        logger.info("Database initialized (SQLite)")

        # Register strategy callbacks
        binance_feed.register(bollinger_strategy.on_bar)
        binance_feed.register(cs_strategy.on_bar)

        # Subscribe to symbols (Strategy 1 + Strategy 2 combined)
        all_symbols = list(set(
            settings.BOLLINGER_PAIRS + settings.CS_UNIVERSE
        ))
        for sym in all_symbols:
            # Bollinger uses 1h, CS also uses 1h — safe to merge
            binance_feed.subscribe(sym, settings.BOLLINGER_INTERVAL)

        # Pre-load historical bars for Kalman warmup
        logger.info("Pre-loading historical bars...")
        for sym in settings.BOLLINGER_PAIRS:
            await binance_feed.fetch_historical(sym, settings.BOLLINGER_INTERVAL, limit=300)

        # Warm up strategies by replaying historical bars
        await self._warmup()

        self.running    = True
        self.start_time = datetime.now(timezone.utc)
        await self._publish_status()
        await telegram.init()
    logger.info("Engine startup complete. Starting live feed...")

    async def _warmup(self):
        """Replay historical bars — NO trading during warmup."""
        logger.info("Warming up strategy signals...")
        bollinger_strategy.is_active = False
        cs_strategy.is_active        = False
        # Warm ETH first so pair prices are available for other symbols
        ordered = ["ETHUSDT"] + [s for s in settings.BOLLINGER_PAIRS if s != "ETHUSDT"]
        for sym in ordered:
            bars = await redis_client.get_bars(sym, settings.BOLLINGER_INTERVAL, n=300)
            for bar in bars:
                bar["is_closed"] = True
                await bollinger_strategy.on_bar(bar)
                await cs_strategy.on_bar(bar)
        bollinger_strategy.is_active = True
        cs_strategy.is_active        = True
        risk_manager.reset_peak()
        # Seed bar counts and Kalman state from Redis after warmup
        for sym in settings.BOLLINGER_PAIRS:
            count = await redis_client.bar_count(sym, settings.BOLLINGER_INTERVAL)
            bollinger_strategy._bars[sym] = count
            bollinger_strategy.total_bars += count
            pass  # _last_z populated during warmup replay below
        logger.info("Warmup complete")    

    async def run(self):
        """Main run loop — starts all async tasks."""
        await self.startup()

        self._tasks = [
            asyncio.create_task(binance_feed.start(),      name="binance_feed"),
            asyncio.create_task(self._heartbeat_loop(),    name="heartbeat"),
            asyncio.create_task(self._price_update_loop(), name="price_updater"),
            asyncio.create_task(self._daily_close_loop(),  name="daily_close"),
            asyncio.create_task(self._funding_rate_loop(), name="funding_rates"),
        ]

        logger.info("All tasks running. Bot is live.")

        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            logger.info("Tasks cancelled")
        except Exception as e:
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
            "uptime_sec": int((datetime.now(timezone.utc) - self.start_time).total_seconds())
                          if self.start_time else 0,
            "portfolio":  portfolio,
            "risk":       risk_manager.to_dict(),
            "strategies": [
                bollinger_strategy.get_stats(),
                cs_strategy.get_stats(),
            ],
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        }
        await redis_client.set_status(status)
        await redis_client.publish("status", status)

    async def pause(self, reason: str = "Manual"):
        bollinger_strategy.is_active = False
        cs_strategy.is_active        = False
        logger.info(f"Trading paused: {reason}")
        await redis_client.publish("status", {"event": "paused", "reason": reason})

    async def resume(self):
        bollinger_strategy.is_active = True
        cs_strategy.is_active        = True
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
        await binance_feed.stop()
        for t in self._tasks:
            t.cancel()
        logger.info("Engine shutdown complete")


engine = TradingEngine()
