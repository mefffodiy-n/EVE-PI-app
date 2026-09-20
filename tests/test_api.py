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
    {"Constellation": "ALPHA", "System": "HOME", "Planet": "4", "Type": "Barren", RADIUS_COLUMN: 5820,
     "POCO Tax Rate [%]": 3.0},
    {"Constellation": "ALPHA", "System": "HOME", "Planet": "7", "Type": "Temperate", RADIUS_COLUMN: 8100,
     "POCO Tax Rate [%]": 1.0},
    {"Constellation": "BETA", "System": "FAR", "Planet": "1", "Type": "Barren", RADIUS_COLUMN: 18000,
     "POCO Tax Rate [%]": 1.0},
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

    def test_initial_data_includes_schematic_quantities(self, client):
        """
        simulateColonyFactories() (web/index.html) считает по этим
        количествам, сколько фабрика потребляет/производит за цикл —
        keyed по type_id ПРОДУКТА (см. scripts/extract_schematics.py).
        """
        schematics = client.get("/api/initial-data").get_json()["schematics"]
        plasmoids = schematics.get("2389")
        assert plasmoids is not None
        assert plasmoids["output_qty"] == 20
        assert plasmoids["inputs"] == {"2308": 3000}

    def test_initial_data_only_lists_processing_tiers(self, client):
        """В выдаче только P2-P4: P1 и сырьё пользователь не выбирает как цель."""
        products = client.get("/api/initial-data").get_json()["products"]
        assert "Biocells" in products          # P2
        assert "Broadcast Node" in products    # P4
        assert "Water" not in products         # P1

    def test_initial_data_includes_product_tiers(self, client):
        """
        17.09.2026, Фаза 11: тир каждого продукта — фронтенду для кнопок-
        фильтров P2/P3/P4 над списком продуктов (web/index.html,
        renderTierFilter/renderProducts).
        """
        tiers = client.get("/api/initial-data").get_json()["product_tiers"]
        assert tiers["Biocells"] == "P2"
        assert tiers["Broadcast Node"] == "P4"
        assert "Water" not in tiers  # P1 не входит в целевые продукты вовсе

    def test_initial_data_includes_type_volumes(self, client, monkeypatch, tmp_path):
        """
        Объём одной единицы товара по type_id — тем же кэшем, которым
        сервер считает used_m3 из реального снимка (scripts/
        sync_colony_status.py::type_volume()). Нужен фронту, чтобы
        досчитывать вперёд занятость причала в панели «Детали»
        (simulateColonyFactories(), web/index.html) — состав со временем
        сдвигается от сырья к готовой продукции, а объём на единицу у
        них обычно разный (17.09.2026).
        """
        import json

        import api.blueprints.reference as reference

        cache_file = tmp_path / "type_volumes.json"
        cache_file.write_text(json.dumps({"2401": 0.38, "9828": 0.15}), encoding="utf-8")
        monkeypatch.setattr(reference, "TYPE_VOLUMES_PATH", cache_file)
        reference._type_volumes.cache_clear()
        reference._initial_payload.cache_clear()

        body = client.get("/api/initial-data").get_json()
        assert body["type_volumes"] == {"2401": 0.38, "9828": 0.15}

        reference._type_volumes.cache_clear()

    def test_initial_data_type_volumes_empty_without_cache_file(self, client, monkeypatch, tmp_path):
        """Файл кэша ещё не создан (сборщик ни разу не запускался) — честно пусто, не ошибка."""
        import api.blueprints.reference as reference

        monkeypatch.setattr(reference, "TYPE_VOLUMES_PATH", tmp_path / "missing.json")
        reference._type_volumes.cache_clear()
        reference._initial_payload.cache_clear()

        body = client.get("/api/initial-data").get_json()
        assert body["type_volumes"] == {}

        reference._type_volumes.cache_clear()

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

    def test_calculate_purchase_p1_builds_no_mining_rows(self, client, seeded_characters, monkeypatch):
        """
        18.09.2026, по прямому запросу пользователя: purchase_p1=true —
        план строит только переработку, весь P1 считается закупленным.
        Констелляции по-прежнему нужны непустыми — фронтенд в этом режиме
        сам подставляет все констелляции региона (togglePurchaseP1(),
        web/index.html), сервер требование не ослабляет.

        HOME/ALPHA — не настоящая система Fountain (та же синтетическая
        планета, что и в tests/test_planner.py), поэтому domain.planets.load_planets
        подменяется прямо в api.blueprints.plans — /api/calculate читает
        его напрямую, не через api.blueprints.reference (тот мокает
        фикстура client, но это другой импорт того же имени).
        """
        import api.blueprints.plans as plans

        book = PlanetBook(pd.DataFrame([
            {"Constellation": "ALPHA", "System": "HOME", "Planet": "4", "Type": "Barren",
             RADIUS_COLUMN: 5820},
            {"Constellation": "ALPHA", "System": "HOME", "Planet": "7", "Type": "Temperate",
             RADIUS_COLUMN: 8100},
        ]))
        monkeypatch.setattr(plans, "load_planets", lambda: book)

        response = client.post(
            "/api/calculate",
            json={
                "constellations": ["ALPHA"],
                "factory_sys": "HOME",
                "target_products": ["Biocells"],
                "purchase_p1": True,
            },
        )
        body = response.get_json()
        assert body["status"] == "success" and body["data"], body
        assert all("Добыча" not in row["role"] for row in body["data"])
        assert body["purchased_p1"], "P1 должен быть учтён как закупленный, не пропасть молча"

    def test_calculate_without_purchase_p1_defaults_to_normal_plan(self, client):
        """purchase_p1 отсутствует в теле запроса — как и раньше, поле не влияет."""
        response = client.post(
            "/api/calculate",
            json={"constellations": ["ALPHA"], "factory_sys": "HOME", "target_products": ["Biocells"]},
        )
        assert response.get_json()["purchased_p1"] == {}

    def test_calculate_includes_duty_cycles(self, client, monkeypatch):
        """
        20.09.2026, по прямому запросу пользователя: пропускная
        способность причала — ответ /api/calculate обязан нести
        duty_cycles/missing_volumes, иначе фронтенду/`/api/plan-
        profitability` неоткуда их взять (тот же путь, что purchased_p1).
        """
        import api.blueprints.plans as plans

        book = PlanetBook(pd.DataFrame([
            {"Constellation": "ALPHA", "System": "HOME", "Planet": "4", "Type": "Barren",
             RADIUS_COLUMN: 5820},
            {"Constellation": "ALPHA", "System": "HOME", "Planet": "7", "Type": "Temperate",
             RADIUS_COLUMN: 8100},
        ]))
        monkeypatch.setattr(plans, "load_planets", lambda: book)

        response = client.post(
            "/api/calculate",
            json={"constellations": ["ALPHA"], "factory_sys": "HOME", "target_products": ["Biocells"]},
        )
        body = response.get_json()
        assert body["status"] == "success", body
        assert "duty_cycles" in body
        assert "missing_volumes" in body
        assert isinstance(body["duty_cycles"], dict)
        assert isinstance(body["missing_volumes"], list)

    def test_saved_plan_round_trips_purchased_p1(self, client):
        """
        20.09.2026, по прямому запросу пользователя: план на закупаемом
        P1 при повторном открытии должен снова показывать список
        закупки сырья — для этого purchased_p1 сохраняется вместе с
        планом (не только в теле /api/calculate, откуда он временный).
        """
        saved = client.post("/api/plans", json={
            "name": "Закупка", "rows": [SAMPLE_PLAN_ROW],
            "purchased_p1": {"Water": 600.0},
        })
        assert saved.status_code == 200
        plan_id = saved.get_json()["plan"]["id"]

        loaded = client.get(f"/api/plans/{plan_id}").get_json()["plan"]
        assert loaded["purchased_p1"] == {"Water": 600.0}

    def test_saved_plan_without_purchase_p1_reports_empty_dict(self, client):
        saved = client.post("/api/plans", json={"name": "Обычный", "rows": [SAMPLE_PLAN_ROW]})
        plan_id = saved.get_json()["plan"]["id"]
        loaded = client.get(f"/api/plans/{plan_id}").get_json()["plan"]
        assert loaded["purchased_p1"] == {}

    def test_saved_plan_round_trips_profitability_inputs(self, client):
        """
        21.09.2026, по прямому запросу пользователя: при повторном
        открытии плана прогноз прибыльности "тупел" до полной загрузки
        без поправок причала — duty_cycles/missing_volumes/revenue_share
        не сохранялись вместе с планом (только purchased_p1). Тот же
        путь, что purchased_p1 (test_saved_plan_round_trips_purchased_p1).
        """
        saved = client.post("/api/plans", json={
            "name": "С поправками", "rows": [SAMPLE_PLAN_ROW],
            "duty_cycles": {"Coolant": 0.42},
            "missing_volumes": ["Water"],
            "revenue_share": {"Coolant": 0.5},
        })
        assert saved.status_code == 200
        plan_id = saved.get_json()["plan"]["id"]

        loaded = client.get(f"/api/plans/{plan_id}").get_json()["plan"]
        assert loaded["duty_cycles"] == {"Coolant": 0.42}
        assert loaded["missing_volumes"] == ["Water"]
        assert loaded["revenue_share"] == {"Coolant": 0.5}

    def test_saved_plan_without_profitability_inputs_reports_empty(self, client):
        saved = client.post("/api/plans", json={"name": "Обычный", "rows": [SAMPLE_PLAN_ROW]})
        plan_id = saved.get_json()["plan"]["id"]
        loaded = client.get(f"/api/plans/{plan_id}").get_json()["plan"]
        assert loaded["duty_cycles"] == {}
        assert loaded["missing_volumes"] == []
        assert loaded["revenue_share"] == {}

    def test_advice_purchase_p1_excludes_mining_from_needed_colonies(self, client, seeded_characters):
        """
        18.09.2026, найдено пользователем: /api/advice считал добывающие
        колонии даже для режима, где build_plan() их не строит — подсказка
        «персонажей больше, чем нужно» переставала предлагать продукты
        раньше, чем реальный пул заполнялся. Числа сверены в tests/test_advice.py
        — здесь только HTTP-контракт (поле доходит до domain.advice.advise()).
        """
        normal = client.post(
            "/api/advice", json={"target_products": ["Biocells"]}
        ).get_json()
        purchasing = client.post(
            "/api/advice", json={"target_products": ["Biocells"], "purchase_p1": True}
        ).get_json()
        assert normal["needed_mining"] > 0
        assert purchasing["needed_mining"] == 0
        assert purchasing["needed_colonies"] < normal["needed_colonies"]

    def test_calculate_lines_per_target_multiplies_colonies(self, client, seeded_characters, monkeypatch):
        """
        18.09.2026, по прямому запросу пользователя: продублировать
        выбранную цепочку столько раз, сколько позволят персонажи, а не
        подбирать второй-третий отдельный продукт вручную.
        """
        import api.blueprints.plans as plans

        book = PlanetBook(pd.DataFrame([
            {"Constellation": "ALPHA", "System": "HOME", "Planet": "4", "Type": "Barren",
             RADIUS_COLUMN: 5820},
            {"Constellation": "ALPHA", "System": "HOME", "Planet": "7", "Type": "Temperate",
             RADIUS_COLUMN: 8100},
        ]))
        monkeypatch.setattr(plans, "load_planets", lambda: book)

        base_payload = {
            "constellations": ["ALPHA"], "factory_sys": "HOME",
            "target_products": ["Biocells"], "purchase_p1": True,
        }
        one_line = client.post("/api/calculate", json=base_payload).get_json()
        three_lines = client.post(
            "/api/calculate", json={**base_payload, "lines_per_target": 3}
        ).get_json()
        assert one_line["status"] == "success" and one_line["data"]
        assert three_lines["status"] == "success" and three_lines["data"]
        assert len(three_lines["data"]) > len(one_line["data"])

    def test_calculate_lines_per_target_bad_value_defaults_to_one(self, client):
        """Нечисловое значение не должно ронять эндпоинт — тихо как 1."""
        response = client.post(
            "/api/calculate",
            json={
                "constellations": ["ALPHA"], "factory_sys": "HOME",
                "target_products": ["Biocells"], "lines_per_target": "abc",
            },
        )
        assert response.status_code == 200

    def test_advice_reports_extra_lines_available(self, client, seeded_characters):
        """
        HTTP-контракт: поле extra_lines_available доходит из
        domain.advice.advise() — числа сверены в tests/test_advice.py.
        """
        body = client.post("/api/advice", json={"target_products": ["Biocells"]}).get_json()
        assert "extra_lines_available" in body

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
    "cpu_percent": 39.2, "pg_percent": 95.1, "poco_rate": 0.03,
}


