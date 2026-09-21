"""
Тесты scripts.refresh_sde — сборщика скелета планет из SDE (Фаза 2
мультирегиональности, 21.09.2026). Сеть не трогаем: `sync()` работает
на маленьком фейковом zip-архиве, собранном в памяти; `current_build_
number()`/`download()` не тестируются на реальной сети — их сетевая
часть тонкая (см. докстринг скрипта), важна логика вокруг них.
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from scripts import refresh_sde


def _build_fake_sde(
    regions: list[dict],
    constellations: list[dict],
    systems: list[dict],
    planets: list[dict],
) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, rows in (
            ("mapRegions.jsonl", regions),
            ("mapConstellations.jsonl", constellations),
            ("mapSolarSystems.jsonl", systems),
            ("mapPlanets.jsonl", planets),
        ):
            zf.writestr(name, "\n".join(json.dumps(row) for row in rows))
    return buffer.getvalue()


FOUNTAIN = {"_key": 10000058, "name": {"en": "Fountain"}}
MINOTAUR = {"_key": 20000001, "name": {"en": "Minotaur"}, "regionID": 10000058}
KJB_SYSTEM = {"_key": 30004643, "name": {"en": "57-KJB"}, "constellationID": 20000001}

NEW_REGION = {"_key": 10000099, "name": {"en": "Testonia"}}
NEW_CONSTELLATION = {"_key": 20009999, "name": {"en": "Newvale"}, "regionID": 10000099}
NEW_SYSTEM = {"_key": 30099999, "name": {"en": "N3W-SY"}, "constellationID": 20009999}


@pytest.fixture
def seeded_fountain_kjb_i():
    """57-KJB I уже существует в БД (как после Фазы 1) — должна быть пропущена."""
    from infra.db import session_scope
    from infra.models import Planet, Region

    with session_scope() as session:
        region = Region(name="Fountain", status="ready")
        session.add(region)
        session.flush()
        session.add(Planet(
            region_id=region.id, constellation="MINOTAUR", system="57-KJB",
            planet_number=1, planet_type="Barren", radius_km=2570.0,
            poco_tax_rate=3.0, poco_owner="INIT",
            r0_densities={"Aqueous Liquids": 36.0},
        ))


class TestSync:
    def test_new_region_and_planet_are_created_with_no_data_status(self, tmp_path):
        payload = _build_fake_sde(
            regions=[NEW_REGION],
            constellations=[NEW_CONSTELLATION],
            systems=[NEW_SYSTEM],
            planets=[{
                "_key": 40000001, "solarSystemID": 30099999, "celestialIndex": 1,
                "radius": 5000000, "typeID": 2016,
            }],
        )
        zip_path = tmp_path / "sde.zip"
        zip_path.write_bytes(payload)

        stats = refresh_sde.sync(zip_path)

        assert stats["regions_created"] == 1
        assert stats["planets_created"] == 1
        assert stats["planets_skipped"] == 0
        assert stats["unknown_type_ids"] == []

        from infra.db import session_scope
        from infra.models import Planet, Region

        with session_scope() as session:
            region = session.query(Region).filter_by(name="Testonia").one()
            assert region.status == "no_data"
            planet = session.query(Planet).filter_by(system="N3W-SY", planet_number=1).one()
            assert planet.constellation == "NEWVALE"  # нормализовано в .upper()
            assert planet.radius_km == 5000.0  # метры -> км
            assert planet.planet_type == "Barren"
            assert planet.poco_tax_rate is None
            assert planet.r0_densities is None

    def test_existing_planet_is_skipped_not_overwritten(self, seeded_fountain_kjb_i, tmp_path):
        payload = _build_fake_sde(
            regions=[FOUNTAIN],
            constellations=[MINOTAUR],
            systems=[KJB_SYSTEM],
            planets=[{
                "_key": 40293647, "solarSystemID": 30004643, "celestialIndex": 1,
                "radius": 2570000, "typeID": 2016,
            }],
        )
        zip_path = tmp_path / "sde.zip"
        zip_path.write_bytes(payload)

        stats = refresh_sde.sync(zip_path)

        assert stats["planets_skipped"] == 1
        assert stats["planets_created"] == 0
        assert stats["regions_created"] == 0  # Fountain уже существует

        from infra.db import session_scope
        from infra.models import Planet, Region

        with session_scope() as session:
            region = session.query(Region).filter_by(name="Fountain").one()
            assert region.status == "ready"  # не понижен до no_data
            planet = session.query(Planet).filter_by(system="57-KJB", planet_number=1).one()
            assert planet.poco_tax_rate == 3.0  # не тронуто
            assert planet.r0_densities == {"Aqueous Liquids": 36.0}  # не тронуто

    def test_unknown_type_id_is_reported_not_fatal(self, tmp_path):
        payload = _build_fake_sde(
            regions=[NEW_REGION],
            constellations=[NEW_CONSTELLATION],
            systems=[NEW_SYSTEM],
            planets=[{
                "_key": 40000002, "solarSystemID": 30099999, "celestialIndex": 1,
                "radius": 5000000, "typeID": 999999,
            }],
        )
        zip_path = tmp_path / "sde.zip"
        zip_path.write_bytes(payload)

        stats = refresh_sde.sync(zip_path)

        assert stats["planets_created"] == 0
        assert stats["unknown_type_ids"] == [999999]
        # Регион, впрочем, ещё не создаётся раньше времени, т.к. планета
        # с неизвестным типом отбрасывается ДО создания региона.
        assert stats["regions_created"] == 0

    def test_second_planet_in_new_region_does_not_create_duplicate_region(self, tmp_path):
        payload = _build_fake_sde(
            regions=[NEW_REGION],
            constellations=[NEW_CONSTELLATION],
            systems=[NEW_SYSTEM],
            planets=[
                {"_key": 1, "solarSystemID": 30099999, "celestialIndex": 1,
                 "radius": 5000000, "typeID": 2016},
                {"_key": 2, "solarSystemID": 30099999, "celestialIndex": 2,
                 "radius": 6000000, "typeID": 11},
            ],
        )
        zip_path = tmp_path / "sde.zip"
        zip_path.write_bytes(payload)

        stats = refresh_sde.sync(zip_path)

        assert stats["regions_created"] == 1
        assert stats["planets_created"] == 2


class TestBuildNumber:
    def test_uses_injected_opener(self):
        assert refresh_sde.current_build_number(opener=lambda: 12345) == 12345


class TestMainSkipsDownloadWhenBuildUnchanged(object):
    def test_skips_download_and_sync_when_build_matches_snapshot(self, tmp_path, monkeypatch):
        snapshot = tmp_path / "sde_build.json"
        snapshot.write_text(json.dumps({"build": 42}), encoding="utf-8")
        monkeypatch.setattr(refresh_sde, "BUILD_SNAPSHOT", snapshot)
        monkeypatch.setattr(refresh_sde, "current_build_number", lambda: 42)

        called = []
        monkeypatch.setattr(refresh_sde, "download", lambda dest: called.append(dest))

        code = refresh_sde.main()

        assert code == 0
        assert called == []
