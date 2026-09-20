"""
Резервная копия базы и снимков кэша.

ЗАЧЕМ. В базе — сохранённые планы, привязки персонажей, зашифрованные
токены ESI, реальные колонии, история добычи. Файл/дамп в git не
хранится; без копий потеря невосстановима.

Что копируем:
  - Базу — SQLite через API `.backup()` (безопасно даже при работающем
    приложении), Postgres через `pg_dump` (тот же принцип: снимок
    согласованного состояния без остановки сервиса). С 15.09.2026 на
    проде — Postgres (docs/ROADMAP.md, Фаза 9); функция для SQLite осталась
    ради разработки и отката. С 18.09.2026 — сжатый custom-формат
    (`pg_dump -Fc`, файл `pidirector.dump`), не текстовый SQL: без сжатия
    размер копии растёт линейно с базой, custom-формат сокращает его в
    разы почти бесплатно. Восстановление —
    `pg_restore --no-owner --clean --if-exists -d имя_базы файл`,
    не `psql имя_базы < файл`, как было для текстового дампа;
  - MySQL/MariaDB (20.09.2026, по прямому запросу пользователя —
    прежде эти движки не бэкапились вовсе, только предупреждение в
    лог) — `mysqldump --single-transaction` (снимок InnoDB без
    блокировки таблиц, тот же принцип, что и у Postgres/SQLite выше),
    сжатый через `gzip` на стороне Python (у `mysqldump`, в отличие от
    `pg_dump`, нет своего сжатого формата), файл `pidirector.sql.gz`.
    Восстановление — `gunzip -c pidirector.sql.gz | mysql -h хост -u
    пользователь -p имя_базы`;
  - снимки `data/cache/*.json` (кроме служебного etags.json) — их легко
    пересобрать, но с копией дашборд не «мигнёт» пустотой после отката.

Дальнейший рост базы — см. docs/ROADMAP.md, Фаза 10, пункт про
инкрементальные бэкапы (WAL-архивирование): применять, когда база
займёт заметное место на диске, не раньше — сейчас (18.09.2026) это
11 МБ, сжатие с запасом покрывает рост на годы вперёд.

Куда: `{PI_BACKUP_DIR}/pi-backup-YYYYMMDD-HHMMSS/`. Старые чистятся,
остаётся последние `PI_BACKUP_KEEP` (по умолчанию 14).

Запуск:
    python -m scripts.backup

По расписанию — в `scripts/scheduler.py` (раз в сутки) или заданием ОС.
"""

from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BACKUP_DIR = Path(os.environ.get("PI_BACKUP_DIR", ROOT / "data" / "backups"))
KEEP = max(1, int(os.environ.get("PI_BACKUP_KEEP", "14")))

CACHE_DIR = ROOT / "data" / "cache"
CACHE_SKIP = {"etags.json"}


def _sqlite_path() -> Path | None:
    from infra import config

    url = config.DATABASE_URL
    if not url.startswith("sqlite:///"):
        return None
    return Path(url[len("sqlite:///"):])


def _postgres_url() -> str | None:
    from infra import config

    url = config.DATABASE_URL
    if not url.startswith(("postgresql://", "postgresql+")):
        return None
    # pg_dump принимает conninfo-URI вида postgresql://..., а не
    # SQLAlchemy-форму с указанием драйвера (postgresql+psycopg://) —
    # драйвер сюда не нужен, это дело SQLAlchemy, не libpq.
    scheme, _, rest = url.partition("://")
    return f"postgresql://{rest}"


def _mariadb_url() -> str | None:
    from infra import config

    url = config.DATABASE_URL
    if not url.startswith(("mysql://", "mysql+", "mariadb://", "mariadb+")):
        return None
    return url


def _backup_postgres(url: str, dst: Path) -> None:
    """
    `pg_dump` в СЖАТОМ custom-формате (`-Fc`, 18.09.2026 — по прямому
    запросу пользователя: без сжатия дамп растёт линейно с базой,
    custom-формат сокращает его в разы почти бесплатно, единственная
    цена — восстановление через `pg_restore`, а не `psql имя_базы < файл`:
        pg_restore --no-owner --clean --if-exists -d имя_базы файл
    """
    with dst.open("wb") as out:
        subprocess.run(
            ["pg_dump", "--no-owner", "--no-privileges", "-Fc", url],
            stdout=out, check=True, timeout=300,
        )


