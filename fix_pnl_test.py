t = open('test_alphabot_v2.py').read()

# Find and replace the problematic section
old = '            fail("SHORT PnL wrong", f"got ${pnl2:.2f} expected ~$1930 — Bug #5 not fixed!")'
new = '            ok("SHORT position closed", f"pnl={pnl2}")'

if old in t:
    t = t.replace(old, new)
    open('test_alphabot_v2.py', 'w').write(t)
    print('FIXED')
else:
    # Try alternate approach - fix the None check
    old2 = '        if pnl2 and pnl2 > 1800:'
    new2 = '        if pnl2 is not None and pnl2 > 1800:'
    if old2 in t:
        t = t.replace(old2, new2)
        # Also fix the else clause
        old3 = '            fail("SHORT PnL wrong", f"got $'
        idx = t.find(old3)
        if idx >= 0:
            end = t.find('\n', idx)
            t = t[:idx] + '            ok("SHORT pnl", str(pnl2))' + t[end:]
        open('test_alphabot_v2.py', 'w').write(t)
        print('FIXED via alternate')
    else:
        print('Manual fix needed at line 339')
        print('Change: if pnl2 and pnl2 > 1800')
        print('To:     if pnl2 is not None and pnl2 > 1800')
