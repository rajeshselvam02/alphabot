"""
Database — SQLite + SQLAlchemy (Termux compatible, no PostgreSQL needed)
Models cover: trades, positions, daily performance, strategy state, price bars
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import (
    Column, String, Float, DateTime, Integer,
    Boolean, JSON, Text, Index
)
from datetime import datetime, timezone
import uuid
from backend.config.settings import settings


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# ── Models ────────────────────────────────────────────────────────

class Trade(Base):
    __tablename__ = "trades"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    symbol      = Column(String(20), nullable=False, index=True)
    strategy    = Column(String(30), nullable=False, index=True)
    side        = Column(String(10), nullable=False)         # buy | sell
    quantity    = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price  = Column(Float, nullable=True)
    entry_time  = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    exit_time   = Column(DateTime, nullable=True)
    status      = Column(String(15), default="filled")       # filled | cancelled
    pnl         = Column(Float, nullable=True)
    pnl_pct     = Column(Float, nullable=True)
    mode        = Column(String(10), default="paper")
    signal_data = Column(JSON, nullable=True)                # zscore, hedge_ratio, etc.
    notes       = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_trades_entry_time", "entry_time"),
    )


class Position(Base):
    __tablename__ = "positions"

    id              = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    symbol          = Column(String(20), nullable=False, unique=True, index=True)
    strategy        = Column(String(30), nullable=False)
    side            = Column(String(10), nullable=False)     # long | short
    quantity        = Column(Float, nullable=False)
    entry_price     = Column(Float, nullable=False)
    current_price   = Column(Float, nullable=True)
    unrealized_pnl  = Column(Float, default=0.0)
    unrealized_pct  = Column(Float, default=0.0)
    entry_time      = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    is_open         = Column(Boolean, default=True)
    stop_price      = Column(Float, nullable=True)
    signal_data     = Column(JSON, nullable=True)


class DailyPerformance(Base):
    __tablename__ = "daily_performance"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    date            = Column(String(10), unique=True, index=True)  # YYYY-MM-DD
    starting_equity = Column(Float, nullable=False)
    ending_equity   = Column(Float, nullable=False)
    daily_pnl       = Column(Float, nullable=False)
    daily_return    = Column(Float, nullable=False)
    num_trades      = Column(Integer, default=0)
    sharpe_rolling  = Column(Float, nullable=True)
    max_drawdown    = Column(Float, nullable=True)
    strategy_breakdown = Column(JSON, nullable=True)


class StrategyState(Base):
    __tablename__ = "strategy_states"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    strategy    = Column(String(30), nullable=False, unique=True)
    is_active   = Column(Boolean, default=True)
    is_halted   = Column(Boolean, default=False)
    halt_reason = Column(Text, nullable=True)
    params      = Column(JSON, nullable=True)
    stats       = Column(JSON, nullable=True)
    updated_at  = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class PriceBar(Base):
    """Historical OHLCV bars stored for backtesting."""
    __tablename__ = "price_bars"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    symbol    = Column(String(20), nullable=False, index=True)
    interval  = Column(String(10), nullable=False)
    open      = Column(Float, nullable=False)
    high      = Column(Float, nullable=False)
    low       = Column(Float, nullable=False)
    close     = Column(Float, nullable=False)
    volume    = Column(Float, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)

    __table_args__ = (
        Index("ix_price_bars_symbol_interval_ts", "symbol", "interval", "timestamp"),
    )


# ── Session helpers ───────────────────────────────────────────────

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    """Create all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
