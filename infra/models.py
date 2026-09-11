"""
ORM-модели.

  - `characters` — персонажи. Пишут два источника: `seed_dev_characters`
    (source="dev", только PI_ENV=dev) и ESI SSO callback (source="esi").
    `domain/planner.py` о происхождении не знает.
  - `plans` — сохранённые планы (замена json-файлов в data/plans/).
    Публичный интерфейс — `domain/plan_storage.py`.

Колонка `account_id` есть в обеих таблицах — разделяет данные разных
пользователей приложения (`api/session.py`). Была заведена под
multi-tenant Фазы 3 заранее (чтобы не делать ALTER позже) и до
11.09.2026 оставалась незаполненной ни одним запросом — из-за этого
разные вошедшие через SSO пользователи видели персонажей, колонии и
планы друг друга. Теперь заполняется в `api/blueprints/auth.py::_store()`
(персонаж) и `domain/plan_storage.py::save()` (план), и читается везде,
где отдаются эти данные.
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

    # Владелец записи — account_id визита, вошедшего через SSO этим
    # персонажем (api/session.py, api/blueprints/auth.py::_store()).
    # NULL — персонаж заведён до 11.09.2026 (когда колонка была
    # зарезервирована, но ещё не читалась ни одним запросом) и пока не
    # входил заново: /api/characters честно его не покажет никому, пока
    # не перелогинится.
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
    # Система и номер планеты — чтобы фронтенд сопоставил колонию со
    # строкой расчётного плана (там ключ — система + номер планеты).
    system_name: Mapped[str] = mapped_column(String(64), default="")
    planet_index: Mapped[int] = mapped_column(Integer, default=0)
    planet_type: Mapped[str] = mapped_column(String(32))    # barren, temperate, …
    upgrade_level: Mapped[int] = mapped_column(Integer)     # уровень командного центра
    num_pins: Mapped[int] = mapped_column(Integer)

    # Поимённый состав колонии — [{"kind": "extractor_control_unit", "count": 2}, ...],
    # тот же формат, что и structures_detail расчётного плана (domain/planner.py),
    # чтобы фронтенд рисовал одни и те же иконки structOrb() для обоих случаев.
    # Строится из реального type_id каждого пина (scripts/sync_colony_status.py),
    # а не берётся из шаблона — колония в игре не обязана совпадать с шаблоном.
    structures: Mapped[list] = mapped_column(JSON, default=list)

    # Детали по каждому пину — то же, что и structures, только поштучно
    # и с реальным состоянием (не просто «есть 8 фабрик», а какая что
    # производит и простаивает ли). Строится из полей ESI, которые раньше
    # не читались (schematic_id, last_cycle_start, extractor_details,
    # contents), см. scripts/sync_colony_status.py::pin_detail().
    pins: Mapped[list] = mapped_column(JSON, default=list)

    # Ближайшее время окончания программы экстрактора на этой планете
    # (soonest expiry_time среди extractor-пинов). None — экстракторов нет
    # или программы не запущены.
    nearest_expiry: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    # Загрузка CPU/Power командного центра — считается ИЗ НАСТОЯЩИХ данных:
    # реальный состав структур (structures), реальное число линков и голов
    # экстрактора (из самого ответа ESI), реальный upgrade_level (тоже ESI),
    # и радиус планеты — из data/planet_industry.csv (см. domain/planets.py),
    # если планета в нём есть: файл покрывает не весь New Eden, а только
    # загруженный регион. Раньше это поле пустовало для ЛЮБОЙ реальной
    # колонии, хотя радиус нужен был только у ESI (её действительно нет) —
    # у CSV он есть. None — планета не найдена в CSV или структура с
    # неизвестным type_id (см. scripts/sync_colony_status.py::real_colony_load()).
    cpu_percent: Mapped[float | None] = mapped_column(nullable=True)
    pg_percent: Mapped[float | None] = mapped_column(nullable=True)
    # Абсолютные числа (tf/MW) рядом с процентами — панель колонии в игре
    # показывает оба (см. скриншот пользователя 11.09.2026), не только %.
    cpu_used: Mapped[float | None] = mapped_column(nullable=True)
    cpu_capacity: Mapped[float | None] = mapped_column(nullable=True)
    pg_used: Mapped[float | None] = mapped_column(nullable=True)
    pg_capacity: Mapped[float | None] = mapped_column(nullable=True)

    synced_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, onupdate=_utcnow
    )


class ExtractionSample(Base):
    """
    Один снимок расчётной скорости добычи ОДНОГО экстрактора — копится
    при каждой синхронизации (scripts/sync_colony_status.py), а не
    перезаписывается: история нужна для честного «Avg. Per hour» и
    графика в панели колонии (Фаза 7, roadmap.md — «порог просадки не
    выдумывать, вывести из истории самой колонии»). ESI сама историю не
    хранит, отдаёт только текущий снимок (qty_per_cycle/cycle_time) —
    копим сами, с нуля, начиная с 11.09.2026.

    pin_id — потому что на одной планете может быть несколько
    экстракторов (разное сырьё), считать их вместе было бы смешиванием
    двух независимых ресурсов.
    """

    __tablename__ = "extraction_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, index=True)
    planet_id: Mapped[int] = mapped_column(Integer, index=True)
    pin_id: Mapped[int] = mapped_column(Integer)

    product_type_id: Mapped[int | None] = mapped_column(nullable=True)
    qty_per_cycle: Mapped[int | None] = mapped_column(nullable=True)
    cycle_seconds: Mapped[int | None] = mapped_column(nullable=True)

    sampled_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow, index=True)
