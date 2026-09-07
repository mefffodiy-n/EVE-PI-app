"""
Построение производственного плана: распределение персонажей по планетам
(добыча / переработка) под выбранные целевые продукты.

Заменяет функцию calculate_plan() из main.py (v1). Ключевые отличия:

- никаких вызовов ESI и БД внутри — planner.py принимает уже готовые
  структуры (список персонажей, RecipeBook, PlanetBook) и возвращает план;
  чтение из БД/ESI-синхронизация остаются на уровне api/ и workers/;
- CPU/PG считаются через domain.capacity на основе JSON-шаблонов
  застройки из data/templates/ (в v1 эти шаблоны лежали в репозитории,
  но не читались ни бэкендом, ни фронтендом), а не хардкодятся;
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
    cpu_percent: float
    powergrid_percent: float

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
      2. посчитать требуемое число планет под переработку (P2-P4) и добычу (P1);
      3. распределить характеристики персонажей (CCU/IC) по слотам фабрик/добычи;
      4. для каждой планеты посчитать реальную загрузку CPU/PG (domain.capacity)
         вместо хардкода;
      5. при выборе фабричного хаба учитывать domain.link_penalty по радиусу;
      6. если request.include_direct_p2 — задействовать planets.best_direct_p2_planet()
         для прямого сценария R0 -> P2 (в v1 не реализовано вообще).
    """
    raise NotImplementedError("TODO(Фаза 1): перенести и переработать calculate_plan() из main.py v1")
