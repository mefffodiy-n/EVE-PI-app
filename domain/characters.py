"""
Персонажи для планировщика: чтение из БД (`load_characters`).

Лежало в scripts/seed_dev_characters.py, из-за чего api/ импортировал
скрипт разработки ради боевой функции (внешняя рецензия, 28.09.2026):
направление зависимостей было обратным. Скрипт теперь только пишет
dev-заглушки, а читают все отсюда.
"""

from __future__ import annotations

DEV_SOURCE = "dev"


def load_characters(account_id: str | None = None) -> list:
    """
    Вернуть персонажей для планировщика.

    Источник данных не важен планировщику: dev-заглушки (source "dev") и
    реальные из ESI (source "esi") лежат в одной таблице с одинаковыми
    полями. В проде без ESI и без seed таблица пуста — вернём пустой
    список, и слой выше честно сообщит о нехватке персонажей.

    Вне dev-окружения:
      - строки с source="dev" ИГНОРИРУЮТСЯ, даже если попали в БД по
        ошибке конфигурации — правило проекта: тестовых персонажей в
        проде быть не должно;
      - строки с source="esi" отдаются, только если account_id совпадает
        (найдено 11.09.2026: без этого разные вошедшие через SSO
        пользователи видели персонажей друг друга). account_id=None
        (визит ещё не входил через SSO) — честно пустой список, а не
        «все подряд».

    В dev-окружении account_id игнорируется целиком: локальная среда
    разработки однопользовательская, сессии там не имеют смысла, и все
    существующие вызовы (скрипты, тесты, scripts/diagnose.py) продолжают
    видеть всё, как и раньше.

    Отсутствие БД/таблицы (свежий клон без `alembic upgrade`) — тоже
    пустой список, а не исключение: страница должна открыться.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import OperationalError

    from domain.planner import CharacterSlot
    from infra import config
    from infra.db import session_scope
    from infra.models import Character

    query = select(Character).order_by(Character.character_id)
    if not config.IS_DEV:
        query = query.where(Character.source != DEV_SOURCE)
        if account_id is None:
            return []
        query = query.where(Character.account_id == account_id)

    try:
        with session_scope() as session:
            rows = session.scalars(query).all()
    except OperationalError:
        return []

    return [
        CharacterSlot(
            character_id=row.character_id,
            name=row.name,
            command_center_upgrades_level=row.command_center_upgrades_level,
            interplanetary_consolidation_level=row.interplanetary_consolidation_level,
        )
        for row in rows
    ]
