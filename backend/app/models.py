"""ORM 模型定义。

数据分层：
- books / chapters        : 小说原文
- book_analyses          : 拆书结果（结构化 JSON，可手动修改）
- chapter_states         : 每章的剧情状态增量（AI 自动抽取，续写一致性的核心）
- character_states       : 人物状态滚动快照（随章节推进 merge 更新）
- generations            : 每次 AI 生成的正文 / 大纲记录
- providers              : LLM 网关配置（OpenAI 兼容，可多个并存、随时切换）
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
    # imported = 拿别人的书续写（需要拆书）；original = 从零原创（先立设定再逐章写）
    kind: Mapped[str] = mapped_column(String(20), default="imported")
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
    # 是否已抽取过剧情状态（chapter_states 里有对应记录）
    state_extracted: Mapped[bool] = mapped_column(Boolean, default=False)
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
    # 原创模式的故事计划：[{number, title, goal, arc, key_events}]，续写时按计划推进
    chapter_plan: Mapped[list[Any]] = mapped_column(JSON, default=list)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)        # 原始模型输出留档
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    book: Mapped[Book] = relationship(back_populates="analysis")


class ChapterState(Base):
    """单章的剧情状态增量（由 AI 从本章正文中抽取）。

    这是解决「续写前后对不上」的核心数据：每写完一章就固化一次
    「人物在哪、在做什么、手上有什么、知道什么、哪些线还悬着」，
    后续章节不再依赖「上一章最后 3000 字」去猜。
    """

    __tablename__ = "chapter_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), index=True
    )
    chapter_number: Mapped[int] = mapped_column(Integer, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")         # 本章摘要（回填 chapter_summaries）
    location: Mapped[str] = mapped_column(Text, default="")        # 本章主要场景地点
    time_note: Mapped[str] = mapped_column(Text, default="")       # 时间推进（距上一章过了多久等）
    present_characters: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # present_characters: [{name, location, doing, mood, goal, items: [...]}]
    items: Mapped[list[Any]] = mapped_column(JSON, default=list)   # [{name, holder, note}]
    known_facts: Mapped[list[Any]] = mapped_column(JSON, default=list)  # 本章后新增的关键知情信息
    open_threads: Mapped[list[Any]] = mapped_column(JSON, default=list)  # 章末悬而未决的事
    new_foreshadows: Mapped[list[Any]] = mapped_column(JSON, default=list)
    resolved_foreshadows: Mapped[list[Any]] = mapped_column(JSON, default=list)
    model: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    book: Mapped[Book] = relationship()


class CharacterState(Base):
    """人物状态的滚动快照：每本书每人一条，随章节推进增量更新。

    与 BookAnalysis.characters（拆书时的静态快照）的区别：
    这里的数据随着 AI 续写不断被刷新，`location` / `doing` / `goal`
    始终指向「截至 last_seen_chapter 章」的真实状态。
    """

    __tablename__ = "character_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), index=True)
    importance: Mapped[str] = mapped_column(String(40), default="配角")
    identity: Mapped[str] = mapped_column(Text, default="")
    personality: Mapped[str] = mapped_column(Text, default="")
    relations: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(Text, default="")
    doing: Mapped[str] = mapped_column(Text, default="")
    mood: Mapped[str] = mapped_column(Text, default="")
    goal: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="")
    items: Mapped[list[Any]] = mapped_column(JSON, default=list)
    knows: Mapped[list[Any]] = mapped_column(JSON, default=list)
    last_seen_chapter: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    book: Mapped[Book] = relationship()


class Provider(Base):
    """LLM 网关配置（OpenAI 兼容协议），可配置多个并存、随时切换。

    数据全本地：api_key 明文存在本机 SQLite 里，接口返回时只给脱敏值。
    """

    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    base_url: Mapped[str] = mapped_column(String(500), default="")
    api_key: Mapped[str] = mapped_column(Text, default="")
    # 三档模型：正文 / 复杂任务 / 廉价任务（状态抽取、章节摘要）
    text_model: Mapped[str] = mapped_column(String(160), default="")
    reasoning_model: Mapped[str] = mapped_column(String(160), default="")
    cheap_model: Mapped[str] = mapped_column(String(160), default="")
    # 该网关的每百万 token 单价（覆盖全局 pricing）
    pricing: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


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
