"""
Помещается ли цепочка в пул персонажей, и что делать, если нет.

ЗАЧЕМ. Планировщик умеет сказать «не хватило персонажей», но не умеет
подсказать выход. А выход почти всегда есть: цепочка ниже тиром, другой
продукт того же тира, или наоборот — если персонажей больше, чем нужно,
то цепочка выше тиром либо вторая линия рядом.

ЧЕТЫРЕ СЛУЧАЯ, которые здесь разбираются:

  fits      цепочка помещается — подсказки не нужны;
  deficit   персонажей не хватает → предлагаем продукты, которые
            поместятся, отсортированные по выгоде. Могут быть ниже
            тиром: лучше делать P2 непрерывно, чем P4 с простоями;
  surplus   персонажей больше, чем нужно → предлагаем цепочку выше
            тиром, которая займёт пул целиком, либо дополнительные
            линии того же или меньшего тира;
  unknown   нет цен — ранжировать по выгоде не из чего, но вместимость
            посчитать всё равно можно.

ЧЕГО ЗДЕСЬ НЕТ. Решения за пользователя. Модуль считает и предлагает;
что выбрать — его дело. Если он проигнорирует подсказку, планировщик
построит план как просили и честно скажет о дефиците (или пустит
свободных персонажей в добычу с пометкой «избыток»).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from domain.plan_messages import render as render_message
from domain.profit import ChainEconomics, colonies_for, evaluate
from domain.recipes import RecipeBook, load_recipes
from domain.throughput import Schematic, load_schematics

# Минимальный уровень Command Center Upgrades для добывающего шаблона.
# Расчётное ограничение: при CCU III он перегружает PG на планете
# любого размера (см. _derived_min_ccu в data/pi_reference.json).
MINER_MIN_CCU = 4

# Сколько вариантов показывать. Больше пяти — это уже не подсказка,
# а второй список продуктов, в котором снова надо выбирать.
MAX_SUGGESTIONS = 5

TIER_ORDER = {"P2": 2, "P3": 3, "P4": 4}


@dataclass
class PoolCapacity:
    """Что может вместить пул персонажей."""

    characters: int
    total_slots: int
    mining_capable_slots: int   # слоты персонажей с CCU >= MINER_MIN_CCU
    double_template_slots: int  # слоты персонажей с CCU V

    @property
    def has_mining_capable(self) -> bool:
        return self.mining_capable_slots > 0


@dataclass
class Suggestion:
    product: str
    tier: str
    colonies: int
    processing: int
    mining: int
    isk_per_colony_hour: float | None
    isk_per_hour: float | None
    leftover_slots: int

    def to_dict(self) -> dict:
        return {
            "product": self.product,
            "tier": self.tier,
            "colonies": self.colonies,
            "processing": self.processing,
            "mining": self.mining,
            "isk_per_colony_hour": self.isk_per_colony_hour,
            "isk_per_hour": self.isk_per_hour,
            "leftover_slots": self.leftover_slots,
        }


@dataclass
class Advice:
    status: str                    # fits | deficit | surplus | unknown
    requested: list[str] = field(default_factory=list)
    needed_colonies: int = 0
    needed_mining: int = 0
    capacity: PoolCapacity | None = None
    missing_colonies: int = 0
    spare_slots: int = 0
    alternatives: list[Suggestion] = field(default_factory=list)
    additions: list[Suggestion] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)       # рендер по-русски, для тестов
    note_data: list[dict] = field(default_factory=list)  # {code, ...} для перевода

    def note(self, code: str, **params) -> None:
        entry = {"code": code, **params}
        self.note_data.append(entry)
        self.notes.append(render_message(entry, "ru"))

    def to_dict(self, lang: str = "ru") -> dict:
        # Ключ называется verdict, а не status: в ответах API status
        # уже занят под success/error, и совпадение имён их столкнёт.
        return {
            "verdict": self.status,
            "requested": self.requested,
            "needed_colonies": self.needed_colonies,
            "needed_mining": self.needed_mining,
            "capacity": {
                "characters": self.capacity.characters,
                "total_slots": self.capacity.total_slots,
                "mining_capable_slots": self.capacity.mining_capable_slots,
                "double_template_slots": self.capacity.double_template_slots,
            } if self.capacity else None,
            "missing_colonies": self.missing_colonies,
            "spare_slots": self.spare_slots,
            "alternatives": [s.to_dict() for s in self.alternatives],
            "additions": [s.to_dict() for s in self.additions],
            "notes": self.notes if lang == "ru" or not self.note_data
                     else [render_message(e, lang) for e in self.note_data],
        }


def pool_capacity(characters: list) -> PoolCapacity:
    """Сколько колоний вытянет пул и с какими ограничениями по прокачке."""
    return PoolCapacity(
        characters=len(characters),
        total_slots=sum(c.planet_slots for c in characters),
        mining_capable_slots=sum(
            c.planet_slots for c in characters
            if c.command_center_upgrades_level >= MINER_MIN_CCU
        ),
        double_template_slots=sum(
            c.planet_slots for c in characters
            if c.command_center_upgrades_level >= 5
        ),
    )


def _chain_size(product: str, schematics, recipes) -> tuple[int, int]:
    processing, mining, _ = colonies_for(product, schematics, recipes)
    return processing, mining


def _fits(processing: int, mining: int, capacity: PoolCapacity) -> bool:
    """
    Помещается ли цепочка.

    Две проверки, а не одна: общего числа слотов может хватать, но если
    все свободные слоты у персонажей с низкой прокачкой, добывать ими
    нельзя — добывающий шаблон требует Command Center Upgrades IV.
    """
    return (
        processing + mining <= capacity.total_slots
        and mining <= capacity.mining_capable_slots
    )


def _candidates(
    prices: dict[str, float],
    capacity: PoolCapacity,
    schematics: dict[str, Schematic],
    recipes: RecipeBook,
    exclude: set[str],
    max_colonies: int | None = None,
) -> list[Suggestion]:
    """Продукты, помещающиеся в заданный запас слотов, по убыванию выгоды."""
    result: list[Suggestion] = []
    for recipe in recipes:
        if recipe.tier not in TIER_ORDER or recipe.name in exclude:
            continue
        try:
            processing, mining = _chain_size(recipe.name, schematics, recipes)
        except KeyError:
            continue

        total = processing + mining
        if max_colonies is not None and total > max_colonies:
            continue
        if not _fits(processing, mining, capacity):
            continue

        economics: ChainEconomics | None = None
        try:
            economics = evaluate(recipe.name, prices, schematics, recipes)
        except KeyError:
            pass

        result.append(Suggestion(
            product=recipe.name,
            tier=recipe.tier,
            colonies=total,
            processing=processing,
            mining=mining,
            isk_per_colony_hour=economics.isk_per_colony_hour if economics else None,
            isk_per_hour=economics.revenue_per_hour if economics else None,
            leftover_slots=(max_colonies - total) if max_colonies is not None
                           else capacity.total_slots - total,
        ))

    # Сначала те, у кого есть цена, по убыванию отдачи с планеты.
    # Без цены — в конце: скрывать нельзя, иначе непонятно, куда делись.
    result.sort(
        key=lambda s: (s.isk_per_colony_hour is not None, s.isk_per_colony_hour or 0.0),
        reverse=True,
    )
    return result[:MAX_SUGGESTIONS]


def advise(
    target_products: list[str],
    characters: list,
    prices: dict[str, float] | None = None,
    schematics: dict[str, Schematic] | None = None,
    recipes: RecipeBook | None = None,
) -> Advice:
    """
    Разобрать, помещается ли задуманное, и предложить выход.

    prices нужны только для ранжирования подсказок. Без них вместимость
    считается всё равно, а порядок предложений становится произвольным —
    об этом говорится в notes, чтобы список не выглядел осмысленнее,
    чем он есть.
    """
    recipes = load_recipes() if recipes is None else recipes
    schematics = load_schematics() if schematics is None else schematics
    prices = prices or {}

    capacity = pool_capacity(characters)
    advice = Advice(status="fits", requested=list(target_products), capacity=capacity)

    if not target_products:
        advice.status = "unknown"
        advice.note("advice_no_targets")
        return advice

    needed_processing = needed_mining = 0
    for product in target_products:
        try:
            processing, mining = _chain_size(product, schematics, recipes)
        except KeyError:
            advice.note("advice_no_production_data", product=product)
            continue
        needed_processing += processing
        needed_mining += mining

    advice.needed_colonies = needed_processing + needed_mining
    advice.needed_mining = needed_mining

    if not prices:
        advice.note("advice_no_prices")

    if not capacity.has_mining_capable and needed_mining:
        advice.note("advice_no_mining_ccu", ccu=MINER_MIN_CCU)

    fits = _fits(needed_processing, needed_mining, capacity)

    if fits:
        spare = capacity.total_slots - advice.needed_colonies
        advice.spare_slots = spare
        # Запас меньше самой компактной цепочки — это не избыток,
        # а нормальный остаток. Предлагать нечего.
        if spare >= 4:
            advice.status = "surplus"
            advice.additions = _candidates(
                prices, capacity, schematics, recipes,
                exclude=set(target_products), max_colonies=spare,
            )
            higher = [s for s in advice.additions
                      if TIER_ORDER.get(s.tier, 0) > max(
                          TIER_ORDER.get(recipes.get(p).tier, 0)
                          for p in target_products if recipes.get(p))]
            if higher:
                advice.note("advice_higher_tier_available")
        return advice

    advice.status = "deficit"
    advice.missing_colonies = advice.needed_colonies - capacity.total_slots
    advice.alternatives = _candidates(
        prices, capacity, schematics, recipes, exclude=set(),
    )
    if not advice.alternatives:
        advice.note("advice_nothing_fits")
    return advice


def surplus_mining_targets(
    target_products: list[str],
    planets,
    constellations: list[str],
    recipes: RecipeBook | None = None,
    schematics: dict[str, Schematic] | None = None,
) -> list[str]:
    """
    Какое сырьё добывать свободными персонажами.

    Берётся сырьё ИЗ ВЫБРАННОЙ цепочки, а не произвольное: избыточная
    добыча должна питать то же производство, иначе она просто копит
    ненужное. Порядок — по убыванию дефицитности: первым то, чего в
    выбранных констелляциях меньше всего, потому что именно оно
    ограничивает выпуск.
    """
    recipes = load_recipes() if recipes is None else recipes
    schematics = load_schematics() if schematics is None else schematics

    from domain.throughput import expand_demand

    p1_products: set[str] = set()
    for product in target_products:
        recipe = recipes.get(product)
        schematic = schematics.get(product)
        if recipe is None or schematic is None:
            continue
        demand = expand_demand(
            {product: schematic.output_per_hour}, schematics=schematics, recipes=recipes
        )
        for name in demand.factories:
            entry = recipes.get(name)
            if entry and entry.tier == "P1":
                p1_products.add(name)

    raw_by_product = {
        name: recipes.get(name).source
        for name in p1_products
        if recipes.get(name) and recipes.get(name).source
    }
    if not raw_by_product:
        return []

    ranked = planets.resource_scarcity(list(raw_by_product.values()), constellations)
    raw_to_p1 = {raw: p1 for p1, raw in raw_by_product.items()}
    return [raw_to_p1[row["resource"]] for row in ranked if row["resource"] in raw_to_p1]
