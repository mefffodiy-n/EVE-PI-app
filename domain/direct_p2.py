"""
Прямое производство P2 на добывающей планете.

ЧТО ЭТО. Обычно P2 делается в две ступени на разных планетах: добывающие
колонии превращают сырьё в P1, груз везут в домашнюю систему, там завод
делает P2. Если же на одной планете есть ОБА нужных вида сырья, всю
цепочку можно уместить на ней: два экстрактора, basic-фабрики под оба P1
и advanced-фабрики под сам P2. Возить между планетами тогда нечего —
с планеты уходит уже готовый P2.

ОТКУДА ВЗЯТ СОСТАВ. Готового шаблона для такой застройки в наборе
DalShooth нет: там отдельно майнеры и отдельно заводы. Поэтому состав
считается здесь из стоимостей отдельных структур. Это не догадка:
те же стоимости воспроизводят итоги всех пяти шаблонов набора точно
(проверяется в tests/test_capacity.py). Но и не выписка из источника,
поэтому расчёт стоит перепроверить в игре перед серьёзной постройкой.

ЧТО ОГРАНИЧИВАЕТ. Powergrid, а не CPU: экстрактор стоит 2600 PG против
400 CPU, и два экстрактора съедают почти треть бюджета ещё до первой
фабрики. При Command Center Upgrades V помещается три advanced-фабрики,
при IV — две.

СКОЛЬКО ЭТО ЭКОНОМИТ. По числу колоний — примерно ничего: тот же выпуск
раздельным путём требует столько же планет. Экономится ЛОГИСТИКА: не
нужно возить P1 с добывающих планет на завод. Обманывать себя тут не
стоит, и в подсказках это сказано прямо.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from domain.capacity import (
    command_center_capacity,
    extractor_heads_load,
    link_load,
    structures_load,
)
from domain.recipes import RecipeBook, load_recipes
from domain.throughput import Schematic, load_schematics

# Сколько голов экстрактора кормят одну basic-фабрику.
# Выведено из 00-шаблона майнера: 10 голов на 8 фабрик. README источника
# подтверждает баланс отдельно — 8 фабрик требуют 1 152 000 единиц P0
# в сутки, и столько даёт один экстрактор с десятью головами.
HEADS_PER_BASIC = 10 / 8
MAX_HEADS_PER_EXTRACTOR = 10

# Больше шести advanced-фабрик не поместится ни при каком уровне,
# перебирать дальше незачем.
MAX_ADVANCED = 6


@dataclass(frozen=True)
class DirectLayout:
    """Застройка планеты под прямое производство P2."""

    product: str
    advanced: int
    basic: int
    extractors: int
    heads_each: int
    links: int
    cpu_used: float
    pg_used: float
    cpu_total: float
    pg_total: float
    units_per_hour: float

    @property
    def cpu_percent(self) -> float:
        return round(100 * self.cpu_used / self.cpu_total, 1) if self.cpu_total else 0.0

    @property
    def pg_percent(self) -> float:
        return round(100 * self.pg_used / self.pg_total, 1) if self.pg_total else 0.0

    @property
    def fits(self) -> bool:
        return self.cpu_used <= self.cpu_total and self.pg_used <= self.pg_total

    def structures_detail(self) -> list[dict]:
        """Состав для полосы иконок на дашборде, в игровом порядке."""
        return [
            {"kind": "command_center", "count": 1},
            {"kind": "launchpad", "count": 1},
            {"kind": "extractor_control_unit", "count": self.extractors},
            {"kind": "basic_industry_facility", "count": self.basic},
            {"kind": "advanced_industry_facility", "count": self.advanced},
        ]

    def to_dict(self) -> dict:
        return {
            "product": self.product,
            "advanced": self.advanced,
            "basic": self.basic,
            "extractors": self.extractors,
            "heads_each": self.heads_each,
            "links": self.links,
            "cpu_percent": self.cpu_percent,
            "pg_percent": self.pg_percent,
            "units_per_hour": round(self.units_per_hour, 2),
        }


def _layout(advanced: int, radius_km: float, ccu_level: int,
            units_per_hour_each: float, product: str) -> DirectLayout:
    """Собрать вариант застройки с заданным числом advanced-фабрик."""
    basic = 2 * advanced          # по одной basic на каждый из двух входов
    heads_each = min(MAX_HEADS_PER_EXTRACTOR, math.ceil(HEADS_PER_BASIC * advanced))

    # Пины: командный центр, причал, два экстрактора, фабрики.
    # Линков на один меньше, чем пинов: дерево, а не кольцо.
    pins = 1 + 1 + 2 + basic + advanced
    links = pins - 1

    structures = structures_load({
        "extractor_control_unit": 2,
        "basic_industry_facility": basic,
        "advanced_industry_facility": advanced,
        "launchpad": 1,
    })
    heads = extractor_heads_load(heads_each * 2)
    wiring = link_load(links, radius_km)
    capacity = command_center_capacity(ccu_level)

    return DirectLayout(
        product=product,
        advanced=advanced,
        basic=basic,
        extractors=2,
        heads_each=heads_each,
        links=links,
        cpu_used=structures.cpu + heads.cpu + wiring.cpu,
        pg_used=structures.pg + heads.pg + wiring.pg,
        cpu_total=capacity.cpu,
        pg_total=capacity.pg,
        units_per_hour=units_per_hour_each * advanced,
    )


def best_layout(
    product: str,
    radius_km: float,
    ccu_level: int,
    schematics: dict[str, Schematic] | None = None,
) -> DirectLayout | None:
    """
    Максимальная застройка, помещающаяся на планету.

    Возвращает None, если не помещается даже одна advanced-фабрика:
    на крупной планете линки съедают бюджет, а при низкой прокачке
    его просто мало.
    """
    schematics = load_schematics() if schematics is None else schematics
    schematic = schematics.get(product)
    if schematic is None:
        return None

    best: DirectLayout | None = None
    for advanced in range(1, MAX_ADVANCED + 1):
        candidate = _layout(advanced, radius_km, ccu_level,
                            schematic.output_per_hour, product)
        if not candidate.fits:
            break
        best = candidate
    return best


def direct_candidates(
    product: str,
    planets,
    constellations: list[str],
    ccu_level: int = 5,
    recipes: RecipeBook | None = None,
    schematics: dict[str, Schematic] | None = None,
) -> list[dict]:
    """
    Планеты, на которых P2 можно делать целиком, и что на них поместится.

    Требование к планете жёсткое: на ней должны быть ОБА вида сырья.
    Таких немного, поэтому список обычно короткий — это нормально,
    прямое производство и не задумано как основной путь.
    """
    recipes = load_recipes() if recipes is None else recipes
    schematics = load_schematics() if schematics is None else schematics

    recipe = recipes.get(product)
    if recipe is None or recipe.tier != "P2":
        return []

    # Входы P2 — это P1; нам нужно сырьё, из которого они делаются.
    raws: list[str] = []
    for p1_name in recipe.inputs:
        p1 = recipes.get(p1_name)
        if p1 is None or not p1.source:
            return []
        raws.append(p1.source)
    if len(raws) != 2:
        return []

    frame = planets.planets_with_all_resources(raws, constellations)
    if frame is None or frame.empty:
        return []

    from domain.planets import RADIUS_COLUMN

    results: list[dict] = []
    for _, row in frame.iterrows():
        radius = row.get(RADIUS_COLUMN)
        if radius is None or radius != radius:
            continue
        layout = best_layout(product, float(radius), ccu_level, schematics)
        if layout is None:
            continue
        results.append({
            "system": str(row.get("System", "")),
            "planet": str(row.get("Planet", "")),
            "planet_type": str(row.get("Type", "")),
            "radius_km": float(radius),
            "constellation": str(row.get("Constellation", "")),
            "raws": raws,
            "layout": layout,
        })

    # Больше advanced-фабрик на планету — меньше планет на тот же выпуск.
    results.sort(key=lambda r: (-r["layout"].advanced, r["radius_km"]))
    return results


def compare_with_split(product: str, layout: DirectLayout,
                       schematics: dict[str, Schematic] | None = None) -> dict:
    """
    Сравнить прямой путь с раздельным на одинаковом выпуске.

    Считается честно, без подгонки в пользу нового способа: раздельный
    путь берёт один завод на 12 advanced-фабрик плюс добывающие колонии,
    кормящие их обоими видами P1.
    """
    FACTORIES_IN_TEMPLATE = 12
    BASIC_PER_MINER = 8

    # Раздельно: завод на 12 фабрик; каждой нужен свой поток двух P1,
    # то есть по 12 basic-фабрик на каждый вход.
    split_processing = 1
    split_mining = math.ceil(FACTORIES_IN_TEMPLATE * 2 / BASIC_PER_MINER)
    split_colonies = split_processing + split_mining
    split_advanced = FACTORIES_IN_TEMPLATE

    # Напрямую: столько колоний, чтобы набрать те же advanced-фабрики.
    direct_colonies = math.ceil(split_advanced / layout.advanced)

    return {
        "advanced_compared": split_advanced,
        "split_colonies": split_colonies,
        "direct_colonies": direct_colonies,
        "colonies_saved": split_colonies - direct_colonies,
        "hauling_removed": True,
        "note": (
            "По числу колоний прямой путь обычно не выигрывает. Его смысл "
            "в логистике: с планеты уходит готовый P2, и возить P1 между "
            "планетами не нужно вовсе."
        ),
    }
