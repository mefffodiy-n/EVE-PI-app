"""
Разовый перенос справочника планет из `data/planet_industry.csv` в БД
(таблицы `regions`/`planets`) — Фаза 1 мультирегиональности, 21.09.2026,
план утверждён пользователем 17.09.2026 (docs/ROADMAP.md, Фаза 10).

ЗАЧЕМ. `domain/planets.py::load_planets()` после этой фазы читает
`regions`/`planets` вместо CSV, но сам публичный API `PlanetBook` не
меняется — см. докстринг `load_planets()`. Этот скрипт наполняет БД
данными ОДИН РАЗ на каждое окружение (dev, каждый прод-сервер,
включая будущий переезд VPS — см. docs/ROADMAP.md и раздел 1
`deploy/README.md`): переключение сервера не требует ничего особого,
`data/planet_industry.csv` едет вместе с репозиторием через git, и
этот скрипт просто прогоняется заново на пустых `regions`/`planets`.

Переиспользует УЖЕ ОТЛАЖЕННЫЙ CSV-парсинг из
`domain.planets._load_from_csv()` (нормализация опечаток в заголовках,
чистка служебных строк, разбор радиуса с пробелами-разделителями) —
не дублирует его здесь. Единственный регион в текущем CSV — Fountain
(в файле нет колонки Region, см. `PlanetBook.regions()`).

Идемпотентен: если `regions` уже не пуста, останавливается с понятным
сообщением, если не передан `--force` (тогда СНАЧАЛА удаляет все
строки `planets`/`regions`, потом переносит заново — не плодит дубли
при повторном запуске).

Файл `data/planet_industry.csv` этот скрипт НЕ удаляет — это
необратимое действие над данными репозитория, его лучше сделать
осознанно вручную после того, как перенос проверен (см. вывод скрипта).

Использование (после `alembic upgrade head`):
    python -m scripts.migrate_planets_csv_to_db
    python -m scripts.migrate_planets_csv_to_db --force   # пересоздать перенос
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

REGION_NAME = "Fountain"

# Границы блоков плотности сырья в собранном DataFrame — те же имена
# колонок, что в CSV (после нормализации опечаток), см.
# domain/planets.py::COLUMN_NAME_FIXES. Вычисляются срезом между двумя
# известными именами, а не хардкодятся списком — переживает добавление/
# переименование отдельных ресурсов в исходном CSV без правки скрипта.
R0_FIRST, R0_LAST = "Aqueous Liquids", "Plasmoids"
P2_FIRST, P2_LAST = "Biocells", "Water-Cooled CPU"


def _density_columns(df: pd.DataFrame, first: str, last: str) -> list[str]:
    cols = list(df.columns)
    return cols[cols.index(first): cols.index(last) + 1]


def _row_densities(row: pd.Series, columns: list[str]) -> dict[str, float]:
    """Только заполненные значения — как у Planet.r0_density в domain/planets.py."""
    result = {}
    for column in columns:
        value = row[column]
        if pd.notna(value):
            result[column] = float(value)
    return result


def migrate(force: bool = False) -> int:
    from infra.db import session_scope
    from infra.models import Planet, Region

    # _load_from_csv() — единственный оставшийся потребитель CSV-парсинга,
    # load_planets() теперь читает из БД (см. domain/planets.py).
    from domain import planets as planets_csv

    with session_scope() as session:
        existing = session.query(Region).count()
        if existing and not force:
            raise RuntimeError(
                f"regions уже содержит {existing} строк(и) — перенос уже выполнялся. "
                "Передайте --force, чтобы стереть и перенести заново."
            )
        if existing and force:
            session.query(Planet).delete()
            session.query(Region).delete()

    book = planets_csv._load_from_csv()
    df = book.dataframe
    r0_columns = _density_columns(df, R0_FIRST, R0_LAST)
    p2_columns = _density_columns(df, P2_FIRST, P2_LAST)

    written = 0
    with session_scope() as session:
        region = Region(name=REGION_NAME, status="ready")
        session.add(region)
        session.flush()  # получить region.id перед вставкой планет

        for _, row in df.iterrows():
            radius = row[planets_csv.RADIUS_COLUMN]
            poco_rate = row[planets_csv.POCO_RATE_COLUMN] if planets_csv.POCO_RATE_COLUMN in df.columns else None
            poco_owner = row["POCO Owner"] if "POCO Owner" in df.columns else None
            session.add(Planet(
                region_id=region.id,
                constellation=str(row["Constellation"]),
                system=str(row["System"]),
                planet_number=int(round(float(row["Planet"]))),
                planet_type=str(row["Type"]),
                radius_km=float(radius) if pd.notna(radius) else None,
                poco_tax_rate=float(poco_rate) if pd.notna(poco_rate) else None,
                poco_owner=str(poco_owner) if pd.notna(poco_owner) else None,
                r0_densities=_row_densities(row, r0_columns) or None,
                p2_direct_densities=_row_densities(row, p2_columns) or None,
            ))
            written += 1

    return written


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="стереть и перенести заново")
    args = parser.parse_args()

    count = migrate(force=args.force)
    print(f"Перенесено планет: {count} (регион: {REGION_NAME})")
    print(
        "data/planet_industry.csv не удалён — после проверки "
        "(python -m scripts.diagnose, /api/calculate на реальном плане) "
        "удалите его вручную и закоммитьте отдельным изменением."
    )
