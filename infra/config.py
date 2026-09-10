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


# ── EVE SSO / OAuth2 (Фаза 3) ─────────────────────────────────────────
# Регистрация приложения: https://developers.eveonline.com/applications
# До появления client_id весь auth-слой просто отдаёт 503 — это ожидаемо
# (roadmap, раздел 0, пункт 6) и не мешает разработке на dev-заглушках.
#
# Тип приложения — public client + PKCE: у self-hosted инструмента нет
# безопасного места для секрета (CLAUDE.md, правило 11). Если приложение
# всё же зарегистрировано как confidential, положите секрет в
# PI_ESI_CLIENT_SECRET — клиент отправит и его, PKCE остаётся.
ESI_CLIENT_ID = os.environ.get("PI_ESI_CLIENT_ID") or None
ESI_CLIENT_SECRET = os.environ.get("PI_ESI_CLIENT_SECRET") or None

# Должен совпадать с Callback URL в настройках приложения на
# developers.eveonline.com — до знака.
ESI_CALLBACK_URL = os.environ.get(
    "PI_ESI_CALLBACK_URL", "http://localhost:8000/api/auth/callback"
)

# Минимальный набор для Фазы 3: уровни скиллов и список колоний.
# Пользователь подтверждает каждый scope в окне SSO.
ESI_SCOPES = os.environ.get(
    "PI_ESI_SCOPES",
    "esi-skills.read_skills.v1 esi-planets.manage_planets.v1",
).split()

# Ключ шифрования refresh/access-токенов в БД (Fernet, base64, 32 байта).
# Сгенерировать: python -c "from infra.crypto import generate_key; print(generate_key())"
TOKEN_ENCRYPTION_KEY = os.environ.get("PI_TOKEN_KEY") or None


def sso_configured() -> bool:
    """Готов ли auth-слой к работе (есть client_id и ключ шифрования)."""
    return bool(ESI_CLIENT_ID and TOKEN_ENCRYPTION_KEY)
