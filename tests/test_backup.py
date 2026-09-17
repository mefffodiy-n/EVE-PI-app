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


def test_noop_when_db_missing(_paths, monkeypatch):
    monkeypatch.setattr("infra.config.DATABASE_URL", f"sqlite:///{_paths / 'nope.db'}")
    assert backup.main() == 0


def test_backs_up_postgres_via_pg_dump(_paths, monkeypatch):
    """
    С 15.09.2026 прод на Postgres (docs/ROADMAP.md, Фаза 9) — pg_dump реальный
    процесс, недоступный в тестовом окружении, поэтому подменяется тем же
    приёмом, что и ESI-клиент в других тестах: сама функция запуска, не
    subprocess.run целиком, чтобы проверить и передаваемый URL, и то, что
    результат действительно попадает в архив бэкапа.
    """
    monkeypatch.setattr("infra.config.DATABASE_URL", "postgresql+psycopg://u:p@localhost/pidirector")
    calls = []

    def fake_backup_postgres(url, dst):
        calls.append(url)
        dst.write_text("-- dump", encoding="utf-8")

    monkeypatch.setattr(backup, "_backup_postgres", fake_backup_postgres)
    assert backup.main() == 0
    # pg_dump получает conninfo-URI (postgresql://), а не форму
    # SQLAlchemy с драйвером (postgresql+psycopg://) — драйвер нужен
    # только SQLAlchemy, не libpq.
    assert calls == ["postgresql://u:p@localhost/pidirector"]
    made = next((_paths / "backups").glob("pi-backup-*"))
    assert (made / "pidirector.dump").read_text(encoding="utf-8") == "-- dump"
    assert "market_prices.json" in {p.name for p in made.iterdir()}


def test_pg_dump_failure_is_logged_not_silenced(_paths, monkeypatch):
    """
    Честный сбой, а не тихий пропуск: если pg_dump не выполнился,
    резервной копии БД в этом запуске нет вовсе — снимки кэша её не
    заменяют, значит запуск должен явно сообщить об ошибке, а не
    отчитаться успехом с неполной копией.
    """
    monkeypatch.setattr("infra.config.DATABASE_URL", "postgresql+psycopg://u:p@localhost/pidirector")

    def fake_backup_postgres(url, dst):
        raise FileNotFoundError("pg_dump: команда не найдена")

    monkeypatch.setattr(backup, "_backup_postgres", fake_backup_postgres)
    assert backup.main() == 1
    assert _backups(_paths) == []


def test_postgres_backup_uses_compressed_custom_format(_paths, monkeypatch, tmp_path):
    """
    18.09.2026, по прямому запросу пользователя: сжатый custom-формат
    (-Fc) вместо текстового SQL-дампа — размер копии растёт линейно с
    базой без сжатия, custom-формат сокращает его в разы почти бесплатно.
    """
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        kwargs["stdout"].write(b"fake dump")

        class Result:
            returncode = 0
        return Result()

    monkeypatch.setattr(backup.subprocess, "run", fake_run)
    dst = tmp_path / "out.dump"
    backup._backup_postgres("postgresql://u:p@localhost/pidirector", dst)

    assert calls[0][:2] == ["pg_dump", "--no-owner"]
    assert "-Fc" in calls[0]
    assert dst.read_bytes() == b"fake dump"


def test_unknown_database_url_still_backs_up_cache(_paths, monkeypatch):
    """Не SQLite и не Postgres — БД не копируем, но снимки кэша всё равно сохраняем."""
    monkeypatch.setattr("infra.config.DATABASE_URL", "mysql://u:p@localhost/x")
    assert backup.main() == 0
    made = next((_paths / "backups").glob("pi-backup-*"))
    assert "market_prices.json" in {p.name for p in made.iterdir()}
