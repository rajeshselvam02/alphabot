import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import desc, select

from backend.db.database import AsyncSessionLocal, ModelRegistry

logger = logging.getLogger("alphabot.model_registry")


def _dt(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class ModelRegistryService:
    async def register_model(
        self,
        *,
        model_type: str,
        model_name: str,
        artifact_path: str,
        version: Optional[str] = None,
        status: str = "candidate",
        training_rows: Optional[int] = None,
        training_start: Optional[str] = None,
        training_end: Optional[str] = None,
        metrics: Optional[dict] = None,
        config: Optional[dict] = None,
        notes: Optional[str] = None,
    ) -> str:
        model_id = str(uuid.uuid4())
        row = ModelRegistry(
            id=model_id,
            created_at=datetime.now(timezone.utc),
            model_type=model_type,
            model_name=model_name,
            version=version or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
            status=status,
            artifact_path=str(Path(artifact_path)),
            training_rows=training_rows,
            training_start=_dt(training_start),
            training_end=_dt(training_end),
            metrics=metrics,
            config=config,
            notes=notes,
        )
        try:
            async with AsyncSessionLocal() as session:
                session.add(row)
                await session.commit()
        except Exception as e:
            logger.warning(f"[MODEL REGISTRY] register failed: {e}")
            raise
        return model_id

    async def recent_models(self, limit: int = 20, model_type: Optional[str] = None) -> list[dict]:
        async with AsyncSessionLocal() as session:
            stmt = select(ModelRegistry).order_by(desc(ModelRegistry.created_at)).limit(limit)
            if model_type:
                stmt = stmt.where(ModelRegistry.model_type == model_type)
            rows = (await session.execute(stmt)).scalars().all()
        return [self._row_to_dict(row) for row in rows]

    async def latest_model(self, model_type: str, status: Optional[str] = None) -> Optional[dict]:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(ModelRegistry)
                .where(ModelRegistry.model_type == model_type)
                .order_by(desc(ModelRegistry.created_at))
                .limit(1)
            )
            if status:
                stmt = stmt.where(ModelRegistry.status == status)
            row = (await session.execute(stmt)).scalars().first()
        return self._row_to_dict(row) if row else None

    def register_model_sync(self, **kwargs) -> str:
        return asyncio.run(self.register_model(**kwargs))

    async def recent_validations(self, limit: int = 20, status: Optional[str] = None) -> list[dict]:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(ModelRegistry)
                .where(ModelRegistry.model_type == "xaufx_validation")
                .order_by(desc(ModelRegistry.created_at))
                .limit(limit)
            )
            if status:
                stmt = stmt.where(ModelRegistry.status == status)
            rows = (await session.execute(stmt)).scalars().all()
        return [self._row_to_dict(row) for row in rows]

    async def latest_validation(self, status: Optional[str] = None) -> Optional[dict]:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(ModelRegistry)
                .where(ModelRegistry.model_type == "xaufx_validation")
                .order_by(desc(ModelRegistry.created_at))
                .limit(1)
            )
            if status:
                stmt = stmt.where(ModelRegistry.status == status)
            row = (await session.execute(stmt)).scalars().first()
        return self._row_to_dict(row) if row else None

    def _row_to_dict(self, row: ModelRegistry) -> dict:
        return {
            "id": row.id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "model_type": row.model_type,
            "model_name": row.model_name,
            "version": row.version,
            "status": row.status,
            "artifact_path": row.artifact_path,
            "training_rows": row.training_rows,
            "training_start": row.training_start.isoformat() if row.training_start else None,
            "training_end": row.training_end.isoformat() if row.training_end else None,
            "promoted_at": row.promoted_at.isoformat() if row.promoted_at else None,
            "rolled_back_at": row.rolled_back_at.isoformat() if row.rolled_back_at else None,
            "metrics": row.metrics,
            "config": row.config,
            "notes": row.notes,
        }


model_registry = ModelRegistryService()
