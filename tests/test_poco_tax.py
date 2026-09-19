"""
domain/poco_tax.py: прибыльность построенного плана с учётом налога POCO.

Налог взимается и на экспорт, и на импорт (подтверждено официальной
механикой, docs/ROADMAP.md Фаза 9) — каждый тест сверяет числа вручную,
не полагаясь на то, что модуль сам себя не обманывает.

Ставка POCO — единственный источник: точная ставка КОНКРЕТНОЙ планеты
(system/planet) из `domain/planets.py::PlanetBook.poco_rate()` (ручной
ввод по типу планеты убран 17.09.2026 при переходе к
мультирегиональности). В тестах вместо настоящего `PlanetBook` —
`_FakePlanetBook`, минимальная замена без побочных эффектов чтения
настоящего CSV.
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


def _mining_row(planet_type="Barren", factories=8, system="MINE", planet=1):
    return {
        "role_key": "mine", "res_out": "Water", "planet_type": planet_type,
        "structures_detail": [{"kind": "basic_industry_facility", "count": factories}],
        "system": system, "planet": planet,
    }


def _processing_row(planet_type="Temperate", factories=12, system="HOME", planet=2):
    return {
        "role_key": "proc", "res_out": "Coolant", "planet_type": planet_type,
        "structures_detail": [{"kind": "advanced_industry_facility", "count": factories}],
        "system": system, "planet": planet,
    }


class _FakePlanetBook:
    """Минимальная замена PlanetBook для тестов — только то, что читает _resolve_rate()."""

    def __init__(self, rates: dict[tuple[str, float], float]):
        self._rates = rates

    def poco_rate(self, system, planet):
        try:
            key = (system, float(planet))
        except (TypeError, ValueError):
            return None
        return self._rates.get(key)


class TestBasicChain:
    def test_export_and_import_both_taxed_on_every_hop(self):
        """
        Числа посчитаны вручную (см. докстринг): экспорт с добывающей
        планеты (Water, ставка 0.10), экспорт с перерабатывающей
        (Coolant, ставка 0.05) И импорт на перерабатывающую (Water,
        та же ставка 0.05) — три налоговых составляющих, а не одна.
        """
        planets = _FakePlanetBook({("MINE", 1.0): 0.10, ("HOME", 2.0): 0.05})
        result = evaluate_plan_profitability(
            rows=[_mining_row(), _processing_row()],
            target_products=["Coolant"],
            prices={"Water": 10.0, "Coolant": 100.0},
            schematics=_schematics(),
            planets=planets,
        )

        assert result.monthly_revenue == 8_640_000.0
        assert result.monthly_export_tax == 1_584_000.0
        assert result.monthly_import_tax == 216_000.0
        assert result.monthly_net_profit == 6_840_000.0

    def test_intermediate_product_is_taxed_but_not_sold(self):
        """Water — не целевой продукт: не входит в выручку, но платит экспортный налог."""
        planets = _FakePlanetBook({("MINE", 1.0): 0.10})
        result = evaluate_plan_profitability(
            rows=[_mining_row()],
            target_products=["Coolant"],  # Water не среди целевых
            prices={"Water": 10.0},
            schematics=_schematics(),
            planets=planets,
        )
        assert result.monthly_revenue is None  # нечего продавать в этом наборе строк
        assert result.monthly_export_tax == 1_152_000.0  # но налог на вывоз всё равно есть

    def test_hours_per_month_is_720(self):
        assert HOURS_PER_MONTH == 720.0


class TestPurchaseP1:
    """
    18.09.2026, по прямому запросу пользователя: план на закупаемом P1
    (planner.py::PlanRequest.purchase_p1) — стоимость закупки вычитается
    из чистой прибыли ОТДЕЛЬНО от налога POCO на ввоз этого же P1 на
    планету переработки (тот считается как обычно, ему всё равно,
    откуда физически взялся материал).
    """

    def test_purchase_cost_reduces_net_profit(self):
        planets = _FakePlanetBook({("HOME", 2.0): 0.05})
        result = evaluate_plan_profitability(
            rows=[_processing_row()],
            target_products=["Coolant"],
            prices={"Water": 10.0, "Coolant": 100.0},
            schematics=_schematics(),
            planets=planets,
            purchased_p1={"Water": 600.0},
        )
        # Revenue/export_tax — те же 8 640 000/432 000, что и в
        # test_export_and_import_both_taxed_on_every_hop (одна и та же
        # строка переработки); import_tax — те же 216 000 (Water на
        # переработку, ставка 0.05). Новое здесь — стоимость покупки:
        # 600 ед/ч * 720 ч * 10 ISK = 4 320 000, вычитается из чистой
        # прибыли ОТДЕЛЬНО от import_tax.
        assert result.monthly_purchase_cost == 4_320_000.0
        assert result.monthly_net_profit == 8_640_000.0 - 432_000.0 - 216_000.0 - 4_320_000.0

    def test_missing_purchase_price_is_a_gap_not_zero(self):
        planets = _FakePlanetBook({("HOME", 2.0): 0.05})
        result = evaluate_plan_profitability(
            rows=[_processing_row()],
            target_products=["Coolant"],
            prices={"Coolant": 100.0},  # цены Water нет вовсе
            schematics=_schematics(),
            planets=planets,
            purchased_p1={"Water": 600.0},
        )
        assert "Water" in result.missing_prices
        assert result.monthly_purchase_cost is None
        assert result.monthly_net_profit is None  # картина неполная — не выдумываем частичную прибыль

    def test_no_purchased_p1_leaves_field_none_not_zero(self):
        """Обычный план (свой P1) не должен внезапно показывать «закупка: 0»."""
        planets = _FakePlanetBook({("HOME", 2.0): 0.05})
        result = evaluate_plan_profitability(
            rows=[_processing_row()],
            target_products=["Coolant"],
            prices={"Water": 10.0, "Coolant": 100.0},
            schematics=_schematics(),
            planets=planets,
        )
        assert result.monthly_purchase_cost is None


class TestHonestGaps:
    def test_missing_price_excluded_not_zeroed(self):
        planets = _FakePlanetBook({("MINE", 1.0): 0.10, ("HOME", 2.0): 0.05})
        result = evaluate_plan_profitability(
            rows=[_mining_row(), _processing_row()],
            target_products=["Coolant"],
            prices={"Coolant": 100.0},  # цены Water нет
            schematics=_schematics(),
            planets=planets,
        )
        assert "Water" in result.missing_prices
        # Экспорт Water (нет цены) выпал, но экспорт Coolant и импорт Water
        # по ставке Temperate тоже требуют цены Water — оба пропущены.
        assert result.monthly_export_tax == 432_000.0  # только Coolant-экспорт
        assert result.monthly_import_tax is None

    def test_missing_rate_excluded_not_zeroed(self):
        planets = _FakePlanetBook({("HOME", 2.0): 0.05})  # ставки MINE нет
        result = evaluate_plan_profitability(
            rows=[_mining_row(), _processing_row()],
            target_products=["Coolant"],
            prices={"Water": 10.0, "Coolant": 100.0},
            schematics=_schematics(),
            planets=planets,
        )
        assert "Barren" in result.missing_rates
        assert result.monthly_revenue == 8_640_000.0  # выручка не зависит от ставок
        # Экспорт с добывающей планеты (нет ставки) не посчитан; с перерабатывающей есть.
        assert result.monthly_export_tax == 432_000.0
        assert result.monthly_import_tax == 216_000.0

    def test_net_profit_is_none_when_picture_is_incomplete(self):
        """
        Частичные revenue/export_tax/import_tax — честные частичные суммы,
        но итоговая чистая прибыль с пропуском где-либо выглядела бы
        прибыльнее, чем есть на самом деле — молчаливое занижение
        налога. Поэтому net_profit требует ПОЛНОЙ картины, а не частичной.
        """
        planets = _FakePlanetBook({("MINE", 1.0): 0.10, ("HOME", 2.0): 0.05})
        result = evaluate_plan_profitability(
            rows=[_mining_row(), _processing_row()],
            target_products=["Coolant"],
            prices={"Coolant": 100.0},  # Water без цены
            schematics=_schematics(),
            planets=planets,
        )
        assert result.monthly_revenue == 8_640_000.0  # частичная сумма есть
        assert result.monthly_net_profit is None  # но итог — честно неизвестен

    def test_row_without_matching_schematic_is_skipped_honestly(self):
        row = {"role_key": "proc", "res_out": "Unknown Product", "planet_type": "Barren",
               "structures_detail": [{"kind": "advanced_industry_facility", "count": 4}]}
        result = evaluate_plan_profitability(
            rows=[row], target_products=["Unknown Product"],
            prices={}, schematics=_schematics(),
        )
        assert result.monthly_revenue is None
        assert result.monthly_export_tax is None

    def test_no_rows_returns_all_none(self):
        result = evaluate_plan_profitability(
            rows=[], target_products=[], prices={},
            schematics=_schematics(),
        )
        assert result.monthly_revenue is None
        assert result.monthly_export_tax is None
        assert result.monthly_import_tax is None
        assert result.monthly_net_profit is None
        assert result.missing_prices == set()
        assert result.missing_rates == set()

    def test_missing_when_planet_unknown_to_file(self):
        """Планета за пределами загруженного региона — честный пробел, не 0%."""
        planets = _FakePlanetBook({})  # планеты нет вовсе
        row = _mining_row(planet_type="Barren", system="Jita", planet=4)
        result = evaluate_plan_profitability(
            rows=[row], target_products=["Water"],
            prices={"Water": 10.0}, schematics=_schematics(), planets=planets,
        )
        assert "Barren" in result.missing_rates
        assert result.monthly_export_tax is None

    def test_missing_when_no_planets_argument(self):
        """planets по умолчанию None — ставки взять неоткуда, честный пробел."""
        row = _mining_row(planet_type="Barren")
        result = evaluate_plan_profitability(
            rows=[row], target_products=["Water"],
            prices={"Water": 10.0}, schematics=_schematics(),
        )
        assert "Barren" in result.missing_rates


class TestColoniesProfitability:
    """
    evaluate_colonies_profitability() — тот же расчёт, что и для плана,
    но по факту (docs/ROADMAP.md, Фаза 9, 16.09.2026): скорость каждой
    колонии уже посчитана фронтендом (никаких "на полную"), налог —
    только экспортный, поколонийно, без графа между колониями.
    """

    def test_revenue_and_tax_summed_per_colony(self):
        planets = _FakePlanetBook({("MINE", 1.0): 0.10, ("HOME", 2.0): 0.05})
        result = evaluate_colonies_profitability(
            colonies=[
                {"label": "A", "product": "Water", "units_per_hour": 1000.0,
                 "planet_type": "Barren", "system": "MINE", "planet": 1},
                {"label": "B", "product": "Coolant", "units_per_hour": 10.0,
                 "planet_type": "Temperate", "system": "HOME", "planet": 2},
            ],
            prices={"Water": 10.0, "Coolant": 100.0},
            planets=planets,
        )
        assert result.monthly_revenue == 7_920_000.0
        assert result.monthly_tax == 756_000.0
        assert result.monthly_net_profit == 7_164_000.0

    def test_idle_colony_contributes_zero_not_missing(self):
        """Простаивающая колония — честный 0, а не пробел: состояние известно."""
        planets = _FakePlanetBook({("MINE", 1.0): 0.10})
        result = evaluate_colonies_profitability(
            colonies=[{"label": "A", "product": "Water", "units_per_hour": 0.0,
                       "planet_type": "Barren", "system": "MINE", "planet": 1}],
            prices={"Water": 10.0},
            planets=planets,
        )
        assert result.missing_output == []
        assert result.monthly_revenue == 0.0
        assert result.monthly_net_profit == 0.0

    def test_unknown_current_rate_is_missing_output_not_zeroed(self):
        planets = _FakePlanetBook({("MINE", 1.0): 0.10, ("HOME", 2.0): 0.05})
        result = evaluate_colonies_profitability(
            colonies=[
                {"label": "A", "product": "Water", "units_per_hour": None,
                 "planet_type": "Barren", "system": "MINE", "planet": 1},
                {"label": "B", "product": "Coolant", "units_per_hour": 10.0,
                 "planet_type": "Temperate", "system": "HOME", "planet": 2},
            ],
            prices={"Water": 10.0, "Coolant": 100.0},
            planets=planets,
        )
        assert result.missing_output == ["A"]
        assert result.monthly_revenue == 720_000.0  # только колония B
        assert result.monthly_net_profit is None  # картина неполная

    def test_missing_price_excluded_not_zeroed(self):
        planets = _FakePlanetBook({("MINE", 1.0): 0.10})
        result = evaluate_colonies_profitability(
            colonies=[{"label": "A", "product": "Water", "units_per_hour": 1000.0,
                       "planet_type": "Barren", "system": "MINE", "planet": 1}],
            prices={},
            planets=planets,
        )
        assert "Water" in result.missing_prices
        assert result.monthly_revenue is None
        assert result.monthly_tax is None

    def test_missing_rate_excluded_not_zeroed(self):
        planets = _FakePlanetBook({})
        result = evaluate_colonies_profitability(
            colonies=[{"label": "A", "product": "Water", "units_per_hour": 1000.0,
                       "planet_type": "Barren", "system": "MINE", "planet": 1}],
            prices={"Water": 10.0},
            planets=planets,
        )
        assert "Barren" in result.missing_rates
        assert result.monthly_revenue == 7_200_000.0  # выручка не зависит от ставки
        assert result.monthly_tax is None
        assert result.monthly_net_profit is None

    def test_no_colonies_returns_all_none(self):
        result = evaluate_colonies_profitability(colonies=[], prices={})
        assert result.monthly_revenue is None
        assert result.monthly_tax is None
        assert result.monthly_net_profit is None
        assert result.missing_output == []

    def test_to_dict_shape(self):
        planets = _FakePlanetBook({("MINE", 1.0): 0.10})
        result = evaluate_colonies_profitability(
            colonies=[{"label": "A", "product": "Water", "units_per_hour": 1000.0,
                       "planet_type": "Barren", "system": "MINE", "planet": 1}],
            prices={"Water": 10.0},
            planets=planets,
        )
        body = result.to_dict()
        assert body["hours_per_month"] == 720.0
        assert body["monthly_net_profit"] == 6_480_000.0
        assert body["missing_prices"] == []
        assert body["missing_rates"] == []
        assert body["missing_output"] == []


class TestAutomaticRateFromPlanetsFile:
    """
    Ставка POCO берётся из PlanetBook.poco_rate() конкретной планеты
    (system/planet строки/колонии) — domain/planets.py::PlanetBook.poco_rate()
    не подделка, а сама точная реализация; здесь проверяется только, что
    poco_tax.py правильно вызывает её через planets, поэтому planets —
    минимальная тестовая замена, без побочных эффектов чтения настоящего CSV.
    """

    def test_uses_planets_file_rate(self):
        planets = _FakePlanetBook({("HOME", 5.0): 0.07})
        row = {**_mining_row(planet_type="Barren", system="HOME", planet=5)}
        result = evaluate_plan_profitability(
            rows=[row], target_products=["Water"],
            prices={"Water": 10.0},
            schematics=_schematics(), planets=planets,
        )
        assert "Barren" not in result.missing_rates
        # 8 фабрик * 100 ед./цикл * (60/30) цикла в час = 1600 ед./ч,
        # * 720 ч * 10 ISK * 0.07 = 806 400.
        assert round(result.monthly_export_tax, 6) == 806_400.0

    def test_colonies_profitability_also_uses_planets_file(self):
        planets = _FakePlanetBook({("AV-VB6", 9.0): 0.05})
        result = evaluate_colonies_profitability(
            colonies=[{
                "label": "A", "product": "Water", "units_per_hour": 1000.0,
                "planet_type": "Barren", "system": "AV-VB6", "planet": 9,
            }],
            prices={"Water": 10.0}, planets=planets,
        )
        assert result.missing_rates == set()
        assert result.monthly_tax == 360_000.0  # 1000*720*10*0.05


class TestToDict:
    def test_to_dict_sums_export_and_import_into_monthly_tax(self):
        planets = _FakePlanetBook({("MINE", 1.0): 0.10, ("HOME", 2.0): 0.05})
        result = evaluate_plan_profitability(
            rows=[_mining_row(), _processing_row()],
            target_products=["Coolant"],
            prices={"Water": 10.0, "Coolant": 100.0},
            schematics=_schematics(),
            planets=planets,
        )
        body = result.to_dict()
        assert body["monthly_tax"] == body["monthly_export_tax"] + body["monthly_import_tax"]
        assert body["hours_per_month"] == 720.0
        assert body["missing_prices"] == []
        assert body["missing_rates"] == []
        assert body["monthly_purchase_cost"] is None  # обычный план — закупки нет

    def test_to_dict_includes_purchase_cost_when_purchasing_p1(self):
        planets = _FakePlanetBook({("HOME", 2.0): 0.05})
        result = evaluate_plan_profitability(
            rows=[_processing_row()],
            target_products=["Coolant"],
            prices={"Water": 10.0, "Coolant": 100.0},
            schematics=_schematics(),
            planets=planets,
            purchased_p1={"Water": 600.0},
        )
        assert result.to_dict()["monthly_purchase_cost"] == 4_320_000.0
