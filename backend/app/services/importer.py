"""小说导入：TXT / EPUB / 手动粘贴，以及统一入库逻辑。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import Book, Chapter
from .text_utils import RawChapter, clean_text, count_words, split_chapters

logger = logging.getLogger(__name__)

ENCODING_GUESSES = ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "utf-16")


@dataclass
class ParsedBook:
    title: str = ""
    author: str = ""
    intro: str = ""
    chapters: list[RawChapter] = field(default_factory=list)


def decode_bytes(data: bytes) -> str:
    """尽量正确地解码中文小说文件（UTF-8 / GB18030 / BIG5 ...）。"""
    if not data:
        return ""
    # BOM 优先
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    try:
        import chardet  # type: ignore

        guess = chardet.detect(data[:200_000])
        if guess.get("encoding") and (guess.get("confidence") or 0) > 0.5:
            try:
                return data.decode(guess["encoding"], errors="replace")
            except (LookupError, UnicodeDecodeError):
                pass
    except Exception:  # noqa: BLE001
        pass

    for encoding in ENCODING_GUESSES:
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def parse_txt(data: bytes, *, fallback_title: str = "") -> ParsedBook:
    text = decode_bytes(data)
    chapters = split_chapters(text, default_title="")
    title = _guess_title(text) or fallback_title or "未命名小说"
    return ParsedBook(title=title, author="", chapters=chapters)


def _guess_title(text: str) -> str:
    """从正文开头的《书名》/ 书名：... 里猜书名。"""
    head = text[:600]
    for line in head.split("\n")[:12]:
        line = line.strip()
        if not line:
            continue
        if line.startswith("书名") and "：" in line:
            return line.split("：", 1)[1].strip()
        if line.startswith("《") and "》" in line:
            return line.strip("《》 ").split("》")[0]
    return ""


def parse_epub(path: Path) -> ParsedBook:
    """解析 EPUB：每个 spine 文档视作一章。"""
    try:
        from bs4 import BeautifulSoup
        from ebooklib import ITEM_DOCUMENT, epub
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("缺少 EPUB 解析依赖，请执行 pip install EbookLib beautifulsoup4 lxml") from exc

    book = epub.read_epub(str(path))
    title = _meta_first(book, "title") or path.stem
    author = _meta_first(book, "creator")
    intro = _meta_first(book, "description")

    chapters: list[RawChapter] = []
    documents = list(book.get_items_of_type(ITEM_DOCUMENT))

    for item in documents:
        soup = BeautifulSoup(item.get_content(), "lxml")
        heading = soup.find(["h1", "h2", "h3"])
        heading_text = heading.get_text(strip=True) if heading else ""
        if heading:
            heading.decompose()
        body = clean_text(soup.get_text("\n", strip=True))
        if count_words(body) < 20 and count_words(heading_text) < 2:
            continue
        chapters.append(
            RawChapter(
                number=len(chapters) + 1,
                title=heading_text or f"第{len(chapters) + 1}章",
                content=body,
            )
        )

    # 文档过少（或都太短）时，退回整体按标题切分
    if len(chapters) < 3:
        merged = clean_text("\n\n".join(f"{c.title}\n{c.content}" for c in chapters))
        splitted = split_chapters(merged)
        if len(splitted) > len(chapters):
            chapters = splitted

    for index, chapter in enumerate(chapters, start=1):
        chapter.number = index
    return ParsedBook(title=title, author=author, intro=intro, chapters=chapters)


def _meta_first(book, key: str) -> str:
    try:
        values = book.get_metadata("DC", key)
        if values:
            return str(values[0][0]).strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


# ----------------------------------------------------------------------
def create_book(
    db: Session,
    *,
    title: str,
    author: str = "",
    intro: str = "",
    source: str = "manual",
    source_id: str = "",
    cover_url: str = "",
    chapters: list[RawChapter],
) -> Book:
    """把解析结果写成 Book + Chapter。自动去重、重新编号、跳过空章节。"""
    book = Book(
        title=title.strip() or "未命名小说",
        author=author.strip(),
        intro=(intro or "").strip()[:5000],
        source=source,
        source_id=str(source_id or ""),
        cover_url=cover_url or "",
    )
    db.add(book)
    db.flush()

    created = 0
    seen_titles: set[str] = set()
    for chapter in chapters:
        content = (chapter.content or "").strip()
        if count_words(content) < 20:
            continue
        title_key = f"{chapter.title}|{content[:50]}"
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        created += 1
        db.add(
            Chapter(
                book_id=book.id,
                number=created,
                title=(chapter.title or f"第{created}章").strip()[:500],
                content=content,
                word_count=count_words(content),
                is_original=True,
                origin="import",
            )
        )

    book.total_chapters = created
    db.commit()
    db.refresh(book)
    return book


def append_chapter(db: Session, book: Book, title: str, content: str) -> Chapter:
    last = (
        db.query(Chapter)
        .filter(Chapter.book_id == book.id)
        .order_by(Chapter.number.desc())
        .first()
    )
    number = (last.number + 1) if last else 1
    chapter = Chapter(
        book_id=book.id,
        number=number,
        title=title or f"第{number}章",
        content=content,
        word_count=count_words(content),
        is_original=True,
        origin="manual",
    )
    db.add(chapter)
    book.total_chapters = (book.total_chapters or 0) + 1
    db.commit()
    db.refresh(chapter)
    return chapter
