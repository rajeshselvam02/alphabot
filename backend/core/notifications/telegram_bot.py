"""
Telegram Bot Integration for AlphaBot
Real-time trade alerts, P&L updates, Z-score notifications
"""
import asyncio
import logging
from datetime import datetime, timezone
from telegram import Bot, Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes
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
        action = "BUY" if side == "buy" else "SELL"
        now = datetime.now(timezone.utc).strftime("%H:%M UTC")
        lines = [
            f"TRADE OPENED | {now}",
            f"Symbol: {symbol}",
            f"Action: {action}",
            f"Price: ${price:,.2f}",
            f"Qty: {qty:.4f}",
            f"Z-Score: {zscore:+.3f}",
            f"Take Profit: ${tp:,.2f}",
            f"Stop Loss: ${sl:,.2f}",
        ]
        await self.send_plain("\n".join(lines))

    async def alert_trade_close(self, symbol: str, price: float,
                                qty: float, pnl: float, reason: str):
        result = "WIN" if pnl >= 0 else "LOSS"
        now = datetime.now(timezone.utc).strftime("%H:%M UTC")
        lines = [
            f"TRADE CLOSED [{result}] | {now}",
            f"Symbol: {symbol}",
            f"Price: ${price:,.2f}",
            f"Qty: {qty:.4f}",
            f"PnL: ${pnl:+.2f}",
            f"Reason: {reason}",
        ]
        await self.send_plain("\n".join(lines))

    async def alert_signal(self, symbol: str, zscore: float, signal: str):
        direction = "LONG" if signal == "buy" else "SHORT"
        now = datetime.now(timezone.utc).strftime("%H:%M UTC")
        lines = [
            f"SIGNAL FIRED | {now}",
            f"Symbol: {symbol}",
            f"Direction: {direction}",
            f"Z-Score: {zscore:+.3f}",
        ]
        await self.send_plain("\n".join(lines))

    async def alert_zscore(self, symbol: str, zscore: float):
        direction = "ABOVE" if zscore > 0 else "BELOW"
        lines = [
            "Z-SCORE ALERT",
            f"Symbol: {symbol}",
            f"Z-Score: {zscore:+.3f} ({direction} mean)",
            "Signal threshold: +-1.5",
        ]
        await self.send_plain("\n".join(lines))

    async def alert_halt(self, reason: str, drawdown: float):
        lines = [
            "BOT HALTED",
            f"Reason: {reason}",
            f"Drawdown: {drawdown:.2f}%",
            "All trading suspended.",
        ]
        await self.send_plain("\n".join(lines))

    async def status(self, equity: float, trades: int,
                     zscores: dict, drawdown: float):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        z_lines = []
        for sym, z in zscores.items():
            fz = float(z)
            if fz != 0.0:
                arrow = "^" if fz > 0 else "v"
                alert = " <<< SIGNAL!" if abs(fz) > 1.5 else ""
                z_lines.append(f"  {sym[:3]}: {fz:+.3f} {arrow}{alert}")
        z_text = "\n".join(z_lines) if z_lines else "  No data yet"
        lines = [
            f"ALPHABOT STATUS | {now}",
            "",
            f"Equity: ${equity:,.2f}",
            f"Trades: {trades}",
            f"Drawdown: {drawdown:.2f}%",
            "",
            "Z-Scores:",
            z_text,
        ]
        await self.send_plain("\n".join(lines))


telegram = TelegramNotifier()


