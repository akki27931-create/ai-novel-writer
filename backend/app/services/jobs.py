"""轻量后台任务管理器。

本地单机场景下不引入 Celery/Redis，直接在 FastAPI 的事件循环里跑 asyncio 任务，
前端通过 /api/jobs/{id} 轮询进度。拆书这种耗时任务因此不会阻塞请求。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

ProgressFn = Callable[[float, str, int, int], None]


@dataclass
class Job:
    id: str
    kind: str
    status: str = "pending"          # pending | running | success | error | cancelled
    progress: float = 0.0
    message: str = ""
    total: int = 0
    done: int = 0
    result: Any = None
    error: str = ""
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    _task: asyncio.Task | None = None
    _cancelled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "progress": round(self.progress, 4),
            "message": self.message,
            "total": self.total,
            "done": self.done,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
        }


class JobManager:
    """进程内任务表，最多保留 100 条。"""

    def __init__(self, max_jobs: int = 100) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._max_jobs = max_jobs

    def create(self, kind: str, runner: Callable[[Job, ProgressFn], Awaitable[Any]]) -> Job:
        job = Job(id=uuid.uuid4().hex, kind=kind)
        self._jobs[job.id] = job
        self._order.append(job.id)
        self._trim()
        job._task = asyncio.create_task(self._run(job, runner))
        return job

    async def _run(self, job: Job, runner: Callable[[Job, ProgressFn], Awaitable[Any]]) -> None:
        def report(progress: float, message: str = "", done: int = 0, total: int = 0) -> None:
            job.progress = max(0.0, min(1.0, progress))
            if message:
                job.message = message
            if total:
                job.total = total
            if done:
                job.done = done

        job.status = "running"
        try:
            job.result = await runner(job, report)
            job.status = "success"
            job.progress = 1.0
            if not job.message:
                job.message = "完成"
        except asyncio.CancelledError:  # noqa: PERF203
            job.status = "cancelled"
            job.message = "已取消"
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("后台任务 %s 失败", job.kind)
            job.status = "error"
            job.error = str(exc)
            job.message = f"失败：{exc}"

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self, limit: int = 20) -> list[Job]:
        return [self._jobs[i] for i in reversed(self._order[-limit:]) if i in self._jobs]

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job and job._task and not job._task.done():
            job._task.cancel()
            return True
        return False

    def is_cancelled(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        return bool(job and (job._cancelled or job.status == "cancelled"))

    def _trim(self) -> None:
        while len(self._order) > self._max_jobs:
            old = self._order.pop(0)
            job = self._jobs.pop(old, None)
            if job and job._task and not job._task.done():
                job._task.cancel()


jobs = JobManager()
