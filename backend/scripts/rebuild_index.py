"""重建向量索引（命令行工具）。

用途：
- 换了向量模型（例如从内置哈希向量换成 bge-small-zh 语义向量）之后，必须重建索引；
- 导入/删改章节后想强制刷新；
- 排查检索问题时看看到底索引了多少块。

用法（在 backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\rebuild_index.py             # 重建所有书的索引
    .venv\\Scripts\\python.exe scripts\\rebuild_index.py --book-id 1 # 只重建某一本
    .venv\\Scripts\\python.exe scripts\\rebuild_index.py --check "断剑 剑意"   # 顺便做一次检索抽查

脚本只动向量库，不会修改小说正文，可以放心重复执行。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.database import SessionLocal, init_db  # noqa: E402
from app.models import Book, Chapter  # noqa: E402
from app.services.indexing import get_store, index_chapters  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="重建向量索引")
    parser.add_argument("--book-id", type=int, default=None, help="只处理指定书籍 ID")
    parser.add_argument("--check", type=str, default="", help="重建后做一次检索抽查的查询词")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        embedder, store = get_store(db)
        print("=" * 68)
        print("向量化后端")
        print("=" * 68)
        print(f"  模式     : {embedder.mode}")
        print(f"  模型     : {embedder.model_name}")
        print(f"  维度     : {embedder.dimension}")
        print(f"  向量库   : {store.backend_name}")

        books = (
            [db.get(Book, args.book_id)]
            if args.book_id
            else db.query(Book).order_by(Book.id).all()
        )
        books = [b for b in books if b is not None]
        if not books:
            print("\n没有找到书。")
            return 1

        for book in books:
            chapters = (
                db.query(Chapter)
                .filter(Chapter.book_id == book.id)
                .order_by(Chapter.number)
                .all()
            )
            print("\n" + "-" * 68)
            print(f"《{book.title}》 #{book.id}：{len(chapters)} 章")
            total_chunks = store.count(book.id)
            print(f"  重建前向量块数：{total_chunks}")

            started = time.perf_counter()
            state = {"last": ""}

            def on_progress(done: int, total: int, _book_id: int = book.id) -> None:
                line = f"  向量化 {done}/{total}"
                if line != state["last"] and (done == total or done % 5 == 0):
                    print(line, flush=True)
                    state["last"] = line

            stats = index_chapters(db, book.id, chapters, force=True, on_progress=on_progress)
            elapsed = time.perf_counter() - started
            print(
                f"  完成：{stats['indexed']} 章 / {store.count(book.id)} 块，"
                f"耗时 {elapsed:.1f} 秒"
            )

            if args.check:
                vector = embedder.encode_one(args.check, is_query=True)
                hits = store.query(book.id, vector, top_k=5)
                print(f"\n  检索抽查：{args.check!r}  (top {len(hits)})")
                for hit in hits:
                    text = str(hit["text"]).replace("\n", " ")[:70]
                    print(
                        f"    [{hit['score']:.3f}] 第{hit['chapter_number']}章 "
                        f"{hit['chapter_title'][:14]} | {text}"
                    )

        print("\n" + "=" * 68)
        print("全部完成。回到网页即可直接续写。")
        print("=" * 68)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
