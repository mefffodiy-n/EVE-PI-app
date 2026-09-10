"""
Сборщик: реальный статус колоний персонажа из ESI.

ЗАЧЕМ. В интерфейсе состояние цикла экстрактора показано как «неизвестно /
появится после подключения к игре». Этот сборщик по расписанию снимает
список колоний и ближайшее время окончания программы экстрактора на
каждой планете. Именно сюда v1 писал `Math.random()` — теперь значение
настоящее либо честно отсутствует.

Эндпоинты:
  GET /characters/{id}/planets/              список колоний (authed)
  GET /characters/{id}/planets/{planet_id}/  пины, экстракторы (authed)
  GET /universe/planets/{planet_id}/         имя планеты (public)
scope: esi-planets.manage_planets.v1

Колонии — снимок, не расчётный план: план говорит «что стоит построить»,
здесь — «что построено». Совмещает их фронтенд по системе и планете.

Запуск вручную:
    python -m scripts.sync_colony_status

По расписанию — в scripts/scheduler.py, ПОСЛЕ refresh_tokens.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def nearest_expiry(planet_detail: dict) -> datetime | None:
    """Самое раннее expiry_time среди пинов планеты (экстракторы)."""
    times = [
        _parse_iso(pin.get("expiry_time"))
        for pin in planet_detail.get("pins", [])
    ]
    times = [t for t in times if t is not None]
    return min(times) if times else None


def _planet_name(client, planet_id: int) -> str:
    from scripts.esi_client import EsiError

    try:
        response = client.get(f"/universe/planets/{planet_id}/")
        if not response.from_cache and isinstance(response.data, dict):
            return str(response.data.get("name") or f"планета {planet_id}")
    except EsiError:
        pass
    return f"планета {planet_id}"


def sync_one(client, session, character) -> str:
    """
    Обновить колонии одного персонажа. 'ok' | 'skipped' | 'error'.
    """
    from sqlalchemy import select

    from infra.credentials import get_access_token
    from infra.models import Colony
    from scripts.esi_client import EsiError, EsiRateLimited

    token = get_access_token(session, character.character_id)
    if token is None:
        return "skipped"

    cid = character.character_id
    try:
        listing = client.get(f"/characters/{cid}/planets/", token=token)
        planets = [] if listing.from_cache else (listing.data or [])

        seen: set[int] = set()
        for entry in planets:
            planet_id = int(entry["planet_id"])
            seen.add(planet_id)
            detail = client.get(f"/characters/{cid}/planets/{planet_id}/", token=token)
            expiry = None if detail.from_cache else nearest_expiry(detail.data or {})

            row = session.get(Colony, (cid, planet_id))
            fields = dict(
                planet_name=_planet_name(client, planet_id),
                planet_type=str(entry.get("planet_type", "")),
                upgrade_level=int(entry.get("upgrade_level", 0)),
                num_pins=int(entry.get("num_pins", 0)),
            )
            if not detail.from_cache:
                fields["nearest_expiry"] = expiry
            if row is None:
                session.add(Colony(character_id=cid, planet_id=planet_id, **fields))
            else:
                for key, value in fields.items():
                    setattr(row, key, value)

        # Колонии, которых больше нет в игре, убираем.
        stale = session.scalars(
            select(Colony).where(Colony.character_id == cid, Colony.planet_id.not_in(seen or {-1}))
        ).all()
        for row in stale:
            session.delete(row)

    except EsiRateLimited:
        raise
    except EsiError as exc:
        print(f"  · {character.name}: не удалось получить колонии ({exc})")
        return "error"

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
            print(f"Синхронизация колоний: {len(characters)} персонажей")
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
