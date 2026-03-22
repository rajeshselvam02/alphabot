"""
AlphaBot Configuration — Termux/Android Optimized
Uses SQLite (no PostgreSQL needed) + Redis for live state
"""
from pydantic_settings import BaseSettings
from typing import List
import os

class Settings(BaseSettings):
    TELEGRAM_TOKEN:   str = ""
    TELEGRAM_CHAT_ID: str = ""
    # ── App ──────────────────────────────────────────────────────
    APP_NAME: str       = "AlphaBot"
    DEBUG: bool         = False
    HOST: str           = "0.0.0.0"
    PORT: int           = 8000

    # ── Database (SQLite - works natively on Termux) ─────────────
    DATABASE_URL: str   = "sqlite+aiosqlite:///./alphabot.db"
    DATABASE_URL_SYNC: str = "sqlite:///./alphabot.db"

    # ── Redis ────────────────────────────────────────────────────
    REDIS_URL: str      = "redis://localhost:6379/0"

    # ── Trading Mode ─────────────────────────────────────────────
    TRADING_MODE: str   = "paper"           # paper | live
    INITIAL_CAPITAL: float = 10000.0

    # ── Binance ──────────────────────────────────────────────────
    BINANCE_API_KEY: str    = ""
    BINANCE_API_SECRET: str = ""
    BINANCE_TESTNET: bool   = True

    # ── Alpaca (US Stocks) ───────────────────────────────────────
    ALPACA_API_KEY: str    = ""
    ALPACA_API_SECRET: str = ""
    ALPACA_BASE_URL: str   = "https://paper-api.alpaca.markets"

    # ── Zerodha ──────────────────────────────────────────────────
    ZERODHA_API_KEY: str      = ""
    ZERODHA_API_SECRET: str   = ""
    ZERODHA_ACCESS_TOKEN: str = ""

    # ── Risk Management (from Ernie Chan Ch.8 / Platen Ch.11) ────
    KELLY_FRACTION: float       = 0.25    # fractional Kelly (conservative)
    MAX_POSITION_PCT: float     = 0.15    # max 15% per position
    MAX_DRAWDOWN_HALT: float    = 0.10    # halt at 10% drawdown
    MAX_DAILY_LOSS_PCT: float   = 0.03    # halt day at 3% daily loss
    MEAN_REV_NO_STOP: bool      = True    # no stop-loss on MR (Chan Ch.8)
    MOMENTUM_STOP_PCT: float    = 0.03    # 3% stop on momentum trades
    MAX_LEVERAGE: float         = 2.0     # max 2x total leverage

    # ── Strategy 1: Bollinger MR ─────────────────────────────────
    # Based on: Chan Ch.3, Williams Ch.10 (martingale theory),
    #           Platen Ch.4 (OU process), Platen Ch.7 (SDE solutions)
    BOLLINGER_PAIRS: List[str]  = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
    BOLLINGER_LOOKBACK: int     = 5
    BOLLINGER_ENTRY_Z: float    = 2.0
    BOLLINGER_EXIT_Z: float     = 0.0
    BOLLINGER_INTERVAL: str     = "1h"
    BOLLINGER_DELTA: float      = 0.0001  # Kalman filter adaptation rate
    BOLLINGER_VE: float         = 0.001   # Kalman measurement noise

    # ── Strategy 2: Cross-Sectional Momentum ─────────────────────
    # Based on: Khandani-Lo (2007), Chan Ch.4
    CS_UNIVERSE: List[str]  = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT",
                                "ADAUSDT","DOTUSDT","LINKUSDT","MATICUSDT"]
    CS_TOP_N: int           = 2    # buy top N performers
    CS_BOTTOM_N: int        = 2    # short bottom N performers
    CS_LOOKBACK: int        = 24   # hours lookback for relative returns
    CS_INTERVAL: str        = "1h"

    # ── Strategy 3: Buy-on-Gap ────────────────────────────────────
    # Based on: Chan Ch.4, adapted for crypto (no gap but uses ATH deviation)
    BOG_STD_LOOKBACK: int   = 90
    BOG_MA_LOOKBACK: int    = 20
    BOG_ENTRY_Z: float      = 1.0
    BOG_TOP_N: int          = 3
    BOG_INTERVAL: str       = "4h"

    # ── Logging ──────────────────────────────────────────────────
    LOG_FILE: str           = "./logs/bot.log"
    LOG_LEVEL: str          = "INFO"
    LOG_ROTATION: str       = "10 MB"

    BINANCE_FUTURES_API_KEY:    str  = ""
    BINANCE_FUTURES_API_SECRET: str  = ""
    BINANCE_FUTURES_TESTNET:    bool = True
    FUTURES_LEVERAGE:           int  = 1
    FUTURES_MAX_POSITION_PCT:   float = 0.10
    DERIBIT_CLIENT_ID:          str  = ""
    DERIBIT_CLIENT_SECRET:      str  = ""
    DERIBIT_TESTNET:            bool = True
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
