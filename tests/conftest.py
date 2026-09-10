"""
Общие фикстуры тестов.

ПОЧЕМУ ЭТОТ ФАЙЛ ЕСТЬ. Снимки в data/cache/ (цены рынка, статус сервера) —
локальные рантайм-артефакты: их пишут фоновые сборщики, в git их нет
(см. .gitignore). Тест, который читал их напрямую, проходил или падал в
зависимости от того, запускал ли разработчик scripts.scheduler на этой
машине. Так и было: test_market_never_calls_external_service ждал пустой
ответ, а получал реальные рекомендации с локального снимка цен.

Фикстура ниже уводит пути снимков в пустой временный каталог для КАЖДОГО
теста. Тест, которому нужен снимок, пишет его туда сам.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_cache_snapshots(tmp_path, monkeypatch):
    """Снимки рынка и статуса сервера — из пустого tmp, а не из data/cache/."""
    import api.blueprints.market as market
    import api.blueprints.meta as meta

    monkeypatch.setattr(market, "SNAPSHOT", tmp_path / "market_prices.json")
    monkeypatch.setattr(meta, "STATUS_SNAPSHOT", tmp_path / "server_status.json")
