"""
Redis Client — Live state, pub/sub, price buffers
Runs on Termux's local Redis server (port 6379)
"""
import redis.asyncio as aioredis
import json
from typing import Any, Optional
from backend.config.settings import settings

_redis: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = await aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=10,
        )
    return _redis


async def close_redis():
    global _redis
    if _redis:
        await _redis.close()
        _redis = None


class RedisClient:
    """Helper methods for all Redis operations used by the bot."""

    # ── JSON store / retrieve ─────────────────────────────────────
    async def set(self, key: str, value: Any, ttl: int = None):
        r = await get_redis()
        data = json.dumps(value, default=str)
        if ttl:
            await r.setex(key, ttl, data)
        else:
            await r.set(key, data)

    async def get(self, key: str) -> Optional[Any]:
        r = await get_redis()
        raw = await r.get(key)
        return json.loads(raw) if raw else None

    async def delete(self, key: str):
        r = await get_redis()
        await r.delete(key)

    # ── Price buffer (rolling window of OHLCV bars) ───────────────
    async def push_bar(self, symbol: str, interval: str, bar: dict, max_bars: int = 500):
        r = await get_redis()
        key = f"bars:{symbol}:{interval}"
        await r.rpush(key, json.dumps(bar, default=str))
        length = await r.llen(key)
        if length > max_bars:
            await r.ltrim(key, length - max_bars, -1)

    async def get_bars(self, symbol: str, interval: str, n: int = 200) -> list:
        r = await get_redis()
        key = f"bars:{symbol}:{interval}"
        raw = await r.lrange(key, -n, -1)
        return [json.loads(b) for b in raw]

    async def bar_count(self, symbol: str, interval: str) -> int:
        r = await get_redis()
        return await r.llen(f"bars:{symbol}:{interval}")

    # ── Live prices (hash: symbol → price) ───────────────────────
    async def set_price(self, symbol: str, price: float):
        r = await get_redis()
        await r.hset("prices:live", symbol, str(price))

    async def get_prices(self) -> dict:
        r = await get_redis()
        raw = await r.hgetall("prices:live")
        return {k: float(v) for k, v in raw.items()}

    async def get_price(self, symbol: str) -> Optional[float]:
        r = await get_redis()
        val = await r.hget("prices:live", symbol)
        return float(val) if val else None

    # ── Portfolio state ────────────────────────────────────────────
    async def set_portfolio(self, state: dict):
        await self.set("portfolio:state", state)

    async def get_portfolio(self) -> Optional[dict]:
        return await self.get("portfolio:state")

    # ── Engine status ──────────────────────────────────────────────
    async def set_status(self, status: dict):
        await self.set("engine:status", status, ttl=120)

    async def get_status(self) -> Optional[dict]:
        return await self.get("engine:status")

    # ── Pub/Sub (push updates to dashboard WebSocket) ─────────────
    async def publish(self, channel: str, message: dict):
        r = await get_redis()
        await r.publish(channel, json.dumps(message, default=str))

    # ── Signal state per symbol ────────────────────────────────────
    async def set_signal(self, strategy: str, symbol: str, signal: dict):
        await self.set(f"signal:{strategy}:{symbol}", signal, ttl=3600)

    async def get_signal(self, strategy: str, symbol: str) -> Optional[dict]:
        return await self.get(f"signal:{strategy}:{symbol}")

    # ── Kalman filter state persistence ───────────────────────────
    async def save_kalman_state(self, symbol: str, state: dict):
        await self.set(f"kalman:{symbol}", state)

    async def load_kalman_state(self, symbol: str) -> Optional[dict]:
        return await self.get(f"kalman:{symbol}")


redis_client = RedisClient()
