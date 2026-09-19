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
from domain.planets import POCO_RATE_COLUMN, RADIUS_COLUMN, PlanetBook


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


def test_preferred_types_win_even_over_smaller_planets():
    """
    Barren и Temperate идут первыми, даже если планета другого типа
    мельче. Радиус решает только внутри группы.
    """
    result = select_factory_sites(_book(SMALL_SYSTEM), "HOME", "P2_P3", 5, 4)
    types = {a.candidate.planet_type for a in result.assignments}
    assert types <= {"Barren", "Temperate"}
    assert not result.used_fallback_types


def test_p2p3_falls_back_to_other_types_with_warning():
    """
    Barren и Temperate — предпочтение, а не запрет: источник разрешает
    P2/P3 на любом типе. При их отсутствии переработка размещается
    на других типах, но пользователь об этом узнаёт.
    """
    book = _book([
        {"System": "HOME", "Planet": "1", "Type": "Lava", RADIUS_COLUMN: 5000},
        {"System": "HOME", "Planet": "2", "Type": "Plasma", RADIUS_COLUMN: 7000},
    ])
    result = select_factory_sites(book, "HOME", "P2_P3", 5, 2)
    assert result.templates_placed == 2
    assert result.used_fallback_types == ["Lava"]
    assert "нет планет Barren или Temperate" in " ".join(result.warnings)


def test_p4_never_falls_back_because_game_forbids_it():
    """
    Для P4 Barren и Temperate — правило игры, а не наше предпочтение.
    Подменять тип нельзя ни при каких условиях.
    """
    book = _book([
        {"System": "HOME", "Planet": "1", "Type": "Lava", RADIUS_COLUMN: 5000},
    ])
    result = select_factory_sites(book, "HOME", "P4", 5, 2)
    assert result.assignments == []
    text = " ".join(result.warnings)
    assert "ограничение игры" in text
    assert "другую домашнюю систему" in text


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


class TestMaxDoubleTemplates:
    """
    18.09.2026, найдено пользователем: план планировал двойные шаблоны
    на КАЖДУЮ колонию тира, если хоть у одного персонажа во всём пуле
    был CCU V, не считаясь с тем, сколько именно CCU5-персонажей
    реально свободно — реальные CCU5-персонажи заканчивались, и все
    следующие колонии массово проваливались с «не хватило персонажей»,
    хотя двойной шаблон физически помещался. Каждое двойное назначение
    (одна планета) — это ДВА разных персонажа (build_plan() зовёт
    pool.take() дважды с одной и той же планетой; второй раз тот же
    персонаж уже исключён), поэтому предел в слотах делится на 2, чтобы
    получить предел в назначениях.
    """

    def test_unbounded_by_default(self):
        """max_double_templates не передан — прежнее поведение, без ограничения."""
        result = select_factory_sites(_book(SMALL_SYSTEM), "HOME", "P2_P3", 5, 4)
        assert all(a.template_count == 2 for a in result.assignments)
        assert not result.warnings

    def test_falls_back_to_single_once_ccu5_slots_run_out(self):
        """
        4 шаблона нужно (значит по 2 двойных назначения максимум), но
        только 2 CCU5-слота свободно — хватает ровно на ОДНО двойное
        назначение (два персонажа), остаток обязан уйти одиночными,
        не провалиться.
        """
        result = select_factory_sites(
            _book(SMALL_SYSTEM), "HOME", "P2_P3", 5, 4, max_double_templates=2
        )
        assert result.satisfied
        doubles = [a for a in result.assignments if a.template_count == 2]
        singles = [a for a in result.assignments if a.template_count == 1]
        assert len(doubles) == 1
        assert singles
        assert any("не хватило" in w for w in result.warnings)

    def test_zero_slots_means_all_single(self):
        result = select_factory_sites(
            _book(SMALL_SYSTEM), "HOME", "P2_P3", 5, 4, max_double_templates=0
        )
        assert result.satisfied
        assert all(a.template_count == 1 for a in result.assignments)

    def test_enough_slots_means_no_downgrade_note(self):
        """Предела хватает с запасом — предупреждения о нехватке CCU5 быть не должно."""
        result = select_factory_sites(
            _book(SMALL_SYSTEM), "HOME", "P2_P3", 5, 4, max_double_templates=100
        )
        assert all(a.template_count == 2 for a in result.assignments)
        assert not result.warnings


