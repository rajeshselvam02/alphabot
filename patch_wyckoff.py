import sys
sys.path.insert(0, '/root/alphabot')

t = open('backend/core/strategies/bollinger_mr.py').read()

# Add import
t = t.replace(
    'from backend.core.signals.quant_signals import (\n    compute_dynamic_exits,\n    meta_label,',
    'from backend.core.signals.quant_signals import (\n    compute_dynamic_exits,\n    meta_label,\n    wyckoff_analysis,'
)

# Add _bars_ohlcv dict to __init__
t = t.replace(
    'self._kf_warmed: dict = {}',
    'self._kf_warmed: dict = {}\n        self._bars_ohlcv: dict = {}'
)

# Store full OHLCV bars in on_bar
t = t.replace(
    '        # Always accumulate prices and volumes (even during warmup)',
    '''        # Store full OHLCV bars for Wyckoff analysis
        if symbol not in self._bars_ohlcv:
            self._bars_ohlcv[symbol] = []
        self._bars_ohlcv[symbol].append({
            'open': bar.get('open', close),
            'high': bar.get('high', close),
            'low':  bar.get('low', close),
            'close': close,
            'volume': volume,
        })
        if len(self._bars_ohlcv[symbol]) > 500:
            self._bars_ohlcv[symbol].pop(0)

        # Always accumulate prices and volumes (even during warmup)'''
)

# Inject Wyckoff filter before RSI filter
t = t.replace(
    '        # ── RSI Filter (Murphy p.239)',
    '''        # ── Wyckoff Filter (Wyckoff Method) ────────────────
        wyckoff_out = wyckoff_analysis(self._bars_ohlcv.get(symbol, []))
        wyckoff_bias = wyckoff_out['wyckoff_bias']

        # ── RSI Filter (Murphy p.239)'''
)

# Pass wyckoff to _signal
t = t.replace(
    'signal = self._signal(symbol, zscore, vol_filter_ok=vol_filter_ok, logit_signal=logit_signal, rsi_signal=rsi_signal, bbw_signal=bbw_signal, macd_signal=macd_signal)',
    'signal = self._signal(symbol, zscore, vol_filter_ok=vol_filter_ok, logit_signal=logit_signal, rsi_signal=rsi_signal, bbw_signal=bbw_signal, macd_signal=macd_signal, wyckoff_bias=wyckoff_bias)'
)

# Update _signal signature
t = t.replace(
    'def _signal(self, symbol: str, zscore: float, vol_filter_ok: bool = True, logit_signal: str = "neutral", rsi_signal: str = "neutral", bbw_signal: str = "neutral", macd_signal: str = "neutral") -> Optional[str]:',
    'def _signal(self, symbol: str, zscore: float, vol_filter_ok: bool = True, logit_signal: str = "neutral", rsi_signal: str = "neutral", bbw_signal: str = "neutral", macd_signal: str = "neutral", wyckoff_bias: str = "neutral") -> Optional[str]:'
)

# Add Wyckoff gate in _signal
t = t.replace(
    '            if not vol_filter_ok or bbw_signal == "wide":\n                return None',
    '''            if not vol_filter_ok or bbw_signal == "wide":
                return None
            if wyckoff_bias == "strong_bearish" and zscore < -ez:
                return None  # Wyckoff says bearish, skip buy
            if wyckoff_bias == "strong_bullish" and zscore > ez:
                return None  # Wyckoff says bullish, skip sell'''
)

# Add wyckoff to published signal
t = t.replace(
    '"macd":      macd_out.get("histogram"),\n            "macd_signal": macd_signal,',
    '"macd":      macd_out.get("histogram"),\n            "macd_signal": macd_signal,\n            "wyckoff":    wyckoff_bias,'
)

open('backend/core/strategies/bollinger_mr.py', 'w').write(t)
print('done')
