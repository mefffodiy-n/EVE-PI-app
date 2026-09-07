"""
Тесты domain.capacity.

Проверяют, что модель воспроизводит числа источника
(DalShooth/EVE_PI_Templates) и что загрузка реально зависит
от входных данных — в отличие от v1, где main.py всегда
возвращал pg_load=95 / cpu_load=95.
"""

from __future__ import annotations

import pytest

from domain.capacity import (
    UnsupportedSetup,
    available_templates_for,
    calculate_colony_load,
    command_center_capacity,
    extractor_heads_load,
    link_load,
    load_templates,
    max_planet_radius_that_fits,
    structures_load,
)


def test_command_center_capacity_matches_source():
    assert command_center_capacity(0).cpu == 1675
    assert command_center_capacity(0).pg == 6000
    assert command_center_capacity(5).cpu == 25415
    assert command_center_capacity(5).pg == 19000


@pytest.mark.parametrize(
    "structures, expected_cpu, expected_pg",
    [
        ({"extractor_control_unit": 2}, 800, 5200),          # LS-майнер, README
        ({"basic_industry_facility": 4}, 800, 3200),          # LS-майнер, README
        ({"advanced_industry_facility": 12}, 6000, 8400),     # P2/P3 x1, README
        ({"advanced_industry_facility": 24}, 12000, 16800),   # P2/P3 x2, README
        ({"high_tech_industry_facility": 8}, 8800, 3200),     # P4 x1, README
        ({"high_tech_industry_facility": 16}, 17600, 6400),   # P4 x2, README
    ],
)
def test_structure_costs_reproduce_readme_sums(structures, expected_cpu, expected_pg):
    load = structures_load(structures)
    assert load.cpu == expected_cpu
    assert load.pg == expected_pg


def test_extractor_heads_are_linear():
    assert extractor_heads_load(1).cpu == 110
    assert extractor_heads_load(10).pg == 5500
    assert extractor_heads_load(16).pg == 8800


@pytest.mark.parametrize(
    "link_count, radius, expected_cpu, expected_pg",
    [
        (7, 15000, 359, 261),     # miner 7x (LS), README
        (12, 2500, 255, 176),     # p2p3 12x, README
        (24, 2500, 509, 352),     # p2p3 24x, README
        (24, 30000, 2093, 1540),  # p2p3 24x, README
        (8, 12500, 362, 262),     # p4 8x, README
        (16, 30000, 1396, 1027),  # p4 16x, README
    ],
)
def test_link_formula_reproduces_readme_tables(link_count, radius, expected_cpu, expected_pg):
    """Формула линков должна воспроизводить таблицы README в пределах округления."""
    load = link_load(link_count, radius)
    assert load.cpu == pytest.approx(expected_cpu, abs=1.0)
    assert load.pg == pytest.approx(expected_pg, abs=1.0)


def test_link_load_grows_with_radius():
    """Радиус планеты влияет на расчёт (в v1 колонка Radius не использовалась)."""
    assert link_load(10, 30000).cpu > link_load(10, 2500).cpu
    assert link_load(10, 30000).pg > link_load(10, 2500).pg


def test_miner_00_matches_parsed_template():
    """Справочник miner_00 соответствует разобранному JSON-шаблону."""
    from domain.templates import load_all_templates

    parsed = load_all_templates().get("Miner - 00 - Bacteria")
    if parsed is None:
        pytest.skip("шаблон Miner - 00 - Bacteria отсутствует в data/templates/")
    ref = load_templates()["miner_00"]
    assert parsed.structure_counts() == ref.structures
    assert parsed.link_count == ref.link_count
    assert parsed.extractor_head_count == ref.extractor_heads


def test_miner_00_requires_ccu4_in_practice():
    """
    Расчётное ограничение: 00-шаблон перегружает PG при CCU III и ниже
    на планете любого размера. В источнике это явно не сказано.
    """
    assert not calculate_colony_load("miner_00", 3, 8160).fits
    assert calculate_colony_load("miner_00", 4, 8160).fits


def test_miner_00_fits_any_planet_in_region_at_ccu5():
    """Максимальный радиус в planet_industry.csv — 149390 км (Gas)."""
    assert calculate_colony_load("miner_00", 5, 149390).fits


def test_two_template_variant_requires_ccu5():
    with pytest.raises(UnsupportedSetup):
        calculate_colony_load("p2p3_2factory", ccu_level=4, planet_radius_km=5000)
    calculate_colony_load("p2p3_2factory", ccu_level=5, planet_radius_km=5000)


def test_available_templates_exclude_two_factory_below_ccu5():
    keys_ccu4 = {t.key for t in available_templates_for(4, "Barren")}
    keys_ccu5 = {t.key for t in available_templates_for(5, "Barren")}
    assert "p2p3_2factory" not in keys_ccu4
    assert "p4_2factory" not in keys_ccu4
    assert {"p2p3_2factory", "p4_2factory"} <= keys_ccu5


def test_p4_template_restricted_to_barren_and_temperate():
    calculate_colony_load("p4_1factory", 5, 5000, planet_type="Barren")
    with pytest.raises(UnsupportedSetup):
        calculate_colony_load("p4_1factory", 5, 5000, planet_type="Gas")
    assert not any(t.produces_tier == "P4" for t in available_templates_for(5, "Gas"))


def test_p2p3_two_factory_overloads_above_12500km():
    """Воспроизводит предупреждение источника."""
    assert calculate_colony_load("p2p3_2factory", 5, 12500).fits
    assert not calculate_colony_load("p2p3_2factory", 5, 15000).fits
    assert 12500 <= max_planet_radius_that_fits("p2p3_2factory", 5) < 15000


def test_load_percent_is_not_constant():
    a = calculate_colony_load("p4_1factory", 5, 2500, planet_type="Barren")
    b = calculate_colony_load("p4_1factory", 3, 30000, planet_type="Barren")
    assert a.cpu_percent != b.cpu_percent
    assert a.pg_percent != b.pg_percent