class TestMinCcuLevel:
    """
    18.09.2026, найдено пользователем: назначение переработки не несло
    минимальный CCU, при котором именно этот шаблон на именно этой
    планете физически помещается — build_plan() мог отдать колонию
    персонажу с CCU 0-1, который её по игре не построит (тот же пробел,
    что уже был закрыт для добычи через min_ccu_level_that_fits("miner_00", radius)).
    """

    def test_single_assignment_carries_a_positive_min_ccu(self):
        result = select_factory_sites(_book(SMALL_SYSTEM), "HOME", "P2_P3", 5, 2, max_double_templates=0)
        assert result.assignments
        assert all(a.min_ccu_level > 0 for a in result.assignments)

    def test_double_assignment_min_ccu_is_five(self):
        result = select_factory_sites(_book(SMALL_SYSTEM), "HOME", "P2_P3", 5, 4)
        doubles = [a for a in result.assignments if a.template_count == 2]
        assert doubles and all(a.min_ccu_level == 5 for a in doubles)

    def test_bigger_planet_needs_higher_min_ccu(self):
        """Радиус влияет на минимальный CCU — не константа тира."""
        small = select_factory_sites(
            _book([{"System": "HOME", "Planet": "1", "Type": "Barren", RADIUS_COLUMN: 2000}]),
            "HOME", "P2_P3", 5, 1, max_double_templates=0,
        )
        big = select_factory_sites(
            _book([{"System": "HOME", "Planet": "1", "Type": "Barren", RADIUS_COLUMN: 90000}]),
            "HOME", "P2_P3", 5, 1, max_double_templates=0, allow_single_fallback=True,
        )
        assert small.assignments and big.assignments
        assert big.assignments[0].min_ccu_level >= small.assignments[0].min_ccu_level


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


def test_warns_when_system_has_no_planets_at_all():
    book = _book([{"System": "OTHER", "Planet": "1", "Type": "Gas", RADIUS_COLUMN: 60000}])
    result = select_factory_sites(book, "EMPTY", "P2_P3", 5, 2)
    assert result.assignments == []
    assert "вообще нет планет" in " ".join(result.warnings)


def test_huge_fallback_planet_is_reported_as_not_fitting():
    """
    Газовый гигант формально допустим для P2/P3, но двойной шаблон
    на нём не помещается — об этом должно быть сказано, а не тихо
    размещено что попало.
    """
    book = _book([{"System": "HOME", "Planet": "1", "Type": "Gas", RADIUS_COLUMN: 60000}])
    result = select_factory_sites(book, "HOME", "P2_P3", 5, 2)
    assert " ".join(result.warnings)


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


def test_order_is_preferred_type_then_radius():
    """
    Порядок двухуровневый: сначала предпочтительные типы, внутри них —
    по возрастанию радиуса. Проверять просто «по радиусу» больше нельзя:
    мелкая Lava идёт после крупной Barren, и это правильно.
    """
    result = select_factory_sites(_book(SMALL_SYSTEM), "HOME", "P2_P3", 5, 8)
    first_round = [a.candidate for a in result.assignments if a.colony_index == 1]

    preferred = [c.radius_km for c in first_round if c.preferred]
    assert preferred == sorted(preferred), "внутри предпочтительных — по радиусу"

    # Ни одна непредпочтительная планета не должна опередить предпочтительную.
    flags = [c.preferred for c in first_round]
    assert flags == sorted(flags, reverse=True)


