"""token 用量与费用记录。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import UsageRecord


def record_usage(
    db: Session,
    *,
    book_id: int | None,
    task_type: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    cost_cny: float,
) -> UsageRecord:
    row = UsageRecord(
        book_id=book_id,
        task_type=task_type,
        model=model,
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        cost_usd=float(cost_usd),
        cost_cny=float(cost_cny),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def summarize_usage(db: Session, *, limit: int = 20) -> dict[str, Any]:
    """汇总用量：总量 + 按模型 / 任务 / 书籍分组 + 最近记录。"""
    totals = db.query(
        func.count(UsageRecord.id),
        func.coalesce(func.sum(UsageRecord.input_tokens), 0),
        func.coalesce(func.sum(UsageRecord.output_tokens), 0),
        func.coalesce(func.sum(UsageRecord.cost_usd), 0.0),
        func.coalesce(func.sum(UsageRecord.cost_cny), 0.0),
    ).one()

    def grouped(column) -> list[dict[str, Any]]:
        rows = (
            db.query(
                column,
                func.count(UsageRecord.id),
                func.coalesce(func.sum(UsageRecord.input_tokens), 0),
                func.coalesce(func.sum(UsageRecord.output_tokens), 0),
                func.coalesce(func.sum(UsageRecord.cost_usd), 0.0),
                func.coalesce(func.sum(UsageRecord.cost_cny), 0.0),
            )
            .group_by(column)
            .order_by(func.sum(UsageRecord.cost_usd).desc())
            .limit(50)
            .all()
        )
        return [
            {
                "key": row[0],
                "requests": int(row[1]),
                "input_tokens": int(row[2]),
                "output_tokens": int(row[3]),
                "cost_usd": round(float(row[4]), 6),
                "cost_cny": round(float(row[5]), 6),
            }
            for row in rows
        ]

    recent_rows = (
        db.query(UsageRecord).order_by(UsageRecord.id.desc()).limit(limit).all()
    )

    return {
        "total_requests": int(totals[0] or 0),
        "total_input_tokens": int(totals[1] or 0),
        "total_output_tokens": int(totals[2] or 0),
        "total_cost_usd": round(float(totals[3] or 0.0), 6),
        "total_cost_cny": round(float(totals[4] or 0.0), 6),
        "by_model": grouped(UsageRecord.model),
        "by_task": grouped(UsageRecord.task_type),
        "by_book": grouped(UsageRecord.book_id),
        "recent": [
            {
                "id": r.id,
                "book_id": r.book_id,
                "task_type": r.task_type,
                "model": r.model,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cost_usd": round(r.cost_usd, 6),
                "cost_cny": round(r.cost_cny, 6),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in recent_rows
        ],
    }
