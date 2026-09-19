"""
Тесты domain.planner.build_plan.

Проверяют то, чего в v1 не было вовсе: что план сходится по балансу,
корректно распределяет персонажей и честно сообщает о дефиците вместо
молчаливого урезания.
"""

from __future__ import annotations

import pandas as pd
import pytest

from domain.planets import POCO_RATE_COLUMN, RADIUS_COLUMN, PlanetBook
from domain.planner import CharacterSlot, PlanRequest, build_plan
from domain.recipes import load_recipes
from domain.throughput import Schematic

# Количества как в реальных шаблонах: P2 — 40 единиц каждого входа
# на цикл и 5 на выходе; P1 — 3000 сырья на цикл и 20 на выходе.
SCHEMATICS = {
    "Biocells": Schematic(
        "Biocells", "advanced_industry_facility",
        {"Biofuels": 40, "Precious Metals": 40}, 5, 60,
    ),
    "Biofuels": Schematic(
        "Biofuels", "basic_industry_facility", {"Carbon Compounds": 3000}, 20, 30,
    ),
    "Precious Metals": Schematic(
        "Precious Metals", "basic_industry_facility", {"Noble Metals": 3000}, 20, 30,
    ),
}

PLANETS = [
    {"Constellation": "ALPHA", "System": "HOME", "Planet": "4", "Type": "Barren",
     RADIUS_COLUMN: 5820, "Carbon Compounds": 0, "Noble Metals": 0},
    {"Constellation": "ALPHA", "System": "HOME", "Planet": "7", "Type": "Temperate",
     RADIUS_COLUMN: 8100, "Carbon Compounds": 0, "Noble Metals": 0},
    {"Constellation": "ALPHA", "System": "MINE1", "Planet": "2", "Type": "Barren",
     RADIUS_COLUMN: 9000, "Carbon Compounds": 34, "Noble Metals": 0},
    {"Constellation": "ALPHA", "System": "MINE1", "Planet": "5", "Type": "Lava",
     RADIUS_COLUMN: 12000, "Carbon Compounds": 0, "Noble Metals": 31},
    {"Constellation": "ALPHA", "System": "MINE2", "Planet": "1", "Type": "Gas",
     RADIUS_COLUMN: 60000, "Carbon Compounds": 28, "Noble Metals": 0},
    {"Constellation": "ALPHA", "System": "MINE2", "Planet": "3", "Type": "Plasma",
     RADIUS_COLUMN: 14000, "Carbon Compounds": 0, "Noble Metals": 25},
]


@pytest.fixture
def planets() -> PlanetBook:
    return PlanetBook(pd.DataFrame(PLANETS))


@pytest.fixture
def characters() -> list[CharacterSlot]:
    return [
        CharacterSlot(90001, "Chief 1", 5, 5),
        CharacterSlot(90002, "Chief 2", 5, 5),
        CharacterSlot(90003, "Miner A", 5, 4),
        CharacterSlot(90004, "Miner B", 4, 4),
    ]


def _plan(planets, characters, **kwargs):
    request = PlanRequest(
        constellations=["ALPHA"],
        factory_system="HOME",
        target_products=["Biocells"],
        **kwargs,
    )
    return build_plan(
        request, characters, recipes=load_recipes(), planets=planets, schematics=SCHEMATICS
    )


class TestDemand:
    def test_chain_expands_to_raw_materials(self, planets, characters):
        result = _plan(planets, characters)
        assert set(result.demand.raw_materials) == {"Carbon Compounds", "Noble Metals"}

    def test_factory_counts_balance(self, planets, characters):
        """
        Одна advanced-фабрика потребляет столько же единиц P1 в час,
        сколько производит одна basic-фабрика, поэтому 12 фабрик P2
        требуют по 12 фабрик на каждый вход.
        """
        factories = _plan(planets, characters).demand.factories
        assert factories["Biocells"] == pytest.approx(12.0)
        assert factories["Biofuels"] == pytest.approx(12.0)
        assert factories["Precious Metals"] == pytest.approx(12.0)


