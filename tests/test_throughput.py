"""
domain/throughput.py::expand_demand — каскадная пропускная способность
причала (Demand.duty_cycles/missing_volumes).

Арифметика самого duty cycle (объём/ёмкость/простой) уже проверена в
tests/test_logistics.py — здесь own_duty_cycle() подменяется на
управляемые значения, чтобы изолированно проверить КАСКАД: продукт не
может работать стабильнее самого нестабильного из своих собственных
входов, произведённых в этом же плане (правило, согласованное
пользователем 20.09.2026 — сценарий «пока перерабатываю P2 из
закупленного P1, причалы P3 будут простаивать»).
"""

from __future__ import annotations

from domain import throughput as throughput_module
from domain.recipes import Recipe, RecipeBook
from domain.throughput import Schematic, expand_demand


def _recipes(purchase_p1_tier_for=()) -> RecipeBook:
    return RecipeBook({
        "Water": Recipe(name="Water", tier="P1", source="Aqueous Liquids"),
        "Coolant": Recipe(name="Coolant", tier="P2", inputs={"Water": 1}),
        "Fuel Block": Recipe(name="Fuel Block", tier="P3", inputs={"Coolant": 1}),
    })


def _schematics() -> dict[str, Schematic]:
    return {
        "Water": Schematic(product="Water", facility="basic_industry_facility",
                            inputs={}, output_qty=100.0, cycle_minutes=30.0),
        "Coolant": Schematic(product="Coolant", facility="advanced_industry_facility",
                              inputs={"Water": 50.0}, output_qty=10.0, cycle_minutes=60.0),
        "Fuel Block": Schematic(product="Fuel Block", facility="advanced_industry_facility",
                                 inputs={"Coolant": 5.0}, output_qty=5.0, cycle_minutes=60.0),
    }


def _mock_duty_cycles(monkeypatch, per_product: dict[str, float]):
    """own_duty_cycle() управляемый: по имени продукта схемы, не по объёму/ёмкости."""
    def fake(schematic):
        return per_product.get(schematic.product, 1.0), []

    monkeypatch.setattr(throughput_module, "own_duty_cycle", fake)


class TestDutyCycleCascade:
    def test_bottleneck_upstream_caps_downstream_not_just_its_own_row(self, monkeypatch):
        """
        Ровно сценарий пользователя: Coolant (P2, из закупленного P1)
        сам по себе простаивает (own duty cycle 0.6) — Fuel Block (P3)
        не может работать стабильнее него, даже если у Fuel Block
        собственный причал был бы идеальным (own duty cycle 1.0).
        """
        _mock_duty_cycles(monkeypatch, {"Water": 1.0, "Coolant": 0.6, "Fuel Block": 1.0})
        demand = expand_demand(
            {"Fuel Block": 10.0}, schematics=_schematics(), recipes=_recipes(),
        )
        assert demand.duty_cycles["Coolant"] == 0.6
        # Fuel Block ограничен минимумом с Coolant, а не собственной единицей.
        assert demand.duty_cycles["Fuel Block"] == 0.6

    def test_no_bottleneck_means_full_duty_cycle_all_the_way_up(self, monkeypatch):
        _mock_duty_cycles(monkeypatch, {"Water": 1.0, "Coolant": 1.0, "Fuel Block": 1.0})
        demand = expand_demand(
            {"Fuel Block": 10.0}, schematics=_schematics(), recipes=_recipes(),
        )
        assert demand.duty_cycles["Coolant"] == 1.0
        assert demand.duty_cycles["Fuel Block"] == 1.0

    def test_true_p0_raw_material_boundary_does_not_get_a_duty_cycle_entry(self, monkeypatch):
        """
        "Aqueous Liquids" (recipe.source сырья Water) — настоящее сырьё
        P0, у него нет ни рецепта, ни схемы, оно попадает в
        demand.raw_materials, не в demand.factories. Его "простой" не
        существует как отдельная сущность — он уже целиком выражен
        через own duty cycle Water (P1), которая его потребляет.
        """
        _mock_duty_cycles(monkeypatch, {"Water": 0.9, "Coolant": 0.8, "Fuel Block": 1.0})
        demand = expand_demand(
            {"Fuel Block": 10.0}, schematics=_schematics(), recipes=_recipes(),
        )
        assert "Aqueous Liquids" in demand.raw_materials
        assert "Aqueous Liquids" not in demand.duty_cycles
        # Coolant (P2) каскадно ограничен И своим причалом (0.8), И тем,
        # что Water (P1) само по себе тоже не идеально (0.9) — простои
        # НАКАПЛИВАЮТСЯ (умножение), 0.8 не "побеждает" как минимум:
        # 0.8 own × 0.9 upstream = 0.72, а не голое 0.8.
        assert demand.duty_cycles["Water"] == 0.9
        assert demand.duty_cycles["Coolant"] == 0.8 * 0.9
        assert demand.duty_cycles["Fuel Block"] == 1.0 * (0.8 * 0.9)

    def test_purchased_p1_is_a_continuous_boundary_like_raw_material(self, monkeypatch):
        """
        Сценарий пользователя дословно: P1 закупается (не в
        demand.factories вовсе, только в demand.purchased_p1) — Coolant
        (P2) ограничен только своим СОБСТВЕННЫМ причалом под
        закупленный Water, не выдуманным простоем самой закупки.
        """
        _mock_duty_cycles(monkeypatch, {"Coolant": 0.7, "Fuel Block": 1.0})
        demand = expand_demand(
            {"Fuel Block": 10.0}, schematics=_schematics(), recipes=_recipes(),
            purchase_p1=True,
        )
        assert demand.purchased_p1.get("Water")
        assert "Water" not in demand.duty_cycles
        assert demand.duty_cycles["Coolant"] == 0.7
        assert demand.duty_cycles["Fuel Block"] == 0.7

    def test_missing_volumes_collected_across_the_whole_tree(self, monkeypatch):
        def fake(schematic):
            if schematic.product == "Coolant":
                return 1.0, ["Water"]
            if schematic.product == "Fuel Block":
                return 1.0, ["Coolant"]
            return 1.0, []

        monkeypatch.setattr(throughput_module, "own_duty_cycle", fake)
        demand = expand_demand(
            {"Fuel Block": 10.0}, schematics=_schematics(), recipes=_recipes(),
        )
        assert demand.missing_volumes == ["Coolant", "Water"]
