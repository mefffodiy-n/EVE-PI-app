"""Оповещения в Discord: настройки (API), сборщик send_alerts, отправка вебхука."""

from __future__ import annotations

import io
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from domain import alerts
from infra import config
from infra.crypto import generate_key
from infra.db import session_scope
from infra.models import AlertSent, AlertSubscription, Character, Colony
from scripts import send_alerts

URL = "https://discord.com/api/webhooks/123456789/AbCdEf-ghij_KLMNO"
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", generate_key())


@pytest.fixture
def client():
    from api import create_app

    app = create_app({"TESTING": True})
    with app.test_client() as c:
        yield c


@pytest.fixture
def signed_in(client):
    with client.session_transaction() as s:
        s["account_id"] = "acc-1"
    return client


class TestWebhookUrl:
    @pytest.mark.parametrize("url", [
        URL, "https://discordapp.com/api/webhooks/1/x",
    ])
    def test_accepts_discord(self, url):
        assert alerts.is_valid_webhook_url(url)

    @pytest.mark.parametrize("url", [
        "http://discord.com/api/webhooks/1/x", "https://evil.example/api/webhooks/1/x",
        "https://discord.com.evil.example/api/webhooks/1/x", "https://127.0.0.1/api/webhooks/1/x",
        "https://discord.com/api/webhooks/abc/x", "https://discord.com/api/webhooks/1/x?a=b", "",
    ])
    def test_rejects_everything_else(self, url):
        assert not alerts.is_valid_webhook_url(url)


class TestSettingsApi:
    def test_requires_a_visit_with_account(self, client):
        assert client.get("/api/alerts").status_code == 401
        assert client.put("/api/alerts", json={"webhook_url": URL}).status_code == 401

    def test_save_and_read_back_masked(self, signed_in):
        r = signed_in.put("/api/alerts", json={"webhook_url": URL, "expiry_lead_hours": 4, "on_deficit": False})
        assert r.status_code == 200
        body = signed_in.get("/api/alerts").get_json()
        assert body["configured"] and body["enabled"] and body["expiry_lead_hours"] == 4
        assert body["on_deficit"] is False and body["on_expiry"] is True
        assert body["webhook_hint"] == "…_KLMNO" and URL not in str(body)
        with session_scope() as s:
            sub = s.execute(select(AlertSubscription)).scalar_one()
            assert URL not in sub.webhook_url_enc

    def test_rejects_bad_url_and_bad_lead(self, signed_in):
        assert signed_in.put("/api/alerts", json={"webhook_url": "https://evil.example/x"}).status_code == 400
        assert signed_in.put("/api/alerts", json={"webhook_url": URL, "expiry_lead_hours": 3}).status_code == 400
        assert signed_in.put("/api/alerts", json={"on_deficit": True}).status_code == 400  # нет подписки и адреса

    def test_without_encryption_key_is_unavailable(self, signed_in, monkeypatch):
        monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "")
        assert signed_in.put("/api/alerts", json={"webhook_url": URL}).status_code == 503

    def test_toggle_and_delete(self, signed_in):
        signed_in.put("/api/alerts", json={"webhook_url": URL})
        assert signed_in.put("/api/alerts", json={"enabled": False}).get_json()["enabled"] is False
        assert signed_in.delete("/api/alerts").status_code == 200
        assert signed_in.get("/api/alerts").get_json()["configured"] is False

    def test_test_button_sends_once_a_minute(self, signed_in, monkeypatch):
        sent = []
        monkeypatch.setattr(send_alerts, "post_webhook", lambda url, text, opener=None: (sent.append((url, text)), ("ok", ""))[1])
        assert signed_in.post("/api/alerts/test").status_code == 404
        signed_in.put("/api/alerts", json={"webhook_url": URL})
        assert signed_in.post("/api/alerts/test").status_code == 200
        assert sent[0][0] == URL
        assert signed_in.post("/api/alerts/test").status_code == 429

    def test_test_button_reports_discord_failure(self, signed_in, monkeypatch):
        monkeypatch.setattr(send_alerts, "post_webhook", lambda *a, **k: ("gone", "HTTP 404"))
        signed_in.put("/api/alerts", json={"webhook_url": URL})
        r = signed_in.post("/api/alerts/test")
        assert r.status_code == 502 and "HTTP 404" in r.get_json()["message"]


def _seed(pins, account="acc-1"):
    with session_scope() as s:
        s.add(Character(character_id=1, name="Pilot", command_center_upgrades_level=5,
                        interplanetary_consolidation_level=5, source="esi", account_id=account))
        s.add(Colony(character_id=1, planet_id=10, planet_name="X I", system_name="AV-VB6",
                     planet_index=4, planet_type="barren", upgrade_level=5, num_pins=len(pins), pins=pins))
        sub = AlertSubscription(account_id=account, webhook_url_enc="", lang="en")
        s.add(sub)
    return None


def _make_sub():
    from infra.crypto import encrypt

    with session_scope() as s:
        sub = s.execute(select(AlertSubscription)).scalar_one()
        sub.webhook_url_enc = encrypt(URL)


def _ecu(base=9000, expiry=None, pid=1):
    return {"kind": "extractor_control_unit", "pin_id": pid, "product": "Water", "qty_per_cycle": base,
            "install_time": "2026-09-25T06:30:00Z", "cycle_seconds": 3600,
            "expiry_time": expiry.isoformat() if expiry else None}


