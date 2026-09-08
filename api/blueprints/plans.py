"""
Эндпоинт построения производственного плана.

Контракт зафиксирован фронтендом (web/index.html):
  POST /api/calculate
  body:  {constellations: [...], factory_sys: "...", target_products: [...]}
  resp:  {status, data: [...], warning, recommendation}

Имя поля factory_sys сохранено как в v1 — переименование сломало бы
существующий index.html без реальной пользы.

НАГРУЗКА. Это самый дорогой обработчик приложения, а Flask синхронный:
пока идёт расчёт, воркер не обслуживает других. Поэтому результат
кэшируется по параметрам запроса — расчёт детерминирован, а множество
возможных комбинаций невелико (список констелляций и продуктов
ограничен справочником). Кэш сбрасывается сборщиками при обновлении
данных.
"""

from __future__ import annotations

from flask import Blueprint

from api.cache import cache_key, json_error, json_ok, parse_json_body, plan_cache
from domain.planets import load_planets
from domain.planner import PlanRequest, build_plan
from domain.recipes import load_recipes
from scripts.seed_dev_characters import load_characters

bp = Blueprint("plans", __name__)

MAX_TARGET_PRODUCTS = 20


@bp.post("/calculate")
def calculate():
    payload, error = parse_json_body(
        {"constellations": list, "factory_sys": str, "target_products": list}
    )
    if error:
        return json_error(error)

    constellations = [str(c) for c in payload["constellations"]]
    factory_sys = payload["factory_sys"].strip()
    targets = [str(t) for t in payload["target_products"]]
    allow_single = bool(payload.get("allow_single_template_fallback", False))

    if not constellations:
        return json_error("Не выбрано ни одной констелляции")
    if not factory_sys:
        return json_error("Не выбрана домашняя система")
    if not targets:
        return json_error("Не выбрано ни одного целевого продукта")
    if len(targets) > MAX_TARGET_PRODUCTS:
        # Ограничение защищает от запроса, который займёт воркер надолго.
        return json_error(
            f"Слишком много целевых продуктов: {len(targets)}. "
            f"Максимум {MAX_TARGET_PRODUCTS} за один расчёт."
        )

    key = cache_key(sorted(constellations), factory_sys, sorted(targets), allow_single)

    def compute():
        # Персонажи: на Фазе 1 — dev-заглушки, в Фазе 3 те же поля придут
        # из sync_character_skills. Планировщик об источнике не знает.
        characters = load_characters()
        if not characters:
            return {
                "data": [],
                "warning": "Нет персонажей. Заполните их командой "
                           "`python -m scripts.seed_dev_characters` (только dev-окружение).",
                "warnings": [],
                "assumptions": [],
                "needs_user_decision": False,
                "site_warnings": [],
            }

        request_obj = PlanRequest(
            constellations=constellations,
            factory_system=factory_sys,
            target_products=targets,
            allow_single_template_fallback=allow_single,
        )
        result = build_plan(
            request_obj,
            characters,
            recipes=load_recipes(),
            planets=load_planets(),
        )
        return result.to_dict()

    return json_ok(**plan_cache.get_or_compute(key, compute))


@bp.get("/cache-stats")
def cache_stats():
    """Диагностика эффективности кэша планов."""
    return json_ok(plan_cache=plan_cache.stats())