class TestPlansIsolation:
    """
    Найдено 11.09.2026: /api/plans отдавал сохранённые планы кому угодно
    без учёта того, кто вошёл — та же ошибка, что и с /api/characters
    (см. TestLoad.test_prod_loads_real_rows_of_matching_account_only в
    tests/test_db.py), только на уровне HTTP, а не domain-функции
    напрямую: проверяем, что сама сессия (api/session.py) действительно
    разделяет запросы, а не только что domain/plan_storage.py умеет
    фильтровать при вызове с нужным account_id.
    """

    def _login_as(self, client, account_id: str) -> None:
        with client.session_transaction() as sess:
            sess["account_id"] = account_id

    def _save(self, client, name: str = "План"):
        return client.post("/api/plans", json={"name": name, "rows": [SAMPLE_PLAN_ROW]})

    def test_two_accounts_do_not_see_each_others_plans(self, client, monkeypatch):
        from infra import config

        monkeypatch.setattr(config, "IS_DEV", False)

        self._login_as(client, "acct-a")
        saved = self._save(client, "План A")
        assert saved.status_code == 200
        plan_id = saved.get_json()["plan"]["id"]

        listed = client.get("/api/plans").get_json()["plans"]
        assert [p["name"] for p in listed] == ["План A"]

        self._login_as(client, "acct-b")
        assert client.get("/api/plans").get_json()["plans"] == []
        assert client.get(f"/api/plans/{plan_id}").status_code == 404
        assert client.delete(f"/api/plans/{plan_id}").status_code == 404

        self._login_as(client, "acct-a")
        assert client.get(f"/api/plans/{plan_id}").status_code == 200

    def test_anonymous_prod_visit_cannot_save_or_list(self, client, monkeypatch):
        from infra import config

        monkeypatch.setattr(config, "IS_DEV", False)

        assert self._save(client).status_code == 400
        assert client.get("/api/plans").get_json()["plans"] == []

    def test_calculate_cache_does_not_leak_between_accounts(self, client, monkeypatch):
        """
        cache_key() теперь включает account_id (см. api/blueprints/plans.py) —
        до 11.09.2026 два разных пользователя с одинаковыми параметрами
        запроса могли получить план ОДНОГО из них из общего кэша, потому
        что ключ строился только из параметров расчёта, не из того, кто
        спрашивает. Только acct-a имеет персонажа — если бы кэш не различал
        аккаунты, второй (пустой) запрос от acct-a мог бы «прилипнуть» к
        acct-b и наоборот при повторных вызовах.
        """
        from infra import config
        from infra.db import session_scope
        from infra.models import Character

        with session_scope() as session:
            session.add(Character(
                character_id=95001, name="Only Acct A",
                command_center_upgrades_level=5, interplanetary_consolidation_level=5,
                source="esi", account_id="acct-a",
            ))

        monkeypatch.setattr(config, "IS_DEV", False)
        payload = {
            "constellations": ["ALPHA"], "factory_sys": "HOME",
            "target_products": ["Biocells"],
        }

        self._login_as(client, "acct-a")
        with_char = client.post("/api/calculate", json=payload).get_json()
        assert "Нет персонажей" not in (with_char.get("warning") or "")

        self._login_as(client, "acct-b")
        without_char = client.post("/api/calculate", json=payload).get_json()
        assert without_char.get("warning") and "Нет персонажей" in without_char["warning"]

        # Повторный запрос acct-a не должен вернуть закэшированный
        # результат acct-b (пустой план из-за отсутствия персонажей).
        self._login_as(client, "acct-a")
        with_char_again = client.post("/api/calculate", json=payload).get_json()
        assert "Нет персонажей" not in (with_char_again.get("warning") or "")


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

    def test_export_adds_purchase_sheet_when_purchase_items_present(self, client):
        """
        19.09.2026, по прямому запросу пользователя: список закупки P1
        (те же данные, что уже показаны на панели «Список закупки
        сырья») попадает в экспорт отдельным листом — только когда
        фронтенд его прислал, обычный план (без purchase_p1) не должен
        обзаводиться пустым лишним листом.
        """
        from io import BytesIO

        from openpyxl import load_workbook

        response = client.post("/api/export", json={
            "plan_data": [SAMPLE_PLAN_ROW],
            "purchase_items": [
                {"product": "Water", "monthly_qty": 432000.0, "price": 10.0, "monthly_cost": 4320000.0},
                {"product": "Oxygen", "monthly_qty": 216000.0, "price": None, "monthly_cost": None},
            ],
            "prices_collected_at": "2026-09-19T00:00:00+00:00",
        })
        workbook = load_workbook(BytesIO(response.data))
        assert "Закупка P1" in workbook.sheetnames
        sheet = workbook["Закупка P1"]
        assert sheet["A3"].value == "Water"
        assert sheet["B3"].value == 432000.0
        assert sheet["C3"].value == 10.0
        assert sheet["D3"].value == 4320000.0
        assert sheet["A4"].value == "Oxygen"
        assert sheet["C4"].value == "нет цены"
        assert sheet["D4"].value is None
        # Итоговая строка — только по продуктам с известной ценой.
        assert sheet["D5"].value == 4320000.0
        assert "2026-09-19T00:00:00+00:00" in str(sheet["A7"].value)

    def test_export_skips_purchase_sheet_when_no_purchase_items(self, client):
        from io import BytesIO

        from openpyxl import load_workbook

        response = client.post("/api/export", json={"plan_data": [SAMPLE_PLAN_ROW]})
        workbook = load_workbook(BytesIO(response.data))
        assert "Закупка P1" not in workbook.sheetnames

    def test_summary_uses_formulas_not_precomputed_values(self, client):
        """
        Сводка должна пересчитываться, если пользователь правит лист
        «План» вручную, поэтому там формулы, а не числа из Python.
        Данные начинаются со строки 3 — строка 1 заголовок блока, строка
        2 шапка таблицы (см. test_summary_has_explicit_command_center_block).
        """
        from io import BytesIO

        from openpyxl import load_workbook

        response = client.post("/api/export", json={"plan_data": [SAMPLE_PLAN_ROW]})
        workbook = load_workbook(BytesIO(response.data))
        assert str(workbook["Сводка"]["B3"].value).startswith("=COUNTIF")
        assert str(workbook["Проверка"]["F2"].value).startswith("=100-")

    def test_summary_has_explicit_command_center_block(self, client):
        """
        17.09.2026, по прямому запросу пользователя: раньше сводка была
        просто «тип планеты + количество», командный центр подразумевался
        неявно. Теперь — явный заголовок блока и отдельная колонка с
        названием командного центра, тем же смыслом, что и «Список
        закупки» в вебе (renderShopping()).
        """
        from io import BytesIO

        from openpyxl import load_workbook

        response = client.post("/api/export", json={"plan_data": [SAMPLE_PLAN_ROW]})
        workbook = load_workbook(BytesIO(response.data))
        summary = workbook["Сводка"]
        assert "командные центры" in str(summary["A1"].value).lower()
        assert summary["C2"].value == "Командный центр"
        assert summary["C3"].value == '=A3&" Command Center"'
        assert "командных центров" in str(summary["A4"].value).lower()  # итоговая строка

    def test_plan_sheet_includes_poco_rate_column(self, client):
        """
        17.09.2026, по прямому запросу пользователя: колонка с налогом
        POCO рядом с CPU/PG — доля хранится как есть (0.03), Excel сам
        показывает её процентом через формат ячейки.
        """
        from io import BytesIO

        from openpyxl import load_workbook

        response = client.post("/api/export", json={"plan_data": [SAMPLE_PLAN_ROW]})
        workbook = load_workbook(BytesIO(response.data))
        poco_cell = workbook["План"]["O2"]  # 15-я колонка, после pg_percent
        assert poco_cell.value == 0.03
        assert poco_cell.number_format == "0.0%"

    def test_plan_sheet_shows_planet_number_as_roman_numeral(self, client):
        """
        18.09.2026, найдено пользователем: план показывал номер планеты
        как «AV-VB6 16.0» — CSV-колонка «Planet» читается pandas float64,
        дробь никуда не девалась при сериализации PlanRow. Просьба
        пользователя была не просто убрать «.0», а показывать номер, как
        в самой игре — римской цифрой («4» -> «IV»). Тот же формат
        проверяется на листе «Проверка» (chk_planet), не только «План».
        """
        from io import BytesIO

        from openpyxl import load_workbook

        response = client.post("/api/export", json={"plan_data": [SAMPLE_PLAN_ROW]})
        workbook = load_workbook(BytesIO(response.data))
        assert workbook["План"]["E2"].value == "IV"
        assert workbook["Проверка"]["B2"].value == "IV"

    def test_overloaded_planets_are_highlighted(self, client):
        """Планеты с загрузкой от 90% подсвечиваются — они сломаются первыми."""
        from io import BytesIO

        from openpyxl import load_workbook

        response = client.post("/api/export", json={"plan_data": [SAMPLE_PLAN_ROW]})
        workbook = load_workbook(BytesIO(response.data))
        pg_cell = workbook["План"]["N2"]  # pg_percent = 95.1
        assert pg_cell.fill.start_color.rgb == "00FFF2CC"


