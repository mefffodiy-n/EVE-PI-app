"""
Тесты HTTP-слоя (Flask).

Проверяют:
  - контракт эндпоинтов совпадает с тем, что вызывает web/index.html;
  - валидация не пускает мусор в domain-слой;
  - механизмы снижения нагрузки (ETag, лимиты размера) работают.
"""

from __future__ import annotations

import pandas as pd
import pytest

from domain.planets import RADIUS_COLUMN, PlanetBook

SAMPLE_PLANETS = [
    {"Constellation": "ALPHA", "System": "HOME", "Planet": "4", "Type": "Barren", RADIUS_COLUMN: 5820},
    {"Constellation": "ALPHA", "System": "HOME", "Planet": "7", "Type": "Temperate", RADIUS_COLUMN: 8100},
    {"Constellation": "BETA", "System": "FAR", "Planet": "1", "Type": "Barren", RADIUS_COLUMN: 18000},
]


@pytest.fixture
def client(monkeypatch):
    book = PlanetBook(pd.DataFrame(SAMPLE_PLANETS))

    import api.blueprints.reference as reference

    monkeypatch.setattr(reference, "load_planets", lambda *a, **k: book)
    reference._initial_payload.cache_clear()

    from api import create_app

    app = create_app({"TESTING": True})
    with app.test_client() as test_client:
        yield test_client

    reference._initial_payload.cache_clear()


class TestReference:
    def test_initial_data_matches_frontend_contract(self, client):
        """web/index.html читает res.bases, res.products, res.product_ids."""
        response = client.get("/api/initial-data")
        assert response.status_code == 200
        body = response.get_json()
        assert body["status"] == "success"
        assert set(body) >= {"bases", "products", "product_ids"}
        assert body["bases"] == ["ALPHA", "BETA"]

    def test_initial_data_only_lists_processing_tiers(self, client):
        """В выдаче только P2-P4: P1 и сырьё пользователь не выбирает как цель."""
        products = client.get("/api/initial-data").get_json()["products"]
        assert "Biocells" in products          # P2
        assert "Broadcast Node" in products    # P4
        assert "Water" not in products         # P1

    def test_initial_data_returns_304_on_repeat(self, client):
        """
        ETag — самый дешёвый способ снять нагрузку: на повторный запрос
        сервер не формирует тело ответа вообще.
        """
        first = client.get("/api/initial-data")
        etag = first.headers.get("ETag")
        assert etag
        second = client.get("/api/initial-data", headers={"If-None-Match": etag})
        assert second.status_code == 304

    def test_systems_filters_by_constellation(self, client):
        response = client.post("/api/systems", json={"constellations": ["ALPHA"]})
        assert response.get_json()["systems"] == ["HOME"]

    @pytest.mark.parametrize(
        "payload",
        [
            {},                                    # нет поля
            {"constellations": "ALPHA"},           # не список
            {"constellations": []},                # пустой список
        ],
    )
    def test_systems_rejects_bad_input(self, client, payload):
        response = client.post("/api/systems", json=payload)
        assert response.status_code == 400
        assert response.get_json()["status"] == "error"

    def test_product_ids_include_command_centers(self, client):
        """
        Список закупки ищет иконки по ключу вида «Barren Command Center».
        Пока отдавались id только целевых продуктов, эти иконки не грузились.
        """
        ids = client.get("/api/initial-data").get_json()["product_ids"]
        if not ids:
            pytest.skip("data/type_ids.json отсутствует")
        assert ids.get("Barren Command Center") == 2524
        assert ids.get("Temperate Command Center") == 2254

    def test_product_ids_include_intermediate_tiers(self, client):
        """В дашборде показываются и P1 — их id тоже должны приходить."""
        ids = client.get("/api/initial-data").get_json()["product_ids"]
        if not ids:
            pytest.skip("data/type_ids.json отсутствует")
        assert "Biofuels" in ids

    def test_recipe_inputs_exposed_for_factory_panel(self, client):
        """
        Панель колонии показывает «Вход» у настоящей фабрики (ESI отдаёт
        только что она производит, не что потребляет) — имена входов
        recipes.json, проверенные по источникам (правило 2).
        """
        body = client.get("/api/initial-data").get_json()
        assert "recipe_inputs" in body
        # P1 — вход один, сырьё R0 из поля source (не inputs).
        assert body["recipe_inputs"]["Biofuels"] == ["Carbon Compounds"]

    def test_thresholds_exposed_for_ui(self, client):
        body = client.get("/api/thresholds/5").get_json()
        assert body["thresholds"]["p2p3_2factory"] is not None
        assert body["thresholds"]["p4_2factory"] is not None

    def test_thresholds_rejects_bad_ccu_level(self, client):
        assert client.get("/api/thresholds/9").status_code == 400