def _run(post):
    with session_scope() as s:
        sub = s.execute(select(AlertSubscription)).scalar_one()
        return send_alerts.process(s, sub, NOW, post=post)


class TestCollector:
    def test_sends_once_then_only_on_state_change(self):
        _seed([_ecu(expiry=NOW + timedelta(hours=1))])
        _make_sub()
        sent = []

        def post(url, text):
            sent.append(text)
            return "ok", ""

        assert _run(post) == "ok"
        assert len(sent) == 1 and "deficit" in sent[0] and "stops soon" in sent[0] and "Pilot" in sent[0]
        assert _run(post) == "none" and len(sent) == 1        # то же состояние — молчим

        with session_scope() as s:                              # экстрактор перезапущен: новая дата окончания
            col = s.execute(select(Colony)).scalar_one()
            col.pins = [_ecu(expiry=NOW + timedelta(hours=1, minutes=30))]
        assert _run(post) == "ok" and len(sent) == 2 and "stops soon" in sent[1]

    def test_recurrence_after_recovery_alerts_again(self):
        _seed([_ecu()])
        _make_sub()
        sent = []
        post = lambda url, text: (sent.append(text), ("ok", ""))[1]  # noqa: E731
        _run(post)
        with session_scope() as s:
            s.execute(select(Colony)).scalar_one().pins = []
        assert _run(post) == "none"
        with session_scope() as s:
            assert s.execute(select(AlertSent)).first() is None     # состояние очищено
            s.execute(select(Colony)).scalar_one().pins = [_ecu()]
        assert _run(post) == "ok" and len(sent) == 2

    def test_rate_limit_leaves_events_unsent_for_next_run(self):
        _seed([_ecu()])
        _make_sub()
        assert _run(lambda u, t: ("rate", "Retry-After 5")) == "rate"
        with session_scope() as s:
            assert s.execute(select(AlertSent)).first() is None
        assert _run(lambda u, t: ("ok", "")) == "ok"

    def test_dead_webhook_disables_subscription(self):
        _seed([_ecu()])
        _make_sub()
        assert _run(lambda u, t: ("gone", "HTTP 404")) == "gone"
        with session_scope() as s:
            sub = s.execute(select(AlertSubscription)).scalar_one()
            assert sub.enabled is False and sub.last_error == "gone:HTTP 404"

    def test_other_accounts_colonies_are_not_included(self):
        _seed([_ecu()], account="somebody-else")
        with session_scope() as s:
            s.execute(select(AlertSubscription)).scalar_one().account_id = "acc-1"
        _make_sub()
        assert _run(lambda u, t: pytest.fail("нечего отправлять")) == "none"

    def test_flags_switch_event_kinds_off(self):
        _seed([_ecu(expiry=NOW + timedelta(hours=1))])
        _make_sub()
        with session_scope() as s:
            sub = s.execute(select(AlertSubscription)).scalar_one()
            sub.on_deficit = False
        texts = []
        _run(lambda u, t: (texts.append(t), ("ok", ""))[1])
        assert "deficit" not in texts[0] and "stops soon" in texts[0]


class TestPostWebhook:
    def test_ok_and_headers(self):
        seen = {}

        def opener(request, timeout):
            seen["ua"] = request.headers["User-agent"]
            seen["body"] = request.data
            return io.BytesIO(b"")

        assert send_alerts.post_webhook(URL, "hi", opener=opener) == ("ok", "")
        assert seen["ua"].startswith("PI-Director/") and b'"parse": []' in seen["body"]

    @pytest.mark.parametrize("code,expected", [(404, "gone"), (401, "gone"), (429, "rate"), (500, "error")])
    def test_http_errors(self, code, expected):
        def opener(request, timeout):
            raise urllib.error.HTTPError(URL, code, "x", {"Retry-After": "3"}, None)

        assert send_alerts.post_webhook(URL, "hi", opener=opener)[0] == expected

    def test_network_error_is_transient(self):
        def opener(request, timeout):
            raise urllib.error.URLError("down")

        assert send_alerts.post_webhook(URL, "hi", opener=opener)[0] == "error"

    def test_foreign_url_is_never_requested(self):
        def opener(request, timeout):
            pytest.fail("запрос к чужому адресу")

        assert send_alerts.post_webhook("https://evil.example/x", "hi", opener=opener)[0] == "gone"


class TestMessage:
    def _events(self, n):
        return [alerts.Deficit(1, 10 + i, "AV-VB6", 4, i, "Water", 9000.0, ["Coolant"]) for i in range(n)]

    def test_russian_and_english(self):
        ru = alerts.render_message(self._events(1), {1: "Pilot"}, "ru")
        en = alerts.render_message(self._events(1), {1: "Pilot"}, "en")
        assert "дефицит добычи" in ru and "под угрозой: Coolant" in ru
        assert "extraction deficit" in en and "chain at risk: Coolant" in en

    def test_long_message_fits_discord_limit(self):
        text = alerts.render_message(self._events(200), {1: "Pilot"}, "en")
        assert len(text) <= alerts.MAX_MESSAGE_CHARS and "more" in text
