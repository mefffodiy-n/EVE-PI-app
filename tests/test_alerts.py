"""domain/alerts: дефицит добычи и истечение экстракторов."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from domain import alerts

AT = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

# Эталон — значения extractorDecayedQty из web/app.js, посчитанные в браузере
# (29.09.2026): формула затухания в Python обязана совпадать с фронтендом.
JS_REFERENCE = [
    (12000, "2026-09-20T00:00:00Z", 1800, 2243),
    (25000, "2026-09-25T06:30:00Z", 3600, 28789),
    (9000, "2026-09-01T00:00:00Z", 900, 266),
    (40000, "2026-09-29T11:59:00Z", 7200, 305343),
    (18000, "2026-08-15T10:15:00Z", 5400, 2578),
]


@pytest.mark.parametrize("base,installed,cycle,expected", JS_REFERENCE)
def test_decay_matches_frontend(base, installed, cycle, expected):
    assert alerts.extractor_decayed_qty(base, installed, cycle, AT) == expected


def test_decay_unknown_inputs_give_none():
    assert alerts.extractor_decayed_qty(None, "2026-09-20T00:00:00Z", 900, AT) is None
    assert alerts.extractor_decayed_qty(100, None, 900, AT) is None
    assert alerts.extractor_decayed_qty(100, "2026-09-20T00:00:00Z", 0, AT) is None
    assert alerts.extractor_decayed_qty(100, "2026-10-20T00:00:00Z", 900, AT) is None  # ещё не установлен


def _ecu(rate_qty, product="Water", expiry=None, pin_id=1):
    # цикл 3600 с: единиц/ч == qty за цикл; база подобрана через эталон ниже
    return {"kind": "extractor_control_unit", "pin_id": pin_id, "product": product,
            "qty_per_cycle": rate_qty, "install_time": "2026-09-25T06:30:00Z",
            "cycle_seconds": 3600, "expiry_time": expiry}


def _colony(*pins, char=1, planet=10):
    return {"character_id": char, "planet_id": planet, "system_name": "AV-VB6",
            "planet_index": 4, "pins": list(pins)}


INPUTS = {"Coolant": ["Water", "Electrolytes"], "Water": ["Aqueous Liquids"]}


def test_deficit_when_rate_below_threshold_and_chain_named_from_real_colonies():
    mine = _colony(_ecu(25000, product="Water"))          # 28789 ед./ч — выше порога
    weak = _colony(_ecu(9000, product="Water", pin_id=2), char=2, planet=11)   # ниже порога
    factory = _colony({"kind": "advanced_industry_facility", "product": "Coolant"}, char=3, planet=12)
    found = alerts.find_deficits([mine, weak, factory], AT, INPUTS)
    assert [d.pin_id for d in found] == [1, 2]      # оба ниже 48 000
    assert found[1].at_risk == ["Coolant"]


def test_no_chain_claim_without_real_consumer():
    found = alerts.find_deficits([_colony(_ecu(9000))], AT, INPUTS)
    assert len(found) == 1 and found[0].at_risk == []


def test_unknown_rate_is_not_a_deficit():
    pin = _ecu(9000)
    pin["install_time"] = None
    assert alerts.find_deficits([_colony(pin)], AT, INPUTS) == []


def test_healthy_extractor_is_not_a_deficit():
    assert alerts.find_deficits([_colony(_ecu(305343 * 3, product="Water"))], AT, INPUTS) == []


def test_expiring_within_lead_and_already_expired():
    soon = (AT + timedelta(hours=1)).isoformat()
    later = (AT + timedelta(hours=5)).isoformat()
    gone = (AT - timedelta(hours=3)).isoformat()
    colonies = [_colony(_ecu(1, expiry=soon, pin_id=1), _ecu(1, expiry=later, pin_id=2),
                        _ecu(1, expiry=gone, pin_id=3), _ecu(1, expiry=None, pin_id=4))]
    found = alerts.find_expiring(colonies, AT, lead_hours=2)
    assert {e.pin_id: e.expired for e in found} == {1: False, 3: True}


def test_recipe_inputs_cover_p1_source_and_higher_tiers():
    inputs = alerts.recipe_inputs()
    assert inputs["Water"] == ["Aqueous Liquids"]
    assert "Water" in inputs["Coolant"]
