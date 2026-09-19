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

# Минимальный CCU, при котором ОДИНОЧНЫЙ шаблон переработки вообще
# помещается хоть на какую-то планету — 18.09.2026, найдено пользователем:
# ни здесь, ни в domain/planner.py эта проверка раньше не делалась
# вовсе для переработки (в отличие от добычи выше), и персонаж с CCU
# 0-1 мог получить колонию, которая по игре ему физически не помещается.
# Настоящий порог растёт с радиусом планеты (P2/P3 — CCU 2 на мелких,
# 3 на крупных; P4 — CCU 3 везде, см. domain/capacity.py::
# min_ccu_level_that_fits()) — здесь взят САМЫЙ МЯГКИЙ вариант (P2/P3
# на самой мелкой планете), потому что colonies_for()/advise() не знают
# заранее ни системы, ни конкретных планет, только продукты и пул.
# Честная, но неполная защита: ловит явный случай CCU 0-1, не ловит
# «CCU 2 есть, но для P4 или крупной планеты нужен CCU 3» — та проверка
# точная и уже есть на уровне build_plan()/select_factory_sites().
PROCESSING_MIN_CCU = 2

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
    processing_capable_slots: int = 0  # слоты персонажей с CCU >= PROCESSING_MIN_CCU

    @property
    def has_mining_capable(self) -> bool:
        return self.mining_capable_slots > 0

    @property
    def has_processing_capable(self) -> bool:
        return self.processing_capable_slots > 0


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
    # Сколько ЕЩЁ раз можно продублировать ВЕСЬ выбранный набор продуктов
    # целиком (18.09.2026, по прямому запросу пользователя) — 0, если
    # места нет вовсе или verdict не surplus. Не гарантия точного числа
    # (см. _chain_size_for_targets()), а консервативная оценка, как и у
    # additions/alternatives.
    extra_lines_available: int = 0
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
                "processing_capable_slots": self.capacity.processing_capable_slots,
            } if self.capacity else None,
            "missing_colonies": self.missing_colonies,
            "spare_slots": self.spare_slots,
            "alternatives": [s.to_dict() for s in self.alternatives],
            "additions": [s.to_dict() for s in self.additions],
            "extra_lines_available": self.extra_lines_available,
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
        processing_capable_slots=sum(
            c.planet_slots for c in characters
            if c.command_center_upgrades_level >= PROCESSING_MIN_CCU
        ),
    )


def _chain_size(product: str, schematics, recipes, purchase_p1: bool = False) -> tuple[int, int]:
    processing, mining, _ = colonies_for(product, schematics, recipes, purchase_p1=purchase_p1)
    return processing, mining


def _chain_size_for_targets(
    target_products: list[str], schematics, recipes, purchase_p1: bool = False,
) -> tuple[int, int]:
    """
    Сумма `_chain_size()` по всем выбранным продуктам — размер ОДНОЙ
    линии всего набора (18.09.2026, по прямому запросу пользователя:
    «продублировать выбранную цепочку столько раз, сколько позволят
    персонажи», не подбирать второй-третий отдельный продукт).

    Продукт без данных для расчёта — молча пропускается (advise() уже
    честно предупредил о нём отдельно через advice_no_production_data,
    здесь дублировать нечего).
    """
    processing = mining = 0
    for product in target_products:
        try:
            p, m = _chain_size(product, schematics, recipes, purchase_p1=purchase_p1)
        except KeyError:
            continue
        processing += p
        mining += m
    return processing, mining


def _fits(processing: int, mining: int, capacity: PoolCapacity) -> bool:
    """
    Помещается ли цепочка.

    Три проверки, а не одна: общего числа слотов может хватать, но если
    все свободные слоты у персонажей с низкой прокачкой — добывать ими
    нельзя (добывающий шаблон требует Command Center Upgrades IV), а
    перерабатывать нельзя персонажем с CCU 0-1 (18.09.2026, найдено
    пользователем — см. PROCESSING_MIN_CCU выше).
    """
    return (
        processing + mining <= capacity.total_slots
        and mining <= capacity.mining_capable_slots
        and processing <= capacity.processing_capable_slots
    )


