"""scripts/verify_backup: проверка, что копия восстанавливается."""

from __future__ import annotations

import gzip
import sqlite3

import pytest

from scripts import backup, verify_backup


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr("infra.logging.LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr("infra.config.DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    return tmp_path


def _sqlite_backup(tmp_path, tables):
    d = tmp_path / "backups" / "pi-backup-20260101-000000"
    d.mkdir(parents=True)
    con = sqlite3.connect(d / "app.db")
    for t in tables:
        con.execute(f"create table {t}(x)")
    con.commit()
    con.close()
    return d


def test_no_backups_is_failure():
    assert verify_backup.main() == 1


def test_complete_sqlite_backup_passes(_env):
    _sqlite_backup(_env, verify_backup.expected_tables())
    assert verify_backup.main() == 0


def test_backup_missing_a_table_fails(_env):
    _sqlite_backup(_env, verify_backup.expected_tables() - {"characters"})
    assert verify_backup.main() == 1


def test_corrupt_backup_fails(_env):
    d = _env / "backups" / "pi-backup-20260101-000000"
    d.mkdir(parents=True)
    (d / "app.db").write_bytes(b"not a database" * 100)
    assert verify_backup.main() == 1


def _dump(tables, complete=True):
    body = "".join(f"CREATE TABLE `{t}` (x int);\n" for t in sorted(tables))
    return (body + ("-- Dump completed on 2026\n" if complete else "")).encode()


def test_mariadb_truncated_dump_detected():
    tables = verify_backup.expected_tables()
    problems = verify_backup._dump_problems(_dump(tables, complete=False).decode(), tables)
    assert any("оборван" in p for p in problems)


def test_mariadb_dump_missing_table_detected():
    tables = verify_backup.expected_tables()
    problems = verify_backup._dump_problems(_dump(tables - {"plans"}).decode(), tables)
    assert any("plans" in p for p in problems)


def test_mariadb_without_create_privilege_falls_back_to_dump_check(_env, monkeypatch):
    dump = _env / "d.sql.gz"
    dump.write_bytes(gzip.compress(_dump(verify_backup.expected_tables())))

    class R:
        returncode = 1
        stdout = b""
        stderr = b"denied"

    monkeypatch.setattr(verify_backup.subprocess, "run", lambda *a, **k: R())
    problems, how = verify_backup.check_mariadb(dump, "mysql+pymysql://u:p@h/pi")
    assert problems == [] and "CREATE DATABASE" in how
