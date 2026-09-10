"""
Сборщик: уровни PI-скиллов персонажей из ESI.

ЗАЧЕМ. После первого входа через SSO у персонажа CCU/IC = 0 (скиллы
callback не знает). Этот сборщик по расписанию подтягивает реальные
уровни, и планировщик начинает считать по ним.

Нужны только два скилла — Command Center Upgrades (ёмкость командного
центра) и Interplanetary Consolidation (число планет на персонажа).
Их type_id лежат в data/pi_reference.json → recommended_skills._type_ids
(проверены по everef.net / db.evetools.org).

Эндпоинт: GET /characters/{id}/skills/  (scope esi-skills.read_skills.v1)
Запросы идут через scripts/esi_client.py с access-токеном из БД.

Запуск вручную:
    python -m scripts.sync_character_skills

По расписанию — в scripts/scheduler.py, ПОСЛЕ refresh_tokens.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _skill_ids() -> tuple[int, int]:
    """(CCU, IC) type_id из data/pi_reference.json — единый источник."""
    from domain.capacity import _reference

    ids = _reference()["recommended_skills"]["_type_ids"]
    return int(ids["command_center_upgrades"]), int(ids["interplanetary_consolidation"])


SKILL_COMMAND_CENTER_UPGRADES, SKILL_INTERPLANETARY_CONSOLIDATION = _skill_ids()


def _levels_from_skills(payload: dict) -> tuple[int, int]:
    """(ccu_level, ic_level) из ответа /skills/. Нет скилла — уровень 0."""
    by_id = {
        int(s.get("skill_id")): int(s.get("active_skill_level", 0))
        for s in payload.get("skills", [])
        if s.get("skill_id") is not None
    }
    return (
        by_id.get(SKILL_COMMAND_CENTER_UPGRADES, 0),
        by_id.get(SKILL_INTERPLANETARY_CONSOLIDATION, 0),
    )


def sync_one(client, session, character) -> str:
    """
    Обновить скиллы одного персонажа. Возвращает 'ok', 'skipped' или 'error'.
    """
    from infra.credentials import get_access_token
    from scripts.esi_client import EsiError, EsiRateLimited

    token = get_access_token(session, character.character_id)
    if token is None:
        return "skipped"

    try:
        response = client.get(
            f"/characters/{character.character_id}/skills/", token=token
        )
    except EsiRateLimited:
        raise
    except EsiError as exc:
        print(f"  · {character.name}: не удалось получить скиллы ({exc})")
        return "error"

    if response.from_cache:
        return "ok"  # 304 — уровни не менялись

    ccu, ic = _levels_from_skills(response.data or {})
    if (character.command_center_upgrades_level, character.interplanetary_consolidation_level) != (ccu, ic):
        character.command_center_upgrades_level = ccu
        character.interplanetary_consolidation_level = ic
        print(f"  · {character.name}: CCU {ccu}, IC {ic}")
    return "ok"


def main(client=None) -> int:
    from sqlalchemy import select
    from sqlalchemy.exc import OperationalError

    from infra.db import session_scope
    from infra.models import Character
    from scripts.esi_client import EsiClient, EsiRateLimited

    client = client or EsiClient()
    errors = 0
    try:
        with session_scope() as session:
            characters = session.scalars(
                select(Character).where(Character.source == "esi")
            ).all()
            if not characters:
                print("Персонажей из ESI нет — синхронизировать нечего.")
                return 0
            print(f"Синхронизация скиллов: {len(characters)} персонажей")
            for character in characters:
                try:
                    if sync_one(client, session, character) == "error":
                        errors += 1
                except EsiRateLimited as exc:
                    print(f"ESI просит подождать: {exc}")
                    return 1
    except OperationalError:
        print("Нет таблиц БД — выполните `python -m alembic upgrade head`.")
        return 0

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
