"""
EVE SSO (OAuth2) — единственная точка обращений к login.eveonline.com.

По документации CCP: https://developers.eveonline.com/docs/services/sso/
Тип приложения — public client + PKCE (CLAUDE.md, правило 11).

Что здесь:
  - PKCE-пара (verifier + challenge);
  - URL авторизации;
  - обмен кода на токены и обновление по refresh-токену;
  - проверка access-токена (JWT) по JWKS: подпись, issuer, audience, срок.

Сеть — через urllib (без зависимости requests), как в esi_client.py.
Тесты подменяют `_get_json` / `_post_form` и `jwks_client`.

БЕЗ client_id всё это не работает — и это ожидаемо (roadmap 0.6).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import jwt

from infra import config

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from version import user_agent  # noqa: E402

SSO_BASE = "https://login.eveonline.com"
METADATA_URL = f"{SSO_BASE}/.well-known/oauth-authorization-server"
EXPECTED_ISSUERS = (SSO_BASE, f"{SSO_BASE}/")
# CCP кладёт в audience и client_id, и литерал "EVE Online".
AUDIENCE_LITERAL = "EVE Online"

TIMEOUT_SECONDS = 15


class SsoError(RuntimeError):
    """Ошибка в ходе SSO-flow."""


class SsoNotConfigured(SsoError):
    """Нет client_id — авторизация невозможна."""


# ── PKCE ─────────────────────────────────────────────────────────────
def pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) по RFC 7636, метод S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def new_state() -> str:
    return secrets.token_urlsafe(24)


# ── Сеть ─────────────────────────────────────────────────────────────
def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent()})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_form(url: str, data: dict, headers: dict | None = None) -> dict:
    body = urllib.parse.urlencode(data).encode("ascii")
    hdrs = {
        "User-Agent": user_agent(),
        "Content-Type": "application/x-www-form-urlencoded",
        "Host": urllib.parse.urlparse(SSO_BASE).netloc,
    }
    hdrs.update(headers or {})
    request = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SsoError(f"SSO вернул HTTP {exc.code}: {detail}") from exc


@lru_cache(maxsize=1)
def _metadata() -> dict:
    """
    Эндпоинты SSO из discovery-документа. Кэш на процесс. При недоступности
    discovery — известные значения (v2), чтобы flow не падал целиком.
    """
    try:
        meta = _get_json(METADATA_URL)
        return {
            "authorization_endpoint": meta["authorization_endpoint"],
            "token_endpoint": meta["token_endpoint"],
            "revocation_endpoint": meta.get("revocation_endpoint", f"{SSO_BASE}/v2/oauth/revoke"),
            "jwks_uri": meta["jwks_uri"],
            "issuer": meta.get("issuer", SSO_BASE),
        }
    except Exception:  # noqa: BLE001 — любая ошибка discovery → фолбэк
        return {
            "authorization_endpoint": f"{SSO_BASE}/v2/oauth/authorize",
            "token_endpoint": f"{SSO_BASE}/v2/oauth/token",
            "revocation_endpoint": f"{SSO_BASE}/v2/oauth/revoke",
            "jwks_uri": f"{SSO_BASE}/oauth/jwks",
            "issuer": SSO_BASE,
        }


# ── Авторизация ──────────────────────────────────────────────────────
def authorize_url(state: str, code_challenge: str) -> str:
    if not config.ESI_CLIENT_ID:
        raise SsoNotConfigured(
            "PI_ESI_CLIENT_ID не задан. Зарегистрируйте приложение на "
            "developers.eveonline.com и укажите client_id."
        )
    params = {
        "response_type": "code",
        "redirect_uri": config.ESI_CALLBACK_URL,
        "client_id": config.ESI_CLIENT_ID,
        "scope": " ".join(config.ESI_SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{_metadata()['authorization_endpoint']}?{urllib.parse.urlencode(params)}"


def _token_request(payload: dict) -> dict:
    """Общий обмен на token endpoint. PKCE всегда; секрет — если задан."""
    headers = {}
    payload = {**payload, "client_id": config.ESI_CLIENT_ID}
    if config.ESI_CLIENT_SECRET:
        basic = base64.b64encode(
            f"{config.ESI_CLIENT_ID}:{config.ESI_CLIENT_SECRET}".encode("ascii")
        ).decode("ascii")
        headers["Authorization"] = f"Basic {basic}"
        payload.pop("client_id", None)
    tokens = _post_form(_metadata()["token_endpoint"], payload, headers)
    if "access_token" not in tokens:
        raise SsoError(f"В ответе token endpoint нет access_token: {tokens}")
    return tokens


def exchange_code(code: str, code_verifier: str) -> dict:
    return _token_request({
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
    })


def refresh(refresh_token: str) -> dict:
    return _token_request({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    })


# ── Отзыв токена ─────────────────────────────────────────────────────
def _revoke_request(url: str, data: dict, headers: dict) -> int:
    """
    POST на revocation_endpoint — только код статуса, не JSON: по RFC 7009
    сервер отвечает пустым телом с 200 независимо от того, был ли токен
    валиден (см. revoke()). Отдельная функция ради инъекции в тестах —
    тот же приём, что у _post_form.
    """
    body = urllib.parse.urlencode(data).encode("ascii")
    hdrs = {"User-Agent": user_agent(), "Content-Type": "application/x-www-form-urlencoded"}
    hdrs.update(headers)
    request = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def revoke(token: str, token_type_hint: str = "refresh_token") -> None:
    """
    Отозвать токен на стороне CCP — POST revocation_endpoint с
    client_secret_basic.

    Доступно ТОЛЬКО когда PI_ESI_CLIENT_SECRET задан: revocation_endpoint
    ESI принимает исключительно client_secret_basic/_post/_jwt (сверено с
    .well-known/oauth-authorization-server, 12.09.2026, правило 11) — без
    секрета публичный PKCE-клиент вызвать его не может ни при каких
    условиях, поэтому это не запасной путь, а отдельная возможность,
    включаемая явно (см. api/blueprints/auth.py::unlink, где вызов
    оборачивается в try — локальное удаление своей копии токена не должно
    зависеть от того, ответит ли CCP).
    """
    if not config.ESI_CLIENT_SECRET:
        raise SsoNotConfigured(
            "PI_ESI_CLIENT_SECRET не задан — отозвать токен на стороне CCP нельзя."
        )
    basic = base64.b64encode(
        f"{config.ESI_CLIENT_ID}:{config.ESI_CLIENT_SECRET}".encode("ascii")
    ).decode("ascii")
    status = _revoke_request(
        _metadata()["revocation_endpoint"],
        {"token": token, "token_type_hint": token_type_hint, "client_id": config.ESI_CLIENT_ID},
        {"Authorization": f"Basic {basic}"},
    )
    if status >= 400:
        raise SsoError(f"SSO revoke вернул HTTP {status}")


# ── Проверка access-токена (JWT) ─────────────────────────────────────
@lru_cache(maxsize=1)
def _jwks_client() -> "jwt.PyJWKClient":
    return jwt.PyJWKClient(_metadata()["jwks_uri"], headers={"User-Agent": user_agent()})


def verify_access_token(token: str) -> dict:
    """
    Проверить JWT по документации CCP: подпись (JWKS), issuer, audience,
    срок. Вернуть claims. При любом нарушении — SsoError.
    """
    if not config.ESI_CLIENT_ID:
        raise SsoNotConfigured("Нет client_id — не с чем сверять audience.")
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=config.ESI_CLIENT_ID,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise SsoError(f"Access-токен не прошёл проверку: {exc}") from exc

    if claims.get("iss") not in EXPECTED_ISSUERS:
        raise SsoError(f"Неожиданный issuer токена: {claims.get('iss')!r}")
    aud = claims.get("aud")
    aud = aud if isinstance(aud, list) else [aud]
    if AUDIENCE_LITERAL not in aud:
        raise SsoError("В audience токена нет литерала \"EVE Online\".")
    return claims


def character_from_claims(claims: dict) -> tuple[int, str]:
    """(character_id, name) из claims. sub имеет вид 'CHARACTER:EVE:<id>'."""
    sub = str(claims.get("sub", ""))
    try:
        character_id = int(sub.rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise SsoError(f"Не разобрать character_id из sub={sub!r}") from exc
    return character_id, str(claims.get("name", ""))


def expires_at(expires_in: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