class TestExportColonies:
    """
    18.09.2026, по прямому запросу пользователя: «раз можем
    экспортировать план, почему не колонии» — /api/export принимает и
    colonies_data, тем же форматом колонок, что и план.
    """

    SAMPLE_COLONY = {
        "character": "Chief 1", "character_id": 90001, "planet_id": 111,
        "planet_name": "HOME IV", "system_name": "HOME", "planet_index": 4,
        "planet_type": "barren", "upgrade_level": 5, "num_pins": 3,
        "structures": [], "routes": [],
        "pins": [
            {"pin_id": 1, "kind": "advanced_industry_facility",
             "product": "Biocells", "cycle_minutes": 60, "last_cycle_start": None},
        ],
        "cpu_percent": 39.2, "pg_percent": 95.1,
        "cpu_used": None, "cpu_capacity": None, "pg_used": None, "pg_capacity": None,
        "nearest_expiry": None, "synced_at": None, "game_last_update": None,
    }

    def _mock_planets(self, monkeypatch):
        """
        `export.py::_load_planets_and_recipes()` импортирует
        `load_planets` заново при каждом вызове — патчим сам модуль
        `domain.planets`, а не `reference.load_planets` (тот патчит
        только имя внутри api.blueprints.reference, см. фикстуру client).

        Planet — числом (не строкой, как в общем SAMPLE_PLANETS): у
        настоящих колоний planet_index приходит от ESI числом, а
        PlanetBook.radius_km()/poco_rate() сравнивают через float() —
        строковая колонка не совпала бы ни с одним числом.
        """
        book = PlanetBook(pd.DataFrame([
            {"Constellation": "ALPHA", "System": "HOME", "Planet": 4, "Type": "Barren",
             RADIUS_COLUMN: 5820, "POCO Tax Rate [%]": 3.0},
        ]))
        monkeypatch.setattr("domain.planets.load_planets", lambda: book)

    def test_colonies_only_produces_single_sheet(self, client, monkeypatch):
        from io import BytesIO

        from openpyxl import load_workbook

        self._mock_planets(monkeypatch)
        r = client.post("/api/export", json={"colonies_data": [self.SAMPLE_COLONY]})
        assert r.status_code == 200
        workbook = load_workbook(BytesIO(r.data))
        # Ни "Сводки", ни "Проверки" — командные центры уже куплены,
        # решение пользователя.
        assert workbook.sheetnames == ["Мои колонии"]

    def test_colony_row_maps_real_data_to_plan_columns(self, client, monkeypatch):
        from io import BytesIO

        from openpyxl import load_workbook

        self._mock_planets(monkeypatch)
        r = client.post("/api/export", json={"colonies_data": [self.SAMPLE_COLONY]})
        sheet = load_workbook(BytesIO(r.data))["Мои колонии"]
        assert sheet["A2"].value == "Chief 1"
        assert sheet["B2"].value == "Переработка"
        assert sheet["C2"].value == "ALPHA"  # 18.09.2026: раньше оставалась пустой
        assert sheet["D2"].value == "HOME"
        assert sheet["E2"].value == "IV"  # 18.09.2026: римская цифра, как в игре, не «4.0»/«4»
        assert sheet["F2"].value == "Barren"  # ESI отдаёт строчными, здесь — с заглавной
        assert sheet["G2"].value == 5820  # радиус из того же файла, что и у плана
        assert sheet["H2"].value == "1x Barren Command Center"
        assert sheet["I2"].value == "Biofuels, Precious Metals"
        assert sheet["J2"].value == "Biocells"
        assert sheet["O2"].value == 0.03  # ставка POCO той же планеты

    def test_combined_colony_is_classified_as_mining_not_processing(self, client, monkeypatch):
        """
        18.09.2026, найдено пользователем на реальном экспорте: колония
        с экстрактором И фабриками на одной планете (обычная оптимизация —
        перерабатывать сырьё сразу на месте добычи, не вывозя P1)
        показывала «Переработка» на КАЖДОЙ такой колонии. Экстрактор
        должен решать роль первым — та же приоритетность, что уже
        использует realRows() (web/index.html) для карточек на дашборде.

        Вход/выход при этом смотрят не на роль, а на факт переработки:
        18.09.2026, тот же отчёт сразу ПОСЛЕ фикса роли — «Добыча» стала
        показываться верно, но «Выход» оставался сырым продуктом
        экстрактора (Precious Metals), а «Вход» — пустым, хотя колония
        реально производит P1 и известно, из чего. Комбинированная
        колония должна показывать то, что она реально выпускает: выход —
        продукт фабрики, вход — сырьё этой же планеты (продукт
        экстрактора), а не Recipe.source (тот годится только для
        привозного сырья, здесь оно своё).
        """
        from io import BytesIO

        from openpyxl import load_workbook

        self._mock_planets(monkeypatch)
        combined = {
            **self.SAMPLE_COLONY,
            "pins": [
                {"pin_id": 1, "kind": "advanced_industry_facility",
                 "product": "Biocells", "cycle_minutes": 60, "last_cycle_start": None},
                {"pin_id": 2, "kind": "extractor_control_unit",
                 "product": "Precious Metals", "heads": 8, "expiry_time": None},
            ],
        }
        r = client.post("/api/export", json={"colonies_data": [combined]})
        sheet = load_workbook(BytesIO(r.data))["Мои колонии"]
        assert sheet["B2"].value == "Добыча"
        assert sheet["J2"].value == "Biocells"  # реальный выход колонии — продукт фабрики
        assert sheet["I2"].value == "Precious Metals"  # сырьё с этой же планеты

    def test_pure_mining_colony_without_factories_reports_raw_output(self, client, monkeypatch):
        """
        Без фабрик на планете вход/выход остаются как раньше: выход —
        сырой продукт экстрактора, входа нет (нечего перерабатывать на
        месте).
        """
        from io import BytesIO

        from openpyxl import load_workbook

        self._mock_planets(monkeypatch)
        pure_mining = {
            **self.SAMPLE_COLONY,
            "pins": [
                {"pin_id": 1, "kind": "extractor_control_unit",
                 "product": "Precious Metals", "heads": 8, "expiry_time": None},
            ],
        }
        r = client.post("/api/export", json={"colonies_data": [pure_mining]})
        sheet = load_workbook(BytesIO(r.data))["Мои колонии"]
        assert sheet["B2"].value == "Добыча"
        assert sheet["J2"].value == "Precious Metals"
        assert sheet["I2"].value is None
        assert sheet["K2"].value == "8 фабрик"  # heads экстрактора, не 1 пин

    def test_factory_input_resolved_by_name_not_left_as_raw_type_id(self, client, monkeypatch):
        """
        18.09.2026, найдено пользователем на реальном экспорте: колонка
        «Вход» показывала "type_id:2307" вместо "Felsic Magma" —
        data/schematics.json резолвит входы только по type_ids.json, а
        там нет сырья R0 (не структура и не продукт с иконкой).
        domain.recipes.py (Recipe.source, проверено по источникам —
        правило 2) знает сырьё P1-рецепта всегда.
        """
        from io import BytesIO

        from openpyxl import load_workbook

        self._mock_planets(monkeypatch)
        p1_colony = {
            **self.SAMPLE_COLONY,
            "pins": [{"pin_id": 1, "kind": "basic_industry_facility",
                      "product": "Silicon", "cycle_minutes": 30, "last_cycle_start": None}],
        }
        r = client.post("/api/export", json={"colonies_data": [p1_colony]})
        sheet = load_workbook(BytesIO(r.data))["Мои колонии"]
        assert sheet["I2"].value == "Felsic Magma"
        assert "type_id" not in str(sheet["I2"].value)

    def test_idle_factory_still_reports_its_assigned_product(self, client, monkeypatch):
        """
        Простаивающая фабрика (цикл истёк давным-давно) по-прежнему
        показывает назначенный продукт — ESI не стирает schematic_id,
        когда истекает таймер, это не одно и то же (тот же факт, на
        котором построен pinCycleInfo() во фронтенде).
        """
        from io import BytesIO

        from openpyxl import load_workbook

        self._mock_planets(monkeypatch)
        idle_colony = {
            **self.SAMPLE_COLONY,
            "pins": [{
                "pin_id": 1, "kind": "advanced_industry_facility",
                "product": "Biocells", "cycle_minutes": 60,
                "last_cycle_start": "2020-01-01T00:00:00Z",
            }],
        }
        r = client.post("/api/export", json={"colonies_data": [idle_colony]})
        sheet = load_workbook(BytesIO(r.data))["Мои колонии"]
        assert sheet["J2"].value == "Biocells"

    def test_plan_and_colonies_together_produce_four_sheets(self, client, monkeypatch):
        from io import BytesIO

        from openpyxl import load_workbook

        self._mock_planets(monkeypatch)
        r = client.post("/api/export", json={
            "plan_data": [SAMPLE_PLAN_ROW], "colonies_data": [self.SAMPLE_COLONY],
        })
        workbook = load_workbook(BytesIO(r.data))
        assert workbook.sheetnames == ["План", "Сводка", "Проверка", "Мои колонии"]

    def test_rejects_when_neither_plan_nor_colonies_given(self, client):
        r = client.post("/api/export", json={"plan_data": [], "colonies_data": []})
        assert r.status_code == 400

    def test_rejects_oversized_colonies_list(self, client):
        r = client.post("/api/export", json={"colonies_data": [{}] * 501})
        assert r.status_code == 400

    def test_mining_colony_role_and_extractor_product(self, client, monkeypatch):
        self._mock_planets(monkeypatch)
        mining_colony = {
            **self.SAMPLE_COLONY,
            "pins": [{
                "pin_id": 2, "kind": "extractor_control_unit",
                "product": "Base Metals", "expiry_time": None,
            }],
        }
        from io import BytesIO

        from openpyxl import load_workbook

        r = client.post("/api/export", json={"colonies_data": [mining_colony]})
        sheet = load_workbook(BytesIO(r.data))["Мои колонии"]
        assert sheet["B2"].value == "Добыча"
        assert sheet["J2"].value == "Base Metals"
        assert sheet["I2"].value is None  # у добычи нет "входа" по определению


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


