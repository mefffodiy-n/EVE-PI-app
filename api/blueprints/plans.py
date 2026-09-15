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
from domain.planner import PlanRequest, PlanResult, build_plan
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
    # Прямое P2 приостановлено (15.09.2026, решение пользователя): состав
    # застройки (domain/direct_p2.py) — расчёт из стоимостей отдельных
    # структур, а не выписка из проверенного игрового шаблона (готового
    # шаблона на два экстрактора + P1 + P2-фабрики на одной планете в
    # наборе DalShooth нет), и никогда не был сверен в игре. Флажок в
    # интерфейсе убран; здесь — вторая точка контроля, чтобы прямой вызов
    # /api/calculate с direct_p2=true в теле запроса тоже не включал
    # непроверенную застройку. Код (domain/direct_p2.py, planner.py::
    # _plan_direct_p2/_place_direct_p2) не удалён — раз проверят в игре,
    # включается обратно снятием этой строки. См. docs/ROADMAP.md, Фаза 9.
    direct_p2 = False
    lang = "en" if str(payload.get("lang", "ru")).lower().startswith("en") else "ru"

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

    from api.session import current_account_id

    account_id = current_account_id()
    # account_id — первый в ключе кэша не для порядка, а по необходимости:
    # без него один и тот же набор параметров у двух разных пользователей
    # (разные персонажи!) вернул бы план ОДНОГО из них другому из кэша
    # (найдено 11.09.2026, тот же класс ошибки, что и в load_characters()).
    key = cache_key(account_id, sorted(constellations), factory_sys, sorted(targets),
                    allow_single, surplus_mining, direct_p2)

    def compute() -> PlanResult:
        # Персонажи — только этого визита (api/session.py). На Фазе 1
        # были dev-заглушки, с Фазы 3 те же поля приходят из
        # sync_character_skills для реальных — планировщик об источнике
        # не знает, знает только про счёт «свои/чужие».
        characters = load_characters(account_id)
        if not characters:
            empty = PlanResult()
            empty.warn("no_characters")
            return empty

        request_obj = PlanRequest(
            constellations=constellations,
            factory_system=factory_sys,
            target_products=targets,
            allow_single_template_fallback=allow_single,
            surplus_mining=surplus_mining,
            direct_p2=direct_p2,
        )
        return build_plan(
            request_obj,
            characters,
            recipes=load_recipes(),
            planets=load_planets(),
        )

    result = plan_cache.get_or_compute(key, compute)
    return json_ok(**result.to_dict(lang))


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
    lang = "en" if str(payload.get("lang", "ru")).lower().startswith("en") else "ru"
    if not targets:
        return json_error("Не выбрано ни одного целевого продукта")

    from api.session import current_account_id
    from scripts.seed_dev_characters import load_characters

    characters = load_characters(current_account_id())
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

    return json_ok(**advise(targets, characters, prices).to_dict(lang))


MAX_POCO_RATE = 1.0  # 100% — выше физически бессмысленно, явный признак опечатки


@bp.post("/plan-profitability")
def plan_profitability():
    """
    Прибыльность УЖЕ ПОСТРОЕННОГО плана с учётом налога POCO
    (docs/ROADMAP.md, Фаза 9, 16.09.2026) — не общая экономика шаблона
    (см. domain/profit.py::rank(), вкладка «Что выгоднее производить»),
    а конкретно эти строки с их настоящими planet_type и structures_detail.

    Строки плана присылает фронтенд — они у него уже есть (последний
    ответ /api/calculate), пересчитывать план заново здесь не нужно и
    было бы двойной работой. Ставки POCO по типам планет — ручной ввод
    пользователя (вкладка «Настройки»), не из data/planet_industry.csv
    (там точные, но статичные значения по конкретным планетам — устарели
    бы молча, см. docstring domain/poco_tax.py).
    """
    from domain.poco_tax import evaluate_plan_profitability

    payload, error = parse_json_body({
        "rows": list, "target_products": list, "poco_rates": dict,
    })
    if error:
        return json_error(error)

    rows = payload["rows"]
    if not all(isinstance(r, dict) for r in rows):
        return json_error("Каждая строка плана должна быть объектом")

    targets = [str(t) for t in payload["target_products"]]

    rates: dict[str, float] = {}
    for planet_type, rate in payload["poco_rates"].items():
        if not isinstance(rate, (int, float)):
            return json_error(f"Ставка POCO для «{planet_type}» должна быть числом")
        if not (0 <= rate <= MAX_POCO_RATE):
            return json_error(
                f"Ставка POCO для «{planet_type}» должна быть от 0 до {MAX_POCO_RATE:.0%}"
            )
        rates[str(planet_type)] = float(rate)

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

    result = evaluate_plan_profitability(rows, targets, prices, rates)
    return json_ok(**result.to_dict())


# ── Сохранённые планы ────────────────────────────────────────────
# Хранятся на сервере, а не в браузере: план должен переживать
# перезагрузку, смену устройства и быть показываемым напарнику.

@bp.get("/plans")
def list_saved():
    """Планы только этого визита (api/session.py) — не все сохранённые кем угодно."""
    from api.session import current_account_id
    from domain.plan_storage import list_plans

    return json_ok(plans=[p.summary() for p in list_plans(current_account_id())])


@bp.post("/plans")
def save_plan():
    from api.session import current_account_id
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
            account_id=current_account_id(),
        )
    except PlanStorageError as exc:
        return json_error(str(exc))

    return json_ok(plan=plan.summary())


@bp.get("/plans/<plan_id>")
def get_plan(plan_id: str):
    """
    Чужой план (или несуществующий) — одна и та же ошибка «не найден»,
    не различить снаружи: не палить сам факт существования plan_id.
    """
    from api.session import current_account_id
    from domain.plan_storage import PlanStorageError, load

    try:
        plan = load(plan_id, current_account_id())
    except PlanStorageError as exc:
        return json_error(str(exc), 404)
    return json_ok(plan=plan.to_dict())


@bp.delete("/plans/<plan_id>")
def remove_plan(plan_id: str):
    from api.session import current_account_id
    from domain.plan_storage import PlanStorageError, delete

    try:
        removed = delete(plan_id, current_account_id())
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
    вопрос, каких именно. Оба плана обязаны принадлежать этому визиту —
    иначе та же честная ошибка «не найден», что и у /plans/<id>.
    """
    from flask import request as flask_request

    from api.session import current_account_id
    from domain.plan_storage import PlanStorageError, compare

    left = flask_request.args.get("left")
    right = flask_request.args.get("right")
    if not left or not right:
        return json_error("Нужны оба идентификатора: left и right")

    try:
        return json_ok(**compare(left, right, current_account_id()))
    except PlanStorageError as exc:
        return json_error(str(exc), 404)


@bp.get("/cache-stats")
def cache_stats():
    """Диагностика эффективности кэша планов."""
    return json_ok(plan_cache=plan_cache.stats())
