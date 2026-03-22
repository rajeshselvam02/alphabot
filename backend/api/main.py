"""
FastAPI Backend — REST API + WebSocket push to dashboard
Termux-friendly: runs on port 8000, accessible via ngrok
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as aioredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from backend.config.settings import settings
from backend.core.engine import engine
from backend.db.redis_client import redis_client, get_redis
from backend.core.execution.paper_trader import paper_trader
from backend.core.execution.risk_manager import risk_manager
from backend.core.strategies.bollinger_mr import bollinger_strategy
from backend.core.strategies.cross_sectional import cs_strategy

# Setup logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(settings.LOG_FILE),
    ]
)
logger = logging.getLogger("alphabot.api")


# ── WebSocket manager ─────────────────────────────────────────────

class WSManager:
    def __init__(self):
        self.clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.append(ws)
        logger.info(f"WS client connected ({len(self.clients)} total)")

    def disconnect(self, ws: WebSocket):
        if ws in self.clients:
            self.clients.remove(ws)

    async def broadcast(self, msg: dict):
        dead = []
        for ws in self.clients:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


ws_manager = WSManager()


# ── Redis pub/sub relay to WebSocket clients ──────────────────────

async def redis_relay():
    """Subscribe to all Redis channels and relay to WebSocket clients."""
    r = await get_redis()
    pub = r.pubsub()
    await pub.subscribe("status", "trades", "signals", "prices")
    logger.info("Redis relay started")

    async for msg in pub.listen():
        if msg["type"] == "message":
            try:
                data = json.loads(msg["data"])
                data["_ch"] = msg["channel"]
                await ws_manager.broadcast(data)
            except Exception as e:
                logger.error(f"Relay error: {e}")


# ── App lifespan (startup / shutdown) ────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    os.makedirs("./logs", exist_ok=True)
    asyncio.create_task(engine.run(),    name="engine")
    asyncio.create_task(redis_relay(),   name="relay")
    yield
    await engine.shutdown()


app = FastAPI(
    title="AlphaBot API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend static files if present
if os.path.exists("./frontend/dist"):
    app.mount("/assets", StaticFiles(directory="./frontend/dist/assets"), name="assets")


# ── WebSocket endpoint ────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        # Send current state on connect
        status = await redis_client.get_status()
        if status:
            status["_ch"] = "status"
            await ws.send_json(status)

        prices = await redis_client.get_prices()
        if prices:
            await ws.send_json({"_ch": "prices", "prices": prices})

        while True:
            await ws.receive_text()   # keep-alive ping handling

    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


# ── REST endpoints ────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"ok": True, "mode": settings.TRADING_MODE, "version": "1.0.0"}


@app.get("/api/portfolio")
async def portfolio():
    return paper_trader.summary()


@app.get("/api/trades")
async def trades(limit: int = 50):
    t = paper_trader.trades[-limit:]
    return {"trades": list(reversed(t)), "total": len(paper_trader.trades)}


@app.get("/api/positions")
async def positions():
    return {"positions": list(paper_trader.positions.values())}


@app.get("/api/prices")
async def prices():
    return {"prices": await redis_client.get_prices()}


@app.get("/api/status")
async def status():
    s = await redis_client.get_status()
    return s or {"running": engine.running, "mode": settings.TRADING_MODE}


@app.get("/api/strategies")
async def get_strategies():
    return {
        "strategies": [
            bollinger_strategy.get_stats(),
            cs_strategy.get_stats(),
        ],
        "risk": risk_manager.to_dict(),
    }


@app.get("/api/bars/{symbol}")
async def get_bars(symbol: str, interval: str = "1h", n: int = 100):
    bars = await redis_client.get_bars(symbol.upper(), interval, n=n)
    return {"symbol": symbol.upper(), "bars": bars}


@app.get("/api/risk")
async def risk():
    return risk_manager.to_dict()


# ── Control endpoints ─────────────────────────────────────────────

@app.post("/api/control/pause")
async def pause():
    await engine.pause("Manual pause via API")
    return {"status": "paused"}


@app.post("/api/control/resume")
async def resume():
    await engine.resume()
    return {"status": "resumed"}


@app.post("/api/control/halt")
async def halt(reason: str = "Manual halt"):
    risk_manager._halt(reason)
    await engine.pause(reason)
    return {"status": "halted", "reason": reason}


@app.post("/api/strategy/{name}/toggle")
async def toggle_strategy(name: str):
    if name == "bollinger_mr":
        bollinger_strategy.is_active = not bollinger_strategy.is_active
        return {"strategy": name, "active": bollinger_strategy.is_active}
    elif name == "cross_sectional":
        cs_strategy.is_active = not cs_strategy.is_active
        return {"strategy": name, "active": cs_strategy.is_active}
    raise HTTPException(404, "Strategy not found")


# ── Serve frontend index.html for SPA routing ─────────────────────

@app.get("/{path:path}")
async def spa_fallback(path: str):
    index = "./frontend/dist/index.html"
    if os.path.exists(index):
        return FileResponse(index)
    return {"msg": "Dashboard not built yet. Run: cd frontend && npm run build"}
