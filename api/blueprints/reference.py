"""
Справочные эндпоинты: констелляции, системы, продукты, пороги радиусов.

Контракт зафиксирован существующим фронтендом (web/index.html):
  GET  /api/initial-data -> {status, bases[], products[], product_ids{}}
  POST /api/systems      -> {status, systems[]}   body: {constellations: [...]}

Данные здесь полностью статичны в пределах процесса, поэтому считаются
один раз и отдаются с ETag — это самый дешёвый способ не нагружать
сервер при большом числе пользователей.

product_ids нужны фронту для иконок с images.evetech.net. Источник —
статический снапшот data/type_ids.json, а НЕ живой запрос к ESI при
старте, как было в main.py v1 (это нарушало правило «пользователь
не инициирует обращения к ESI»).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from flask import Blueprint

from api.cache import json_error, json_ok, parse_json_body, with_etag
from domain.factory_site import describe_thresholds
from domain.planets import load_planets
from domain.recipes import load_recipes

bp = Blueprint("reference", __name__)

TYPE_IDS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "type_ids.json"
SCHEMATICS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "schematics.json"
TYPE_VOLUMES_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "cache" / "type_volumes.json"
)

PROCESSING_TIERS = ("P2", "P3", "P4")


@lru_cache(maxsize=1)
def _type_ids() -> dict[str, int]:
    """
    Статический снапшот type_id продуктов PI.

    Файла может не быть — тогда фронт просто не покажет иконки.
    Это лучше, чем сходить за ними в ESI: правило проекта прямо
    запрещает обращения к ESI из пользовательского запроса.
    """
    if not TYPE_IDS_PATH.is_file():
        return {}
    return json.loads(TYPE_IDS_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _schematics_by_output() -> dict[str, dict]:
    """
    Количества вход/выход за цикл каждой фабричной схемы, по type_id
    ПРОДУКТА (см. scripts/extract_schematics.py — ключ "S" игровых
    шаблонов оказался type_id продукта, а не ESI schematic_id, поэтому
    сопоставление с настоящим пином идёт через product_type_id/имя, а
    не через schematic_id из ESI).

    Нужно фронтенду для честной проекции состояния фабрики вперёд по
    времени (simulateColonyFactories(), web/index.html) — сколько сырья
    фабрика потребляет и производит за цикл ESI не отдаёт вовсе, а
    recipes.json даёт только пропорции входов, не абсолютные количества.
    Отдаём только inputs/output_qty — остальные поля файла (facility,
    sources, inputs_by_name) фронту не нужны, там уже есть свои имена и
    иконки через product_ids.
    """
    if not SCHEMATICS_PATH.is_file():
        return {}
    raw = json.loads(SCHEMATICS_PATH.read_text(encoding="utf-8")).get("schematics", {})
    return {
        type_id: {"inputs": entry["inputs"], "output_qty": entry["output_qty"]}
        for type_id, entry in raw.items()
    }


@lru_cache(maxsize=1)
def _type_volumes() -> dict[str, float]:
    """
    Объём одной единицы предмета (м³) по type_id — то же самое, чем
    сервер уже переводит содержимое причала в used_m3 (см.
    scripts/sync_colony_status.py::type_volume()), только сюда, фронту.

    ЗАЧЕМ. simulateColonyFactories() (web/index.html) досчитывает
    содержимое причала вперёд по времени — сырьё убывает, продукт
    прибывает, — но старое used_m3/capacity_m3 остаётся статичным
    снимком с последней синхронизации: с течением времени объём в
    причале на самом деле меняется (сырьё обычно «тяжелее» готовой
    продукции на единицу), а показанный процент — нет (найдено
    пользователем 17.09.2026). Без объёма на единицу фронту нечем
    пересчитать used_m3 под спроецированное содержимое.

    Кэш файла (data/cache/type_volumes.json) РАСТЁТ во время работы
    процесса — каждый новый тип предмета, встреченный sync_colony_status,
    дописывается в файл. `lru_cache` здесь означает то же самое
    ограничение, что и у _type_ids()/_schematics_by_output(): свежие
    типы, узнанные ПОСЛЕ старта этого процесса, попадут во фронт только
    после его перезапуска. До тех пор для них честно «нет данных»
    (simulateColonyFactories() пропускает used_m3 при первом же
    неизвестном типе, правило 1) — деградация, а не поломка.
    """
    if not TYPE_VOLUMES_PATH.is_file():
        return {}
    try:
        raw = json.loads(TYPE_VOLUMES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, (int, float))}


@lru_cache(maxsize=1)
def _initial_payload() -> dict:
    """
    Считается один раз на процесс.

    Отсутствие данных по планетам НЕ роняет ответ: продукты берутся из
    рецептов и доступны всегда, а список констелляций приходит пустым
    с внятным пояснением. Иначе интерфейс показывал бы пустые списки
    без единого намёка на причину — ровно тот симптом, по которому
    невозможно понять, что не так.
    """
    processing_recipes = [r for r in load_recipes() if r.tier in PROCESSING_TIERS]
    products = sorted(r.name for r in processing_recipes)
    # Тир продукта — для кнопок-фильтров P2/P3/P4 над списком в карточке
    # «Что производим» (17.09.2026, Фаза 11, по прямому запросу
    # пользователя): раньше фронтенд получал только имена, без тира
    # сузить список можно было исключительно текстовым поиском.
    product_tiers = {r.name: r.tier for r in processing_recipes}
    ids = _type_ids()

    # Отдаём карту id целиком, а не только по целевым продуктам.
    # Фронтенду она нужна шире: помимо выпадающего списка, из неё берутся
    # иконки промежуточных продуктов в дашборде (включая P1) и иконки
    # командных центров в списке закупки, где ключ выглядит как
    # "Barren Command Center". Раньше отдавались только P2-P4, поэтому
    # обе эти группы иконок оставались пустыми.
    # Имена входов рецепта — фронтенду показать «Вход» у настоящей фабрики
    # (панель колонии): ESI отдаёт, что фабрика производит, но не что она
    # потребляет — recipes.json это уже знает и проверено по источникам
    # (правило 2), выдумывать не приходится. У P1 вход один — R0-сырьё,
    # лежит в поле source, а не inputs (см. domain/recipes.py).
    recipe_inputs: dict[str, list[str]] = {}
    for r in load_recipes():
        if r.inputs:
            recipe_inputs[r.name] = sorted(r.inputs.keys())
        elif r.source:
            recipe_inputs[r.name] = [r.source]

    payload = {
        "products": products,
        "product_tiers": product_tiers,
        "product_ids": ids,
        "recipe_inputs": recipe_inputs,
        "schematics": _schematics_by_output(),
        "type_volumes": _type_volumes(),
        "bases": [],
        "regions": {},
        "system_counts": {},
        "data_problems": [],
    }

    try:
        book = load_planets()
        if book.dataframe.empty:
            # БД без переноса (свежий клон/сервер до `scripts.
            # migrate_planets_csv_to_db`) — load_planets() с 21.09.2026
            # (Фаза 1 мультирегиональности) не падает на этом, а честно
            # отдаёт пустой PlanetBook (см. её докстринг); раньше здесь
            # ловился FileNotFoundError отсутствующего CSV — теперь
            # пустоту нужно обнаруживать явно, иначе списки констелляций
            # и систем молча окажутся пустыми без объяснения (правило 1).
            payload["data_problems"].append(
                "Таблицы planets/regions пусты — списки констелляций и "
                "систем будут пусты. Выполните: python -m alembic upgrade "
                "head && python -m scripts.migrate_planets_csv_to_db "
                "(см. deploy/README.md, раздел 1)."
            )
        else:
            payload["bases"] = book.constellations()
            payload["regions"] = book.regions()
            payload["system_counts"] = {
                c: int((book.dataframe["Constellation"] == c).sum())
                for c in payload["bases"]
            }
    except Exception as exc:  # данные в БД испорчены неожиданным образом
        payload["data_problems"].append(
            f"Не удалось разобрать данные о планетах: "
            f"{type(exc).__name__}: {exc}"
        )

    if not payload["product_ids"]:
        payload["data_problems"].append(
            "Нет карты type_id — иконки продуктов не отобразятся. "
            "Создайте её командой: python -m scripts.extract_schematics --write"
        )
    if not payload["schematics"]:
        payload["data_problems"].append(
            "Нет data/schematics.json — проекция состояния фабрик "
            "вперёд по времени недоступна, показывается только честное "
            "«неизвестно». Создайте файл: python -m scripts.extract_schematics --write"
        )

    return payload


@bp.get("/initial-data")
@with_etag
def initial_data():
    return json_ok(**_initial_payload())


@bp.post("/systems")
def systems():
    payload, error = parse_json_body({"constellations": list})
    if error:
        return json_error(error)

    constellations = [str(c) for c in payload["constellations"]]
    if not constellations:
        return json_error("Список констелляций пуст")

    return json_ok(systems=load_planets().systems_in(constellations))


@bp.get("/system-planets")
def system_planets():
    """
    Планеты одной системы: тип и радиус.

    Нужно, чтобы показать пороговые радиусы ДО расчёта — пользователь
    сразу видит, сколько планет в выбранной системе вообще подходит,
    а не узнаёт об этом после построения плана.
    """
    from flask import request as flask_request

    from domain.planets import RADIUS_COLUMN

    system = (flask_request.args.get("system") or "").strip()
    if not system:
        return json_error("Не указана система")

    frame = load_planets().factory_candidates(system)

    planets = []
    for _, row in frame.iterrows():
        radius = row.get(RADIUS_COLUMN)
        if radius is None or radius != radius:
            continue
        planets.append({
            "planet": str(row.get("Planet", "")),
            "type": str(row.get("Type", "")),
            "radius": float(radius),
        })
    return json_ok(system=system, planets=planets)


@bp.get("/thresholds/<int:ccu_level>")
@with_etag
def thresholds(ccu_level: int):
    """
    Предельные радиусы планет для каждого перерабатывающего шаблона.

    Нужно фронтенду, чтобы показать цифры ДО выбора домашней системы,
    а не после расчёта плана: пользователь сразу видит, что двойной
    шаблон требует планет не крупнее такого-то радиуса.
    """
    if not 0 <= ccu_level <= 5:
        return json_error("Уровень Command Center Upgrades должен быть от 0 до 5")
    return json_ok(ccu_level=ccu_level, thresholds=describe_thresholds(ccu_level))
