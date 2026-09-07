"""
Dev-заглушки персонажей для отладки planner.py без действующих ESI-токенов.

Контекст (см. roadmap.md, раздел 0, пункт 4): на момент разработки нет
действующих ESI client_id/secret и токенов персонажей. Это нормально —
но заглушка не должна жить внутри боевого auth-эндпоинта, как это было
в main.py v1 (там /api/auth/callback просто вставлял мок-персонажей
в БД вместо реального обмена OAuth-кода на токен).

Этот скрипт:
  - запускается ТОЛЬКО вручную и ТОЛЬКО при ENV=dev (см. infra/config.py,
    появится вместе с реальной БД в Фазе 1);
  - пишет тестовых персонажей в ту же таблицу characters, которую в Фазе 3
    будет заполнять реальный ESI SSO callback — poэтому domain.planner
    не должен и не будет знать, откуда взялся персонаж;
  - никогда не импортируется и не вызывается из api/ (это гарантирует,
    что заглушка физически не может утечь в прод).

Использование (после появления infra/db в Фазе 1):
    python -m scripts.seed_dev_characters
"""

from __future__ import annotations

# TODO(Фаза 1): заменить на реальный доступ к БД, когда появится infra/db.
# Пока — только описание формы данных, которые ожидает domain.planner.CharacterSlot.

DEV_CHARACTERS = [
    # (character_id, name, ccu_level, ic_level)
    (90001, "Dev Factory Chief 1", 5, 5),
    (90002, "Dev Factory Chief 2", 5, 5),
    *[(90004 + i, f"Dev Miner {i + 1}", 4, 5) for i in range(10)],
]


def seed() -> None:
    """
    Записать DEV_CHARACTERS в БД (dev-окружение).

    TODO(Фаза 1):
      1. проверить infra.config.ENV == "dev", иначе — явный RuntimeError
         ("seed_dev_characters запрещён вне dev-окружения");
      2. записать в таблицу characters через тот же слой доступа к БД,
         которым будет пользоваться будущий ESI SSO callback.
    """
    raise NotImplementedError("TODO(Фаза 1): реализовать после появления infra/db")


if __name__ == "__main__":
    seed()