class TestPurchaseP1:
    """
    18.09.2026, по прямому запросу пользователя: «план и выгода только
    по переработке из закупаемого P1» — не строить добывающие колонии
    вовсе, P1 считается закупленным на бирже.
    """

    def test_no_mining_rows_when_purchasing_p1(self, planets, characters):
        result = _plan(planets, characters, purchase_p1=True)
        assert all("Добыча" not in r.role for r in result.rows)

    def test_processing_rows_still_placed(self, planets, characters):
        result = _plan(planets, characters, purchase_p1=True)
        assert any("Добыча" not in r.role for r in result.rows)

    def test_p1_recorded_as_purchased_not_built(self, planets, characters):
        result = _plan(planets, characters, purchase_p1=True)
        assert result.demand.purchased_p1["Biofuels"] == pytest.approx(480.0)
        assert result.demand.purchased_p1["Precious Metals"] == pytest.approx(480.0)
        assert "Biofuels" not in result.demand.factories
        assert "Precious Metals" not in result.demand.factories
        assert not result.demand.raw_materials

    def test_assumption_recorded(self, planets, characters):
        result = _plan(planets, characters, purchase_p1=True)
        codes = {e["code"] for e in result.assumption_data}
        assert "p1_purchased_on_market" in codes

    def test_purchased_p1_exposed_in_to_dict(self, planets, characters):
        result = _plan(planets, characters, purchase_p1=True)
        assert result.to_dict()["purchased_p1"] == result.demand.purchased_p1

    def test_normal_plan_has_empty_purchased_p1(self, planets, characters):
        result = _plan(planets, characters)
        assert result.to_dict()["purchased_p1"] == {}

    def test_surplus_mining_ignored_when_purchasing_p1(self, planets, characters):
        """
        Излишек добычи бессмысленен без добычи вовсе — покупка P1
        отключает его молча, а не требует от вызывающего кода помнить
        о несовместимости этих двух флагов.
        """
        result = _plan(planets, characters, purchase_p1=True, surplus_mining=True)
        assert all("Добыча" not in r.role for r in result.rows)


