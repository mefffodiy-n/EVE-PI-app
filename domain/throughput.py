"""
Производительность фабрик и расчёт потребности в них.

ОТКУДА БЕРУТСЯ ЧИСЛА:
  - количества вход/выход за цикл — из data/schematics.json, извлечённого
    из маршрутов реальных шаблонов (scripts/extract_schematics.py);
  - длительности циклов — из data/pi_reference.json, раздел
    production_cycles;
  - состав рецептов — из data/recipes.json.

Ничего не додумывается: если для продукта нет схемы, расчёт честно
сообщает об этом, а не подставляет правдоподобное число.

ЧТО НЕ ПРОВЕРЕНО. Из всей цепочки не подтверждено ровно одно значение —
отношение цикла advanced-фабрики к циклу basic. Соотношения между
P2, P3 и P4 от длительности цикла не зависят вовсе (она сокращается),
а на границе P1->P2 влияет. Все использованные допущения возвращаются
в результате расчёта, чтобы интерфейс мог их показать.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from domain.logistics import (
    causeway_units,
    launchpad_capacity_m3,
    logistics_downtime_hours,
    own_consumption_profile,
    volume_of,
)
from domain.recipes import RecipeBook, load_recipes

ROOT = Path(__file__).resolve().parent.parent
SCHEMATICS_PATH = ROOT / "data" / "schematics.json"
REFERENCE_PATH = ROOT / "data" / "pi_reference.json"

# Какая фабрика делает продукт какого тира.
FACILITY_BY_TIER = {
    "P1": "basic_industry_facility",
    "P2": "advanced_industry_facility",
    "P3": "advanced_industry_facility",
    "P4": "high_tech_industry_facility",
}


class MissingProductionData(ValueError):
    """Для продукта нет данных о производительности — расчёт невозможен."""


@dataclass(frozen=True)
class Schematic:
    product: str
    facility: str
    inputs: dict[str, float]      # единиц каждого входа за цикл
    output_qty: float             # единиц продукта за цикл
    cycle_minutes: float

    @property
    def output_per_hour(self) -> float:
        return self.output_qty * 60.0 / self.cycle_minutes

    def input_per_hour(self, name: str) -> float:
        return self.inputs[name] * 60.0 / self.cycle_minutes


@dataclass
class Demand:
    """Потребность в производстве, единиц в час, по продуктам."""

    units_per_hour: dict[str, float] = field(default_factory=dict)
    factories: dict[str, float] = field(default_factory=dict)
    raw_materials: dict[str, float] = field(default_factory=dict)  # P0, единиц в час
    assumptions_used: list[dict] = field(default_factory=list)  # {code, ...} — см. plan_messages
    missing: list[str] = field(default_factory=list)

    # P1, единиц в час — заполняется только в режиме expand_demand(purchase_p1=True)
    # (18.09.2026, по прямому запросу пользователя): эти продукты считаются
    # закупленными на бирже, не входят в factories/raw_materials этой ветки.
    purchased_p1: dict[str, float] = field(default_factory=dict)

    # {продукт: доля времени реальной работы, не простоя} — пропускная
    # способность причала (20.09.2026, по прямому запросу пользователя,
    # см. domain/logistics.py). Каскадное: продукт не может работать
    # стабильнее, чем самый нестабильный из его собственных входов,
    # произведённых В ЭТОМ ЖЕ плане (сырьё P0 и закупленный P1 —
    # граница, считаются непрерывными сами по себе — их простой уже
    # учтён в duty cycle СЛЕДУЮЩЕГО тира, который их потребляет).
    # Продукт без входов, требующих схемы (нет в demand.factories) —
    # 1.0 по умолчанию, если явно не посчитан.
    duty_cycles: dict[str, float] = field(default_factory=dict)
    missing_volumes: list[str] = field(default_factory=list)

    # Доля СОБСТВЕННОГО спроса каждого целевого продукта, которая идёт
    # на прямую продажу, а не на переработку в ДРУГОЙ выбранный целевой
    # продукт этого же плана (20.09.2026, по прямому запросу
    # пользователя: «нельзя просто убирать продукт, если он участвует
    # в цепочке выше тиром — нужно разделять по виду: целевой для своей
    # цепочки, или проходной для цепочки более высокого тира»).
    # Пользователь может выбрать целями одновременно, например, Data
    # Chips (P3) И Broadcast Node (P4, который её ест) — тогда часть
    # построенных колоний Data Chips обслуживает СВОЙ прямой таргет
    # (продаётся), а часть — питает Broadcast Node (не продаётся сама
    # по себе). Единственное различие между этими колониями — для чего
    # они посчитаны, физически они одинаковые и делят один пул фабрик
    # (правило "shared_components" — общие компоненты считаются
    # суммарной потребностью, не строятся отдельно на каждую цепочку).
    # revenue_share[product] = прямой_целевой_расход / суммарный_расход
    # — 1.0, если продукт нигде больше не потребляется в этом плане.
    revenue_share: dict[str, float] = field(default_factory=dict)

    def add(self, product: str, rate: float) -> None:
        self.units_per_hour[product] = self.units_per_hour.get(product, 0.0) + rate


@lru_cache(maxsize=1)
def _cycles() -> dict[str, dict]:
    reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in reference["production_cycles"].items() if not k.startswith("_")}


@lru_cache(maxsize=1)
def load_schematics() -> dict[str, Schematic]:
    """
    Схемы производства, ключ — имя продукта.

    Файл создаётся скриптом scripts/extract_schematics.py из шаблонов.
    Если его нет, возвращается пустой словарь: планировщик тогда честно
    сообщит, что данных не хватает, вместо выдачи выдуманного плана.
    """
    if not SCHEMATICS_PATH.is_file():
        return {}

    raw = json.loads(SCHEMATICS_PATH.read_text(encoding="utf-8"))
    cycles = _cycles()
    result: dict[str, Schematic] = {}

    for entry in raw.get("schematics", {}).values():
        product = entry.get("product")
        facility = entry.get("facility")
        output_qty = entry.get("output_qty")
        inputs = entry.get("inputs_by_name") or {}
        if not product or not facility or output_qty is None:
            continue
        cycle = cycles.get(facility, {}).get("minutes")
        if cycle is None:
            continue
        result[product] = Schematic(
            product=product,
            facility=facility,
            inputs={str(k): float(v) for k, v in inputs.items()},
            output_qty=float(output_qty),
            cycle_minutes=float(cycle),
        )
    return result


def unverified_assumptions() -> list[dict]:
    """Непроверенные допущения, влияющие на расчёт: код + параметры."""
    return [
        {"code": "assume_cycle_duration", "facility": facility, "minutes": entry["minutes"]}
        for facility, entry in _cycles().items()
        if not entry.get("verified", False)
    ]


def expand_demand(
    targets: dict[str, float],
    schematics: dict[str, Schematic] | None = None,
    recipes: RecipeBook | None = None,
    purchase_p1: bool = False,
) -> Demand:
    """
    Развернуть потребность в целевых продуктах до сырья P0.

    targets: {имя продукта: единиц в час}.

    Возвращает Demand с потребностью по всем промежуточным продуктам,
    числом фабрик на каждом уровне и расходом сырья P0.

    purchase_p1 (18.09.2026, по прямому запросу пользователя: «план и
    выгода только по переработке из закупаемого P1») — P1 становится
    листом дерева вместо разворота в P0: не строится (не попадает в
    demand.factories), не считается сырьём собственной добычи, а
    накапливается в demand.purchased_p1 по имени продукта. Дерево выше
    P1 (P2→P3→P4) разворачивается одинаково в обоих режимах — эта
    функция не дублируется.
    """
    schematics = load_schematics() if schematics is None else schematics
    recipes = load_recipes() if recipes is None else recipes

    demand = Demand(assumptions_used=unverified_assumptions())
    queue: list[tuple[str, float]] = list(targets.items())
    seen_depth = 0

    while queue:
        seen_depth += 1
        if seen_depth > 10_000:
            raise RuntimeError("Похоже, дерево рецептов зациклилось")

        product, rate = queue.pop()
        if rate <= 0:
            continue

        recipe = recipes.get(product)
        if recipe is None:
            # Не продукт PI — значит сырьё P0.
            demand.raw_materials[product] = demand.raw_materials.get(product, 0.0) + rate
            continue

        if purchase_p1 and recipe.tier == "P1":
            # Лист дерева: не строится (не в demand.factories/missing),
            # закупается на бирже — до подсчёта фабрик и до обращения к
            # schematics, которых для закупаемого P1 не нужно вовсе.
            demand.purchased_p1[product] = demand.purchased_p1.get(product, 0.0) + rate
            continue

        demand.add(product, rate)

        schematic = schematics.get(product)
        if schematic is None:
            if product not in demand.missing:
                demand.missing.append(product)
            continue

        factories = rate / schematic.output_per_hour
        demand.factories[product] = demand.factories.get(product, 0.0) + factories

        if recipe.tier == "P1":
            # P1 делается из сырья P0; количество на цикл известно из схемы.
            for source_name, qty in schematic.inputs.items():
                queue.append((source_name, factories * qty * 60.0 / schematic.cycle_minutes))
            # Имя сырья в схеме может не разрешиться (нет шаблона для P0),
            # поэтому дублируем известное из рецепта.
            if recipe.source and not schematic.inputs:
                queue.append((recipe.source, factories * 3000.0 * 2))
            continue

        for input_name, qty_per_cycle in schematic.inputs.items():
            queue.append((input_name, factories * qty_per_cycle * 60.0 / schematic.cycle_minutes))

    for product, direct_rate in targets.items():
        total_rate = demand.units_per_hour.get(product)
        demand.revenue_share[product] = 1.0 if not total_rate else min(1.0, direct_rate / total_rate)

    _compute_duty_cycles(demand, schematics)
    return demand


class _Relay:
    """Один узел цепочки партий-эстафеты — см. _compute_duty_cycles()."""

    __slots__ = ("drain_hours", "cycle_hours", "batch_units")

    def __init__(self, drain_hours: float, cycle_hours: float, batch_units: float):
        self.drain_hours = drain_hours
        self.cycle_hours = cycle_hours
        self.batch_units = batch_units


# Сентинел для входов, которые НЕ ограничивают потребителя: сырьё P0,
# закупленный P1, P1 из basic_industry_facility (исключён из модели) и
# защита от цикла в дереве рецептов. Ведёт себя как непрерывный,
# неограниченный источник — не участвует ни в min(drain), ни в
# max(cycle) у потребителя.
_UNLIMITED = _Relay(drain_hours=float("inf"), cycle_hours=float("inf"), batch_units=float("inf"))


def _compute_duty_cycles(demand: Demand, schematics: dict[str, Schematic]) -> None:
    """
    Партиями-эстафетой: игрок забирает готовую продукцию ОДНИМ рейсом
    только когда причал вышестоящего тира ПОЛНОСТЬЮ переработан, везёт
    всю партию сразу на причал следующего тира — пока новая партия не
    привезена, нижестоящий тир простаивает, даже если сам уже свободен
    (согласовано с пользователем 20.09.2026, взамен более ранней модели
    "непрерывного ручейка" — см. докстринг domain/logistics.py).

    Для продукта X с профилем расхода (own_consumption_profile):
      - drain_full = СУММАРНАЯ ёмкость ВСЕХ реально построенных причалов
        X (`causeway_units` × ёмкость одного) / суммарный расход —
        сколько часов X перерабатывает их все, если бы держали полными
        (потолок, ограниченный физическим размером причалов X). Не одна
        представительная колония — реальное число, заданное рецептами
        (см. docstring domain/logistics.py::causeway_units, 20.09.2026 —
        без этого расчёт ломает баланс масс между тирами с разным
        реальным числом колоний).
      - для каждого произведённого в этом плане входа I (не границы):
        сколько часов X проработает НА ОДНОЙ партии от I (её объём,
        делённый на расход X этого входа в час) — берём МИНИМУМ по
        всем таким входам вместе с drain_full: X не может работать
        дольше, чем позволяет самый скудный вход или собственный
        причал.
      - cycle_X = X не может начать новый цикл раньше, чем закончился
        ЕГО СОБСТВЕННЫЙ простой на довозку (drain_X + downtime), И
        раньше, чем самый медленный поставщик произвёл новую партию
        (МАКСИМУМ циклов входов) — оба условия одновременно.
      - duty_cycle_X = drain_X / cycle_X — та же семантика поля, что и
        раньше (дробь 0..1, дальше по стеку без изменений).

    Сырьё P0, закупленный P1 и P1 из basic_industry_facility (исключён
    из модели, см. own_consumption_profile) — границы: не ограничивают
    ни объёмом партии, ни темпом (_UNLIMITED).
    """
    missing_volumes: set[str] = set()
    resolved: dict[str, _Relay] = {}
    downtime = logistics_downtime_hours()
    capacity = launchpad_capacity_m3()

    def resolve(product: str, visiting: set[str]) -> _Relay:
        if product in resolved:
            return resolved[product]
        if product in visiting:
            # Цикл в дереве рецептов не должен возникать — честно не
            # даём зависнуть, если всё же случится, вместо бесконечной
            # рекурсии (тот же инвариант, что и seen_depth выше).
            return _UNLIMITED
        visiting.add(product)

        schematic = schematics[product]
        total_factories = demand.factories.get(product, 0.0)
        profile, missing = own_consumption_profile(schematic, total_factories)
        missing_volumes.update(missing)

        total_rate = sum(profile.values()) if profile else 0.0
        if total_rate <= 0:
            # P1 (исключён), нет данных об объёме входов схемы целиком,
            # или нулевой расход (не должно случаться на реальных
            # рецептах, но не делить на ноль) — не участвует в модели.
            visiting.discard(product)
            resolved[product] = _UNLIMITED
            return _UNLIMITED

        # Ёмкость причала — на РЕАЛЬНОЕ число построенных причалов
        # продукта (causeway_units), не на одну представительную
        # колонию: у соседних тиров обычно разное число колоний,
        # заданное рецептами, и без этого расчёт ломает баланс масс
        # между ними (см. docstring domain/logistics.py::causeway_units).
        drain_full = capacity * causeway_units(schematic, total_factories) / total_rate

        upstream_drains: list[float] = [drain_full]
        upstream_cycles: list[float] = []
        for input_name, rate in profile.items():
            if input_name not in demand.factories:
                continue  # граница: не ограничивает объёмом партии
            upstream = resolve(input_name, visiting)
            if upstream.batch_units == float("inf"):
                continue  # исключённый узел (P1) — тоже не ограничивает
            volume = volume_of(input_name)
            if volume is None:
                continue  # честный пробел уже учтён в missing_volumes у upstream
            delivered_m3 = upstream.batch_units * volume
            upstream_drains.append(delivered_m3 / rate)
            upstream_cycles.append(upstream.cycle_hours)

        drain = min(upstream_drains)
        cycle = max([drain + downtime] + upstream_cycles)
        batch_units = schematic.output_per_hour * total_factories * drain

        visiting.discard(product)
        node = _Relay(drain_hours=drain, cycle_hours=cycle, batch_units=batch_units)
        resolved[product] = node
        return node

    for product in list(demand.factories):
        resolve(product, set())

    for product, node in resolved.items():
        demand.duty_cycles[product] = 1.0 if node.cycle_hours == float("inf") else node.drain_hours / node.cycle_hours

    demand.missing_volumes = sorted(missing_volumes)


def templates_for_factories(product_tier: str, factory_count: float, factories_per_template: int) -> int:
    """
    Сколько шаблонов нужно под заданное число фабрик.

    Округление вверх: половина шаблона не ставится.
    """
    import math

    if factory_count <= 0:
        return 0
    return int(math.ceil(factory_count / factories_per_template))
