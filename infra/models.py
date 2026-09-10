"""
ORM-модели.

Таблица `characters` — единственная на этапе подготовки Фазы 3. В неё
пишут два источника:
  - `scripts/seed_dev_characters.py` (source="dev", только ENV=dev);
  - будущий ESI SSO callback (source="esi").
`domain/planner.py` читает её через `scripts.seed_dev_characters.load_characters`
и о происхождении данных не знает — поля одинаковые.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


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
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover - для отладки
        return (
            f"Character(id={self.character_id}, name={self.name!r}, "
            f"ccu={self.command_center_upgrades_level}, "
            f"ic={self.interplanetary_consolidation_level}, source={self.source!r})"
        )
