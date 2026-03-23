t = open('backend/core/strategies/bollinger_mr.py').read()

# Find the _bars reset line inside _init()
old = """            self._bars[symbol] = 0
            self._last_z[symbol]   = 0.0"""

new = """            if not (symbol in self._bars and self._bars[symbol] > 20):
                self._bars[symbol] = 0  # only reset if not already seeded by engine
            self._last_z[symbol]   = 0.0"""

if old in t:
    t = t.replace(old, new)
    open('backend/core/strategies/bollinger_mr.py', 'w').write(t)
    print('FIXED')
else:
    print('Pattern not found — showing _init context:')
    idx = t.find('def _init')
    print(repr(t[idx:idx+600]))