def _candidates(
    prices: dict[str, float],
    capacity: PoolCapacity,
    schematics: dict[str, Schematic],
    recipes: RecipeBook,
    exclude: set[str],
    max_colonies: int | None = None,
    purchase_p1: bool = False,
) -> list[Suggestion]:
    """Продукты, помещающиеся в заданный запас слотов, по убыванию выгоды."""
    result: list[Suggestion] = []
    for recipe in recipes:
        if recipe.tier not in TIER_ORDER or recipe.name in exclude:
            continue
        try:
            processing, mining = _chain_size(recipe.name, schematics, recipes, purchase_p1=purchase_p1)
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
    purchase_p1: bool = False,
    lines_per_target: int = 1,
) -> Advice:
    """
    Разобрать, помещается ли задуманное, и предложить выход.

    prices нужны только для ранжирования подсказок. Без них вместимость
    считается всё равно, а порядок предложений становится произвольным —
    об этом говорится в notes, чтобы список не выглядел осмысленнее,
    чем он есть.

    purchase_p1 (18.09.2026, найдено пользователем: план на закупаемом
    P1 использовал только 24 колонии из 84 — подсказка «персонажей
    больше, чем нужно» предлагала продукты до тех пор, пока СЧИТАЛА пул
    заполненным, включая в счёт добывающие колонии, которых в этом
    режиме build_plan() не строит вовсе; реальный запас оставался
    неиспользованным, а подсказка про него уже не сообщала). Без этого
    флага needed_colonies здесь и в build_plan() расходятся всегда,
    когда пользователь выбрал закупку P1 — не только в этом случае.

    lines_per_target (18.09.2026, по прямому запросу пользователя:
    «продублировать выбранную цепочку столько раз, сколько позволят
    персонажи») — уже выбранное число линий (planner.py::PlanRequest.
    lines_per_target), учитывается в needed_colonies/needed_mining,
    чтобы «избыток»/«дефицит» не расходились с тем, что реально построит
    build_plan() при этом значении. Линейное приближение (сумма по
    продуктам, каждый умножен отдельно) — не точное число колоний
    build_plan() (тот считает общий expand_demand() и может округлить
    чуть экономнее), но не хуже: как и у остальных Suggestion, это
    консервативная оценка, не гарантия.
    """
    recipes = load_recipes() if recipes is None else recipes
    schematics = load_schematics() if schematics is None else schematics
    prices = prices or {}
    lines_per_target = max(1, lines_per_target)

    capacity = pool_capacity(characters)
    advice = Advice(status="fits", requested=list(target_products), capacity=capacity)

    if not target_products:
        advice.status = "unknown"
        advice.note("advice_no_targets")
        return advice

    needed_processing = needed_mining = 0
    for product in target_products:
        try:
            processing, mining = _chain_size(product, schematics, recipes, purchase_p1=purchase_p1)
        except KeyError:
            advice.note("advice_no_production_data", product=product)
            continue
        needed_processing += processing * lines_per_target
        needed_mining += mining * lines_per_target

    advice.needed_colonies = needed_processing + needed_mining
    advice.needed_mining = needed_mining

    if not prices:
        advice.note("advice_no_prices")

    if not capacity.has_mining_capable and needed_mining:
        advice.note("advice_no_mining_ccu", ccu=MINER_MIN_CCU)

    if not capacity.has_processing_capable and needed_processing:
        advice.note("advice_no_processing_ccu", ccu=PROCESSING_MIN_CCU)

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
                exclude=set(target_products), max_colonies=spare, purchase_p1=purchase_p1,
            )
            higher = [s for s in advice.additions
                      if TIER_ORDER.get(s.tier, 0) > max(
                          TIER_ORDER.get(recipes.get(p).tier, 0)
                          for p in target_products if recipes.get(p))]
            if higher:
                advice.note("advice_higher_tier_available")

            # Сколько ЕЩЁ раз влезет весь выбранный набор целиком —
            # приоритетнее «взять другой продукт» (пользователь сам об
            # этом попросил вместо ручного подбора второго-третьего
            # продукта). Считается на размере ОДНОЙ линии (без текущего
            # lines_per_target), а сравнивается с уже уменьшенным на
            # needed_processing/needed_mining остатком — так число
            # получается «сколько ЕЩЁ», а не «сколько всего».
            one_line_processing, one_line_mining = _chain_size_for_targets(
                target_products, schematics, recipes, purchase_p1=purchase_p1
            )
            one_line_total = one_line_processing + one_line_mining
            if one_line_total > 0:
                by_total = spare // one_line_total
                by_mining = (
                    (capacity.mining_capable_slots - needed_mining) // one_line_mining
                    if one_line_mining else by_total
                )
                by_processing = (
                    (capacity.processing_capable_slots - needed_processing) // one_line_processing
                    if one_line_processing else by_total
                )
                advice.extra_lines_available = max(0, min(by_total, by_mining, by_processing))
        return advice

    advice.status = "deficit"
    advice.missing_colonies = advice.needed_colonies - capacity.total_slots
    advice.alternatives = _candidates(
        prices, capacity, schematics, recipes, exclude=set(), purchase_p1=purchase_p1,
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
