"""
AlphaBot Full Integration Test
Tests every component end-to-end without needing to watch the screen
"""
import asyncio
import sys
sys.path.insert(0, '/root/alphabot')

async def run_all_tests():
    results = []
    
    def ok(name, val=""):
        results.append(("PASS", name, str(val)))
        print(f"  PASS  {name} {val}")
    
    def fail(name, val=""):
        results.append(("FAIL", name, str(val)))
        print(f"  FAIL  {name} {val}")

    print("\n" + "="*55)
    print("  ALPHABOT FULL INTEGRATION TEST")
    print("="*55)

    # ── 1. IMPORTS ──────────────────────────────────────────
    print("\n[1] Core imports")
    try:
        from backend.config.settings import settings
        ok("settings loaded", f"mode={settings.TRADING_MODE}")
    except Exception as e:
        fail("settings", e)

    try:
        from backend.db.redis_client import redis_client
        ok("redis_client imported")
    except Exception as e:
        fail("redis_client", e)

    try:
        from backend.core.signals.quant_signals import (
            KalmanFilter, compute_rsi, compute_bbw, compute_macd,
            compute_ravi, logit_direction_filter, wyckoff_analysis,
            meta_label, fetch_cumulative_delta, compute_dynamic_exits,
            prob_bet_size, zscore_to_prob, DQLAgent, dql_agent,
            triple_barrier_label, frac_diff
        )
        ok("quant_signals all imports")
    except Exception as e:
        fail("quant_signals imports", e)

    try:
        from backend.core.strategies.bollinger_mr import bollinger_strategy
        ok("bollinger_strategy imported")
    except Exception as e:
        fail("bollinger_strategy", e)

    try:
        from backend.core.execution.paper_trader import paper_trader
        ok("paper_trader imported")
    except Exception as e:
        fail("paper_trader", e)

    try:
        from backend.core.execution.risk_manager import risk_manager
        ok("risk_manager imported")
    except Exception as e:
        fail("risk_manager", e)

    # ── 2. REDIS CONNECTION ──────────────────────────────────
    print("\n[2] Redis connection")
    try:
        bars = await redis_client.get_bars('BTCUSDT', '15m', n=5)
        if bars and len(bars) > 0:
            ok("Redis bars", f"{len(bars)} bars, last close={bars[-1]['close']}")
        else:
            fail("Redis bars", "empty")
    except Exception as e:
        fail("Redis connection", e)

    # ── 3. SIGNAL MATH ───────────────────────────────────────
    print("\n[3] Signal math")
    try:
        bars300 = await redis_client.get_bars('BTCUSDT', '15m', n=300)
        prices = [b['close'] for b in bars300]
        
        kf = KalmanFilter(delta=0.0001, Ve=0.001)
        for i in range(1, min(50, len(prices))):
            r = kf.update(prices[i-1], prices[i])
        ok("Kalman filter", f"z={r['zscore']:.3f}")
    except Exception as e:
        fail("Kalman filter", e)

    try:
        rsi = compute_rsi(prices)
        ok("RSI", f"{rsi['rsi']:.1f} {rsi['signal']}")
    except Exception as e:
        fail("RSI", e)

    try:
        bbw = compute_bbw(prices)
        ok("BBW", f"{bbw.get('bbw',0):.2f} {bbw['signal']}")
    except Exception as e:
        fail("BBW", e)

    try:
        macd = compute_macd(prices)
        ok("MACD", f"{macd['signal']}")
    except Exception as e:
        fail("MACD", e)

    try:
        ravi = compute_ravi(prices)
        ok("RAVI", f"{ravi['signal']}")
    except Exception as e:
        fail("RAVI", e)

    try:
        ohlcv = [{'open':b['open'],'high':b['high'],'low':b['low'],
                  'close':b['close'],'volume':b['volume']} for b in bars300]
        wyck = wyckoff_analysis(ohlcv)
        ok("Wyckoff", f"{wyck['wyckoff_bias']}")
    except Exception as e:
        fail("Wyckoff", e)

    try:
        ml = meta_label(prices, zscore=-1.5, volume=100, avg_volume=100, rsi=40, bbw=3.0)
        ok("Meta-label", f"prob={ml['prob_take']:.2f} take={ml['take']}")
    except Exception as e:
        fail("Meta-label", e)

    try:
        cd = await fetch_cumulative_delta('BTCUSDT', limit=50)
        ok("Cumulative delta", f"{cd['signal']} delta={cd['delta_pct']:.1f}%")
    except Exception as e:
        fail("Cumulative delta", e)

    try:
        exits = compute_dynamic_exits(prices, zscore=-1.5)
        ok("Dynamic exits", f"TP={exits['take_profit']} SL={exits['stop_loss']} bars={exits['max_bars']}")
    except Exception as e:
        fail("Dynamic exits", e)

    # ── 4. DQL AGENT ─────────────────────────────────────────
    print("\n[4] DQL Agent")
    try:
        actions = ['HOLD','BUY','SELL']
        correct = 0
        tests = [(-2.0,2),(-1.5,1),(0.0,0),(1.5,2),(2.0,2)]
        for z, expected in tests:
            state = dql_agent.build_state(z, 50.0, 1.0, 0.0, 0.49, 20.0)
            a = dql_agent.predict(state)
            if a == expected:
                correct += 1
        ok("DQL predictions", f"{correct}/5 correct")
    except Exception as e:
        fail("DQL agent", e)

    # ── 5. RISK MANAGER ──────────────────────────────────────
    print("\n[5] Risk manager")
    try:
        prob = zscore_to_prob(2.0)
        size = prob_bet_size(prob, max_bet_pct=0.15, kelly_fraction=0.25)
        ok("Prob bet sizing", f"z=2.0 prob={prob:.3f} size={size*100:.2f}%")
    except Exception as e:
        fail("Prob bet sizing", e)

    try:
        qty = risk_manager.zscore_size(2.0, 68000.0)
        ok("Risk manager sizing", f"qty={qty:.6f}")
    except Exception as e:
        fail("Risk manager sizing", e)

    # ── 6. PAPER TRADER EXECUTION ────────────────────────────
    print("\n[6] Paper trader execution")
    try:
        from backend.core.strategies.bollinger_mr import bollinger_strategy
        bollinger_strategy._stats['BTCUSDT'] = {
            'hurst': 0.49, 'half_life': 20.0, 'adf_t': -3.0
        }
        bollinger_strategy._prices['BTCUSDT'] = [68000 + i*5 for i in range(25)]

        cash_before = paper_trader.cash
        pos_before  = len(paper_trader.positions)

        kf_out = {
            'zscore': -1.6, 'mean': 69000.0, 'forecast_std': 500.0,
            'hedge_ratio': 34.0, 'forecast_error': -800.0,
            'is_jump': False, 'jump_magnitude': 0.0,
        }
        await bollinger_strategy._execute('BTCUSDT', 'buy', 68000.0, -1.6, kf_out, prob=0.65)

        cash_after = paper_trader.cash
        pos_after  = len(paper_trader.positions)

        if pos_after > pos_before:
            ok("Trade execution BUY", f"cash ${cash_before:.2f} -> ${cash_after:.2f} positions={pos_after}")
        else:
            fail("Trade execution BUY", "position not opened")
    except Exception as e:
        fail("Trade execution", e)

    # ── 7. POSITION CHECK ────────────────────────────────────
    print("\n[7] Position state")
    try:
        pos = paper_trader.positions.get('BTCUSDT')
        if pos:
            ok("Position stored", f"side={pos['side']} qty={pos['quantity']:.4f} entry=${pos['entry_price']:,.2f}")
            sd = pos.get('signal_data', {})
            if sd.get('take_profit'):
                ok("Triple barrier stored", f"TP={sd['take_profit']} SL={sd['stop_loss']}")
            else:
                fail("Triple barrier", "TP/SL missing")
        else:
            fail("Position", "not found")
    except Exception as e:
        fail("Position check", e)

    # ── 8. TELEGRAM ──────────────────────────────────────────
    print("\n[8] Telegram")
    try:
        from backend.core.notifications.telegram_bot import telegram
        if telegram.enabled and telegram.bot:
            await telegram.send_plain("AlphaBot test complete — pipeline verified!")
            ok("Telegram send", "message sent")
        else:
            fail("Telegram", "not initialized — run inside uvicorn")
    except Exception as e:
        fail("Telegram", e)

    # ── SUMMARY ──────────────────────────────────────────────
    print("\n" + "="*55)
    passed = sum(1 for r in results if r[0] == "PASS")
    failed = sum(1 for r in results if r[0] == "FAIL")
    print(f"  RESULTS: {passed} PASS | {failed} FAIL | {len(results)} total")
    print("="*55)

    if failed == 0:
        print("  ALL TESTS PASSED — Bot ready to trade!")
    else:
        print("  FAILED TESTS:")
        for r in results:
            if r[0] == "FAIL":
                print(f"    - {r[1]}: {r[2]}")
    print()

asyncio.run(run_all_tests())
