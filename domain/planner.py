"""
Построение производственного плана: распределение персонажей по планетам.

Заменяет calculate_plan() из main.py (v1). Отличия:

  - никаких вызовов ESI и БД внутри: planner принимает готовые структуры
    и возвращает план. Чтение из БД и синхронизация — уровнем выше;
  - CPU/PG считаются через domain.capacity (структуры шаблона + головы
    экстракторов + линки от радиуса), а не константами 95/85/75;
  - число фабрик выводится из схем производства, извлечённых из самих
    шаблонов, а не из констант 16/24/12;
  - площадки под переработку подбираются через domain.factory_site:
    только Barren/Temperate, по возрастанию радиуса, с предупреждением
    вместо тихого отката на одиночный шаблон;
  - все непроверенные допущения возвращаются в результате, чтобы
    интерфейс мог их показать, а не выдавал расчёт за точный.

ФОРМАТ СТРОКИ ПЛАНА зафиксирован существующим фронтендом
(web/index.html читает поля id, char_id, character, role, planet,
system, cc_type, res_out, structures, type_id, hours_left).
Дополнительные поля фронт игнорирует, поэтому план несёт и их —
они нужны экспорту и будущему интерфейсу.

Про hours_left: значение None означает «неизвестно», и фронтенд обязан
показывать именно это. Сейчас index.html:475 дорисовывает его через
Math.random() и выводит как настоящий таймер — убрать одновременно с
появлением реальных данных из workers/jobs/sync_colony_status.py (Фаза 3).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from domain.capacity import (
    UnsupportedSetup,
    calculate_colony_load,
    load_templates,
    min_ccu_level_that_fits,
)
from domain.factory_site import TEMPLATE_BY_TIER, SiteSelection, select_factory_sites
from domain.planets import RADIUS_COLUMN, PlanetBook
from domain.recipes import RecipeBook, load_recipes
from domain.throughput import Demand, Schematic, expand_demand, load_schematics

# Сколько фабрик содержит один шаблон каждого вида.
# Расчётный минимум для добывающего шаблона: при CCU III он перегружает
# PG на планете любого размера (см. _derived_min_ccu в pi_reference.json).
MINER_MIN_CCU = 4

FACTORIES_PER_TEMPLATE = {
    "miner_00": 8,            # basic-фабрики
    "p2p3_1factory": 12,      # advanced
    "p2p3_2factory": 24,
    "p4_1factory": 8,         # high-tech
    "p4_2factory": 16,
}


@dataclass(frozen=True)
class CharacterSlot:
    """
    Персонаж, доступный для назначения на планеты.

    На Фазе 1 источник — scripts/seed_dev_characters.py (dev-заглушки).
    В Фазе 3 те же поля придут из sync_character_skills (реальный ESI).
    Планировщик о происхождении данных не знает и знать не должен.
    """

    character_id: int
    name: str
    command_center_upgrades_level: int      # 0-5
    interplanetary_consolidation_level: int  # 0-5

    @property
    def planet_slots(self) -> int:
        """
        Сколько планет может держать персонаж.

        ДОПУЩЕНИЕ (см. assumptions.planet_slots_per_character в
        data/pi_reference.json): базовая одна планета плюс по одной за
        уровень Interplanetary Consolidation. В материалах источника
        это прямо не указано — проверить по описанию скилла в игре.
        """
        return self.interplanetary_consolidation_level + 1

    @property
    def can_place_two_templates(self) -> bool:
        return self.command_center_upgrades_level >= 5


@dataclass(frozen=True)
class PlanRequest:
    constellations: list[str]
    factory_system: str
    target_products: list[str]

    # Сколько шаблонов верхнего уровня на каждый целевой продукт.
    # Одна «линия» = один полный шаблон целевого продукта.
    lines_per_target: int = 1

    include_direct_p2: bool = False           # Фаза 4: сценарий прямого R0 -> P2
    allow_single_template_fallback: bool = False


@dataclass
class PlanRow:
    """Одна строка плана — одна планета одного персонажа."""

    id: str
    char_id: int
    character: str
    role: str                 # содержит «Добыча» для добывающих — фронт это проверяет
    planet: str
    system: str
    constellation: str
    cc_type: str              # например «1x Barren Command Center»
    res_out: str
    res_in: str | None
    structures: str           # строка с числом фабрик — фронт достаёт из неё число
    type_id: int | None
    template_key: str
    template_count: int
    planet_type: str
    planet_radius_km: float
    cpu_percent: float
    pg_percent: float
    hours_left: float | None = None  # None = неизвестно; НЕ подставлять случайное

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "char_id": self.char_id,
            "character": self.character,
            "role": self.role,
            "planet": self.planet,
            "system": self.system,
            "constellation": self.constellation,
            "cc_type": self.cc_type,
            "res_out": self.res_out,
            "res_in": self.res_in,
            "structures": self.structures,
            "type_id": self.type_id,
            "template_key": self.template_key,
            "template_count": self.template_count,
            "planet_type": self.planet_type,
            "planet_radius_km": self.planet_radius_km,
            "cpu_percent": self.cpu_percent,
            "pg_percent": self.pg_percent,
            "hours_left": self.hours_left,
        }


@dataclass
class PlanResult:
    rows: list[PlanRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    demand: Demand | None = None

    # Подбор площадок держим отдельно: эти предупреждения требуют
    # РЕШЕНИЯ пользователя (сменить систему или согласиться на один
    # шаблон), а не просто информируют.
    site_selections: list[SiteSelection] = field(default_factory=list)

    @property
    def needs_user_decision(self) -> bool:
        return any(s.warnings and not s.assignments for s in self.site_selections)

    def to_dict(self) -> dict:
        return {
            "data": [r.to_dict() for r in self.rows],
            "warning": " ".join(self.warnings) if self.warnings else None,
            "warnings": self.warnings,
            "assumptions": self.assumptions,
            "needs_user_decision": self.needs_user_decision,
            "site_warnings": [w for s in self.site_selections for w in s.warnings],
        }


class _CharacterPool:
    """Раздаёт слоты планет, соблюдая ограничение по числу планет."""

    def __init__(self, characters: list[CharacterSlot]):
        # Сначала прокачанные: им доступны двойные шаблоны, и разумно
        # занять их именно переработкой, где это даёт выигрыш.
        self._characters = sorted(
            characters,
            key=lambda c: (-c.command_center_upgrades_level, -c.interplanetary_consolidation_level),
        )
        self._used: dict[int, int] = {c.character_id: 0 for c in characters}

    def take(
        self,
        require_ccu5: bool = False,
        min_ccu: int = 0,
        prefer_least_skilled: bool = False,
    ) -> CharacterSlot | None:
        """
        Выдать слот планеты.

        prefer_least_skilled=True для добычи: добывающему шаблону хватает
        Command Center Upgrades IV, и занимать им персонажа с CCU V
        расточительно — только он может ставить два перерабатывающих
        шаблона на планету, что вдвое сокращает число нужных планет.
        """
        order = list(reversed(self._characters)) if prefer_least_skilled else self._characters
        for character in order:
            if require_ccu5 and not character.can_place_two_templates:
                continue
            if character.command_center_upgrades_level < min_ccu:
                continue
            if self._used[character.character_id] < character.planet_slots:
                self._used[character.character_id] += 1
                return character
        return None

    @property
    def free_slots(self) -> int:
        return sum(c.planet_slots - self._used[c.character_id] for c in self._characters)

    @property
    def total_slots(self) -> int:
        return sum(c.planet_slots for c in self._characters)


def _tier_of(product: str, recipes: RecipeBook) -> str | None:
    recipe = recipes.get(product)
    return recipe.tier if recipe else None


def _processing_tier_key(tier: str) -> str:
    return "P4" if tier == "P4" else "P2_P3"


def build_plan(
    request: PlanRequest,
    characters: list[CharacterSlot],
    recipes: RecipeBook | None = None,
    planets: PlanetBook | None = None,
    schematics: dict[str, Schematic] | None = None,
) -> PlanResult:
    """
    Построить план распределения персонажей по добыче и переработке.

    Порядок работы:
      1. развернуть целевые продукты до сырья через domain.throughput;
      2. перевести число фабрик в число шаблонов;
      3. разместить перерабатывающие шаблоны в домашней системе через
         domain.factory_site (Barren/Temperate, по возрастанию радиуса);
      4. разместить добывающие шаблоны на планетах с нужным сырьём;
      5. раздать планеты персонажам с учётом их слотов.
    """
    recipes = load_recipes() if recipes is None else recipes
    schematics = load_schematics() if schematics is None else schematics
    result = PlanResult()

    if planets is None:
        raise ValueError("Не передан PlanetBook — планировщик не читает файлы сам")

    if not schematics:
        result.warnings.append(
            "Нет данных о производительности: файл data/schematics.json отсутствует. "
            "Сформируйте его командой `python -m scripts.extract_schematics --write` — "
            "числа будут извлечены из ваших шаблонов, без обращения к ESI."
        )
        return result

    # 1. Потребность. Одна линия = один полный шаблон целевого продукта.
    targets: dict[str, float] = {}
    for product in request.target_products:
        tier = _tier_of(product, recipes)
        if tier is None:
            result.warnings.append(f"Неизвестный продукт: {product}")
            continue
        schematic = schematics.get(product)
        if schematic is None:
            result.warnings.append(
                f"Для продукта {product} нет шаблона в data/templates/ — "
                f"расчёт по нему невозможен."
            )
            continue
        template_key = TEMPLATE_BY_TIER[_processing_tier_key(tier)][1]
        factories = FACTORIES_PER_TEMPLATE[template_key]
        targets[product] = schematic.output_per_hour * factories * request.lines_per_target

    if not targets:
        result.warnings.append("Не удалось построить план: нет пригодных целевых продуктов.")
        return result

    demand = expand_demand(targets, schematics=schematics, recipes=recipes)
    result.demand = demand
    result.assumptions = list(demand.assumptions_used)
    result.assumptions.append(
        "Число планет на персонажа принято как Interplanetary Consolidation + 1 "
        "(не подтверждено источником)"
    )
    for missing in demand.missing:
        result.warnings.append(f"Нет схемы производства для {missing} — цепочка оборвана.")

    pool = _CharacterPool(characters)
    row_counter = 0

    # 2-3. Переработка: группируем по тиру и размещаем в домашней системе.
    processing: dict[str, list[tuple[str, float]]] = {"P2_P3": [], "P4": []}
    for product, factory_count in sorted(demand.factories.items()):
        tier = _tier_of(product, recipes)
        if tier in (None, "P1"):
            continue
        processing[_processing_tier_key(tier)].append((product, factory_count))

    for tier_key, items in processing.items():
        if not items:
            continue
        single_key = TEMPLATE_BY_TIER[tier_key][1]
        per_template = FACTORIES_PER_TEMPLATE[single_key]
        templates_needed = sum(
            int(math.ceil(count / per_template)) for _, count in items
        )

        selection = select_factory_sites(
            planets,
            request.factory_system,
            tier_key,
            ccu_level=max((c.command_center_upgrades_level for c in characters), default=0),
            templates_needed=templates_needed,
            allow_single_fallback=request.allow_single_template_fallback,
        )
        result.site_selections.append(selection)
        result.warnings.extend(selection.warnings)

        # Раздаём назначенные площадки под конкретные продукты.
        queue: list[str] = []
        for product, count in items:
            queue.extend([product] * int(math.ceil(count / per_template)))

        for assignment in selection.assignments:
            for _ in range(assignment.template_count):
                if not queue:
                    break
                product = queue.pop(0)
                character = pool.take(require_ccu5=assignment.template_count == 2)
                if character is None:
                    result.warnings.append(
                        "Не хватило слотов планет у персонажей для переработки."
                    )
                    break
                row_counter += 1
                result.rows.append(
                    PlanRow(
                        id=f"row-{row_counter}",
                        char_id=character.character_id,
                        character=character.name,
                        role=f"Переработка {tier_key.replace('_', '/')}",
                        planet=assignment.candidate.planet,
                        system=assignment.candidate.system,
                        constellation="",
                        cc_type=f"1x {assignment.candidate.planet_type} Command Center",
                        res_out=product,
                        res_in=", ".join(sorted(schematics[product].inputs))
                        if product in schematics
                        else None,
                        structures=f"{FACTORIES_PER_TEMPLATE[assignment.template_key]} фабрик",
                        type_id=None,
                        template_key=assignment.template_key,
                        template_count=assignment.template_count,
                        planet_type=assignment.candidate.planet_type,
                        planet_radius_km=assignment.candidate.radius_km,
                        cpu_percent=assignment.cpu_percent,
                        pg_percent=assignment.pg_percent,
                    )
                )

    # 4. Добыча: шаблоны miner_00 на планетах с нужным сырьём.
    per_miner = FACTORIES_PER_TEMPLATE["miner_00"]
    for product, factory_count in sorted(demand.factories.items()):
        if _tier_of(product, recipes) != "P1":
            continue
        recipe = recipes.get(product)
        raw_name = recipe.source if recipe else None
        templates_needed = int(math.ceil(factory_count / per_miner))
        placed = 0

        candidates = planets.planets_with_resource(raw_name, request.constellations) if raw_name else None
        if candidates is None or candidates.empty:
            result.warnings.append(
                f"КРИТИЧЕСКИЙ ДЕФИЦИТ: нет планет с сырьём «{raw_name}» "
                f"для производства {product} в выбранных констелляциях."
            )
            continue

        skill_shortfall = False
        for _, row in candidates.iterrows():
            if placed >= templates_needed:
                break
            radius = float(row.get(RADIUS_COLUMN) or 0.0)

            # Сначала выясняем, какая прокачка нужна ИМЕННО НА ЭТОЙ планете:
            # на крупной шаблон может не влезть при CCU IV и влезть при CCU V.
            # Определять до выбора персонажа важно ещё и потому, что иначе
            # неудачная попытка расходовала бы его слот впустую.
            required_ccu = min_ccu_level_that_fits("miner_00", radius)
            if required_ccu is None:
                result.warnings.append(
                    f"{row.get('System')} {row.get('Planet')}: добывающий шаблон "
                    f"не помещается ни при каком уровне Command Center Upgrades "
                    f"(радиус {radius:,.0f} км).".replace(",", "\u00a0")
                )
                continue

            # Берём наименее прокачанного из подходящих: персонажи с CCU V
            # ценнее на переработке, где только они ставят два шаблона.
            character = pool.take(min_ccu=required_ccu, prefer_least_skilled=True)
            if character is None:
                skill_shortfall = True
                continue

            load = calculate_colony_load(
                "miner_00", character.command_center_upgrades_level, radius
            )

            row_counter += 1
            placed += 1
            result.rows.append(
                PlanRow(
                    id=f"row-{row_counter}",
                    char_id=character.character_id,
                    character=character.name,
                    role="Добыча",
                    planet=str(row.get("Planet", "")),
                    system=str(row.get("System", "")),
                    constellation=str(row.get("Constellation", "")),
                    cc_type=f"1x {row.get('Type')} Command Center",
                    res_out=product,
                    res_in=raw_name,
                    structures=f"{per_miner} фабрик",
                    type_id=None,
                    template_key="miner_00",
                    template_count=1,
                    planet_type=str(row.get("Type", "")),
                    planet_radius_km=radius,
                    cpu_percent=load.cpu_percent,
                    pg_percent=load.pg_percent,
                )
            )

        if placed < templates_needed:
            reason = (
                " Не хватило свободных персонажей нужного уровня."
                if skill_shortfall
                else " Не хватило подходящих планет с этим сырьём."
            )
            result.warnings.append(
                f"ДЕФИЦИТ ДОБЫЧИ: для {product} нужно {templates_needed} планет, "
                f"размещено {placed}.{reason}"
            )

    if pool.free_slots == 0 and pool.total_slots:
        result.warnings.append("Все слоты планет заняты — резерва под расширение нет.")

    return result
