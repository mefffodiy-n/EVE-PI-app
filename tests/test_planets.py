"""
Тесты domain.planets: PlanetBook.radius_km().
"""

from __future__ import annotations

from domain.planets import load_planets, planet_number_to_roman


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
    """Ставка POCO по конкретной планете — единственный источник (domain/poco_tax.py)."""

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


class TestConstellationOf:
    """
    18.09.2026, найдено пользователем: колонка «Констелляция» в
    экспорте настоящих колоний в Excel оставалась пустой — данные для
    неё есть в этом же файле, просто не читались.
    """

    def test_known_system_returns_its_constellation(self):
        book = load_planets()
        assert book.constellation_of("57-KJB") == "MINOTAUR"

    def test_unknown_system_is_none(self):
        book = load_planets()
        assert book.constellation_of("Jita") is None


class TestPlanetNumberToRoman:
    """
    18.09.2026, найдено пользователем: номер планеты расчётного плана
    отображался как «16.0» (колонка «Planet» читается pandas как
    float64) — игра номер планеты арабской цифрой не показывает вовсе,
    просьба пользователя была не просто убрать «.0», а перейти на
    римские цифры везде, как в самой игре.
    """

    def test_common_case(self):
        assert planet_number_to_roman(4) == "IV"

    def test_float_from_csv_dropped_correctly(self):
        """Ровно то значение, что реально приходит из PlanetBook: «16.0»."""
        assert planet_number_to_roman(16.0) == "XVI"

    def test_float_like_string_from_json(self):
        """То же самое, но как приходит по проводу от фронтенда (JSON-строка)."""
        assert planet_number_to_roman("16.0") == "XVI"

    def test_int_string_from_esi(self):
        assert planet_number_to_roman("1") == "I"

    def test_zero_has_no_roman_form(self):
        """Планет с номером 0 не бывает, но честно, не выдумывая цифру."""
        assert planet_number_to_roman(0) == "0"

    def test_non_numeric_value_passed_through(self):
        assert planet_number_to_roman("Jita") == "Jita"

    def test_none_becomes_empty_string(self):
        assert planet_number_to_roman(None) == ""


