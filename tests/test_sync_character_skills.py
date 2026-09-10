"""
scripts/sync_character_skills: подтягивание уровней PI-скиллов из ESI.

Мок ESI — через opener клиента (url, headers) -> (status, body, headers).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from infra import config, crypto
from infra.credentials import save_tokens
from infra.db import session_scope
from infra.models import Character
from scripts import sync_character_skills as sync
from scripts.esi_client import EsiClient
from scripts.sync_character_skills import _levels_from_skills

CCU_ID = sync.SKILL_COMMAND_CENTER_UPGRADES
IC_ID = sync.SKILL_INTERPLANETARY_CONSOLIDATION


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", crypto.generate_key())
    import scripts.esi_client as ec
    monkeypatch.setattr(ec, "ETAG_STORE", tmp_path / "etags.json")


def _fake_opener(skills, *, status=200):
    def opener(url, headers):
        assert headers.get("Authorization", "").startswith("Bearer ")
        body = json.dumps({"skills": skills, "total_sp": 1}) if status == 200 else "{}"
        return status, body, {}
    return opener


def _add_esi_character(character_id=95538921, *, token_minutes=60):
    with session_scope() as s:
        s.add(Character(
            character_id=character_id, name=f"Pilot {character_id}",
            command_center_upgrades_level=0, interplanetary_consolidation_level=0,
            source="esi",
        ))
        if token_minutes is not None:
            save_tokens(s, character_id, {
                "access_token": "a", "refresh_token": "r",
                "expires_in": int(token_minutes * 60),
            }, ["esi-skills.read_skills.v1"])


class TestLevelsFromSkills:
    def test_extracts_both_skills(self):
        payload = {"skills": [
            {"skill_id": CCU_ID, "active_skill_level": 5},
            {"skill_id": IC_ID, "active_skill_level": 4},
            {"skill_id": 3300, "active_skill_level": 3},
        ]}
        assert _levels_from_skills(payload) == (5, 4)

    def test_missing_skill_is_zero(self):
        assert _levels_from_skills({"skills": [{"skill_id": CCU_ID, "active_skill_level": 2}]}) == (2, 0)

    def test_empty(self):
        assert _levels_from_skills({}) == (0, 0)


class TestSync:
    def test_updates_character_levels(self):
        _add_esi_character()
        client = EsiClient(opener=_fake_opener([
            {"skill_id": CCU_ID, "active_skill_level": 5},
            {"skill_id": IC_ID, "active_skill_level": 4},
        ]))
        assert sync.main(client=client) == 0
        with session_scope() as s:
            char = s.get(Character, 95538921)
            assert (char.command_center_upgrades_level, char.interplanetary_consolidation_level) == (5, 4)

    def test_character_without_credential_is_skipped(self):
        _add_esi_character(token_minutes=None)
        client = EsiClient(opener=_fake_opener([{"skill_id": CCU_ID, "active_skill_level": 5}]))
        assert sync.main(client=client) == 0
        with session_scope() as s:
            assert s.get(Character, 95538921).command_center_upgrades_level == 0

    def test_expired_token_is_skipped(self):
        _add_esi_character(token_minutes=-5)
        called = []
        client = EsiClient(opener=lambda u, h: called.append(u) or (200, "{}", {}))
        assert sync.main(client=client) == 0
        assert called == []

    def test_dev_characters_are_ignored(self):
        with session_scope() as s:
            s.add(Character(
                character_id=90001, name="Dev", command_center_upgrades_level=5,
                interplanetary_consolidation_level=5, source="dev",
            ))
        client = EsiClient(opener=lambda u, h: (200, "{}", {}))
        assert sync.main(client=client) == 0

    def test_no_esi_characters_is_noop(self):
        assert sync.main(client=EsiClient(opener=lambda u, h: (200, "{}", {}))) == 0

    def test_esi_error_is_reported(self):
        _add_esi_character()
        client = EsiClient(opener=_fake_opener([], status=500))
        assert sync.main(client=client) == 1
