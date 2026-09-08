"""
Выбор планет под перерабатывающие шаблоны (P2-P4) в домашней системе.

ПРАВИЛА (решение проекта):

  1. Под переработку берутся только планеты типов Barren и Temperate.
     Источник разрешает P2/P3 на любом типе и требует Barren/Temperate
     только для P4 — мы сужаем правило до единого для всей переработки.

  2. Планеты выбираются ПО ВОЗРАСТАНИЮ РАДИУСА. Стоимость линков растёт
     линейно с радиусом, поэтому меньшая планета всегда даёт больше
     свободных CPU/PG. Именно радиус решает, поместятся ли два шаблона.

  3. Если радиусы планет в выбранной пользователем системе превышают
     предел для двух шаблонов — не молча ставим один, а ПРЕДУПРЕЖДАЕМ
     и даём пользователю выбор: другая домашняя система или один шаблон
     на планету (вдвое больше планет и персонажей).

Пороговые радиусы при CCU V (расчёт domain.capacity):
    p2p3_2factory  — примерно до 12 800 км (ограничивает PG)
    p4_2factory    — примерно до 9 600 км  (ограничивает CPU)
    одиночные варианты помещаются на планету практически любого размера.

Порог для p4_2factory зависит от неразрешённого расхождения в источнике
по стоимости второго причала (см. assumptions.launchpad_count_for_two_templates
в data/pi_reference.json): при консервативных 7200 CPU это 9 600 км,
при варианте README (5200) было бы 61 700 км. Поэтому предупреждение
о P4 может оказаться избыточным — это указано в тексте предупреждения.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.capacity import (
    UnsupportedSetup,
    calculate_colony_load,
    load_templates,
    max_planet_radius_that_fits,
)
from domain.planets import RADIUS_COLUMN, PlanetBook

# Какой шаблон отвечает какому тиру переработки.
TEMPLATE_BY_TIER = {
    "P2_P3": {1: "p2p3_1factory", 2: "p2p3_2factory"},
    "P4": {1: "p4_1factory", 2: "p4_2factory"},
}


@dataclass(frozen=True)
class PlanetCandidate:
    system: str
    planet: str
    planet_type: str
    radius_km: float


@dataclass(frozen=True)
class SiteAssignment:
    """Одна планета с назначенным на неё шаблоном."""

    candidate: PlanetCandidate
    template_key: str
    template_count: int
    cpu_percent: float
    pg_percent: float


@dataclass
class SiteSelection:
    """Результат подбора планет под переработку в одной системе."""

    system: str
    tier: str
    ccu_level: int
    assignments: list[SiteAssignment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    downgraded_to_single: bool = False
    templates_requested: int = 0

    @property
    def templates_placed(self) -> int:
        return sum(a.template_count for a in self.assignments)

    @property
    def satisfied(self) -> bool:
        return self.templates_placed >= self.templates_requested


def _km(value: float) -> str:
    """Число с пробелом-разделителем разрядов, как принято в русской типографике."""
    return f"{value:,.0f}".replace(",", "\u00a0")


def _candidates(planets: PlanetBook, system: str) -> list[PlanetCandidate]:
    df = planets.factory_candidates(system)
    result: list[PlanetCandidate] = []
    for _, row in df.iterrows():
        radius = row.get(RADIUS_COLUMN)
        if radius is None or radius != radius:  # NaN
            continue
        result.append(
            PlanetCandidate(
                system=str(row["System"]),
                planet=str(row.get("Planet", "")),
                planet_type=str(row["Type"]),
                radius_km=float(radius),
            )
        )
    return result


def _fits(template_key: str, ccu_level: int, candidate: PlanetCandidate):
    """Вернуть ColonyLoad, если комбинация допустима и помещается, иначе None."""
    try:
        load = calculate_colony_load(
            template_key,
            ccu_level,
            candidate.radius_km,
            planet_type=candidate.planet_type,
        )
    except UnsupportedSetup:
        return None
    return load if load.fits else None


def select_factory_sites(
    planets: PlanetBook,
    system: str,
    tier: str,
    ccu_level: int,
    templates_needed: int,
    allow_single_fallback: bool = False,
) -> SiteSelection:
    """
    Подобрать планеты под переработку в домашней системе.

    tier: "P2_P3" или "P4".
    templates_needed: сколько шаблонов надо разместить (не планет).
    allow_single_fallback: если двойной вариант не помещается ни на одну
        планету — ставить ли одиночные. По умолчанию False: сначала
        пользователь должен увидеть предупреждение и решить сам.

    Возвращает SiteSelection с назначениями и предупреждениями.
    Пустые assignments при непустых warnings — это не ошибка, а
    приглашение пользователю выбрать: другая система или один шаблон.
    """
    if tier not in TEMPLATE_BY_TIER:
        raise ValueError(f"Неизвестный тир переработки: {tier}")

    selection = SiteSelection(
        system=system, tier=tier, ccu_level=ccu_level, templates_requested=templates_needed
    )

    candidates = _candidates(planets, system)
    if not candidates:
        selection.warnings.append(
            f"В системе {system} нет планет типов Barren или Temperate — "
            f"перерабатывающие шаблоны ставить некуда. Выберите другую домашнюю систему."
        )
        return selection

    double_key = TEMPLATE_BY_TIER[tier][2]
    single_key = TEMPLATE_BY_TIER[tier][1]
    double_available = ccu_level >= load_templates()[double_key].min_ccu_level

    smallest = candidates[0]

    if not double_available:
        selection.warnings.append(
            f"Command Center Upgrades {ccu_level}: два шаблона на планету недоступны "
            f"(нужен уровень 5). Используется один шаблон на планету — планет "
            f"потребуется вдвое больше."
        )

    # Пытаемся разместить двойные шаблоны на самых мелких планетах.
    remaining = templates_needed
    used: set[str] = set()

    if double_available:
        for candidate in candidates:
            if remaining < 2:
                break
            load = _fits(double_key, ccu_level, candidate)
            if load is None:
                continue
            selection.assignments.append(
                SiteAssignment(
                    candidate=candidate,
                    template_key=double_key,
                    template_count=2,
                    cpu_percent=load.cpu_percent,
                    pg_percent=load.pg_percent,
                )
            )
            used.add(candidate.planet)
            remaining -= 2

        if not selection.assignments:
            threshold = max_planet_radius_that_fits(double_key, ccu_level)
            smallest_km = _km(smallest.radius_km)
            threshold_km = _km(threshold) if threshold is not None else "неизвестен"
            selection.warnings.append(
                f"В системе {system} ни одна планета Barren/Temperate не подходит под "
                f"два шаблона {tier}: самая мелкая — {smallest.planet_type} радиусом "
                f"{smallest_km} км, а предел при Command Center Upgrades "
                f"{ccu_level} — примерно {threshold_km} км. "
                f"Варианты: выбрать другую домашнюю систему с планетами поменьше "
                f"либо ставить по одному шаблону на планету (планет и персонажей "
                f"потребуется вдвое больше)."
            )
            if tier == "P4":
                selection.warnings.append(
                    "Замечание: порог для двойного P4-шаблона опирается на "
                    "неразрешённое расхождение в источнике по стоимости второго "
                    "причала (7200 против 5200 CPU). Взято консервативное значение; "
                    "если проверка в игре покажет 5200, предел вырастет примерно "
                    "с 9 600 до 61 700 км и это предупреждение станет излишним."
                )
            if not allow_single_fallback:
                return selection
            selection.downgraded_to_single = True

    # Одиночные шаблоны: либо CCU < 5, либо явно разрешён откат.
    if remaining > 0 and (not double_available or selection.downgraded_to_single):
        for candidate in candidates:
            if remaining <= 0:
                break
            if candidate.planet in used:
                continue
            load = _fits(single_key, ccu_level, candidate)
            if load is None:
                continue
            selection.assignments.append(
                SiteAssignment(
                    candidate=candidate,
                    template_key=single_key,
                    template_count=1,
                    cpu_percent=load.cpu_percent,
                    pg_percent=load.pg_percent,
                )
            )
            used.add(candidate.planet)
            remaining -= 1

    if remaining > 0:
        selection.warnings.append(
            f"В системе {system} не хватило подходящих планет: размещено "
            f"{selection.templates_placed} шаблонов из {templates_needed}. "
            f"Пригодных планет Barren/Temperate — {len(candidates)}."
        )

    return selection


def describe_thresholds(ccu_level: int) -> dict[str, float | None]:
    """
    Предельные радиусы планет для каждого перерабатывающего шаблона.

    Полезно отдавать во фронтенд, чтобы пользователь видел цифры
    до выбора домашней системы, а не после расчёта плана.
    """
    keys = [k for tier in TEMPLATE_BY_TIER.values() for k in tier.values()]
    result: dict[str, float | None] = {}
    for key in keys:
        try:
            result[key] = max_planet_radius_that_fits(key, ccu_level)
        except UnsupportedSetup:
            result[key] = None
    return result