class TestColoniesIsolation:
    """
    /api/colonies (api/blueprints/meta.py::colonies()) фильтрует через тот
    же load_characters(current_account_id()), что и персонажи и планы
    (уже проверенные примитивы, см. test_db.py::TestLoad и
    TestPlansIsolation выше) — гарантия логически та же, но отдельного
    HTTP-теста на два разных account_id именно для этого эндпоинта не
    было (docs/ROADMAP.md, Фаза 9, 16.09.2026): TestColonies проверяет
    сортировку и историю добычи, не многопользовательскую изоляцию.
    """

    def _login_as(self, client, account_id: str) -> None:
        with client.session_transaction() as sess:
            sess["account_id"] = account_id

    def test_two_accounts_do_not_see_each_others_colonies(self, client, monkeypatch):
        from infra import config
        from infra.db import session_scope
        from infra.models import Character, Colony

        monkeypatch.setattr(config, "IS_DEV", False)

        with session_scope() as s:
            s.add(Character(character_id=201, name="Pilot A", command_center_upgrades_level=5,
                             interplanetary_consolidation_level=5, source="esi", account_id="acct-a"))
            s.add(Character(character_id=202, name="Pilot B", command_center_upgrades_level=5,
                             interplanetary_consolidation_level=5, source="esi", account_id="acct-b"))
            s.add(Colony(character_id=201, planet_id=910, planet_name="A II", system_name="A",
                         planet_index=2, planet_type="barren", upgrade_level=4, num_pins=3))
            s.add(Colony(character_id=202, planet_id=920, planet_name="B II", system_name="B",
                         planet_index=2, planet_type="barren", upgrade_level=4, num_pins=3))

        self._login_as(client, "acct-a")
        rows = client.get("/api/colonies").get_json()["colonies"]
        assert [r["planet_name"] for r in rows] == ["A II"]

        self._login_as(client, "acct-b")
        rows = client.get("/api/colonies").get_json()["colonies"]
        assert [r["planet_name"] for r in rows] == ["B II"]

    def test_anonymous_prod_visit_sees_no_colonies(self, client, monkeypatch):
        from infra import config
        from infra.db import session_scope
        from infra.models import Character, Colony

        monkeypatch.setattr(config, "IS_DEV", False)

        with session_scope() as s:
            s.add(Character(character_id=203, name="Pilot C", command_center_upgrades_level=5,
                             interplanetary_consolidation_level=5, source="esi", account_id="acct-c"))
            s.add(Colony(character_id=203, planet_id=930, planet_name="C II", system_name="C",
                         planet_index=2, planet_type="barren", upgrade_level=4, num_pins=3))

        assert client.get("/api/colonies").get_json()["colonies"] == []


