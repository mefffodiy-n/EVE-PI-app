"""
Запись токенов ESI в таблицу credentials.

Общий код для двух мест: `api/blueprints/auth.py` (первый вход) и
`scripts/refresh_tokens.py` (обновление по расписанию). Токены всегда
проходят через `infra.crypto.encrypt` — в открытом виде в БД не лежат.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

DEFAULT_TTL_SECONDS = 1200

# Токен считаем негодным для запроса, если истекает в ближайшие полминуты:
# запрос может не успеть дойти.
_STALE_MARGIN = timedelta(seconds=30)


def _expires_at(expires_in) -> datetime:
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        seconds = DEFAULT_TTL_SECONDS
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def save_tokens(session: Session, character_id: int, tokens: dict, scopes: list) -> None:
    """Upsert строки credentials зашифрованными токенами из ответа SSO."""
    from infra.crypto import encrypt
    from infra.models import Credential

    fields = dict(
        access_token=encrypt(tokens["access_token"]),
        refresh_token=encrypt(tokens["refresh_token"]),
        access_expires_at=_expires_at(tokens.get("expires_in")),
        scopes=list(scopes or []),
    )
    cred = session.get(Credential, character_id)
    if cred is None:
        session.add(Credential(character_id=character_id, **fields))
    else:
        for key, value in fields.items():
            setattr(cred, key, value)


def get_access_token(session: Session, character_id: int) -> str | None:
    """
    Расшифрованный access-токен персонажа, если он есть и ещё годен.

    None означает: токена нет, он просрочен или не расшифровывается.
    Обновление — задача `scripts/refresh_tokens.py`; сборщики скиллов и
    колоний идут в расписании после него, поэтому здесь достаточно
    проверки, а не рефреша.
    """
    from infra.crypto import TokenCryptoError, decrypt
    from infra.models import Credential

    cred = session.get(Credential, character_id)
    if cred is None:
        return None
    if cred.access_expires_at <= datetime.now(timezone.utc) + _STALE_MARGIN:
        return None
    try:
        return decrypt(cred.access_token)
    except TokenCryptoError:
        return None
