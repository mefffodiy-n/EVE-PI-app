"""
Тесты для domain.planner.build_plan().

В v1 тестов не было вообще — вся проверка расчётной логики шла "на глаз"
через ручной запуск сервера. Для новой версии это неприемлемо, так как
capacity.py/link_penalty.py вводят новую логику, которой раньше не было,
и её нужно проверять числами.

Заполняется по мере реализации build_plan() в Фазе 1. Заготовки ниже
фиксируют минимальный набор сценариев, которые обязаны быть покрыты.
"""

from __future__ import annotations

import pytest


def test_build_plan_single_p4_target_with_dev_characters():
    """
    Базовый happy-path: один целевой продукт P4, персонажи из
    seed_dev_characters.DEV_CHARACTERS, все R0-ресурсы доступны в регионе.
    """
    pytest.skip("TODO(Фаза 1): реализовать вместе с domain.planner.build_plan")


def test_build_plan_reports_deficit_when_not_enough_factory_planets():
    """
    Аналог warning "ДЕФИЦИТ СБОРКИ" из v1 (main.py, calculate_plan) —
    убедиться, что при нехватке планет под переработку planner.py
    сообщает об этом явно, а не молча урезает план.
    """
    pytest.skip("TODO(Фаза 1)")


def test_build_plan_missing_raw_resource_in_region_raises_warning():
    """
    Аналог "КРИТИЧЕСКИЙ ДЕФИЦИТ: Нет планет для добычи" из v1 —
    если региона не хватает какого-то R0, план должен явно предупредить,
    а не молча пропустить назначение.
    """
    pytest.skip("TODO(Фаза 1)")
