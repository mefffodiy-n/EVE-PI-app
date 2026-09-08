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


class TestExport:
    def test_export_rejects_empty_plan(self, client):
        assert client.post("/api/export", json={"plan_data": []}).status_code == 400

    def test_export_limits_plan_size(self, client):
        response = client.post("/api/export", json={"plan_data": [{}] * 501})
        assert response.status_code == 400


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
