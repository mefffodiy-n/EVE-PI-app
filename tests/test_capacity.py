"""
Тесты для domain.capacity.

Цель — зафиксировать, что:
  1. JSON-шаблоны застройки из data/templates/ реально читаются
     (в v1 они лежали в репозитории мёртвым грузом);
  2. процент загрузки CPU/PG зависит от входных данных, а не является
     константой (в v1 main.py всегда возвращал pg_load=95, cpu_load=95).
"""

from __future__ import annotations

import pytest

from domain.capacity import load_templates


def test_templates_are_loaded_from_data_dir():
    """Шаблоны застройки читаются и парсятся."""
    templates = load_templates()
    assert templates, "data/templates/ пуста — шаблоны застройки не найдены"
    assert all(t.total_cpu > 0 and t.total_pg > 0 for t in templates.values())


def test_miner_template_matches_source_file():
    """Значения из miner_p1.json загружаются без искажений."""
    templates = load_templates()
    miner = templates.get("miner_p1")
    if miner is None:
        pytest.skip("miner_p1.json отсутствует в data/templates/")
    assert miner.total_cpu == 12400
    assert miner.total_pg == 16800


def test_load_percent_depends_on_input():
    """
    Загрузка должна меняться при разных шаблонах/уровнях CCU.

    Реализуется вместе с COMMAND_CENTER_CAPACITY — до этого
    calculate_colony_load() сознательно не реализована, чтобы
    не считать проценты от выдуманного лимита.
    """
    pytest.skip("TODO(Фаза 1): требует заполнения COMMAND_CENTER_CAPACITY")


def test_higher_ccu_level_increases_available_capacity():
    """Более высокий уровень Command Center Upgrades даёт больше базового CPU/PG."""
    pytest.skip("TODO(Фаза 1): требует заполнения COMMAND_CENTER_CAPACITY")
