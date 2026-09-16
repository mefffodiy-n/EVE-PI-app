"""
domain/poco_tax.py: прибыльность построенного плана с учётом налога POCO.

Налог взимается и на экспорт, и на импорт (подтверждено официальной
механикой, docs/ROADMAP.md Фаза 9) — каждый тест сверяет числа вручную,
не полагаясь на то, что модуль сам себя не обманывает.
"""

from __future__ import annotations

from domain.poco_tax import (
    HOURS_PER_MONTH,
    evaluate_colonies_profitability,
    evaluate_plan_profitability,
)
from domain.throughput import Schematic


def _schematics():
    return {
        # P1: сырьё без входов — добывается, не привозится.
        "Water": Schematic(product="Water", facility="basic_industry_facility",
                            inputs={}, output_qty=100.0, cycle_minutes=30.0),
        # P2: целевой продукт, потребляет Water.
        "Coolant": Schematic(product="Coolant", facility="advanced_industry_facility",
                              inputs={"Water": 50.0}, output_qty=10.0, cycle_minutes=60.0),
    }


def _mining_row(planet_type="Barren", factories=8):
    return {
        "role_key": "mine", "res_out": "Water", "planet_type": planet_type,
        "structures_detail": [{"kind": "basic_industry_facility", "count": factories}],
    }


def _processing_row(planet_type="Temperate", factories=12):
    return {
        "role_key": "proc", "res_out": "Coolant", "planet_type": planet_type,
        "structures_detail": [{"kind": "advanced_industry_facility", "count": factories}],
    }


class TestBasicChain:
    def test_export_and_import_both_taxed_on_every_hop(self):
        """
        Числа посчитаны вручную (см. докстринг): экспорт с добывающей
        планеты (Water, ставка Barren), экспорт с перерабатывающей
        (Coolant, ставка Temperate) И импорт на перерабатывающую (Water,
        та же ставка Temperate) — три налоговых составляющих, а не одна.
        """
        result = evaluate_plan_profitability(
            rows=[_mining_row(), _processing_row()],
            target_products=["Coolant"],
            prices={"Water": 10.0, "Coolant": 100.0},
            poco_rates={"Barren": 0.10, "Temperate": 0.05},
            schematics=_schematics(),
        )

        assert result.monthly_revenue == 8_640_000.0
        assert result.monthly_export_tax == 1_584_000.0
        assert result.monthly_import_tax == 216_000.0
        assert result.monthly_net_profit == 6_840_000.0

    def test_intermediate_product_is_taxed_but_not_sold(self):
        """Water — не целевой продукт: не входит в выручку, но платит экспортный налог."""
        result = evaluate_plan_profitability(
            rows=[_mining_row()],
            target_products=["Coolant"],  # Water не среди целевых
            prices={"Water": 10.0},
            poco_rates={"Barren": 0.10},
            schematics=_schematics(),
        )
        assert result.monthly_revenue is None  # нечего продавать в этом наборе строк
        assert result.monthly_export_tax == 1_152_000.0  # но налог на вывоз всё равно есть

    def test_hours_per_month_is_720(self):
        assert HOURS_PER_MONTH == 720.0


class TestHonestGaps:
    def test_missing_price_excluded_not_zeroed(self):
        result = evaluate_plan_profitability(
            rows=[_mining_row(), _processing_row()],
            target_products=["Coolant"],
            prices={"Coolant": 100.0},  # цены Water нет
            poco_rates={"Barren": 0.10, "Temperate": 0.05},
            schematics=_schematics(),
        )
        assert "Water" in result.missing_prices
        # Экспорт Water (нет цены) выпал, но экспорт Coolant и импорт Water
        # по ставке Temperate тоже требуют цены Water — оба пропущены.
        assert result.monthly_export_tax == 432_000.0  # только Coolant-экспорт
        assert result.monthly_import_tax is None

    def test_missing_rate_excluded_not_zeroed(self):
        result = evaluate_plan_profitability(
            rows=[_mining_row(), _processing_row()],
            target_products=["Coolant"],
            prices={"Water": 10.0, "Coolant": 100.0},
            poco_rates={"Temperate": 0.05},  # ставки Barren нет
            schematics=_schematics(),
        )
        assert "Barren" in result.missing_rates
        assert result.monthly_revenue == 8_640_000.0  # выручка не зависит от ставок
        # Экспорт с Barren (добыча) не посчитан — нет ставки; Temperate есть.
        assert result.monthly_export_tax == 432_000.0
        assert result.monthly_import_tax == 216_000.0

    def test_net_profit_is_none_when_picture_is_incomplete(self):
        """
        Частичные revenue/export_tax/import_tax — честные частичные суммы,
        но итоговая чистая прибыль с пропуском где-либо выглядела бы
        прибыльнее, чем есть на самом деле — молчаливое занижение
        налога. Поэтому net_profit требует ПОЛНОЙ картины, а не частичной.
        """
        result = evaluate_plan_profitability(
            rows=[_mining_row(), _processing_row()],
            target_products=["Coolant"],
            prices={"Coolant": 100.0},  # Water без цены
            poco_rates={"Barren": 0.10, "Temperate": 0.05},
            schematics=_schematics(),
        )
        assert result.monthly_revenue == 8_640_000.0  # частичная сумма есть
        assert result.monthly_net_profit is None  # но итог — честно неизвестен

    def test_row_without_matching_schematic_is_skipped_honestly(self):
        row = {"role_key": "proc", "res_out": "Unknown Product", "planet_type": "Barren",
               "structures_detail": [{"kind": "advanced_industry_facility", "count": 4}]}
        result = evaluate_plan_profitability(
            rows=[row], target_products=["Unknown Product"],
            prices={}, poco_rates={}, schematics=_schematics(),
        )
        assert result.monthly_revenue is None
        assert result.monthly_export_tax is None

    def test_no_rows_returns_all_none(self):
        result = evaluate_plan_profitability(
            rows=[], target_products=[], prices={}, poco_rates={},
            schematics=_schematics(),
        )
        assert result.monthly_revenue is None
        assert result.monthly_export_tax is None
        assert result.monthly_import_tax is None
        assert result.monthly_net_profit is None
        assert result.missing_prices == set()
        assert result.missing_rates == set()


