"""
EVE SSO: вход через OAuth2 Authorization Code + PKCE.

Контракт фронтенда (web/index.html → startLogin):
  GET /api/auth/login-url   -> {status, url}       браузер идёт по url
  GET /api/auth/callback    -> 302 на / (?auth=ok|error)

ИСКЛЮЧЕНИЯ ИЗ ПРАВИЛА 3 (их два, оба разовые действия пользователя, а
не обслуживание данных):
  1. Обмен кода на токен — синхронный вызов к login.eveonline.com прямо
     в обработчике: без него колбэк не имеет смысла.
  2. Первая синхронизация скиллов/колоний нового персонажа — уже идёт в
     esi.evetech.net и расходует лимит ошибок ESI, поэтому вынесена в
     фоновый поток (`_sync_first_login_async`), не блокирующий ответ.
     Не ждать этого персонажа до ближайшего расписания scheduler.py —
     разово, при входе, дальше он живёт по общему расписанию как все.

БЕЗ client_id и ключа шифрования — login-url отдаёт 503. Это ожидаемо
(roadmap 0.6): разработка идёт на dev-заглушках.
"""

from __future__ import annotations

import logging
import threading
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

    _sync_first_login_async(character_id)

    return redirect("/?auth=ok")


def _first_sync_one(character_id: int, client=None) -> None:
    """
    Собственно синхронизация — вынесена из потока отдельной функцией,
    чтобы тест мог вызвать её напрямую (с мок-клиентом, синхронно), не
    гоняясь за фоновым потоком.

    client=None означает реальный EsiClient() — по умолчанию для боевого
    вызова из фонового потока; тест передаёт свой с мок-opener'ом.
    """
    from infra.db import session_scope
    from infra.models import Character
    from scripts import sync_character_skills, sync_colony_status
    from scripts.esi_client import EsiClient, EsiRateLimited

    log = logging.getLogger("auth.first_sync")
    try:
        client = client or EsiClient()
        with session_scope() as session:
            character = session.get(Character, character_id)
            if character is None:
                return
            sync_character_skills.sync_one(client, session, character)
            sync_colony_status.sync_one(client, session, character)
    except EsiRateLimited:
        pass  # подождём ближайшего расписания, а не будем спорить с лимитом
    except Exception as exc:  # noqa: BLE001 — фон не должен ронять процесс
        log.warning("Первичная синхронизация персонажа %s не удалась: %s",
                    character_id, exc)


def _sync_first_login_async(character_id: int) -> None:
    """
    Сразу после входа подтянуть скиллы и колонии ЭТОГО персонажа в
    фоновом потоке — не дожидаясь ближайшего расписания scheduler.py
    (скиллы — раз в 6 ч, колонии — раз в 30 мин).

    ВТОРОЕ (наравне с обменом кода на токен выше) исключение из правила 3,
    и оно другого рода: эти вызовы идут в esi.evetech.net, а не только в
    login.eveonline.com, и расходуют лимит ошибок ESI. Оправдание то же —
    это разовое действие В ОТВЕТ НА только что состоявшийся вход
    пользователя, а не обслуживание данных по расписанию, и для этого
    персонажа ещё не было ни одного запроса к этим маршрутам — значит
    это не может быть преждевременным повторным (см. roadmap.md, Фаза 2,
    пункт про проверку Expires).

    Фоновый поток, а не прямой вызов в обработчике — чтобы не держать
    HTTP-ответ пользователю на время синка: у персонажа может быть много
    колоний, каждая — отдельный запрос к ESI (на боевых данных 11.09.2026
    у одного персонажа было 5 колоний, у другого 16 — секунды, не доли
    секунды). Сбой фона не должен быть заметен пользователю: он и так уже
    вошёл, а данные всё равно подтянутся по расписанию максимум через
    30 минут.
    """
    threading.Thread(target=_first_sync_one, args=(character_id,),
                      daemon=True, name=f"first-sync-{character_id}").start()