def test_p4_warning_mentions_unresolved_launchpad_assumption():
    """
    Порог для двойного P4 зависит от расхождения в источнике, закрытого без
    проверки в игре (нет подходящей планеты в загруженном регионе) —
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


def test_does_not_fall_back_to_other_types_when_preferred_can_host_all_colonies():
    """
    17.09.2026, найдено пользователем на реальном плане: раньше при
    потребности больше числа РАЗЛИЧНЫХ предпочтительных планет
    переработка всё равно уходила на другие типы, хотя одна планета
    несёт неограниченное число колоний (правило 4) и двух предпочтительных
    (Barren + Temperate) хватало с большим запасом.
    """
    result = select_factory_sites(_book(SMALL_SYSTEM), "HOME", "P2_P3", 5, 20)
    assert result.satisfied
    assert not result.used_fallback_types
    assert not result.warnings
    types = {a.candidate.planet_type for a in result.assignments}
    assert types <= {"Barren", "Temperate"}


def test_lower_tax_preferred_planet_used_first_even_if_larger():
    """
    Среди предпочтительных планет ставка POCO — критерий выбора наравне
    с радиусом, и по прямому запросу пользователя (17.09.2026) стоит
    впереди него: планета с меньшим налогом используется первой, даже
    если она крупнее (обе всё равно помещаются под шаблон).
    """
    book = _book([
        {"System": "HOME", "Planet": 4, "Type": "Barren", RADIUS_COLUMN: 5000,
         POCO_RATE_COLUMN: 10},
        {"System": "HOME", "Planet": 7, "Type": "Temperate", RADIUS_COLUMN: 9000,
         POCO_RATE_COLUMN: 3},
    ])
    result = select_factory_sites(book, "HOME", "P2_P3", 5, 2)
    assert len(result.assignments) == 1
    assert result.assignments[0].candidate.planet == "7"  # ниже налог, хоть и крупнее


def test_warns_and_marks_fallback_types_when_preferred_too_big_for_template():
    """
    Отличается от site_no_preferred: предпочтительные планеты в системе
    ЕСТЬ, но ни одна не помещается под шаблон по размеру — переработка
    уходит на другие типы, и это должно быть явно сказано, а не тихо.
    """
    book = _book([
        {"System": "HOME", "Planet": "1", "Type": "Barren", RADIUS_COLUMN: 18000},
        {"System": "HOME", "Planet": "2", "Type": "Lava", RADIUS_COLUMN: 5000},
    ])
    result = select_factory_sites(book, "HOME", "P2_P3", 5, 2)
    assert result.used_fallback_types == ["Lava"]
    text = " ".join(result.warnings)
    assert "другие типы планет" in text
    assert "Lava" in text


def test_warns_when_some_colonies_end_up_on_higher_tax_planet():
    """
    17.09.2026, по прямому запросу пользователя: если часть переработки
    вынужденно ушла на планету с более высокой ставкой POCO, чем у
    лучшего использованного варианта, план остаётся рабочим, но
    предупреждает и называет конкретные планеты — не блокирует выбор
    домашней системы, просто честно указывает на разницу.
    """
    book = _book([
        # Помещается и под двойной, и под одиночный шаблон, но налог выше.
        {"System": "HOME", "Planet": 4, "Type": "Barren", RADIUS_COLUMN: 5820,
         POCO_RATE_COLUMN: 10},
        # Налог ниже, но слишком крупная под двойной — только одиночный.
        {"System": "HOME", "Planet": 7, "Type": "Temperate", RADIUS_COLUMN: 18000,
         POCO_RATE_COLUMN: 1},
    ])
    result = select_factory_sites(book, "HOME", "P2_P3", 5, 5)  # 2 двойных + 1 одиночный
    assert result.satisfied
    rates_used = {a.candidate.poco_rate for a in result.assignments}
    assert rates_used == {0.10, 0.01}
    text = " ".join(result.warnings)
    assert "выше" in text and "1%" in text


def test_thresholds_reported_for_ui():
    """Пороги отдаются во фронт, чтобы пользователь видел цифры до расчёта."""
    at5 = describe_thresholds(5)
    assert at5["p2p3_2factory"] is not None
    assert at5["p4_2factory"] is not None
    assert describe_thresholds(4)["p2p3_2factory"] is None, "при CCU 4 двойной недоступен"
