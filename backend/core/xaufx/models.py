from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class Signal:
    strategy: str
    symbol: str
    side: str
    score: float = 0.0
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    reason: str = ""


@dataclass
class Position:
    symbol: str
    side: str
    qty: float
    entry_price: float
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    opened_at: Optional[datetime] = None


@dataclass
class Fill:
    symbol: str
    side: str
    qty: float
    price: float
    ts: datetime
    fee: float = 0.0
    strategy: str = ""


@dataclass
class PortfolioState:
    starting_equity: float
    equity: float
    cash: float
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    positions: Dict[str, Position] = field(default_factory=dict)
    trades_today: int = 0
    halted: bool = False
    halt_reason: str = ""


@dataclass
class StrategySnapshot:
    strategy: str
    symbol: str
    signal: str
    meta: Dict[str, float | str | int] = field(default_factory=dict)


@dataclass
class EngineSnapshot:
    mode: str
    equity: float
    halted: bool
    strategies: List[StrategySnapshot] = field(default_factory=list)
