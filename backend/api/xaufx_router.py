
from fastapi import APIRouter
from backend.core.xaufx.engine import XAUFXEngine

router = APIRouter(prefix="/api/xaufx")

engine = XAUFXEngine()


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/run")
def run():
    return engine.run_once()