class TestAllocation:
    def test_processing_is_placed(self, planets, characters):
        """
        Регрессия: при потребности в одном шаблоне и доступном двойном
        варианте переработка терялась целиком.
        """
        rows = [r for r in _plan(planets, characters).rows if "Добыча" not in r.role]
        assert rows, "перерабатывающие шаблоны должны быть размещены"

    def test_processing_only_on_barren_or_temperate(self, planets, characters):
        rows = [r for r in _plan(planets, characters).rows if "Добыча" not in r.role]
        assert all(r.planet_type in ("Barren", "Temperate") for r in rows)

    def test_processing_uses_home_system(self, planets, characters):
        rows = [r for r in _plan(planets, characters).rows if "Добыча" not in r.role]
        assert all(r.system == "HOME" for r in rows)

    def test_poco_rate_filled_from_planets_file(self, characters):
        """
        17.09.2026, по прямому запросу пользователя: каждая строка плана
        несёт ставку POCO своей конкретной планеты (для колонки в Excel
        и для сравнения ставок между строками), а не только сама
        переработка/выгрузка это уже умели считать отдельно.
        """
        planets_with_rate = PlanetBook(pd.DataFrame([
            {**PLANETS[0], "Planet": 4, POCO_RATE_COLUMN: 3},   # HOME 4, Barren
            {**PLANETS[1], "Planet": 7, POCO_RATE_COLUMN: 1},   # HOME 7, Temperate
            {**PLANETS[2]},                        # MINE1 2 — ставка неизвестна
            {**PLANETS[3]},
            {**PLANETS[4]},
            {**PLANETS[5]},
        ]))
        result = _plan(planets_with_rate, characters)
        proc_rows = [r for r in result.rows if "Добыча" not in r.role]
        assert proc_rows and all(r.poco_rate is not None for r in proc_rows)
        mine_rows = [r for r in result.rows if "Добыча" in r.role]
        assert mine_rows and all(r.poco_rate is None for r in mine_rows)

    def test_mining_planets_actually_have_the_resource(self, planets, characters):
        by_planet = {(p["System"], p["Planet"]): p for p in PLANETS}
        for row in _plan(planets, characters).rows:
            if "Добыча" not in row.role:
                continue
            source = row.res_in
            assert by_planet[(row.system, row.planet)][source] > 0, (
                f"{row.system}-{row.planet} не содержит {source}"
            )

    def test_large_planet_gets_sufficiently_skilled_character(self, planets, characters):
        """
        Газовая планета 60 000 км не помещается при CCU IV, но помещается
        при CCU V — планировщик обязан подобрать подходящего персонажа,
        а не отбросить планету.
        """
        rows = [r for r in _plan(planets, characters).rows if r.planet_radius_km > 50000]
        assert rows, "крупная планета должна быть использована"
        assigned = {r.char_id for r in rows}
        skilled = {c.character_id for c in characters if c.command_center_upgrades_level >= 5}
        assert assigned <= skilled

    def test_character_planet_slots_are_respected(self, planets, characters):
        result = _plan(planets, characters)
        used: dict[int, int] = {}
        for row in result.rows:
            used[row.char_id] = used.get(row.char_id, 0) + 1
        limits = {c.character_id: c.planet_slots for c in characters}
        assert all(count <= limits[char_id] for char_id, count in used.items())

    def test_one_character_never_gets_two_colonies_on_same_planet(self, planets, characters):
        """
        Колония привязана к паре «персонаж + планета». Несколько колоний
        на одной планете возможны, но только от РАЗНЫХ персонажей.
        """
        from collections import Counter

        rows = _plan(planets, characters).rows
        per_planet: dict[tuple[str, str], Counter] = {}
        for row in rows:
            key = (row.system, row.planet)
            per_planet.setdefault(key, Counter())[row.char_id] += 1

        duplicates = [
            (planet, char_id, count)
            for planet, counter in per_planet.items()
            for char_id, count in counter.items()
            if count > 1
        ]
        assert not duplicates, f"персонаж дважды на одной планете: {duplicates}"

    def test_planet_can_host_colonies_of_several_characters(self):
        """Нехватка планет решается разными персонажами на одной планете."""
        book = PlanetBook(pd.DataFrame([
            {"Constellation": "ALPHA", "System": "HOME", "Planet": "4", "Type": "Barren",
             RADIUS_COLUMN: 5820, "Carbon Compounds": 34, "Noble Metals": 31},
        ]))
        crew = [CharacterSlot(i, f"Char {i}", 5, 5) for i in range(1, 7)]
        rows = _plan(book, crew).rows
        assert len(rows) > 1, "одна планета должна нести несколько колоний"
        assert len({r.char_id for r in rows}) == len(rows), "у каждой колонии свой персонаж"

    def test_every_row_carries_type_id_for_icons(self, planets, characters):
        """
        Фронтенд строит иконку продукта из p.type_id. Пустое значение даёт
        сломанную картинку в дашборде — так и было, пока планировщик
        проставлял здесь None.
        """
        rows = _plan(planets, characters).rows
        assert rows
        assert all(r.type_id for r in rows), (
            f"строки без type_id: {[r.res_out for r in rows if not r.type_id]}"
        )

    def test_every_row_has_fitting_load(self, planets, characters):
        for row in _plan(planets, characters).rows:
            assert row.cpu_percent <= 100.0
            assert row.pg_percent <= 100.0


