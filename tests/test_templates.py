"""
Тесты разбора JSON-шаблонов застройки (domain.templates).
"""

from __future__ import annotations

import pytest

from domain.templates import TemplateParseError, load_all_templates, parse_template


def _sample():
    templates = load_all_templates()
    if not templates:
        pytest.skip("data/templates/ пуста")
    return next(iter(templates.values()))


def test_templates_parse_without_errors():
    templates = load_all_templates()
    assert templates, "не удалось разобрать ни одного шаблона"


def test_pins_links_and_routes_are_consistent():
    tpl = _sample()
    valid = set(range(1, len(tpl.pins) + 1))
    for link in tpl.links:
        assert link.source in valid and link.destination in valid
    for route in tpl.routes:
        assert set(route.path) <= valid


def test_miner_00_structure_composition():
    """00-майнер: 1 экстрактор с 10 головами, 8 basic-фабрик, storage, launchpad."""
    tpl = load_all_templates().get("Miner - 00 - Bacteria")
    if tpl is None:
        pytest.skip("шаблон отсутствует")
    assert tpl.structure_counts() == {
        "extractor_control_unit": 1,
        "basic_industry_facility": 8,
        "storage_facility": 1,
        "launchpad": 1,
    }
    assert tpl.extractor_head_count == 10
    assert tpl.link_count == 10


def test_ls_templates_are_skipped():
    """Варианты LS исключены решением проекта."""
    assert not any(" - LS - " in (t.source_file or "") for t in load_all_templates().values())


def test_unknown_pin_type_raises():
    """
    Незнакомый type_id должен ронять разбор, а не подставлять 'прочее':
    иначе расчёт CPU/PG по такому шаблону молча занизится.
    """
    broken = {
        "CmdCtrLv": 5,
        "P": [{"H": 0, "La": 0.0, "Lo": 0.0, "S": None, "T": 999999}],
        "L": [],
    }
    with pytest.raises(TemplateParseError):
        parse_template(broken)


def test_with_command_center_level_rewrites_field():
    """
    Подстановка CmdCtrLv под уровень персонажа — та правка, которую
    README источника предлагает делать вручную.
    """
    tpl = _sample()
    updated = tpl.with_command_center_level(4)
    assert updated["CmdCtrLv"] == 4
    assert tpl.raw["CmdCtrLv"] == 5, "исходный шаблон не должен меняться"
    assert updated["P"] == tpl.raw["P"], "остальное содержимое должно сохраниться"


def test_with_command_center_level_validates_range():
    tpl = _sample()
    with pytest.raises(ValueError):
        tpl.with_command_center_level(6)
