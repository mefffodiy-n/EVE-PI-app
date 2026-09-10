"""
Доступ к БД: движок, сессии, декларативная база.

Движок создаётся лениво и один раз на процесс. Тесты вызывают
`reset_engine(url)`, чтобы переключиться на временный SQLite.

Схему создаёт Alembic (`alembic upgrade head`). `create_all()` —
только для тестов и самого первого локального запуска.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from infra import config


class Base(DeclarativeBase):
    """Общая база для всех ORM-моделей (infra/models.py)."""


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _build_engine(url: str) -> Engine:
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        # Flask обслуживает запросы в нескольких потоках; SQLite-соединение
        # иначе привязано к потоку, в котором создано.
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


def engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _build_engine(config.DATABASE_URL)
    return _engine


def _factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=engine(), expire_on_commit=False, future=True
        )
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Сессия с коммитом на выходе и откатом при исключении."""
    session = _factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine(url: str | None = None) -> None:
    """
    Сбросить закэшированный движок. Для тестов: перед этим подменяют
    `infra.config.DATABASE_URL` (или передают url сюда).
    """
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
    if url is not None:
        config.DATABASE_URL = url


def create_all() -> None:
    """Создать все таблицы без миграций (тесты, первый локальный запуск)."""
    from infra import models  # noqa: F401 — регистрирует таблицы в Base.metadata

    Base.metadata.create_all(engine())
