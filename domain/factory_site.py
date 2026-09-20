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

Порог для p4_2factory зависит от расхождения в источнике по стоимости
второго причала (см. assumptions.launchpad_count_for_two_templates в
data/pi_reference.json): при консервативных 7200 CPU это 9 600 км, при
варианте README (5200) было бы 61 700 км. Расхождение закрыто без
проверки в игре (16.09.2026) — в загруженном регионе (Fountain) нет
Barren/Temperate планеты нужного радиуса, консервативное значение
остаётся окончательным. Поэтому предупреждение о P4 может оказаться
избыточным — это указано в тексте предупреждения.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.capacity import (
    UnsupportedSetup,
    calculate_colony_load,
    load_templates,
    max_planet_radius_that_fits,
    min_ccu_level_that_fits,
)
from domain.planets import PREFERRED_FACTORY_PLANET_TYPES, RADIUS_COLUMN, PlanetBook
from domain.plan_messages import render as render_message

# Какой шаблон отвечает какому тиру переработки.
TEMPLATE_BY_TIER = {
    "P2_P3": {1: "p2p3_1factory", 2: "p2p3_2factory"},
    "P4": {1: "p4_1factory", 2: "p4_2factory"},
}

# Сколько фабрик содержит один шаблон каждого вида (structure_counts()
# реальных игровых шаблонов, domain/templates.py). Лежит здесь, а не в
# planner.py, чтобы domain/logistics.py тоже мог использовать эти числа
# для расчёта пропускной способности причала без циклического импорта
# (planner.py -> throughput.py -> logistics.py -> обратно на planner.py).
FACTORIES_PER_TEMPLATE = {
    "miner_00": 8,            # basic-фабрики
    "p2p3_1factory": 12,      # advanced
    "p2p3_2factory": 24,
    "p4_1factory": 8,         # high-tech
    "p4_2factory": 16,
}


@dataclass(frozen=True)
class PlanetCandidate:
    system: str
    planet: str
    planet_type: str
    radius_km: float
    poco_rate: float | None = None  # доля 0..1; None — ставка неизвестна

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

    # Наименьший CCU, при котором ИМЕННО ЭТОТ шаблон на ИМЕННО ЭТОЙ
    # планете физически помещается (18.09.2026, найдено пользователем:
    # build_plan() назначал колонии переработки персонажу без проверки
    # минимального CCU вовсе — тот же пробел, что уже был закрыт для
    # добычи, min_ccu_level_that_fits("miner_00", radius)). Растёт с
    # радиусом планеты (крупнее планета — дороже линки — нужен более
    # высокий CCU), поэтому это не константа тира, а свойство конкретной
    # площадки; build_plan() передаёт это значение в pool.take(min_ccu=...).
    min_ccu_level: int = 0


