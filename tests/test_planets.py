"""
Тесты domain.planets: PlanetBook.radius_km().
"""

from __future__ import annotations

from domain.planets import load_planets


class TestRadiusKm:
    def test_known_planet_returns_real_radius(self):
        """57-KJB I — реальная строка data/planet_industry.csv, радиус 2570 км."""
        book = load_planets()
        assert book.radius_km("57-KJB", 1) == 2570.0

    def test_accepts_string_and_float_planet_number(self):
        book = load_planets()
        assert book.radius_km("57-KJB", "1") == book.radius_km("57-KJB", 1.0)

    def test_unknown_system_is_none(self):
        """Файл покрывает только загруженный регион, не весь New Eden."""
        book = load_planets()
        assert book.radius_km("Jita", 4) is None

    def test_unknown_planet_number_in_known_system_is_none(self):
        book = load_planets()
        assert book.radius_km("57-KJB", 99) is None


class TestPocoRate:
    """
    Ставка POCO по конкретной планете — точнее, чем ручной ввод по типу
    (domain/poco_tax.py): внутри одного типа в загруженном регионе
    встречаются разные ставки сразу (см. test_breakdown_finds_mixed_rates).
    """

    def test_known_planet_returns_real_rate_as_fraction(self):
        """57-KJB I — та же строка, что и у радиуса, ставка 3% -> 0.03."""
        book = load_planets()
        assert book.poco_rate("57-KJB", 1) == 0.03

    def test_accepts_string_and_float_planet_number(self):
        book = load_planets()
        assert book.poco_rate("57-KJB", "1") == book.poco_rate("57-KJB", 1.0)

    def test_unknown_system_is_none(self):
        book = load_planets()
        assert book.poco_rate("Jita", 4) is None

    def test_unknown_planet_number_in_known_system_is_none(self):
        book = load_planets()
        assert book.poco_rate("57-KJB", 99) is None


class TestPocoRateBreakdown:
    def test_breakdown_finds_mixed_rates(self):
        """
        Barren в загруженном регионе встречается и на 3%, и на 1% —
        ручной ввод «по типу» поэтому заведомо приближение, не точное
        значение (правило 1), и ставка по КОНКРЕТНОЙ планете (см. выше)
        приоритетнее.
        """
        book = load_planets()
        breakdown = book.poco_rate_breakdown()
        assert "Barren" in breakdown
        assert set(breakdown["Barren"]) == {"3", "1"}
        assert breakdown["Barren"]["3"] > 0
        assert breakdown["Barren"]["1"] > 0

    def test_breakdown_counts_sum_to_type_total(self):
        book = load_planets()
        breakdown = book.poco_rate_breakdown()
        barren_planets = book.dataframe[book.dataframe["Type"] == "Barren"]
        assert sum(breakdown["Barren"].values()) == len(barren_planets)