class TestCcu5Scarcity:
    """
    18.09.2026, найдено пользователем на реальном плане (закупка P1,
    8 разных P2/P3/P4-продуктов, план использовал только 24 колонии из
    84 возможных с десятками предупреждений «не хватило персонажей»,
    хотя пользователь честно следовал подсказке о свободных слотах).

    Причина: `build_plan()` планировал двойные шаблоны на КАЖДУЮ
    колонию тира, если хоть один персонаж во всём пуле имел CCU V, не
    считаясь с тем, сколько именно CCU5-персонажей реально свободно —
    и тем более с тем, что одно двойное назначение занимает ДВУХ разных
    персонажей на одной планете (build_plan() зовёт pool.take() дважды
    с одной и той же планетой; второй раз тот же персонаж уже исключён),
    а не одного.
    """

    @pytest.fixture
    def rich_planets(self) -> PlanetBook:
        """
        Много Barren в HOME — двойное назначение занимает ДВУХ разных
        персонажей на ОДНОЙ планете (правило «один персонаж — одна
        колония на планете»), поэтому переиспользование одной и той же
        планеты под несколько двойных назначений само по себе требует
        ДОПОЛНИТЕЛЬНЫХ CCU5-персонажей — отдельное, не касающееся этого
        теста ограничение (правило 4 модуля domain/factory_site.py:
        реальные системы Fountain не настолько бедны планетами). Тест
        проверяет именно предел по СЛОТАМ, поэтому планет здесь с
        запасом.
        """
        rows = [
            {"Constellation": "ALPHA", "System": "HOME", "Planet": str(n), "Type": "Barren",
             RADIUS_COLUMN: 4000 + n * 100, "Carbon Compounds": 0, "Noble Metals": 0}
            for n in range(1, 10)
        ] + [row for row in PLANETS if row["System"] != "HOME"]
        return PlanetBook(pd.DataFrame(rows))

    SCARCE_CCU5_CREW = (
        # 2 персонажа с CCU V, но по 3 слота каждый (IC II) — 6 CCU5-слотов
        # всего, ровно на 3 полных двойных назначения (6 // 2). Остаток
        # 8-мишаблонной потребности обязан уйти одиночными.
        [CharacterSlot(1, "CCU5 A", 5, 2), CharacterSlot(2, "CCU5 B", 5, 2)]
        + [CharacterSlot(i, f"CCU4 {i}", 4, 5) for i in range(10, 20)]
    )

    def test_full_demand_satisfied_with_scarce_ccu5(self, rich_planets):
        """
        8 шаблонов нужно (lines_per_target=8), но CCU5-слотов хватает
        только на 3 полных двойных назначения — раньше это приводило к
        массовому «не хватило персонажей»; план обязан закрыть всю
        потребность, доставив остаток одиночными шаблонами.
        """
        result = _plan(rich_planets, self.SCARCE_CCU5_CREW, lines_per_target=8)
        rows = [r for r in result.rows if "Добыча" not in r.role]
        assert sum(r.template_count for r in rows) >= 8
        # Только про переработку — добыче в этом крохотном пуле
        # характеров закономерно не хватает персонажей отдельно, это не
        # то, что проверяет этот тест (см. TestAllocation про добычу).
        assert not any(g.role_key == "proc" for g in result.staffing_gaps)
        assert not any("Переработка" in w and "не хватило" in w for w in result.warnings)

    def test_ccu5_downgrade_is_informational_not_blocking(self, rich_planets):
        """
        Откат части колоний на одиночный шаблон из-за нехватки CCU5 —
        не отказ (в отличие от «двойной физически не помещается» выше):
        план строится без `allow_single_template_fallback`.
        """
        result = _plan(rich_planets, self.SCARCE_CCU5_CREW, lines_per_target=8)
        assert any("одиночным шаблоном" in w for w in result.warnings)
        rows = [r for r in result.rows if "Добыча" not in r.role]
        singles = [r for r in rows if r.template_count == 1]
        doubles = [r for r in rows if r.template_count == 2]
        assert singles and doubles


