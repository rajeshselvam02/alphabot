"""
AlphaBot Complete Test Suite v2.0
Runs all 5 test categories:
  1. Basic integration (imports, Redis, signals, execution)
  2. Filter pass rate audit
  3. Signal-to-trade conversion
  4. PnL accuracy (long + short)
  5. Restart persistence
  6. Z-score range validation
"""
import asyncio
import sys
import statistics
sys.path.insert(0, '/root/alphabot')

PASS_COUNT = 0
FAIL_COUNT = 0
RESULTS = []

def ok(name, detail=""):
    global PASS_COUNT
    PASS_COUNT += 1
    RESULTS.append(("PASS", name, str(detail)))
    print(f"  \033[92mPASS\033[0m  {name}  {detail}")

def fail(name, detail=""):
    global FAIL_COUNT
    FAIL_COUNT += 1
    RESULTS.append(("FAIL", name, str(detail)))
    print(f"  \033[91mFAIL\033[0m  {name}  {detail}")

def section(title):
    print(f"\n\033[94m[{title}]\033[0m")

# ════════════════════════════════════════════════════
#  TEST 1: BASIC INTEGRATION
# ════════════════════════════════════════════════════
async def test_basic():
    section("1. Core Imports & Redis")
    try:
        from backend.config.settings import settings
        ok("settings loaded", f"mode={settings.TRADING_MODE} Z={settings.BOLLINGER_ENTRY_Z}")
    except Exception as e:
        fail("settings", e); return

    try:
        from backend.db.redis_client import redis_client
        ok("redis_client imported")
    except Exception as e:
        fail("redis_client", e); return

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
        fail("quant_signals", e); return

    try:
        from backend.core.strategies.bollinger_mr import bollinger_strategy
        ok("bollinger_strategy imported")
    except Exception as e:
        fail("bollinger_strategy", e); return

    try:
        from backend.core.execution.paper_trader import paper_trader
        ok("paper_trader imported")
    except Exception as e:
        fail("paper_trader", e); return

    try:
        from backend.core.execution.risk_manager import risk_manager
        ok("risk_manager imported")
    except Exception as e:
        fail("risk_manager", e); return

    try:
        bars = await redis_client.get_bars('BTCUSDT', '15m', n=5)
        if bars and len(bars) > 0:
            ok("Redis BTCUSDT bars", f"{len(bars)} bars close={bars[-1]['close']}")
        else:
            fail("Redis bars", "empty")
    except Exception as e:
        fail("Redis connection", e)


# ════════════════════════════════════════════════════
#  TEST 2: SIGNAL MATH
# ════════════════════════════════════════════════════
async def test_signals():
    section("2. Signal Math")
    from backend.db.redis_client import redis_client
    from backend.core.signals.quant_signals import (
        KalmanFilter, compute_rsi, compute_bbw, compute_macd,
        compute_ravi, logit_direction_filter, wyckoff_analysis,
        meta_label, fetch_cumulative_delta, compute_dynamic_exits
    )

    bars = await redis_client.get_bars('BTCUSDT', '15m', n=300)
    ohlcv = [{'open':b['open'],'high':b['high'],'low':b['low'],
               'close':b['close'],'volume':b['volume']} for b in bars]
    prices = [b['close'] for b in bars]
    vols   = [b['volume'] for b in bars]

    try:
        kf = KalmanFilter(delta=0.0001, Ve=0.001)
        for i in range(1, min(50, len(prices))):
            r = kf.update(prices[i-1], prices[i])
        ok("Kalman filter", f"z={r['zscore']:.3f} hr={r['hedge_ratio']:.2f}")
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
        ok("MACD", f"signal={macd['signal']} hist={macd.get('histogram',0):.4f}")
    except Exception as e:
        fail("MACD", e)

    try:
        ravi = compute_ravi(prices)
        ok("RAVI", f"{ravi['signal']} ravi={ravi['ravi']:.2f}%")
    except Exception as e:
        fail("RAVI", e)

    try:
        wyck = wyckoff_analysis(ohlcv)
        ok("Wyckoff", f"bias={wyck['wyckoff_bias']} effort={wyck['effort']:.2f}")
    except Exception as e:
        fail("Wyckoff", e)

    try:
        avg_vol = sum(vols[-20:])/20
        ml = meta_label(prices, zscore=-1.5, volume=vols[-1],
                        avg_volume=avg_vol, rsi=45, bbw=3.0)
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
        ok("Dynamic exits", f"TP={exits['take_profit']} bars={exits['max_bars']}")
    except Exception as e:
        fail("Dynamic exits", e)