class TestPlans:
    @pytest.mark.parametrize(
        "payload, reason",
        [
            ({"factory_sys": "HOME", "target_products": ["Biocells"]}, "нет constellations"),
            ({"constellations": [], "factory_sys": "HOME", "target_products": ["Biocells"]}, "пустые констелляции"),
            ({"constellations": ["ALPHA"], "factory_sys": "", "target_products": ["Biocells"]}, "нет системы"),
            ({"constellations": ["ALPHA"], "factory_sys": "HOME", "target_products": []}, "нет продуктов"),
        ],
    )
    def test_calculate_validates_input(self, client, payload, reason):
        response = client.post("/api/calculate", json=payload)
        assert response.status_code == 400, reason

    def test_calculate_limits_product_count(self, client):
        """
        Ограничение защищает воркер: Flask синхронный, и один огромный
        расчёт заблокировал бы обслуживание остальных пользователей.
        """
        response = client.post(
            "/api/calculate",
            json={
                "constellations": ["ALPHA"],
                "factory_sys": "HOME",
                "target_products": ["Biocells"] * 25,
            },
        )
        assert response.status_code == 400
        assert "Слишком много" in response.get_json()["message"]


SAMPLE_PLAN_ROW = {
    "character": "Chief 1", "role": "Переработка P2/P3", "constellation": "ALPHA",
    "system": "HOME", "planet": "4", "planet_type": "Barren", "planet_radius_km": 5820.0,
    "cc_type": "1x Barren Command Center", "res_in": "Biofuels", "res_out": "Biocells",
    "structures": "12 фабрик", "template_key": "p2p3_1factory",
    "cpu_percent": 39.2, "pg_percent": 95.1,
}


class TestExport:
    def test_export_rejects_empty_plan(self, client):
        assert client.post("/api/export", json={"plan_data": []}).status_code == 400

    def test_export_limits_plan_size(self, client):
        response = client.post("/api/export", json={"plan_data": [{}] * 501})
        assert response.status_code == 400

    def test_export_rejects_non_object_rows(self, client):
        response = client.post("/api/export", json={"plan_data": ["строка"]})
        assert response.status_code == 400

    def test_export_returns_xlsx_attachment(self, client):
        response = client.post("/api/export", json={"plan_data": [SAMPLE_PLAN_ROW]})
        assert response.status_code == 200
        assert "spreadsheetml" in response.headers["Content-Type"]
        assert "attachment" in response.headers["Content-Disposition"]
        assert response.data[:2] == b"PK", "xlsx — это zip-архив"

    def test_export_workbook_structure(self, client):
        from io import BytesIO

        from openpyxl import load_workbook

        response = client.post("/api/export", json={"plan_data": [SAMPLE_PLAN_ROW]})
        workbook = load_workbook(BytesIO(response.data))
        assert workbook.sheetnames == ["План", "Сводка", "Проверка"]
        assert workbook["План"].max_row == 2
        assert workbook["План"].freeze_panes == "A2"

    def test_summary_uses_formulas_not_precomputed_values(self, client):
        """
        Сводка должна пересчитываться, если пользователь правит лист
        «План» вручную, поэтому там формулы, а не числа из Python.
        """
        from io import BytesIO

        from openpyxl import load_workbook

        response = client.post("/api/export", json={"plan_data": [SAMPLE_PLAN_ROW]})
        workbook = load_workbook(BytesIO(response.data))
        assert str(workbook["Сводка"]["B2"].value).startswith("=COUNTIF")
        assert str(workbook["Проверка"]["F2"].value).startswith("=100-")

    def test_overloaded_planets_are_highlighted(self, client):
        """Планеты с загрузкой от 90% подсвечиваются — они сломаются первыми."""
        from io import BytesIO

        from openpyxl import load_workbook

        response = client.post("/api/export", json={"plan_data": [SAMPLE_PLAN_ROW]})
        workbook = load_workbook(BytesIO(response.data))
        pg_cell = workbook["План"]["N2"]  # pg_percent = 95.1
        assert pg_cell.fill.start_color.rgb == "00FFF2CC"