class TestPlanProfitability:
    """
    POST /api/plan-profitability (docs/ROADMAP.md, Фаза 9, 16.09.2026) —
    прибыльность УЖЕ ПОСТРОЕННОГО плана с учётом налога POCO (и на
    экспорт, и на импорт — domain/poco_tax.py). Числа те же, что в
    tests/test_poco_tax.py — здесь проверяется HTTP-контракт (валидация,
    снимок цен), не арифметика.
    """

    MINING_ROW = {
        "role_key": "mine", "res_out": "Water", "planet_type": "Barren",
        "structures_detail": [{"kind": "basic_industry_facility", "count": 8}],
        "system": "MINE", "planet": "1",
    }
    PROCESSING_ROW = {
        "role_key": "proc", "res_out": "Coolant", "planet_type": "Temperate",
        "structures_detail": [{"kind": "advanced_industry_facility", "count": 12}],
        "system": "HOME", "planet": "2",
    }

    def _mock_prices(self, monkeypatch, prices: dict[str, float]):
        import api.blueprints.market as market

        monkeypatch.setattr(market, "_load_snapshot", lambda: {
            "prices": {name: {"buy_max": price} for name, price in prices.items()}
        })

    def _mock_planets(self, monkeypatch, rates: dict[tuple[str, str], float]):
        """Ставка POCO — единственный источник теперь: точная ставка планеты из файла."""
        import api.blueprints.plans as plans

        class FakeBook:
            def poco_rate(self, system, planet):
                return rates.get((system, str(planet)))

        monkeypatch.setattr(plans, "_load_planets_book", lambda: FakeBook())

    def _mock_schematics(self, monkeypatch):
        """
        Настоящие Water/Coolant из data/schematics.json — не те числа, на
        которых построены ожидаемые суммы теста (см. tests/test_poco_tax.py) —
        подменяем на контролируемые, чтобы проверять HTTP-контракт, а не
        переоткрывать арифметику, уже покрытую доменными тестами.
        """
        import domain.poco_tax as poco_tax
        from domain.throughput import Schematic

        monkeypatch.setattr(poco_tax, "load_schematics", lambda: {
            "Water": Schematic(product="Water", facility="basic_industry_facility",
                                inputs={}, output_qty=100.0, cycle_minutes=30.0),
            "Coolant": Schematic(product="Coolant", facility="advanced_industry_facility",
                                  inputs={"Water": 50.0}, output_qty=10.0, cycle_minutes=60.0),
        })

    def test_computes_revenue_and_both_tax_directions(self, monkeypatch):
        self._mock_prices(monkeypatch, {"Water": 10.0, "Coolant": 100.0})
        self._mock_schematics(monkeypatch)
        self._mock_planets(monkeypatch, {("MINE", "1"): 0.10, ("HOME", "2"): 0.05})
        from api import create_app

        with create_app({"TESTING": True}).test_client() as client:
            r = client.post("/api/plan-profitability", json={
                "rows": [self.MINING_ROW, self.PROCESSING_ROW],
                "target_products": ["Coolant"],
            })
        assert r.status_code == 200
        body = r.get_json()
        assert body["monthly_revenue"] == 8_640_000.0
        assert body["monthly_export_tax"] == 1_584_000.0
        assert body["monthly_import_tax"] == 216_000.0
        assert body["monthly_net_profit"] == 6_840_000.0
        assert body["hours_per_month"] == 720.0

    def test_duty_cycles_from_request_body_discount_revenue(self, monkeypatch):
        """
        20.09.2026, по прямому запросу пользователя: пропускная
        способность причала передаётся тем же путём, что purchased_p1
        — фронтенд шлёт то, что уже получил в /api/calculate, сервер
        не пересчитывает demand заново. Числа — как в
        tests/test_poco_tax.py::TestDutyCycle (сверены вручную).
        """
        self._mock_prices(monkeypatch, {"Water": 10.0, "Coolant": 100.0})
        self._mock_schematics(monkeypatch)
        self._mock_planets(monkeypatch, {("MINE", "1"): 0.10, ("HOME", "2"): 0.05})
        from api import create_app

        with create_app({"TESTING": True}).test_client() as client:
            r = client.post("/api/plan-profitability", json={
                "rows": [self.MINING_ROW, self.PROCESSING_ROW],
                "target_products": ["Coolant"],
                "duty_cycles": {"Coolant": 0.5},
                "missing_volumes": ["Oxygen"],
            })
        body = r.get_json()
        assert body["monthly_revenue"] == 8_640_000.0 * 0.5
        assert body["monthly_export_tax"] == 1_152_000.0 + 432_000.0 * 0.5
        assert body["monthly_import_tax"] == 216_000.0 * 0.5
        assert body["missing_volumes"] == ["Oxygen"]

    def test_missing_price_reported_not_hidden(self, monkeypatch):
        self._mock_prices(monkeypatch, {"Coolant": 100.0})  # Water без цены
        self._mock_schematics(monkeypatch)
        self._mock_planets(monkeypatch, {("MINE", "1"): 0.10, ("HOME", "2"): 0.05})
        from api import create_app

        with create_app({"TESTING": True}).test_client() as client:
            r = client.post("/api/plan-profitability", json={
                "rows": [self.MINING_ROW, self.PROCESSING_ROW],
                "target_products": ["Coolant"],
            })
        body = r.get_json()
        assert "Water" in body["missing_prices"]
        assert body["monthly_net_profit"] is None  # картина неполная — не выдаём частичную

    def test_works_without_market_snapshot(self, monkeypatch):
        """Правило 3: эндпоинт не ходит наружу и отвечает даже без снимка цен."""
        import api.blueprints.market as market

        monkeypatch.setattr(market, "_load_snapshot", lambda: {})
        self._mock_schematics(monkeypatch)
        self._mock_planets(monkeypatch, {("MINE", "1"): 0.10})
        from api import create_app

        with create_app({"TESTING": True}).test_client() as client:
            r = client.post("/api/plan-profitability", json={
                "rows": [self.MINING_ROW],
                "target_products": ["Water"],
            })
        assert r.status_code == 200
        assert r.get_json()["monthly_revenue"] is None
        assert "Water" in r.get_json()["missing_prices"]

    def test_uses_planets_file_rate(self, monkeypatch):
        """
        Ставка берётся автоматически по конкретной планете строки
        (data/planet_industry.csv) — единственный источник (17.09.2026,
        ручной ввод по типу убран при переходе к мультирегиональности).
        """
        self._mock_prices(monkeypatch, {"Water": 10.0})
        self._mock_schematics(monkeypatch)
        self._mock_planets(monkeypatch, {("HOME", "5"): 0.07})
        row = {**self.MINING_ROW, "system": "HOME", "planet": "5"}
        from api import create_app

        with create_app({"TESTING": True}).test_client() as client:
            r = client.post("/api/plan-profitability", json={
                "rows": [row], "target_products": ["Water"],
            })
        body = r.get_json()
        assert "Barren" not in body["missing_rates"]
        assert body["monthly_export_tax"] is not None

    def test_returns_purchase_items_and_prices_snapshot_stamp(self, monkeypatch):
        """
        19.09.2026, по прямому запросу пользователя: список закупки P1
        постатейно, с меткой времени снимка цен — для панели «Список
        закупки сырья» и одноимённого листа экспорта.
        """
        import api.blueprints.market as market

        monkeypatch.setattr(market, "_load_snapshot", lambda: {
            "prices": {"Water": {"buy_max": 10.0}},
            "collected_at": "2026-09-19T00:00:00+00:00",
        })
        self._mock_schematics(monkeypatch)
        self._mock_planets(monkeypatch, {("HOME", "2"): 0.05})
        from api import create_app

        with create_app({"TESTING": True}).test_client() as client:
            r = client.post("/api/plan-profitability", json={
                "rows": [self.PROCESSING_ROW],
                "target_products": ["Coolant"],
                "purchased_p1": {"Water": 600.0},
            })
        body = r.get_json()
        assert body["purchase_items"] == [{
            "product": "Water", "monthly_qty": 432000.0, "price": 10.0, "monthly_cost": 4320000.0,
        }]
        assert body["prices_collected_at"] == "2026-09-19T00:00:00+00:00"

    def test_purchased_p1_cost_reduces_net_profit(self, monkeypatch):
        """
        18.09.2026, по прямому запросу пользователя: purchased_p1 в теле
        запроса (из PlanResult.to_dict()["purchased_p1"], planner.py::
        PlanRequest.purchase_p1) добавляет стоимость закупки к прибыли —
        числа совпадают с tests/test_poco_tax.py::TestPurchaseP1.
        """
        self._mock_prices(monkeypatch, {"Water": 10.0, "Coolant": 100.0})
        self._mock_schematics(monkeypatch)
        self._mock_planets(monkeypatch, {("HOME", "2"): 0.05})
        from api import create_app

        with create_app({"TESTING": True}).test_client() as client:
            r = client.post("/api/plan-profitability", json={
                "rows": [self.PROCESSING_ROW],
                "target_products": ["Coolant"],
                "purchased_p1": {"Water": 600.0},
            })
        assert r.status_code == 200
        body = r.get_json()
        assert body["monthly_purchase_cost"] == 4_320_000.0
        assert body["monthly_net_profit"] == 8_640_000.0 - 432_000.0 - 216_000.0 - 4_320_000.0

    def test_without_purchased_p1_field_stays_normal_plan(self, monkeypatch):
        """Поле необязательно — старые запросы фронтенда (без него) не ломаются."""
        self._mock_prices(monkeypatch, {"Water": 10.0, "Coolant": 100.0})
        self._mock_schematics(monkeypatch)
        self._mock_planets(monkeypatch, {("HOME", "2"): 0.05})
        from api import create_app

        with create_app({"TESTING": True}).test_client() as client:
            r = client.post("/api/plan-profitability", json={
                "rows": [self.PROCESSING_ROW],
                "target_products": ["Coolant"],
            })
        assert r.get_json()["monthly_purchase_cost"] is None


