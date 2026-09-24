"""ORM 模型定义。

数据分层：
- books / chapters        : 小说原文
- book_analyses          : 拆书结果（结构化 JSON，可手动修改）
- generations            : 每次 AI 生成的正文 / 大纲记录
- usage_records          : token 消耗与费用流水
- app_settings           : 用户设置（模型名、温度、提示词覆盖等）
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500), index=True)
    author: Mapped[str] = mapped_column(String(200), default="")
    # fanqie | txt | epub | manual
    source: Mapped[str] = mapped_column(String(20), default="manual")
    source_id: Mapped[str] = mapped_column(String(200), default="")
    intro: Mapped[str] = mapped_column(Text, default="")
    cover_url: Mapped[str] = mapped_column(String(1000), default="")
    total_chapters: Mapped[int] = mapped_column(Integer, default=0)
    analyzed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    chapters: Mapped[list["Chapter"]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        order_by="Chapter.number",
    )
    analysis: Mapped["BookAnalysis | None"] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        uselist=False,
    )
    generations: Mapped[list["Generation"]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
    )


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer, index=True)  # 第几章（从 1 开始）
    title: Mapped[str] = mapped_column(String(500), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    # True = 原文；False = AI 生成/续写
    is_original: Mapped[bool] = mapped_column(Boolean, default=True)
    # ai | import
    origin: Mapped[str] = mapped_column(String(20), default="import")
    summary: Mapped[str] = mapped_column(Text, default="")
    vectorized: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    book: Mapped[Book] = relationship(back_populates="chapters")


class BookAnalysis(Base):
    """一本书的拆书结果，每个板块独立 JSON，方便前端分块编辑。"""

    __tablename__ = "book_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), unique=True, index=True
    )
    synopsis: Mapped[str] = mapped_column(Text, default="")            # 全书总纲
    outline: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)     # 分卷 / 阶段大纲
    characters: Mapped[list[Any]] = mapped_column(JSON, default=list)      # 人物卡
    worldview: Mapped[list[Any]] = mapped_column(JSON, default=list)       # 世界观
    timeline: Mapped[list[Any]] = mapped_column(JSON, default=list)        # 时间线
    foreshadows: Mapped[list[Any]] = mapped_column(JSON, default=list)     # 伏笔
    style: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)      # 文风样本
    chapter_summaries: Mapped[list[Any]] = mapped_column(JSON, default=list)  # 每章摘要
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)        # 原始模型输出留档
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    book: Mapped[Book] = relationship(back_populates="analysis")


class Generation(Base):
    __tablename__ = "generations"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    chapter_number: Mapped[int] = mapped_column(Integer, default=0)  # 目标章节号
    # continue | outline | polish | rewrite
    mode: Mapped[str] = mapped_column(String(20), default="continue")
    # AI 自动拟定的章节标题（“全自动续写”时生成）
    title: Mapped[str] = mapped_column(String(500), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    goal: Mapped[str] = mapped_column(Text, default="")
    target_words: Mapped[int] = mapped_column(Integer, default=3000)
    prompt: Mapped[str] = mapped_column(Text, default="")   # 实际发送给模型的提示词（便于调试）
    result: Mapped[str] = mapped_column(Text, default="")
    source_text: Mapped[str] = mapped_column(Text, default="")  # 润色/重写时的输入
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cost_cny: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="success")  # success | error
    error: Mapped[str] = mapped_column(Text, default="")
    saved_chapter_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    book: Mapped[Book] = relationship(back_populates="generations")


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    task_type: Mapped[str] = mapped_column(String(40), default="")  # continue|analyze|summarize|polish
    model: Mapped[str] = mapped_column(String(120), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cost_cny: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class AppSetting(Base):
    """键值设置表。value 统一存 JSON，便于扩展。"""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, default=None)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
