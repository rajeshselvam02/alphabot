"""
Phase 0 Fixes 2-5:
  Fix 2: Market data freshness gate
  Fix 3: Durable risk halt in Redis
  Fix 4: Startup validation
  Fix 5: Test isolation cleanup
"""
import ast

# ══════════════════════════════════════════════════
# FIX 2: MARKET DATA FRESHNESS GATE
# ══════════════════════════════════════════════════
t = open('backend/core/strategies/bollinger_mr.py').read()

old_onbar = '''    async def on_bar(self, bar: dict):
        """Entry point — called by engine on each closed bar."""
        symbol = bar["symbol"]
        close  = bar["close"]
        volume = bar.get("volume", 0.0)
        self._init(symbol)'''

new_onbar = '''    async def on_bar(self, bar: dict):
        """Entry point — called by engine on each closed bar."""
        symbol = bar["symbol"]
        close  = bar["close"]
        volume = bar.get("volume", 0.0)
        self._init(symbol)

        # ── Freshness gate: reject stale bars ──────────────────
        try:
            from datetime import datetime, timezone, timedelta
            bar_ts  = bar.get("timestamp", "")
            if bar_ts:
                bar_time = datetime.fromisoformat(bar_ts)
                age = datetime.now(timezone.utc) - bar_time
                max_age = timedelta(minutes=45)  # 3x 15m interval
                if age > max_age and self.is_active:
                    logger.warning(
                        f"[STALE] {symbol} bar is {int(age.total_seconds()//60)}m old "
                        f"(ts={bar_ts[:16]}) — skipping signal"
                    )
                    return
        except Exception:
            pass  # if timestamp parsing fails, allow bar through'''

if old_onbar in t:
    t = t.replace(old_onbar, new_onbar)
    print("FIXED 2: Freshness gate added to on_bar()")
else:
    print("SKIP 2: Pattern not found — already has freshness gate or structure changed")

open('backend/core/strategies/bollinger_mr.py', 'w').write(t)
try:
    ast.parse(open('backend/core/strategies/bollinger_mr.py').read())
    print("bollinger_mr.py syntax OK")
except SyntaxError as e:
    print(f"SYNTAX ERROR bollinger_mr: {e}")


# ══════════════════════════════════════════════════
# FIX 3: DURABLE RISK HALT IN REDIS
# ══════════════════════════════════════════════════
t = open('backend/core/execution/risk_manager.py').read()

old_halt = '''    def _halt(self, reason: str):
        if not self.is_halted:
            self.is_halted   = True
            self.halt_reason = reason
            logger.critical(f"RISK HALT: {reason}")'''

new_halt = '''    def _halt(self, reason: str):
        if not self.is_halted:
            self.is_halted   = True
            self.halt_reason = reason
            logger.critical(f"RISK HALT: {reason}")
            # Persist halt to Redis so restart does not auto-resume
            try:
                import asyncio, json
                from datetime import datetime, timezone
                from backend.db.redis_client import redis_client
                halt_data = json.dumps({
                    "reason": reason,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                asyncio.create_task(redis_client.set("risk:halted", halt_data))
            except Exception as e:
                logger.error(f"[RISK] Failed to persist halt: {e}")'''

old_resume = '''    def resume(self):
        self.is_halted   = False
        self.halt_reason = ""
        logger.info("Risk halt cleared")'''

new_resume = '''    def resume(self):
        self.is_halted   = False
        self.halt_reason = ""
        logger.info("Risk halt cleared")
        # Clear halt from Redis
        try:
            import asyncio
            from backend.db.redis_client import redis_client
            asyncio.create_task(redis_client.delete("risk:halted"))
        except Exception:
            pass'''

if old_halt in t:
    t = t.replace(old_halt, new_halt)
    print("FIXED 3a: Halt persisted to Redis")
else:
    print("SKIP 3a: _halt pattern not found")

if old_resume in t:
    t = t.replace(old_resume, new_resume)
    print("FIXED 3b: Resume clears Redis halt key")
else:
    print("SKIP 3b: resume pattern not found")

open('backend/core/execution/risk_manager.py', 'w').write(t)
try:
    ast.parse(open('backend/core/execution/risk_manager.py').read())
    print("risk_manager.py syntax OK")
except SyntaxError as e:
    print(f"SYNTAX ERROR risk_manager: {e}")


# ══════════════════════════════════════════════════
# FIX 4: STARTUP VALIDATION + HALT RECOVERY IN ENGINE
# ══════════════════════════════════════════════════
t = open('backend/core/engine.py').read()

old_startup_begin = '''    async def startup(self):
        """Initialize all components — called once at startup."""
        logger.info("=" * 55)
        logger.info(f"  ALPHABOT STARTING — Mode: {self.mode.upper()}")
        logger.info(f"  Capital: ${settings.INITIAL_CAPITAL:,.0f}")
        logger.info("=" * 55)

        await init_db()
        logger.info("Database initialized (SQLite)")'''

