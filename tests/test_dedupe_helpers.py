"""Общие помощники, заменившие три копии разбора цен и две — lines_per_target (рецензия, 29.09.2026)."""

from __future__ import annotations

import pytest

from api.blueprints import market
from api.blueprints.plans import _parse_lines_per_target


def test_buy_prices_keeps_only_positive_buy_max():
    raw = {"A": {"buy_max": 5.0}, "B": {"buy_max": 0}, "C": {}, "D": "junk"}
    assert market.buy_prices(raw) == {"A": 5.0}


def test_load_buy_prices_survives_broken_snapshot(monkeypatch):
    monkeypatch.setattr(market, "_load_snapshot", lambda: {"prices": "oops"})
    assert market.load_buy_prices() == {}


def test_load_buy_prices_reads_snapshot(monkeypatch):
    monkeypatch.setattr(market, "_load_snapshot", lambda: {"prices": {"X": {"buy_max": 2.5}}})
    assert market.load_buy_prices() == {"X": 2.5}


@pytest.mark.parametrize("value, expected", [
    (None, 1), ("abc", 1), (0, 1), (-3, 1), (7, 7), ("4", 4), (999, 50),
])
def test_parse_lines_per_target(value, expected):
    payload = {} if value is None else {"lines_per_target": value}
    assert _parse_lines_per_target(payload) == expected
