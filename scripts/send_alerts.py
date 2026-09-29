"""
Рассылка оповещений в Discord: дефицит добычи и истечение экстракторов.

ЗАЧЕМ ОТДЕЛЬНЫМ СКРИПТОМ. Правило 3: обработчики ничего не отправляют
наружу, всё внешнее делают сборщики по расписанию. Скрипт идёт после
`sync_colony_status` (данные колоний свежие), читает включённые подписки
(`alert_subscriptions`), считает события (`domain/alerts.py`) по колониям
персонажей аккаунта и шлёт ОДНО сообщение на подписку с новыми событиями.
Повтор — только при смене состояния (`alert_sent`).

DISCORD. Вебхук — обычный POST JSON на discord.com. Лимиты: 429 с
`Retry-After` — не долбим, оставляем до следующего запуска (события не
помечаются отправленными); 401/403/404 — вебхук удалён или неверен:
подписка отключается с пометкой в `last_error`, пользователь увидит её
в «Настройках».

Запуск: python -m scripts.send_alerts   (по расписанию — scripts/scheduler.py)
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TIMEOUT_SECONDS = 10


def post_webhook(url: str, content: str, opener=None) -> tuple[str, str]:
    """
    Отправить сообщение. Возвращает (итог, подробность):
    "ok" | "gone" (вебхук недействителен) | "rate" (лимит Discord) | "error".
    """
    from domain.alerts import is_valid_webhook_url
    from version import user_agent

    if not is_valid_webhook_url(url):
        return "gone", "invalid_url"
    body = json.dumps({"content": content, "allowed_mentions": {"parse": []}}).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": user_agent()},
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=TIMEOUT_SECONDS):
            return "ok", ""
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            return "rate", f"Retry-After {exc.headers.get('Retry-After', '?')}"
        if exc.code in (401, 403, 404):
            return "gone", f"HTTP {exc.code}"
        return "error", f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return "error", f"{type(exc).__name__}"


def _colonies_of(session, account_id: str) -> tuple[list[dict], dict[int, str]]:
    from sqlalchemy import select

    from infra.models import Character, Colony

    chars = session.execute(select(Character).where(Character.account_id == account_id)).scalars().all()
    names = {c.character_id: c.name for c in chars}
    if not names:
        return [], names
    rows = session.execute(select(Colony).where(Colony.character_id.in_(names))).scalars().all()
    colonies = [
        {
            "character_id": r.character_id, "planet_id": r.planet_id,
            "system_name": r.system_name, "planet_index": r.planet_index,
            "pins": r.pins or [],
        }
        for r in rows
    ]
    return colonies, names


def process(session, sub, now: datetime, post=post_webhook) -> str:
    """Одна подписка. Возвращает итог отправки: "none" | "ok" | "gone" | "rate" | "error"."""
    from sqlalchemy import delete, select

    from domain import alerts
    from infra.crypto import TokenCryptoError, decrypt
    from infra.models import AlertSent

    try:
        url = decrypt(sub.webhook_url_enc)
    except TokenCryptoError:
        sub.last_error = "decrypt:"
        return "gone"

    colonies, names = _colonies_of(session, sub.account_id)
    events = alerts.collect_events(
        colonies, now, on_expiry=sub.on_expiry, on_deficit=sub.on_deficit,
        lead_hours=sub.expiry_lead_hours,
    )
    sent = {
        row.key: row
        for row in session.execute(select(AlertSent).where(AlertSent.subscription_id == sub.id)).scalars()
    }
    current = {key for key, _, _ in events}
    stale = [key for key in sent if key not in current]
    if stale:
        session.execute(delete(AlertSent).where(
            AlertSent.subscription_id == sub.id, AlertSent.key.in_(stale)))

    fresh = [(k, t, ev) for k, t, ev in events if k not in sent or sent[k].token != t]
    if not fresh:
        return "none"

    text = alerts.render_message([ev for _, _, ev in fresh], names, sub.lang)
    outcome, detail = post(url, text)
    if outcome == "ok":
        for key, token, _ in fresh:
            row = sent.get(key)
            if row is None:
                session.add(AlertSent(subscription_id=sub.id, key=key, token=token, sent_at=now))
            else:
                row.token, row.sent_at = token, now
        sub.last_sent_at, sub.last_error = now, None
    elif outcome == "gone":
        sub.enabled = False
        sub.last_error = f"gone:{detail}"[:255]
    else:
        sub.last_error = f"{outcome}:{detail}"[:255]
    return outcome


def main() -> int:
    from sqlalchemy import select

    from infra.db import session_scope
    from infra.logging import configure
    from infra.models import AlertSubscription

    log = configure("send_alerts")
    now = datetime.now(timezone.utc)
    failed = 0
    with session_scope() as session:
        subs = session.execute(
            select(AlertSubscription).where(AlertSubscription.enabled.is_(True))
        ).scalars().all()
        for sub in subs:
            outcome = process(session, sub, now)
            if outcome in ("error", "rate"):
                failed += 1
            log.info("Оповещения: подписка %s — %s", sub.id, outcome)
    log.info("Оповещения: подписок %d, сбоев %d", len(subs), failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
