"""文本处理工具：中文分章、字数统计、摘要截断、分块。"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 中文网文章节标题：第123章 / 第一百二十三回 / 第3节 ...
_ZH_CHAPTER = re.compile(
    r"^[\s\u3000]{0,8}(?:正文[\s\u3000]*)?第[\s\u3000]*([0-9０-９零一二三四五六七八九十百千万两]+)"
    r"[\s\u3000]*([章回节集])[\s\u3000]*[：:、.．·\-—]*[\s\u3000]*(.{0,60})$",
    re.MULTILINE,
)
# 卷标题（作为二级结构，单独识别）
_ZH_VOLUME = re.compile(
    r"^[\s\u3000]{0,8}第[\s\u3000]*([0-9０-９零一二三四五六七八九十百千万两]+)"
    r"[\s\u3000]*([卷部篇])[\s\u3000]*[：:、.．·\-—]*[\s\u3000]*(.{0,60})$",
    re.MULTILINE,
)
# 英文章节：Chapter 12
_EN_CHAPTER = re.compile(
    r"^[\s\u3000]{0,8}(?:Chapter|CHAPTER|chapter)[\s\u3000]+(\d+)[\s\u3000]*[：:、.．\-—]*[\s\u3000]*(.{0,60})$",
    re.MULTILINE,
)

# 明显的广告 / 站点声明行，导入时剔除
_NOISE_LINE = re.compile(
    r"(www\.[\w.-]+|https?://|请记住本站|章节错误|内容严重缺失|加入书签|投推荐票|手机版阅读|一秒记住|天才一秒)"
)

_PLACEHOLDER_TITLES = {"", "正文", "无题", "-", "—"}


@dataclass
class RawChapter:
    number: int
    title: str
    content: str


def count_words(text: str) -> int:
    """中文字数统计：忽略所有空白字符。"""
    return len(re.sub(r"\s+", "", text or ""))


def clean_text(text: str) -> str:
    """去掉导入文本里的广告行、多余空行。"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    lines = [line.rstrip() for line in text.split("\n")]
    kept = [line for line in lines if not _NOISE_LINE.search(line)]
    text = "\n".join(kept)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalize_title(raw: str) -> str:
    title = re.sub(r"[\s\u3000]+", " ", raw or "").strip(" :：、.．-—·\t")
    return "" if title in _PLACEHOLDER_TITLES else title


def _match_boundaries(text: str) -> list[tuple[int, str]]:
    """返回 [(起始下标, 标题)]，按位置排序。

    标题保留作者原文写法（例如「第一章 觉醒」），不强行转成阿拉伯数字，
    这样目录看起来和原书一致。
    """
    matches: list[tuple[int, str]] = []
    for m in _ZH_CHAPTER.finditer(text):
        title = _normalize_title(f"第{m.group(1)}{m.group(2)} {m.group(3)}")
        matches.append((m.start(), title or m.group(0).strip()))
    if len(matches) < 3:
        for m in _EN_CHAPTER.finditer(text):
            title = _normalize_title(f"Chapter {m.group(1)} {m.group(2)}")
            matches.append((m.start(), title or m.group(0).strip()))
    if len(matches) < 3:
        for m in _ZH_VOLUME.finditer(text):
            title = _normalize_title(f"第{m.group(1)}{m.group(2)} {m.group(3)}")
            matches.append((m.start(), title or m.group(0).strip()))
    matches.sort(key=lambda item: item[0])

    # 过滤误判：只有当两处标题之间内容很短、且没有任何句末标点时，
    # 才认定它是目录/引用行（真实章节正文一定会出现「。！？」等标点）。
    filtered: list[tuple[int, str]] = []
    for pos, title in matches:
        if filtered:
            prev_pos = filtered[-1][0]
            between = text[prev_pos:pos]
            if count_words(between) < 80 and not re.search(r"[。！？!?…”」』]", between):
                filtered[-1] = (prev_pos, title)
                continue
        filtered.append((pos, title))
    return filtered


