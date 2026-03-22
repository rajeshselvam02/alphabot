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

from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update

class TelegramCommandBot:
    """
    Telegram command bot — query AlphaBot from Telegram.
    Commands: /status /zscores /positions /pause /resume
    """
    def __init__(self, notifier: TelegramNotifier):
        self.notifier = notifier
        self.app      = None
        self._get_portfolio = None
        self._get_strategies = None
        self._get_positions  = None

    def register_callbacks(self, portfolio_fn, strategies_fn, positions_fn):
        self._get_portfolio  = portfolio_fn
        self._get_strategies = strategies_fn
        self._get_positions  = positions_fn

    async def start(self):
        if not self.notifier.enabled:
            return
        self.app = Application.builder().token(self.notifier.token).build()
        self.app.add_handler(CommandHandler("status",    self._cmd_status))
        self.app.add_handler(CommandHandler("zscores",   self._cmd_zscores))
        self.app.add_handler(CommandHandler("positions", self._cmd_positions))
        self.app.add_handler(CommandHandler("help",      self._cmd_help))
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        logger.info("[TG] Command bot started — /status /zscores /positions /help")

    async def stop(self):
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        msg = (
            "AlphaBot Commands:\n\n"
            "/status — equity, trades, drawdown\n"
            "/zscores — live Z-scores for all pairs\n"
            "/positions — open positions with P&L\n"
            "/help — show this message"
        )
        await update.message.reply_text(msg)

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._get_portfolio:
            await update.message.reply_text("Bot not ready yet.")
            return
        p = self._get_portfolio()
        msg = (
            f"ALPHABOT STATUS\n\n"
            f"Equity: ${p.get('equity', 0):,.2f}\n"
            f"Cash: ${p.get('cash', 0):,.2f}\n"
            f"P&L: ${p.get('total_pnl', 0):+.2f}\n"
            f"Trades: {p.get('total_trades', 0)}\n"
            f"Win Rate: {p.get('win_rate', 0):.1f}%\n"
            f"Drawdown: {p.get('drawdown', 0):.2f}%\n"
            f"Sharpe: {p.get('sharpe', 0):.3f}"
        )
        await update.message.reply_text(msg)

    async def _cmd_zscores(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._get_strategies:
            await update.message.reply_text("Bot not ready yet.")
            return
        s = self._get_strategies()
        lines = []
        for sym, z in s.get('last_z', {}).items():
            fz = float(z)
            if fz != 0.0:
                alert = " <<< SIGNAL" if abs(fz) > 1.5 else ""
                lines.append(f"{sym[:3]}: {fz:+.4f}{alert}")
        msg = "LIVE Z-SCORES\n\n" + "\n".join(lines) if lines else "No Z-scores yet."
        await update.message.reply_text(msg)

    async def _cmd_positions(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._get_positions:
            await update.message.reply_text("Bot not ready yet.")
            return
        positions = self._get_positions()
        if not positions:
            await update.message.reply_text("No open positions.")
            return
        lines = []
        for sym, pos in positions.items():
            lines.append(
                f"{sym}\n"
                f"  Side: {pos['side']}\n"
                f"  Entry: ${pos['entry_price']:,.2f}\n"
                f"  Current: ${pos['current_price']:,.2f}\n"
                f"  PnL: ${pos['unrealized_pnl']:+.2f}"
            )
        await update.message.reply_text("OPEN POSITIONS\n\n" + "\n\n".join(lines))

command_bot = TelegramCommandBot(telegram)
