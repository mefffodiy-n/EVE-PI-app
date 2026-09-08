"""
Dev-заглушки персонажей для отладки planner.py без действующих ESI-токенов.

Контекст (см. roadmap.md, раздел 0, пункт 4): действующих ESI client_id/
secret пока нет. Это нормально, но заглушка не должна жить внутри боевого
auth-эндпоинта, как в main.py v1 (там /api/auth/callback вставлял мок-
персонажей вместо реального обмена OAuth-кода на токен).

Этот скрипт:
  - запускается ТОЛЬКО вручную и ТОЛЬКО при ENV=dev;
  - пишет персонажей в ту же таблицу characters, которую в Фазе 3 будет
    заполнять реальный ESI SSO callback — поэтому domain.planner не знает
    и не должен знать, откуда взялся персонаж;
  - никогда не импортируется из api/ (заглушка физически не может утечь в прод).

Использование (после появления infra/db в Фазе 1):
    python -m scripts.seed_dev_characters
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


def load_characters() -> list:
    """
    Вернуть персонажей для планировщика.

    Фаза 1: читает dev-заглушки, если разрешено окружением.
    Фаза 3: то же место будет читать реальных персонажей из БД,
    заполненных sync_character_skills. Планировщик разницы не заметит —
    поля те же.

    Возвращает пустой список вне dev-окружения: молча подставлять
    тестовых персонажей в проде нельзя.
    """
    import os

    from domain.planner import CharacterSlot

    if os.environ.get("PI_ENV", "dev").lower() != "dev":
        return []

    return [
        CharacterSlot(
            character_id=char_id,
            name=name,
            command_center_upgrades_level=ccu,
            interplanetary_consolidation_level=ic,
        )
        for char_id, name, ccu, ic, _ in DEV_CHARACTERS
    ]


def seed() -> None:
    """
    Записать DEV_CHARACTERS в БД (dev-окружение).

    TODO(Фаза 1):
      1. проверить infra.config.ENV == "dev", иначе — RuntimeError
         ("seed_dev_characters запрещён вне dev-окружения");
      2. записать в таблицу characters через тот же слой доступа к БД,
         которым будет пользоваться будущий ESI SSO callback.
    """
    raise NotImplementedError("TODO(Фаза 1): реализовать после появления infra/db")


if __name__ == "__main__":
    seed()
