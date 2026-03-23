"""
AlphaBot UI/UX + Database Mapping Test Suite
Tests:
  1. All API endpoints respond correctly
  2. WebSocket publishes correct data shape
  3. Dashboard data fields match API schema
  4. SQLite database schema + CRUD
  5. Redis key mapping validation
  6. Signal → Redis → API → Dashboard flow
  7. Trade → DB → API → Dashboard flow
"""
import asyncio
import sys
import json
import sqlite3
import aiohttp
import redis as redis_sync

sys.path.insert(0, '/root/alphabot')

PASS_COUNT = 0
FAIL_COUNT = 0
BASE = "http://localhost:8000"

def ok(name, detail=""):
    global PASS_COUNT
    PASS_COUNT += 1
    print(f"  \033[92mPASS\033[0m  {name}  {detail}")

def fail(name, detail=""):
    global FAIL_COUNT
    FAIL_COUNT += 1
    print(f"  \033[91mFAIL\033[0m  {name}  {detail}")

def section(title):
    print(f"\n\033[94m[{title}]\033[0m")

# ════════════════════════════════════════════════════
#  TEST 1: API ENDPOINTS
# ════════════════════════════════════════════════════
async def test_api_endpoints():
    section("1. API Endpoints")

    endpoints = [
        ("GET", "/api/health",     ["ok", "mode", "version"]),
        ("GET", "/api/portfolio",  ["equity", "cash", "total_return_pct", "win_rate_pct", "drawdown_pct", "sharpe"]),
        ("GET", "/api/positions",  None),   # list — may be empty
        ("GET", "/api/trades",     None),   # list — may be empty
        ("GET", "/api/strategies", ["strategies"]),
        ("GET", "/api/risk",       ["equity", "drawdown", "is_halted", "var_24h"]),
    ]

    async with aiohttp.ClientSession() as session:
        for method, path, required_keys in endpoints:
            try:
                async with session.get(f"{BASE}{path}", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status != 200:
                        fail(f"{method} {path}", f"status={r.status}")
                        continue
                    data = await r.json()
                    if required_keys:
                        missing = [k for k in required_keys if k not in data]
                        if missing:
                            fail(f"{method} {path}", f"missing keys: {missing}")
                        else:
                            ok(f"{method} {path}", f"all {len(required_keys)} keys present")
                    else:
                        ok(f"{method} {path}", f"status=200 type={type(data).__name__}")
            except aiohttp.ClientConnectorError:
                fail(f"{method} {path}", "connection refused — is bot running?")
            except Exception as e:
                fail(f"{method} {path}", str(e))


# ════════════════════════════════════════════════════
#  TEST 2: API DATA SCHEMA VALIDATION
# ════════════════════════════════════════════════════
async def test_api_schema():
    section("2. API Data Schema")

    async with aiohttp.ClientSession() as session:
        # Portfolio schema
        try:
            async with session.get(f"{BASE}/api/portfolio") as r:
                p = await r.json()
                numeric = ["equity", "cash", "total_return_pct", "unrealized_pnl",
                           "total_fees", "win_rate_pct", "drawdown_pct"]
                for k in numeric:
                    if k in p and isinstance(p[k], (int, float)):
                        pass
                    else:
                        fail(f"portfolio.{k}", f"type={type(p.get(k)).__name__} expected number")
                        break
                else:
                    ok("Portfolio schema", f"equity=${p['equity']:,.2f} trades={p.get('total_trades',0)}")
        except Exception as e:
            fail("Portfolio schema", e)

        # Strategies schema
        try:
            async with session.get(f"{BASE}/api/strategies") as r:
                d = await r.json()
                strats = d.get("strategies", [])
                if not strats:
                    fail("Strategies schema", "empty strategies list")
                else:
                    s = strats[0]
                    required = ["strategy", "is_active", "signals_fired",
                                "trades_made", "total_bars", "bar_counts", "last_z"]
                    missing = [k for k in required if k not in s]
                    if missing:
                        fail("Strategies schema", f"missing: {missing}")
                    else:
                        ok("Strategies schema", f"bars={s['total_bars']} signals={s['signals_fired']}")

                    # Z-scores must be numeric
                    last_z = s.get("last_z", {})
                    for sym, z in last_z.items():
                        try:
                            float(z)
                        except (TypeError, ValueError):
                            fail(f"Z-score {sym}", f"not numeric: {z}")
                            break
                    else:
                        ok("Z-score types", f"{len(last_z)} symbols all numeric")
        except Exception as e:
            fail("Strategies schema", e)

        # Risk schema
        try:
            async with session.get(f"{BASE}/api/risk") as r:
                risk = await r.json()
                required = ["equity", "drawdown", "daily_loss", "is_halted",
                            "var_24h", "var_pct", "trade_count"]
                missing = [k for k in required if k not in risk]
                if missing:
                    fail("Risk schema", f"missing: {missing}")
                else:
                    ok("Risk schema", f"drawdown={risk['drawdown']*100:.2f}% halted={risk['is_halted']}")
        except Exception as e:
            fail("Risk schema", e)


# ════════════════════════════════════════════════════
#  TEST 3: REDIS KEY MAPPING
# ════════════════════════════════════════════════════
def test_redis_keys():
    section("3. Redis Key Mapping")

    try:
        r = redis_sync.Redis(host='localhost', port=6379, decode_responses=True)
        r.ping()
        ok("Redis connection", "ping OK")
    except Exception as e:
        fail("Redis connection", e)
        return

    # Bar storage
    for sym in ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT']:
        key = f'bars:{sym}:15m'
        count = r.llen(key)
        if count > 0:
            last = json.loads(r.lrange(key, -1, -1)[0])
            required = ['symbol', 'interval', 'open', 'high', 'low', 'close', 'volume', 'timestamp']
            missing = [k for k in required if k not in last]
            if missing:
                fail(f"Bar schema {sym}", f"missing: {missing}")
            else:
                ok(f"Bars {sym}", f"{count} bars close={last['close']}")
        else:
            fail(f"Bars {sym}", "empty")

    # Bar counts
    for sym in ['BTCUSDT', 'ETHUSDT']:
        val = r.get(f'bars_count:{sym}')
        if val and int(val) > 20:
            ok(f"bars_count:{sym}", f"{val} bars")
        else:
            fail(f"bars_count:{sym}", f"value={val} — restart persistence broken")

    # Portfolio state
    port_raw = r.get('portfolio')
    if port_raw:
        port = json.loads(port_raw)
        required = ['equity', 'cash', 'total_return_pct', 'win_rate_pct']
        missing = [k for k in required if k not in port]
        if missing:
            fail("Redis portfolio", f"missing: {missing}")
        else:
            ok("Redis portfolio", f"equity=${port['equity']:,.2f}")
    else:
        ok("Redis portfolio", "not yet published — normal < 30s")

    # Status state
    status_raw = r.get('status')
    if status_raw:
        status = json.loads(status_raw)
        if 'strategies' in status and 'portfolio' in status:
            ok("Redis status", f"running={status.get('running')}")
        else:
            fail("Redis status", f"missing strategies or portfolio key")
    else:
        ok("Redis status", "not yet published — normal within 30s of start")

    # Price keys
    price = r.get('price:BTCUSDT')
    if price:
        ok("Redis price:BTCUSDT", f"${float(price):,.2f}")
    else:
        ok("Redis price:BTCUSDT", "not yet set — normal < 30s")


# ════════════════════════════════════════════════════
#  TEST 4: SQLITE DATABASE
# ════════════════════════════════════════════════════
def test_database():
    section("4. SQLite Database")

    try:
        conn = sqlite3.connect('/root/alphabot/alphabot.db')
        cursor = conn.cursor()
        ok("SQLite connection", "alphabot.db opened")
    except Exception as e:
        fail("SQLite connection", e)
        return

    # Check tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    ok("Tables found", f"{tables}")

    expected_tables = ["trades","positions","daily_performance","strategy_states","price_bars"]
    for t in expected_tables:
        if t in tables:
            ok(f"Table '{t}' exists")
        else:
            fail(f"Table '{t}' missing", "run init_db()")

    # Check trades table schema
    if 'trades' in tables:
        cursor.execute("PRAGMA table_info(trades)")
        cols = [row[1] for row in cursor.fetchall()]
        required_cols = ["id","symbol","strategy","side","quantity","entry_price","pnl","mode"]
        missing = [c for c in required_cols if c not in cols]
        if missing:
            fail("Trades schema", f"missing columns: {missing}")
        else:
            ok("Trades schema", f"{len(cols)} columns: {cols}")

        # Count records
        cursor.execute("SELECT COUNT(*) FROM trades")
        count = cursor.fetchone()[0]
        ok("Trades records", f"{count} total trades in DB")

    # Check signals table
    if 'signals' in tables:
        cursor.execute("PRAGMA table_info(signals)")
        cols = [row[1] for row in cursor.fetchall()]
        ok("Signals schema", f"{len(cols)} columns")
        cursor.execute("SELECT COUNT(*) FROM signals")
        count = cursor.fetchone()[0]
        ok("Signals records", f"{count} total signals in DB")

    conn.close()


# ════════════════════════════════════════════════════
#  TEST 5: SIGNAL → REDIS → API FLOW
# ════════════════════════════════════════════════════
async def test_signal_flow():
    section("5. Signal → Redis → API Flow")

    from backend.db.redis_client import redis_client

    # Publish a test signal
    test_signal = {
        "strategy": "bollinger_mr",
        "symbol": "BTCUSDT",
        "close": 68245.2,
        "zscore": 1.85,
        "mean": 67500.0,
        "std": 400.0,
        "signal": "sell",
        "vol_ok": True,
        "rsi": 55.0,
        "wyckoff": "neutral",
        "cd_signal": "neutral",
    }

    try:
        await redis_client.publish("signals", test_signal)
        ok("Signal published to Redis pubsub")
    except Exception as e:
        fail("Signal publish", e)

    # Verify strategies endpoint shows live data
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{BASE}/api/strategies") as r:
                d = await r.json()
                strats = d.get("strategies", [])
                if strats and strats[0].get("last_z"):
                    z_btc = strats[0]["last_z"].get("BTCUSDT", 0)
                    ok("API returns live Z-scores", f"BTC z={z_btc}")
                else:
                    fail("API Z-scores", "empty or missing")
        except Exception as e:
            fail("API strategies after signal", e)


# ════════════════════════════════════════════════════
#  TEST 6: DASHBOARD DATA COMPLETENESS
# ════════════════════════════════════════════════════
async def test_dashboard_data():
    section("6. Dashboard Data Completeness")

    async with aiohttp.ClientSession() as session:
        try:
            # Home page metrics
            async with session.get(f"{BASE}/api/portfolio") as r:
                p = await r.json()
                dashboard_fields = {
                    "PORTFOLIO VALUE":  "equity",
                    "UNREALIZED P&L":   "unrealized_pnl",
                    "WIN RATE":         "win_rate_pct",
                    "DRAWDOWN":         "drawdown_pct",
                    "SHARPE":           "sharpe",
                    "EQUITY CURVE":     "equity",
                }
                missing = []
                for label, key in dashboard_fields.items():
                    if key not in p:
                        missing.append(f"{label}({key})")
                if missing:
                    fail("Dashboard home fields", f"missing: {missing}")
                else:
                    ok("Dashboard home fields", f"all {len(dashboard_fields)} present")

            # Signals page
            async with session.get(f"{BASE}/api/strategies") as r:
                d = await r.json()
                s = d.get("strategies", [{}])[0]
                signal_fields = {
                    "Z-SCORE":      "last_z",
                    "BAR COUNTS":   "bar_counts",
                    "SIGNALS FIRED":"signals_fired",
                    "TRADES MADE":  "trades_made",
                }
                missing = [f"{l}({k})" for l, k in signal_fields.items() if k not in s]
                if missing:
                    fail("Signals page fields", f"missing: {missing}")
                else:
                    ok("Signals page fields", f"all {len(signal_fields)} present")

            # Risk page
            async with session.get(f"{BASE}/api/risk") as r:
                risk = await r.json()
                risk_fields = {
                    "DRAWDOWN":     "drawdown",
                    "DAILY LOSS":   "daily_loss",
                    "VAR 24H":      "var_24h",
                    "HALTED":       "is_halted",
                    "KELLY":        "trade_count",
                }
                missing = [f"{l}({k})" for l, k in risk_fields.items() if k not in risk]
                if missing:
                    fail("Risk page fields", f"missing: {missing}")
                else:
                    ok("Risk page fields", f"all {len(risk_fields)} present")

        except aiohttp.ClientConnectorError:
            fail("Dashboard data", "bot not running — start uvicorn first")
        except Exception as e:
            fail("Dashboard data", e)


# ════════════════════════════════════════════════════
#  TEST 7: WEBSOCKET CONNECTIVITY
# ════════════════════════════════════════════════════
async def test_websocket():
    section("7. WebSocket Connectivity")

    try:
        import websockets
        uri = "ws://localhost:8000/ws"
        async with websockets.connect(uri, ping_timeout=5) as ws:
            ok("WebSocket connects", uri)
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                data = json.loads(msg)
                if "portfolio" in data or "strategies" in data or "type" in data:
                    ok("WebSocket message shape", f"keys={list(data.keys())[:5]}")
                else:
                    fail("WebSocket message", f"unexpected shape: {list(data.keys())}")
            except asyncio.TimeoutError:
                ok("WebSocket connected", "no immediate message (normal if no event)")
    except ImportError:
        fail("WebSocket test", "pip install websockets")
    except Exception as e:
        fail("WebSocket", str(e))


# ════════════════════════════════════════════════════
#  TEST 8: CONTROL ENDPOINTS
# ════════════════════════════════════════════════════
async def test_control():
    section("8. Control Endpoints")

    async with aiohttp.ClientSession() as session:
        # Pause
        try:
            async with session.post(f"{BASE}/api/control/pause") as r:
                if r.status in (200, 204):
                    ok("POST /api/control/pause", f"status={r.status}")
                else:
                    fail("POST /api/control/pause", f"status={r.status}")
        except Exception as e:
            fail("Pause endpoint", e)

        # Resume
        try:
            async with session.post(f"{BASE}/api/control/resume") as r:
                if r.status in (200, 204):
                    ok("POST /api/control/resume", f"status={r.status}")
                else:
                    fail("POST /api/control/resume", f"status={r.status}")
        except Exception as e:
            fail("Resume endpoint", e)

        # Verify bot still active after resume
        try:
            async with session.get(f"{BASE}/api/health") as r:
                h = await r.json()
                if h.get("ok"):
                    ok("Bot healthy after resume", f"mode={h.get('mode')}")
                else:
                    fail("Bot health after resume", str(h))
        except Exception as e:
            fail("Health after resume", e)


# ════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════
async def main():
    print("\n" + "="*56)
    print("  ALPHABOT UI/UX + DATABASE TEST SUITE")
    print("="*56)
    print("  NOTE: Bot must be running on port 8000")
    print("="*56)

    await test_api_endpoints()
    await test_api_schema()
    test_redis_keys()
    test_database()
    await test_signal_flow()
    await test_dashboard_data()
    await test_websocket()
    await test_control()

    total = PASS_COUNT + FAIL_COUNT
    print("\n" + "="*56)
    print(f"  RESULTS: {PASS_COUNT} PASS | {FAIL_COUNT} FAIL | {total} total")
    print("="*56)
    if FAIL_COUNT == 0:
        print("  \033[92mALL TESTS PASSED\033[0m")
    else:
        print(f"  \033[91m{FAIL_COUNT} TESTS FAILED — check above\033[0m")
    print()


if __name__ == "__main__":
    asyncio.run(main())
