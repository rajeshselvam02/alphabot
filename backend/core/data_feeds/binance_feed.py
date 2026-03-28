"""
Binance Data Feed — WebSocket + REST historical fetch
Works on Termux with mobile data (handles reconnects gracefully)
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional
import aiohttp
import websockets
from backend.config.settings import settings
from backend.db.redis_client import redis_client

logger = logging.getLogger("alphabot.feed")

GATEIO_WS = "wss://api.gateio.ws/ws/v4/"
INTERVAL_MAP = {"1m":"1","3m":"3","5m":"5","15m":"15","30m":"30","1h":"60","2h":"120","4h":"240","1d":"D"}
GATEIO_REST = "https://api.gateio.ws"



class BinanceFeed:
    """
    Async WebSocket feed for Binance kline (OHLCV) data.
    
    - Subscribes to multiple symbol/interval pairs
    - Pushes closed bars to Redis buffer
    - Calls registered callbacks on each closed bar
    - Auto-reconnects on disconnect (important for mobile)
    """

    def __init__(self):
        self._callbacks: List[Callable]       = []
        self._subscriptions: Dict[str, str]   = {}  # symbol → interval
        self._running: bool                   = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._reconnect_delay: float          = 5.0
        self._bar_counts: Dict[str, int]      = {}

    def register(self, fn: Callable):
        """Register an async callback: called on each closed bar."""
        self._callbacks.append(fn)

    def subscribe(self, symbol: str, interval: str):
        """Add symbol/interval to subscription list."""
        sym = symbol.upper()
        self._subscriptions[sym] = interval
        self._bar_counts[sym] = 0
        logger.info(f"Subscribed: {sym} @ {interval}")

    def _bybit_interval(self, iv):
        return INTERVAL_MAP.get(iv, iv)

    async def _subscribe_msg(self, ws):
        for sym, iv in self._subscriptions.items():
            pair = sym.replace("USDT","_USDT")
            msg = {"time": 1, "channel": "spot.candlesticks", "event": "subscribe", "payload": [iv, pair]}
            await ws.send(json.dumps(msg))
        logger.info(f"Gate.io subscribed: {len(self._subscriptions)} streams")

    async def _process(self, raw: str):
        """Parse WebSocket message and dispatch callbacks."""
        try:
            msg  = json.loads(raw)
            if msg.get("event") in ("subscribe", "unsubscribe") or msg.get("channel") != "spot.candlesticks":
                return
            k = msg.get("result", {})
            if not k:
                return
            pair = k.get("n", "")  # e.g. "1h_BTC_USDT"
            parts = pair.split("_", 1)
            sym = parts[1].replace("_","").upper() if len(parts) > 1 else ""
            interval = self._subscriptions.get(sym, settings.BOLLINGER_INTERVAL)
            # Gate.io never sends w=True — detect close by timestamp change
            t_val = k.get("t", "0")
            sym_key = f"_last_t_{sym}"
            prev_t = getattr(self, sym_key, None)
            is_closed = (prev_t is not None and t_val != prev_t)
            setattr(self, sym_key, t_val)
            bar = {
                "symbol":    sym,
                "interval":  interval,
                "open":      float(k["o"]),
                "high":      float(k["h"]),
                "low":       float(k["l"]),
                "close":     float(k["c"]),
                "volume":    float(k["v"]),
                "timestamp": datetime.fromtimestamp(
                    int(k["t"]), tz=timezone.utc
                ).isoformat(),
                "is_closed": bool(is_closed),
            }

            # Always update live price
            await redis_client.set_price(bar["symbol"], bar["close"])

            # Only buffer + callback on closed bars
            if bar["is_closed"]:
                await redis_client.push_bar(bar["symbol"], bar["interval"], bar)
                sym = bar["symbol"]
                self._bar_counts[sym] = self._bar_counts.get(sym, 0) + 1
                logger.debug(f"[{sym}] bar closed @ {bar['close']:.4f}")

                for cb in self._callbacks:
                    try:
                        await cb(bar)
                    except Exception as e:
                        logger.error(f"Callback error: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Feed parse error: {e}")

    async def start(self):
        """Gate.io WebSocket with timestamp-delta close detection."""
        if not self._subscriptions:
            for sym in settings.BOLLINGER_PAIRS:
                self.subscribe(sym, settings.BOLLINGER_INTERVAL)
        self._running = True
        last_ts = {}
        logger.info("Starting Gate.io REST polling...")
        while self._running:
            try:
                subs = list(self._subscriptions.items())
                eth = [(s,i) for s,i in subs if s=="ETHUSDT"]
                rest = [(s,i) for s,i in subs if s!="ETHUSDT"]
                for sym, iv in eth + rest:
                    try:
                        pair = sym.replace("USDT", "_USDT")
                        url = f"{GATEIO_REST}/api/v4/spot/candlesticks"
                        params = {"currency_pair": pair, "interval": iv, "limit": 2}
                        async with aiohttp.ClientSession() as session:
                            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                                if resp.status != 200:
                                    continue
                                raw = await resp.json()
                        if not raw or len(raw) < 2:
                            continue
                        k = raw[-2]
                        ts = k[0]
                        if last_ts.get(sym) == ts:
                            continue
                        last_ts[sym] = ts
                        bar = {
                            "symbol":    sym,
                            "interval":  iv,
                            "open":      float(k[5]),
                            "high":      float(k[3]),
                            "low":       float(k[4]),
                            "close":     float(k[2]),
                            "volume":    float(k[6]),
                            "timestamp": datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat(),
                            "is_closed": True,
                        }
                        await redis_client.set_price(sym, bar["close"])
                        await redis_client.push_bar(sym, iv, bar)
                        self._bar_counts[sym] = self._bar_counts.get(sym, 0) + 1
                        logger.info(f"[{sym}] bar closed @ {bar['close']:.2f}")
                        for cb in self._callbacks:
                            try:
                                await cb(bar)
                            except Exception as e:
                                logger.error(f"Callback error: {e}", exc_info=True)
                    except Exception as e:
                        logger.error(f"Poll error {sym}: {e}")
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Polling error: {e}")
                await asyncio.sleep(30)

    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.close()
        logger.info("Binance feed stopped")

    async def fetch_historical(
        self, symbol: str, interval: str, limit: int = 300
    ) -> list:
        """
        Fetch historical klines from REST API.
        Pre-populates Redis buffer so Kalman filter warms up immediately.
        """
        pair = symbol.replace("USDT","_USDT")
        url = f"{GATEIO_REST}/api/v4/spot/candlesticks"
        params = {"currency_pair": pair, "interval": interval, "limit": min(limit, 1000)}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        logger.warning(f"Historical fetch failed: {resp.status}")
                        return []
                    raw = await resp.json()
        except Exception as e:
            logger.error(f"Historical fetch error for {symbol}: {e}")
            return []

        bars = []
        for k in raw:
            bar = {
                "symbol":    symbol,
                "interval":  interval,
                "open":      float(k[5]),
                "high":      float(k[3]),
                "low":       float(k[4]),
                "close":     float(k[2]),
                "volume":    float(k[6]),
                "timestamp": datetime.fromtimestamp(
                    int(k[0]), tz=timezone.utc
                ).isoformat(),
                "is_closed": True,
            }
            bars.append(bar)
            await redis_client.push_bar(symbol, interval, bar)

        logger.info(f"Pre-loaded {len(bars)} bars for {symbol}/{interval}")
        return bars

    @property
    def bar_counts(self) -> dict:
        return dict(self._bar_counts)


binance_feed = BinanceFeed()