class TelegramCommandBot:
    """
    Telegram command bot — query AlphaBot from Telegram.
    Commands: /status /zscores /positions /trades /pnl /help
    """
    def __init__(self, notifier: TelegramNotifier):
        self.notifier = notifier
        self.app      = None
        self._get_portfolio  = None
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
        self.app.add_handler(CommandHandler("trades",    self._cmd_trades))
        self.app.add_handler(CommandHandler("pnl",       self._cmd_pnl))
        self.app.add_handler(CommandHandler("help",      self._cmd_help))
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        logger.info("[TG] Command bot started — /status /zscores /positions /trades /pnl /help")

    async def stop(self):
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        lines = [
            "AlphaBot Commands:",
            "",
            "/status   — equity, trades, drawdown",
            "/zscores  — live Z-scores for all pairs",
            "/positions — open positions with P&L",
            "/trades   — last 5 trades",
            "/pnl      — full P&L report",
            "/help     — show this message",
        ]
        await update.message.reply_text("\n".join(lines))

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._get_portfolio:
            await update.message.reply_text("Bot not ready yet.")
            return
        p = self._get_portfolio()
        lines = [
            "ALPHABOT STATUS",
            "",
            f"Equity:   ${p.get('equity', 0):,.2f}",
            f"Cash:     ${p.get('cash', 0):,.2f}",
            f"P&L:      ${p.get('equity', 10000) - 10000:+.2f}",
            f"Trades:   {p.get('total_trades', 0)}",
            f"Win Rate: {p.get('win_rate', 0):.1f}%",
            f"Drawdown: {p.get('drawdown', 0):.2f}%",
        ]
        await update.message.reply_text("\n".join(lines))

    async def _cmd_zscores(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._get_strategies:
            await update.message.reply_text("Bot not ready yet.")
            return
        s = self._get_strategies()
        lines = ["LIVE Z-SCORES", ""]
        for sym, z in s.get('last_z', {}).items():
            fz = float(z)
            if fz != 0.0:
                alert = " <<< SIGNAL!" if abs(fz) > 1.5 else ""
                lines.append(f"{sym[:3]}: {fz:+.4f}{alert}")
        if len(lines) == 2:
            lines.append("No Z-scores yet.")
        await update.message.reply_text("\n".join(lines))

    async def _cmd_positions(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._get_positions:
            await update.message.reply_text("Bot not ready yet.")
            return
        positions = self._get_positions()
        if not positions:
            await update.message.reply_text("No open positions.")
            return
        lines = ["OPEN POSITIONS", ""]
        for sym, pos in positions.items():
            lines.append(f"{sym}")
            lines.append(f"  Side:    {pos['side']}")
            lines.append(f"  Entry:   ${pos['entry_price']:,.2f}")
            lines.append(f"  Current: ${pos['current_price']:,.2f}")
            lines.append(f"  PnL:     ${pos['unrealized_pnl']:+.2f}")
            lines.append("")
        await update.message.reply_text("\n".join(lines))

    async def _cmd_trades(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._get_portfolio:
            await update.message.reply_text("Bot not ready.")
            return
        p = self._get_portfolio()
        trades = p.get("trades", [])[-5:]
        if not trades:
            await update.message.reply_text("No trades yet.")
            return
        lines = ["LAST 5 TRADES", ""]
        for tr in reversed(trades):
            pnl = tr.get("pnl")
            pnl_str = f"PnL: ${pnl:+.2f}" if pnl is not None else "Open"
            lines.append(f"{tr['symbol']} {tr['side'].upper()} @ ${tr['fill_price']:,.2f}")
            lines.append(f"  {pnl_str}")
        await update.message.reply_text("\n".join(lines))

    async def _cmd_pnl(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._get_portfolio:
            await update.message.reply_text("Bot not ready.")
            return
        p = self._get_portfolio()
        equity   = p.get("equity", 10000)
        pnl      = equity - 10000
        pnl_pct  = pnl / 10000 * 100
        wins     = p.get("wins", 0)
        losses   = p.get("losses", 0)
        total    = wins + losses
        win_rate = wins / total * 100 if total > 0 else 0
        fees     = p.get("total_fees", 0)
        result   = "Up" if pnl >= 0 else "Down"
        lines = [
            f"P&L REPORT [{result}]",
            "",
            f"Starting:  $10,000.00",
            f"Equity:    ${equity:,.2f}",
            f"Total P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)",
            f"Fees paid: ${fees:.2f}",
            "",
            f"Trades:    {total}",
            f"Wins:      {wins}",
            f"Losses:    {losses}",
            f"Win Rate:  {win_rate:.1f}%",
        ]
        await update.message.reply_text("\n".join(lines))


command_bot = TelegramCommandBot(telegram)
