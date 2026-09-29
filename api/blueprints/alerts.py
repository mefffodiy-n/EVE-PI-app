"""
Настройки оповещений в Discord (C3): подписка аккаунта.

Рассылку ведёт сборщик scripts/send_alerts.py, а не обработчик (правило 3).
Единственное исключение — кнопка «Тест»: разовое действие самого
пользователя, одно сообщение в ЕГО же вебхук, не чаще раза в минуту.
Адрес вебхука наружу не отдаётся — только маска последних символов.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Blueprint

from api.cache import json_error, json_ok, parse_json_body
from api.session import current_account_id
from domain import alerts
from infra.crypto import TokenCryptoError, decrypt, encrypt
from infra.db import session_scope
from infra.models import AlertSent, AlertSubscription

bp = Blueprint("alerts", __name__)

TEST_COOLDOWN = timedelta(seconds=60)


def _get(session, account_id: str) -> AlertSubscription | None:
    from sqlalchemy import select

    return session.execute(
        select(AlertSubscription).where(AlertSubscription.account_id == account_id)
    ).scalar_one_or_none()


def _view(sub: AlertSubscription | None) -> dict:
    if sub is None:
        return {"configured": False, "allowed_lead_hours": list(alerts.ALLOWED_LEAD_HOURS)}
    try:
        tail = decrypt(sub.webhook_url_enc)[-6:]
    except TokenCryptoError:
        tail = ""
    return {
        "configured": True,
        "enabled": sub.enabled,
        "on_expiry": sub.on_expiry,
        "on_deficit": sub.on_deficit,
        "expiry_lead_hours": sub.expiry_lead_hours,
        "lang": sub.lang,
        "webhook_hint": f"…{tail}" if tail else "",
        "last_error": sub.last_error,
        "last_sent_at": sub.last_sent_at.isoformat() if sub.last_sent_at else None,
        "allowed_lead_hours": list(alerts.ALLOWED_LEAD_HOURS),
    }


def _account():
    account_id = current_account_id()
    return account_id, (None if account_id else json_error("no_visit_characters", status=401))


@bp.get("/alerts")
def get_alerts():
    account_id, err = _account()
    if err:
        return err
    with session_scope() as session:
        return json_ok(**_view(_get(session, account_id)))


@bp.put("/alerts")
def put_alerts():
    from api.errors import request_lang

    account_id, err = _account()
    if err:
        return err
    payload, error = parse_json_body({})
    if error:
        return json_error(error)

    url = payload.get("webhook_url")
    lead = payload.get("expiry_lead_hours")
    if lead is not None and (not isinstance(lead, int) or lead not in alerts.ALLOWED_LEAD_HOURS):
        return json_error("alerts_bad_lead", allowed=", ".join(map(str, alerts.ALLOWED_LEAD_HOURS)))
    if url is not None and not alerts.is_valid_webhook_url(str(url).strip()):
        return json_error("alerts_bad_webhook")

    with session_scope() as session:
        sub = _get(session, account_id)
        if sub is None:
            if url is None:
                return json_error("alerts_bad_webhook")
            sub = AlertSubscription(account_id=account_id, webhook_url_enc="")
            session.add(sub)
        if url is not None:
            try:
                sub.webhook_url_enc = encrypt(str(url).strip())
            except TokenCryptoError:
                session.rollback()
                return json_error("alerts_unavailable", status=503)
            sub.enabled = True
            sub.last_error = None
            session.flush()
            session.query(AlertSent).filter(AlertSent.subscription_id == sub.id).delete()
        for field in ("on_expiry", "on_deficit"):
            if isinstance(payload.get(field), bool):
                setattr(sub, field, payload[field])
        if isinstance(payload.get("enabled"), bool):
            sub.enabled = payload["enabled"]
            if sub.enabled:
                sub.last_error = None
        if lead is not None:
            sub.expiry_lead_hours = lead
        sub.lang = request_lang()
        session.flush()
        return json_ok(**_view(sub))


@bp.delete("/alerts")
def delete_alerts():
    account_id, err = _account()
    if err:
        return err
    with session_scope() as session:
        sub = _get(session, account_id)
        if sub is not None:
            session.query(AlertSent).filter(AlertSent.subscription_id == sub.id).delete()
            session.delete(sub)
    return json_ok(configured=False)


@bp.post("/alerts/test")
def test_alerts():
    from scripts.send_alerts import post_webhook

    account_id, err = _account()
    if err:
        return err
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        sub = _get(session, account_id)
        if sub is None:
            return json_error("alerts_no_subscription", status=404)
        if sub.last_test_at and now - sub.last_test_at < TEST_COOLDOWN:
            return json_error("alerts_test_too_soon", status=429)
        try:
            url = decrypt(sub.webhook_url_enc)
        except TokenCryptoError:
            return json_error("alerts_unavailable", status=503)
        sub.last_test_at = now
        session.flush()
        lang = sub.lang
    outcome, detail = post_webhook(url, alerts.webhook_test_message(lang))
    if outcome != "ok":
        return json_error("alerts_webhook_failed", status=502, detail=detail or outcome)
    return json_ok(sent=True)
