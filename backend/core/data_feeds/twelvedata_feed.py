"""
Twelve Data Feed — Forex + XAU/USD
Polls 1h OHLCV candles for all forex pairs and Gold.
Same interface as binance_feed.py — drop-in compatible with engine.py.

Instruments: EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD, USD/CAD, XAU/USD
API: https://api.twelvedata.com  (free tier: 800 req/day)
Poll interval: 60s per cycle (8 pairs × 1 req = 8 req/cycle × 24 cycles = 192 req/day)
"""
import asyncio
import logging
import aiohttp
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional
from backend.config.settings import settings
from backend.db.redis_client import redis_client

logger = logging.getLogger("alphabot.twelvedata_feed")

# Twelve Data symbol format uses "/" e.g. "EUR/USD", "XAU/USD"
FOREX_SYMBOLS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "USD/CHF",
    "AUD/USD",
    "USD/CAD",
    "XAU/USD",
]

# Internal key format (no slash) used in Redis + strategy dicts
def to_key(symbol: str) -> str:
    """EUR/USD → EURUSD"""
    return symbol.replace("/", "")

# Reverse: EURUSD → EUR/USD
def to_api(symbol: str) -> str:
    """EURUSD → EUR/USD"""
    if "/" in symbol:
        return symbol
    if symbol == "XAUUSD":
        return "XAU/USD"
    return symbol[:3] + "/" + symbol[3:]

BASE_URL = "https://api.twelvedata.com"