class TestScarcePlanetsFallback:
    """
    18.09.2026, найдено пользователем на реальном плане: с двумя
    подходящими планетами в системе и большим спросом переработка
    массово проваливалась с «не хватило персонажей» при CCU5, хотя
    max_double_templates (см. TestCcu5Scarcity выше) в теории должен был
    это предотвратить. Настоящая причина глубже: max_double_templates
    ограничивает ОБЩЕЕ число свободных CCU5-слотов, но не то, что
    ОДНОМУ И ТОМУ ЖЕ человеку нельзя дать вторую колонию на ТОЙ ЖЕ
    планете — с малым числом планет разных CCU5-персонажей на каждую
    из них может просто не хватить, даже когда общих CCU5-слотов
    формально много (IC-слоты одного и того же человека не помогают,
    если ему больше некуда столько раз ставить колонию на ЭТОЙ планете).

    Фикс — build_plan() пробует одиночный шаблон НА ТОЙ ЖЕ площадке,
    когда для второго места в двойном назначении не находится ЕЩЁ
    ОДНОГО подходящего персонажа, вместо того чтобы сразу сдаваться.
    """

    TWO_PLANETS = PlanetBook(pd.DataFrame([
        {"Constellation": "ALPHA", "System": "HOME", "Planet": "4", "Type": "Barren",
         RADIUS_COLUMN: 5820, "Carbon Compounds": 0, "Noble Metals": 0},
        {"Constellation": "ALPHA", "System": "HOME", "Planet": "7", "Type": "Temperate",
         RADIUS_COLUMN: 7080, "Carbon Compounds": 0, "Noble Metals": 0},
    ]))

    def test_falls_back_to_single_when_no_second_ccu5_character_for_this_planet(self):
        """
        4 персонажа с CCU V (по одной колонии на планету каждый) — на 2
        планетах это ровно 4 колонии максимум (2 планеты × 2 человека на
        двойной шаблон). Спрос на 6 шаблонов: остаток обязан уйти
        одиночными на тех же двух планетах — персонажей с CCU IV тоже
        хватает, просто не на двойной.
        """
        crew = (
            [CharacterSlot(i, f"CCU5 {i}", 5, 5) for i in range(1, 5)]
            + [CharacterSlot(i, f"CCU4 {i}", 4, 5) for i in range(10, 14)]
        )
        result = _plan(self.TWO_PLANETS, crew, purchase_p1=True, lines_per_target=6)
        rows = [r for r in result.rows if "Добыча" not in r.role]
        assert sum(r.template_count for r in rows) >= 6
        assert not any(g.role_key == "proc" for g in result.staffing_gaps)

    def test_more_suitable_planets_raise_the_ceiling(self):
        """
        Тот же пул, но система с бОльшим числом подходящих планет —
        план должен успеть построить не меньше колоний, чем с двумя
        планетами (обычно больше, если исходно не хватало именно площадок).
        """
        crew = (
            [CharacterSlot(i, f"CCU5 {i}", 5, 5) for i in range(1, 5)]
            + [CharacterSlot(i, f"CCU4 {i}", 4, 5) for i in range(10, 14)]
        )
        many_planets = PlanetBook(pd.DataFrame([
            {"Constellation": "ALPHA", "System": "HOME", "Planet": str(n), "Type": "Barren",
             RADIUS_COLUMN: 4000 + n * 100, "Carbon Compounds": 0, "Noble Metals": 0}
            for n in range(1, 9)
        ]))
        two = _plan(self.TWO_PLANETS, crew, purchase_p1=True, lines_per_target=6)
        many = _plan(many_planets, crew, purchase_p1=True, lines_per_target=6)
        two_rows = sum(r.template_count for r in two.rows if "Добыча" not in r.role)
        many_rows = sum(r.template_count for r in many.rows if "Добыча" not in r.role)
        assert many_rows >= two_rows

    def test_honest_min_ccu_reported_when_fallback_also_fails(self):
        """
        Когда не хватает вообще никого (даже под одиночный шаблон),
        сообщение о нехватке обязано называть РЕАЛЬНЫЙ нижний порог
        (тот, что не смогли закрыть), а не всегда «нужен CCU V» — иначе
        пользователь тренирует не тот навык / выбирает не то решение.
        """
        crew = [CharacterSlot(1, "Solo", 5, 5)]
        result = _plan(self.TWO_PLANETS, crew, purchase_p1=True, lines_per_target=6)
        proc_gaps = [g for g in result.staffing_gaps if g.role_key == "proc"]
        assert proc_gaps
        assert all(g.min_ccu_level < 5 for g in proc_gaps)


