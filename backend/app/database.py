"""数据库连接与会话管理（SQLite + SQLAlchemy 2.0）。"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import env


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


engine = create_engine(
    env.sqlite_url,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    """开启外键约束 + WAL 模式，提升本地并发读写体验。"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：每个请求一个数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表（幂等）+ 轻量字段迁移。"""
    from . import models  # noqa: F401  确保模型已注册到 Base.metadata

    Base.metadata.create_all(bind=engine)
    _ensure_columns()


def _ensure_columns() -> None:
    """SQLite 轻量迁移：给已存在的表补上后来新增的列（不引入 Alembic）。"""
    from sqlalchemy import inspect, text

    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        wanted: dict[str, dict[str, str]] = {
            "generations": {"title": "VARCHAR(500) DEFAULT ''"},
        }
        with engine.begin() as conn:
            for table, columns in wanted.items():
                if table not in tables:
                    continue
                existing = {col["name"] for col in inspector.get_columns(table)}
                for name, ddl in columns.items():
                    if name not in existing:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
    except Exception:  # noqa: BLE001  迁移失败不应阻止启动
        import logging

        logging.getLogger(__name__).warning("字段迁移失败（可忽略）", exc_info=True)
