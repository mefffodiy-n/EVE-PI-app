"""
Общие фикстуры тестов.

ПОЧЕМУ ЭТОТ ФАЙЛ ЕСТЬ. Всё, что тест читает не из кода, должно быть
изолировано — иначе результат зависит от машины разработчика:

  - снимки в data/cache/ (цены, статус сервера, статус планировщика)
    пишут фоновые сборщики; так и было — test_market_never_calls_external_service
    ждал пустой ответ, а получал рекомендации с локального снимка цен;
  - БД (data/pi_director.db) наполняет `scripts.seed_dev_characters`.

Обе фикстуры autouse: уводят пути во временный каталог для КАЖДОГО теста.
Тест, которому нужны данные, кладёт их туда сам.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_cache_snapshots(tmp_path, monkeypatch):
    """Снимки рынка, статуса сервера и планировщика — из пустого tmp, а не из data/cache/."""
    import api.blueprints.market as market
    import api.blueprints.meta as meta

    monkeypatch.setattr(market, "SNAPSHOT", tmp_path / "market_prices.json")
    monkeypatch.setattr(meta, "STATUS_SNAPSHOT", tmp_path / "server_status.json")
    monkeypatch.setattr(meta, "JOB_STATUS_SNAPSHOT", tmp_path / "scheduler_status.json")


@pytest.fixture(autouse=True)
def isolate_database(tmp_path, monkeypatch):
    """
    Пустая SQLite в tmp на каждый тест. Таблицы создаются из моделей
    (без Alembic — миграции проверяются отдельным тестом).
    """
    from infra import config, db

    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setattr(config, "DATABASE_URL", url)
    db.reset_engine(url)
    db.create_all()

    # load_planets() кэширует результат на процесс (@lru_cache(maxsize=1),
    # domain/planets.py) — раньше это было безопасно (источник, CSV-файл,
    # не менялся между тестами), но с переходом на БД (Фаза 1
    # мультирегиональности, 21.09.2026) каждый тест получает СВОЮ пустую
    # БД, и без сброса кэша все тесты после первого, вызвавшего
    # load_planets(), видели бы результат первого теста, а не свою БД.
    from domain.planets import load_planets

    load_planets.cache_clear()

    yield
    db.reset_engine()  # следующий тест поставит свой url через эту же фикстуру


@pytest.fixture
def seeded_characters():
    """13 dev-персонажей в изолированной БД (для тестов планировщика через API)."""
    from scripts.seed_dev_characters import seed

    return seed()


@pytest.fixture
def seed_57kjb_planet():
    """
    57-KJB I (MINOTAUR, Fountain) — та же планета, что раньше была
    реальной строкой data/planet_industry.csv (радиус 2570 км, POCO 3%,
    владелец INIT). С переходом domain.planets.load_planets() на чтение
    из БД (Фаза 1 мультирегиональности, 21.09.2026) тесты, которым
    нужна «известная реальная планета региона», сами кладут её в
    изолированную тестовую БД — используется tests/test_planets.py и
    tests/test_sync_colony_status.py.
    """
    from infra.db import session_scope
    from infra.models import Planet, Region

    with session_scope() as session:
        region = Region(name="Fountain", status="ready")
        session.add(region)
        session.flush()
        session.add(Planet(
            region_id=region.id,
            constellation="MINOTAUR",
            system="57-KJB",
            planet_number=1,
            planet_type="Barren",
            radius_km=2570.0,
            poco_tax_rate=3.0,
            poco_owner="INIT",
            r0_densities={"Aqueous Liquids": 36.0},
            p2_direct_densities=None,
        ))
