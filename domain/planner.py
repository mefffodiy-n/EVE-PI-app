"""
Построение производственного плана: распределение персонажей по планетам
(добыча / переработка) под выбранные целевые продукты.

Заменяет функцию calculate_plan() из main.py (v1). Ключевые отличия:

- никаких вызовов ESI и БД внутри — planner.py принимает уже готовые
  структуры (список персонажей, RecipeBook, PlanetBook) и возвращает план;
  чтение из БД/ESI-синхронизация остаются на уровне api/ и workers/;
- CPU/PG считаются через domain.capacity на основе каталога шаблонов
  застройки (data/pi_reference.json, источник — DalShooth/EVE_PI_Templates),
  с учётом радиуса планеты и уровня CCU, а не хардкодятся;
- на одну планету может ставиться ДВА шаблона (варианты p2p3_2factory /
  p4_2factory) — но только при Command Center Upgrades V. Выбор варианта
  зависит от прокачки конкретного персонажа;
- P4-шаблоны ставятся только на Barren и Temperate планеты;
- выбор фабричного хаба учитывает domain.link_penalty (радиус планеты),
  а не берёт первую попавшуюся систему;
- сценарий "прямое R0 -> P2" — отдельный параметр (см. Фаза 4 в roadmap.md),
  а не молча игнорируемая часть данных.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.planets import PlanetBook
from domain.recipes import RecipeBook


@dataclass(frozen=True)
class CharacterSlot:
    """
    Персонаж, доступный для назначения на планету.

    В Фазе 1 источник этих данных — scripts/seed_dev_characters.py
    (dev-заглушки). В Фазе 3 источник тот же по структуре, но данные
    приходят из sync_character_skills (реальный ESI). planner.py
    не различает происхождение — только характеристики.
    """

    character_id: int
    name: str
    command_center_upgrades_level: int  # 0-5
    interplanetary_consolidation_level: int  # 0-5


@dataclass(frozen=True)
class TemplateChoice:
    """
    Какой шаблон застройки выбран для планеты и почему.

    Отдельный тип, потому что выбор нетривиален: для P2/P3 и P4 есть
    варианты на 1 и на 2 фабрики, вариант на 2 требует CCU V, а влезет
    он или нет — зависит ещё и от радиуса планеты (линки дорожают).
    """

    template_key: str
    factory_count: int          # 1 или 2 шаблона на этой планете
    reason: str                 # почему выбран именно этот вариант
    cpu_percent: float
    pg_percent: float


@dataclass(frozen=True)
class PlanRequest:
    constellations: list[str]
    factory_system: str
    target_products: list[str]
    include_direct_p2: bool = False  # Фаза 4: сценарий прямого R0 -> P2


@dataclass
class PlanAssignment:
    constellation: str
    system: str
    planet: str
    character_id: int
    character_name: str
    role: str  # "extraction" | "processing"
    resource_out: str
    resource_in: str | None
    structures_summary: str
    template: TemplateChoice
    cpu_percent: float
    powergrid_percent: float
    planet_radius_km: float
    planet_type: str

    # Время до истечения цикла экстрактора. None означает "неизвестно" —
    # и фронтенд обязан показывать именно "неизвестно", а не подставлять
    # своё значение. В v1 index.html:475 дорисовывал это поле как
    # Math.random() * 22 и отрисовывал как настоящий таймер (красная
    # пульсация при < 2ч, сортировка "по времени таймера"), из-за чего
    # интерфейс показывал правдоподобный шум. Реальные значения появятся
    # в Фазе 3 из workers/jobs/sync_colony_status.py.
    extractor_hours_left: float | None = None


@dataclass
class PlanResult:
    assignments: list[PlanAssignment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_plan(
    request: PlanRequest,
    characters: list[CharacterSlot],
    recipes: RecipeBook,
    planets: PlanetBook,
) -> PlanResult:
    """
    Построить план распределения персонажей по добыче/переработке.

    Переносимая из v1 логика (требует явной проверки формул перед переносом,
    а не копирования вслепую — см. roadmap.md, раздел 1, пункт про
    произвольные множители 16/24/12 факторов):
      1. развернуть target_products в дерево сырья через recipes.raw_materials_for();
      2. посчитать требуемое число фабрик по тирам;
      3. для каждого персонажа определить ДОСТУПНЫЕ ему шаблоны:
         CCU V -> можно 2 шаблона на планету (p2p3_2factory / p4_2factory),
         CCU < 5 -> только варианты на 1 фабрику. Это прямо влияет на то,
         сколько планет нужно: 2 шаблона на планету вдвое сокращают их число;
      4. подобрать планеты под каждый шаблон с проверкой через
         capacity.calculate_colony_load(): тип планеты (P4 только Barren/
         Temperate) и радиус (линки дорожают, большие планеты не влезают);
      5. число слотов планет на персонажа = Interplanetary Consolidation + 1;
      6. если request.include_direct_p2 — задействовать planets.best_direct_p2_planet()
         для прямого сценария R0 -> P2 (в v1 не реализовано вообще).

    ЗАМЕЧАНИЕ по константам v1: main.py резал фабрики кусками по 16 (P4) и
    24 (P2/P3). Эти числа совпадают с вариантами шаблонов на 2 фабрики
    (P4: High-Tech x16, P2/P3: Advanced x24) — то есть неявно предполагали
    CCU V у всех персонажей. Здесь это условие должно проверяться явно.
    Значение 12 для P1 в v1 источнику не соответствует.
    """
    raise NotImplementedError("TODO(Фаза 1): перенести и переработать calculate_plan() из main.py v1")
