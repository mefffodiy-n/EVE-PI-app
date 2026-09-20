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


class TestOwnConsumptionProfile:
    def test_returns_m3_per_hour_for_every_input(self, monkeypatch, tmp_path):
        """
        Расход м³/час на ВЕСЬ шаблон колонии (не одну фабрику) — найденный
        пользователем баг 20.09.2026: причал общий на 12 advanced-фабрик
        (P2/P3), а расход считался так, будто причал обслуживает одну.
        """
        type_ids = {"Water": 1, "Electrolytes": 2}
        volumes = {1: 0.19, 2: 0.19}
        _setup(monkeypatch, tmp_path, type_ids, volumes, capacity_m3=10_000, downtime_hours=0.5)
        schematic = Schematic(
            product="Coolant", facility="advanced_industry_facility",
            inputs={"Water": 40.0, "Electrolytes": 40.0}, output_qty=10.0, cycle_minutes=60.0,
        )

        profile, missing = logistics.own_consumption_profile(schematic)

        factories = logistics.FACILITY_FACTORIES_PER_COLONY["advanced_industry_facility"]
        assert missing == []
        assert profile["Water"] == pytest.approx(40.0 * factories * 0.19)
        assert profile["Electrolytes"] == pytest.approx(40.0 * factories * 0.19)

    def test_missing_volume_excludes_the_whole_schematic_not_a_guess(self, monkeypatch, tmp_path):
        """Честный пробел (правило 1) — вся схема выходит из модели, не штраф на один вход."""
        type_ids = {"Water": 1, "Electrolytes": 2}
        volumes = {1: 0.19}  # у Electrolytes объёма нет
        _setup(monkeypatch, tmp_path, type_ids, volumes)
        schematic = Schematic(
            product="Coolant", facility="advanced_industry_facility",
            inputs={"Water": 40.0, "Electrolytes": 40.0}, output_qty=10.0, cycle_minutes=60.0,
        )

        profile, missing = logistics.own_consumption_profile(schematic)

        assert profile == {}
        assert missing == ["Electrolytes"]

    def test_basic_industry_facility_never_enters_the_causeway_model(self, monkeypatch, tmp_path):
        """
        P1 добывается и подаётся на фабрику той же колонии непрерывно -
        это не партия, которую физически возит игрок с другой колонии,
        поэтому простой на логистику к нему не относится вовсе (не
        просто "нет данных об объёме" - структурно исключён).
        """
        type_ids = {"Aqueous Liquids": 1}
        volumes = {1: 0.38}
        schematic = Schematic(
            product="Water", facility="basic_industry_facility",
            inputs={"Aqueous Liquids": 3000.0}, output_qty=100.0, cycle_minutes=30.0,
        )
        _setup(monkeypatch, tmp_path, type_ids, volumes)

        profile, missing = logistics.own_consumption_profile(schematic)

        assert profile == {}
        assert missing == []

    def test_no_inputs_gives_empty_profile(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, type_ids={}, volumes={})
        schematic = Schematic(product="X", facility="advanced_industry_facility",
                               inputs={}, output_qty=100.0, cycle_minutes=30.0)
        profile, missing = logistics.own_consumption_profile(schematic)
        assert profile == {}
        assert missing == []