class TestColoniesProfitability:
    """
    POST /api/colonies-profitability (docs/ROADMAP.md, Фаза 9,
    16.09.2026) — прибыльность НАСТОЯЩИХ синхронизированных колоний.
    Числа — в tests/test_poco_tax.py; здесь только HTTP-контракт.
    """

    def _mock_prices(self, monkeypatch, prices: dict[str, float]):
        import api.blueprints.market as market

        monkeypatch.setattr(market, "_load_snapshot", lambda: {
            "prices": {name: {"buy_max": price} for name, price in prices.items()}
        })

    def _mock_planets(self, monkeypatch, rates: dict[tuple[str, str], float]):
        import api.blueprints.plans as plans

        class FakeBook:
            def poco_rate(self, system, planet):
                return rates.get((system, str(planet)))

        monkeypatch.setattr(plans, "_load_planets_book", lambda: FakeBook())

    def test_computes_revenue_and_tax(self, monkeypatch):
        self._mock_prices(monkeypatch, {"Water": 10.0})
        self._mock_planets(monkeypatch, {("MINE", "1"): 0.10})
        from api import create_app

        with create_app({"TESTING": True}).test_client() as client:
            r = client.post("/api/colonies-profitability", json={
                "colonies": [
                    {"label": "A", "product": "Water", "units_per_hour": 1000.0,
                     "planet_type": "Barren", "system": "MINE", "planet": "1"},
                ],
            })
        assert r.status_code == 200
        body = r.get_json()
        assert body["monthly_revenue"] == 7_200_000.0
        assert body["monthly_tax"] == 720_000.0
        assert body["monthly_net_profit"] == 6_480_000.0
        assert body["hours_per_month"] == 720.0

    def test_unknown_current_rate_reported_not_hidden(self, monkeypatch):
        self._mock_prices(monkeypatch, {"Water": 10.0})
        self._mock_planets(monkeypatch, {("MINE", "1"): 0.10})
        from api import create_app

        with create_app({"TESTING": True}).test_client() as client:
            r = client.post("/api/colonies-profitability", json={
                "colonies": [
                    {"label": "A", "product": "Water", "units_per_hour": None,
                     "planet_type": "Barren", "system": "MINE", "planet": "1"},
                ],
            })
        body = r.get_json()
        assert body["missing_output"] == ["A"]
        assert body["monthly_net_profit"] is None

    def test_rejects_non_dict_colony(self, monkeypatch):
        self._mock_prices(monkeypatch, {})
        from api import create_app

        with create_app({"TESTING": True}).test_client() as client:
            r = client.post("/api/colonies-profitability", json={
                "colonies": ["not a dict"],
            })
        assert r.status_code == 400

    def test_works_without_market_snapshot(self, monkeypatch):
        """Правило 3: эндпоинт не ходит наружу и отвечает даже без снимка цен."""
        import api.blueprints.market as market

        monkeypatch.setattr(market, "_load_snapshot", lambda: {})
        self._mock_planets(monkeypatch, {("MINE", "1"): 0.10})
        from api import create_app

        with create_app({"TESTING": True}).test_client() as client:
            r = client.post("/api/colonies-profitability", json={
                "colonies": [
                    {"label": "A", "product": "Water", "units_per_hour": 1000.0,
                     "planet_type": "Barren", "system": "MINE", "planet": "1"},
                ],
            })
        assert r.status_code == 200
        assert r.get_json()["monthly_revenue"] is None
        assert "Water" in r.get_json()["missing_prices"]

    def test_uses_planets_file_rate(self, monkeypatch):
        """Ставка POCO — автоматически по планете колонии, единственный источник."""
        self._mock_prices(monkeypatch, {"Water": 10.0})
        self._mock_planets(monkeypatch, {("AV-VB6", "9"): 0.05})
        from api import create_app

        with create_app({"TESTING": True}).test_client() as client:
            r = client.post("/api/colonies-profitability", json={
                "colonies": [{
                    "label": "A", "product": "Water", "units_per_hour": 1000.0,
                    "planet_type": "Barren", "system": "AV-VB6", "planet": 9,
                }],
            })
        body = r.get_json()
        assert body["missing_rates"] == []
        assert body["monthly_tax"] == 360_000.0


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
