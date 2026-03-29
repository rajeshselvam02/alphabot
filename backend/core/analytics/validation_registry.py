from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from backend.core.analytics.model_registry import model_registry

logger = logging.getLogger("alphabot.validation_registry")


class ValidationRegistryService:
    ARTIFACT_DIR = Path("reports/validation_artifacts/xaufx")
    LEGACY_MANIFEST_GLOB = "reports/xaufx*_train_test_manifest.json"

    async def recent_validations(self, limit: int = 20, status: Optional[str] = None) -> list[dict]:
        rows = await model_registry.recent_validations(limit=limit, status=status)
        if rows:
            return rows
        return self._recent_validation_artifacts(limit=limit, status=status)

    async def latest_validation(self, status: Optional[str] = None) -> Optional[dict]:
        row = await model_registry.latest_validation(status=status)
        if row:
            return row
        rows = self._recent_validation_artifacts(limit=1, status=status)
        return rows[0] if rows else None

    def register_validation_artifact(
        self,
        *,
        artifact_path: str,
        runner: str,
        config_hash: str,
        code_version: str,
        verdict: str | None,
        metrics: dict[str, Any] | None,
        config: dict[str, Any] | None,
        notes: str | None = None,
    ) -> str | None:
        status = verdict or "candidate"
        try:
            return model_registry.register_model_sync(
                model_type="xaufx_validation",
                model_name=runner,
                artifact_path=artifact_path,
                version=code_version,
                status=status,
                training_rows=None,
                training_start=None,
                training_end=None,
                metrics=metrics,
                config={
                    "config_hash": config_hash,
                    "runner_config": config,
                },
                notes=notes,
            )
        except Exception as exc:
            logger.warning(f"[VALIDATION REGISTRY] register failed: {exc}")
            return None

    def load_validation_artifact(self, artifact_path: str) -> dict[str, Any]:
        return json.loads(Path(artifact_path).read_text(encoding="utf-8"))

    async def backfill_model_registry(self) -> int:
        count = 0
        for path in self._candidate_artifact_paths():
            if "artifact_smoke__" in path.name:
                continue
            if await model_registry.artifact_exists(str(path)):
                continue
            row = self._row_from_artifact_file(path)
            if row is None:
                continue
            try:
                await model_registry.register_model(
                    model_type="xaufx_validation",
                    model_name=row["model_name"],
                    artifact_path=row["artifact_path"],
                    version=row["version"] or "legacy",
                    status=row["status"],
                    training_rows=None,
                    training_start=None,
                    training_end=None,
                    metrics=row["metrics"],
                    config=row["config"],
                    notes="Backfilled from historical XAU/FX validation artifact.",
                )
                count += 1
            except Exception as exc:
                logger.warning(f"[VALIDATION REGISTRY] backfill failed for {path}: {exc}")
        return count

    def _recent_validation_artifacts(self, limit: int = 20, status: Optional[str] = None) -> list[dict]:
        rows: list[dict] = []
        for path in self._candidate_artifact_paths():
            if "artifact_smoke__" in path.name:
                continue
            row = self._row_from_artifact_file(path, status=status)
            if row is None:
                continue
            rows.append(row)
            if len(rows) >= limit:
                break

        return rows

    def _candidate_artifact_paths(self) -> list[Path]:
        base_dir = Path.cwd() / self.ARTIFACT_DIR
        artifact_paths: list[Path] = []
        if base_dir.exists():
            artifact_paths.extend(base_dir.glob("*.json"))
        artifact_paths.extend(Path.cwd().glob(self.LEGACY_MANIFEST_GLOB))
        return sorted(artifact_paths, key=lambda p: p.stat().st_mtime, reverse=True)

    def _row_from_artifact_file(self, path: Path, status: Optional[str] = None) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

        summary = payload.get("summary") or payload.get("best_summary") or {}
        artifact_status = summary.get("verdict") or payload.get("status") or "candidate"
        if status and artifact_status != status:
            return None

        return {
            "id": payload.get("run_id") or path.stem,
            "created_at": payload.get("generated_at"),
            "model_type": "xaufx_validation",
            "model_name": payload.get("runner", "xaufx_validation"),
            "version": payload.get("code_version"),
            "status": artifact_status,
            "artifact_path": str(path),
            "training_rows": None,
            "training_start": None,
            "training_end": None,
            "promoted_at": None,
            "rolled_back_at": None,
            "metrics": summary,
            "config": {
                "config_hash": payload.get("config_hash") or payload.get("best_config_hash"),
                "runner_config": payload.get("best_config"),
            },
            "notes": "Loaded from validation artifact file fallback.",
        }


validation_registry = ValidationRegistryService()
