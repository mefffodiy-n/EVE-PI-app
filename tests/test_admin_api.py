"""
Тесты api/blueprints/admin.py — Фаза 3 мультирегиональности (28.09.2026).

Изоляция БД — autouse-фикстура conftest.py::isolate_database. Доступ
проверяется в prod-режиме (IS_DEV=False): в dev load_characters()
игнорирует account_id и видит всех персонажей сразу, что здесь как раз
не годится для проверки "чужому — 404".
"""

from __future__ import annotations

import io

import pytest

from infra import config
from infra.db import session_scope
from infra.models import Character, Planet, Region


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "IS_DEV", False)
    monkeypatch.setattr(config, "ADMIN_CHARACTER_IDS", frozenset({500001}))

    from api import create_app

    app = create_app({"TESTING": True})
    with app.test_client() as test_client:
        yield test_client


def _login_as(client, account_id: str) -> None:
    with client.session_transaction() as sess:
        sess["account_id"] = account_id


def _add_character(character_id: int, account_id: str) -> None:
    with session_scope() as s:
        s.add(Character(
            character_id=character_id, name=f"Pilot {character_id}",
            command_center_upgrades_level=5, interplanetary_consolidation_level=5,
            source="esi", account_id=account_id,
        ))


def _add_region(name: str, status: str) -> int:
    with session_scope() as s:
        region = Region(name=name, status=status)
        s.add(region)
        s.flush()
        return region.id


class TestAccessControl:
    def test_anonymous_gets_404_not_403(self, client):
        response = client.get("/api/admin/regions")
        assert response.status_code == 404

    def test_non_admin_character_gets_404(self, client):
        _add_character(999999, "acct-plain")
        _login_as(client, "acct-plain")
        response = client.get("/api/admin/regions")
        assert response.status_code == 404

    def test_admin_character_gets_200(self, client):
        _add_character(500001, "acct-admin")
        _login_as(client, "acct-admin")
        response = client.get("/api/admin/regions")
        assert response.status_code == 200
        assert response.get_json()["status"] == "success"

    def test_empty_allowlist_locks_everyone_out(self, client, monkeypatch):
        """PI_ADMIN_CHARACTER_IDS не задан — раздел не заведён никем."""
        monkeypatch.setattr(config, "ADMIN_CHARACTER_IDS", frozenset())
        _add_character(500001, "acct-admin")
        _login_as(client, "acct-admin")
        response = client.get("/api/admin/regions")
        assert response.status_code == 404


@pytest.fixture
def admin_client(client):
    _add_character(500001, "acct-admin")
    _login_as(client, "acct-admin")
    return client


class TestRegionsList:
    def test_lists_all_statuses_including_no_data(self, admin_client):
        _add_region("Fountain", "ready")
        _add_region("Testonia", "no_data")

        body = admin_client.get("/api/admin/regions").get_json()
        by_name = {r["name"]: r for r in body["regions"]}
        assert by_name["Fountain"]["status"] == "ready"
        assert by_name["Testonia"]["status"] == "no_data"

    def test_planet_count_is_accurate(self, admin_client):
        region_id = _add_region("Testonia", "no_data")
        with session_scope() as s:
            s.add(Planet(region_id=region_id, constellation="NEWVALE", system="N3W-SY",
                          planet_number=1, planet_type="Barren", radius_km=5000.0))
            s.add(Planet(region_id=region_id, constellation="NEWVALE", system="N3W-SY",
                          planet_number=2, planet_type="Temperate", radius_km=6000.0))

        body = admin_client.get("/api/admin/regions").get_json()
        region = next(r for r in body["regions"] if r["name"] == "Testonia")
        assert region["planet_count"] == 2


class TestMarkReady:
    def test_switches_status_to_ready(self, admin_client):
        region_id = _add_region("Testonia", "no_data")
        response = admin_client.post(f"/api/admin/regions/{region_id}/ready")
        assert response.get_json()["status"] == "success"

        with session_scope() as s:
            assert s.get(Region, region_id).status == "ready"

    def test_unknown_region_is_404(self, admin_client):
        response = admin_client.post("/api/admin/regions/999999/ready")
        assert response.status_code == 404

    def test_becomes_visible_via_load_planets_after_ready(self, admin_client):
        """load_planets() фильтрует по status='ready' (Фаза 2) — после
        ручного 'Готово' регион должен в ней появиться."""
        from domain.planets import load_planets

        region_id = _add_region("Testonia", "no_data")
        with session_scope() as s:
            s.add(Planet(region_id=region_id, constellation="NEWVALE", system="N3W-SY",
                          planet_number=1, planet_type="Barren", radius_km=5000.0))

        load_planets.cache_clear()
        assert "Testonia" not in load_planets().regions()

        admin_client.post(f"/api/admin/regions/{region_id}/ready")
        load_planets.cache_clear()
        assert "Testonia" in load_planets().regions()


