"""
Тесты domain.capacity.

Проверяют, что модель воспроизводит числа из источника
(DalShooth/EVE_PI_Templates) и что загрузка реально зависит
от входных данных — в отличие от v1, где main.py всегда
возвращал pg_load=95 / cpu_load=95.
"""

from __future__ import annotations

import pytest

from domain.capacity import (
    UnsupportedSetup,
    calculate_colony_load,
    command_center_capacity,
    extractor_heads_load,
    link_load,
    load_templates,
    max_planet_radius_that_fits,
)


def test_command_center_capacity_matches_source():
    """Таблица ёмкости CC по уровням CCU."""
    assert command_center_capacity(0).cpu == 1675
    assert command_center_capacity(0).pg == 6000
    assert command_center_capacity(5).cpu == 25415
    assert command_center_capacity(5).pg == 19000


def test_template_totals_match_source():
    templates = load_templates()
    assert templates["miner_p1"].total.cpu == 5700
    assert templates["miner_p1"].total.pg == 9800
    assert templates["p4_1factory"].total.cpu == 12400
    assert templates["p2p3_2factory"].total.pg == 18200


def test_extractor_heads_are_linear():
    """Источник: x1 = 110/550, x10 = 1100/5500, x16 = 1760/8800."""
    assert extractor_heads_load(1).cpu == 110
    assert extractor_heads_load(10).pg == 5500
    assert extractor_heads_load(16).cpu == 1760
    assert extractor_heads_load(16).pg == 8800


def test_link_load_grows_with_radius():
    """
    Ключевая проверка: радиус планеты влияет на расчёт.
    В v1 колонка Radius [km] читалась из CSV и не использовалась вообще.
    """
    small = link_load("p2p3_24x", 2500)
    large = link_load("p2p3_24x", 30000)
    assert large.cpu > small.cpu
    assert large.pg > small.pg
    assert small.cpu == 509 and small.pg == 352
    assert large.cpu == 2093 and large.pg == 1540


def test_link_load_interpolates_between_table_points():
    mid = link_load("p4_8x", 3750)  # ровно между 2500 (170) и 5000 (218)
    assert mid.cpu == pytest.approx(194.0)


def test_two_factory_template_requires_ccu5():
    """Два шаблона на планету доступны только при Command Center Upgrades V."""
    with pytest.raises(UnsupportedSetup):
        calculate_colony_load("p2p3_2factory", ccu_level=4, planet_radius_km=5000)
    # при CCU 5 — проходит
    calculate_colony_load("p2p3_2factory", ccu_level=5, planet_radius_km=5000)


def test_p4_template_restricted_to_barren_and_temperate():
    calculate_colony_load("p4_1factory", 5, 5000, planet_type="Barren")
    with pytest.raises(UnsupportedSetup):
        calculate_colony_load("p4_1factory", 5, 5000, planet_type="Gas")


def test_p2p3_two_factory_overloads_above_12500km():
    """
    Воспроизводит предупреждение источника: с CCU5 и двумя фабриками P2/P3
    командный центр перегружается на планетах радиусом больше 12 500 км.
    """
    assert calculate_colony_load("p2p3_2factory", 5, 12500).fits
    assert not calculate_colony_load("p2p3_2factory", 5, 15000).fits
    limit = max_planet_radius_that_fits("p2p3_2factory", 5)
    assert 12500 <= limit < 15000


def test_load_percent_is_not_constant():
    """Загрузка меняется при изменении входных данных (в v1 была константой)."""
    a = calculate_colony_load("p4_1factory", 5, 2500, planet_type="Barren")
    b = calculate_colony_load("p4_1factory", 3, 30000, planet_type="Barren")
    assert a.cpu_percent != b.cpu_percent
    assert a.pg_percent != b.pg_percent


@pytest.mark.xfail(
    reason="Источник расходится сам с собой: README предупреждает о перегрузке выше 15000 км, "
           "но по его же таблицам предел ~26000 км. Требует сверки с JSON-файлами шаблонов.",
    strict=True,
)
def test_miner_with_16_heads_overloads_above_15000km_per_readme_warning():
    assert not calculate_colony_load("miner_p1", 5, 17500, extractor_head_count=16).fits
