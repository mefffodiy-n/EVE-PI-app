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
    yield
    db.reset_engine()  # следующий тест поставит свой url через эту же фикстуру


@pytest.fixture
def seeded_characters():
    """13 dev-персонажей в изолированной БД (для тестов планировщика через API)."""
    from scripts.seed_dev_characters import seed

    return seed()
