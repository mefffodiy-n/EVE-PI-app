"""
Оценка выгоды производственных цепочек.

ЧЕМ ЭТО ОТЛИЧАЕТСЯ ОТ ПЕРВОЙ ВЕРСИИ. Там «рекомендация» была максимумом
цены за единицу с ручным исключением Water и Oxygen. Это ничего не
значит: единица Broadcast Node стоит на порядки дороже единицы Water,
но и делается несопоставимо дольше и из большего числа компонентов.
Сравнивать цены за единицу продуктов разных тиров — всё равно что
сравнивать цену слитка и цену гвоздя.

ЧТО СЧИТАЕТСЯ ЗДЕСЬ. Три величины, каждая отвечает на свой вопрос:

  ISK/час          сколько приносит одна производственная линия
                   (один полный шаблон целевого продукта);

  ISK/колония-час  сколько приносит одна планета — главный показатель,
                   потому что планеты и есть ограниченный ресурс:
                   их число упирается в персонажей и их прокачку;

  ISK/персонаж-час то же в пересчёте на человека, если планеты
                   распределены по слотам полностью.

Ограниченный ресурс здесь именно колонии, а не ISK: сырьё добывается
на месте и деньги не стоит, а вот планет и персонажей всегда не хватает.
Поэтому ранжировать цепочки по ISK/час неверно — линия P4 приносит
больше в час, но занимает вдесятеро больше планет. Верхняя строка
рейтинга должна быть по ISK/колония-час.

ЧЕГО НЕ УЧТЕНО, и это важно знать при чтении чисел:
  - налог POCO при вывозе с планеты (колонка есть в planet_industry.csv,
    но объёмы перевозок мы не считаем);
  - стоимость доставки до торгового узла;
  - брокерские сборы и налог с продажи;
  - истощение месторождений (см. assumptions в data/pi_reference.json).
Все они уменьшают выгоду, поэтому числа здесь — верхняя оценка.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from domain.recipes import RecipeBook, load_recipes
from domain.throughput import Schematic, expand_demand, load_schematics

# Сколько фабрик в одном шаблоне верхнего уровня. Совпадает с
# FACTORIES_PER_TEMPLATE в planner.py; продублировано осознанно, чтобы
# оценка выгоды не зависела от планировщика и считалась до построения плана.
FACTORIES_PER_TEMPLATE = {"P2": 12, "P3": 12, "P4": 8}
FACTORIES_PER_MINER = 8


@dataclass
class ChainEconomics:
    """Экономика одной производственной линии."""

    product: str
    tier: str

    units_per_hour: float
    price_per_unit: float | None
    revenue_per_hour: float | None

    processing_colonies: int
    mining_colonies: int

    missing_prices: list[str] = field(default_factory=list)

    @property
    def total_colonies(self) -> int:
        return self.processing_colonies + self.mining_colonies

    @property
    def isk_per_colony_hour(self) -> float | None:
        if self.revenue_per_hour is None or not self.total_colonies:
            return None
        return self.revenue_per_hour / self.total_colonies

    def isk_per_character_hour(self, planet_slots: int = 6) -> float | None:
        """Выгода в пересчёте на персонажа при заданном числе слотов планет."""
        if self.revenue_per_hour is None or not self.total_colonies:
            return None
        characters = math.ceil(self.total_colonies / max(planet_slots, 1))
        return self.revenue_per_hour / characters

    def to_dict(self) -> dict:
        return {
            "product": self.product,
            "tier": self.tier,
            "units_per_hour": round(self.units_per_hour, 2),
            "price_per_unit": self.price_per_unit,
            "revenue_per_hour": self.revenue_per_hour,
            "processing_colonies": self.processing_colonies,
            "mining_colonies": self.mining_colonies,
            "total_colonies": self.total_colonies,
            "isk_per_colony_hour": self.isk_per_colony_hour,
            "isk_per_character_hour": self.isk_per_character_hour(),
            "missing_prices": self.missing_prices,
        }


class MissingPrices(ValueError):
    """Нет цен — считать выгоду не из чего."""


def colonies_for(
    product: str,
    schematics: dict[str, Schematic] | None = None,
    recipes: RecipeBook | None = None,
) -> tuple[int, int, float]:
    """
    Сколько колоний требует одна линия продукта и сколько единиц в час она даёт.

    Возвращает (планет под переработку, планет под добычу, единиц в час).

    Считается тем же разворотом дерева, что и в планировщике, поэтому
    оценка выгоды и построенный план не разойдутся в числе планет.
    """
    schematics = load_schematics() if schematics is None else schematics
    recipes = load_recipes() if recipes is None else recipes

    recipe = recipes.get(product)
    schematic = schematics.get(product)
    if recipe is None or schematic is None:
        raise KeyError(f"Нет данных для продукта {product}")

    factories = FACTORIES_PER_TEMPLATE.get(recipe.tier)
    if factories is None:
        raise KeyError(f"Продукт {product} не является целевым (тир {recipe.tier})")

    units_per_hour = schematic.output_per_hour * factories
    demand = expand_demand({product: units_per_hour}, schematics=schematics, recipes=recipes)

    processing = 0
    mining = 0
    for name, count in demand.factories.items():
        tier = recipes.get(name).tier if recipes.get(name) else None
        if tier == "P1":
            mining += math.ceil(count / FACTORIES_PER_MINER)
        elif tier in FACTORIES_PER_TEMPLATE:
            processing += math.ceil(count / FACTORIES_PER_TEMPLATE[tier])

    return processing, mining, units_per_hour


def evaluate(
    product: str,
    prices: dict[str, float],
    schematics: dict[str, Schematic] | None = None,
    recipes: RecipeBook | None = None,
) -> ChainEconomics:
    """
    Посчитать экономику одной линии.

    prices: {имя продукта: цена за единицу}. Отсутствующая цена не
    выдумывается — она попадает в missing_prices, а выручка остаётся None.
    """
    recipes = load_recipes() if recipes is None else recipes
    processing, mining, units = colonies_for(product, schematics, recipes)
    recipe = recipes.get(product)

    price = prices.get(product)
    revenue = None if price is None else price * units

    return ChainEconomics(
        product=product,
        tier=recipe.tier,
        units_per_hour=units,
        price_per_unit=price,
        revenue_per_hour=revenue,
        processing_colonies=processing,
        mining_colonies=mining,
        missing_prices=[] if price is not None else [product],
    )


def rank(
    prices: dict[str, float],
    products: list[str] | None = None,
    schematics: dict[str, Schematic] | None = None,
    recipes: RecipeBook | None = None,
    planet_slots: int = 6,
) -> list[ChainEconomics]:
    """
    Ранжировать цепочки по ISK на колонию в час.

    Именно по колония-часу, а не по ISK/час: ограниченный ресурс —
    планеты и персонажи, а не время. Линия, приносящая больше всех
    в час, но занимающая вдвое больше планет, хуже.
    """
    recipes = load_recipes() if recipes is None else recipes
    schematics = load_schematics() if schematics is None else schematics

    if products is None:
        products = [r.name for r in recipes if r.tier in FACTORIES_PER_TEMPLATE]

    result: list[ChainEconomics] = []
    for name in products:
        try:
            result.append(evaluate(name, prices, schematics, recipes))
        except KeyError:
            continue

    # Сначала те, у кого есть цена, по убыванию выгоды на колонию.
    # Без цены — в конце: скрывать их нельзя, иначе непонятно,
    # почему продукт пропал из списка.
    result.sort(
        key=lambda c: (c.isk_per_colony_hour is not None, c.isk_per_colony_hour or 0.0),
        reverse=True,
    )
    return result
