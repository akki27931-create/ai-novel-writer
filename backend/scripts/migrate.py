"""数据库迁移脚本（幂等，可反复执行）。

做三件事：
1. 建新表          ：chapter_states / character_states / providers
2. 给老表补新列    ：books.kind、book_analyses.chapter_plan、chapters.state_extracted
3. 数据回填        ：
   - 老书的 kind 统一置为 'imported'
   - 把 BookAnalysis.characters 灌进 character_states（作为状态快照的起点）
   - 给每本书建一个默认 provider（沿用旧的 base_url / api_key / 模型名），
     这样不配置多网关也能照常工作

用法（在 backend/ 目录下）：
    .venv\\Scripts\\python.exe scripts\\migrate.py
    .venv\\Scripts\\python.exe scripts\\migrate.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import env  # noqa: E402
from app.database import SessionLocal, engine, init_db  # noqa: E402
from app.models import AppSetting, Book, BookAnalysis, CharacterState, Provider  # noqa: E402
from app.settings_store import create_default_provider  # noqa: E402
from sqlalchemy import inspect  # noqa: E402

NEW_TABLES = ("chapter_states", "character_states", "providers")
NEW_COLUMNS = {
    "books": {"kind": "VARCHAR(20) DEFAULT 'imported'"},
    "book_analyses": {"chapter_plan": "JSON DEFAULT '[]'"},
    "chapters": {"state_extracted": "BOOLEAN DEFAULT 0"},
}


def report_schema() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    print("  表：")
    for name in sorted(tables):
        mark = "新建" if name in NEW_TABLES else "已有"
        print(f"    [{mark}] {name}")
    print("  列：")
    for table, columns in NEW_COLUMNS.items():
        if table not in tables:
            print(f"    [{table}] 表不存在，跳过")
            continue
        existing = {col["name"] for col in inspector.get_columns(table)}
        for name in columns:
            mark = "已有" if name in existing else "待补"
            print(f"    [{mark}] {table}.{name}")


def tables_ready() -> bool:
    """老库迁移的前提：核心表已存在。全新安装时不需要回填。"""
    existing = set(inspect(engine).get_table_names())
    return {"books", "book_analyses", "app_settings"} <= existing


def backfill_book_kind(db, dry_run: bool) -> int:
    count = 0
    for book in db.query(Book).all():
        if not book.kind:
            book.kind = "imported"
            count += 1
    if count and not dry_run:
        db.commit()
    return count


def backfill_character_states(db, dry_run: bool) -> int:
    """把拆书得到的人物卡灌成状态快照的起点。

    注意：拆书的人物卡是「全书视角的静态快照」，不是任何单章的状态，
    所以 location / doing / goal 先留空，等 AI 抽取到对应章节后自然填上。
    """
    created = 0
    known = {
        (row.book_id, row.name)
        for row in db.query(CharacterState.book_id, CharacterState.name).all()
    }
    for analysis in db.query(BookAnalysis).all():
        for item in analysis.characters or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or (analysis.book_id, name) in known:
                continue
            known.add((analysis.book_id, name))
            created += 1
            if dry_run:
                continue
            db.add(
                CharacterState(
                    book_id=analysis.book_id,
                    name=name,
                    importance=str(item.get("importance") or "配角"),
                    identity=str(item.get("identity") or ""),
                    personality=str(item.get("personality") or ""),
                    relations=str(item.get("relations") or ""),
                    status=str(item.get("status") or ""),
                    items=list(item.get("items") or []),
                    knows=list(item.get("knows") or []),
                    last_seen_chapter=int(item.get("first_chapter") or 0),
                )
            )
    if created and not dry_run:
        db.commit()
    return created


def backfill_default_provider(db, dry_run: bool) -> int:
    """用环境变量 / 旧配置建一个默认网关，保证升级后开箱即用。"""
    if db.query(Provider).count() > 0:
        return 0
    if dry_run:
        return 1  # 只告诉用户「会建一个」，不写库
    created = create_default_provider(db)
    return 1 if created is not None else 0


def cleanup_legacy_settings(db, dry_run: bool) -> int:
    """旧版把 api_key 存在 app_settings 里，迁移后由 providers 表接管。

    保留 base_url / text_model / reasoning_model 作为「无 provider 时的兜底」，
    只清掉明文 api_key，避免同时存在两份真相。
    """
    row = db.get(AppSetting, "api_key")
    if row is None or not row.value:
        return 0
    if not dry_run:
        db.delete(row)
        db.commit()
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 网文续写 —— 数据库迁移")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要做的改动，不写库")
    args = parser.parse_args()

    print("=" * 70)
    print("数据库迁移")
    print(f"数据目录：{env.data_path}")
    print(f"数据库  ：{env.data_path / 'app.db'}")
    print("=" * 70)

    print("\n[1] 迁移前结构")
    report_schema()

    print("\n[2] 建表 + 补列")
    if args.dry_run:
        existing = set(inspect(engine).get_table_names())
        for name in NEW_TABLES:
            print(f"  [待建] {name}" if name not in existing else f"  [已有] {name}")
        for table, columns in NEW_COLUMNS.items():
            if table in existing:
                have = {c["name"] for c in inspect(engine).get_columns(table)}
                for col in columns:
                    print(f"  [{'已有' if col in have else '待补'}] {table}.{col}")
    else:
        init_db()
        print("  完成（create_all 幂等，补列失败不影响启动）")

    print("\n[3] 回填数据")
    if not tables_ready():
        # dry-run 下不会建表，全新安装时这里就是空的，不必也别去查
        print("  数据库还是空的（全新安装或尚未 init_db），没有需要回填的老数据。")
    else:
        db = SessionLocal()
        try:
            kind_fixed = backfill_book_kind(db, args.dry_run)
            print(f"  books.kind 回填：{kind_fixed} 本")

            chars = backfill_character_states(db, args.dry_run)
            print(f"  character_states 初始化：{chars} 条人物快照")

            created = backfill_default_provider(db, args.dry_run)
            print(f"  providers 默认网关：新建 {created} 个")

            removed = cleanup_legacy_settings(db, args.dry_run)
            print(f"  清理旧版明文 api_key：{removed} 条")
        finally:
            db.close()

    print("\n[4] 迁移后结构")
    report_schema()

    print("\n" + "=" * 70)
    if args.dry_run:
        print("dry-run 结束，未写入任何改动。去掉 --dry-run 才会真正执行。")
    else:
        print("迁移完成。")
        print("最后一步：老书的历史章节还没有剧情状态，需要手动补建（会消耗 API 额度，走廉价模型档）：")
        print("  · 网页：「续写」页 → 右侧「剧情状态」→ 点「补建状态」")
        print("  · 或接口：POST /api/books/{id}/state/extract")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