def split_chapters(text: str, *, default_title: str = "") -> list[RawChapter]:
    """把整本书文本切成章节。

    1. 优先按「第X章 / 第X回 / Chapter N」标题切分；
    2. 没有标题时按段落聚合，每约 3000 字切一章；
    3. 标题之前的序章内容若超过 300 字，会保留为第 1 章（楔子）。
    """
    text = clean_text(text)
    if not text:
        return []

    boundaries = _match_boundaries(text)
    chapters: list[RawChapter] = []

    if len(boundaries) >= 2:
        preface = text[: boundaries[0][0]].strip()
        if count_words(preface) >= 300:
            chapters.append(RawChapter(number=1, title="楔子", content=preface))
        for idx, (pos, title) in enumerate(boundaries):
            end = boundaries[idx + 1][0] if idx + 1 < len(boundaries) else len(text)
            body = text[pos:end].strip()
            # 去掉标题行本身
            newline = body.find("\n")
            body = body[newline + 1 :].strip() if newline != -1 else ""
            if count_words(body) < 20:
                continue
            chapters.append(RawChapter(number=len(chapters) + 1, title=title, content=body))
    else:
        for index, body in enumerate(_split_by_size(text, 3000), start=1):
            title = default_title or f"第{index}章"
            chapters.append(RawChapter(number=index, title=title, content=body))

    # 重新编号，保证连续
    for index, chapter in enumerate(chapters, start=1):
        chapter.number = index
    return chapters


def _split_by_size(text: str, size: int) -> list[str]:
    """没有章节标题时的兜底切分：按段落单元聚合，每约 size 字一块。"""
    units = _split_paragraphs(text, size)
    chunks: list[str] = []
    buffer: list[str] = []
    length = 0
    for unit in units:
        buffer.append(unit)
        length += count_words(unit)
        if length >= size:
            chunks.append("\n".join(buffer))
            buffer, length = [], 0
    if buffer:
        chunks.append("\n".join(buffer))
    return chunks


def chunk_text(text: str, *, size: int = 500, overlap: int = 80) -> list[str]:
    """把章节正文切成带重叠的检索块。按段落/句子边界对齐，避免切断句子。"""
    text = (text or "").strip()
    if not text:
        return []
    if count_words(text) <= size:
        return [text]

    units = _split_paragraphs(text, size)
    chunks: list[str] = []
    buffer: list[str] = []
    length = 0

    for unit in units:
        unit_len = count_words(unit)
        if length + unit_len > size and buffer:
            chunks.append("\n".join(buffer))
            # 保留尾部若干单元作为重叠，保证跨块的上下文连续
            tail_units: list[str] = []
            tail_len = 0
            for prev in reversed(buffer):
                tail_units.insert(0, prev)
                tail_len += count_words(prev)
                if tail_len >= overlap:
                    break
            buffer, length = tail_units, tail_len
        buffer.append(unit)
        length += unit_len

    if buffer:
        chunks.append("\n".join(buffer))
    return [c for c in chunks if count_words(c) >= 30]


def _split_paragraphs(text: str, size: int) -> list[str]:
    """拆成不超过 size 字的单元。没有换行的超长段落会按句子边界再拆。"""
    units: list[str] = []
    for raw in text.split("\n"):
        para = raw.strip()
        if not para:
            continue
        if count_words(para) <= size:
            units.append(para)
        else:
            units.extend(_split_oversized(para, size))
    return units


def _split_oversized(para: str, size: int) -> list[str]:
    """按中英文句末标点断句，再按 size 聚合；实在不行才硬切。"""
    sentences = [s for s in re.split(r"(?<=[。！？!?…；;])", para) if s.strip()]
    if not sentences:
        sentences = [para]

    pieces: list[str] = []
    buffer = ""
    for sentence in sentences:
        if buffer and count_words(buffer) + count_words(sentence) > size:
            pieces.append(buffer)
            buffer = sentence
        else:
            buffer += sentence
    if buffer.strip():
        pieces.append(buffer)

    # 极端情况（整段没有一个句末标点）：硬切
    final: list[str] = []
    for piece in pieces:
        while count_words(piece) > size * 2:
            final.append(piece[: size * 2])
            piece = piece[size * 2 :]
        if piece.strip():
            final.append(piece)
    return final or [para]


def tail(text: str, chars: int) -> str:
    """取文本末尾 N 个字符（用于“上一章结尾”）。"""
    text = (text or "").strip()
    if len(text) <= chars:
        return text
    return "……" + text[-chars:]


def head(text: str, chars: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= chars else text[:chars] + "……"


def clamp_text(text: str, max_chars: int) -> str:
    """把长文本压到 max_chars 内：保留头尾，中间省略。"""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n……（中间省略）……\n" + text[-half:]
