"""
Dev-заглушки персонажей для отладки planner.py без действующих ESI-токенов.

Контекст (см. roadmap.md, раздел 0, пункт 4): действующих ESI client_id/
secret пока нет. Это нормально, но заглушка не должна жить внутри боевого
auth-эндпоинта, как в main.py v1 (там /api/auth/callback вставлял мок-
персонажей вместо реального обмена OAuth-кода на токен).

Этот скрипт:
  - запускается ТОЛЬКО вручную и ТОЛЬКО при PI_ENV=dev;
  - пишет персонажей в ту же таблицу characters, которую в Фазе 3 будет
    заполнять реальный ESI SSO callback (там source="esi"), — поэтому
    domain.planner не знает и не должен знать, откуда взялся персонаж;
  - никогда не импортируется из api/ (заглушка физически не может утечь в прод).

Использование:
    python -m scripts.extract_schematics --write   # один раз, если ещё нет
    python -m alembic upgrade head                  # создать таблицы
    python -m scripts.seed_dev_characters           # наполнить dev-персонажами
"""

from __future__ import annotations

# Уровни скиллов взяты из раздела "Skill Recommendations" источника шаблонов
# (https://github.com/DalShooth/EVE_PI_Templates):
#   Miner:   Command Center Upgrades V, Interplanetary Consolidation IV
#   Factory: Command Center Upgrades V, Interplanetary Consolidation V
#
# Важно для проверки логики: Command Center Upgrades V — обязательное условие
# для варианта "2 шаблона на одну планету". Поэтому в наборе намеренно есть
# и персонажи с CCU 4 — чтобы planner.py гарантированно проходил ветку, где
# доступны только одиночные шаблоны, а не только счастливый путь.

DEV_CHARACTERS = [
    # (character_id, name, ccu_level, ic_level, комментарий)
    (90001, "Dev Factory Chief 1", 5, 5, "может ставить 2 шаблона на планету"),
    (90002, "Dev Factory Chief 2", 5, 5, "может ставить 2 шаблона на планету"),
    (90003, "Dev Miner Full",      5, 4, "рекомендованная прокачка майнера"),
    (90004, "Dev Miner Partial",   4, 4, "CCU IV — только одиночные шаблоны"),
    (90005, "Dev Miner Rookie",    3, 2, "низкая прокачка: добывающий шаблон не влезет"),
    *[
        (90010 + i, f"Dev Miner {i + 1}", 5, 4, "рекомендованная прокачка майнера")
        for i in range(8)
    ],
]

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


def seed() -> int:
    """
    Записать DEV_CHARACTERS в таблицу characters (только PI_ENV=dev).

    Идемпотентно: повторный запуск обновляет существующие строки по
    character_id, не плодит дубли. Реальных персонажей (source != "dev")
    не трогает.

    Возвращает число записанных строк.
    """
    from infra import config
    from infra.db import session_scope
    from infra.models import Character

    if not config.IS_DEV:
        raise RuntimeError(
            "seed_dev_characters запрещён вне dev-окружения: "
            f"PI_ENV={config.ENV!r}. Тестовых персонажей в проде быть не должно."
        )

    with session_scope() as session:
        for char_id, name, ccu, ic, _comment in DEV_CHARACTERS:
            existing = session.get(Character, char_id)
            if existing is not None and existing.source != DEV_SOURCE:
                # Настоящего персонажа с тем же id (маловероятно —
                # dev-id начинаются с 900xx) не перезаписываем.
                continue
            if existing is None:
                session.add(Character(
                    character_id=char_id, name=name,
                    command_center_upgrades_level=ccu,
                    interplanetary_consolidation_level=ic,
                    source=DEV_SOURCE,
                ))
            else:
                existing.name = name
                existing.command_center_upgrades_level = ccu
                existing.interplanetary_consolidation_level = ic

    return len(DEV_CHARACTERS)


if __name__ == "__main__":
    written = seed()
    print(f"Записано dev-персонажей: {written}")