@dataclass
class SiteSelection:
    """Результат подбора планет под переработку в одной системе."""

    system: str
    tier: str
    ccu_level: int
    assignments: list[SiteAssignment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)         # рендер по-русски, для тестов и хранилища
    warning_data: list[dict] = field(default_factory=list)    # {code, ...параметры} для перевода
    downgraded_to_single: bool = False
    templates_requested: int = 0
    candidates_available: int = 0

    # Пришлось ли ставить переработку на непредпочтительные типы планет.
    used_fallback_types: list[str] = field(default_factory=list)

    def warn(self, code: str, **params) -> None:
        """Записать предупреждение: код+параметры для перевода и русский рендер."""
        entry = {"code": code, **params}
        self.warning_data.append(entry)
        self.warnings.append(render_message(entry, "ru"))

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

    ПОРЯДОК СРЕДИ ПРЕДПОЧТИТЕЛЬНЫХ (17.09.2026, по прямому запросу
    пользователя): ставка POCO — тоже критерий выбора планеты для
    переработки, и «всегда», а не только при прочих равных, поэтому
    стоит перед радиусом (сам радиус остаётся решающим для того,
    ПОМЕЩАЕТСЯ ли шаблон — это отдельная проверка в _fits(), сюда не
    относится). Неизвестная ставка — не «дёшево» и не «дорого», честно
    сортируется ПОСЛЕ любой известной (правило 1 — не подставлять
    оптимистичное предположение вместо пробела).
    """
    df = planets.factory_candidates(system)
    result: list[PlanetCandidate] = []
    for _, row in df.iterrows():
        radius = row.get(RADIUS_COLUMN)
        if radius is None or radius != radius:  # NaN
            continue
        system_name = str(row["System"])
        planet_number = row.get("Planet", "")
        result.append(
            PlanetCandidate(
                system=system_name,
                planet=str(planet_number),
                planet_type=str(row["Type"]),
                radius_km=float(radius),
                poco_rate=planets.poco_rate(system_name, planet_number),
            )
        )
    # Предпочтительные типы впереди; внутри группы — по возрастанию
    # ставки POCO (неизвестная ставка — в конец), затем по радиусу.
    result.sort(key=lambda c: (
        not c.preferred,
        c.poco_rate is None,
        c.poco_rate if c.poco_rate is not None else 0.0,
        c.radius_km,
    ))
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
    max_double_templates: int | None = None,
) -> SiteSelection:
    """
    Подобрать планеты под переработку в домашней системе.

    tier: "P2_P3" или "P4".
    templates_needed: сколько шаблонов надо разместить (не планет).
    allow_single_fallback: если двойной вариант не помещается ни на одну
        планету — ставить ли одиночные. По умолчанию False: сначала
        пользователь должен увидеть предупреждение и решить сам.

    max_double_templates (18.09.2026, найдено пользователем: план
    планировал двойные шаблоны на КАЖДУЮ колонию тира, если хоть один
    персонаж во всём пуле имел CCU V, а не по числу СВОБОДНЫХ CCU5-
    слотов — реальные CCU5-персонажи заканчивались, и все следующие
    колонии проваливались с «не хватило персонажей», хотя обычных
    IC-слотов хватало) — сколько двойных шаблонов реально можно
    укомплектовать (`_CharacterPool.free_slots(require_ccu5=True)`,
    вызывающий код). `None` — прежнее поведение без ограничения (не
    используется build_plan(), оставлено для прямых вызовов/тестов).
    Остаток сверх этого предела ставится одиночными шаблонами так же,
    как и нечётный хвост — без предупреждения и без allow_single_fallback:
    это не отказ разместить двойной (тот уже помещается физически),
    а честный учёт того, кому реально есть чем его укомплектовать.

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
        selection.warn("site_no_planets", system=system)
        return selection

    preferred = [c for c in candidates if c.preferred]
    if not preferred:
        types = ", ".join(sorted({c.planet_type for c in candidates}))
        if tier == "P4":
            # Для P4 это не предпочтение, а правило игры: шаблон
            # не ставится на другие типы вовсе.
            selection.warn("site_p4_no_preferred", system=system, types=types)
            return selection
        selection.warn("site_no_preferred", system=system, tier=tier, types=types)

    double_key = TEMPLATE_BY_TIER[tier][2]
    single_key = TEMPLATE_BY_TIER[tier][1]
    double_available = ccu_level >= load_templates()[double_key].min_ccu_level

    # Самая мелкая планета вообще — для текста предупреждения
    # "site_double_too_big" ниже. Не candidates[0]: с 17.09.2026 порядок
    # списка возглавляет самая ДЕШЁВАЯ ПО НАЛОГУ предпочтительная планета,
    # не обязательно самая мелкая.
    smallest = min(candidates, key=lambda c: c.radius_km)

    if not double_available:
        selection.warn("site_no_ccu5", ccu_level=ccu_level)

    def _pool(template_key: str) -> list[PlanetCandidate]:
        """
        Предпочтительные планеты, пока среди них есть хоть одна
        подходящая под шаблон; общий список (включая другие типы) —
        только когда НИ ОДНА предпочтительная не подходит вовсе.

        "Нехватка" предпочтительных (правило 1 в докстринге модуля) —
        про ПРИГОДНОСТЬ, не про количество: одна планета несёт
        неограниченное число колоний (правило 4), поэтому счётчик
        кандидатов не может "закончиться" сам по себе. Раньше здесь
        безусловно передавался весь `candidates` (предпочтительные и
        остальные вперемешку) — из-за этого переработка иногда уходила
        на непредпочтительные типы, даже когда предпочтительных хватало
        с большим запасом (найдено пользователем на реальном плане
        17.09.2026: часть колоний Barren/Temperate, часть — на Storm,
        хотя Barren/Temperate было достаточно).
        """
        if preferred and any(_fits(template_key, ccu_level, c) is not None for c in preferred):
            return preferred
        return candidates

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
            threshold_km = _km(threshold) if threshold is not None else "?"
            selection.warn(
                "site_double_too_big",
                system=system, tier=tier, planet_type=smallest.planet_type,
                smallest_km=smallest_km, ccu_level=ccu_level, threshold_km=threshold_km,
            )
            if tier == "P4":
                selection.warn("site_p4_launchpad_caveat")
            if not allow_single_fallback:
                return selection
            selection.downgraded_to_single = True
        else:
            wanted_doubles = remaining // 2
            double_budget = wanted_doubles
            if max_double_templates is not None:
                # Каждое двойное назначение — это ДВА разных персонажа
                # на одной планете (build_plan() зовёт pool.take() дважды
                # с одним и тем же (system, planet); второй раз тот же
                # персонаж уже исключён — второй колонии там нужен
                # кто-то ещё), не один персонаж на 24 фабрики. Поэтому
                # предел в НАЗНАЧЕНИЯХ — это предел в СЛОТАХ, делённый
                # на 2, а не сам предел в слотах (18.09.2026, найдено
                # тут же: без деления пополам план обещал вдвое больше
                # двойных назначений, чем реально хватало персонажей —
                # к «не хватило персонажей» приводила уже НЕ вся добыча
                # CCU5 разом, а ровно половина списка, что было не сразу
                # заметно и подтвердилось только прямым прогоном).
                double_budget = min(double_budget, max(max_double_templates, 0) // 2)
            placed_doubles = 0
            for candidate, colony_index in _cycle(_pool(double_key), double_budget):
                if placed_doubles >= double_budget:
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
                        min_ccu_level=min_ccu_level_that_fits(
                            double_key, candidate.radius_km, planet_type=candidate.planet_type
                        ) or 5,
                    )
                )
                used[candidate.planet] = colony_index
                remaining -= 2
                placed_doubles += 1

            # Ограничение по CCU5-слотам, не по физическому размеру
            # планеты (та проверка — double_fits_somewhere выше) — не
            # блокирует план и не требует allow_single_fallback: сверх
            # этого предела просто ставятся одиночные шаблоны, как и
            # нечётный хвост ниже.
            if double_budget < wanted_doubles:
                selection.warn("site_ccu5_slots_exhausted", system=system, tier=tier)

    # Одиночные шаблоны. Три случая:
    #   - CCU < 5: двойной недоступен в принципе;
    #   - пользователь согласился на откат после предупреждения;
    #   - остался НЕЧЁТНЫЙ хвост после размещения двойных — это штатная
    #     ситуация, а не откат, и предупреждения не требует.
    if remaining > 0:
        for candidate, colony_index in _cycle(_pool(single_key), remaining, start=used):
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
                    min_ccu_level=min_ccu_level_that_fits(
                        single_key, candidate.radius_km, planet_type=candidate.planet_type
                    ) or 0,
                )
            )
            used[candidate.planet] = colony_index
            remaining -= 1

    used_types = {a.candidate.planet_type for a in selection.assignments}
    selection.used_fallback_types = sorted(
        t for t in used_types if t not in PREFERRED_FACTORY_PLANET_TYPES
    )

    if selection.used_fallback_types and preferred:
        # Отличается от site_no_preferred/site_p4_no_preferred (которые
        # уже покрывают случай "предпочтительных типов в системе нет
        # вовсе"): здесь предпочтительные планеты ЕСТЬ, но часть
        # переработки всё равно ушла на другие типы, потому что ни одна
        # предпочтительная не подошла по размеру под этот шаблон
        # (см. _pool() выше).
        selection.warn(
            "site_fallback_size_used",
            system=system, tier=tier,
            types=", ".join(selection.used_fallback_types),
        )

    # Ставка POCO неодинакова у разных планет одного и того же типа
    # (см. domain/planets.py::PlanetBook.poco_rate) — даже когда все
    # назначения предпочтительного типа, часть могла получить более
    # высокую ставку, чем лучший доступный вариант (например, лучшая
    # по налогу планета не поместилась под двойной шаблон, а под
    # одиночный — поместилась, и на неё ушла лишь часть колоний).
    # Честно называем это пользователю и перечисляем конкретные планеты,
    # а не молчим — план остаётся рабочим (правило пользователя 17.09.2026:
    # не блокировать, просто пометить).
    used_rates = [
        a.candidate.poco_rate for a in selection.assignments if a.candidate.poco_rate is not None
    ]
    if used_rates:
        best_rate = min(used_rates)
        outliers = sorted({
            (a.candidate.system, a.candidate.planet, a.candidate.poco_rate)
            for a in selection.assignments
            if a.candidate.poco_rate is not None and a.candidate.poco_rate > best_rate
        })
        if outliers:
            selection.warn(
                "site_higher_tax_used",
                system=system, tier=tier, best_rate=f"{best_rate:.0%}",
                planets=", ".join(f"{s} {p} ({r:.0%})" for s, p, r in outliers),
            )

    if remaining > 0:
        selection.warn(
            "site_unplaced",
            system=system, tier=tier, remaining=remaining,
            requested=templates_needed, available=selection.candidates_available,
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
