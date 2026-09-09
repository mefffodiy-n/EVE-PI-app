"""
Выбор планет под перерабатывающие шаблоны (P2-P4) в домашней системе.

ПРАВИЛА (решение проекта):

  1. Barren и Temperate — ПРЕДПОЧТЕНИЕ, а не запрет, и различие тут
     принципиальное:
       - для P4 это правило игры: шаблон физически не ставится на другие
         типы, и если таких планет в системе нет, переработка P4
         невозможна — об этом надо сказать прямо;
       - для P2/P3 ограничения нет. Barren и Temperate предпочтительны
         лишь потому, что в среднем мельче, а значит дешевле по линкам.
         При их нехватке берутся другие типы с предупреждением.

  2. Планеты выбираются ПО ВОЗРАСТАНИЮ РАДИУСА. Стоимость линков растёт
     линейно с радиусом, поэтому меньшая планета всегда даёт больше
     свободных CPU/PG. Именно радиус решает, поместятся ли два шаблона.

  3. Если радиусы планет в выбранной пользователем системе превышают
     предел для двух шаблонов — не молча ставим один, а ПРЕДУПРЕЖДАЕМ
     и даём пользователю выбор: другая домашняя система или один шаблон
     на планету (вдвое больше планет и персонажей).

  4. ОДНА ПЛАНЕТА ВМЕЩАЕТ НЕОГРАНИЧЕННОЕ ЧИСЛО КОЛОНИЙ РАЗНЫХ ПЕРСОНАЖЕЙ,
     причём и добывающих, и перерабатывающих одновременно. Колония
     привязана к персонажу, а не к планете, поэтому нехватка планет
     не является ограничением вовсе: она решается добавлением персонажа
     из пула. Единственное ограничение — число персонажей и их слотов.

     Источник правила — указание владельца проекта, знающего механику
     игры; подтвердить внешней ссылкой не удалось (профильная страница
     форума отдаёт 503).

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
from domain.planets import PREFERRED_FACTORY_PLANET_TYPES, RADIUS_COLUMN, PlanetBook

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

    @property
    def preferred(self) -> bool:
        return self.planet_type in PREFERRED_FACTORY_PLANET_TYPES


@dataclass(frozen=True)
class SiteAssignment:
    """Одна колония: планета плюс поставленный на неё шаблон."""

    candidate: PlanetCandidate
    template_key: str
    template_count: int
    cpu_percent: float
    pg_percent: float

    # Какая по счёту колония на этой планете. Планета может нести
    # колонии нескольких персонажей, поэтому одна и та же планета
    # встречается в плане несколько раз.
    colony_index: int = 1


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
    candidates_available: int = 0

    # Пришлось ли ставить переработку на непредпочтительные типы планет.
    used_fallback_types: list[str] = field(default_factory=list)

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
    """
    Кандидаты по возрастанию радиуса, предпочтительные типы впереди.

    Внутри каждой группы порядок по радиусу сохраняется, поэтому
    самая мелкая Barren идёт раньше самой мелкой Lava, но обе — раньше
    крупных планет своего типа.
    """
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
    # Предпочтительные типы впереди, внутри групп — по радиусу.
    result.sort(key=lambda c: (not c.preferred, c.radius_km))
    return result


def _cycle(candidates: list[PlanetCandidate], needed: int, start: dict[str, int] | None = None):
    """
    Перебирать планеты по кругу, отдавая (планета, номер колонии).

    Планеты идут по возрастанию радиуса, и когда список исчерпан, обход
    начинается заново со следующим номером колонии: одна планета несёт
    сколько угодно колоний разных персонажей. Благодаря этому нехватка
    планет перестаёт быть ограничением — упирается только в число
    персонажей, что и требуется по правилам проекта.
    """
    if not candidates:
        return
    offsets = dict(start or {})
    produced = 0
    # Числа колоний на планете механика не ограничивает, поэтому кругов
    # ровно столько, сколько нужно, плюс запас на планеты, куда шаблон
    # не помещается по CPU/PG и которые будут пропущены.
    limit = max(needed, 1) * 2 + len(candidates)
    round_index = 0
    while produced < limit:
        for candidate in candidates:
            colony_index = offsets.get(candidate.planet, 0) + round_index + 1
            yield candidate, colony_index
            produced += 1
            if produced >= limit:
                return
        round_index += 1


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
            f"В системе {system} вообще нет планет — переработку ставить некуда. "
            f"Выберите другую домашнюю систему."
        )
        return selection

    preferred = [c for c in candidates if c.preferred]
    if not preferred:
        types = ", ".join(sorted({c.planet_type for c in candidates}))
        if tier == "P4":
            # Для P4 это не предпочтение, а правило игры: шаблон
            # не ставится на другие типы вовсе.
            selection.warnings.append(
                f"В системе {system} нет планет Barren или Temperate. "
                f"Переработка P4 на них и только на них — это ограничение игры, "
                f"обойти его нельзя. Доступны только: {types}. "
                f"Выберите другую домашнюю систему."
            )
            return selection
        selection.warnings.append(
            f"В системе {system} нет планет Barren или Temperate. Переработка "
            f"{tier} будет размещена на других типах ({types}) — это допустимо, "
            f"но такие планеты в среднем крупнее, и линки обойдутся дороже. "
            f"Если запас по CPU/PG окажется мал, выберите другую домашнюю систему."
        )

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
    selection.candidates_available = len(candidates)
    used: dict[str, int] = {}

    if double_available:
        # Проверяем ПРИГОДНОСТЬ отдельно от количества. Иначе нечётный
        # остаток (или потребность в одном шаблоне) выглядел бы как
        # «двойной не помещается», и пользователь получал бы ложное
        # предупреждение о слишком крупных планетах.
        double_fits_somewhere = any(
            _fits(double_key, ccu_level, candidate) is not None for candidate in candidates
        )

        if not double_fits_somewhere:
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
        else:
            for candidate, colony_index in _cycle(candidates, remaining // 2):
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
                        colony_index=colony_index,
                    )
                )
                used[candidate.planet] = colony_index
                remaining -= 2

    # Одиночные шаблоны. Три случая:
    #   - CCU < 5: двойной недоступен в принципе;
    #   - пользователь согласился на откат после предупреждения;
    #   - остался НЕЧЁТНЫЙ хвост после размещения двойных — это штатная
    #     ситуация, а не откат, и предупреждения не требует.
    if remaining > 0:
        for candidate, colony_index in _cycle(candidates, remaining, start=used):
            if remaining <= 0:
                break
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
                    colony_index=colony_index,
                )
            )
            used[candidate.planet] = colony_index
            remaining -= 1

    used_types = {a.candidate.planet_type for a in selection.assignments}
    selection.used_fallback_types = sorted(
        t for t in used_types if t not in PREFERRED_FACTORY_PLANET_TYPES
    )

    if remaining > 0:
        selection.warnings.append(
            f"В системе {system} не удалось разместить {remaining} шаблонов "
            f"{tier} из {templates_needed}: на пригодных планетах "
            f"({selection.candidates_available} шт. Barren/Temperate) шаблон "
            f"не помещается по CPU/PG. Планеты слишком крупные."
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
