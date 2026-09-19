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
    purchase_p1 = bool(payload.get("purchase_p1", False))
    # Сколько раз строить весь набор целевых продуктов целиком
    # (18.09.2026, по прямому запросу пользователя — продублировать
    # выбранную цепочку, а не подбирать второй-третий продукт вручную).
    # Зажато в [1, 50]: build_plan() и без верхней границы деградирует
    # честными предупреждениями (не крашится), но заведомо большее
    # значение не имеет смысла ни для одного реального пула персонажей;
    # нечисловое значение — тихо как 1, не 400 (не критично для расчёта).
    try:
        lines_per_target = int(payload.get("lines_per_target", 1))
    except (TypeError, ValueError):
        lines_per_target = 1
    lines_per_target = max(1, min(lines_per_target, 50))
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
                    allow_single, surplus_mining, direct_p2, purchase_p1, lines_per_target)

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
            purchase_p1=purchase_p1,
            lines_per_target=lines_per_target,
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
    # 18.09.2026, найдено пользователем: без этого флага подсказка о
    # свободных персонажах считала добывающие колонии, которых
    # build_plan() в режиме purchase_p1 не строит вовсе — подсказка
    # переставала предлагать продукты раньше, чем пул реально заполнялся.
    purchase_p1 = bool(payload.get("purchase_p1", False))
    # Уже выбранное число линий (18.09.2026, по прямому запросу
    # пользователя) — чтобы «избыток»/«дефицит» не расходились с тем,
    # что реально построит build_plan() при этом значении.
    try:
        lines_per_target = int(payload.get("lines_per_target", 1))
    except (TypeError, ValueError):
        lines_per_target = 1
    lines_per_target = max(1, min(lines_per_target, 50))
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

    return json_ok(**advise(
        targets, characters, prices, purchase_p1=purchase_p1, lines_per_target=lines_per_target
    ).to_dict(lang))


def _load_prices() -> dict[str, float]:
    """Тот же снимок рыночных цен, что и у /api/market/best-product (правило 3 — только чтение)."""
    try:
        from api.blueprints.market import _load_snapshot

        raw = (_load_snapshot() or {}).get("prices") or {}
        return {
            name: entry["buy_max"]
            for name, entry in raw.items()
            if isinstance(entry, dict) and entry.get("buy_max")
        }
    except Exception:
        return {}


def _prices_collected_at() -> str | None:
    """
    Метка времени снимка цен (19.09.2026) — для «цена на момент
    построения плана» в панели/экспорте списка закупки P1: показывает,
    насколько свежи цены, а не выдаёт их за собранные прямо сейчас
    (правило 3 — снимок собирает scripts/refresh_market_prices.py по
    расписанию, обработчик его не обновляет).
    """
    try:
        from api.blueprints.market import _load_snapshot

        return (_load_snapshot() or {}).get("collected_at")
    except Exception:
        return None


def _load_planets_book():
    """
    Для точной автоматической ставки POCO по конкретной планете
    (domain/planets.py::PlanetBook.poco_rate(), см. domain/poco_tax.py) —
    тот же файл, что и весь остальной расчёт, читается один раз на
    процесс. `None`, если файла нет (свежий репозиторий без данных) —
    тогда прогноз прибыльности честно остаётся на ручном вводе по типу.
    """
    try:
        from domain.planets import load_planets

        return load_planets()
    except FileNotFoundError:
        return None


@bp.post("/plan-profitability")
def plan_profitability():
    """
    Прибыльность УЖЕ ПОСТРОЕННОГО плана с учётом налога POCO
    (docs/ROADMAP.md, Фаза 9, 16.09.2026) — не общая экономика шаблона
    (см. domain/profit.py::rank(), вкладка «Что выгоднее производить»),
    а конкретно эти строки с их настоящими planet_type и structures_detail.

    Строки плана присылает фронтенд — они у него уже есть (последний
    ответ /api/calculate), пересчитывать план заново здесь не нужно и
    было бы двойной работой. Ставка POCO — автоматически точная ставка
    КОНКРЕТНОЙ планеты строки из data/planet_industry.csv, когда она
    известна (ручной ввод по типу планеты убран 17.09.2026 при переходе
    к мультирегиональности — см. docstring domain/poco_tax.py).
    """
    from domain.poco_tax import evaluate_plan_profitability

    payload, error = parse_json_body({
        "rows": list, "target_products": list,
    })
    if error:
        return json_error(error)

    rows = payload["rows"]
    if not all(isinstance(r, dict) for r in rows):
        return json_error("Каждая строка плана должна быть объектом")

    targets = [str(t) for t in payload["target_products"]]
    # Необязательное поле (режим purchase_p1, 18.09.2026) — {} в обычном
    # плане, не добавлено в схему parse_json_body() выше специально: она
    # проверяет только обязательные поля, остальное читается из того же payload.
    purchased_p1 = payload.get("purchased_p1") or {}

    result = evaluate_plan_profitability(
        rows, targets, _load_prices(), planets=_load_planets_book(), purchased_p1=purchased_p1
    )
    return json_ok(**result.to_dict(), prices_collected_at=_prices_collected_at())


@bp.post("/colonies-profitability")
def colonies_profitability():
    """
    Прибыльность НАСТОЯЩИХ синхронизированных колоний («Мои колонии в
    игре») с учётом налога POCO (docs/ROADMAP.md, Фаза 9, 16.09.2026,
    пункт после plan-profitability) — тот же характер расчёта, что и у
    плана, но по факту, а не по теории: скорость выхода каждой колонии
    фронтенд уже посчитал с поправкой на реальное состояние (затухание
    экстрактора, простой/работа фабрик — `colonyOutputRatePerHour()`,
    `web/index.html`), налог — только экспортный, поколонийно, без графа
    между разными колониями (см. domain/poco_tax.py::ColonyProfitability).

    colonies — по одной записи на колонию: label, product, units_per_hour
    (`null` — текущее состояние неизвестно), planet_type, system, planet.
    Ставка POCO — автоматически точная ставка этой планеты из файла,
    когда известна её система/номер (ручной ввод по типу убран
    17.09.2026, см. docstring domain/poco_tax.py).
    """
    from domain.poco_tax import evaluate_colonies_profitability

    payload, error = parse_json_body({"colonies": list})
    if error:
        return json_error(error)

    colonies = payload["colonies"]
    if not all(isinstance(c, dict) for c in colonies):
        return json_error("Каждая колония должна быть объектом")

    result = evaluate_colonies_profitability(colonies, _load_prices(), planets=_load_planets_book())
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
