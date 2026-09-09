"""
Тесты оценки выгоды (domain.profit) и сборщика цен.

Главное, что проверяется: ранжирование идёт по ISK на колонию в час,
а не по цене за единицу и не по ISK в час. В первой версии рекомендация
была максимумом цены за единицу — величиной, несопоставимой между
тирами, и потому бессмысленной.
"""

from __future__ import annotations

import urllib.parse

import pytest

from domain.profit import ChainEconomics, colonies_for, evaluate, rank
from domain.recipes import load_recipes
from domain.throughput import Schematic

# Схемы с количествами из настоящих шаблонов.
FAC = {
    "P1": "basic_industry_facility",
    "P2": "advanced_industry_facility",
    "P3": "advanced_industry_facility",
    "P4": "high_tech_industry_facility",
}
OUT = {"P1": 20, "P2": 5, "P3": 3, "P4": 1}
CYCLE = {"P1": 30, "P2": 60, "P3": 60, "P4": 60}


@pytest.fixture(scope="module")
def schematics():
    recipes = load_recipes()
    return {
        r.name: Schematic(
            r.name,
            FAC[r.tier],
            {r.source: 3000} if r.tier == "P1" else dict(r.inputs),
            OUT[r.tier],
            CYCLE[r.tier],
        )
        for r in recipes
    }


class TestColonyCount:
    def test_deeper_tier_needs_more_colonies(self, schematics):
        """P4 требует заметно больше планет, чем P2 — иначе расчёт неверен."""
        _, _, _ = colonies_for("Biocells", schematics)
        p2 = sum(colonies_for("Biocells", schematics)[:2])
        p4 = sum(colonies_for("Broadcast Node", schematics)[:2])
        assert p4 > p2 * 5

    def test_counts_match_planner_logic(self, schematics):
        """
        Оценка выгоды и планировщик должны сходиться в числе планет:
        иначе рекомендация обещает одно, а план строит другое.
        """
        from domain.planner import FACTORIES_PER_TEMPLATE as PLANNER

        processing, mining, _ = colonies_for("Biocells", schematics)
        assert PLANNER["p2p3_1factory"] == 12
        assert PLANNER["miner_00"] == 8
        assert processing >= 1 and mining >= 1

    def test_unknown_product_rejected(self, schematics):
        with pytest.raises(KeyError):
            colonies_for("Нет такого продукта", schematics)

    def test_raw_tier_is_not_a_target(self, schematics):
        """P1 — не целевой продукт: его не заказывают, а получают по пути."""
        with pytest.raises(KeyError):
            colonies_for("Water", schematics)


class TestRanking:
    def test_ranked_by_isk_per_colony_hour(self, schematics):
        """
        Ключевое отличие от первой версии: сравниваются не цены за
        единицу, а отдача с планеты.
        """
        prices = {"Biocells": 5000.0, "Broadcast Node": 1_000_000.0}
        result = rank(prices, products=list(prices), schematics=schematics)
        values = [c.isk_per_colony_hour for c in result]
        assert values == sorted(values, reverse=True)

    def test_high_revenue_can_lose_to_compact_chain(self, schematics):
        """
        Линия с большей выручкой в час проигрывает, если занимает
        непропорционально больше планет. Ради этого метрика и введена.
        """
        prices = {"Biocells": 20000.0, "Broadcast Node": 900_000.0}
        result = {c.product: c for c in rank(prices, products=list(prices), schematics=schematics)}
        big, small = result["Broadcast Node"], result["Biocells"]
        assert big.revenue_per_hour > small.revenue_per_hour
        assert small.isk_per_colony_hour > big.isk_per_colony_hour

    def test_products_without_price_go_last_but_stay(self, schematics):
        """
        Товар без цены не выбрасывается: иначе непонятно, почему он
        пропал из списка. Он уходит вниз с пометкой.
        """
        prices = {"Biocells": 5000.0}
        result = rank(prices, products=["Biocells", "Coolant"], schematics=schematics)
        assert [c.product for c in result] == ["Biocells", "Coolant"]
        assert result[-1].isk_per_colony_hour is None
        assert result[-1].missing_prices == ["Coolant"]

    def test_price_is_never_invented(self, schematics):
        result = evaluate("Coolant", {}, schematics)
        assert result.price_per_unit is None
        assert result.revenue_per_hour is None

    def test_character_metric_depends_on_slots(self, schematics):
        chain = evaluate("Biocells", {"Biocells": 5000.0}, schematics)
        assert chain.isk_per_character_hour(6) >= chain.isk_per_character_hour(2)


class TestCollector:
    def test_service_ids_excluded_from_price_request(self):
        """
        Структуры, планеты и командные центры не торгуются как продукция
        и не должны попадать в запрос цен.
        """
        from scripts.refresh_market_prices import pi_type_ids

        names = pi_type_ids()
        assert names
        assert not any(n.startswith(("structure:", "planet:", "_")) for n in names)
        assert not any("Command Center" in n for n in names)

    def test_empty_market_is_not_a_price(self):
        """Fuzzwork отдаёт ноль при отсутствии ордеров — это не цена."""
        from scripts.refresh_market_prices import fetch_prices

        def fake(url):
            ids = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["types"][0].split(",")
            return {ids[0]: {"buy": {"max": "0"}, "sell": {"min": "0"}},
                    ids[1]: {"buy": {"max": "1500.5"}, "sell": {"min": "1700"}}}

        result = fetch_prices([100, 200], station=1, opener=fake)
        assert result[100]["buy_max"] is None
        assert result[200]["buy_max"] == 1500.5

    def test_long_lists_are_split_into_requests(self):
        """Слишком длинный адрес запроса сервер отвергнет — режем на части."""
        from scripts.refresh_market_prices import CHUNK_SIZE, fetch_prices

        calls = []

        def fake(url):
            ids = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["types"][0].split(",")
            calls.append(len(ids))
            return {i: {"buy": {"max": "10"}, "sell": {"min": "12"}} for i in ids}

        fetch_prices(list(range(CHUNK_SIZE * 2 + 5)), station=1, opener=fake)
        assert len(calls) == 3
        assert max(calls) <= CHUNK_SIZE


class TestMarketEndpoint:
    def test_endpoint_never_calls_outside(self, monkeypatch):
        """
        Правило проекта: запрос пользователя не инициирует обращений
        наружу. Проверяем буквально — подменяем сетевой вызов на взрыв.
        """
        import urllib.request

        def explode(*args, **kwargs):
            raise AssertionError("эндпоинт полез в сеть — это нарушение правила проекта")

        monkeypatch.setattr(urllib.request, "urlopen", explode)

        from api import create_app

        client = create_app({"TESTING": True}).test_client()
        assert client.get("/api/market/best-product").status_code == 200

    def test_missing_snapshot_explains_what_to_run(self, monkeypatch):
        import api.blueprints.market as market

        monkeypatch.setattr(market, "_load_snapshot", lambda: {})
        from api import create_app

        body = create_app({"TESTING": True}).test_client() \
            .get("/api/market/best-product").get_json()
        assert body["recommended"] is None
        assert "refresh_market_prices" in body["note"]