def _store(character_id: int, name: str, tokens: dict, claims: dict) -> None:
    """
    Записать персонажа (source="esi") и зашифрованные токены.

    account_id (см. api/session.py) привязывает персонажа к ТЕКУЩЕМУ
    визиту — единственное место, где решается, кому этот персонаж будет
    виден дальше в /api/characters, /api/colonies, /api/calculate и
    сохранённых планах (найдено 11.09.2026: без этого разные пользователи
    видели персонажей и колонии друг друга). Повторный вход тем же
    персонажем из другого браузера/сессии ПЕРЕВЯЗЫВАЕТ его новому визиту
    — это ожидаемо: доступ туда, откуда только что подтверждён логин.
    """
    from api.session import ensure_account_id
    from infra.credentials import save_tokens
    from infra.db import session_scope
    from infra.models import Character

    scopes = claims.get("scp")
    scopes = scopes if isinstance(scopes, list) else ([scopes] if scopes else [])
    account_id = ensure_account_id()

    with session_scope() as session:
        char = session.get(Character, character_id)
        if char is None:
            # Уровни скиллов пока неизвестны — их заполнит будущий
            # sync_character_skills. 0 честнее выдуманного значения.
            session.add(Character(
                character_id=character_id, name=name,
                command_center_upgrades_level=0,
                interplanetary_consolidation_level=0,
                source="esi", account_id=account_id,
            ))
        else:
            char.name = name
            char.source = "esi"
            char.account_id = account_id

        save_tokens(session, character_id, tokens, scopes)


@bp.post("/auth/unlink/<int:character_id>")
def unlink(character_id: int):
    """
    «Отвязать персонажа». Всегда стирает свою копию токена и открепляет
    персонажа от этого визита (account_id=None) — тот же вид, что и до
    первого входа, планировщик и сборщики его больше не увидят, не
    дожидаясь ближайшего refresh_tokens. Это гарантировано в любом случае.

    Настоящий отзыв на стороне CCP (POST /v2/oauth/revoke) — ДОПОЛНИТЕЛЬНО,
    только если задан PI_ESI_CLIENT_SECRET (см. scripts/esi_sso.py::revoke,
    там разбор — без секрета публичный PKCE-клиент не может аутентифицировать
    этот запрос вообще, не запасной путь, а отдельная возможность). Сбой
    вызова к CCP (сеть, уже отозван раньше, секрет не задан) не должен
    мешать локальному удалению — оно происходит в любом случае, отсюда
    порядок: сперва пробуем revoke() СО старым токеном, потом стираем.
    Ответ несёт revoked, чтобы фронт мог честно сказать, что произошло на
    самом деле, а не что должно было произойти (правило 1).

    Персонаж должен принадлежать текущему визиту — тот же честный «не
    найден», что и у чужих сохранённых планов (domain/plan_storage.py):
    не подтверждать самим ответом сам факт существования character_id.
    Dev-заглушки (source="dev") отвязать нельзя — они не проходили через
    SSO, отвязывать у них нечего, и в dev это привело бы к путанице ради
    несуществующего сценария.
    """
    from api.session import current_account_id
    from infra.credentials import delete_tokens, get_refresh_token
    from infra.db import session_scope
    from infra.models import Character

    account_id = current_account_id()
    if account_id is None:
        return json_error("Персонаж не найден", 404)

    with session_scope() as session:
        character = session.get(Character, character_id)
        if character is None or character.account_id != account_id:
            return json_error("Персонаж не найден", 404)
        if character.source != "esi":
            return json_error("Этого персонажа нельзя отвязать")

        revoked = False
        if config.ESI_CLIENT_SECRET:
            refresh_token = get_refresh_token(session, character_id)
            if refresh_token:
                from scripts.esi_sso import revoke

                try:
                    revoke(refresh_token)
                    revoked = True
                except Exception as exc:  # noqa: BLE001 — сбой CCP не должен мешать локальному удалению
                    current_app.logger.warning(
                        "Отзыв токена персонажа %s на стороне CCP не удался: %s",
                        character_id, exc,
                    )

        character.account_id = None
        delete_tokens(session, character_id)

    return json_ok(unlinked=character_id, revoked=revoked)
