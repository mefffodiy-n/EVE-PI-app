"""
Сборщик скелета планет по всем регионам New Eden — Фаза 2
мультирегиональности (21.09.2026, docs/ROADMAP.md, Фаза 10).

ЧТО ДЕЛАЕТ. Наполняет таблицы `regions`/`planets` (`infra/models.py`)
скелетом (регион/констелляция/система/тип/радиус) из официального SDE
(Static Data Export) CCP — для ЛЮБОГО региона, не только Fountain.
Плотность сырья и POCO нигде не трогает: ни у новых регионов (там их и
взять неоткуда — SDE абундантность не публикует, docs/ROADMAP.md), ни
у уже существующего Fountain (ручной ввод через будущую Фазу 3, или
уже перенесённые Фазой 1 данные — их менять не наше дело).

ИСТОЧНИК И ПОЧЕМУ ЭТО БЕЗОПАСНО (проверено 21.09.2026, живым запросом,
билд 3503375 — правило 11, источник истины не память). SDE — это НЕ
ESI: распространяется не через API с лимитом ошибок, а как ОДИН
скачиваемый ZIP-архив (JSON Lines) с обычного CDN (CloudFront+S3),
с ETag/Last-Modified. Ссылка с авторедиректом на актуальный билд:

    https://developers.eveonline.com/static-data/eve-online-static-data-latest-jsonl.zip

`HEAD`-запрос по этой ссылке отдаёт заголовок `x-sde-build-number` БЕЗ
скачивания тела — им проверяем, изменилось ли что-то, и НЕ качаем
95 МБ архив, если билд тот же, что при прошлом успешном запуске
(снимок — data/cache/sde_build.json). Официальная документация
(developers.eveonline.com/docs/services/static-data/) не упоминает ни
лимита частоты, ни авторизации — это статический файл, а не
последовательность вызовов, поэтому банить не за что, если не
скачивать его чаще, чем нужно (см. интервал в scripts/scheduler.py).

Из архива читаются ТОЛЬКО 4 небольших датасета (regions/constellations/
solarSystems/planets), построчно (генератором, не целиком в память) —
`types.jsonl` (152 МБ) и `mapMoons.jsonl`/`mapAsteroidBelts.jsonl` не
трогаются вовсе: имена 16 типов планет на всю игру зашиты константой
ниже (см. PLANET_TYPE_NAMES), т.к. они не меняются от билда к билду.

СВЕРКА РЕГИСТРА. SDE даёт имя констелляции в исходном регистре
(«Minotaur»), а уже перенесённые Фазой 1 968 строк Fountain хранят его
ЗАГЛАВНЫМИ («MINOTAUR» — так был оформлен исходный CSV, весь фронтенд
полагается на этот регистр). Имя констелляции нормализуется в
`.upper()` при записи, чтобы не завести разнобой среди планет одной и
той же констелляции. Имя системы — не трогается (в SDE уже в
каноническом виде, совпадает без преобразований — проверено на
57-KJB).

СОПОСТАВЛЕНИЕ С УЖЕ СУЩЕСТВУЮЩИМИ СТРОКАМИ. Регион — по `Region.name`
(уникален с Фазы 1). Планета — по паре (`system`, `planet_number`):
довольно и без отдельного столбца `sde_id` — система+номер планеты
уникальны по факту устройства игры. Уже существующая планета
ПРОПУСКАЕТСЯ целиком (не трогает радиус/тип/плотность/POCO — вдруг уже
правлены вручную); новый регион создаётся со статусом `no_data`
(показывать пользователям в планировщике его будет решать Фаза 4).

Запуск:
    python -m scripts.refresh_sde              # вручную, разово
    python -m scripts.scheduler                 # по расписанию (раз в 6 часов)
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CACHE_DIR = ROOT / "data" / "cache"
BUILD_SNAPSHOT = CACHE_DIR / "sde_build.json"

LATEST_URL = "https://developers.eveonline.com/static-data/eve-online-static-data-latest-jsonl.zip"
TIMEOUT_SECONDS = 60
COMMIT_BATCH_SIZE = 500

# Все типы планет в игре (группа "Planet", groupID=7 в types.jsonl) —
# проверено на билде 3503375, 21.09.2026, живым запросом к SDE. Список
# на всю игру, меняется крайне редко (последнее добавление — "Scorched
# Barren"), поэтому не стоит стримить 152-мегабайтный types.jsonl ради
# 16 строк на каждый запуск; при появлении нового typeID сборщик его
# честно пометит в unknown_type_ids, не упадёт и не выдумает имя.
PLANET_TYPE_NAMES: dict[int, str] = {
    11: "Temperate",
    12: "Ice",
    13: "Gas",
    2014: "Oceanic",
    2015: "Lava",
    2016: "Barren",
    2017: "Storm",
    2063: "Plasma",
    30889: "Shattered",
    56018: "Barren",
    56019: "Ice",
    56020: "Lava",
    56021: "Oceanic",
    56022: "Plasma",
    56023: "Temperate",
    56024: "Storm",
    73911: "Scorched Barren",
}


def _user_agent() -> str:
    from version import user_agent
    return user_agent()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """
    Заголовок `x-sde-build-number` есть только на САМОМ 302-редиректе
    `.../latest-jsonl.zip` -> `.../eve-online-static-data-<build>-jsonl.
    zip` — конечный (уже проверенный) URL этот заголовок не отдаёт.
    urllib по умолчанию следует за редиректом сам и до заголовков
    редиректа код не добирается, поэтому здесь редирект отключается —
    он всплывает как HTTPError(302) с нужным заголовком в `e.headers`.
    """

    def redirect_request(self, *args, **kwargs):
        return None


def current_build_number(opener=None) -> int | None:
    """
    Номер текущего билда SDE без скачивания архива — `HEAD`-запрос на
    редиректящий URL, заголовок `x-sde-build-number` берётся с самого
    редиректа (см. `_NoRedirect`). `opener` — инъекция для тестов
    (как у scripts/refresh_market_prices.py::fetch_prices).
    """
    if opener is not None:
        return opener()

    request = urllib.request.Request(LATEST_URL, method="HEAD",
                                      headers={"User-Agent": _user_agent()})
    build_opener = urllib.request.build_opener(_NoRedirect)
    try:
        with build_opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            value = response.headers.get("x-sde-build-number")
    except urllib.error.HTTPError as exc:
        value = exc.headers.get("x-sde-build-number") if exc.code in (301, 302, 303, 307, 308) else None
    return int(value) if value else None


def _last_synced_build() -> int | None:
    if not BUILD_SNAPSHOT.is_file():
        return None
    try:
        return json.loads(BUILD_SNAPSHOT.read_text(encoding="utf-8")).get("build")
    except (json.JSONDecodeError, OSError):
        return None


def _save_synced_build(build: int) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_SNAPSHOT.write_text(json.dumps({"build": build}), encoding="utf-8")


def download(dest: Path) -> None:
    """Потоковое скачивание архива (не грузит 95 МБ в память целиком)."""
    import shutil

    request = urllib.request.Request(LATEST_URL, headers={"User-Agent": _user_agent()})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS * 5) as response:
        with open(dest, "wb") as fh:
            shutil.copyfileobj(response, fh)


def _read_jsonl(zf: zipfile.ZipFile, member: str):
    """Построчный генератор записей jsonl-члена архива, без полной распаковки."""
    with zf.open(member) as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def sync(zip_path: Path) -> dict:
    """
    Разобрать архив и наполнить regions/planets скелетом.

    Возвращает статистику: regions_created, planets_created,
    planets_skipped, unknown_type_ids (список typeID без имени в
    PLANET_TYPE_NAMES — честный пробел, не падение).
    """
    from sqlalchemy import select

    from infra.db import session_scope
    from infra.models import Planet as PlanetRow
    from infra.models import Region as RegionRow

    with zipfile.ZipFile(zip_path) as zf:
        regions_by_id = {
            row["_key"]: row["name"]["en"]
            for row in _read_jsonl(zf, "mapRegions.jsonl")
        }
        constellations_by_id = {
            row["_key"]: (row["name"]["en"].upper(), row["regionID"])
            for row in _read_jsonl(zf, "mapConstellations.jsonl")
        }
        systems_by_id = {
            row["_key"]: (row["name"]["en"], row["constellationID"])
            for row in _read_jsonl(zf, "mapSolarSystems.jsonl")
        }

        stats = {
            "regions_created": 0, "planets_created": 0,
            "planets_skipped": 0, "unknown_type_ids": set(),
        }

        with session_scope() as session:
            region_cache: dict[str, RegionRow] = {
                row.name: row for row in session.scalars(select(RegionRow))
            }
            existing_planets: set[tuple[str, int]] = {
                (row.system, row.planet_number)
                for row in session.scalars(select(PlanetRow))
            }

            pending = 0
            for row in _read_jsonl(zf, "mapPlanets.jsonl"):
                system_id = row.get("solarSystemID")
                system = systems_by_id.get(system_id)
                if system is None:
                    continue
                system_name, constellation_id = system
                constellation = constellations_by_id.get(constellation_id)
                if constellation is None:
                    continue
                constellation_name, region_id = constellation
                region_name = regions_by_id.get(region_id)
                if region_name is None:
                    continue

                planet_number = row.get("celestialIndex")
                key = (system_name, planet_number)
                if key in existing_planets:
                    stats["planets_skipped"] += 1
                    continue

                type_id = row.get("typeID")
                planet_type = PLANET_TYPE_NAMES.get(type_id)
                if planet_type is None:
                    stats["unknown_type_ids"].add(type_id)
                    continue

                region_row = region_cache.get(region_name)
                if region_row is None:
                    region_row = RegionRow(name=region_name, status="no_data")
                    session.add(region_row)
                    session.flush()  # получить region_row.id
                    region_cache[region_name] = region_row
                    stats["regions_created"] += 1

                radius = row.get("radius")
                session.add(PlanetRow(
                    region_id=region_row.id,
                    constellation=constellation_name,
                    system=system_name,
                    planet_number=planet_number,
                    planet_type=planet_type,
                    radius_km=(radius / 1000.0) if radius is not None else None,
                ))
                existing_planets.add(key)
                stats["planets_created"] += 1

                pending += 1
                if pending >= COMMIT_BATCH_SIZE:
                    session.flush()
                    pending = 0

        stats["unknown_type_ids"] = sorted(stats["unknown_type_ids"])
        return stats


def main() -> int:
    build = current_build_number()
    if build is None:
        print("Не удалось определить билд SDE (нет заголовка x-sde-build-number).")
        return 1

    if build == _last_synced_build():
        print(f"SDE уже актуален (билд {build}) — архив не скачивается.")
        return 0

    print(f"Новый билд SDE: {build}. Скачиваю архив…")
    # data/cache/, не системный tempfile.TemporaryDirectory(): прод-служба
    # запущена под systemd с ProtectSystem=strict + явным ReadWritePaths
    # (deploy/README.md, раздел 7.4) — /tmp там НЕ входит в разрешённые
    # пути и недоступен на запись, что и обнаружилось на бою 21.09.2026
    # (FileNotFoundError: No usable temporary directory found).
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = CACHE_DIR / "sde_download.zip"
    try:
        try:
            download(zip_path)
            stats = sync(zip_path)
        except Exception as exc:
            print(f"Не удалось обновить скелет планет: {type(exc).__name__}: {exc}")
            return 1
    finally:
        zip_path.unlink(missing_ok=True)

    _save_synced_build(build)

    from domain.planets import load_planets
    load_planets.cache_clear()

    print(f"Готово: билд {build}.")
    print(f"  новых регионов:  {stats['regions_created']}")
    print(f"  новых планет:    {stats['planets_created']}")
    print(f"  уже были:        {stats['planets_skipped']}")
    if stats["unknown_type_ids"]:
        print(f"  неизвестный тип планеты (typeID): {stats['unknown_type_ids']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
