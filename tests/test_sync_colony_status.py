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


def _opener(planets: dict):
    """
    planets: {planet_id: {"type": str, "level": int, "pins": [expiry_iso|None], "name": str}}
    """
    listing = [
        {"planet_id": pid, "planet_type": p["type"],
         "upgrade_level": p["level"], "num_pins": len(p["pins"]), "solar_system_id": 30000142}
        for pid, p in planets.items()
    ]

    def opener(url, headers):
        m = re.search(r"/planets/(\d+)/$", url)
        if url.endswith("/planets/"):
            body = listing
        elif "/universe/planets/" in url:
            body = {"name": planets[int(m.group(1))]["name"], "system_id": 30000142}
        elif m:
            pins = [{"pin_id": i, "expiry_time": e} for i, e in enumerate(planets[int(m.group(1))]["pins"]) if e]
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


class TestSync:
    def test_writes_colonies_with_name_and_expiry(self):
        _add_esi_character()
        soon = _iso(90)
        client = EsiClient(opener=_opener({
            40009077: {"type": "barren", "level": 4, "pins": [soon, _iso(400)], "name": "Tanoo I"},
            40009078: {"type": "temperate", "level": 3, "pins": [], "name": "Tanoo II"},
        }))
        assert sync.main(client=client) == 0
        with session_scope() as s:
            rows = {r.planet_id: r for r in s.scalars(__import__("sqlalchemy").select(Colony)).all()}
            assert rows[40009077].planet_name == "Tanoo I"
            assert rows[40009077].planet_type == "barren"
            assert abs((rows[40009077].nearest_expiry - datetime.fromisoformat(soon)).total_seconds()) < 2
            assert rows[40009078].nearest_expiry is None

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
