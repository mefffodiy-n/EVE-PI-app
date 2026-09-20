"""
domain/throughput.py::expand_demand — каскад партий-эстафеты причала
(Demand.duty_cycles/missing_volumes).

20.09.2026, второй пересмотр за день: пользователь (реальный человек,
не бот) указал, что модель "непрерывного ручейка" (простой апстрима
просто домножает duty cycle) физически неверна — он не может возить
сырьё между колониями 24/7 мелкими партиями. Верная модель — партиями-
эстафетой: продукт получает НОВУЮ партию сырья только когда апстрим
целиком опустошит СВОЙ причал и довезёт результат одним рейсом; пока
партия не пришла — простаивает, даже если сам давно освободился. Темп
всей цепочки задаёт САМОЕ МЕДЛЕННОЕ звено (обычно верхний, самый
"тяжёлый" по числу видов сырья тир), а не среднее по цепочке.

Здесь проверяется КАСКАД на реальных числах (не заглушках): схемы
настоящие (Schematic с реальными output_qty/cycle_minutes/inputs),
`volume_of`/`launchpad_capacity_m3`/`logistics_downtime_hours` —
через ту же изоляцию reference/type_ids/volumes, что и в
tests/test_logistics.py (реальный `FACILITY_FACTORIES_PER_COLONY` НЕ
мокается — это проверенная игровая константа, не часть сценария теста).
"""

from __future__ import annotations

import json

import pytest

from domain import logistics
from domain.recipes import Recipe, RecipeBook
from domain.throughput import Schematic, expand_demand


@pytest.fixture(autouse=True)
def _isolated_caches(monkeypatch, tmp_path):
    """Тот же приём, что и в test_logistics.py — свой снимок на тест, не общий."""
    for fn in (logistics._reference, logistics.launchpad_capacity_m3,
               logistics.logistics_downtime_hours, logistics._type_ids, logistics._type_volumes):
        fn.cache_clear()
    yield
    for fn in (logistics._reference, logistics.launchpad_capacity_m3,
               logistics.logistics_downtime_hours, logistics._type_ids, logistics._type_volumes):
        fn.cache_clear()


def _setup(monkeypatch, tmp_path, type_ids: dict[str, int], volumes: dict[str, float],
           capacity_m3=1_000, downtime_hours=0.5):
    ref_path = tmp_path / "pi_reference.json"
    ids_path = tmp_path / "type_ids.json"
    vol_path = tmp_path / "type_volumes.json"
    ref_path.write_text(json.dumps({
        "storage_capacity_m3": {"launchpad": capacity_m3},
        "assumptions": {"logistics_refill_downtime_hours": {"value": downtime_hours}},
    }), encoding="utf-8")
    ids_path.write_text(json.dumps(type_ids), encoding="utf-8")
    vol_path.write_text(json.dumps({str(tid): v for tid, v in volumes.items()}), encoding="utf-8")

    monkeypatch.setattr(logistics, "REFERENCE_PATH", ref_path)
    monkeypatch.setattr(logistics, "TYPE_IDS_PATH", ids_path)
    monkeypatch.setattr(logistics, "TYPE_VOLUMES_PATH", vol_path)


def _recipes() -> RecipeBook:
    return RecipeBook({
        "Water": Recipe(name="Water", tier="P1", source="Aqueous Liquids"),
        "Coolant": Recipe(name="Coolant", tier="P2", inputs={"Water": 1}),
        "Fuel Block": Recipe(name="Fuel Block", tier="P3", inputs={"Coolant": 1}),
    })


def _schematics(fuel_block_input_per_hour: float = 5.0) -> dict[str, Schematic]:
    """
    Coolant: причал (1000 м³) держит 100 ед./ч Water (объём 1.0) —
    drain=0.8333ч, цикл=1.3333ч (с простоем 0.5ч), партия=10 ед.
    Fuel Block: получает эту партию (10 ед. Coolant, объём 1.0 = 10 м³
    — вдесятеро меньше своей полной ёмкости причала), съедает её за
    drain_from_upstream часов — параметр `fuel_block_input_per_hour`
    двигает этот расход, чтобы получить два разных сценария в тестах
    ниже (апстрим задаёт темп vs у Fuel Block самой не хватило бы
    времени даже на приехавшую партию, случай не встречается здесь,
    но параметр оставлен для читаемости чисел).
    """
    return {
        "Water": Schematic(product="Water", facility="basic_industry_facility",
                            inputs={}, output_qty=100.0, cycle_minutes=30.0),
        "Coolant": Schematic(product="Coolant", facility="advanced_industry_facility",
                              inputs={"Water": 100.0}, output_qty=1.0, cycle_minutes=60.0),
        "Fuel Block": Schematic(product="Fuel Block", facility="advanced_industry_facility",
                                 inputs={"Coolant": fuel_block_input_per_hour}, output_qty=1.0, cycle_minutes=60.0),
    }


