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

Заодно снимается поимённый состав колонии (`structures`, тот же формат,
что и `structures_detail` расчётного плана) — из type_id каждого пина,
а не из шаблона застройки: колония в игре не обязана совпадать ни с
одним из 68 game-шаблонов. Нужно, чтобы «Мои колонии в игре» рисовались
той же полосой иконок structOrb(), что и колонии плана на дашборде.

Запуск вручную:
    python -m scripts.sync_colony_status

По расписанию — в scripts/scheduler.py, ПОСЛЕ refresh_tokens.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TYPE_IDS_PATH = ROOT / "data" / "type_ids.json"

_ROMAN = {"I": 1, "V": 5, "X": 10}


def _roman_to_int(text: str) -> int:
    """«IV» → 4. Планет в системе EVE не больше ~13, хватает I..XIII."""
    total = 0
    prev = 0
    for ch in reversed(text.strip().upper()):
        value = _ROMAN.get(ch, 0)
        if value == 0:
            return 0
        total += -value if value < prev else value
        prev = max(prev, value)
    return total


def planet_index(planet_name: str, system_name: str) -> int:
    """
    Номер планеты из её имени: «Tanoo IV» при системе «Tanoo» → 4.
    Совпадает с колонкой Planet в planet_industry.csv, по которой
    фронтенд сопоставляет колонию со строкой плана.
    """
    if system_name and planet_name.startswith(system_name):
        suffix = planet_name[len(system_name):].strip()
    else:
        suffix = planet_name.rsplit(" ", 1)[-1]
    return _roman_to_int(suffix)


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# Отображаемое имя структуры в игре -> «kind», тот же словарь значений,
# что у structures_detail расчётного плана (domain/planner.py).
#
# ОШИБКА, КОТОРУЮ ЭТО ИСПРАВЛЯЕТ (11.09.2026, живые данные). Раньше
# считалось, что только командный центр специфичен для типа планеты, а
# у launchpad/storage/extractor control unit/фабрик один type_id на всех.
# Неверно: «Storm Basic Industry Facility» и «Temperate Basic Industry
# Facility» — разные предметы с разными type_id, как и все остальные
# структуры. Реальная колония на любой планете кроме Barren/Temperate
# показывала только командный центр — остальные пины (те же самые
# постройки!) не распознавались. Полная матрица «тип планеты × структура»
# добыта через `scripts/resolve_pi_structure_type_ids.py` (ESI
# `/universe/ids/`) и лежит в data/type_ids.json как «<Тип> <Имя>»,
# в тех же именах, что показывает сама игра.
_STRUCTURE_SUFFIX_TO_KIND = {
    " Launchpad": "launchpad",
    " Storage Facility": "storage_facility",
    " Extractor Control Unit": "extractor_control_unit",
    " Basic Industry Facility": "basic_industry_facility",
    " Advanced Industry Facility": "advanced_industry_facility",
    # В игре — «…High-Tech Production Plant», не «…Industry Facility»,
    # и существует только для Barren/Temperate: P4 ставится только на
    # них (правило игры, см. CLAUDE.md), другим планетам эта структура
    # просто не нужна и не существует.
    " High-Tech Production Plant": "high_tech_industry_facility",
    " Command Center": "command_center",
}


@lru_cache(maxsize=1)
def _kind_by_type_id() -> dict[int, str]:
    """type_id пина -> «kind» — реверс матрицы из data/type_ids.json."""
    if not TYPE_IDS_PATH.is_file():
        return {}
    raw = json.loads(TYPE_IDS_PATH.read_text(encoding="utf-8"))
    out: dict[int, str] = {}
    for name, type_id in raw.items():
        if name.startswith("structure:"):
            out[int(type_id)] = name.split(":", 1)[1]
            continue
        for suffix, kind in _STRUCTURE_SUFFIX_TO_KIND.items():
            if name.endswith(suffix):
                out[int(type_id)] = kind
                break
    return out


def structures_detail(pins: list[dict]) -> list[dict]:
    """
    Поимённый состав колонии из реальных пинов — тот же формат
    [{"kind": ..., "count": ...}], что и у расчётного плана, в том же
    порядке (STRUCTURE_ORDER), чтобы фронтенд рисовал одинаковую полосу
    иконок для плана и для факта. Пин с неизвестным type_id пропускается
    молча — это честный пробел, а не повод падать.
    """
    from domain.planner import STRUCTURE_ORDER

    kind_by_type = _kind_by_type_id()
    counts: dict[str, int] = {}
    for pin in pins:
        kind = kind_by_type.get(int(pin.get("type_id", 0)))
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
    return [
        {"kind": kind, "count": counts[kind]}
        for kind in STRUCTURE_ORDER
        if counts.get(kind)
    ]


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


def _system_name(client, system_id: int, cache: dict) -> str:
    """Имя системы по solar_system_id. Кэш на один прогон сборщика."""
    from scripts.esi_client import EsiError

    if system_id in cache:
        return cache[system_id]
    name = ""
    try:
        response = client.get(f"/universe/systems/{system_id}/")
        if not response.from_cache and isinstance(response.data, dict):
            name = str(response.data.get("name") or "")
    except EsiError:
        pass
    cache[system_id] = name
    return name


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
    system_cache: dict[int, str] = {}
    try:
        listing = client.get(f"/characters/{cid}/planets/", token=token)
        planets = [] if listing.from_cache else (listing.data or [])

        seen: set[int] = set()
        for entry in planets:
            planet_id = int(entry["planet_id"])
            seen.add(planet_id)
            detail = client.get(f"/characters/{cid}/planets/{planet_id}/", token=token)
            expiry = None if detail.from_cache else nearest_expiry(detail.data or {})
            structures = None if detail.from_cache else structures_detail(
                (detail.data or {}).get("pins", [])
            )

            name = _planet_name(client, planet_id)
            system = _system_name(client, int(entry.get("solar_system_id", 0)), system_cache)

            row = session.get(Colony, (cid, planet_id))
            fields = dict(
                planet_name=name,
                system_name=system,
                planet_index=planet_index(name, system),
                planet_type=str(entry.get("planet_type", "")),
                upgrade_level=int(entry.get("upgrade_level", 0)),
                num_pins=int(entry.get("num_pins", 0)),
            )
            if not detail.from_cache:
                fields["nearest_expiry"] = expiry
                fields["structures"] = structures
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