class TwelveDataFeed:
    """
    Polls Twelve Data every 60s for closed 1h candles.
    Fires registered callbacks with a bar dict identical to binance_feed format.
    """

    def __init__(self):
        self._callbacks: List[Callable] = []
        self._subscriptions: Dict[str, str] = {}   # key → interval
        self._running = False
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_bar_time: Dict[str, int] = {}   # key → last seen open_time

    # ── Public API (mirrors binance_feed) ──────────────────────

    def register(self, callback: Callable):
        """Register a strategy callback to receive bars."""
        self._callbacks.append(callback)
        logger.info(f"Registered callback: {callback.__qualname__}")

    def subscribe(self, symbol: str, interval: str = "1h"):
        """Subscribe to a symbol. symbol can be 'EURUSD' or 'EUR/USD'."""
        key = to_key(symbol)
        self._subscriptions[key] = interval
        logger.info(f"Subscribed: {key} @ {interval}")

    async def fetch_historical(self, symbol: str, interval: str = "1h", limit: int = 300):
        """
        Fetch historical bars and store in Redis for warmup.
        Called by engine._warmup() before live polling starts.
        """
        key = to_key(symbol)
        api_sym = to_api(key)
        params = {
            "symbol":     api_sym,
            "interval":   interval,
            "outputsize": limit,
            "apikey":     settings.TWELVEDATA_API_KEY,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{BASE_URL}/time_series",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as r:
                    if r.status != 200:
                        logger.warning(f"[{key}] HTTP {r.status} on historical fetch")
                        return
                    data = await r.json()

            if data.get("status") == "error":
                logger.warning(f"[{key}] API error: {data.get('message')}")
                return

            values = data.get("values", [])
            if not values:
                logger.warning(f"[{key}] No historical data returned")
                return

            # Twelve Data returns newest first — reverse to oldest first
            values.reverse()

            bars = []
            for v in values:
                try:
                    dt = datetime.fromisoformat(v["datetime"]).replace(tzinfo=timezone.utc)
                    bar = {
                        "symbol":     key,
                        "interval":   interval,
                        "open_time":  int(dt.timestamp() * 1000),
                        "open":       float(v["open"]),
                        "high":       float(v["high"]),
                        "low":        float(v["low"]),
                        "close":      float(v["close"]),
                        "volume":     float(v.get("volume") or 0),
                        "is_closed":  True,
                        "source":     "twelvedata",
                    }
                    bars.append(bar)
                except (KeyError, ValueError) as e:
                    logger.debug(f"[{key}] Bar parse error: {e}")
                    continue

            # Store in Redis (same key format as crypto bars)
            redis_key = f"bars:{key}:{interval}"
            for bar in bars:
                await redis_client.push_bar(bar["symbol"], bar["interval"], bar)

            # Trim to last 500 bars
            # ltrim handled by push_bar internally

            # Track last bar time to avoid re-firing
            if bars:
                self._last_bar_time[key] = bars[-1]["open_time"]

            logger.info(f"[{key}] Loaded {len(bars)} historical bars")

        except Exception as e:
            logger.error(f"[{key}] Historical fetch failed: {e}")

    async def start(self):
        """Main polling loop — runs forever, fires callbacks on new closed bars."""
        self._running = True
        logger.info(f"TwelveData feed starting — {len(self._subscriptions)} symbols")

        while self._running:
            try:
                await self._poll_all()
            except Exception as e:
                logger.error(f"Poll cycle error: {e}")
            # Poll every 60s — 8 pairs × 24h = 192 req/day (well within 800 free limit)
            await asyncio.sleep(60)

    async def stop(self):
        self._running = False
        logger.info("TwelveData feed stopped")

    # ── Internal ───────────────────────────────────────────────

    async def _poll_all(self):
        """Poll latest bar for every subscribed symbol."""
        if not self._subscriptions:
            return

        async with aiohttp.ClientSession() as session:
            for key, interval in self._subscriptions.items():
                try:
                    await self._poll_one(session, key, interval)
                    # Small delay between requests to be kind to API
                    await asyncio.sleep(8)  # 7 symbols × 8s = 56s spread, stays under 8 req/min
                except Exception as e:
                    logger.warning(f"[{key}] Poll error: {e}")

    async def _poll_one(self, session: aiohttp.ClientSession, key: str, interval: str):
        """Fetch latest 2 bars for a symbol, fire callback if bar is new + closed."""
        api_sym = to_api(key)
        params = {
            "symbol":     api_sym,
            "interval":   interval,
            "outputsize": 2,          # current + previous (previous is confirmed closed)
            "apikey":     settings.TWELVEDATA_API_KEY,
        }

        async with session.get(
            f"{BASE_URL}/time_series",
            params=params,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            if r.status != 200:
                logger.warning(f"[{key}] HTTP {r.status}")
                return
            data = await r.json()

        if data.get("status") == "error":
            logger.warning(f"[{key}] {data.get('message')}")
            return

        values = data.get("values", [])
        if len(values) < 2:
            return

        # Index 0 = newest (still forming), index 1 = previous (closed)
        closed_raw = values[1]

        try:
            dt = datetime.fromisoformat(closed_raw["datetime"]).replace(tzinfo=timezone.utc)
            open_time = int(dt.timestamp() * 1000)
        except (KeyError, ValueError):
            return

        # Skip if we already processed this bar
        if self._last_bar_time.get(key) == open_time:
            return

        bar = {
            "symbol":    key,
            "interval":  interval,
            "open_time": open_time,
            "open":      float(closed_raw["open"]),
            "high":      float(closed_raw["high"]),
            "low":       float(closed_raw["low"]),
            "close":     float(closed_raw["close"]),
            "volume":    float(closed_raw.get("volume") or 0),
            "is_closed": True,
            "source":    "twelvedata",
        }

        # Freshness gate — reject bars older than 2h (forex can have gaps on weekends)
        age_minutes = (datetime.now(timezone.utc).timestamp() - dt.timestamp()) / 60
        if age_minutes > 120:
            logger.debug(f"[{key}] Stale bar ({age_minutes:.0f}min old) — skipping")
            return

        # Store in Redis
        redis_key = f"bars:{key}:{interval}"
        await redis_client.push_bar(bar["symbol"], bar["interval"], bar)
        # ltrim handled by push_bar internally

        # Update live price cache
        await redis_client.set_price(key, bar["close"])

        self._last_bar_time[key] = open_time
        logger.info(f"[{key}] New bar @ {dt.strftime('%H:%M')} close={bar['close']:.5f}")

        # Fire all registered callbacks
        for cb in self._callbacks:
            try:
                await cb(bar)
            except Exception as e:
                logger.error(f"Callback {cb.__qualname__} error: {e}")


twelvedata_feed = TwelveDataFeed()
