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
    kind: str = "imported"
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
    kind: str = "imported"


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
    chapter_plan: Any = None
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
    chapter_plan: Any = None


class AnalyzeRequest(BaseModel):
    model: str | None = None              # 留空使用设置里的“复杂任务模型”
    provider_id: int | None = None        # 指定网关；留空用当前激活网关
    batch_size: int = 5                   # 每批处理的章节数（推理模型输出预算有限，别调太大）
    max_chapters: int | None = None       # 只拆前 N 章（控制成本）
    redo_summaries: bool = False          # 为 True 时重新生成章节摘要
    with_states: bool = True              # 拆书后自动补建「剧情状态层」（续写一致性的基础）
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
    provider_id: int | None = None        # 每次生成前可手动切换网关
    mode: Literal["continue", "outline", "polish", "rewrite"] = "continue"
    source_text: str = ""                 # polish / rewrite 模式的输入
    temperature: float | None = None
    retrieval_top_k: int | None = None
    auto_save: bool = False               # 生成成功后自动保存为章节
    previous_chapters: int = 2            # 携带最近 N 章原文（1~5）
    auto_goal: bool = False               # True = 即使填了目标也让 AI 重新推演剧情
    chapter_title: str = ""               # 章节标题，留空则用 AI 拟定的标题


class BatchGenerateRequest(BaseModel):
    """一口气连写 N 章（全自动：AI 自己决定每章写什么）。"""

    book_id: int
    count: int = Field(default=5, ge=1, le=20)
    target_words: int = 3000
    model: str | None = None
    provider_id: int | None = None
    previous_chapters: int = 2
    start_chapter: int | None = None      # 从第几章开始写（默认接续最后一章）


class RetrievedChunk(BaseModel):
    chapter_number: int
    chapter_title: str
    score: float
    text: str
    recall: str = "semantic"              # semantic = 语义召回；keyword = 关键词必召回


class GenerateContext(BaseModel):
    chapter_number: int
    model: str
    prompt: str
    outline_text: str
    core_settings_text: str
    foreshadow_text: str
    story_state: str = ""
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
    provider_id: int | None = None


# ----------------------------- 剧情状态 -----------------------------
class CharacterStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    importance: str
    identity: str
    location: str
    doing: str
    mood: str
    goal: str
    status: str
    items: Any
    knows: Any
    last_seen_chapter: int


class ChapterStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chapter_number: int
    summary: str
    location: str
    time_note: str
    present_characters: Any
    items: Any
    known_facts: Any
    open_threads: Any
    new_foreshadows: Any
    resolved_foreshadows: Any


class StoryStateOut(BaseModel):
    book_id: int
    snapshot: str                    # 拼装好的、真正会喂给模型的那段文本
    chapters: list[ChapterStateOut]
    characters: list[CharacterStateOut]
    missing_chapters: list[int]      # 还没抽取状态的章节号
    state_model: str = ""            # 状态抽取会用的模型
    auto_extract: bool = True


class ExtractStatesRequest(BaseModel):
    model: str | None = None
    provider_id: int | None = None
    upto: int | None = None          # 只抽取目标章节之前的内容
    batch_size: int = 4
    limit: int = 400


# ----------------------------- 从零原创 -----------------------------
class OriginalBookRequest(BaseModel):
    """用一句话设定，让 AI 生成完整故事圣经并建书。"""

    title: str = Field(min_length=1, max_length=500)
    genre: str = Field(default="玄幻爽文", description="题材/类型")
    protagonist: str = Field(default="", description="主角设定")
    hook: str = Field(default="", description="核心金手指 / 卖点")
    style_hint: str = Field(default="", description="风格要求")
    total_chapters: int = Field(default=120, ge=10, le=2000)
    target_words: int = Field(default=3000, ge=500, le=10000)
    extra: str = ""
    model: str | None = None
    provider_id: int | None = None
    plan_chapters: int = Field(default=20, ge=0, le=200, description="建书时预先排多少章的计划")
    plan_batch_size: int = Field(
        default=5, ge=1, le=20, description="每次让模型输出几章计划（越小越快、越不容易被截断）"
    )


class PlanChaptersRequest(BaseModel):
    start_chapter: int = Field(default=1, ge=1)
    count: int = Field(default=20, ge=1, le=200)
    model: str | None = None
    provider_id: int | None = None
    replace: bool = False            # True = 覆盖已有计划
    batch_size: int = Field(default=5, ge=1, le=20, description="每次让模型输出几章计划")


# ----------------------------- 设置 / 用量 -----------------------------
class SettingsOut(BaseModel):
    settings: dict[str, Any]
    candidate_models: list[str]
    providers: list[dict[str, Any]] = []
    active_provider: dict[str, Any] | None = None


class SettingsUpdate(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    text_model: str | None = None
    reasoning_model: str | None = None
    active_provider_id: int | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, ge=256, le=65536)
    retrieval_top_k: int | None = Field(default=None, ge=1, le=30)
    keyword_recall_enabled: bool | None = None
    keyword_recall_limit: int | None = Field(default=None, ge=0, le=20)
    embedding_model: str | None = None
    # 剧情状态相关
    auto_extract_state: bool | None = None
    state_model: str | None = None
    previous_full_chapter: bool | None = None
    previous_tail_chars: int | None = Field(default=None, ge=500, le=20000)
    state_snapshot_budget: int | None = Field(default=None, ge=1000, le=30000)
    usd_to_cny: float | None = Field(default=None, gt=0)
    pricing: dict[str, dict[str, float]] | None = None
    fanqie_mcp_command: str | None = None
    fanqie_api_base: str | None = None
    prompt_overrides: dict[str, str] | None = None


class ProviderIn(BaseModel):
    id: int | None = None
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None       # 空字符串 = 不改动；null = 清空
    text_model: str | None = None
    reasoning_model: str | None = None
    cheap_model: str | None = None
    pricing: dict[str, dict[str, float]] | None = None
    note: str | None = None
    is_active: bool | None = None


class ProviderOut(BaseModel):
    id: int
    name: str
    base_url: str
    text_model: str
    reasoning_model: str
    cheap_model: str
    pricing: dict[str, Any] = {}
    note: str = ""
    is_active: bool
    api_key_present: bool
    api_key_masked: str = ""


class ProviderListOut(BaseModel):
    providers: list[ProviderOut]
    active_provider_id: int | None = None
    resolved: dict[str, Any] = {}    # 实际生效的连接参数（已脱敏），便于排查


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
