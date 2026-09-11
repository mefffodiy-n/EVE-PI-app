"""
Слой БД: infra/db, infra/models, seed_dev_characters.

Проверяется то, что легко сломать незаметно:
  - seed идемпотентен и не трогает реальных персонажей;
  - вне dev seed запрещён, а dev-строки не читаются;
  - миграция Alembic не разошлась с моделями (частая ошибка — поправил
    модель, забыл сгенерировать revision).
"""

from __future__ import annotations

import pytest

from infra import config, db
from infra.models import Character
from scripts.seed_dev_characters import DEV_CHARACTERS, load_characters, seed


class TestSeed:
    def test_seed_writes_all_dev_characters(self):
        assert seed() == len(DEV_CHARACTERS)
        chars = load_characters()
        assert len(chars) == len(DEV_CHARACTERS)
        assert {c.name for c in chars} == {row[1] for row in DEV_CHARACTERS}

    def test_seed_is_idempotent(self):
        seed()
        seed()
        with db.session_scope() as s:
            assert s.query(Character).count() == len(DEV_CHARACTERS)

    def test_seed_updates_changed_skill_levels(self, monkeypatch):
        seed()
        # Скилл прокачали — повторный seed обновляет строку, а не плодит.
        monkeypatch.setattr(
            "scripts.seed_dev_characters.DEV_CHARACTERS",
            [(90001, "Dev Factory Chief 1", 4, 4, "понизили")],
        )
        seed()
        got = {c.character_id: c for c in load_characters()}
        assert got[90001].command_center_upgrades_level == 4

    def test_seed_refused_outside_dev(self, monkeypatch):
        monkeypatch.setattr(config, "IS_DEV", False)
        with pytest.raises(RuntimeError, match="вне dev"):
            seed()

    def test_seed_does_not_overwrite_real_character(self):
        with db.session_scope() as s:
            s.add(Character(
                character_id=90001, name="Real Pilot",
                command_center_upgrades_level=3, interplanetary_consolidation_level=3,
                source="esi",
            ))
        seed()
        with db.session_scope() as s:
            real = s.get(Character, 90001)
            assert real.name == "Real Pilot" and real.source == "esi"


class TestLoad:
    def test_missing_table_yields_empty_not_error(self):
        db.Base.metadata.drop_all(db.engine())
        assert load_characters() == []

    def test_prod_ignores_dev_rows(self, monkeypatch):
        seed()
        monkeypatch.setattr(config, "IS_DEV", False)
        assert load_characters() == []

    def test_prod_loads_real_rows_of_matching_account_only(self, monkeypatch):
        """
        Найдено 11.09.2026: разные пользователи, вошедшие через SSO,
        видели персонажей друг друга — load_characters() отдавала ВСЕ
        esi-строки без учёта того, кто спрашивает. Теперь нужен
        совпадающий account_id.
        """
        with db.session_scope() as s:
            s.add(Character(
                character_id=1001, name="Real Pilot",
                command_center_upgrades_level=5, interplanetary_consolidation_level=5,
                source="esi", account_id="acct-a",
            ))
            s.add(Character(
                character_id=1002, name="Someone Else",
                command_center_upgrades_level=5, interplanetary_consolidation_level=5,
                source="esi", account_id="acct-b",
            ))
        monkeypatch.setattr(config, "IS_DEV", False)
        assert [c.name for c in load_characters(account_id="acct-a")] == ["Real Pilot"]
        assert [c.name for c in load_characters(account_id="acct-b")] == ["Someone Else"]

    def test_prod_without_account_id_sees_nothing(self, monkeypatch):
        """Анонимный визит (ещё не входил через SSO) — честно пусто, не «всё»."""
        with db.session_scope() as s:
            s.add(Character(
                character_id=1001, name="Real Pilot",
                command_center_upgrades_level=5, interplanetary_consolidation_level=5,
                source="esi", account_id="acct-a",
            ))
        monkeypatch.setattr(config, "IS_DEV", False)
        assert load_characters() == []
        assert load_characters(account_id=None) == []

    def test_dev_ignores_account_id(self, monkeypatch):
        """Локальная разработка однопользовательская — account_id тут ни на что не влияет."""
        with db.session_scope() as s:
            s.add(Character(
                character_id=1001, name="Real Pilot",
                command_center_upgrades_level=5, interplanetary_consolidation_level=5,
                source="esi", account_id=None,
            ))
        assert config.IS_DEV
        assert any(c.name == "Real Pilot" for c in load_characters(account_id="does-not-matter"))
        assert any(c.name == "Real Pilot" for c in load_characters())

    def test_fields_map_to_character_slot(self):
        seed()
        chief = next(c for c in load_characters() if c.character_id == 90001)
        assert chief.command_center_upgrades_level == 5
        assert chief.interplanetary_consolidation_level == 5
        assert chief.planet_slots == 6  # IC 5 + 1


class TestMigrationParity:
    def test_alembic_head_matches_models(self, tmp_path, monkeypatch):
        """
        `alembic upgrade head` на пустой БД должен дать те же таблицы и
        колонки, что и Base.metadata. Расхождение = забытый revision.
        """
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import create_engine, inspect

        url = f"sqlite:///{tmp_path / 'mig.db'}"
        monkeypatch.setattr(config, "DATABASE_URL", url)

        cfg = Config(str(config.ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(config.ROOT / "migrations"))
        command.upgrade(cfg, "head")

        migrated = inspect(create_engine(url))
        model_tables = set(db.Base.metadata.tables) | {"alembic_version"}
        assert set(migrated.get_table_names()) == model_tables

        for name, table in db.Base.metadata.tables.items():
            migrated_cols = {c["name"] for c in migrated.get_columns(name)}
            assert migrated_cols == set(table.columns.keys()), name
