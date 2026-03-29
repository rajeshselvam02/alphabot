from fastapi import APIRouter

from backend.core.analytics.decision_logger import decision_logger
from backend.core.analytics.model_registry import model_registry
from backend.core.analytics.validation_registry import validation_registry

router = APIRouter(prefix="/api/learning", tags=["learning"])


@router.get("/decisions")
async def learning_decisions(limit: int = 50, include_smoke: bool = False):
    return {"decisions": await decision_logger.recent_decisions(limit, include_smoke=include_smoke)}


@router.get("/outcomes")
async def learning_outcomes(limit: int = 50, include_smoke: bool = False):
    return {"outcomes": await decision_logger.recent_outcomes(limit, include_smoke=include_smoke)}


@router.get("/decision-summary")
async def learning_decision_summary(include_smoke: bool = False):
    return {"summary": await decision_logger.decision_summary(include_smoke=include_smoke)}


@router.get("/outcome-summary")
async def learning_outcome_summary(include_smoke: bool = False):
    return {"summary": await decision_logger.outcome_summary(include_smoke=include_smoke)}


@router.get("/dataset")
async def learning_dataset(limit: int = 200, include_smoke: bool = False):
    return {"rows": await decision_logger.export_training_dataset(limit=limit, include_smoke=include_smoke)}


@router.get("/quality-summary")
async def learning_quality_summary(include_smoke: bool = False):
    return {"summary": await decision_logger.strategy_quality_summary(include_smoke=include_smoke)}


@router.get("/models")
async def learning_models(limit: int = 20, model_type: str | None = None):
    return {"models": await model_registry.recent_models(limit=limit, model_type=model_type)}


@router.get("/models/latest")
async def learning_latest_model(model_type: str, status: str | None = None):
    return {"model": await model_registry.latest_model(model_type=model_type, status=status)}


@router.get("/validations")
async def learning_validations(limit: int = 20, status: str | None = None):
    return {"validations": await validation_registry.recent_validations(limit=limit, status=status)}


@router.get("/validations/latest")
async def learning_latest_validation(status: str | None = None):
    return {"validation": await validation_registry.latest_validation(status=status)}
