"""
scripts/sync_colony_status: снимок реальных колоний персонажа из ESI.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from infra import config, crypto
from infra.credentials import save_tokens
from infra.db import session_scope
from infra.models import Character, Colony
from scripts import sync_colony_status as sync
from scripts.esi_client import EsiClient


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", crypto.generate_key())
    import scripts.esi_client as ec
    monkeypatch.setattr(ec, "ETAG_STORE", tmp_path / "etags.json")


def _iso(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


SYSTEM_ID = 30000142
SYSTEM_NAME = "Jita"


def _opener(planets: dict):
    """
    planets: {planet_id: {"type": str, "level": int, "pins": [expiry_iso|None], "name": str}}

    Элемент "pins" может быть строкой/None (только expiry, как раньше) или
    словарём {"expiry": ..., "type_id": ...} — для проверки structures_detail.
    """
    listing = [
        {"planet_id": pid, "planet_type": p["type"],
         "upgrade_level": p["level"], "num_pins": len(p["pins"]), "solar_system_id": SYSTEM_ID}
        for pid, p in planets.items()
    ]

    def opener(url, headers):
        m = re.search(r"/(\d+)/$", url)
        if url.endswith("/planets/"):
            body = listing
        elif "/universe/systems/" in url:
            body = {"name": SYSTEM_NAME, "system_id": SYSTEM_ID}
        elif "/universe/planets/" in url:
            body = {"name": planets[int(m.group(1))]["name"], "system_id": SYSTEM_ID}
        elif m:
            pins = []
            for i, entry in enumerate(planets[int(m.group(1))]["pins"]):
                if isinstance(entry, dict):
                    pin = {"pin_id": i}
                    if entry.get("expiry"):
                        pin["expiry_time"] = entry["expiry"]
                    if entry.get("type_id"):
                        pin["type_id"] = entry["type_id"]
                    pins.append(pin)
                elif entry:
                    pins.append({"pin_id": i, "expiry_time": entry})
            body = {"pins": pins, "links": [], "routes": []}
        else:
            body = {}
        return 200, json.dumps(body), {}
    return opener


def _add_esi_character(cid=95538921, token_minutes=60):
    with session_scope() as s:
        s.add(Character(character_id=cid, name="Pilot", command_center_upgrades_level=5,
                        interplanetary_consolidation_level=5, source="esi"))
        if token_minutes is not None:
            save_tokens(s, cid, {"access_token": "a", "refresh_token": "r",
                                 "expires_in": int(token_minutes * 60)},
                        ["esi-planets.manage_planets.v1"])


class TestNearestExpiry:
    def test_picks_soonest(self):
        a, b = _iso(300), _iso(60)
        detail = {"pins": [{"expiry_time": a}, {"expiry_time": b}, {"pin_id": 9}]}
        assert sync.nearest_expiry(detail).isoformat() == b

    def test_none_without_expiry(self):
        assert sync.nearest_expiry({"pins": [{"pin_id": 1}]}) is None


class TestPlanetIndex:
    @pytest.mark.parametrize("name,idx", [
        ("Jita IV", 4), ("Jita I", 1), ("Jita IX", 9), ("Jita XIII", 13),
        ("Serpentis Prime IV", 4),
    ])
    def test_from_name(self, name, idx):
        sysname = name.rsplit(" ", 1)[0]
        assert sync.planet_index(name, sysname) == idx

    def test_unparseable_is_zero(self):
        assert sync.planet_index("Weird Moon 3", "Weird") == 0


class TestStructuresDetail:
    def test_maps_known_type_ids_and_orders_like_a_plan(self):
        # 2481 = structure:basic_industry_facility, 3068 = structure:extractor_control_unit,
        # 2524 = Barren Command Center — те же id, что отдаёт data/type_ids.json
        # фронтенду для иконок (см. docstring _kind_by_type_id).
        pins = [
            {"type_id": 2481}, {"type_id": 2481},
            {"type_id": 3068},
            {"type_id": 2524},
            {"type_id": 999999},  # неизвестный — молча пропускается
        ]
        assert sync.structures_detail(pins) == [
            {"kind": "command_center", "count": 1},
            {"kind": "extractor_control_unit", "count": 1},
            {"kind": "basic_industry_facility", "count": 2},
        ]

    def test_empty_without_pins(self):
        assert sync.structures_detail([]) == []

    def test_pin_without_type_id_skipped(self):
        assert sync.structures_detail([{"pin_id": 1}]) == []


class TestSync:
    def test_writes_colonies_with_name_and_expiry(self):
        _add_esi_character()
        soon = _iso(90)
        client = EsiClient(opener=_opener({
            40009077: {"type": "barren", "level": 4, "pins": [soon, _iso(400)], "name": "Jita IV"},
            40009078: {"type": "temperate", "level": 3, "pins": [], "name": "Jita V"},
        }))
        assert sync.main(client=client) == 0
        with session_scope() as s:
            rows = {r.planet_id: r for r in s.scalars(__import__("sqlalchemy").select(Colony)).all()}
            assert rows[40009077].planet_name == "Jita IV"
            assert rows[40009077].system_name == "Jita"
            assert rows[40009077].planet_index == 4
            assert rows[40009077].planet_type == "barren"
            assert abs((rows[40009077].nearest_expiry - datetime.fromisoformat(soon)).total_seconds()) < 2
            assert rows[40009078].nearest_expiry is None
            assert rows[40009078].planet_index == 5

    def test_writes_structures_from_real_pin_type_ids(self):
        _add_esi_character()
        client = EsiClient(opener=_opener({
            40009077: {"type": "barren", "level": 4, "name": "Jita IV", "pins": [
                {"type_id": 2524},                 # Barren Command Center
                {"type_id": 3068, "expiry": _iso(90)},  # extractor control unit
                {"type_id": 2481}, {"type_id": 2481},   # 2 basic factories
            ]},
        }))
        assert sync.main(client=client) == 0
        with session_scope() as s:
            row = s.get(Colony, (95538921, 40009077))
            assert row.structures == [
                {"kind": "command_center", "count": 1},
                {"kind": "extractor_control_unit", "count": 1},
                {"kind": "basic_industry_facility", "count": 2},
            ]

    def test_removes_colonies_no_longer_in_game(self):
        _add_esi_character()
        with session_scope() as s:
            s.add(Colony(character_id=95538921, planet_id=999, planet_name="Old",
                         planet_type="lava", upgrade_level=1, num_pins=1, nearest_expiry=None))
        client = EsiClient(opener=_opener({
            40009077: {"type": "barren", "level": 4, "pins": [_iso(120)], "name": "Tanoo I"},
        }))
        assert sync.main(client=client) == 0
        with session_scope() as s:
            ids = {r.planet_id for r in s.scalars(__import__("sqlalchemy").select(Colony)).all()}
            assert ids == {40009077}

    def test_no_token_skips(self):
        _add_esi_character(token_minutes=None)
        called = []
        client = EsiClient(opener=lambda u, h: called.append(u) or (200, "[]", {}))
        assert sync.main(client=client) == 0
        assert called == []

    def test_no_esi_characters_noop(self):
        assert sync.main(client=EsiClient(opener=lambda u, h: (200, "[]", {}))) == 0