class TestTemplateDownload:
    def test_includes_all_54_resource_columns_even_when_empty(self, admin_client):
        from domain.planets import P2_DIRECT_RESOURCE_NAMES, R0_RESOURCE_NAMES

        region_id = _add_region("Testonia", "no_data")
        with session_scope() as s:
            s.add(Planet(region_id=region_id, constellation="NEWVALE", system="N3W-SY",
                          planet_number=1, planet_type="Barren", radius_km=5000.0))

        response = admin_client.get(f"/api/admin/regions/{region_id}/template.csv")
        assert response.status_code == 200
        assert response.mimetype == "text/csv"
        header = response.get_data(as_text=True).splitlines()[0]
        for name in (*R0_RESOURCE_NAMES, *P2_DIRECT_RESOURCE_NAMES):
            assert name in header

    def test_snapshot_includes_already_known_values(self, admin_client):
        """«скачал — что уже есть» (ROADMAP): не пустой скелет, а текущее состояние."""
        region_id = _add_region("Testonia", "no_data")
        with session_scope() as s:
            s.add(Planet(region_id=region_id, constellation="NEWVALE", system="N3W-SY",
                          planet_number=1, planet_type="Barren", radius_km=5000.0,
                          poco_tax_rate=3.0, r0_densities={"Water": 42.0}))

        text = admin_client.get(f"/api/admin/regions/{region_id}/template.csv").get_data(as_text=True)
        assert "42.0" in text
        assert "3.0" in text

    def test_unknown_region_is_404(self, admin_client):
        response = admin_client.get("/api/admin/regions/999999/template.csv")
        assert response.status_code == 404


class TestUpload:
    def _csv(self, header: list[str], rows: list[list[str]]) -> io.BytesIO:
        text = ";".join(header) + "\n" + "\n".join(";".join(row) for row in rows)
        return io.BytesIO(text.encode("utf-8"))

    def test_fills_only_provided_cells_without_erasing_existing(self, admin_client):
        region_id = _add_region("Testonia", "no_data")
        with session_scope() as s:
            s.add(Planet(region_id=region_id, constellation="NEWVALE", system="N3W-SY",
                          planet_number=1, planet_type="Barren", radius_km=5000.0,
                          r0_densities={"Water": 42.0}))

        # POCO Tax Rate заполнена, "Water" — пустая ячейка (не должна стереть 42.0).
        payload = self._csv(
            ["Constellation", "System", "Planet", "Type", "Radius [km]",
             "POCO Tax Rate [%]", "POCO Owner", "Water"],
            [["NEWVALE", "N3W-SY", "1", "Barren", "5000", "3.0", "INIT", ""]],
        )
        response = admin_client.post(
            f"/api/admin/regions/{region_id}/upload",
            data={"file": (payload, "template.csv")},
            content_type="multipart/form-data",
        )
        body = response.get_json()
        assert body["updated"] == 1
        assert body["unmatched"] == []

        with session_scope() as s:
            planet = s.query(Planet).filter_by(region_id=region_id, system="N3W-SY").one()
            assert planet.poco_tax_rate == 3.0
            assert planet.r0_densities == {"Water": 42.0}  # не стёрто пустой ячейкой

    def test_unmatched_rows_are_reported_not_fatal(self, admin_client):
        region_id = _add_region("Testonia", "no_data")
        payload = self._csv(
            ["Constellation", "System", "Planet", "Type", "Radius [km]",
             "POCO Tax Rate [%]", "POCO Owner"],
            [["NEWVALE", "GHOST-SYS", "9", "Barren", "5000", "3.0", "INIT"]],
        )
        response = admin_client.post(
            f"/api/admin/regions/{region_id}/upload",
            data={"file": (payload, "template.csv")},
            content_type="multipart/form-data",
        )
        body = response.get_json()
        assert body["updated"] == 0
        assert body["unmatched"] == [{"system": "GHOST-SYS", "planet": 9}]

    def test_missing_file_is_a_clean_error_not_500(self, admin_client):
        region_id = _add_region("Testonia", "no_data")
        response = admin_client.post(f"/api/admin/regions/{region_id}/upload")
        assert response.status_code == 400


class TestPlanetsListAndUpdate:
    def test_lists_planets_of_one_region(self, admin_client):
        region_id = _add_region("Testonia", "no_data")
        with session_scope() as s:
            s.add(Planet(region_id=region_id, constellation="NEWVALE", system="N3W-SY",
                          planet_number=1, planet_type="Barren", radius_km=5000.0))

        body = admin_client.get(f"/api/admin/regions/{region_id}/planets").get_json()
        assert len(body["planets"]) == 1
        assert body["planets"][0]["system"] == "N3W-SY"

    def test_patch_updates_single_planet(self, admin_client):
        region_id = _add_region("Testonia", "no_data")
        with session_scope() as s:
            planet = Planet(region_id=region_id, constellation="NEWVALE", system="N3W-SY",
                             planet_number=1, planet_type="Barren", radius_km=5000.0)
            s.add(planet)
            s.flush()
            planet_id = planet.id

        response = admin_client.patch(
            f"/api/admin/planets/{planet_id}",
            json={"poco_tax_rate": 1.0, "r0_densities": {"Water": 10.0}},
        )
        assert response.get_json()["status"] == "success"

        with session_scope() as s:
            planet = s.get(Planet, planet_id)
            assert planet.poco_tax_rate == 1.0
            assert planet.r0_densities == {"Water": 10.0}

    def test_patch_unknown_planet_is_404(self, admin_client):
        response = admin_client.patch("/api/admin/planets/999999", json={"poco_tax_rate": 1.0})
        assert response.status_code == 404
