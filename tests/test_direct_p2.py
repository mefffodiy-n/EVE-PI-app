"""
Тесты прямого производства P2 на добывающей планете.

Особенность этой части: готового шаблона застройки в источнике нет,
состав считается из стоимостей отдельных структур. Поэтому проверяется
не совпадение с шаблоном, а внутренняя состоятельность расчёта —
что застройка действительно помещается и что баланс фабрик соблюдён.
"""

from __future__ import annotations

import pandas as pd
import pytest

from domain.direct_p2 import (
    HEADS_PER_BASIC,
    best_layout,
    compare_with_split,
    direct_candidates,
)
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
    return {r.name: Schematic(r.name, FAC[r.tier],
                              {r.source: 3000} if r.tier == "P1" else dict(r.inputs),
                              OUT[r.tier], CYCLE[r.tier])
            for r in load_recipes()}


@pytest.fixture(scope="module")
def recipes():
    return load_recipes()


class TestLayout:
    def test_layout_fits_within_command_centre(self, schematics):
        layout = best_layout("Biocells", 5000, 5, schematics)
        assert layout is not None
        assert layout.fits
        assert layout.cpu_percent <= 100 and layout.pg_percent <= 100

    def test_powergrid_is_the_binding_constraint(self, schematics):
        """
        Два экстрактора стоят 5200 PG против 800 CPU — бюджет упирается
        в Powergrid задолго до CPU. Если это перестанет быть так,
        расчёт стоит пересмотреть.
        """
        layout = best_layout("Biocells", 5000, 5, schematics)
        assert layout.pg_percent > layout.cpu_percent

    def test_basic_factories_balance_advanced(self, schematics):
        """
        Одна advanced-фабрика потребляет два вида P1, и каждый поток даёт
        ровно одна basic-фабрика. Значит basic вдвое больше advanced.
        """
        layout = best_layout("Biocells", 5000, 5, schematics)
        assert layout.basic == 2 * layout.advanced

    def test_heads_follow_factory_count(self, schematics):
        """Голов должно хватать на фабрики: соотношение из 00-шаблона майнера."""
        import math

        layout = best_layout("Biocells", 5000, 5, schematics)
        assert layout.heads_each >= math.ceil(HEADS_PER_BASIC * layout.advanced) or \
               layout.heads_each == 10

    def test_lower_skill_fits_less(self, schematics):
        low = best_layout("Biocells", 5000, 4, schematics)
        high = best_layout("Biocells", 5000, 5, schematics)
        assert low.advanced < high.advanced

    def test_unknown_product_returns_nothing(self, schematics):
        assert best_layout("Нет такого", 5000, 5, schematics) is None


class TestCandidates:
    def test_requires_both_raw_materials_on_one_planet(self, recipes, schematics):
        """
        Планета с одним видом сырья не годится: цепочка не замкнётся,
        и второй P1 всё равно придётся везти.
        """
        book = PlanetBook(pd.DataFrame([
            {"Constellation": "A", "System": "S", "Planet": "1", "Type": "Barren",
             RADIUS_COLUMN: 5000, "Carbon Compounds": 30, "Noble Metals": 0},
            {"Constellation": "A", "System": "S", "Planet": "2", "Type": "Barren",
             RADIUS_COLUMN: 5000, "Carbon Compounds": 30, "Noble Metals": 25},
        ]))
        found = direct_candidates("Biocells", book, ["A"], 5, recipes, schematics)
        assert [c["planet"] for c in found] == ["2"]

    def test_only_p2_products_qualify(self, recipes, schematics):
        """Выше P2 цепочка на одну планету физически не помещается."""
        book = PlanetBook(pd.DataFrame([
            {"Constellation": "A", "System": "S", "Planet": "1", "Type": "Barren",
             RADIUS_COLUMN: 5000, "Carbon Compounds": 30, "Noble Metals": 25},
        ]))
        assert direct_candidates("Robotics", book, ["A"], 5, recipes, schematics) == []
        assert direct_candidates("Water", book, ["A"], 5, recipes, schematics) == []


class TestHonestComparison:
    def test_comparison_does_not_oversell_the_method(self, schematics):
        """
        Прямой путь обычно не экономит колоний. Расчёт не должен
        подгонять сравнение в свою пользу.
        """
        layout = best_layout("Biocells", 5000, 5, schematics)
        result = compare_with_split("Biocells", layout)
        assert result["colonies_saved"] <= 1
        assert "логистик" in result["note"].lower()


class TestPlannerIntegration:
    def test_flag_replaces_split_chain_entirely(self, recipes, schematics):
        """
        Прямое производство заменяет раздельное, а не добавляется к нему:
        иначе выпуск удвоился бы против заказанного.
        """
        book = _both_resources_book()
        crew = [CharacterSlot(i, f"C{i}", 5, 5) for i in range(1, 9)]

        direct = build_plan(
            PlanRequest(constellations=["A"], factory_system="HOME",
                        target_products=["Biocells"], direct_p2=True),
            crew, recipes=recipes, planets=book, schematics=schematics)
        roles = {r.role for r in direct.rows}
        assert "Прямое P2" in roles
        assert not any("Переработка" in r for r in roles)

    def test_without_flag_nothing_changes(self, recipes, schematics):
        book = _both_resources_book()
        crew = [CharacterSlot(i, f"C{i}", 5, 5) for i in range(1, 9)]
        plain = build_plan(
            PlanRequest(constellations=["A"], factory_system="HOME",
                        target_products=["Biocells"]),
            crew, recipes=recipes, planets=book, schematics=schematics)
        assert not any("Прямое" in r.role for r in plain.rows)

    def test_falls_back_when_no_suitable_planets(self, recipes, schematics):
        """
        Без планет с обоими ресурсами прямой путь невозможен — цель
        должна вернуться в обычный расчёт, а не потеряться.
        """
        book = PlanetBook(pd.DataFrame([
            {"Constellation": "A", "System": "HOME", "Planet": "1", "Type": "Barren",
             RADIUS_COLUMN: 5000, "Carbon Compounds": 30, "Noble Metals": 0},
            {"Constellation": "A", "System": "HOME", "Planet": "2", "Type": "Temperate",
             RADIUS_COLUMN: 6000, "Carbon Compounds": 0, "Noble Metals": 28},
        ]))
        crew = [CharacterSlot(i, f"C{i}", 5, 5) for i in range(1, 9)]
        result = build_plan(
            PlanRequest(constellations=["A"], factory_system="HOME",
                        target_products=["Biocells"], direct_p2=True),
            crew, recipes=recipes, planets=book, schematics=schematics)
        assert result.rows, "цель не должна пропасть без подходящих планет"
        assert any("Добыча" in r.role for r in result.rows)


def _both_resources_book() -> PlanetBook:
    rows = [
        {"Constellation": "A", "System": "HOME", "Planet": "1", "Type": "Barren",
         RADIUS_COLUMN: 5000, "Carbon Compounds": 0, "Noble Metals": 0},
    ]
    for i in range(2, 8):
        rows.append({"Constellation": "A", "System": "MINE", "Planet": str(i),
                     "Type": "Barren", RADIUS_COLUMN: 5000 + i * 100,
                     "Carbon Compounds": 30, "Noble Metals": 26})
    return PlanetBook(pd.DataFrame(rows))
