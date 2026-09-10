"""
scripts/refresh_tokens: фоновое продление access-токенов ESI.

Свежие не трогает, протухающие обновляет, отозванные удаляет (Character
остаётся). Пустая таблица — no-op.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from infra import config, crypto
from infra.crypto import encrypt
from infra.db import session_scope
from infra.models import Character, Credential
from scripts import refresh_tokens


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", crypto.generate_key())


@pytest.fixture(autouse=True)
def _mock_sso(monkeypatch):
    import scripts.esi_sso as sso

    monkeypatch.setattr(sso, "refresh", lambda rt: {
        "access_token": "new-access-jwt",
        "refresh_token": "rotated-refresh",
        "expires_in": 1199,
    })
    monkeypatch.setattr(sso, "verify_access_token",
                        lambda token: {"scp": ["esi-skills.read_skills.v1"]})
    return sso


def _add_credential(character_id: int, *, minutes_left: float, refresh_token="valid-refresh"):
    with session_scope() as s:
        s.add(Character(
            character_id=character_id, name=f"Pilot {character_id}",
            command_center_upgrades_level=0, interplanetary_consolidation_level=0,
            source="esi",
        ))
        s.add(Credential(
            character_id=character_id,
            access_token=encrypt("old-access"),
            refresh_token=encrypt(refresh_token),
            access_expires_at=datetime.now(timezone.utc) + timedelta(minutes=minutes_left),
            scopes=["old-scope"],
        ))


def test_empty_table_is_noop():
    assert refresh_tokens.main() == 0


def test_fresh_token_is_left_alone():
    _add_credential(1, minutes_left=60)
    assert refresh_tokens.main() == 0
    with session_scope() as s:
        assert crypto.decrypt(s.get(Credential, 1).access_token) == "old-access"


def test_expiring_token_is_refreshed():
    _add_credential(2, minutes_left=5)
    assert refresh_tokens.main() == 0
    with session_scope() as s:
        cred = s.get(Credential, 2)
        assert crypto.decrypt(cred.access_token) == "new-access-jwt"
        assert crypto.decrypt(cred.refresh_token) == "rotated-refresh"
        assert cred.scopes == ["esi-skills.read_skills.v1"]
        assert cred.access_expires_at > datetime.now(timezone.utc) + timedelta(minutes=15)


def test_revoked_refresh_token_removes_credential_keeps_character(monkeypatch):
    from scripts.esi_sso import SsoError

    _add_credential(3, minutes_left=1)
    monkeypatch.setattr("scripts.esi_sso.refresh",
                        lambda rt: (_ for _ in ()).throw(SsoError("SSO вернул HTTP 400: invalid_grant")))
    assert refresh_tokens.main() == 0
    with session_scope() as s:
        assert s.get(Credential, 3) is None
        assert s.get(Character, 3) is not None


def test_undecryptable_refresh_token_removes_credential(monkeypatch):
    _add_credential(4, minutes_left=1)
    with session_scope() as s:
        s.get(Credential, 4).refresh_token = "not-a-fernet-token"
    assert refresh_tokens.main() == 0
    with session_scope() as s:
        assert s.get(Credential, 4) is None


def test_transient_error_keeps_credential_and_reports(monkeypatch):
    from scripts.esi_sso import SsoError

    _add_credential(5, minutes_left=1)
    monkeypatch.setattr("scripts.esi_sso.refresh",
                        lambda rt: (_ for _ in ()).throw(SsoError("SSO вернул HTTP 503: down")))
    assert refresh_tokens.main() == 1
    with session_scope() as s:
        assert s.get(Credential, 5) is not None