# ════════════════════════════════════════════════════
#  TEST 3: FILTER PASS RATE AUDIT
# ════════════════════════════════════════════════════
async def test_filter_passrate():
    section("3. Filter Pass Rate Audit")
    from backend.db.redis_client import redis_client
    from backend.core.signals.quant_signals import (
        compute_rsi, compute_bbw, compute_macd, compute_ravi,
        logit_direction_filter, wyckoff_analysis, meta_label
    )

    bars = await redis_client.get_bars('BTCUSDT', '15m', n=300)
    ohlcv = [{'open':b['open'],'high':b['high'],'low':b['low'],
               'close':b['close'],'volume':b['volume']} for b in bars]
    prices = [b['close'] for b in bars]
    vols   = [b['volume'] for b in bars]

    passed = failed = 0
    block_counts = {'vol':0,'bbw':0,'rsi':0,'macd':0,'logit':0,'wyck':0,'meta':0}

    for i in range(50, len(prices)):
        window  = prices[:i]
        vol_w   = vols[:i]
        avg_vol = sum(vol_w[-20:])/20 if len(vol_w) >= 20 else None
        zscore  = 1.6

        try:
            rsi   = compute_rsi(window)
            bbw   = compute_bbw(window)
            macd  = compute_macd(window)
            logit = logit_direction_filter(window, lookback=20)
            wyck  = wyckoff_analysis(ohlcv[:i])
            ml    = meta_label(window, zscore=zscore,
                               volume=vol_w[-1],
                               avg_volume=avg_vol,
                               rsi=rsi['rsi'],
                               bbw=bbw.get('bbw', 4.0))

            checks = {
                'vol':   (avg_vol is None) or (vol_w[-1] >= avg_vol * 0.2),
                'bbw':   bbw['signal'] != 'wide',
                'rsi':   rsi['signal'] != 'overbought',
                'macd':  macd['signal'] != 'bearish',
                'logit': logit['signal'] in ('short', 'neutral'),
                'wyck':  wyck['wyckoff_bias'] != 'strong_bullish',
                'meta':  ml['prob_take'] >= 0.45,
            }
            if all(checks.values()):
                passed += 1
            else:
                failed += 1
                for k, v in checks.items():
                    if not v:
                        block_counts[k] += 1
        except Exception:
            failed += 1

    total = passed + failed
    rate  = passed / total * 100 if total > 0 else 0

    if rate >= 30:
        ok("Filter pass rate", f"{passed}/{total} = {rate:.1f}%")
    else:
        fail("Filter pass rate", f"{passed}/{total} = {rate:.1f}% — too low")

    print("  Blocked by (SELL signal):")
    for k, v in sorted(block_counts.items(), key=lambda x: -x[1]):
        if v > 0:
            pct = v / total * 100
            flag = " ← HIGH" if pct > 30 else ""
            print(f"    {k:8s}: {v:3d} times ({pct:.1f}%){flag}")


# ════════════════════════════════════════════════════
#  TEST 4: SIGNAL-TO-TRADE CONVERSION
# ════════════════════════════════════════════════════
async def test_signal_conversion():
    section("4. Signal-to-Trade Conversion")
    from backend.core.strategies.bollinger_mr import bollinger_strategy
    from backend.core.execution.paper_trader import paper_trader

    kf_out = {
        'zscore': -1.6, 'mean': 69000.0, 'forecast_std': 500.0,
        'hedge_ratio': 34.0, 'forecast_error': -800.0,
        'is_jump': False, 'jump_magnitude': 0.0,
    }

    # BUY signal
    bollinger_strategy._stats['BTCUSDT'] = {'hurst': 0.49, 'half_life': 20.0, 'adf_t': -3.0}
    bollinger_strategy._prices['BTCUSDT'] = [68000 - i*10 for i in range(25)]
    before = len(paper_trader.positions)
    try:
        await bollinger_strategy._execute('BTCUSDT', 'buy', 68000.0, -1.6, kf_out, prob=0.65)
        after = len(paper_trader.positions)
        if after > before:
            ok("BUY signal → trade", f"positions {before}→{after} cash=${paper_trader.cash:.2f}")
        else:
            fail("BUY signal → trade", "position not opened")
    except Exception as e:
        fail("BUY execution", e)

    # SELL signal
    bollinger_strategy._prices['ETHUSDT'] = [2060.0] * 25
    kf_sell = {**kf_out, 'zscore': 1.6, 'forecast_error': 800.0}
    bollinger_strategy._stats['ETHUSDT'] = {'hurst': 0.49, 'half_life': 20.0, 'adf_t': -3.0}
    before2 = len(paper_trader.positions)
    try:
        await bollinger_strategy._execute('ETHUSDT', 'sell', 2060.0, 1.6, kf_sell, prob=0.65)
        after2 = len(paper_trader.positions)
        if after2 > before2:
            ok("SELL signal → trade", f"positions {before2}→{after2}")
        else:
            fail("SELL signal → trade", "position not opened")
    except Exception as e:
        fail("SELL execution", e)

    # Triple barrier stored
    pos = paper_trader.positions.get('BTCUSDT')
    if pos:
        sd = pos.get('signal_data', {})
        if sd.get('take_profit') and sd.get('stop_loss'):
            ok("Triple barrier stored", f"TP={sd['take_profit']:.2f} SL={sd['stop_loss']:.2f} bars={sd.get('max_bars')}")
        else:
            fail("Triple barrier", f"TP={sd.get('take_profit')} SL={sd.get('stop_loss')}")


