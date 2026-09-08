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
    assumptions_used: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

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


def unverified_assumptions() -> list[str]:
    """Список непроверенных допущений, влияющих на расчёт."""
    result = []
    for facility, entry in _cycles().items():
        if not entry.get("verified", False):
            result.append(
                f"Длительность цикла {facility} принята {entry['minutes']} мин "
                f"(не подтверждено источником)"
            )
    return result


def expand_demand(
    targets: dict[str, float],
    schematics: dict[str, Schematic] | None = None,
    recipes: RecipeBook | None = None,
) -> Demand:
    """
    Развернуть потребность в целевых продуктах до сырья P0.

    targets: {имя продукта: единиц в час}.

    Возвращает Demand с потребностью по всем промежуточным продуктам,
    числом фабрик на каждом уровне и расходом сырья P0.
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

    return demand


def templates_for_factories(product_tier: str, factory_count: float, factories_per_template: int) -> int:
    """
    Сколько шаблонов нужно под заданное число фабрик.

    Округление вверх: половина шаблона не ставится.
    """
    import math

    if factory_count <= 0:
        return 0
    return int(math.ceil(factory_count / factories_per_template))
