"""RAG 检索与上下文拼装。

核心约束：
- **禁止**把全书塞进上下文；
- 上下文按「优先级」分块，而不是平均分配预算：
    1. 剧情状态快照（谁在哪、在做什么、手上有什么、谁不在场）← 一致性关键，最高优先
    2. 上一章全文（不再是 3000 字尾巴）
    3. 未回收伏笔
    4. 大纲 / 总纲
    5. 相关人物卡 + 世界观 + 文风样本
    6. 检索到的原文片段（语义召回 + 关键词**必召回**兜底）
- 检索严格排除“未来章节”（chapter_number < 目标章节），避免剧情穿越和剧透泄漏。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ..models import Book, BookAnalysis, Chapter
from ..settings_store import get_settings
from .indexing import get_store
from .state import build_state_snapshot, latest_state
from .text_utils import clamp_text, tail

logger = logging.getLogger(__name__)

# 各分块的字符预算。状态快照给得最多，因为它是「对不上」问题的直接解药；
# 静态设定（人物卡/世界观/文风）反而可以压缩，它们不随剧情变化。
STATE_BUDGET = 6000           # 剧情状态快照
CORE_SETTINGS_BUDGET = 4200   # 核心设定（人物卡 + 世界观 + 文风）
SNIPPET_BUDGET = 3000         # 检索片段
OUTLINE_BUDGET = 2000         # 大纲字符预算
FORESHADOW_BUDGET = 1600      # 伏笔字符预算
PREVIOUS_CHAPTER_BUDGET = 6000  # 上一章全文预算（超出才截断）


@dataclass
class PromptSlots:
    """提示词各占位符的内容。"""

    chapter_number: int
    outline: str = ""
    core_settings: str = ""
    foreshadows: str = ""
    story_state: str = ""
    previous_tail: str = ""
    retrieved: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""

    def as_mapping(self) -> dict[str, Any]:
        return {
            "chapter_number": self.chapter_number,
            "outline": self.outline,
            "core_settings": self.core_settings,
            "foreshadows": self.foreshadows,
            "story_state": self.story_state,
            "previous_tail": self.previous_tail,
            "chapter_plan": "",
            "target_words": "",
            "goal": "",
            "source_text": "",
        }


# ----------------------------------------------------------------------
# 格式化工具
# ----------------------------------------------------------------------
def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


# 供其它模块直接使用（不强求调用方去碰私有名）
as_list = _as_list


def format_characters(items: list[Any], limit: int = 12) -> str:
    lines: list[str] = []
    for item in items[:limit]:
        card = _as_dict(item)
        if not card:
            continue
        name = card.get("name") or "未知"
        parts = [
            f"身份：{card.get('identity', '')}" if card.get("identity") else "",
            f"性格：{card.get('personality', '')}" if card.get("personality") else "",
            f"关系：{card.get('relations', '')}" if card.get("relations") else "",
            f"状态：{card.get('status', '')}" if card.get("status") else "",
        ]
        detail = "；".join(p for p in parts if p)
        lines.append(f"- {name}（{detail}）" if detail else f"- {name}")
    return "\n".join(lines)


def format_worldview(items: list[Any], limit: int = 15) -> str:
    lines: list[str] = []
    for item in items[:limit]:
        entry = _as_dict(item)
        if not entry:
            continue
        category = entry.get("category") or "设定"
        name = entry.get("name") or ""
        desc = entry.get("description") or ""
        lines.append(f"- [{category}] {name}：{desc}")
    return "\n".join(lines)


def format_foreshadows(items: list[Any], limit: int = 15) -> str:
    lines: list[str] = []
    for item in items:
        entry = _as_dict(item)
        if not entry:
            continue
        status = str(entry.get("status") or "未回收")
        if "回收" in status and "未" not in status:
            continue  # 已回收的不再送入上下文
        content = entry.get("content") or ""
        planted = entry.get("planted_chapter") or ""
        payoff = entry.get("possible_payoff") or ""
        text = f"- （第{planted}章埋下）{content}"
        if payoff:
            text += f" → 可考虑：{payoff}"
        lines.append(text)
        if len(lines) >= limit:
            break
    return "\n".join(lines)


def format_outline(analysis: BookAnalysis | None, chapter_number: int) -> str:
    if analysis is None:
        return "（暂无拆书结果，请在书籍页先执行“拆书”）"
    outline = _as_dict(analysis.outline)
    segments: list[str] = []
    if analysis.synopsis:
        segments.append(f"【全书总纲】{analysis.synopsis}")
    volumes = _as_list(outline.get("volumes"))
    for volume in volumes[-4:]:
        vol = _as_dict(volume)
        name = vol.get("name") or "分卷"
        rng = vol.get("range") or ""
        summary = vol.get("summary") or ""
        segments.append(f"【{name}】{rng} {summary}")
    if outline.get("current_stage"):
        segments.append(f"【当前阶段】{outline['current_stage']}")
    directions = _as_list(outline.get("next_directions"))
    if directions:
        segments.append("【后续可能走向】" + "；".join(str(d) for d in directions[:5]))
    segments.append(f"【本次目标】续写第 {chapter_number} 章。")
    return clamp_text("\n".join(segments), OUTLINE_BUDGET)


def format_chapter_plan(analysis: BookAnalysis | None, chapter_number: int) -> str:
    """原创模式：取出「本章的既定计划」，作为硬约束喂给规划/续写模型。

    导入续写模式下 chapter_plan 为空，返回空串，模板里自然什么都不显示。
    """
    if analysis is None:
        return ""
    plans = _as_list(analysis.chapter_plan)
    if not plans:
        return ""
    current = None
    upcoming: list[str] = []
    for item in plans:
        entry = _as_dict(item)
        number = int(entry.get("number") or 0)
        if not number:
            continue
        if number == chapter_number:
            current = entry
        elif number > chapter_number:
            upcoming.append(f"- 第{number}章 {entry.get('title') or ''}：{entry.get('goal') or ''}")
        if len(upcoming) >= 3:
            break

    lines: list[str] = []
    if current:
        lines.append(
            f"# 本章既定计划（必须遵守，不要改写主线）\n"
            f"- 标题：{current.get('title') or ''}\n"
            f"- 目标：{current.get('goal') or ''}"
        )
        if current.get("arc"):
            lines.append(f"- 所属阶段：{current['arc']}")
        advance = _as_list(current.get("foreshadows_to_advance"))
        if advance:
            lines.append("- 本章要推进的伏笔：" + "；".join(str(a) for a in advance))
    if upcoming:
        lines.append("# 之后的既定走向（仅作参考，不要提前写）\n" + "\n".join(upcoming[:3]))
    return "\n".join(lines)


def format_style(style: Any, limit: int = 1200) -> str:
    data = _as_dict(style)
    if not data:
        return ""
    lines: list[str] = ["【文风样本（必须模仿）】"]
    if data.get("tone"):
        lines.append(f"- 基调：{data['tone']}")
    if data.get("pacing"):
        lines.append(f"- 节奏：{data['pacing']}")
    if data.get("dialogue_style"):
        lines.append(f"- 对话习惯：{data['dialogue_style']}")
    words = _as_list(data.get("common_words"))
    if words:
        lines.append("- 高频词：" + "、".join(str(w) for w in words[:20]))
    patterns = _as_list(data.get("sentence_patterns"))
    if patterns:
        lines.append("- 常用句式：" + "；".join(str(p) for p in patterns[:8]))
    taboos = _as_list(data.get("taboos"))
    if taboos:
        lines.append("- 避免：" + "；".join(str(t) for t in taboos[:6]))
    if data.get("sample"):
        lines.append(f"- 原文样例：{data['sample']}")
    return clamp_text("\n".join(lines), limit)


# ----------------------------------------------------------------------
# 相关性筛选
# ----------------------------------------------------------------------
def _relevance_keywords(goal: str, retrieved: list[dict[str, Any]]) -> str:
    text = goal or ""
    for item in retrieved[:5]:
        text += "\n" + str(item.get("text", ""))[:400]
    return text


_IMPORTANCE_ORDER = {"主角": 0, "重要配角": 1, "配角": 2}


def select_characters(
    characters: list[Any], keywords: str, limit: int = 10
) -> list[Any]:
    """优先返回在目标/检索片段中被提到的人物，其余按重要度补足。"""
    mentioned: list[Any] = []
    rest: list[Any] = []
    for item in characters:
        card = _as_dict(item)
        name = str(card.get("name") or "").strip()
        if name and name in keywords:
            mentioned.append(item)
        else:
            rest.append(item)
    rest.sort(key=lambda x: _IMPORTANCE_ORDER.get(str(_as_dict(x).get("importance") or ""), 3))
    return (mentioned + rest)[:limit]


def select_worldview(worldview: list[Any], keywords: str, limit: int = 12) -> list[Any]:
    mentioned, rest = [], []
    for item in worldview:
        entry = _as_dict(item)
        name = str(entry.get("name") or "").strip()
        if name and name in keywords:
            mentioned.append(item)
        else:
            rest.append(item)
    # 主角相关设定优先，其次按类别分组顺序
    return (mentioned + rest)[:limit]


# ----------------------------------------------------------------------
# 检索
# ----------------------------------------------------------------------
def retrieve(
    db: Session,
    book: Book,
    *,
    target_number: int,
    goal: str,
    top_k: int = 8,
    recent_tail: str = "",
    character_names: list[str] | None = None,
    min_score: float = -1.0,
    must_recall: list[str] | None = None,
    keyword_recall: bool = True,
    keyword_limit: int = 4,
    include_meta: bool = False,
) -> list[dict[str, Any]]:
    """检索相关原文片段（排除目标章节及之后的章节）。

    两路召回合并：
    1. **语义召回**（向量相似度）—— 负责「氛围、情境相似」的内容；
    2. **关键词必召回**（字面命中）—— 负责「人名/物品名/地点名」这类
       换了说法就召不回来的关键锚点。关键词命中优先级更高。
    """
    embedder, store = get_store(db)

    # 向量模型换过之后，旧向量属于另一个语义空间（维度可能相同而毫无意义），
    # 必须先清空再重建，否则会检索出垃圾片段且不报错。
    if store.ensure_book(book.id):
        logger.warning(
            "书籍 %s 的向量索引与当前向量模型不匹配，已清空。"
            "请重新执行「拆书」或「建立向量索引」。",
            book.id,
        )
        return []

    # ---------- 1. 关键词必召回（不依赖相似度，先占位） ----------
    recalled: list[dict[str, Any]] = []
    keywords = [k.strip() for k in (must_recall or []) if k and k.strip()]
    if keyword_recall and keywords:
        try:
            recalled = store.keyword_search(
                book.id,
                keywords,
                limit=keyword_limit,
                max_chapter_number=target_number,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("关键词召回失败 book=%s: %s", book.id, exc)

    # ---------- 2. 语义召回 ----------
    query_parts = [goal or ""]
    if recent_tail:
        query_parts.append(recent_tail[-600:])
    if character_names:
        query_parts.append(" ".join(character_names[:8]))
    query_text = "\n".join(p for p in query_parts if p).strip()

    semantic: list[dict[str, Any]] = []
    if query_text:
        try:
            vector = embedder.encode_one(query_text, is_query=True)
            semantic = store.query(
                book.id,
                vector,
                top_k=top_k,
                max_chapter_number=target_number,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("检索失败 book=%s: %s", book.id, exc)

    # ---------- 3. 合并去重：关键词命中优先，且不占用语义召回的配额 ----------
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in [*recalled, *semantic]:
        if float(item.get("score", 0)) < min_score:
            continue
        fingerprint = str(item["text"])[:60]
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        deduped.append(item)

    if not include_meta:
        for item in deduped:
            item.pop("recall", None)
    return deduped


def build_core_settings(
    analysis: BookAnalysis | None,
    *,
    characters: list[Any],
    worldview: list[Any],
    retrieved: list[dict[str, Any]],
) -> str:
    blocks: list[str] = []
    if characters:
        blocks.append("【人物卡】\n" + format_characters(characters))
    if worldview:
        blocks.append("【世界观设定】\n" + format_worldview(worldview))
    if analysis is not None:
        style_text = format_style(analysis.style)
        if style_text:
            blocks.append(style_text)
    if retrieved:
        snippets = []
        for item in retrieved[:8]:
            # 关键词必召回的片段标注出来，提示模型这是「关键锚点」而非泛泛相关
            tag = "★关键锚点 " if item.get("recall") == "keyword" else ""
            snippets.append(
                f"{tag}（第{item['chapter_number']}章 {item.get('chapter_title') or ''}）"
                f"{item['text']}"
            )
        blocks.append(
            "【原文相关片段（供衔接参考，★表示与该人物/物品直接相关，务必核对）】\n"
            + clamp_text("\n---\n".join(snippets), SNIPPET_BUDGET)
        )
    if not blocks:
        return "（暂无拆书结果与检索片段，请先执行拆书并建立向量索引）"
    return clamp_text("\n\n".join(blocks), CORE_SETTINGS_BUDGET)


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------
def load_analysis(db: Session, book_id: int) -> BookAnalysis | None:
    return db.query(BookAnalysis).filter(BookAnalysis.book_id == book_id).first()


def resolve_target_number(db: Session, book: Book, requested: int | None) -> int:
    """确定“要续写的章节号”：默认 = 最后一章 + 1。"""
    if requested and requested > 0:
        return requested
    last = (
        db.query(Chapter)
        .filter(Chapter.book_id == book.id)
        .order_by(Chapter.number.desc())
        .first()
    )
    return (last.number + 1) if last else 1


def previous_chapters_text(
    db: Session,
    book_id: int,
    target_number: int,
    count: int = 2,
    *,
    full_previous: bool = True,
    tail_chars: int = 3000,
    previous_budget: int = PREVIOUS_CHAPTER_BUDGET,
) -> tuple[str, str]:
    """返回 (上一章内容, 供检索用的最近正文尾部)。

    full_previous=True 时送入**上一章全文**——旧版只送末尾 3000 字，
    导致「上一章开头发生的场景转移」完全丢失，这是续写错位的常见来源。
    """
    count = max(1, min(count, 5))
    chapters = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.number < target_number)
        .order_by(Chapter.number.desc())
        .limit(count)
        .all()
    )
    if not chapters:
        return "", ""
    chapters.reverse()
    last = chapters[-1]
    if full_previous:
        head_text = clamp_text(last.content, previous_budget)
        # 超预算时明确提示被截断的位置，避免模型以为故事就到这里
        if len(last.content or "") > previous_budget:
            head_text += f"\n\n（……上一章剩余部分因长度限制省略……）"
    else:
        head_text = tail(last.content, tail_chars)
    return head_text, tail("\n".join(c.content for c in chapters), 1200)


def _collect_recall_keywords(
    db: Session,
    book_id: int,
    target_number: int,
    *,
    character_names: list[str],
    analysis: BookAnalysis | None,
    limit: int = 12,
) -> list[str]:
    """挑出「必须字面召回」的锚点词：人物名 + 当前关键物品 + 当前地点。

    这些词一旦漏掉，就会出现「主角的刀上一章还在，这一章凭空没了」这类硬伤。
    """
    keywords: list[str] = []

    # 1) 上一章在场人物 + 最近出场的主要人物
    state = latest_state(db, book_id, target_number)
    if state is not None:
        for item in _as_list(state.present_characters):
            name = str(_as_dict(item).get("name") or "").strip()
            if name:
                keywords.append(name)
        for item in _as_list(state.items):
            name = str(_as_dict(item).get("name") or "").strip()
            if name:
                keywords.append(name)
        location = (state.location or "").strip()
        if 1 < len(location) <= 12:
            keywords.append(location)

    # 2) 本轮粗筛出的人物
    keywords.extend(n for n in character_names if n)

    # 3) 未回收伏笔里的关键词（它们才是最容易断线的）
    if analysis is not None:
        for item in _as_list(analysis.foreshadows):
            entry = _as_dict(item)
            status = str(entry.get("status") or "未回收")
            if "回收" in status and "未" not in status:
                continue
            for keyword in _as_list(entry.get("keywords")):
                text = str(keyword or "").strip()
                if 1 < len(text) <= 12:
                    keywords.append(text)

    # 去重 + 限量，长词优先（长词更具体，命中率更高）
    unique = list(dict.fromkeys(k for k in keywords if k))
    unique.sort(key=len, reverse=True)
    return unique[:limit]


def build_slots(
    db: Session,
    book: Book,
    *,
    target_number: int,
    goal: str,
    top_k: int = 8,
    previous_count: int = 2,
    analysis: BookAnalysis | None = None,
) -> PromptSlots:
    """拼装续写所需的全部上下文（严格遵守 RAG 上下文预算）。"""
    analysis = analysis if analysis is not None else load_analysis(db, book.id)
    settings = get_settings(db, include_secrets=True)

    prev_tail, recent_tail = previous_chapters_text(
        db,
        book.id,
        target_number,
        previous_count,
        full_previous=bool(settings.get("previous_full_chapter", True)),
        tail_chars=int(settings.get("previous_tail_chars") or 3000),
    )

    characters_all = _as_list(analysis.characters) if analysis else []
    # 先用目标做第一轮筛选关键词，再用检索结果做第二轮，提高命中率
    coarse_keywords = f"{goal}\n{prev_tail}"
    coarse_characters = select_characters(characters_all, coarse_keywords, limit=8)
    names = [str(_as_dict(c).get("name") or "") for c in coarse_characters]

    must_recall = _collect_recall_keywords(
        db,
        book.id,
        target_number,
        character_names=[n for n in names if n],
        analysis=analysis,
    )

    retrieved = retrieve(
        db,
        book,
        target_number=target_number,
        goal=goal,
        top_k=top_k,
        recent_tail=recent_tail,
        character_names=[n for n in names if n],
        must_recall=must_recall,
        keyword_recall=bool(settings.get("keyword_recall_enabled", True)),
        keyword_limit=int(settings.get("keyword_recall_limit") or 4),
        include_meta=True,
    )

    keywords = _relevance_keywords(goal, retrieved) + prev_tail[-500:]
    characters = select_characters(characters_all, keywords, limit=10)
    worldview = select_worldview(_as_list(analysis.worldview) if analysis else [], keywords, limit=12)

    foreshadows = (
        format_foreshadows(_as_list(analysis.foreshadows)) if analysis else ""
    )
    if len(foreshadows) > FORESHADOW_BUDGET:
        foreshadows = clamp_text(foreshadows, FORESHADOW_BUDGET)

    story_state = build_state_snapshot(
        db,
        book.id,
        target_number=target_number,
        budget=int(settings.get("state_snapshot_budget") or STATE_BUDGET),
        analysis=analysis,
    )

    return PromptSlots(
        chapter_number=target_number,
        outline=format_outline(analysis, target_number),
        core_settings=build_core_settings(
            analysis, characters=characters, worldview=worldview, retrieved=retrieved
        ),
        foreshadows=foreshadows or "（暂无未回收伏笔记录）",
        story_state=story_state,
        previous_tail=prev_tail or "（这是全书第一章，无上一章内容）",
        retrieved=retrieved,
    )