class TestRelayCascade:
    def test_downstream_is_paced_by_the_slower_upstream_cycle(self, monkeypatch, tmp_path):
        """
        Ровно сценарий пользователя: Fuel Block съедает партию от
        Coolant за 0.1667ч (расход задан большим — fuel_block_input_
        per_hour=5.0), а сам простой на довозку занял бы всего 0.6667ч
        цикла — НО темп задаёт Coolant (цикл 1.3333ч, куда дольше),
        поэтому Fuel Block простаивает и его цикл РАВЕН циклу Coolant,
        а не своему более короткому.
        """
        _setup(monkeypatch, tmp_path, type_ids={"Water": 1, "Coolant": 2},
               volumes={1: 1.0, 2: 1.0}, capacity_m3=1_000, downtime_hours=0.5)

        demand = expand_demand(
            {"Fuel Block": 10.0}, schematics=_schematics(fuel_block_input_per_hour=5.0),
            recipes=_recipes(),
        )

        assert demand.duty_cycles["Coolant"] == pytest.approx(0.625, abs=1e-3)
        # Заметно НИЖЕ, чем "непрерывный ручеёк" дал бы для Fuel Block
        # (тот считал бы её независимо от объёма партии Coolant).
        assert demand.duty_cycles["Fuel Block"] == pytest.approx(0.125, abs=1e-3)
        assert demand.duty_cycles["Fuel Block"] < demand.duty_cycles["Coolant"]

    def test_true_p0_raw_material_boundary_does_not_get_a_duty_cycle_entry(self, monkeypatch, tmp_path):
        """
        "Aqueous Liquids" (recipe.source сырья Water) — настоящее сырьё
        P0, у него нет ни рецепта, ни схемы, оно попадает в
        demand.raw_materials, не в demand.factories, и не ограничивает
        Water никак (Water — P1, basic_industry_facility, исключён из
        модели целиком — см. docstring domain/logistics.py).
        """
        _setup(monkeypatch, tmp_path, type_ids={"Water": 1, "Coolant": 2},
               volumes={1: 1.0, 2: 1.0})

        demand = expand_demand(
            {"Fuel Block": 10.0}, schematics=_schematics(), recipes=_recipes(),
        )

        assert "Aqueous Liquids" in demand.raw_materials
        assert "Aqueous Liquids" not in demand.duty_cycles
        assert demand.duty_cycles["Water"] == 1.0

    def test_purchased_p1_is_a_continuous_boundary_like_raw_material(self, monkeypatch, tmp_path):
        """
        Сценарий пользователя дословно: P1 закупается (не в
        demand.factories вовсе, только в demand.purchased_p1) — Coolant
        (P2) получает ПОЛНЫЙ причал каждый раз (закупка непрерывна и
        неограничена), его duty cycle равен собственному drain/cycle,
        как если бы Water вообще не участвовал в дереве.
        """
        _setup(monkeypatch, tmp_path, type_ids={"Water": 1, "Coolant": 2},
               volumes={1: 1.0, 2: 1.0})

        demand = expand_demand(
            {"Fuel Block": 10.0}, schematics=_schematics(), recipes=_recipes(),
            purchase_p1=True,
        )

        assert demand.purchased_p1.get("Water")
        assert "Water" not in demand.duty_cycles
        assert demand.duty_cycles["Coolant"] == pytest.approx(0.625, abs=1e-3)

    def test_missing_volumes_collected_across_the_whole_tree(self, monkeypatch, tmp_path):
        """У Coolant нет объёма (Water не в type_ids) — вся схема Coolant
        честно выходит из модели, и Fuel Block автоматически теряет
        апстрим-ограничение (Coolant для него как P1: не в profile)."""
        _setup(monkeypatch, tmp_path, type_ids={"Coolant": 2}, volumes={2: 1.0})

        demand = expand_demand(
            {"Fuel Block": 10.0}, schematics=_schematics(), recipes=_recipes(),
        )

        assert demand.missing_volumes == ["Water"]
        assert demand.duty_cycles["Coolant"] == 1.0
