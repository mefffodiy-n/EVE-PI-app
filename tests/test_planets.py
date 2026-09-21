"""
Тесты domain.planets.

С 21.09.2026 (Фаза 1 мультирегиональности) load_planets() читает
`regions`/`planets` из БД, а не data/planet_industry.csv — фикстура
seed_57kjb_planet() ниже заполняет изолированную тестовую БД (conftest.py::
isolate_database) строками, эквивалентными реальной 57-KJB I из
прежнего CSV, чтобы сами проверки (радиус/POCO/констелляция) не
менялись. load_planets.cache_clear() вызывается conftest.py перед
каждым тестом — без этого @lru_cache(maxsize=1) отдавал бы результат
первого теста, вызвавшего load_planets(), всем последующим (раньше
кэш был безопасен: источник-файл не менялся между тестами, теперь
меняется).
"""

from __future__ import annotations

import pytest

from domain.planets import load_planets, planet_number_to_roman


class TestRadiusKm:
    def test_known_planet_returns_real_radius(self, seed_57kjb_planet):
        book = load_planets()
        assert book.radius_km("57-KJB", 1) == 2570.0

    def test_accepts_string_and_float_planet_number(self, seed_57kjb_planet):
        book = load_planets()
        assert book.radius_km("57-KJB", "1") == book.radius_km("57-KJB", 1.0)

    def test_unknown_system_is_none(self, seed_57kjb_planet):
        """БД покрывает только загруженный регион, не весь New Eden."""
        book = load_planets()
        assert book.radius_km("Jita", 4) is None

    def test_unknown_planet_number_in_known_system_is_none(self, seed_57kjb_planet):
        book = load_planets()
        assert book.radius_km("57-KJB", 99) is None


class TestPocoRate:
    """Ставка POCO по конкретной планете — единственный источник (domain/poco_tax.py)."""

    def test_known_planet_returns_real_rate_as_fraction(self, seed_57kjb_planet):
        book = load_planets()
        assert book.poco_rate("57-KJB", 1) == 0.03

    def test_accepts_string_and_float_planet_number(self, seed_57kjb_planet):
        book = load_planets()
        assert book.poco_rate("57-KJB", "1") == book.poco_rate("57-KJB", 1.0)

    def test_unknown_system_is_none(self, seed_57kjb_planet):
        book = load_planets()
        assert book.poco_rate("Jita", 4) is None

    def test_unknown_planet_number_in_known_system_is_none(self, seed_57kjb_planet):
        book = load_planets()
        assert book.poco_rate("57-KJB", 99) is None


class TestConstellationOf:
    """
    18.09.2026, найдено пользователем: колонка «Констелляция» в
    экспорте настоящих колоний в Excel оставалась пустой — данные для
    неё есть в этом же файле, просто не читались.
    """

    def test_known_system_returns_its_constellation(self, seed_57kjb_planet):
        book = load_planets()
        assert book.constellation_of("57-KJB") == "MINOTAUR"

    def test_unknown_system_is_none(self, seed_57kjb_planet):
        book = load_planets()
        assert book.constellation_of("Jita") is None


class TestRegionsFromDb:
    """
    21.09.2026, Фаза 1 мультирегиональности: колонки «Region» не было
    ни в одном CSV (данные покрывали один регион без явной пометки),
    поэтому PlanetBook.regions() всегда возвращал {} — с переносом в
    БД колонка появляется по-честному, и уже существующий метод
    оживает без собственных изменений.
    """

    def test_regions_lists_constellations_by_region(self, seed_57kjb_planet):
        book = load_planets()
        assert book.regions() == {"Fountain": ["MINOTAUR"]}

    def test_resolve_resource_column_finds_seeded_resource(self, seed_57kjb_planet):
        book = load_planets()
        assert book.resolve_resource_column("Aqueous Liquids") == "Aqueous Liquids"

    def test_resolve_resource_column_none_when_never_recorded(self, seed_57kjb_planet):
        """Ресурс, которого нет ни у одной планеты БД — честный пробел, не 0."""
        book = load_planets()
        assert book.resolve_resource_column("Water") is None


