"""
Phase 0 Fix 1: Portfolio State Persistence
- Saves full portfolio state to Redis on every trade
- Loads state on startup so positions survive restarts
"""
import ast

# ── Fix paper_trader.py ──────────────────────────────────────────
t = open('backend/core/execution/paper_trader.py').read()

# 1. Add load_state method + save_state call
old_init = '''    def __init__(self):
        self.cash:       float              = settings.INITIAL_CAPITAL
        self.equity:     float              = settings.INITIAL_CAPITAL
        self.positions:  Dict[str, dict]    = {}
        self.trades:     list               = []
        self.total_fees: float              = 0.0
        self.wins:       int                = 0
        self.losses:     int                = 0'''

new_init = '''    def __init__(self):
        self.cash:       float              = settings.INITIAL_CAPITAL
        self.equity:     float              = settings.INITIAL_CAPITAL
        self.positions:  Dict[str, dict]    = {}
        self.trades:     list               = []
        self.total_fees: float              = 0.0
        self.wins:       int                = 0
        self.losses:     int                = 0

    async def load_state(self):
        """Load portfolio state from Redis — called on startup before warmup."""
        try:
            raw = await redis_client.get('portfolio_state')
            if raw:
                import json
                s = json.loads(raw)
                self.cash       = s.get('cash', settings.INITIAL_CAPITAL)
                self.equity     = s.get('equity', settings.INITIAL_CAPITAL)
                self.positions  = s.get('positions', {})
                self.trades     = s.get('trades', [])
                self.total_fees = s.get('total_fees', 0.0)
                self.wins       = s.get('wins', 0)
                self.losses     = s.get('losses', 0)
                import logging
                logging.getLogger("alphabot.paper").info(
                    f"[PAPER] State restored: cash=${self.cash:,.2f} "
                    f"positions={len(self.positions)} trades={len(self.trades)}"
                )
            else:
                logging.getLogger("alphabot.paper").info(
                    "[PAPER] No saved state found — starting fresh"
                )
        except Exception as e:
            import logging
            logging.getLogger("alphabot.paper").warning(f"[PAPER] State load failed: {e}")

    async def save_state(self):
        """Persist full portfolio state to Redis — called after every trade."""
        try:
            import json
            state = {
                'cash':       self.cash,
                'equity':     self.equity,
                'positions':  self.positions,
                'trades':     self.trades[-200:],  # keep last 200
                'total_fees': self.total_fees,
                'wins':       self.wins,
                'losses':     self.losses,
            }
            await redis_client.set('portfolio_state', json.dumps(state))
        except Exception as e:
            import logging
            logging.getLogger("alphabot.paper").warning(f"[PAPER] State save failed: {e}")'''

if old_init in t:
    t = t.replace(old_init, new_init)
    print("FIXED: Added load_state() and save_state() methods")
else:
    print("Pattern not found for __init__ — check manually")

# 2. Call save_state() at end of execute()
old_push = '''        await redis_client.publish("trades", trade)
        await self._push_state()'''
new_push = '''        await redis_client.publish("trades", trade)
        await self._push_state()
        await self.save_state()'''

if old_push in t:
    t = t.replace(old_push, new_push)
    print("FIXED: save_state() called after every trade")
else:
    print("Pattern not found for save_state call")

open('backend/core/execution/paper_trader.py', 'w').write(t)

try:
    ast.parse(open('backend/core/execution/paper_trader.py').read())
    print("paper_trader.py syntax OK")
except SyntaxError as e:
    print(f"SYNTAX ERROR: {e}")

# ── Fix engine.py — load state on startup ───────────────────────
t = open('backend/core/engine.py').read()

old_warmup_end = '''        bollinger_strategy.is_active = True
        cs_strategy.is_active        = True
        risk_manager.reset_peak()'''

new_warmup_end = '''        bollinger_strategy.is_active = True
        cs_strategy.is_active        = True
        risk_manager.reset_peak()
        # Restore portfolio state from Redis (survives restarts)
        await paper_trader.load_state()'''

if old_warmup_end in t:
    t = t.replace(old_warmup_end, new_warmup_end)
    print("FIXED: engine.py loads portfolio state after warmup")
else:
    print("Pattern not found for engine warmup end")

open('backend/core/engine.py', 'w').write(t)

try:
    ast.parse(open('backend/core/engine.py').read())
    print("engine.py syntax OK")
except SyntaxError as e:
    print(f"SYNTAX ERROR: {e}")

print("\nPhase 0 Fix 1 complete — portfolio state now persists across restarts")
