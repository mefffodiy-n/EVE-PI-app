"""
Конфигурация окружения — одно место, где читаются переменные среды.

Значения берутся при импорте модуля. Тесты подменяют их через
monkeypatch на атрибуты этого модуля, а не через os.environ, чтобы
не зависеть от порядка импортов.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# dev | prod. По умолчанию dev: боевое окружение обязано выставить
# PI_ENV=prod явно. От этого зависит, разрешён ли seed_dev_characters
# и подставляются ли тестовые персонажи (см. правило проекта 6).
ENV = os.environ.get("PI_ENV", "dev").lower()

IS_DEV = ENV == "dev"


def _default_database_url() -> str:
    """
    SQLite-файл в data/. Выбор для разработки: нулевая настройка, Docker
    не нужен (правило проекта 4). В продакшене — Postgres (roadmap,
    раздел 5): нужен для multi-tenant и конкурентной записи из воркеров
    и API. Переключение — только сменой PI_DATABASE_URL и `alembic
    upgrade head`; ORM-модели те же.
    """
    return f"sqlite:///{ROOT / 'data' / 'pi_director.db'}"


DATABASE_URL = os.environ.get("PI_DATABASE_URL", _default_database_url())
