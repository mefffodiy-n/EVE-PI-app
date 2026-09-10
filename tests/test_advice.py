"""
Тесты подсказок по вместимости пула персонажей.

Четыре сценария, ради которых модуль и написан:
  не помещается  → предложить то, что поместится, включая тир ниже;
  помещается с запасом → предложить, чем занять остаток;
  отказ при дефиците → план строится с честным сообщением о нехватке;
  отказ при избытке → свободные персонажи идут в добычу того же сырья.

Отдельно проверяется, что подсказки не врут о вместимости: прокачка
персонажей важна не меньше числа слотов, и «слотов хватает» ещё не
значит «поместится».
"""

from __future__ import annotations

import pandas as pd
import pytest

from domain.advice import MINER_MIN_CCU, advise, pool_capacity, surplus_mining_targets
from domain.planets import RADIUS_COLUMN, PlanetBook
from domain.planner import CharacterSlot, PlanRequest, build_plan
from domain.recipes import load_recipes
from domain.throughput import Schematic

FAC = {"P1": "basic_industry_facility", "P2": "advanced_industry_facility",
       "P3": "advanced_industry_facility", "P4": "high_tech_industry_facility"}
OUT = {"P1": 20, "P2": 5, "P3": 3, "P4": 1}
CYCLE = {"P1": 30, "P2": 60, "P3": 60, "P4": 60}


@pytest.fixture(scope="module")
def schematics():
    return {
        r.name: Schematic(r.name, FAC[r.tier],
                          {r.source: 3000} if r.tier == "P1" else dict(r.inputs),
                          OUT[r.tier], CYCLE[r.tier])
        for r in load_recipes()
    }


@pytest.fixture(scope="module")
def recipes():
    return load_recipes()


def crew(count: int, ccu: int = 5, ic: int = 5) -> list[CharacterSlot]:
    return [CharacterSlot(i, f"C{i}", ccu, ic) for i in range(1, count + 1)]


PRICES = {"Broadcast Node": 1_500_000.0, "Biocells": 1_500.0,
          "Camera Drones": 80_000.0, "Robotics": 70_000.0}


class TestCapacity:
    def test_slots_counted_by_skill_not_just_headcount(self):
        """
        Слоты зависят от Interplanetary Consolidation, а способность
        добывать — от Command Center Upgrades. Считать надо оба.
        """
        mixed = [CharacterSlot(1, "A", 5, 5), CharacterSlot(2, "B", 3, 2)]
        capacity = pool_capacity(mixed)
        assert capacity.total_slots == 6 + 3
        assert capacity.mining_capable_slots == 6      # у второго CCU 3
        assert capacity.double_template_slots == 6

    def test_pool_without_mining_capable_characters_is_flagged(self, schematics, recipes):
        """
        Слотов может хватать, но если ни у кого нет CCU IV, добывать
        нечем — молчать об этом нельзя.
        """
        result = advise(["Biocells"], crew(10, ccu=3), PRICES, schematics, recipes)
        assert result.status == "deficit"
        assert any(str(MINER_MIN_CCU) in note for note in result.notes)


class TestDeficit:
    def test_deficit_offers_chains_that_fit(self, schematics, recipes):
        result = advise(["Broadcast Node"], crew(3), PRICES, schematics, recipes)
        assert result.status == "deficit"
        assert result.missing_colonies > 0
        assert result.alternatives, "при дефиците должны быть предложения"
        for suggestion in result.alternatives:
            assert suggestion.colonies <= result.capacity.total_slots

    def test_alternatives_may_be_a_lower_tier(self, schematics, recipes):
        """
        Тир ниже — нормальный ответ: делать P3 непрерывно лучше,
        чем P4 с простоями.
        """
        result = advise(["Broadcast Node"], crew(3), PRICES, schematics, recipes)
        tiers = {s.tier for s in result.alternatives}
        assert tiers and tiers != {"P4"}

    def test_alternatives_ranked_by_profit_when_prices_known(self, schematics, recipes):
        result = advise(["Broadcast Node"], crew(4), PRICES, schematics, recipes)
        priced = [s.isk_per_colony_hour for s in result.alternatives
                  if s.isk_per_colony_hour is not None]
        assert priced == sorted(priced, reverse=True)

    def test_missing_prices_are_admitted_not_hidden(self, schematics, recipes):
        """Без цен порядок произвольный — список не должен выглядеть осмысленнее."""
        result = advise(["Broadcast Node"], crew(3), {}, schematics, recipes)
        assert any("цены" in note.lower() for note in result.notes)

    def test_notes_translate_to_english(self, schematics, recipes):
        """Панель вместимости не переводилась при переключении на английский."""
        import re

        result = advise(["Broadcast Node"], crew(3), {}, schematics, recipes)
        payload = result.to_dict("en")
        assert payload["notes"]
        assert not any(re.search("[А-Яа-я]", n) for n in payload["notes"])
        assert result.to_dict("ru")["notes"] == result.notes


