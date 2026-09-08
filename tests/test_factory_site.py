"""
Тесты domain.factory_site — подбор планет под переработку (P2-P4).

Проверяемые правила проекта:
  - только Barren и Temperate;
  - по возрастанию радиуса;
  - если двойной шаблон не помещается — предупреждение и выбор,
    а не молчаливая замена на одиночный.
"""

from __future__ import annotations

import pandas as pd
import pytest

from domain.factory_site import describe_thresholds, select_factory_sites
from domain.planets import RADIUS_COLUMN, PlanetBook


def _book(rows: list[dict]) -> PlanetBook:
    return PlanetBook(pd.DataFrame(rows).sort_values(RADIUS_COLUMN))


SMALL_SYSTEM = [
    {"System": "HOME", "Planet": "4", "Type": "Barren", RADIUS_COLUMN: 5820},
    {"System": "HOME", "Planet": "7", "Type": "Temperate", RADIUS_COLUMN: 8100},
    {"System": "HOME", "Planet": "9", "Type": "Gas", RADIUS_COLUMN: 64000},
    {"System": "HOME", "Planet": "2", "Type": "Lava", RADIUS_COLUMN: 4000},
]

BIG_SYSTEM = [
    {"System": "BIG", "Planet": "1", "Type": "Barren", RADIUS_COLUMN: 18000},
    {"System": "BIG", "Planet": "3", "Type": "Temperate", RADIUS_COLUMN: 22000},
]


def test_only_barren_and_temperate_are_used():
    """Gas и Lava не должны попадать под переработку, даже если Lava мельче всех."""
    result = select_factory_sites(_book(SMALL_SYSTEM), "HOME", "P2_P3", 5, 4)
    types = {a.candidate.planet_type for a in result.assignments}
    assert types <= {"Barren", "Temperate"}
    assert "Lava" not in types, "Lava радиусом 4000 не должна выбираться, несмотря на малый радиус"


def test_planets_are_taken_smallest_radius_first():
    result = select_factory_sites(_book(SMALL_SYSTEM), "HOME", "P2_P3", 5, 4)
    radii = [a.candidate.radius_km for a in result.assignments]
    assert radii == sorted(radii)
    assert radii[0] == 5820


def test_double_template_used_when_it_fits():
    result = select_factory_sites(_book(SMALL_SYSTEM), "HOME", "P2_P3", 5, 4)
    assert all(a.template_count == 2 for a in result.assignments)
    assert result.satisfied
    assert not result.warnings


def test_warns_and_places_nothing_when_planets_too_large():
    """
    Главное правило: не подставлять одиночный шаблон молча.
    Пользователь должен сначала увидеть предупреждение и выбрать сам.
    """
    result = select_factory_sites(_book(BIG_SYSTEM), "BIG", "P2_P3", 5, 4)
    assert result.assignments == []
    assert result.warnings
    text = " ".join(result.warnings)
    assert "другую домашнюю систему" in text
    assert "по одному шаблону" in text


def test_single_fallback_only_on_explicit_consent():
    result = select_factory_sites(
        _book(BIG_SYSTEM), "BIG", "P2_P3", 5, 4, allow_single_fallback=True
    )
    assert result.downgraded_to_single
    assert result.assignments
    assert all(a.template_count == 1 for a in result.assignments)


def test_warns_when_no_suitable_planet_types_at_all():
    book = _book([{"System": "GASONLY", "Planet": "1", "Type": "Gas", RADIUS_COLUMN: 60000}])
    result = select_factory_sites(book, "GASONLY", "P2_P3", 5, 2)
    assert result.assignments == []
    assert "нет планет типов Barren или Temperate" in " ".join(result.warnings)


def test_ccu4_falls_back_to_single_with_warning():
    """При CCU < 5 двойной вариант недоступен в принципе — предупреждаем сразу."""
    result = select_factory_sites(_book(SMALL_SYSTEM), "HOME", "P2_P3", 4, 2)
    assert all(a.template_count == 1 for a in result.assignments)
    assert "нужен уровень 5" in " ".join(result.warnings)


def test_planet_shortage_is_solved_by_extra_colonies():
    """
    Планет под переработку не может «не хватить»: колония привязана
    к персонажу, поэтому на одной планете размещаются колонии разных
    персонажей. Ограничением остаётся число персонажей, а не планет —
    это правило проекта.
    """
    result = select_factory_sites(_book(SMALL_SYSTEM), "HOME", "P2_P3", 5, 10)
    assert result.satisfied, "нехватка планет не должна блокировать переработку"
    assert not result.warnings
    # Планет всего две пригодные, значит какая-то использована повторно.
    assert max(a.colony_index for a in result.assignments) > 1


def test_reused_planets_keep_smallest_radius_first():
    """Повторный обход идёт в том же порядке — сначала самые мелкие планеты."""
    result = select_factory_sites(_book(SMALL_SYSTEM), "HOME", "P2_P3", 5, 8)
    first_round = [a.candidate.radius_km for a in result.assignments if a.colony_index == 1]
    assert first_round == sorted(first_round)


def test_p4_warning_mentions_unresolved_launchpad_assumption():
    """
    Порог для двойного P4 зависит от неразрешённого расхождения в источнике —
    предупреждение обязано об этом сообщать, иначе пользователь сменит
    систему из-за цифры, которая может оказаться неверной.
    """
    result = select_factory_sites(_book(BIG_SYSTEM), "BIG", "P4", 5, 2)
    text = " ".join(result.warnings)
    assert "причала" in text
    assert "5200" in text


@pytest.mark.parametrize("tier", ["P2_P3", "P4"])
def test_unknown_tier_rejected(tier):
    select_factory_sites(_book(SMALL_SYSTEM), "HOME", tier, 5, 1)
    with pytest.raises(ValueError):
        select_factory_sites(_book(SMALL_SYSTEM), "HOME", "P1", 5, 1)


def test_thresholds_reported_for_ui():
    """Пороги отдаются во фронт, чтобы пользователь видел цифры до расчёта."""
    at5 = describe_thresholds(5)
    assert at5["p2p3_2factory"] is not None
    assert at5["p4_2factory"] is not None
    assert describe_thresholds(4)["p2p3_2factory"] is None, "при CCU 4 двойной недоступен"
