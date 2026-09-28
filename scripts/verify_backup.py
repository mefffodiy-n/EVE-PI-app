"""
Проверка, что последняя резервная копия действительно восстанавливается.

ЗАЧЕМ. Копия, которую ни разу не разворачивали, — предположение, а не
копия. Скрипт берёт самую свежую `pi-backup-*` и проверяет её на ПУСТОЙ
базе, не трогая рабочую:
  - SQLite — копия открывается, `PRAGMA integrity_check`;
  - MariaDB/MySQL — дамп разворачивается во временную БД
    `<имя>_restorecheck` (создаётся и удаляется); если у пользователя БД
    нет права CREATE DATABASE, проверяется сам дамп: gzip цел, дамп
    дочитан до конца (`Dump completed`), в нём есть все таблицы схемы;
  - Postgres — `pg_restore --list` читает оглавление дампа, все таблицы
    схемы должны быть в нём.
Обязательное условие везде: в копии есть ВСЕ таблицы из моделей
(`infra/models.py`) плюс `alembic_version`. Итог — в журнал; код возврата
1 виден как сбой задачи в `/api/meta`.

Запуск: python -m scripts.verify_backup   (по расписанию — раз в неделю
в scripts/scheduler.py)
"""

from __future__ import annotations

import gzip
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import backup  # noqa: E402


def expected_tables() -> set[str]:
    from infra import models  # noqa: F401  (регистрация моделей в метаданных)
    from infra.db import Base

    return set(Base.metadata.tables) | {"alembic_version"}


def latest_backup() -> Path | None:
    dirs = sorted(
        (p for p in backup.BACKUP_DIR.glob("pi-backup-*") if p.is_dir()),
        key=lambda p: p.name,
    )
    return dirs[-1] if dirs else None


def check_sqlite(path: Path) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "restore.db"
        copy.write_bytes(path.read_bytes())
        con = sqlite3.connect(copy)
        try:
            verdict = con.execute("PRAGMA integrity_check").fetchone()[0]
            if verdict != "ok":
                return [f"integrity_check: {verdict}"]
            found = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        finally:
            con.close()
    missing = expected_tables() - found
    return [f"нет таблиц: {', '.join(sorted(missing))}"] if missing else []


def _dump_problems(text: str, tables: set[str]) -> list[str]:
    found = set(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?`?(\w+)`?", text))
    problems = []
    missing = tables - found
    if missing:
        problems.append(f"нет таблиц: {', '.join(sorted(missing))}")
    if "Dump completed" not in text:
        problems.append("дамп оборван (нет строки «Dump completed»)")
    return problems


def check_mariadb(path: Path, url: str) -> tuple[list[str], str]:
    """Возвращает (проблемы, как проверено)."""
    from sqlalchemy.engine import make_url

    with gzip.open(path, "rb") as f:
        data = f.read()
    problems = _dump_problems(data.decode("utf-8", "replace"), expected_tables())
    if problems:
        return problems, "структура дампа"

    parsed = make_url(url)
    scratch = f"{parsed.database}_restorecheck"
    base = ["mysql"]
    if parsed.host:
        base += ["-h", parsed.host]
    if parsed.port:
        base += ["-P", str(parsed.port)]
    if parsed.username:
        base += ["-u", parsed.username]
    env = dict(os.environ)
    if parsed.password:
        env["MYSQL_PWD"] = parsed.password

    def sql(stmt: str, db: str | None = None, stdin: bytes | None = None):
        cmd = base + (["-D", db] if db else []) + (["-e", stmt] if stmt else [])
        return subprocess.run(cmd, input=stdin, env=env, capture_output=True, timeout=300)

    created = sql(f"DROP DATABASE IF EXISTS `{scratch}`; CREATE DATABASE `{scratch}`")
    if created.returncode != 0:
        return [], "структура дампа (нет права CREATE DATABASE)"
    try:
        loaded = sql("", scratch, stdin=data)
        if loaded.returncode != 0:
            err = loaded.stderr.decode("utf-8", "replace")[:300]
            return [f"дамп не развернулся: {err}"], "восстановление"
        shown = sql("SHOW TABLES", scratch)
        got = set(shown.stdout.decode().split("\n")[1:]) - {""}
        missing = expected_tables() - got
        if missing:
            return [f"после восстановления нет таблиц: {', '.join(sorted(missing))}"], "восстановление"
        return [], "восстановление во временную БД"
    finally:
        sql(f"DROP DATABASE IF EXISTS `{scratch}`")


def check_postgres(path: Path) -> list[str]:
    out = subprocess.run(
        ["pg_restore", "--list", str(path)], capture_output=True, timeout=120, check=True
    ).stdout.decode("utf-8", "replace")
    found = set(re.findall(r"\bTABLE\s+\S+\s+(\w+)\s", out))
    missing = expected_tables() - found
    return [f"нет таблиц: {', '.join(sorted(missing))}"] if missing else []


def main() -> int:
    from infra.logging import configure

    log = configure("verify_backup")
    latest = latest_backup()
    if latest is None:
        log.error("Проверка копии: в %s нет ни одной копии", backup.BACKUP_DIR)
        return 1

    how = "восстановление"
    try:
        sqlite_db = backup._sqlite_path()
        pg_url = backup._postgres_url()
        maria_url = backup._mariadb_url()
        if sqlite_db is not None:
            problems = check_sqlite(latest / sqlite_db.name)
            how = "открытие копии SQLite"
        elif pg_url is not None:
            problems = check_postgres(latest / "pidirector.dump")
            how = "оглавление pg_restore"
        elif maria_url is not None:
            problems, how = check_mariadb(latest / "pidirector.sql.gz", maria_url)
        else:
            log.warning("Проверка копии: неизвестный PI_DATABASE_URL, пропуск")
            return 0
    except (OSError, subprocess.SubprocessError, sqlite3.Error, EOFError) as exc:
        log.error("Проверка копии %s не удалась: %s", latest.name, exc)
        return 1

    if problems:
        log.error("Копия %s НЕ восстанавливается (%s): %s", latest.name, how, "; ".join(problems))
        return 1
    log.info("Копия %s проверена, восстановима (%s)", latest.name, how)
    return 0


if __name__ == "__main__":
    sys.exit(main())
