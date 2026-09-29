"""domain/price_history: запись снимков, дневные ряды, динамика выручки."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from domain import price_history
from infra.db import session_scope

TYPE_IDS = {"Coolant": 1, "Robotics": 2}
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def _snap(session, days_ago, hour, coolant, robotics=None):
    prices = {"Coolant": {"buy_max": coolant, "sell_min": coolant + 5}}
    if robotics is not None:
        prices["Robotics"] = {"buy_max": robotics, "sell_min": None}
    price_history.record(
        session, prices, "jita", TYPE_IDS, now=NOW - timedelta(days=days_ago) + timedelta(hours=hour - 12)
    )


def test_record_skips_unknown_and_empty_entries():
    with session_scope() as s:
        n = price_history.record(
            s,
            {"Coolant": {"buy_max": 10}, "Nope": {"buy_max": 5}, "Robotics": {"buy_max": None, "sell_min": None}},
            "jita", TYPE_IDS, now=NOW,
        )
    assert n == 1


def test_daily_series_takes_last_price_of_the_day():
    with session_scope() as s:
        _snap(s, 1, 8, 100)
        _snap(s, 1, 20, 120)
        _snap(s, 0, 9, 130)
        series = price_history.daily_buy_series(s, "jita", 1, 30, now=NOW)
    assert list(series.values()) == [120.0, 130.0]


def test_revenue_trend_only_counts_days_with_all_prices():
    with session_scope() as s:
        _snap(s, 2, 10, 100, 1000)
        _snap(s, 1, 10, 110)            # у Robotics в этот день цены нет
        _snap(s, 0, 10, 150, 1500)
        result = price_history.revenue_trend(
            s, "jita",
            [{"product": "Coolant", "monthly_units": 10}, {"product": "Robotics", "monthly_units": 2}],
            30, TYPE_IDS, now=NOW,
        )
    assert [p["revenue"] for p in result["points"]] == [3000.0, 4500.0]
    assert result["change_pct"] == 50.0
    assert len(result["by_product"]["Coolant"]) == 3


def test_revenue_trend_without_history_is_honest():
    with session_scope() as s:
        result = price_history.revenue_trend(
            s, "jita", [{"product": "Coolant", "monthly_units": 10}], 30, TYPE_IDS, now=NOW
        )
    assert result["points"] == [] and result["change_pct"] is None


def test_prune_removes_only_old_samples():
    with session_scope() as s:
        _snap(s, 100, 10, 90)
        _snap(s, 1, 10, 100)
        assert price_history.prune(s, now=NOW) == 1
        assert price_history.daily_buy_series(s, "jita", 1, 200, now=NOW)


def test_api_returns_trend_and_validates_body(monkeypatch):
    from api import create_app

    client = create_app({"TESTING": True}).test_client()
    assert client.post("/api/plan-revenue-trend", json={}).status_code == 400
    r = client.post(
        "/api/plan-revenue-trend",
        json={"days": 30, "items": [{"product": "Coolant", "monthly_units": 5}]},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["points"] == [] and "by_product" in body


@pytest.mark.parametrize("bad", [{"items": [1]}])
def test_api_rejects_non_object_items(bad):
    from api import create_app

    client = create_app({"TESTING": True}).test_client()
    assert client.post("/api/plan-revenue-trend", json=bad).status_code == 400
