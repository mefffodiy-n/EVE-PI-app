"""
domain/logistics.py: пропускная способность причала между тирами
переработки — сколько времени фабрика реально работает, а не
простаивает в ожидании новой партии сырья.

Модель и её обоснование — докстринг domain/logistics.py. Здесь —
арифметика: два входа против трёх при одной и той же ёмкости причала
должны давать РАЗНЫЙ duty cycle (то самое наблюдение пользователя,
из-за которого фича появилась), а честный пробел в данных об объёме
не должен выдумывать число.
"""

from __future__ import annotations

import json

import pytest

from domain import logistics
from domain.throughput import Schematic


@pytest.fixture(autouse=True)
def _isolated_caches(monkeypatch, tmp_path):
    """Каждый тест — со своим снимком reference/type_ids/volumes, не общим."""
    logistics._reference.cache_clear()
    logistics.launchpad_capacity_m3.cache_clear()
    logistics.logistics_downtime_hours.cache_clear()
    logistics._type_ids.cache_clear()
    logistics._type_volumes.cache_clear()
    yield
    logistics._reference.cache_clear()
    logistics.launchpad_capacity_m3.cache_clear()
    logistics.logistics_downtime_hours.cache_clear()
    logistics._type_ids.cache_clear()
    logistics._type_volumes.cache_clear()


def _write_reference(path, capacity_m3=10_000, downtime_hours=0.5):
    path.write_text(json.dumps({
        "storage_capacity_m3": {"launchpad": capacity_m3},
        "assumptions": {"logistics_refill_downtime_hours": {"value": downtime_hours}},
    }), encoding="utf-8")


def _setup(monkeypatch, tmp_path, type_ids: dict[str, int], volumes: dict[str, float],
           capacity_m3=10_000, downtime_hours=0.5):
    ref_path = tmp_path / "pi_reference.json"
    ids_path = tmp_path / "type_ids.json"
    vol_path = tmp_path / "type_volumes.json"
    _write_reference(ref_path, capacity_m3, downtime_hours)
    ids_path.write_text(json.dumps(type_ids), encoding="utf-8")
    vol_path.write_text(json.dumps({str(tid): v for tid, v in volumes.items()}), encoding="utf-8")

    monkeypatch.setattr(logistics, "REFERENCE_PATH", ref_path)
    monkeypatch.setattr(logistics, "TYPE_IDS_PATH", ids_path)
    monkeypatch.setattr(logistics, "TYPE_VOLUMES_PATH", vol_path)


class TestVolumeOf:
    def test_returns_none_without_type_id_file(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, type_ids={}, volumes={})
        assert logistics.volume_of("Water") is None

    def test_returns_none_when_type_id_known_but_volume_missing(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, type_ids={"Water": 3778}, volumes={})
        assert logistics.volume_of("Water") is None

    def test_returns_volume_when_both_present(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, type_ids={"Water": 3778}, volumes={3778: 0.38})
        assert logistics.volume_of("Water") == 0.38


class TestOwnDutyCycle:
    def test_more_and_heavier_inputs_drain_the_causeway_faster(self, monkeypatch, tmp_path):
        """
        Ровно то наблюдение пользователя, из-за которого появилась
        фича: при ОДНОМ И ТОМ ЖЕ расходе каждого вида сырья в час и
        одинаковом объёме единицы, рецепт с 3 видами входов держит
        причал меньше, чем рецепт с 2 — сумма расхода в м³/ч больше.
        Ёмкость причала намеренно маленькая (не настоящие 10 000 м³),
        чтобы время опустошения было сопоставимо с простоем на
        довозку и эффект был виден, а не тонул в округлении.
        """
        type_ids = {"P1_A": 1, "P1_B": 2, "P2_A": 3, "P2_B": 4, "P2_C": 5}
        volumes = {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0}
        _setup(monkeypatch, tmp_path, type_ids, volumes, capacity_m3=1_000, downtime_hours=0.5)

        p2_schematic = Schematic(
            product="P2X", facility="advanced_industry_facility",
            inputs={"P1_A": 100.0, "P1_B": 100.0}, output_qty=10.0, cycle_minutes=60.0,
        )
        p3_schematic = Schematic(
            product="P3X", facility="advanced_industry_facility",
            inputs={"P2_A": 100.0, "P2_B": 100.0, "P2_C": 100.0}, output_qty=6.0, cycle_minutes=60.0,
        )

        p2_duty, p2_missing = logistics.own_duty_cycle(p2_schematic)
        p3_duty, p3_missing = logistics.own_duty_cycle(p3_schematic)

        assert p2_missing == p3_missing == []
        assert 0 < p3_duty < p2_duty < 1

    def test_missing_volume_defaults_to_full_duty_cycle_not_a_guess(self, monkeypatch, tmp_path):
        """Честный пробел (правило 1) — не штраф, не выдуманное число."""
        _setup(monkeypatch, tmp_path, type_ids={"Water": 1}, volumes={})  # объёма нет
        schematic = Schematic(
            product="Coolant", facility="advanced_industry_facility",
            inputs={"Water": 50.0}, output_qty=10.0, cycle_minutes=60.0,
        )
        duty, missing = logistics.own_duty_cycle(schematic)
        assert duty == 1.0
        assert missing == ["Water"]

    def test_no_inputs_is_full_duty_cycle(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, type_ids={}, volumes={})
        schematic = Schematic(product="Water", facility="basic_industry_facility",
                               inputs={}, output_qty=100.0, cycle_minutes=30.0)
        duty, missing = logistics.own_duty_cycle(schematic)
        assert duty == 1.0
        assert missing == []

    def test_smaller_causeway_or_longer_downtime_lowers_duty_cycle(self, monkeypatch, tmp_path):
        type_ids = {"Water": 1}
        volumes = {1: 0.38}
        schematic = Schematic(product="Coolant", facility="advanced_industry_facility",
                               inputs={"Water": 50.0}, output_qty=10.0, cycle_minutes=60.0)

        _setup(monkeypatch, tmp_path, type_ids, volumes, capacity_m3=10_000, downtime_hours=0.5)
        duty_normal, _ = logistics.own_duty_cycle(schematic)

        logistics._reference.cache_clear()
        logistics.launchpad_capacity_m3.cache_clear()
        logistics.logistics_downtime_hours.cache_clear()
        _setup(monkeypatch, tmp_path, type_ids, volumes, capacity_m3=10_000, downtime_hours=5.0)
        duty_more_downtime, _ = logistics.own_duty_cycle(schematic)

        assert duty_more_downtime < duty_normal
