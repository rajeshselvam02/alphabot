from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple

import requests

from ..models import Candle


class TwelveDataQuotaExceeded(RuntimeError):
    pass


class TwelveDataFeed:
    BASE_URL = "https://api.twelvedata.com/time_series"
    MAX_OUTPUTSIZE = 5000
    _quota_exhausted_until_utc: datetime | None = None

    def __init__(self, api_key: str, timeout: int = 20) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self._cache: Dict[Tuple[str, str, int], Tuple[datetime, List[Candle]]] = {}

    @classmethod
    def _quota_blocked(cls) -> bool:
        if cls._quota_exhausted_until_utc is None:
            return False
        return datetime.now(timezone.utc) < cls._quota_exhausted_until_utc

    @classmethod
    def _block_until_next_utc_day(cls) -> None:
        now = datetime.now(timezone.utc)
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        cls._quota_exhausted_until_utc = tomorrow

    def _ttl_seconds(self, interval: str) -> int:
        if interval == "1day":
            return 3600
        if interval == "1h":
            return 600
        if interval == "15min":
            return 180
        return 300

    def _request_chunk(
        self,
        symbol: str,
        interval: str,
        outputsize: int,
        end_date: str | None = None,
    ) -> List[Candle]:
        params = {
            "symbol": self._map_symbol(symbol),
            "interval": interval,
            "outputsize": min(outputsize, self.MAX_OUTPUTSIZE),
            "apikey": self.api_key,
            "format": "JSON",
            "timezone": "UTC",
        }
        if end_date is not None:
            params["end_date"] = end_date

        resp = requests.get(self.BASE_URL, params=params, timeout=self.timeout)
        resp.raise_for_status()
        payload = resp.json()

        message = str(payload.get("message", "")).lower()
        status = str(payload.get("status", "")).lower()

        if "run out of api credits" in message or "credits were used" in message:
            self._block_until_next_utc_day()
            raise TwelveDataQuotaExceeded(payload.get("message", "Twelve Data quota exhausted"))

        if status == "error":
            raise RuntimeError(f"Twelve Data error for {symbol}: {payload.get('message', 'unknown error')}")

        values = payload.get("values", [])
        if not values:
            return []

        candles: List[Candle] = []
        for row in reversed(values):
            candles.append(
                Candle(
                    ts=datetime.fromisoformat(row["datetime"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0.0) or 0.0),
                )
            )
        return candles

    def fetch_bars(self, symbol: str, interval: str, outputsize: int = 300) -> List[Candle]:
        if self._quota_blocked():
            raise TwelveDataQuotaExceeded("Twelve Data quota exhausted; blocked until next UTC day")

        key = (symbol, interval, outputsize)
        now = datetime.now(timezone.utc)
        ttl = self._ttl_seconds(interval)

        cached = self._cache.get(key)
        if cached is not None:
            cached_at, cached_candles = cached
            age = (now - cached_at).total_seconds()
            if age < ttl:
                return cached_candles

        if outputsize <= self.MAX_OUTPUTSIZE:
            candles = self._request_chunk(symbol, interval, outputsize)
            self._cache[key] = (now, candles)
            return candles

        remaining = outputsize
        end_date: str | None = None
        merged: List[Candle] = []
        seen_ts: set[datetime] = set()

        while remaining > 0:
            chunk_size = min(remaining, self.MAX_OUTPUTSIZE)
            chunk = self._request_chunk(symbol, interval, chunk_size, end_date=end_date)

            if not chunk:
                break

            new_chunk = []
            for c in chunk:
                if c.ts not in seen_ts:
                    seen_ts.add(c.ts)
                    new_chunk.append(c)

            if not new_chunk:
                break

            merged = new_chunk + merged
            remaining = outputsize - len(merged)

            oldest = new_chunk[0].ts
            prior = oldest - timedelta(seconds=1)
            end_date = prior.strftime("%Y-%m-%d %H:%M:%S")

            if len(chunk) < chunk_size:
                break

        merged.sort(key=lambda c: c.ts)
        if len(merged) > outputsize:
            merged = merged[-outputsize:]

        self._cache[key] = (now, merged)
        return merged

    @staticmethod
    def _map_symbol(symbol: str) -> str:
        mapping = {
            "XAUUSD": "XAU/USD",
            "EURUSD": "EUR/USD",
            "GBPUSD": "GBP/USD",
            "USDJPY": "USD/JPY",
            "USDCHF": "USD/CHF",
            "AUDUSD": "AUD/USD",
            "USDCAD": "USD/CAD",
        }
        return mapping.get(symbol, symbol)