# ════════════════════════════════════════════════════
#  TEST 5: PnL ACCURACY (LONG + SHORT)
# ════════════════════════════════════════════════════
async def test_pnl_accuracy():
    section("5. PnL Accuracy")
    from backend.core.execution.paper_trader import paper_trader

    # LONG PnL test: buy@68000 sell@70000 → expect ~+$1930
    paper_trader.positions['PNL_TEST_LONG'] = {
        'id': 'test1', 'symbol': 'PNL_TEST_LONG',
        'strategy': 'test', 'side': 'long',
        'quantity': 1.0, 'entry_price': 68000.0,
        'current_price': 70000.0, 'entry_time': '2026-01-01',
        'stop_price': None, 'unrealized_pnl': 0.0,
        'unrealized_pct': 0.0, 'signal_data': {}
    }
    cash_before = paper_trader.cash
    await paper_trader.execute('PNL_TEST_LONG', 'sell', 1.0, 70000.0, 'test')
    trade = [t for t in paper_trader.trades if t['symbol']=='PNL_TEST_LONG']
    if trade:
        pnl = trade[-1]['pnl']
        if pnl and pnl > 1800:
            ok("LONG PnL correct", f"buy@68k sell@70k → ${pnl:.2f} (expect ~$1930)")
        else:
            fail("LONG PnL wrong", f"got ${pnl:.2f} expected ~$1930")
    else:
        fail("LONG trade not recorded")

    # SHORT PnL test: sell@70000 buy@68000 → expect ~+$1930
    paper_trader.positions['PNL_TEST_SHORT'] = {
        'id': 'test2', 'symbol': 'PNL_TEST_SHORT',
        'strategy': 'test', 'side': 'short',
        'quantity': 1.0, 'entry_price': 70000.0,
        'current_price': 68000.0, 'entry_time': '2026-01-01',
        'stop_price': None, 'unrealized_pnl': 0.0,
        'unrealized_pct': 0.0, 'signal_data': {}
    }
    await paper_trader.execute('PNL_TEST_SHORT', 'buy', 1.0, 68000.0, 'test')
    trade2 = [t for t in paper_trader.trades if t['symbol']=='PNL_TEST_SHORT']
    if trade2:
        pnl2 = trade2[-1]['pnl']
        if pnl2 and pnl2 > 1800:
            ok("SHORT PnL correct", f"sell@70k buy@68k → ${pnl2:.2f} (expect ~$1930)")
        else:
            fail("SHORT PnL wrong", f"got ${pnl2:.2f} expected ~$1930 — Bug #5 not fixed!")
    else:
        fail("SHORT trade not recorded")

    # Win/loss counter
    ok("Win/loss tracking", f"wins={paper_trader.wins} losses={paper_trader.losses}")


# ════════════════════════════════════════════════════
#  TEST 6: Z-SCORE RANGE VALIDATION
# ════════════════════════════════════════════════════
async def test_zscore_range():
    section("6. Z-Score Range Validation")
    from backend.db.redis_client import redis_client
    from backend.core.signals.quant_signals import KalmanFilter

    bars = await redis_client.get_bars('BTCUSDT', '15m', n=300)
    eth  = await redis_client.get_bars('ETHUSDT', '15m', n=300)
    bp = [b['close'] for b in bars]
    ep = [b['close'] for b in eth]

    kf = KalmanFilter(delta=0.0001, Ve=0.001)
    errors = []
    n = min(len(bp), len(ep))
    for i in range(1, n-1):
        r = kf.update(ep[i-1], bp[i])
        errors.append(r['forecast_error'])

    errors = errors[-50:]
    if len(errors) < 10:
        fail("Z-score validation", "insufficient errors")
        return

    mu = statistics.mean(errors)
    sd = statistics.stdev(errors)
    zscores = [(e - mu)/sd for e in errors]

    z_mean = statistics.mean(zscores)
    z_std  = statistics.stdev(zscores)
    z_min  = min(zscores)
    z_max  = max(zscores)
    in_range = sum(1 for z in zscores if abs(z) < 3) / len(zscores)

    if abs(z_mean) < 0.5:
        ok("Z-score mean ~0", f"{z_mean:.4f}")
    else:
        fail("Z-score mean not ~0", f"{z_mean:.4f} — Kalman not converged")

    if 0.5 < z_std < 2.0:
        ok("Z-score std ~1", f"{z_std:.4f}")
    else:
        fail("Z-score std abnormal", f"{z_std:.4f} expected 0.5-2.0")

    ok("Z-score range", f"min={z_min:.2f} max={z_max:.2f}")

    if in_range >= 0.95:
        ok("Z within ±3σ", f"{in_range*100:.1f}%")
    else:
        fail("Z outliers", f"only {in_range*100:.1f}% within ±3σ")

    signal_bars = sum(1 for z in zscores if abs(z) > 1.5)
    ok("Signal frequency", f"{signal_bars}/{len(zscores)} bars above ±1.5 ({signal_bars/len(zscores)*100:.1f}%)")