class TestProcessingMinCcu:
    """
    18.09.2026, найдено пользователем: назначение переработки не несло
    минимальный CCU персонажа, при котором шаблон физически помещается —
    build_plan() мог отдать колонию персонажу с CCU 0-1 (тот же пробел,
    что уже был закрыт для добычи — min_ccu_level_that_fits("miner_00",
    radius)).
    """

    def test_low_ccu_character_never_gets_a_processing_colony(self, planets):
        """
        Один способный (CCU III) и один неспособный (CCU 0) персонаж,
        потребность — две колонии: неспособный не должен занять вторую
        колонию просто потому, что слот формально свободен.
        """
        crew = [CharacterSlot(1, "Capable", 3, 5), CharacterSlot(2, "Rookie CCU0", 0, 5)]
        result = _plan(planets, crew, lines_per_target=2)
        rows = [r for r in result.rows if "Добыча" not in r.role]
        assert rows
        assert all(r.character != "Rookie CCU0" for r in rows)
    def test_extractor_timer_is_never_invented(self, planets, characters):
        """
        hours_left = None означает «неизвестно». Подставлять сюда
        правдоподобное число нельзя: в v1 фронтенд рисовал Math.random()
        как настоящий таймер.
        """
        assert all(r.hours_left is None for r in _plan(planets, characters).rows)

    def test_unverified_assumptions_are_reported(self, planets, characters):
        """
        В расчёте должны перечисляться только ДЕЙСТВИТЕЛЬНО непроверенные
        значения. Длительности циклов подтверждены вики EVE University,
        формула числа планет на персонажа — пользователем по игре
        (16.09.2026, docs/ROADMAP.md) — обе закрыты и не должны значиться
        допущениями.
        """
        assumptions = _plan(planets, characters).assumptions
        assert not any("Interplanetary Consolidation" in a for a in assumptions), (
            "формула числа планет подтверждена и не должна значиться допущением"
        )
        assert not any("цикла" in a for a in assumptions), (
            "циклы подтверждены источником и не должны значиться допущениями"
        )

    def test_missing_schematics_stop_the_chain_loudly(self, planets, characters):
        request = PlanRequest(
            constellations=["ALPHA"], factory_system="HOME", target_products=["Biocells"]
        )
        result = build_plan(
            request, characters, recipes=load_recipes(), planets=planets, schematics={}
        )
        assert result.rows == []
        assert any("schematics.json" in w for w in result.warnings)

    def test_missing_resource_reported_as_critical(self, planets):
        """Если сырья нет в регионе — сказать прямо, а не выдать урезанный план."""
        empty = PlanetBook(pd.DataFrame([
            {"Constellation": "ALPHA", "System": "HOME", "Planet": "4", "Type": "Barren",
             RADIUS_COLUMN: 5820, "Carbon Compounds": 0, "Noble Metals": 0},
        ]))
        result = _plan(empty, [CharacterSlot(90001, "Solo", 5, 5)])
        assert any("КРИТИЧЕСКИЙ ДЕФИЦИТ" in w for w in result.warnings)

    def test_shortage_of_characters_is_reported(self, planets):
        result = _plan(planets, [CharacterSlot(90001, "Solo", 5, 0)])  # 1 слот
        assert any("ДЕФИЦИТ" in w for w in result.warnings)

    def test_shortage_is_quantified_as_characters_needed(self, planets):
        """
        Дефицит — это не «что-то пошло не так», а задача: сколько ещё
        персонажей нужно и до какого уровня прокачки.
        """
        result = _plan(planets, [CharacterSlot(90001, "Solo", 5, 0)])
        staffing = result.characters_required()
        assert staffing, "дефицит должен превращаться в кадровую потребность"
        mining = staffing.get("Добыча")
        assert mining and mining["characters_needed"] >= 1
        assert mining["min_ccu_level"] == 4, "добывающему шаблону хватает CCU IV"

    def test_characters_needed_depends_on_planet_slots(self, planets):
        result = _plan(planets, [CharacterSlot(90001, "Solo", 5, 0)])
        few = result.characters_required(planet_slots_per_character=2)
        many = result.characters_required(planet_slots_per_character=6)
        if "Добыча" in few and "Добыча" in many:
            assert few["Добыча"]["characters_needed"] >= many["Добыча"]["characters_needed"]


class TestSharedInputs:
    """
    Один и тот же компонент входит в несколько рецептов, и потребность
    в нём должна СУММИРОВАТЬСЯ, а не браться по максимуму.
    """

    def test_shared_component_demand_accumulates(self, planets, characters):
        from domain.throughput import expand_demand

        recipes = load_recipes()
        schematics = dict(SCHEMATICS)
        one = expand_demand({"Biocells": 60.0}, schematics=schematics, recipes=recipes)
        two = expand_demand({"Biocells": 120.0}, schematics=schematics, recipes=recipes)
        assert two.factories["Biofuels"] == pytest.approx(2 * one.factories["Biofuels"])

    def test_raw_material_totals_sum_across_branches(self, planets, characters):
        from domain.throughput import expand_demand

        recipes = load_recipes()
        demand = expand_demand({"Biocells": 60.0}, schematics=SCHEMATICS, recipes=recipes)
        # Оба входа Biocells разворачиваются до своего сырья независимо.
        assert set(demand.raw_materials) == {"Carbon Compounds", "Noble Metals"}
        assert all(v > 0 for v in demand.raw_materials.values())

    def test_purchase_p1_stops_expansion_at_p1(self, planets, characters):
        """
        18.09.2026, по прямому запросу пользователя: с purchase_p1=True
        P1 — лист дерева (закупается), а не разворачивается в P0.
        """
        from domain.throughput import expand_demand

        recipes = load_recipes()
        demand = expand_demand(
            {"Biocells": 60.0}, schematics=SCHEMATICS, recipes=recipes, purchase_p1=True
        )
        assert demand.purchased_p1["Biofuels"] == pytest.approx(480.0)
        assert demand.purchased_p1["Precious Metals"] == pytest.approx(480.0)
        assert "Biofuels" not in demand.factories
        assert "Precious Metals" not in demand.factories
        assert not demand.raw_materials
        # P2 разворачивается как обычно — граница только у P1.
        assert demand.factories["Biocells"] == pytest.approx(12.0)


