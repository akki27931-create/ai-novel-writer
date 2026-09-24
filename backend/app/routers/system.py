"""路由：健康检查与后台任务状态。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import __version__
from ..config import env
from ..database import get_db
from ..schemas import JobOut
from ..services.jobs import jobs

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/system/info")
def system_info(db: Session = Depends(get_db)) -> dict[str, Any]:
    """运行时诊断信息：当前用的是哪个向量后端 / 向量模型。"""
    info: dict[str, Any] = {"version": __version__, "data_dir": str(env.data_path)}
    try:
        from ..services.indexing import get_store

        embedder, store = get_store(db)
        info.update(
            {
                "vector_backend": store.backend_name,
                "embedding_mode": embedder.mode,
                "embedding_model": embedder.model_name,
                "embedding_dimension": embedder.dimension,
            }
        )
    except Exception as exc:  # noqa: BLE001
        info["error"] = str(exc)
    return info


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(limit: int = 20) -> list[JobOut]:
    return [JobOut(**job.to_dict()) for job in jobs.list(limit=max(1, min(limit, 100)))]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str) -> JobOut:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return JobOut(**job.to_dict())


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, bool]:
    return {"cancelled": jobs.cancel(job_id)}