class TestSurplus:
    def test_surplus_offers_additions_that_fit_the_remainder(self, schematics, recipes):
        result = advise(["Biocells"], crew(20), PRICES, schematics, recipes)
        assert result.status == "surplus"
        assert result.spare_slots > 0
        for suggestion in result.additions:
            assert suggestion.colonies <= result.spare_slots

    def test_higher_tier_is_mentioned_when_it_fits(self, schematics, recipes):
        result = advise(["Biocells"], crew(20), PRICES, schematics, recipes)
        assert any("тира" in note for note in result.notes)

    def test_small_remainder_is_not_called_surplus(self, schematics, recipes):
        """
        Остаток меньше самой компактной цепочки — это не избыток,
        а нормальный хвост. Предлагать там нечего.
        """
        result = advise(["Biocells"], crew(1), PRICES, schematics, recipes)
        assert result.status == "fits"


class TestSurplusMining:
    def test_targets_come_from_the_chosen_chain(self, schematics, recipes):
        """
        Избыточная добыча должна питать то же производство, иначе она
        копит ненужное.
        """
        book = PlanetBook(pd.DataFrame([
            {"Constellation": "A", "System": "S", "Planet": "1", "Type": "Barren",
             RADIUS_COLUMN: 5000, "Carbon Compounds": 30, "Noble Metals": 20},
        ]))
        targets = surplus_mining_targets(["Biocells"], book, ["A"], recipes, schematics)
        assert set(targets) <= {"Biofuels", "Precious Metals"}

    def test_scarcest_resource_comes_first(self):
        """Первым добывается то, чего меньше: именно оно ограничивает выпуск."""
        book = PlanetBook(pd.DataFrame([
            {"Constellation": "A", "System": "S", "Planet": "1", "Type": "Barren",
             RADIUS_COLUMN: 5000, "Carbon Compounds": 5, "Noble Metals": 90},
            {"Constellation": "A", "System": "S", "Planet": "2", "Type": "Barren",
             RADIUS_COLUMN: 6000, "Carbon Compounds": 0, "Noble Metals": 80},
        ]))
        ranked = book.resource_scarcity(["Carbon Compounds", "Noble Metals"], ["A"])
        assert ranked[0]["resource"] == "Carbon Compounds"

    def test_plan_without_flag_has_no_surplus_rows(self, schematics, recipes, tmp_path):
        book = _mining_book()
        result = build_plan(
            PlanRequest(constellations=["A"], factory_system="HOME",
                        target_products=["Biocells"]),
            crew(12), recipes=recipes, planets=book, schematics=schematics,
        )
        assert not [r for r in result.rows if "избыток" in r.role]

    def test_flag_puts_spare_characters_on_extraction(self, schematics, recipes):
        book = _mining_book()
        result = build_plan(
            PlanRequest(constellations=["A"], factory_system="HOME",
                        target_products=["Biocells"], surplus_mining=True),
            crew(12), recipes=recipes, planets=book, schematics=schematics,
        )
        surplus = [r for r in result.rows if "избыток" in r.role]
        assert surplus, "свободные персонажи должны быть заняты"
        # Добывается сырьё той же цепочки, а не произвольное.
        assert {r.res_out for r in surplus} <= {"Biofuels", "Precious Metals"}
        assert any("сверх потребности" in w for w in result.warnings)

    def test_surplus_rows_respect_one_colony_per_character_per_planet(self, schematics, recipes):
        book = _mining_book()
        result = build_plan(
            PlanRequest(constellations=["A"], factory_system="HOME",
                        target_products=["Biocells"], surplus_mining=True),
            crew(12), recipes=recipes, planets=book, schematics=schematics,
        )
        from collections import Counter

        per_planet: dict[tuple, Counter] = {}
        for row in result.rows:
            per_planet.setdefault((row.system, row.planet), Counter())[row.char_id] += 1
        assert not [
            (k, c) for k, counter in per_planet.items()
            for c, n in counter.items() if n > 1
        ]


