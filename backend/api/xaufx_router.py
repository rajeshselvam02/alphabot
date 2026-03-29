from fastapi import APIRouter, HTTPException
from backend.core.xaufx.engine import XAUFXEngine
from backend.core.analytics.validation_registry import validation_registry

router = APIRouter(prefix="/api/xaufx")

engine = XAUFXEngine()


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/run")
def run():
    return engine.run_once()


@router.get("/validation/latest")
async def latest_validation_summary(status: str | None = None):
    validation = await validation_registry.latest_validation(status=status)
    if not validation:
        raise HTTPException(status_code=404, detail="No XAU/FX validation artifacts registered")
    return {"validation": validation}
