import ast

# ── Bug #4: Volume threshold 0.8 → 0.2 ─────────────────────────
t = open('backend/core/strategies/bollinger_mr.py').read()
if 'volume < 0.8 * avg_vol' in t:
    t = t.replace('volume < 0.8 * avg_vol', 'volume < 0.2 * avg_vol')
    print('Bug #4 FIXED: volume threshold 0.8 → 0.2')
elif 'volume < 0.2 * avg_vol' in t:
    print('Bug #4 already fixed')
else:
    print('Bug #4 pattern not found')

# ── Bug #6: Restore RSI/MACD in _signal() ───────────────────────
old_buy  = '            if zscore < -ez and logit_signal in ("long", "neutral"):'
new_buy  = '            if zscore < -ez and logit_signal in ("long", "neutral") and rsi_signal != "oversold" and macd_signal != "bullish":'
old_sell = '            if zscore > ez and logit_signal in ("short", "neutral"):'
new_sell = '            if zscore > ez and logit_signal in ("short", "neutral") and rsi_signal != "overbought" and macd_signal != "bearish":'

if old_buy in t:
    t = t.replace(old_buy, new_buy)
    print('Bug #6 BUY FIXED: RSI+MACD restored')
elif 'rsi_signal != "oversold"' in t:
    print('Bug #6 BUY already fixed')
else:
    print('Bug #6 BUY pattern not found')

if old_sell in t:
    t = t.replace(old_sell, new_sell)
    print('Bug #6 SELL FIXED: RSI+MACD restored')
elif 'rsi_signal != "overbought"' in t:
    print('Bug #6 SELL already fixed')
else:
    print('Bug #6 SELL pattern not found')

# ── Bug #9: avg_vol None guard ───────────────────────────────────
if 'avg_vol if avg_vol > 0 else 1' in t:
    t = t.replace('avg_vol if avg_vol > 0 else 1', 'avg_vol if avg_vol and avg_vol > 0 else 1')
    print('Bug #9 FIXED: avg_vol None guard added')
elif 'avg_vol if avg_vol and avg_vol > 0 else 1' in t:
    print('Bug #9 already fixed')
else:
    print('Bug #9 pattern not found')

open('backend/core/strategies/bollinger_mr.py', 'w').write(t)

try:
    ast.parse(t)
    print('bollinger_mr.py syntax OK')
except SyntaxError as e:
    print(f'SYNTAX ERROR: {e}')

# ── Bug #5: Short PnL formula ────────────────────────────────────
t = open('backend/core/execution/paper_trader.py').read()
old_pnl = '                gross_pnl = (fill_price - pos["entry_price"]) * close_qty'
new_pnl = '''                if pos["side"] == "long":
                    gross_pnl = (fill_price - pos["entry_price"]) * close_qty
                else:  # short position
                    gross_pnl = (pos["entry_price"] - fill_price) * close_qty'''
if old_pnl in t:
    t = t.replace(old_pnl, new_pnl)
    open('backend/core/execution/paper_trader.py', 'w').write(t)
    print('Bug #5 FIXED: short PnL formula corrected')
elif 'pos["side"] == "long"' in t:
    print('Bug #5 already fixed')
else:
    print('Bug #5 pattern not found')

try:
    ast.parse(open('backend/core/execution/paper_trader.py').read())
    print('paper_trader.py syntax OK')
except SyntaxError as e:
    print(f'SYNTAX ERROR: {e}')

# ── Bug #7: fetch_historical hardcoded '1h' ──────────────────────
t = open('backend/core/data_feeds/binance_feed.py').read()
old_iv = '        params = {"currency_pair": pair, "interval": "1h", "limit": min(limit, 1000)}'
new_iv = '        gate_iv = INTERVAL_MAP.get(interval, interval)\n        params = {"currency_pair": pair, "interval": gate_iv, "limit": min(limit, 1000)}'
if old_iv in t:
    t = t.replace(old_iv, new_iv)
    open('backend/core/data_feeds/binance_feed.py', 'w').write(t)
    print('Bug #7 FIXED: fetch_historical interval hardcode removed')
elif 'gate_iv' in t:
    print('Bug #7 already fixed')
else:
    print('Bug #7 pattern not found')

try:
    ast.parse(open('backend/core/data_feeds/binance_feed.py').read())
    print('binance_feed.py syntax OK')
except SyntaxError as e:
    print(f'SYNTAX ERROR: {e}')

# ── Bug #8: total_bars double count in engine.py ─────────────────
t = open('backend/core/engine.py').read()
old_tb = '            bollinger_strategy.total_bars += count'
new_tb = '            # total_bars already incremented during warmup replay'
if old_tb in t:
    t = t.replace(old_tb, new_tb)
    open('backend/core/engine.py', 'w').write(t)
    print('Bug #8 FIXED: total_bars double count removed')
elif '# total_bars already incremented' in t:
    print('Bug #8 already fixed')
else:
    print('Bug #8 pattern not found')

try:
    ast.parse(open('backend/core/engine.py').read())
    print('engine.py syntax OK')
except SyntaxError as e:
    print(f'SYNTAX ERROR: {e}')

print('\nAll patches applied.')