class TestFrontendContract:
    def test_row_dict_has_all_fields_frontend_reads(self, planets, characters):
        rows = _plan(planets, characters).to_dict()["data"]
        assert rows
        required = {
            "id", "char_id", "character", "role", "role_key", "planet",
            "system", "cc_type", "res_out", "structures", "type_id", "hours_left",
        }
        assert required <= set(rows[0])

    def test_extraction_role_exposed_as_language_neutral_key(self, planets, characters):
        """
        index.html переводит роль по role_key, а не по русской подстроке в
        role: раньше бейдж «Переработка P2/P3» не переводился на английский.
        Строка role остаётся русской — её по подстроке ищет plan_storage.
        """
        result = _plan(planets, characters)
        mining = [r for r in result.rows if r.template_key == "miner_00"]
        assert mining
        assert all(r.role_key == "mine" for r in mining)
        assert all("Добыча" in r.role for r in mining)
        proc = [r for r in result.rows if r.role.startswith("Переработка")]
        assert all(r.role_key == "proc" for r in proc)
        assert all(r.role_tier in ("P2/P3", "P4") for r in proc)

    def test_structures_string_contains_extractable_number(self, planets, characters):
        """index.html достаёт число фабрик из строки structures."""
        import re

        for row in _plan(planets, characters).rows:
            assert re.search(r"\d+", row.structures)

    def test_factory_summary_is_language_neutral_data(self, planets, characters):
        """index.html рисует «N фабрик» из factory_summary, а не из русской строки."""
        for row in _plan(planets, characters).to_dict()["data"]:
            fs = row["factory_summary"]
            assert fs, row["structures"]
            assert "factories" in fs or {"advanced", "basic"} <= set(fs)


class TestMessagesI18n:
    """Предупреждения и допущения переводятся; раньше «ДЕФИЦИТ ДОБЫЧИ…»
    показывался по-английски как есть."""

    CYRILLIC = __import__("re").compile("[А-Яа-яЁё]")

    def _deficit_plan(self):
        empty = PlanetBook(pd.DataFrame([
            {"Constellation": "ALPHA", "System": "HOME", "Planet": "4", "Type": "Barren",
             RADIUS_COLUMN: 5820, "Carbon Compounds": 0, "Noble Metals": 0},
        ]))
        return _plan(empty, [CharacterSlot(90001, "Solo", 5, 5)])

    def test_english_render_has_no_cyrillic(self):
        payload = self._deficit_plan().to_dict("en")
        assert payload["critical_warnings"], "у дефицитного плана есть критические"
        # assumptions может быть пуст: раньше формула числа планет на
        # персонажа значилась допущением всегда, теперь она подтверждена
        # (docs/ROADMAP.md, 16.09.2026) и не добавляется — блок остаётся
        # в цикле ниже честности ради (не Cyrillic, если что-то в нём есть).
        for block in ("warnings", "critical_warnings", "assumptions", "site_warnings"):
            for text in payload[block]:
                assert not self.CYRILLIC.search(text), (block, text)

    def test_russian_render_matches_plain_attribute(self):
        result = self._deficit_plan()
        payload = result.to_dict("ru")
        rendered = set(payload["warnings"]) | set(payload["critical_warnings"])
        assert rendered == set(result.warnings)

    def test_critical_warnings_are_separated(self):
        payload = self._deficit_plan().to_dict("ru")
        assert any("КРИТИЧЕСКИЙ" in w for w in payload["critical_warnings"])

    def test_every_catalog_code_is_bilingual(self):
        from domain.plan_messages import _CATALOG

        for code, entry in _CATALOG.items():
            assert entry.get("ru") and entry.get("en"), code
