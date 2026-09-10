"""
ORM-модели.

  - `characters` — персонажи. Пишут два источника: `seed_dev_characters`
    (source="dev", только PI_ENV=dev) и будущий ESI SSO callback
    (source="esi"). `domain/planner.py` о происхождении не знает.
  - `plans` — сохранённые планы (замена json-файлов в data/plans/).
    Публичный интерфейс — `domain/plan_storage.py`.

Колонка `account_id` есть в обеих таблицах под multi-tenant Фазы 3;
пока всегда NULL, но заведена сразу, чтобы не делать ALTER позже.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base, UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Character(Base):
    __tablename__ = "characters"

    # ESI character id — натуральный ключ. Реальный OAuth-flow пишет сюда
    # же, поэтому dev-заглушка и настоящий персонаж неотличимы для расчёта.
    character_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(255))

    command_center_upgrades_level: Mapped[int] = mapped_column(Integer)
    interplanetary_consolidation_level: Mapped[int] = mapped_column(Integer)

    # "dev" | "esi": чтобы seed не трогал реальных персонажей и чтобы
    # диагностика могла отличить окружение.
    source: Mapped[str] = mapped_column(String(16), default="esi")

    # Владелец записи. Multi-tenant — Фаза 3; сейчас всегда NULL, но
    # колонка есть, чтобы не делать миграцию с ALTER позже.
    account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover - для отладки
        return (
            f"Character(id={self.character_id}, name={self.name!r}, "
            f"ccu={self.command_center_upgrades_level}, "
            f"ic={self.interplanetary_consolidation_level}, source={self.source!r})"
        )


class Plan(Base):
    __tablename__ = "plans"

    # uuid4().hex[:12] — генерирует plan_storage, не БД (id виден в URL,
    # автоинкремент подсказывал бы число сохранённых планов).
    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))

    # ISO-строка UTC с точностью до секунды. Строкой, а не DateTime:
    # фронтенд получает её как есть, менять формат нельзя. Сортировка
    # по ISO-строке совпадает с хронологической.
    created_at: Mapped[str] = mapped_column(String(32))

    account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    request: Mapped[dict] = mapped_column(JSON, default=dict)
    rows: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    assumptions: Mapped[list] = mapped_column(JSON, default=list)


class Credential(Base):
    """
    Токены ESI одного персонажа. Отдельно от `characters` (roadmap):
    персонаж — это данные для расчёта, токен — секрет с другим жизненным
    циклом (обновляется фоновым refresh_tokens, шифруется at rest).
    """

    __tablename__ = "credentials"

    character_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)

    # Зашифрованы infra.crypto (Fernet). В открытом виде в БД не лежат.
    access_token: Mapped[str] = mapped_column(String)
    refresh_token: Mapped[str] = mapped_column(String)

    access_expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    scopes: Mapped[list] = mapped_column(JSON, default=list)

    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, onupdate=_utcnow
    )


class Colony(Base):
    """
    Реальная колония персонажа в игре — снимок из ESI
    (`scripts/sync_colony_status.py`). Отдельно от расчётного плана:
    план — «что стоит построить», колония — «что построено».
    """

    __tablename__ = "colonies"

    character_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    planet_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)

    planet_name: Mapped[str] = mapped_column(String(128))   # напр. «Jita IV»
    planet_type: Mapped[str] = mapped_column(String(32))    # barren, temperate, …
    upgrade_level: Mapped[int] = mapped_column(Integer)     # уровень командного центра
    num_pins: Mapped[int] = mapped_column(Integer)

    # Ближайшее время окончания программы экстрактора на этой планете
    # (soonest expiry_time среди extractor-пинов). None — экстракторов нет
    # или программы не запущены.
    nearest_expiry: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    synced_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, onupdate=_utcnow
    )
