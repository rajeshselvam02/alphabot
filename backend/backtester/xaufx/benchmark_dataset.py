from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List

from backend.core.xaufx.models import Candle


def candle_to_dict(candle: Candle) -> Dict[str, Any]:
    return {
        "ts": candle.ts.isoformat(),
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
    }


def candle_from_dict(payload: Dict[str, Any]) -> Candle:
    return Candle(
        ts=datetime.fromisoformat(payload["ts"]),
        open=float(payload["open"]),
        high=float(payload["high"]),
        low=float(payload["low"]),
        close=float(payload["close"]),
        volume=float(payload.get("volume", 0.0) or 0.0),
    )


def dataset_hash(payload: Dict[str, Any]) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(normalized.encode("utf-8")).hexdigest()


def build_snapshot_payload(
    *,
    profile: str,
    symbol: str,
    provider: str,
    intraday_interval: str,
    daily_interval: str,
    intraday_candles: List[Candle],
    daily_candles: List[Candle],
) -> Dict[str, Any]:
    frozen_inputs = {
        "symbol": symbol,
        "provider": provider,
        "intraday_interval": intraday_interval,
        "daily_interval": daily_interval,
        "intraday_bars": [candle_to_dict(c) for c in intraday_candles],
        "daily_bars": [candle_to_dict(c) for c in daily_candles],
    }
    payload = {
        "profile": profile,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "frozen_inputs": frozen_inputs,
    }
    payload["dataset_hash"] = dataset_hash(frozen_inputs)
    return payload


def save_snapshot(path: str | Path, payload: Dict[str, Any]) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def load_snapshot(path: str | Path) -> Dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    frozen_inputs = payload["frozen_inputs"]
    intraday_candles = [candle_from_dict(c) for c in frozen_inputs["intraday_bars"]]
    daily_candles = [candle_from_dict(c) for c in frozen_inputs["daily_bars"]]
    return {
        "profile": payload.get("profile", ""),
        "fetched_at": payload.get("fetched_at", ""),
        "dataset_hash": payload.get("dataset_hash", ""),
        "symbol": frozen_inputs["symbol"],
        "provider": frozen_inputs["provider"],
        "intraday_interval": frozen_inputs["intraday_interval"],
        "daily_interval": frozen_inputs["daily_interval"],
        "intraday_candles": intraday_candles,
        "daily_candles": daily_candles,
    }
