import sys
sys.path.insert(0, '/root/alphabot')

t = open('backend/core/notifications/telegram_bot.py').read()

# Add handlers
t = t.replace(
    '        self.app.add_handler(CommandHandler("help",      self._cmd_help))',
    '        self.app.add_handler(CommandHandler("help",      self._cmd_help))\n        self.app.add_handler(CommandHandler("trades",    self._cmd_trades))\n        self.app.add_handler(CommandHandler("pnl",       self._cmd_pnl))'
)

new_cmds = '''
    async def _cmd_trades(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._get_portfolio:
            await update.message.reply_text("Bot not ready.")
            return
        p = self._get_portfolio()
        trades = p.get("trades", [])[-5:]
        if not trades:
            await update.message.reply_text("No trades yet.")
            return
        lines = []
        for tr in reversed(trades):
            pnl = tr.get("pnl")
            pnl_str = f"PnL: ${pnl:+.2f}" if pnl is not None else "Open"
            lines.append(f"{tr['symbol']} {tr['side'].upper()} @ ${tr['fill_price']:,.2f} | {pnl_str}")
        await update.message.reply_text("LAST 5 TRADES\\n\\n" + "\\n".join(lines))

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
        emoji    = "Up" if pnl >= 0 else "Down"
        lines = [
            f"P&L REPORT [{emoji}]",
            f"Starting: $10,000.00",
            f"Equity:   ${equity:,.2f}",
            f"P&L:      ${pnl:+.2f} ({pnl_pct:+.2f}%)",
            f"Fees:     ${fees:.2f}",
            f"Trades: {total} | W: {wins} | L: {losses}",
            f"Win Rate: {win_rate:.1f}%",
        ]
        await update.message.reply_text("\\n".join(lines))

'''

t = t.replace(
    'command_bot = TelegramCommandBot(telegram)',
    new_cmds + 'command_bot = TelegramCommandBot(telegram)'
)

open('backend/core/notifications/telegram_bot.py', 'w').write(t)
print('done')
