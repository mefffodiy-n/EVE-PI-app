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
    surplus_mining = bool(payload.get("surplus_mining", False))
    direct_p2 = bool(payload.get("direct_p2", False))

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

    key = cache_key(sorted(constellations), factory_sys, sorted(targets),
                    allow_single, surplus_mining, direct_p2)

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
            surplus_mining=surplus_mining,
            direct_p2=direct_p2,
        )
        result = build_plan(
            request_obj,
            characters,
            recipes=load_recipes(),
            planets=load_planets(),
        )
        return result.to_dict()

    return json_ok(**plan_cache.get_or_compute(key, compute))


@bp.post("/advice")
def advice():
    """
    Помещается ли задуманное в пул персонажей и что делать, если нет.

    Отдельный эндпоинт, а не часть расчёта: подсказка нужна ДО того, как
    строить план. Узнавать о нехватке персонажей из наполовину построенного
    плана — значит тратить время впустую.
    """
    from domain.advice import advise
    from domain.planets import load_planets  # noqa: F401 (проверка доступности данных)

    payload, error = parse_json_body({"target_products": list})
    if error:
        return json_error(error)

    targets = [str(t) for t in payload["target_products"]]
    if not targets:
        return json_error("Не выбрано ни одного целевого продукта")

    from scripts.seed_dev_characters import load_characters

    characters = load_characters()
    if not characters:
        return json_error("Нет персонажей — вместимость считать не от чего", 503)

    # Цены нужны только для сортировки подсказок по выгоде. Их
    # отсутствие не мешает посчитать вместимость, и модуль об этом скажет.
    prices: dict[str, float] = {}
    try:
        from api.blueprints.market import _load_snapshot

        raw = (_load_snapshot() or {}).get("prices") or {}
        prices = {
            name: entry["buy_max"]
            for name, entry in raw.items()
            if isinstance(entry, dict) and entry.get("buy_max")
        }
    except Exception:
        prices = {}

    return json_ok(**advise(targets, characters, prices).to_dict())


# ── Сохранённые планы ────────────────────────────────────────────
# Хранятся на сервере, а не в браузере: план должен переживать
# перезагрузку, смену устройства и быть показываемым напарнику.

@bp.get("/plans")
def list_saved():
    from domain.plan_storage import list_plans

    return json_ok(plans=[p.summary() for p in list_plans()])


@bp.post("/plans")
def save_plan():
    from domain.plan_storage import PlanStorageError, save

    payload, error = parse_json_body({"rows": list})
    if error:
        return json_error(error)

    try:
        plan = save(
            name=str(payload.get("name", "")),
            request=payload.get("request") or {},
            rows=payload["rows"],
            warnings=payload.get("warnings") or [],
            assumptions=payload.get("assumptions") or [],
        )
    except PlanStorageError as exc:
        return json_error(str(exc))

    return json_ok(plan=plan.summary())


@bp.get("/plans/<plan_id>")
def get_plan(plan_id: str):
    from domain.plan_storage import PlanStorageError, load

    try:
        plan = load(plan_id)
    except PlanStorageError as exc:
        return json_error(str(exc), 404)
    return json_ok(plan=plan.to_dict())


@bp.delete("/plans/<plan_id>")
def remove_plan(plan_id: str):
    from domain.plan_storage import PlanStorageError, delete

    try:
        removed = delete(plan_id)
    except PlanStorageError as exc:
        return json_error(str(exc))
    if not removed:
        return json_error("План не найден", 404)
    return json_ok(deleted=plan_id)


@bp.get("/plans/compare")
def compare_plans():
    """
    Сравнить два плана: /api/plans/compare?left=<id>&right=<id>

    Отдаёт не только разницу в числах, но и перечень колоний, которые
    появились или исчезли: «на три планеты меньше» не отвечает на
    вопрос, каких именно.
    """
    from flask import request as flask_request

    from domain.plan_storage import PlanStorageError, compare

    left = flask_request.args.get("left")
    right = flask_request.args.get("right")
    if not left or not right:
        return json_error("Нужны оба идентификатора: left и right")

    try:
        return json_ok(**compare(left, right))
    except PlanStorageError as exc:
        return json_error(str(exc), 404)


@bp.get("/cache-stats")
def cache_stats():
    """Диагностика эффективности кэша планов."""
    return json_ok(plan_cache=plan_cache.stats())
