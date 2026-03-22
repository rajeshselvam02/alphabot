import asyncio
import logging
from datetime import datetime, timezone
from telegram import Bot
from telegram.error import TelegramError
from backend.config.settings import settings

logger = logging.getLogger("alphabot.telegram")

class TelegramNotifier:
    def __init__(self):
        self.token   = getattr(settings, 'TELEGRAM_TOKEN', None)
        self.chat_id = getattr(settings, 'TELEGRAM_CHAT_ID', None)
        self.bot     = None
        self.enabled = bool(self.token and self.chat_id)

    async def init(self):
        if not self.enabled:
            logger.warning("[TG] Telegram not configured")
            return
        try:
            self.bot = Bot(token=self.token)
            me = await self.bot.get_me()
            logger.info(f"[TG] Connected as @{me.username}")
            await self.send_plain("AlphaBot Online - Paper trading started. Watching for signals.")
        except Exception as e:
            logger.error(f"[TG] Init failed: {e}")
            self.enabled = False

    async def send_plain(self, text: str):
        if not self.enabled or not self.bot:
            return
        try:
            await self.bot.send_message(chat_id=self.chat_id, text=text)
        except TelegramError as e:
            logger.error(f"[TG] Send error: {e}")

    async def alert_trade_open(self, side: str, symbol: str, price: float,
                               qty: float, zscore: float, tp: float, sl: float):
        emoji = "BUY" if side == "buy" else "SELL"
        now = datetime.now(timezone.utc).strftime("%H:%M UTC")
        msg = (
            f"TRADE OPENED | {now}\n"
            f"Symbol: {symbol}\n"
            f"Action: {emoji}\n"
            f"Price: ${price:,.2f}\n"
            f"Qty: {qty:.4f}\n"
            f"Z-Score: {zscore:+.3f}\n"
            f"Take Profit: ${tp:,.2f}\n"
            f"Stop Loss: ${sl:,.2f}"
        )
        await self.send_plain(msg)

    async def alert_trade_close(self, symbol: str, price: float,
                                qty: float, pnl: float, reason: str):
        emoji = "WIN" if pnl >= 0 else "LOSS"
        now = datetime.now(timezone.utc).strftime("%H:%M UTC")
        msg = (
            f"TRADE CLOSED [{emoji}] | {now}\n"
            f"Symbol: {symbol}\n"
            f"Price: ${price:,.2f}\n"
            f"Qty: {qty:.4f}\n"
            f"PnL: ${pnl:+.2f}\n"
            f"Reason: {reason}"
        )
        await self.send_plain(msg)

    async def alert_signal(self, symbol: str, zscore: float, signal: str):
        emoji = "LONG" if signal == "buy" else "SHORT"
        now = datetime.now(timezone.utc).strftime("%H:%M UTC")
        msg = (
            f"SIGNAL FIRED | {now}\n"
            f"Symbol: {symbol}\n"
            f"Direction: {emoji}\n"
            f"Z-Score: {zscore:+.3f}"
        )
        await self.send_plain(msg)

    async def alert_zscore(self, symbol: str, zscore: float):
        direction = "ABOVE" if zscore > 0 else "BELOW"
        msg = (
            f"Z-SCORE ALERT\n"
            f"Symbol: {symbol}\n"
            f"Z-Score: {zscore:+.3f} ({direction} mean)\n"
            f"Signal threshold: +-1.5"
        )
        await self.send_plain(msg)

    async def alert_halt(self, reason: str, drawdown: float):
        msg = (
            f"BOT HALTED\n"
            f"Reason: {reason}\n"
            f"Drawdown: {drawdown:.2f}%\n"
            f"All trading suspended."
        )
        await self.send_plain(msg)

    async def status(self, equity: float, trades: int,
                     zscores: dict, drawdown: float):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        z_lines = ""
        for sym, z in zscores.items():
            fz = float(z)
            if fz != 0.0:
                arrow = "^" if fz > 0 else "v"
                z_lines += f"  {sym[:3]}: {fz:+.3f} {arrow}\n"
        msg = (
            f"ALPHABOT STATUS | {now}\n\n"
            f"Equity: ${equity:,.2f}\n"
            f"Trades: {trades}\n"
            f"Drawdown: {drawdown:.2f}%\n\n"
            f"Z-Scores:\n{z_lines}"
        )
        await self.send_plain(msg)

telegram = TelegramNotifier()
