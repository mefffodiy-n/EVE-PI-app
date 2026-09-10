"""
EVE SSO: вход через OAuth2 Authorization Code + PKCE.

Контракт фронтенда (web/index.html → startLogin):
  GET /api/auth/login-url   -> {status, url}       браузер идёт по url
  GET /api/auth/callback    -> 302 на / (?auth=ok|error)

ИСКЛЮЧЕНИЕ ИЗ ПРАВИЛА 3. Обычные обработчики в сеть не ходят. Здесь —
ходят: обмен кода на токен по своей природе синхронный вызов к
login.eveonline.com. Это разовое действие пользователя (не обслуживание
данных), идёт не к ESI-API и на его лимит ошибок не влияет. Дальше
токены обновляет фоновый refresh_tokens, а не обработчик.

БЕЗ client_id и ключа шифрования — login-url отдаёт 503. Это ожидаемо
(roadmap 0.6): разработка идёт на dev-заглушках.
"""

from __future__ import annotations

import time

from flask import Blueprint, current_app, redirect, request

from api.cache import json_error, json_ok
from infra import config

bp = Blueprint("auth", __name__)

# state -> (code_verifier, created_at). В памяти процесса: flow занимает
# секунды, а нескольких воркеров на self-hosted инструменте нет. Рестарт
# во время входа — пользователь жмёт «Войти» заново.
_PENDING: dict[str, tuple[str, float]] = {}
_PENDING_TTL = 600


def _sweep() -> None:
    cutoff = time.time() - _PENDING_TTL
    for state in [s for s, (_, ts) in _PENDING.items() if ts < cutoff]:
        _PENDING.pop(state, None)


@bp.get("/auth/login-url")
def login_url():
    from scripts.esi_sso import authorize_url, new_state, pkce_pair

    if not config.sso_configured():
        missing = []
        if not config.ESI_CLIENT_ID:
            missing.append("PI_ESI_CLIENT_ID")
        if not config.TOKEN_ENCRYPTION_KEY:
            missing.append("PI_TOKEN_KEY")
        return json_error(
            "Вход через EVE SSO не настроен: не задано " + ", ".join(missing) + ". "
            "Зарегистрируйте приложение на developers.eveonline.com.",
            503,
        )

    _sweep()
    state = new_state()
    verifier, challenge = pkce_pair()
    _PENDING[state] = (verifier, time.time())
    return json_ok(url=authorize_url(state, challenge))


@bp.get("/auth/callback")
def callback():
    from scripts.esi_sso import (
        character_from_claims,
        exchange_code,
        expires_at,
        verify_access_token,
    )

    _sweep()

    if request.args.get("error"):
        return redirect(f"/?auth=error&reason={request.args.get('error')}")

    state = request.args.get("state", "")
    code = request.args.get("code", "")
    pending = _PENDING.pop(state, None)
    if pending is None or not code:
        return redirect("/?auth=error&reason=state")
    verifier, _ = pending

    try:
        tokens = exchange_code(code, verifier)
        claims = verify_access_token(tokens["access_token"])
        character_id, name = character_from_claims(claims)
        _store(character_id, name, tokens, claims)
    except Exception as exc:  # noqa: BLE001 — сбой SSO не должен ронять сервер
        current_app.logger.warning("EVE SSO callback не удался: %s", exc)
        return redirect("/?auth=error&reason=exchange")

    return redirect("/?auth=ok")


def _store(character_id: int, name: str, tokens: dict, claims: dict) -> None:
    """Записать персонажа (source="esi") и зашифрованные токены."""
    from infra.crypto import encrypt
    from infra.db import session_scope
    from infra.models import Character, Credential
    from scripts.esi_sso import expires_at

    scopes = claims.get("scp")
    scopes = scopes if isinstance(scopes, list) else ([scopes] if scopes else [])

    with session_scope() as session:
        char = session.get(Character, character_id)
        if char is None:
            # Уровни скиллов пока неизвестны — их заполнит будущий
            # sync_character_skills. 0 честнее выдуманного значения.
            session.add(Character(
                character_id=character_id, name=name,
                command_center_upgrades_level=0,
                interplanetary_consolidation_level=0,
                source="esi",
            ))
        else:
            char.name = name
            char.source = "esi"

        cred = session.get(Credential, character_id)
        fields = dict(
            access_token=encrypt(tokens["access_token"]),
            refresh_token=encrypt(tokens["refresh_token"]),
            access_expires_at=expires_at(tokens.get("expires_in", 1200)),
            scopes=scopes,
        )
        if cred is None:
            session.add(Credential(character_id=character_id, **fields))
        else:
            for key, value in fields.items():
                setattr(cred, key, value)