class TestColonies:
    def test_empty_when_nothing_synced(self, client):
        body = client.get("/api/colonies").get_json()
        assert body["status"] == "success"
        assert body["colonies"] == []

    def test_returns_synced_colonies_soonest_first(self, client):
        from datetime import datetime, timedelta, timezone

        from infra.db import session_scope
        from infra.models import Character, Colony

        now = datetime.now(timezone.utc)
        with session_scope() as s:
            s.add(Character(character_id=1, name="Pilot", command_center_upgrades_level=5,
                            interplanetary_consolidation_level=5, source="esi"))
            s.add(Colony(character_id=1, planet_id=10, planet_name="B II", system_name="B",
                         planet_index=2, planet_type="barren", upgrade_level=4, num_pins=8,
                         nearest_expiry=now + timedelta(hours=9),
                         structures=[{"kind": "command_center", "count": 1}]))
            s.add(Colony(character_id=1, planet_id=11, planet_name="B III", system_name="B",
                         planet_index=3, planet_type="temperate",
                         upgrade_level=3, num_pins=5, nearest_expiry=now + timedelta(hours=2)))
            s.add(Colony(character_id=1, planet_id=12, planet_name="B IV", system_name="B",
                         planet_index=4, planet_type="lava",
                         upgrade_level=1, num_pins=2, nearest_expiry=None))

        rows = client.get("/api/colonies").get_json()["colonies"]
        assert [r["planet_name"] for r in rows] == ["B III", "B II", "B IV"]
        assert rows[0]["character"] == "Pilot"
        assert rows[0]["character_id"] == 1
        assert (rows[0]["system_name"], rows[0]["planet_index"]) == ("B", 3)
        assert rows[2]["nearest_expiry"] is None
        # B II — вторая по сортировке (soonest first), structures задан у неё.
        assert rows[1]["structures"] == [{"kind": "command_center", "count": 1}]
        assert rows[2]["structures"] == []

    def test_extractor_pins_carry_extraction_history(self, client):
        """
        История добычи (ExtractionSample) приклеивается к своему пину по
        (character_id, planet_id, pin_id) — не выдумывается заново на
        каждый запрос, а копится sync_colony_status.py.
        """
        from datetime import datetime, timezone

        from infra.db import session_scope
        from infra.models import Character, Colony, ExtractionSample

        with session_scope() as s:
            s.add(Character(character_id=1, name="Pilot", command_center_upgrades_level=5,
                            interplanetary_consolidation_level=5, source="esi"))
            s.add(Colony(character_id=1, planet_id=10, planet_name="B II", system_name="B",
                         planet_index=2, planet_type="barren", upgrade_level=4, num_pins=1,
                         pins=[{"kind": "extractor_control_unit", "pin_id": 555,
                                "product": "Suspended Plasma", "qty_per_cycle": 5000,
                                "cycle_seconds": 900}]))
            s.add(ExtractionSample(character_id=1, planet_id=10, pin_id=555,
                                    product_type_id=2308, qty_per_cycle=5000, cycle_seconds=900,
                                    sampled_at=datetime.now(timezone.utc)))

        rows = client.get("/api/colonies").get_json()["colonies"]
        history = rows[0]["pins"][0]["extraction_history"]
        assert len(history) == 1
        assert history[0]["qty_per_cycle"] == 5000
        assert history[0]["cycle_seconds"] == 900


class TestMarket:
    def test_market_never_calls_external_service(self, client):
        """
        Правило проекта: пользователь не инициирует внешних обращений.
        Эндпоинт обязан отвечать из кэша, даже когда снапшота ещё нет.
        """
        body = client.get("/api/market/best-product").get_json()
        assert body["status"] == "success"
        assert body["recommended"] is None
        assert body["analytics"] == []


class TestErrorFormat:
    def test_unknown_route_returns_json_not_html(self, client):
        """Фронтенд везде читает поле status — ошибки должны быть в том же формате."""
        response = client.get("/api/nonexistent")
        assert response.status_code == 404
        assert response.get_json()["status"] == "error"


class TestCache:
    def test_lru_cache_evicts_and_counts(self):
        from api.cache import LruResultCache

        cache = LruResultCache(maxsize=2)
        cache.get_or_compute("a", lambda: 1)
        cache.get_or_compute("b", lambda: 2)
        cache.get_or_compute("a", lambda: 99)      # попадание, значение не пересчитывается
        cache.get_or_compute("c", lambda: 3)       # вытесняет "b" как самый давний

        assert cache.stats()["size"] == 2
        assert cache.hits == 1
        assert cache.get_or_compute("a", lambda: 99) == 1

    def test_cache_key_is_order_independent_for_same_data(self):
        from api.cache import cache_key

        assert cache_key(["a", "b"], "x") == cache_key(["a", "b"], "x")
        assert cache_key(["a", "b"], "x") != cache_key(["b", "a"], "x")
