"""
Резервная копия базы и снимков кэша.

ЗАЧЕМ. В базе — сохранённые планы, привязки персонажей, зашифрованные
токены ESI, реальные колонии, история добычи. Файл/дамп в git не
хранится; без копий потеря невосстановима.

Что копируем:
  - Базу — SQLite через API `.backup()` (безопасно даже при работающем
    приложении), Postgres через `pg_dump` (тот же принцип: снимок
    согласованного состояния без остановки сервиса). С 15.09.2026 на
    проде — Postgres (roadmap.md, Фаза 9); функция для SQLite осталась
    ради разработки и отката;
  - снимки `data/cache/*.json` (кроме служебного etags.json) — их легко
    пересобрать, но с копией дашборд не «мигнёт» пустотой после отката.

Куда: `{PI_BACKUP_DIR}/pi-backup-YYYYMMDD-HHMMSS/`. Старые чистятся,
остаётся последние `PI_BACKUP_KEEP` (по умолчанию 14).

Запуск:
    python -m scripts.backup

По расписанию — в `scripts/scheduler.py` (раз в сутки) или заданием ОС.
"""

from __future__ import annotations

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


def _backup_postgres(url: str, dst: Path) -> None:
    """
    `pg_dump` в формате обычного SQL-дампа (не custom): восстановление —
    `psql имя_базы < файл`, без утилиты `pg_restore` под рукой.
    """
    with dst.open("wb") as out:
        subprocess.run(
            ["pg_dump", "--no-owner", "--no-privileges", url],
            stdout=out, check=True, timeout=300,
        )


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
            _backup_postgres(pg_url, target / "pidirector.sql")
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
            # Честно падаем в лог, а не молчим: без дампа резервной копии
            # БД в этом запуске нет вовсе, снимки кэша ниже её не заменяют.
            log.error("pg_dump не выполнился: %s — копии БД в этом запуске нет", exc)
            shutil.rmtree(target, ignore_errors=True)
            return 1
        copied = 1
    else:
        log.warning("Неизвестный PI_DATABASE_URL — не SQLite и не Postgres, БД не копирую")
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
