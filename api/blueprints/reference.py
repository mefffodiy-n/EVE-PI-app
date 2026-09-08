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
def _initial_payload() -> dict:
    """Считается один раз на процесс."""
    recipes = load_recipes()
    planets = load_planets()
    products = sorted(r.name for r in recipes if r.tier in PROCESSING_TIERS)
    ids = _type_ids()
    return {
        "bases": planets.constellations(),
        "products": products,
        "product_ids": {name: ids[name] for name in products if name in ids},
    }


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
