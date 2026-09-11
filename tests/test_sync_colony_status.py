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
    monkeypatch.setattr(sync, "SCHEMATIC_CACHE_PATH", tmp_path / "schematics.json")
    monkeypatch.setattr(sync, "VOLUME_CACHE_PATH", tmp_path / "type_volumes.json")


def _iso(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


SYSTEM_ID = 30000142
SYSTEM_NAME = "Jita"


def _opener(planets: dict, schematics: dict | None = None, volumes: dict | None = None):
    """
    planets: {planet_id: {"type": str, "level": int, "pins": [expiry_iso|None], "name": str}}

    Элемент "pins" может быть строкой/None (только expiry, как раньше) или
    словарём {"expiry", "type_id", "schematic_id", "last_cycle_start",
    "contents", "extractor_details", "install_time"} — для structures_detail
    и pins_detail. schematics — {schematic_id: {"schematic_name", "cycle_time"}}
    для мока GET /universe/schematics/{id}/. volumes — {type_id: объём_м3}
    для мока GET /universe/types/{id}/ (поле "volume").
    """
    schematics = schematics or {}
    volumes = volumes or {}
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
        elif "/universe/schematics/" in url:
            sid = int(m.group(1))
            if sid not in schematics:
                return 404, "{}", {}
            body = schematics[sid]
        elif "/universe/types/" in url:
            tid = int(m.group(1))
            if tid not in volumes:
                return 404, "{}", {}
            body = {"volume": volumes[tid]}
        elif m:
            pins = []
            for i, entry in enumerate(planets[int(m.group(1))]["pins"]):
                if isinstance(entry, dict):
                    pin = {"pin_id": i}
                    for key in ("type_id", "schematic_id", "last_cycle_start",
                                "contents", "extractor_details", "install_time"):
                        if key in entry:
                            pin[key] = entry[key]
                    if entry.get("expiry"):
                        pin["expiry_time"] = entry["expiry"]
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

    def test_non_temperate_planet_structures_recognised(self):
        """
        Регрессия 11.09.2026: считалось, что только командный центр
        специфичен для типа планеты, у остальных структур — один type_id
        на всех. Неверно: у КАЖДОЙ структуры свой id на планету
        («Storm Basic Industry Facility» != «Temperate Basic Industry
        Facility»). Реальная Storm-колония показывала только командный
        центр — эти самые пины молча пропускались.
        """
        pins = [
            {"type_id": 2550},  # Storm Command Center
            {"type_id": 3067},  # Storm Extractor Control Unit
            {"type_id": 2557},  # Storm Launchpad
            {"type_id": 2561},  # Storm Storage Facility
            {"type_id": 2483}, {"type_id": 2483},  # Storm Basic Industry Facility ×2
        ]
        assert sync.structures_detail(pins) == [
            {"kind": "command_center", "count": 1},
            {"kind": "launchpad", "count": 1},
            {"kind": "storage_facility", "count": 1},
            {"kind": "extractor_control_unit", "count": 1},
            {"kind": "basic_industry_facility", "count": 2},
        ]


class TestSchematicInfo:
    def test_resolves_and_caches(self, tmp_path):
        client = EsiClient(opener=_opener({}, schematics={
            122: {"schematic_name": "Plasmoids", "cycle_time": 1800},
        }))
        info = sync.schematic_info(client, 122)
        assert info == {"product": "Plasmoids", "cycle_minutes": 30.0}

        # Второй вызов — из кэша на диске, без нового запроса к ESI.
        calls = []
        counting_client = EsiClient(opener=lambda u, h: calls.append(u) or (200, "{}", {}))
        assert sync.schematic_info(counting_client, 122) == info
        assert calls == []

    def test_unknown_schematic_is_none(self):
        client = EsiClient(opener=_opener({}, schematics={}))
        assert sync.schematic_info(client, 999999) is None


class TestPinDetail:
    def test_extractor_control_unit(self):
        with_names = sync._name_by_type_id  # прогреть кэш реальными данными
        with_names.cache_clear()
        pin = {
            "expiry_time": "2026-09-11T14:45:01Z",
            "install_time": "2026-09-10T14:45:01Z",
            "extractor_details": {
                "cycle_time": 900, "product_type_id": 2308, "qty_per_cycle": 5770,
                "heads": [{"head_id": i} for i in range(10)],
            },
        }
        detail = sync.pin_detail(pin, "extractor_control_unit", client=None)
        assert detail["heads"] == 10
        assert detail["product"] == "Suspended Plasma"
        assert detail["qty_per_cycle"] == 5770
        assert detail["cycle_seconds"] == 900
        assert detail["expiry_time"] == "2026-09-11T14:45:01Z"

    def test_industry_facility_resolves_via_schematic_info(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sync, "SCHEMATIC_CACHE_PATH", tmp_path / "schematics.json")
        client = EsiClient(opener=_opener({}, schematics={
            122: {"schematic_name": "Plasmoids", "cycle_time": 1800},
        }))
        pin = {"schematic_id": 122, "last_cycle_start": "2026-09-10T10:04:34Z",
               "contents": [{"type_id": 2308, "amount": 2604}]}
        detail = sync.pin_detail(pin, "basic_industry_facility", client)
        assert detail["product"] == "Plasmoids"
        assert detail["cycle_minutes"] == 30.0
        assert detail["last_cycle_start"] == "2026-09-10T10:04:34Z"
        assert detail["contents"] == [{"type_id": 2308, "name": "Suspended Plasma", "amount": 2604}]

    def test_storage_contents(self):
        client = EsiClient(opener=_opener({}, volumes={2308: 0.15}))
        pin = {"contents": [{"type_id": 2308, "amount": 25320}]}
        detail = sync.pin_detail(pin, "storage_facility", client)
        assert detail["contents"] == [{"type_id": 2308, "name": "Suspended Plasma", "amount": 25320}]
        assert detail["used_m3"] == 3798.0
        assert detail["capacity_m3"] == 12_000

    def test_storage_used_volume_is_none_without_item_volume(self):
        """
        Честный пробел, а не заниженная сумма: если объём хоть одного
        предмета неизвестен, used_m3 не считается вовсе (правило 1).
        """
        client = EsiClient(opener=_opener({}, volumes={}))
        pin = {"contents": [{"type_id": 2308, "amount": 100}]}
        detail = sync.pin_detail(pin, "launchpad", client)
        assert detail["used_m3"] is None
        assert detail["capacity_m3"] == 10_000

    def test_storage_used_volume_zero_when_empty(self):
        detail = sync.pin_detail({"contents": []}, "launchpad", client=None)
        assert detail["used_m3"] == 0.0

    def test_command_center_has_no_extra_fields(self):
        assert sync.pin_detail({}, "command_center", client=None) == {"kind": "command_center"}


class TestRealColonyLoad:
    """
    real_colony_load() — CPU/Power командного центра НАСТОЯЩЕЙ колонии,
    из реального состава структур/линков/голов (ESI) и реального радиуса
    планеты (data/planet_industry.csv, не ESI — она радиус не отдаёт).
    """

    def test_computes_from_real_planet_in_region_csv(self):
        # 57-KJB I — реальная строка CSV, радиус 2570 км (см. test_planets.py).
        structures = [
            {"kind": "command_center", "count": 1},
            {"kind": "extractor_control_unit", "count": 1},
            {"kind": "basic_industry_facility", "count": 4},
        ]
        raw_pins = [
            {"extractor_details": {"heads": [{"head_id": i} for i in range(2)]}},
        ]
        cpu, pg = sync.real_colony_load(structures, raw_pins, link_count=7, system="57-KJB",
                                         planet_index_=1, ccu_level=5)
        assert cpu is not None and pg is not None
        assert cpu > 0 and pg > 0

    def test_none_when_planet_not_in_region_csv(self):
        """Jita — не входит в загруженный регион planet_industry.csv."""
        cpu, pg = sync.real_colony_load([], [], link_count=0, system="Jita",
                                         planet_index_=4, ccu_level=5)
        assert (cpu, pg) == (None, None)

    def test_command_center_excluded_before_calling_domain(self):
        """
        Командный центр — единственная структура в списке. Если бы он не
        фильтровался, calculate_real_colony_load() упал бы (его нет в
        каталоге потребления) — здесь фиксируется, что real_colony_load()
        отфильтровывает его сам, а не полагается на пустой список извне.
        """
        structures = [{"kind": "command_center", "count": 1}]
        cpu, pg = sync.real_colony_load(structures, [], link_count=0, system="57-KJB",
                                         planet_index_=1, ccu_level=5)
        assert cpu is not None and pg is not None


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
