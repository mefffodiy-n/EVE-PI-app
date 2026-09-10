"""
scripts/backup: резервная копия SQLite-базы и снимков кэша.
"""

from __future__ import annotations

import sqlite3

import pytest

from scripts import backup


@pytest.fixture(autouse=True)
def _paths(tmp_path, monkeypatch):
    db = tmp_path / "app.db"
    sqlite3.connect(db).executescript("create table t(x); insert into t values (1);")
    monkeypatch.setattr("infra.config.DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path / "backups")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "market_prices.json").write_text("{}", encoding="utf-8")
    (cache / "etags.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(backup, "CACHE_DIR", cache)
    monkeypatch.setattr("infra.logging.LOG_DIR", tmp_path / "logs")
    return tmp_path


def _backups(root):
    return sorted(p.name for p in (root / "backups").glob("pi-backup-*"))


def test_creates_a_backup_with_db_and_snapshots(_paths):
    assert backup.main() == 0
    made = list((_paths / "backups").glob("pi-backup-*"))
    assert len(made) == 1
    files = {p.name for p in made[0].iterdir()}
    assert "app.db" in files
    assert "market_prices.json" in files
    assert "etags.json" not in files  # служебный, не копируем


def test_backup_db_is_a_valid_copy(_paths):
    backup.main()
    copy = next((_paths / "backups").glob("pi-backup-*")) / "app.db"
    assert sqlite3.connect(copy).execute("select x from t").fetchone() == (1,)


def test_prunes_to_keep_limit(_paths, monkeypatch):
    monkeypatch.setattr(backup, "KEEP", 3)
    for i in range(5):
        (_paths / "backups" / f"pi-backup-2026010{i}-000000").mkdir(parents=True)
    backup.main()  # +1 свежая, всего было бы 6 → останется 3
    assert len(_backups(_paths)) == 3
    assert _backups(_paths)[-1].startswith("pi-backup-2026")  # свежая на месте


def test_noop_for_non_sqlite_url(_paths, monkeypatch):
    monkeypatch.setattr("infra.config.DATABASE_URL", "postgresql://x/y")
    assert backup.main() == 0
    assert _backups(_paths) == []


def test_noop_when_db_missing(_paths, monkeypatch):
    monkeypatch.setattr("infra.config.DATABASE_URL", f"sqlite:///{_paths / 'nope.db'}")
    assert backup.main() == 0
