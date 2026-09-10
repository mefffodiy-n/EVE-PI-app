"""
Сборщик: обновление access-токенов ESI по refresh-токену.

ЗАЧЕМ ОТДЕЛЬНЫМ СКРИПТОМ. Access-токен живёт ~20 минут. Обновлять его в
HTTP-обработчике нельзя (правило 3 — исключение только для самого
OAuth-flow). Этот скрипт по расписанию продлевает токены до истечения,
чтобы `sync_character_skills` / `sync_colony_status` всегда имели свежий.

Запуск вручную:
    python -m scripts.refresh_tokens

По расписанию — в `scripts/scheduler.py` (раз в 15 минут).

Пока никто не вошёл через настоящий SSO, таблица `credentials` пуста и
скрипт — мгновенный no-op.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Обновляем токен, если до истечения меньше этого запаса. Больше интервала
# планировщика (15 мин) — чтобы токен не успел протухнуть между прогонами,
# даже если один был пропущен.
REFRESH_MARGIN = timedelta(minutes=20)


def _due_credentials(session):
    from sqlalchemy import select

    from infra.models import Credential

    cutoff = datetime.now(timezone.utc) + REFRESH_MARGIN
    return session.scalars(
        select(Credential).where(Credential.access_expires_at <= cutoff)
    ).all()


def _refresh_one(session, cred) -> str:
    """
    Обновить один credential. Возвращает 'ok', 'revoked' или 'error'.

    'revoked' — refresh-токен больше не действует (пользователь отозвал
    доступ или сменил пароль). Строка удаляется: персонажу нужно заново
    войти через EVE SSO. Character при этом остаётся.
    """
    from infra.credentials import save_tokens
    from infra.crypto import decrypt
    from scripts.esi_sso import SsoError, verify_access_token

    try:
        refresh_token = decrypt(cred.refresh_token)
    except Exception as exc:  # noqa: BLE001
        print(f"  · персонаж {cred.character_id}: refresh-токен не расшифровать ({exc}) — удаляю")
        session.delete(cred)
        return "revoked"

    from scripts.esi_sso import refresh as sso_refresh

    try:
        tokens = sso_refresh(refresh_token)
    except SsoError as exc:
        text = str(exc).lower()
        if "invalid_grant" in text or "400" in text:
            print(f"  · персонаж {cred.character_id}: refresh отклонён ({exc}) — удаляю, нужен повторный вход")
            session.delete(cred)
            return "revoked"
        print(f"  · персонаж {cred.character_id}: временная ошибка обновления ({exc})")
        return "error"

    # scopes берём из нового access-токена; если проверка не прошла —
    # оставляем прежние.
    scopes = cred.scopes
    try:
        claims = verify_access_token(tokens["access_token"])
        raw = claims.get("scp")
        scopes = raw if isinstance(raw, list) else ([raw] if raw else scopes)
    except SsoError as exc:
        print(f"  · персонаж {cred.character_id}: новый токен не прошёл проверку ({exc})")
        return "error"

    save_tokens(session, cred.character_id, tokens, scopes)
    print(f"  · персонаж {cred.character_id}: токен обновлён")
    return "ok"


def main() -> int:
    from sqlalchemy.exc import OperationalError

    from infra.db import session_scope

    errors = 0
    try:
        with session_scope() as session:
            due = _due_credentials(session)
            if not due:
                print("Обновлять нечего: свежих токенов нет или таблица пуста.")
                return 0
            print(f"К обновлению: {len(due)}")
            for cred in due:
                if _refresh_one(session, cred) == "error":
                    errors += 1
    except OperationalError:
        print("Нет таблицы credentials — выполните `python -m alembic upgrade head`.")
        return 0

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
