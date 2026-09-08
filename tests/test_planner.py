"""
Тесты domain.planner.build_plan.

Проверяют то, чего в v1 не было вовсе: что план сходится по балансу,
корректно распределяет персонажей и честно сообщает о дефиците вместо
молчаливого урезания.
"""

from __future__ import annotations

import pandas as pd
import pytest

from domain.planets import RADIUS_COLUMN, PlanetBook
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


class TestHonesty:
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
        значения. Длительности циклов подтверждены вики EVE University и
        из списка убраны; осталось одно — формула числа планет.
        """
        assumptions = _plan(planets, characters).assumptions
        assert any("Interplanetary Consolidation" in a for a in assumptions)
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


class TestFrontendContract:
    def test_row_dict_has_all_fields_frontend_reads(self, planets, characters):
        rows = _plan(planets, characters).to_dict()["data"]
        assert rows
        required = {
            "id", "char_id", "character", "role", "planet",
            "system", "cc_type", "res_out", "structures", "type_id", "hours_left",
        }
        assert required <= set(rows[0])

    def test_extraction_role_contains_keyword_frontend_checks(self, planets, characters):
        """index.html отличает добычу по подстроке «Добыча» в поле role."""
        result = _plan(planets, characters)
        mining = [r for r in result.rows if r.template_key == "miner_00"]
        assert mining
        assert all("Добыча" in r.role for r in mining)

    def test_structures_string_contains_extractable_number(self, planets, characters):
        """index.html достаёт число фабрик из строки structures."""
        import re

        for row in _plan(planets, characters).rows:
            assert re.search(r"\d+", row.structures)