def _mining_book() -> PlanetBook:
    return PlanetBook(pd.DataFrame([
        {"Constellation": "A", "System": "HOME", "Planet": "1", "Type": "Barren",
         RADIUS_COLUMN: 5000, "Carbon Compounds": 0, "Noble Metals": 0},
        {"Constellation": "A", "System": "HOME", "Planet": "2", "Type": "Temperate",
         RADIUS_COLUMN: 6000, "Carbon Compounds": 0, "Noble Metals": 0},
        {"Constellation": "A", "System": "MINE", "Planet": "3", "Type": "Barren",
         RADIUS_COLUMN: 7000, "Carbon Compounds": 34, "Noble Metals": 0},
        {"Constellation": "A", "System": "MINE", "Planet": "4", "Type": "Plasma",
         RADIUS_COLUMN: 8000, "Carbon Compounds": 0, "Noble Metals": 31},
    ]))


class TestLogistics:
    """
    Колонии одного персонажа должны кучковаться по системам.

    Смысл чисто практический: персонаж с шестью колониями в одной системе
    собирает продукцию за один заход, а с шестью в разных — за шесть
    перелётов. До этой правки разброс был максимальным: каждая колония
    в своей системе.
    """

    def test_character_prefers_system_where_already_working(self, schematics, recipes):
        book = _logistics_book()
        result = build_plan(
            PlanRequest(constellations=["A"], factory_system="HOME",
                        target_products=["Biocells"]),
            crew(6), recipes=recipes, planets=book, schematics=schematics,
        )
        logistics = result.logistics()
        assert logistics["avg_systems_per_character"] <= 1.5, (
            f"колонии разбросаны: {logistics['characters']}"
        )

    def test_systems_with_more_resources_come_first(self):
        """
        Система, дающая сразу два нужных ресурса, логистически ценнее
        двух систем с одним каждая.
        """
        book = _logistics_book()
        coverage = book.system_coverage(["Carbon Compounds", "Noble Metals"], ["A"])
        assert coverage.get("RICH") == 2
        assert coverage.get("POOR1") == 1

    def test_logistics_summary_reports_spread(self, schematics, recipes):
        book = _logistics_book()
        result = build_plan(
            PlanRequest(constellations=["A"], factory_system="HOME",
                        target_products=["Biocells"]),
            crew(6), recipes=recipes, planets=book, schematics=schematics,
        )
        summary = result.logistics()
        assert summary["systems_total"] >= 1
        assert summary["max_systems_per_character"] >= 1
        for person in summary["characters"]:
            assert person["system_count"] == len(person["systems"])

    def test_one_character_still_never_doubles_on_a_planet(self, schematics, recipes):
        """Кучкование не должно нарушить более важное правило."""
        from collections import Counter

        book = _logistics_book()
        result = build_plan(
            PlanRequest(constellations=["A"], factory_system="HOME",
                        target_products=["Biocells"], surplus_mining=True),
            crew(6), recipes=recipes, planets=book, schematics=schematics,
        )
        per_planet: dict[tuple, Counter] = {}
        for row in result.rows:
            per_planet.setdefault((row.system, row.planet), Counter())[row.char_id] += 1
        assert not [
            (k, c) for k, counter in per_planet.items()
            for c, n in counter.items() if n > 1
        ]


def _logistics_book() -> PlanetBook:
    """
    Одна богатая система с обоими ресурсами и две бедные с одним каждая.
    Планировщик должен предпочесть богатую.
    """
    rows = [
        {"Constellation": "A", "System": "HOME", "Planet": "1", "Type": "Barren",
         RADIUS_COLUMN: 5000, "Carbon Compounds": 0, "Noble Metals": 0},
        {"Constellation": "A", "System": "HOME", "Planet": "2", "Type": "Temperate",
         RADIUS_COLUMN: 6000, "Carbon Compounds": 0, "Noble Metals": 0},
    ]
    for i in range(1, 5):
        rows.append({"Constellation": "A", "System": "RICH", "Planet": str(i),
                     "Type": "Barren", RADIUS_COLUMN: 7000 + i * 100,
                     "Carbon Compounds": 30, "Noble Metals": 28})
    rows.append({"Constellation": "A", "System": "POOR1", "Planet": "1",
                 "Type": "Barren", RADIUS_COLUMN: 7000,
                 "Carbon Compounds": 34, "Noble Metals": 0})
    rows.append({"Constellation": "A", "System": "POOR2", "Planet": "1",
                 "Type": "Plasma", RADIUS_COLUMN: 7000,
                 "Carbon Compounds": 0, "Noble Metals": 33})
    return PlanetBook(pd.DataFrame(rows))