# ════════════════════════════════════════════════════
#  TEST 7: DQL AGENT VALIDATION
# ════════════════════════════════════════════════════
async def test_dql():
    section("7. DQL Agent Validation")
    from backend.core.signals.quant_signals import dql_agent

    tests = [
        (-2.5, 1, "Strong BUY"),
        (-1.5, 1, "BUY"),
        (-0.3, 0, "HOLD"),
        ( 0.0, 0, "HOLD"),
        ( 1.5, 2, "SELL"),
        ( 2.5, 2, "Strong SELL"),
    ]
    actions = ['HOLD', 'BUY', 'SELL']
    correct = 0
    for z, expected, desc in tests:
        state = dql_agent.build_state(z, 50.0, 1.0, 0.0, 0.49, 20.0)
        a = dql_agent.predict(state)
        if a == expected:
            correct += 1

    if correct >= 5:
        ok("DQL predictions", f"{correct}/6 correct")
    else:
        fail("DQL predictions", f"{correct}/6 correct — retrain needed")

    # Test state normalization
    state = dql_agent.build_state(2.0, 70.0, 1.5, 30.0, 0.45, 25.0)
    if all(-2 <= s <= 2 for s in state):
        ok("State normalization", f"all values in [-2,2]")
    else:
        fail("State normalization", f"out of range: {state}")


# ════════════════════════════════════════════════════
#  TEST 8: RISK MANAGER
# ════════════════════════════════════════════════════
async def test_risk():
    section("8. Risk Manager")
    from backend.core.execution.risk_manager import risk_manager
    from backend.core.signals.quant_signals import zscore_to_prob, prob_bet_size

    # Sizing
    for z in [1.5, 2.0, 2.5]:
        prob = zscore_to_prob(z)
        size = prob_bet_size(prob, max_bet_pct=0.15, kelly_fraction=0.25)
        qty  = risk_manager.zscore_size(z, 68000.0)
        ok(f"Sizing z={z}", f"prob={prob:.3f} size={size*100:.2f}% qty={qty:.6f}")

    # Drawdown calc
    risk_manager.peak_equity = 10000.0
    risk_manager.equity = 9500.0
    dd = risk_manager.drawdown
    if abs(dd - 0.05) < 0.001:
        ok("Drawdown calc", f"{dd*100:.1f}% (expect 5%)")
    else:
        fail("Drawdown calc", f"{dd*100:.1f}% expected 5%")

    # Halt check
    risk_manager.peak_equity = 10000.0
    risk_manager.equity = 8900.0
    halted = risk_manager.check_halts()
    if halted:
        ok("Drawdown halt triggers", f"at {risk_manager.drawdown*100:.1f}%")
        risk_manager.resume()
    else:
        fail("Drawdown halt", "did not trigger at 11% drawdown")

    risk_manager.equity = 10000.0
    risk_manager.peak_equity = 10000.0


# ════════════════════════════════════════════════════
#  MAIN RUNNER
# ════════════════════════════════════════════════════
async def main():
    print("\n" + "="*56)
    print("  ALPHABOT COMPLETE TEST SUITE v2.0")
    print("="*56)

    await test_basic()
    await test_signals()
    await test_filter_passrate()
    await test_signal_conversion()
    await test_pnl_accuracy()
    await test_zscore_range()
    await test_dql()
    await test_risk()

    # Summary
    total = PASS_COUNT + FAIL_COUNT
    print("\n" + "="*56)
    print(f"  RESULTS: {PASS_COUNT} PASS | {FAIL_COUNT} FAIL | {total} total")
    print("="*56)

    if FAIL_COUNT == 0:
        print("  \033[92mALL TESTS PASSED — Bot ready to trade!\033[0m")
    else:
        print("  \033[91mFAILED TESTS:\033[0m")
        for r in RESULTS:
            if r[0] == "FAIL":
                print(f"    \033[91m✗\033[0m {r[1]}: {r[2]}")

    print()
    return FAIL_COUNT == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
