"""Pydantic 请求 / 响应模型。"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ----------------------------- 书籍 / 章节 -----------------------------
class BookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    author: str
    source: str
    source_id: str
    intro: str
    cover_url: str
    total_chapters: int
    analyzed: bool
    created_at: dt.datetime
    updated_at: dt.datetime


class BookCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    author: str = ""
    intro: str = ""
    source: str = "manual"
    source_id: str = ""


class BookUpdate(BaseModel):
    title: str | None = None
    author: str | None = None
    intro: str | None = None


class ChapterBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: int
    title: str
    word_count: int
    is_original: bool
    origin: str
    vectorized: bool


class ChapterOut(ChapterBrief):
    content: str
    summary: str


class ChapterCreate(BaseModel):
    title: str = ""
    content: str = Field(min_length=1)
    number: int | None = None
    is_original: bool = True
    origin: str = "manual"


class ChapterUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    summary: str | None = None


class UploadResult(BaseModel):
    book: BookOut
    chapters_created: int
    message: str


# ----------------------------- 拆书 -----------------------------
class AnalysisOut(BaseModel):
    book_id: int
    synopsis: str
    outline: Any
    characters: Any
    worldview: Any
    timeline: Any
    foreshadows: Any
    style: Any
    chapter_summaries: Any
    updated_at: dt.datetime | None = None


class AnalysisSectionUpdate(BaseModel):
    synopsis: str | None = None
    outline: Any = None
    characters: Any = None
    worldview: Any = None
    timeline: Any = None
    foreshadows: Any = None
    style: Any = None
    chapter_summaries: Any = None


class AnalyzeRequest(BaseModel):
    model: str | None = None              # 留空使用设置里的“复杂任务模型”
    batch_size: int = 5                   # 每批处理的章节数（推理模型输出预算有限，别调太大）
    max_chapters: int | None = None       # 只拆前 N 章（控制成本）
    redo_summaries: bool = False          # 为 True 时重新生成章节摘要
    section: Literal[
        "all", "characters", "worldview", "timeline", "foreshadows", "style", "outline"
    ] = "all"


# ----------------------------- 续写 -----------------------------
class GenerateRequest(BaseModel):
    book_id: int
    chapter_number: int | None = None     # 目标章节号，默认 = 原文最后一章 + 1
    goal: str = Field(default="", description="本章目标；留空则由 AI 自动决定写什么")
    target_words: int = 3000
    model: str | None = None              # 每次生成前可手动切换模型
    mode: Literal["continue", "outline", "polish", "rewrite"] = "continue"
    source_text: str = ""                 # polish / rewrite 模式的输入
    temperature: float | None = None
    retrieval_top_k: int | None = None
    auto_save: bool = False               # 生成成功后自动保存为章节
    previous_chapters: int = 2            # 携带最近 N 章原文（1~3）
    auto_goal: bool = False               # True = 即使填了目标也让 AI 重新推演剧情
    chapter_title: str = ""               # 章节标题，留空则用 AI 拟定的标题


class BatchGenerateRequest(BaseModel):
    """一口气连写 N 章（全自动：AI 自己决定每章写什么）。"""

    book_id: int
    count: int = Field(default=5, ge=1, le=20)
    target_words: int = 3000
    model: str | None = None
    previous_chapters: int = 2
    start_chapter: int | None = None      # 从第几章开始写（默认接续最后一章）


class RetrievedChunk(BaseModel):
    chapter_number: int
    chapter_title: str
    score: float
    text: str


class GenerateContext(BaseModel):
    chapter_number: int
    model: str
    prompt: str
    outline_text: str
    core_settings_text: str
    foreshadow_text: str
    previous_tail: str
    retrieved: list[RetrievedChunk]
    goal: str = ""
    title: str = ""
    auto_planned: bool = False
    plan: dict[str, Any] | None = None


class GenerationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    book_id: int
    chapter_number: int
    mode: str
    model: str
    title: str = ""
    goal: str
    target_words: int
    result: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cost_cny: float
    duration_ms: int
    status: str
    error: str
    saved_chapter_id: int | None
    created_at: dt.datetime


class SaveGenerationRequest(BaseModel):
    title: str | None = None
    number: int | None = None
    replace_chapter_id: int | None = None


class ConsistencyRequest(BaseModel):
    book_id: int
    source_text: str
    model: str | None = None


# ----------------------------- 设置 / 用量 -----------------------------
class SettingsOut(BaseModel):
    settings: dict[str, Any]
    candidate_models: list[str]


class SettingsUpdate(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    text_model: str | None = None
    reasoning_model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, ge=256, le=65536)
    retrieval_top_k: int | None = Field(default=None, ge=1, le=30)
    embedding_model: str | None = None
    usd_to_cny: float | None = Field(default=None, gt=0)
    pricing: dict[str, dict[str, float]] | None = None
    fanqie_mcp_command: str | None = None
    fanqie_api_base: str | None = None
    prompt_overrides: dict[str, str] | None = None


class ModelInfo(BaseModel):
    id: str
    owned_by: str = ""


class UsageSummary(BaseModel):
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    total_cost_cny: float
    by_model: list[dict[str, Any]]
    by_task: list[dict[str, Any]]
    by_book: list[dict[str, Any]]
    recent: list[dict[str, Any]]


class VectorizeRequest(BaseModel):
    force: bool = False
    max_chapters: int | None = None


class PasteRequest(BaseModel):
    title: str = ""
    author: str = ""
    content: str = Field(min_length=1)
    chapter_title: str = ""
    split: bool = True   # True = 按“第X章”自动切分；False = 整段作为一章


class JobOut(BaseModel):
    id: str
    kind: str
    status: Literal["pending", "running", "success", "error", "cancelled"]
    progress: float
    message: str
    total: int
    done: int
    result: Any = None
    error: str = ""
    created_at: dt.datetime


# ----------------------------- 番茄小说 -----------------------------
class FanqieSearchRequest(BaseModel):
    keyword: str = Field(min_length=1)


class FanqieImportRequest(BaseModel):
    book_id: str = Field(min_length=1, description="番茄书籍 ID")
    title: str = ""
    max_chapters: int | None = None
    start_chapter: int = 1
    analyze: bool = False


class FanqieStatusOut(BaseModel):
    configured: bool
    available: bool
    command: str
    tools: list[str] = []
    message: str = ""
