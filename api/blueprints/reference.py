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
def _initial_payload() -> dict:
    """
    Считается один раз на процесс.

    Отсутствие данных по планетам НЕ роняет ответ: продукты берутся из
    рецептов и доступны всегда, а список констелляций приходит пустым
    с внятным пояснением. Иначе интерфейс показывал бы пустые списки
    без единого намёка на причину — ровно тот симптом, по которому
    невозможно понять, что не так.
    """
    products = sorted(r.name for r in load_recipes() if r.tier in PROCESSING_TIERS)
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
        "product_ids": ids,
        "recipe_inputs": recipe_inputs,
        "schematics": _schematics_by_output(),
        "bases": [],
        "regions": {},
        "system_counts": {},
        "data_problems": [],
    }

    try:
        book = load_planets()
        payload["bases"] = book.constellations()
        payload["regions"] = book.regions()
        payload["system_counts"] = {
            c: int((book.dataframe["Constellation"] == c).sum())
            for c in payload["bases"]
        }
    except FileNotFoundError:
        payload["data_problems"].append(
            "Не найден файл data/planet_industry.csv — списки констелляций "
            "и систем будут пусты. Скопируйте его из корня старого "
            "репозитория, переименовав без пробела в имени."
        )
    except Exception as exc:  # формат файла испорчен
        payload["data_problems"].append(
            f"Не удалось разобрать data/planet_industry.csv: "
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

    try:
        return json_ok(systems=load_planets().systems_in(constellations))
    except FileNotFoundError:
        return json_error(
            "Не найден файл data/planet_industry.csv — список систем недоступен.", 503
        )


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

    try:
        frame = load_planets().factory_candidates(system)
    except FileNotFoundError:
        return json_error("Нет данных по планетам", 503)

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
