"""
Разовый перенос данных из прод-Postgres в MariaDB/MySQL.

ЗАЧЕМ. Тот же принцип, что и у `scripts/migrate_sqlite_to_postgres.py`
(см. его докстринг) — переключение `PI_DATABASE_URL` даёт СХЕМУ через
`alembic upgrade head`, но не переносит уже накопленные строки:
персонажей, их зашифрованные токены, реальные колонии, историю добычи,
сохранённые планы.

Копирует построчно через ORM-модели (infra/models.py), не сырым SQL —
таблицы без внешних ключей друг на друга (natural key `character_id`,
не ForeignKey — см. docs/ROADMAP.md), порядок переноса не важен для
целостности, важен только для читаемости прогресса.

После копирования строк с явными PK у таблиц с autoincrement (только
ExtractionSample.id) синхронизирует AUTO_INCREMENT на стороне
назначения — иначе следующая вставка попытается переиспользовать уже
занятый id и упадёт на PRIMARY KEY.

Запуск (однократно, ПОСЛЕ `alembic upgrade head` на пустой MariaDB):
    python -m scripts.migrate_postgres_to_mysql \
        --postgres postgresql+psycopg://user:pass@localhost/pidirector \
        --mysql mysql+pymysql://user:pass@localhost/pidirector

--dry-run считает строки на обеих сторонах, ничего не пишет.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from infra.models import Character, Colony, Credential, ExtractionSample, Plan

# Порядок неважен для целостности — см. докстринг модуля.
TABLES = [Character, Plan, Credential, Colony, ExtractionSample]


def _count(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def migrate(postgres_url: str, mysql_url: str, dry_run: bool) -> None:
    src_engine = create_engine(postgres_url, future=True)
    dst_engine = create_engine(mysql_url, future=True)

    with Session(src_engine) as src, Session(dst_engine) as dst:
        print(f"{'Таблица':<20} {'Postgres':>10} {'MySQL (до)':>12}")
        counts_before = {}
        for model in TABLES:
            src_n = _count(src, model)
            dst_n = _count(dst, model)
            counts_before[model] = (src_n, dst_n)
            print(f"{model.__tablename__:<20} {src_n:>10} {dst_n:>12}")

        if dry_run:
            print("\n--dry-run: ничего не записано.")
            return

        for model in TABLES:
            src_n, dst_n = counts_before[model]
            if dst_n:
                print(f"! {model.__tablename__}: в MySQL уже {dst_n} строк — "
                      f"пропускаю, чтобы не задвоить (таблица не пустая).")
                continue
            rows = src.scalars(select(model)).all()
            for row in rows:
                dst.merge(row)  # merge, не add: переносит PK как есть
            dst.commit()
            print(f"✓ {model.__tablename__}: перенесено {len(rows)} строк")

        # ExtractionSample.id — единственный autoincrement PK среди
        # перенесённых таблиц; без синхронизации следующая вставка
        # через AUTO_INCREMENT столкнётся с уже занятым id и упадёт.
        max_id = dst.scalar(select(func.max(ExtractionSample.id))) or 0
        dst.execute(text(f"ALTER TABLE extraction_samples AUTO_INCREMENT = {max_id + 1}"))
        dst.commit()
        print(f"✓ extraction_samples.AUTO_INCREMENT выставлен на {max_id + 1}")

        print("\nПроверка после переноса:")
        for model in TABLES:
            print(f"{model.__tablename__:<20} {_count(dst, model):>10}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres", required=True, help="URL источника (Postgres)")
    parser.add_argument("--mysql", required=True, help="URL назначения (MySQL/MariaDB)")
    parser.add_argument("--dry-run", action="store_true", help="только посчитать строки")
    args = parser.parse_args()

    migrate(args.postgres, args.mysql, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