new_startup_begin = '''    async def startup(self):
        """Initialize all components — called once at startup."""
        logger.info("=" * 55)
        logger.info(f"  ALPHABOT STARTING — Mode: {self.mode.upper()}")
        logger.info(f"  Capital: ${settings.INITIAL_CAPITAL:,.0f}")
        logger.info("=" * 55)

        await init_db()
        logger.info("Database initialized (SQLite)")

        # ── Startup validation ──────────────────────────────────
        await self._validate_startup()'''

old_warmup_start = '''    async def _warmup(self):
        """Replay historical bars — NO trading during warmup."""'''

new_warmup_start = '''    async def _validate_startup(self):
        """Validate critical dependencies and restore durable state."""
        import json
        # Check for durable risk halt
        halt_raw = await redis_client.get("risk:halted")
        if halt_raw:
            try:
                h = json.loads(halt_raw)
                logger.critical(
                    f"[STARTUP] RISK HALT DETECTED from previous session: "
                    f"{h.get('reason')} at {h.get('timestamp','?')}"
                )
                risk_manager.is_halted   = True
                risk_manager.halt_reason = h.get("reason", "Previous session halt")
                logger.critical("[STARTUP] Bot starting in HALTED state — use /api/control/resume to re-enable")
            except Exception:
                pass

        # Check Redis connectivity
        try:
            await redis_client.get("ping_test")
            logger.info("[STARTUP] Redis connection OK")
        except Exception as e:
            logger.critical(f"[STARTUP] Redis unavailable: {e}")

        # Check required settings
        missing = []
        if not settings.TELEGRAM_TOKEN:
            missing.append("TELEGRAM_TOKEN")
        if not settings.TELEGRAM_CHAT_ID:
            missing.append("TELEGRAM_CHAT_ID")
        if missing:
            logger.warning(f"[STARTUP] Missing optional settings: {missing}")
        else:
            logger.info("[STARTUP] All settings validated")

    async def _warmup(self):
        """Replay historical bars — NO trading during warmup."""'''

if old_startup_begin in t:
    t = t.replace(old_startup_begin, new_startup_begin)
    print("FIXED 4a: Startup validation call added")
else:
    print("SKIP 4a: startup pattern not found")

if old_warmup_start in t:
    t = t.replace(old_warmup_start, new_warmup_start)
    print("FIXED 4b: _validate_startup() method added")
else:
    print("SKIP 4b: warmup pattern not found")

open('backend/core/engine.py', 'w').write(t)
try:
    ast.parse(open('backend/core/engine.py').read())
    print("engine.py syntax OK")
except SyntaxError as e:
    print(f"SYNTAX ERROR engine: {e}")


# ══════════════════════════════════════════════════
# FIX 5: TEST ISOLATION — CLEANUP IN TEST SUITE
# ══════════════════════════════════════════════════
t = open('test_alphabot_v2.py').read()

old_end = '''if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)'''

new_end = '''async def cleanup_test_artifacts():
    """Remove test trades and positions from paper_trader after test run."""
    from backend.core.execution.paper_trader import paper_trader
    test_symbols = ['PNL_TEST_LONG', 'PNL_TEST_SHORT', 'ETHUSDT', 'BTCUSDT']
    # Remove test positions
    for sym in list(paper_trader.positions.keys()):
        if sym.startswith('PNL_TEST'):
            del paper_trader.positions[sym]
    # Remove test trades
    paper_trader.trades = [
        t for t in paper_trader.trades
        if not t['symbol'].startswith('PNL_TEST')
    ]
    # Reset counters to only count real trades
    real_trades = [t for t in paper_trader.trades if t.get('pnl') is not None]
    paper_trader.wins   = sum(1 for t in real_trades if t['pnl'] and t['pnl'] > 0)
    paper_trader.losses = sum(1 for t in real_trades if t['pnl'] and t['pnl'] <= 0)
    print("  Test artifacts cleaned from paper_trader")

if __name__ == "__main__":
    success = asyncio.run(main())
    # Clean up test artifacts so they don't appear in dashboard
    asyncio.run(cleanup_test_artifacts())
    sys.exit(0 if success else 1)'''

if old_end in t:
    t = t.replace(old_end, new_end)
    open('test_alphabot_v2.py', 'w').write(t)
    print("FIXED 5: Test isolation cleanup added to test suite")
else:
    print("SKIP 5: end pattern not found in test file")

try:
    ast.parse(open('test_alphabot_v2.py').read())
    print("test_alphabot_v2.py syntax OK")
except SyntaxError as e:
    print(f"SYNTAX ERROR test: {e}")

print("\nAll Phase 0 fixes applied.")
print("Next: copy fix_p0_1_persistence.py output then restart bot")
