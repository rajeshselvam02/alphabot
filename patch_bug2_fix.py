t = open('backend/core/strategies/bollinger_mr.py').read()

# Remove the double-nested mess and replace with clean version
old = """            # Bar count seeded by engine.py after warmup — don't overwrite
            if not (symbol in self._bars and self._bars[symbol] > 20):
                if not (symbol in self._bars and self._bars[symbol] > 20):
                self._bars[symbol] = 0  # only reset if not already seeded by engine
            self._last_z[symbol]   = 0.0"""

new = """            # Bar count seeded by engine.py after warmup — don't overwrite
            if not (symbol in self._bars and self._bars[symbol] > 20):
                self._bars[symbol] = 0
            self._last_z[symbol]   = 0.0"""

if old in t:
    t = t.replace(old, new)
    open('backend/core/strategies/bollinger_mr.py', 'w').write(t)
    print('FIXED')
else:
    print('Pattern not found — doing line-by-line fix')
    lines = t.split('\n')
    new_lines = []
    skip_next = False
    for i, line in enumerate(lines):
        if skip_next:
            skip_next = False
            continue
        # Fix the double nested if
        if 'if not (symbol in self._bars' in line and i+1 < len(lines) and 'if not (symbol in self._bars' in lines[i+1]:
            new_lines.append(line)  # keep first if
            skip_next = True        # skip duplicate
        else:
            new_lines.append(line)
    t = '\n'.join(new_lines)
    open('backend/core/strategies/bollinger_mr.py', 'w').write(t)
    print('Fixed via line-by-line')
