"""
Резервная копия базы и снимков кэша.

ЗАЧЕМ. В `data/pi_director.db` — сохранённые планы и (в Фазе 3) привязки
персонажей. Файл в git не хранится; без копий его потеря невосстановима.

Что копируем:
  - SQLite-базу — через API `.backup()`, безопасно даже при работающем
    приложении (для Postgres на проде — `pg_dump`, см. deploy/README.md);
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

    db = _sqlite_path()
    if db is None:
        log.warning("PI_DATABASE_URL не SQLite — используйте pg_dump (deploy/README.md)")
        return 0
    if not db.is_file():
        log.warning("Базы %s ещё нет — бэкапить нечего", db)
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / f"pi-backup-{stamp}"
    target.mkdir(parents=True, exist_ok=True)

    _backup_sqlite(db, target / db.name)
    copied = 1
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
