"""
Разовый перенос данных из dev/старой prod SQLite-базы в Postgres.

ЗАЧЕМ. Переключение PI_DATABASE_URL на Postgres (см. requirements.txt,
CLAUDE.md, roadmap.md Фаза 9) даёт СХЕМУ через `alembic upgrade head`,
но не переносит уже накопленные строки — персонажей, их зашифрованные
токены, реальные колонии, историю добычи, сохранённые планы. Без этого
скрипта переключение на Postgres означало бы для всех вошедших
пользователей выход в чистую БД: заново логиниться через EVE SSO,
терять сохранённые планы и накопленную историю добычи.

Копирует построчно через ORM-модели (infra/models.py), а не сырым SQL —
таблицы без внешних ключей друг на друга, порядок неважен. Зашифрованные
поля (Credential.access_token/refresh_token) копируются как есть: это
шифрование уровня приложения (infra/crypto.py, тот же PI_TOKEN_KEY на
обеих сторонах), не связано с СУБД.

После копирования строк с явными PK у таблиц с autoincrement (только
ExtractionSample.id) синхронизирует последовательность Postgres —
иначе следующая вставка через autoincrement попытается переиспользовать
уже занятый id и упадёт на UNIQUE.

Запуск (однократно, ПОСЛЕ `alembic upgrade head` на пустой Postgres):
    python -m scripts.migrate_sqlite_to_postgres \
        --sqlite sqlite:////opt/pi-director/app/data/pi_director.db \
        --postgres postgresql+psycopg://user:pass@localhost/pi_director

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

# Порядок неважен — ни одна модель не ссылается на другую через FK
# (roadmap.md: связь Colony/Credential с Character — по character_id
# как по натуральному ключу, без ForeignKey, см. infra/models.py).
TABLES = [Character, Plan, Credential, Colony, ExtractionSample]


def _count(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def migrate(sqlite_url: str, postgres_url: str, dry_run: bool) -> None:
    src_engine = create_engine(sqlite_url, future=True)
    dst_engine = create_engine(postgres_url, future=True)

    with Session(src_engine) as src, Session(dst_engine) as dst:
        print(f"{'Таблица':<20} {'SQLite':>10} {'Postgres (до)':>15}")
        counts_before = {}
        for model in TABLES:
            src_n = _count(src, model)
            dst_n = _count(dst, model)
            counts_before[model] = (src_n, dst_n)
            print(f"{model.__tablename__:<20} {src_n:>10} {dst_n:>15}")

        if dry_run:
            print("\n--dry-run: ничего не записано.")
            return

        for model in TABLES:
            src_n, dst_n = counts_before[model]
            if dst_n:
                print(f"! {model.__tablename__}: в Postgres уже {dst_n} строк — "
                      f"пропускаю, чтобы не задвоить (таблица не пустая).")
                continue
            rows = src.scalars(select(model)).all()
            for row in rows:
                dst.merge(row)  # merge, не add: переносит PK как есть
            dst.commit()
            print(f"✓ {model.__tablename__}: перенесено {len(rows)} строк")

        # ExtractionSample.id — единственный autoincrement PK среди
        # перенесённых таблиц; без этого следующий INSERT через
        # sequence столкнётся с уже занятым id и упадёт.
        if postgres_url.startswith(("postgresql", "postgres")):
            max_id = dst.scalar(select(func.max(ExtractionSample.id))) or 0
            dst.execute(
                text("SELECT setval('extraction_samples_id_seq', :max_id)"),
                {"max_id": max_id},
            )
            dst.commit()
            print(f"✓ extraction_samples_id_seq выставлена на {max_id}")

        print("\nПроверка после переноса:")
        for model in TABLES:
            print(f"{model.__tablename__:<20} {_count(dst, model):>10}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True, help="URL источника (SQLite)")
    parser.add_argument("--postgres", required=True, help="URL назначения (Postgres)")
    parser.add_argument("--dry-run", action="store_true", help="только посчитать строки")
    args = parser.parse_args()

    migrate(args.sqlite, args.postgres, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