class TestColoniesProfitability:
    """
    evaluate_colonies_profitability() — тот же расчёт, что и для плана,
    но по факту (docs/ROADMAP.md, Фаза 9, 16.09.2026): скорость каждой
    колонии уже посчитана фронтендом (никаких "на полную"), налог —
    только экспортный, поколонийно, без графа между колониями.
    """

    def test_revenue_and_tax_summed_per_colony(self):
        result = evaluate_colonies_profitability(
            colonies=[
                {"label": "A", "product": "Water", "units_per_hour": 1000.0, "planet_type": "Barren"},
                {"label": "B", "product": "Coolant", "units_per_hour": 10.0, "planet_type": "Temperate"},
            ],
            prices={"Water": 10.0, "Coolant": 100.0},
            poco_rates={"Barren": 0.10, "Temperate": 0.05},
        )
        assert result.monthly_revenue == 7_920_000.0
        assert result.monthly_tax == 756_000.0
        assert result.monthly_net_profit == 7_164_000.0

    def test_idle_colony_contributes_zero_not_missing(self):
        """Простаивающая колония — честный 0, а не пробел: состояние известно."""
        result = evaluate_colonies_profitability(
            colonies=[{"label": "A", "product": "Water", "units_per_hour": 0.0, "planet_type": "Barren"}],
            prices={"Water": 10.0},
            poco_rates={"Barren": 0.10},
        )
        assert result.missing_output == []
        assert result.monthly_revenue == 0.0
        assert result.monthly_net_profit == 0.0

    def test_unknown_current_rate_is_missing_output_not_zeroed(self):
        result = evaluate_colonies_profitability(
            colonies=[
                {"label": "A", "product": "Water", "units_per_hour": None, "planet_type": "Barren"},
                {"label": "B", "product": "Coolant", "units_per_hour": 10.0, "planet_type": "Temperate"},
            ],
            prices={"Water": 10.0, "Coolant": 100.0},
            poco_rates={"Barren": 0.10, "Temperate": 0.05},
        )
        assert result.missing_output == ["A"]
        assert result.monthly_revenue == 720_000.0  # только колония B
        assert result.monthly_net_profit is None  # картина неполная

    def test_missing_price_excluded_not_zeroed(self):
        result = evaluate_colonies_profitability(
            colonies=[{"label": "A", "product": "Water", "units_per_hour": 1000.0, "planet_type": "Barren"}],
            prices={},
            poco_rates={"Barren": 0.10},
        )
        assert "Water" in result.missing_prices
        assert result.monthly_revenue is None
        assert result.monthly_tax is None

    def test_missing_rate_excluded_not_zeroed(self):
        result = evaluate_colonies_profitability(
            colonies=[{"label": "A", "product": "Water", "units_per_hour": 1000.0, "planet_type": "Barren"}],
            prices={"Water": 10.0},
            poco_rates={},
        )
        assert "Barren" in result.missing_rates
        assert result.monthly_revenue == 7_200_000.0  # выручка не зависит от ставки
        assert result.monthly_tax is None
        assert result.monthly_net_profit is None

    def test_no_colonies_returns_all_none(self):
        result = evaluate_colonies_profitability(colonies=[], prices={}, poco_rates={})
        assert result.monthly_revenue is None
        assert result.monthly_tax is None
        assert result.monthly_net_profit is None
        assert result.missing_output == []

    def test_to_dict_shape(self):
        result = evaluate_colonies_profitability(
            colonies=[{"label": "A", "product": "Water", "units_per_hour": 1000.0, "planet_type": "Barren"}],
            prices={"Water": 10.0},
            poco_rates={"Barren": 0.10},
        )
        body = result.to_dict()
        assert body["hours_per_month"] == 720.0
        assert body["monthly_net_profit"] == 6_480_000.0
        assert body["missing_prices"] == []
        assert body["missing_rates"] == []
        assert body["missing_output"] == []


class TestToDict:
    def test_to_dict_sums_export_and_import_into_monthly_tax(self):
        result = evaluate_plan_profitability(
            rows=[_mining_row(), _processing_row()],
            target_products=["Coolant"],
            prices={"Water": 10.0, "Coolant": 100.0},
            poco_rates={"Barren": 0.10, "Temperate": 0.05},
            schematics=_schematics(),
        )
        body = result.to_dict()
        assert body["monthly_tax"] == body["monthly_export_tax"] + body["monthly_import_tax"]
        assert body["hours_per_month"] == 720.0
        assert body["missing_prices"] == []
        assert body["missing_rates"] == []
