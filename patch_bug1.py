t = open('backend/core/strategies/bollinger_mr.py').read()

old = """        try:
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                redis_client.set(f"bars_count:{symbol}", self._bars[symbol])
            )
        except Exception:
            pass"""

new = """        asyncio.create_task(
            redis_client.set(f"bars_count:{symbol}", self._bars[symbol])
        )"""

if old in t:
    t = t.replace(old, new)
    open('backend/core/strategies/bollinger_mr.py', 'w').write(t)
    print('FIXED')
else:
    print('Pattern not found — showing context:')
    idx = t.find('get_event_loop')
    print(repr(t[idx-100:idx+200]))