class TestEmptyDatabase:
    """
    Свежий клон без `alembic upgrade head`/переноса — БД либо пуста,
    либо таблиц ещё нет вовсе. Страница должна открыться, а не упасть.
    """

    def test_no_planets_gives_empty_book_not_exception(self):
        book = load_planets()
        assert book.constellations() == []
        assert book.regions() == {}
        assert book.radius_km("57-KJB", 1) is None


class TestNoDataRegionsExcluded:
    """
    21.09.2026, найдено при разборе жалобы пользователя «страница
    открывается 16 секунд» — вместе и баг, и причина просадки
    производительности. `scripts/refresh_sde.py` (Фаза 2) заводит
    регион со статусом `no_data`, а load_planets() должен был его не
    показывать («не показывается пользователям в планировщике»,
    docs/ROADMAP.md) — но ничего в domain/planets.py этот статус не
    проверяло: PlanetBook честно тянул все ~68 тыс. скелетных планет
    всех регионов на каждую холодную сборку кэша.
    """

    def test_no_data_region_is_excluded_from_planet_book(self, seed_57kjb_planet):
        from infra.db import session_scope
        from infra.models import Planet, Region

        with session_scope() as session:
            region = Region(name="Testonia", status="no_data")
            session.add(region)
            session.flush()
            session.add(Planet(
                region_id=region.id, constellation="NEWVALE", system="N3W-SY",
                planet_number=1, planet_type="Barren", radius_km=5000.0,
            ))

        load_planets.cache_clear()
        book = load_planets()

        assert "Testonia" not in book.regions()
        assert book.radius_km("N3W-SY", 1) is None  # честно нет, не 5000
        assert "57-KJB" in set(book.dataframe["System"])  # ready-регион остаётся


class TestCacheTtl:
    """
    21.09.2026, Фаза 2 мультирегиональности: `scripts/refresh_sde.py`
    пишет в regions/planets из ДРУГОГО процесса (планировщик), поэтому
    вечный `@lru_cache` заменён на TTL (`PLANETS_CACHE_TTL_SECONDS`) —
    без него уже запущенный веб-процесс никогда не увидел бы новые
    регионы.
    """

    def test_returns_cached_value_before_ttl_expires(self, seed_57kjb_planet):
        first = load_planets()
        # Вторая планета добавлена мимо load_planets() — если бы кэша не
        # было, следующий вызов увидел бы её немедленно.
        from infra.db import session_scope
        from infra.models import Planet, Region

        with session_scope() as session:
            region = session.query(Region).filter_by(name="Fountain").one()
            session.add(Planet(
                region_id=region.id, constellation="MINOTAUR", system="57-KJB",
                planet_number=2, planet_type="Barren", radius_km=1000.0,
            ))

        assert load_planets() is first
        assert load_planets().dataframe.shape[0] == first.dataframe.shape[0]

    def test_recomputes_after_ttl_expires(self, seed_57kjb_planet, monkeypatch):
        import time

        import domain.planets as planets_module

        clock = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])

        first = load_planets()

        from infra.db import session_scope
        from infra.models import Planet, Region

        with session_scope() as session:
            region = session.query(Region).filter_by(name="Fountain").one()
            session.add(Planet(
                region_id=region.id, constellation="MINOTAUR", system="57-KJB",
                planet_number=2, planet_type="Barren", radius_km=1000.0,
            ))

        clock[0] += planets_module.PLANETS_CACHE_TTL_SECONDS + 1
        second = load_planets()
        assert second is not first
        assert second.dataframe.shape[0] == first.dataframe.shape[0] + 1

    def test_cache_clear_forces_immediate_refresh(self, seed_57kjb_planet):
        from infra.db import session_scope
        from infra.models import Planet, Region

        first = load_planets()

        with session_scope() as session:
            region = session.query(Region).filter_by(name="Fountain").one()
            session.add(Planet(
                region_id=region.id, constellation="MINOTAUR", system="57-KJB",
                planet_number=2, planet_type="Barren", radius_km=1000.0,
            ))

        load_planets.cache_clear()
        second = load_planets()
        assert second.dataframe.shape[0] == first.dataframe.shape[0] + 1


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