def _backup_mariadb(url: str, dst: Path) -> None:
    """
    `mysqldump --single-transaction` (20.09.2026, по прямому запросу
    пользователя — до этого MySQL/MariaDB не бэкапились вовсе, см.
    docstring модуля) — снимок InnoDB без блокировки таблиц, тот же
    принцип согласованности без остановки сервиса, что и у Postgres/
    SQLite выше. У `mysqldump`, в отличие от `pg_dump`, нет собственного
    сжатого бинарного формата — сжимаем сами через `gzip`, тем же
    мотивом, что и `-Fc` для Postgres (без сжатия копия растёт линейно
    с базой). Восстановление:
        gunzip -c pidirector.sql.gz | mysql -h хост -u пользователь -p имя_базы

    Пароль передаётся подпроцессу через переменную окружения `MYSQL_PWD`
    (то, что рекомендует сама документация MySQL), а не аргументом
    командной строки `-pПАРОЛЬ` — иначе он был бы виден в выводе `ps`
    любому пользователю системы, не только в истории шелла запустившего.
    """
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    cmd = ["mysqldump", "--single-transaction", "--routines", "--triggers"]
    if parsed.host:
        cmd += ["-h", parsed.host]
    if parsed.port:
        cmd += ["-P", str(parsed.port)]
    if parsed.username:
        cmd += ["-u", parsed.username]
    cmd.append(parsed.database)

    env = dict(os.environ)
    if parsed.password:
        env["MYSQL_PWD"] = parsed.password

    result = subprocess.run(cmd, stdout=subprocess.PIPE, env=env, check=True, timeout=300)
    with gzip.open(dst, "wb") as out:
        out.write(result.stdout)


def _backup_sqlite(src: Path, dst: Path) -> None:
    """Согласованная копия через sqlite3.Connection.backup()."""
    with sqlite3.connect(src) as source, sqlite3.connect(dst) as target:
        source.backup(target)


def _prune(keep: int) -> list[str]:
    dirs = sorted(
        (p for p in BACKUP_DIR.glob("pi-backup-*") if p.is_dir()),
        key=lambda p: p.name,
    )
    removed = []
    for old in dirs[:-keep] if keep else dirs:
        shutil.rmtree(old, ignore_errors=True)
        removed.append(old.name)
    return removed


def main() -> int:
    from infra.logging import configure

    log = configure("backup")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / f"pi-backup-{stamp}"

    db = _sqlite_path()
    pg_url = _postgres_url()
    maria_url = _mariadb_url()
    if db is not None:
        if not db.is_file():
            log.warning("Базы %s ещё нет — бэкапить нечего", db)
            return 0
        target.mkdir(parents=True, exist_ok=True)
        _backup_sqlite(db, target / db.name)
        copied = 1
    elif pg_url is not None:
        target.mkdir(parents=True, exist_ok=True)
        try:
            _backup_postgres(pg_url, target / "pidirector.dump")
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
            # Честно падаем в лог, а не молчим: без дампа резервной копии
            # БД в этом запуске нет вовсе, снимки кэша ниже её не заменяют.
            log.error("pg_dump не выполнился: %s — копии БД в этом запуске нет", exc)
            shutil.rmtree(target, ignore_errors=True)
            return 1
        copied = 1
    elif maria_url is not None:
        target.mkdir(parents=True, exist_ok=True)
        try:
            _backup_mariadb(maria_url, target / "pidirector.sql.gz")
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
            log.error("mysqldump не выполнился: %s — копии БД в этом запуске нет", exc)
            shutil.rmtree(target, ignore_errors=True)
            return 1
        copied = 1
    else:
        log.warning("Неизвестный PI_DATABASE_URL — не SQLite, не Postgres и не MySQL/MariaDB, БД не копирую")
        target.mkdir(parents=True, exist_ok=True)
        copied = 0
    if CACHE_DIR.is_dir():
        for snap in CACHE_DIR.glob("*.json"):
            if snap.name not in CACHE_SKIP:
                shutil.copy2(snap, target / snap.name)
                copied += 1

    removed = _prune(KEEP)
    log.info("Копия: %s (%d файлов)%s", target.name, copied,
             f", удалено старых: {len(removed)}" if removed else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
