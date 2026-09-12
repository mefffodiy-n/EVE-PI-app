"""
EVE SSO: login-url и callback.

Без client_id — 503. С мокнутыми token endpoint и JWKS полный flow:
persist персонажа (source="esi") и зашифрованных токенов, отказ при
плохом JWT (issuer / audience / срок).
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from infra import config, crypto
from infra.db import session_scope
from infra.models import Character, Credential

CLIENT_ID = "test-client-id"


@pytest.fixture
def _rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def sso_env(monkeypatch, _rsa_key):
    """client_id, ключ шифрования, мокнутые сеть SSO и JWKS."""
    monkeypatch.setattr(config, "ESI_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(config, "ESI_CLIENT_SECRET", None)
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", crypto.generate_key())

    import scripts.esi_sso as sso

    monkeypatch.setattr(sso, "_metadata", lambda: {
        "authorization_endpoint": "https://login.eveonline.com/v2/oauth/authorize",
        "token_endpoint": "https://login.eveonline.com/v2/oauth/token",
        "jwks_uri": "https://login.eveonline.com/oauth/jwks",
        "issuer": "https://login.eveonline.com",
    })
    monkeypatch.setattr(
        sso, "_jwks_client",
        lambda: SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key=_rsa_key.public_key())
        ),
    )
    return sso


def _make_jwt(_rsa_key, **overrides):
    claims = {
        "sub": "CHARACTER:EVE:95538921",
        "name": "Test Pilot",
        "iss": "https://login.eveonline.com",
        "aud": [CLIENT_ID, "EVE Online"],
        "scp": ["esi-skills.read_skills.v1"],
        "exp": datetime.now(timezone.utc) + timedelta(minutes=20),
    }
    claims.update(overrides)
    return jwt.encode(claims, _rsa_key, algorithm="RS256")


@pytest.fixture
def client():
    from api import create_app

    return create_app({"TESTING": True}).test_client()


class TestLoginUrl:
    def test_503_without_client_id(self, client, monkeypatch):
        monkeypatch.setattr(config, "ESI_CLIENT_ID", None)
        r = client.get("/api/auth/login-url")
        assert r.status_code == 503
        assert "PI_ESI_CLIENT_ID" in r.get_json()["message"]

    def test_url_carries_pkce_and_scopes(self, client, sso_env):
        r = client.get("/api/auth/login-url")
        assert r.status_code == 200
        url = r.get_json()["url"]
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert q["response_type"] == ["code"]
        assert q["client_id"] == [CLIENT_ID]
        assert q["code_challenge_method"] == ["S256"]
        assert q["code_challenge"] and q["state"]
        assert "esi-skills.read_skills.v1" in q["scope"][0]


class TestCallback:
    def _login(self, client):
        url = client.get("/api/auth/login-url").get_json()["url"]
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        return q["state"][0]

    def test_full_flow_persists_character_and_encrypted_tokens(self, client, sso_env, _rsa_key, monkeypatch):
        access = _make_jwt(_rsa_key)
        sso_env._post_form = lambda *a, **k: {
            "access_token": access, "refresh_token": "refresh-xyz", "expires_in": 1199,
        }
        # monkeypatch через атрибут модуля (fixture уже импортировала его)
        import scripts.esi_sso as mod
        mod._post_form = sso_env._post_form

        # Первичный синк персонажа реально ходит в esi.evetech.net (см.
        # _sync_first_login_async) — здесь это не тестируется, только
        # что колбэк сохранил персонажа и токены. Не даём фоновому
        # потоку стартовать реальный EsiClient() во время теста.
        import api.blueprints.auth as auth_module
        monkeypatch.setattr(auth_module, "_sync_first_login_async", lambda cid: None)

        state = self._login(client)
        r = client.get(f"/api/auth/callback?code=abc&state={state}")
        assert r.status_code == 302 and "auth=ok" in r.headers["Location"]

        with session_scope() as s:
            char = s.get(Character, 95538921)
            assert char and char.source == "esi" and char.name == "Test Pilot"
            cred = s.get(Credential, 95538921)
            assert cred and cred.access_token != access  # зашифрован
            assert crypto.decrypt(cred.refresh_token) == "refresh-xyz"
            assert cred.scopes == ["esi-skills.read_skills.v1"]

    def test_callback_triggers_first_sync_for_the_new_character(self, client, sso_env, _rsa_key, monkeypatch):
        """Колбэк обязан запустить первичный синк именно этого персонажа."""
        import api.blueprints.auth as auth_module

        access = _make_jwt(_rsa_key)
        sso_env._post_form = lambda *a, **k: {
            "access_token": access, "refresh_token": "refresh-xyz", "expires_in": 1199,
        }
        import scripts.esi_sso as mod
        mod._post_form = sso_env._post_form

        seen = []
        monkeypatch.setattr(auth_module, "_sync_first_login_async", lambda cid: seen.append(cid))

        state = self._login(client)
        client.get(f"/api/auth/callback?code=abc&state={state}")
        assert seen == [95538921]

    def test_unknown_state_redirects_to_error(self, client, sso_env):
        r = client.get("/api/auth/callback?code=abc&state=nonexistent")
        assert r.status_code == 302 and "auth=error" in r.headers["Location"]

    @pytest.mark.parametrize("bad", [
        {"iss": "https://evil.example"},
        {"aud": ["someone-else"]},
        {"exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
    ])
    def test_bad_jwt_is_rejected(self, client, sso_env, _rsa_key, bad):
        import scripts.esi_sso as mod
        mod._post_form = lambda *a, **k: {
            "access_token": _make_jwt(_rsa_key, **bad),
            "refresh_token": "r", "expires_in": 1199,
        }
        state = self._login(client)
        r = client.get(f"/api/auth/callback?code=abc&state={state}")
        assert "auth=error" in r.headers["Location"]
        with session_scope() as s:
            assert s.get(Character, 95538921) is None


class TestFirstSyncOne:
    """
    _first_sync_one — то, что реально бежит в фоновом потоке после входа.
    Вызывается тут напрямую (синхронно, с мок-клиентом), а не через
    threading.Thread — гоняться за фоновым потоком в тесте незачем,
    достаточно проверить саму функцию.
    """

    def _opener(self, ccu_level=5, ic_level=4):
        from scripts.sync_character_skills import (
            SKILL_COMMAND_CENTER_UPGRADES, SKILL_INTERPLANETARY_CONSOLIDATION,
        )

        def opener(url, headers):
            if url.endswith("/skills/"):
                body = {"skills": [
                    {"skill_id": SKILL_COMMAND_CENTER_UPGRADES, "active_skill_level": ccu_level},
                    {"skill_id": SKILL_INTERPLANETARY_CONSOLIDATION, "active_skill_level": ic_level},
                ], "total_sp": 1}
            elif url.endswith("/planets/"):
                body = []  # у персонажа пока нет колоний — синк не должен споткнуться
            else:
                body = {}
            return 200, json.dumps(body), {}
        return opener

    def _add_character(self, cid=95538921):
        with session_scope() as s:
            s.add(Character(character_id=cid, name="Pilot",
                            command_center_upgrades_level=0,
                            interplanetary_consolidation_level=0, source="esi"))

    def test_updates_skills_without_waiting_for_schedule(self, monkeypatch, tmp_path):
        from infra import config, crypto
        from infra.credentials import save_tokens
        from scripts.esi_client import EsiClient

        monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", crypto.generate_key())
        import scripts.esi_client as ec
        monkeypatch.setattr(ec, "ETAG_STORE", tmp_path / "etags.json")

        self._add_character()
        with session_scope() as s:
            save_tokens(s, 95538921, {"access_token": "a", "refresh_token": "r",
                                      "expires_in": 3600}, ["esi-skills.read_skills.v1"])

        import api.blueprints.auth as auth_module
        client = EsiClient(opener=self._opener(ccu_level=5, ic_level=4))
        auth_module._first_sync_one(95538921, client=client)

        with session_scope() as s:
            char = s.get(Character, 95538921)
            assert char.command_center_upgrades_level == 5
            assert char.interplanetary_consolidation_level == 4

    def test_unknown_character_is_a_noop(self, monkeypatch, tmp_path):
        from infra import config, crypto
        from scripts.esi_client import EsiClient

        monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", crypto.generate_key())
        import scripts.esi_client as ec
        monkeypatch.setattr(ec, "ETAG_STORE", tmp_path / "etags.json")

        import api.blueprints.auth as auth_module
        # Не должно бросить исключение, даже если персонажа уже нет в БД
        # (устарел, пока поток стартовал).
        auth_module._first_sync_one(999999999, client=EsiClient(opener=self._opener()))


class TestUnlink:
    """
    «Отвязать персонажа» (roadmap.md, Фаза 3) — честная замена настоящему
    отзыву на стороне CCP (недоступен публичному PKCE-клиенту без
    client_secret, см. infra/credentials.py::delete_tokens). Здесь
    проверяется только наш собственный эффект: токен стёрт, персонаж
    откреплён от визита (account_id=None) — не запрос к login.eveonline.com,
    его тут нет и быть не может.
    """

    def _login_as(self, client, account_id):
        with client.session_transaction() as sess:
            sess["account_id"] = account_id

    def _add_character(self, cid, account_id, source="esi"):
        from infra.credentials import save_tokens

        with session_scope() as s:
            s.add(Character(character_id=cid, name="Pilot",
                            command_center_upgrades_level=5,
                            interplanetary_consolidation_level=4,
                            source=source, account_id=account_id))
            if source == "esi":
                save_tokens(s, cid, {"access_token": "a", "refresh_token": "r",
                                     "expires_in": 3600}, ["esi-skills.read_skills.v1"])

    def test_unlink_deletes_token_and_clears_account_id(self, client, monkeypatch):
        from infra import config, crypto

        monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", crypto.generate_key())
        self._add_character(95538921, "acct-a")
        self._login_as(client, "acct-a")

        r = client.post("/api/auth/unlink/95538921")
        assert r.status_code == 200
        assert r.get_json()["unlinked"] == 95538921

        with session_scope() as s:
            char = s.get(Character, 95538921)
            assert char is not None and char.account_id is None
            assert s.get(Credential, 95538921) is None

    def test_cannot_unlink_someone_elses_character(self, client, monkeypatch):
        from infra import config, crypto

        monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", crypto.generate_key())
        self._add_character(95538921, "acct-a")
        self._login_as(client, "acct-b")

        r = client.post("/api/auth/unlink/95538921")
        assert r.status_code == 404

        with session_scope() as s:
            char = s.get(Character, 95538921)
            assert char.account_id == "acct-a"  # не тронут
            assert s.get(Credential, 95538921) is not None

    def test_anonymous_visit_cannot_unlink(self, client, monkeypatch):
        from infra import config, crypto

        monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", crypto.generate_key())
        self._add_character(95538921, "acct-a")

        r = client.post("/api/auth/unlink/95538921")
        assert r.status_code == 404

    def test_dev_stub_cannot_be_unlinked(self, client, monkeypatch):
        from infra import config, crypto

        monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", crypto.generate_key())
        self._add_character(90001, "acct-a", source="dev")
        self._login_as(client, "acct-a")

        r = client.post("/api/auth/unlink/90001")
        assert r.status_code == 400

        with session_scope() as s:
            assert s.get(Character, 90001).account_id == "acct-a"  # не тронут

    def test_unknown_character_reports_the_same_not_found(self, client, monkeypatch):
        from infra import config, crypto

        monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", crypto.generate_key())
        self._login_as(client, "acct-a")

        r = client.post("/api/auth/unlink/1")
        assert r.status_code == 404

    def test_characters_endpoint_flags_esi_linked_for_the_unlink_button(self, client, monkeypatch):
        """
        /api/characters — источник esi_linked, по которому фронт решает,
        показывать ли кнопку «Отвязать персонажа» (не всё персонажу можно
        отвязать — см. test_dev_stub_cannot_be_unlinked).
        """
        from infra import config, crypto

        monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", crypto.generate_key())
        self._add_character(95538921, "acct-a", source="esi")
        self._add_character(90001, "acct-a", source="dev")
        self._login_as(client, "acct-a")

        body = client.get("/api/characters").get_json()
        by_id = {c["character_id"]: c for c in body["characters"]}
        assert by_id[95538921]["esi_linked"] is True
        assert by_id[90001]["esi_linked"] is False
        # _add_character() создаёт настоящий credential для source="esi" —
        # значит needs_reconnect должно быть False, а не просто отсутствовать.
        assert by_id[95538921]["needs_reconnect"] is False
        assert by_id[90001]["needs_reconnect"] is False

    def test_characters_endpoint_flags_needs_reconnect_when_credential_is_gone(self, client, monkeypatch):
        """
        sync_colony_status.py::sync_one() молча пропускает персонажа без
        валидного токена (get_access_token() -> None, "skipped" — не
        ошибка джоба), поэтому чип «Сборщики» остаётся зелёным, даже
        когда конкретный esi-персонаж не обновляется вовсе. needs_reconnect
        — единственный видимый сигнал этого в интерфейсе, см. renderCrew()
        в web/index.html.
        """
        from infra import config, crypto

        monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", crypto.generate_key())
        with session_scope() as s:
            s.add(Character(character_id=95538921, name="Pilot",
                             command_center_upgrades_level=5,
                             interplanetary_consolidation_level=4,
                             source="esi", account_id="acct-a"))
        # Ни одного save_tokens() — refresh_tokens.py удалил credential
        # после invalid_grant (пользователь отозвал доступ/сменил пароль).
        self._login_as(client, "acct-a")

        body = client.get("/api/characters").get_json()
        by_id = {c["character_id"]: c for c in body["characters"]}
        assert by_id[95538921]["needs_reconnect"] is True

    def test_unlink_calls_real_revoke_when_secret_configured(self, client, monkeypatch):
        """
        PI_ESI_CLIENT_SECRET задан — unlink() обязан не только стереть
        свою копию, но и попытаться настоящий отзыв на стороне CCP
        (scripts/esi_sso.py::revoke, POST /v2/oauth/revoke с
        client_secret_basic — единственный способ аутентификации, который
        публичный PKCE-клиент без секрета не может использовать вовсе).
        """
        from infra import config, crypto

        monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", crypto.generate_key())
        monkeypatch.setattr(config, "ESI_CLIENT_ID", "test-client-id")
        monkeypatch.setattr(config, "ESI_CLIENT_SECRET", "test-secret")

        import scripts.esi_sso as sso
        monkeypatch.setattr(sso, "_metadata", lambda: {
            "revocation_endpoint": "https://login.eveonline.com/v2/oauth/revoke",
        })
        calls = []
        monkeypatch.setattr(sso, "_revoke_request", lambda url, data, headers: calls.append(
            (url, data, headers)) or 200)

        self._add_character(95538921, "acct-a")
        self._login_as(client, "acct-a")

        r = client.post("/api/auth/unlink/95538921")
        assert r.status_code == 200
        assert r.get_json()["revoked"] is True

        assert len(calls) == 1
        url, data, headers = calls[0]
        assert url == "https://login.eveonline.com/v2/oauth/revoke"
        assert data["token"] == "r"  # refresh_token из _add_character
        assert data["token_type_hint"] == "refresh_token"
        assert headers["Authorization"].startswith("Basic ")

        # Своя копия всё равно стёрта — revoke() не заменяет локальное удаление.
        with session_scope() as s:
            assert s.get(Character, 95538921).account_id is None
            assert s.get(Credential, 95538921) is None

    def test_unlink_succeeds_locally_even_if_ccp_revoke_fails(self, client, monkeypatch):
        """
        CCP недоступен/вернул ошибку — локальное удаление токена не
        должно от этого зависеть: пользователь пришёл отвязать персонажа
        именно от НАШЕГО приложения, и это должно получиться всегда.
        """
        from infra import config, crypto

        monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", crypto.generate_key())
        monkeypatch.setattr(config, "ESI_CLIENT_ID", "test-client-id")
        monkeypatch.setattr(config, "ESI_CLIENT_SECRET", "test-secret")

        import scripts.esi_sso as sso
        monkeypatch.setattr(sso, "_metadata", lambda: {
            "revocation_endpoint": "https://login.eveonline.com/v2/oauth/revoke",
        })
        monkeypatch.setattr(sso, "_revoke_request", lambda url, data, headers: 500)

        self._add_character(95538921, "acct-a")
        self._login_as(client, "acct-a")

        r = client.post("/api/auth/unlink/95538921")
        assert r.status_code == 200
        assert r.get_json()["revoked"] is False

        with session_scope() as s:
            assert s.get(Character, 95538921).account_id is None
            assert s.get(Credential, 95538921) is None

    def test_unlink_without_secret_does_not_attempt_revoke(self, client, monkeypatch):
        """Без секрета — revoked=False и ни одного обращения к CCP."""
        from infra import config, crypto

        monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", crypto.generate_key())
        monkeypatch.setattr(config, "ESI_CLIENT_SECRET", None)

        import scripts.esi_sso as sso
        calls = []
        monkeypatch.setattr(sso, "_revoke_request", lambda *a, **k: calls.append(1) or 200)

        self._add_character(95538921, "acct-a")
        self._login_as(client, "acct-a")

        r = client.post("/api/auth/unlink/95538921")
        assert r.get_json()["revoked"] is False
        assert calls == []


def test_verify_rejects_missing_eve_online_audience(sso_env, _rsa_key, monkeypatch):
    from scripts.esi_sso import SsoError, verify_access_token

    token = _make_jwt(_rsa_key, aud=CLIENT_ID)  # только client_id, без литерала
    with pytest.raises(SsoError, match="EVE Online"):
        verify_access_token(token)
